"""LangGraph 图编排：requirement → code_gen → review → (needs_changes 循环) → test_gen → done。"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple

try:  # langgraph 是可选依赖，不可用时回退到线性执行器（便于本地冒烟）
    from langgraph.graph import END, StateGraph  # type: ignore
    _LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - optional
    _LANGGRAPH_AVAILABLE = False
    END = "__end__"  # type: ignore

try:  # 方案C：SqliteSaver 是可选依赖，用于断点续跑
    from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
    _SQLITE_SAVER_AVAILABLE = True
except Exception:  # pragma: no cover - optional
    _SQLITE_SAVER_AVAILABLE = False
    SqliteSaver = None  # type: ignore


from app.agents.code_gen_agent import code_gen_node
from app.agents.requirement_agent import requirement_node
from app.agents.review_agent import review_node
from app.agents.test_gen_agent import test_gen_node
from app.core.config import settings
from app.core.events import EventType, event_bus
from app.core.llm_adapter import llm
from app.db.sqlite_client import db
from app.graph.state import (
    PipelinePhase,
    PipelineState,
    PipelineStatus,
    ReviewReport,
    ReviewVerdict,
    ensure_status,
    new_state,
    should_enter_test_gen,
    should_retry_codegen,
)

from app.core.tracing import tracer
from app.core.memory import memory

try:
    from app.core.observability import metrics_collector, alert_manager
except ImportError:
    metrics_collector = None  # type: ignore[assignment]
    alert_manager = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ====================================================================== #
#  方案C：SqliteSaver 单例（断点续跑用）
# ====================================================================== #
#  - 用独立 sqlite 文件 data/checkpoints.db，与业务库 data/app.db 分离
#  - 全局单例避免重复打开连接
#  - langgraph-checkpoint-sqlite 未装时降级为 None（不持久化，但仍能跑）

_CHECKPOINT_DB_PATH = Path("data/checkpoints.db")
_checkpointer_local = threading.local()
_checkpointer_connections: List[sqlite3.Connection] = []
# 每个执行线程持有独立 SqliteSaver/连接，避免单个 sqlite 连接跨线程共享。
_checkpointer_lock = threading.Lock()


def get_checkpointer():
    """获取当前线程专属 SqliteSaver；未安装则返回 None。"""
    global _checkpointer_connections
    if not _SQLITE_SAVER_AVAILABLE:
        return None
    cached = getattr(_checkpointer_local, "checkpointer", None)
    if cached is not None:
        return cached
    with _checkpointer_lock:
        cached = getattr(_checkpointer_local, "checkpointer", None)
        if cached is not None:
            return cached
        try:
            _CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(_CHECKPOINT_DB_PATH), check_same_thread=False)
            checkpointer = SqliteSaver(conn)
            _checkpointer_local.conn = conn
            _checkpointer_local.checkpointer = checkpointer
            _checkpointer_connections.append(conn)
            logger.info("SqliteSaver 已初始化：%s", _CHECKPOINT_DB_PATH)
            return checkpointer
        except Exception as e:
            logger.warning("SqliteSaver 初始化失败，断点续跑不可用：%s", e)
            return None


def has_checkpoint(task_id: str) -> bool:
    """Return True when a runnable checkpoint exists for task_id."""
    if not (_LANGGRAPH_AVAILABLE and _SQLITE_SAVER_AVAILABLE):
        return False
    checkpointer = get_checkpointer()
    if checkpointer is None:
        return False
    try:
        return checkpointer.get_tuple({"configurable": {"thread_id": task_id}}) is not None
    except Exception:
        return False


def _checkpoint_phase(checkpoint: Any) -> Optional[str]:
    """Best-effort extraction of the latest persisted pipeline phase."""
    channel_values = getattr(checkpoint, "channel_values", None)
    if channel_values is None and isinstance(checkpoint, dict):
        channel_values = checkpoint.get("channel_values", checkpoint)
    if not channel_values:
        return None
    state = channel_values.get("state") if isinstance(channel_values, dict) else getattr(
        channel_values, "state", None
    )
    if not state:
        return None
    status = state.get("status") if isinstance(state, dict) else getattr(state, "status", None)
    if not status:
        return None
    phase = status.get("phase") if isinstance(status, dict) else getattr(status, "phase", None)
    if phase is None:
        return None
    return phase.value if hasattr(phase, "value") else str(phase)


def close_checkpointer_connections() -> None:
    """Close every sqlite connection created for thread-local checkpointers."""
    with _checkpointer_lock:
        conns = list(_checkpointer_connections)
        _checkpointer_connections.clear()
        try:
            _checkpointer_local.checkpointer = None
            _checkpointer_local.conn = None
        except Exception:
            pass
    for conn in conns:
        try:
            conn.close()
        except Exception:
            logger.warning("Failed to close a checkpointer sqlite connection", exc_info=True)


# ====================================================================== #
#  条件边（LangGraph 条件路由 + 非 LangGraph fallback 都会用到）
# ====================================================================== #

def _router_after_review(state: PipelineState) -> str:
    """评审之后的路由判定。

    返回:
        - "retry" → code_gen（retry_counter 自增后重做）
        - "test_gen" → 进入测试生成
        - "done" → 失败或禁用测试时，直接结束
    """
    report: ReviewReport = state.get("review_report") or ReviewReport()
    counter = int(state.get("retry_counter") or 0)

    # 1) rejected → 直接结束
    if report.verdict == ReviewVerdict.REJECTED:
        return "done"

    # 2) 需要修复且未超出重试上限 → 回退 code_gen
    if report.need_fix and counter < settings.agent.max_review_retry:
        return "retry"

    # 3) 需要修复但已达上限 → 直接结束（标记失败）
    if report.need_fix:
        return "done"

    # 4) approved → 视配置决定是否进入测试
    if report.verdict == ReviewVerdict.APPROVED:
        return "test_gen" if should_enter_test_gen(state) else "done"

    return "done"


# ====================================================================== #
#  自增重试计数器的包装节点
# ====================================================================== #

def retry_code_gen_node(state: PipelineState) -> Dict[str, Any]:
    """作为 REVIEW → CODE_GEN 的中间节点：在重跑 code_gen 前把 retry_counter +1。"""
    counter = int(state.get("retry_counter") or 0)
    status = ensure_status(state)
    status.phase = PipelinePhase.CODE_GEN
    status.touch()
    return {
        "retry_counter": counter + 1,
        "status": status,
    }


# ====================================================================== #
#  Supervisor 集中编排
# ====================================================================== #

def supervisor_node(state: PipelineState) -> Dict[str, Any]:
    """Supervisor 节点：分析各节点输出，决定下一步路由。

    策略：
    - 如果 state 中有 human_approval 待处理，路由到 human_in_loop
    - 如果所有节点都已完成，路由到 done
    - 否则根据当前阶段决定下一步
    """
    current_phase = ensure_status(state).phase
    task_id = state.get("task_id", "")
    node_outputs = state.get("node_outputs", {})

    # 1) 检查是否有 HITL 待审批
    human_approval = state.get("human_approval")
    if human_approval is not None:
        decision = "human_in_loop"
    else:
        # 2) 根据当前阶段决定下一步
        if current_phase == PipelinePhase.PENDING:
            decision = "requirement"
        elif current_phase == PipelinePhase.REQUIREMENT:
            decision = "code_gen"
        elif current_phase == PipelinePhase.CODE_GEN:
            decision = "review"
        elif current_phase == PipelinePhase.CODE_REVIEW:
            # 检查 review 结果
            report = state.get("review_report") or ReviewReport()
            counter = int(state.get("retry_counter") or 0)
            if report.verdict == ReviewVerdict.REJECTED:
                decision = "done"
            elif report.need_fix and counter < settings.agent.max_review_retry:
                decision = "retry"
            elif report.need_fix:
                decision = "done"
            elif report.verdict == ReviewVerdict.APPROVED:
                decision = "test_gen" if should_enter_test_gen(state) else "done"
            else:
                decision = "done"
        elif current_phase == PipelinePhase.TEST_GEN:
            decision = "done"
        else:
            decision = "done"

    logger.info("Supervisor 决策: %s (当前阶段: %s)", decision, current_phase.value if hasattr(current_phase, 'value') else current_phase)
    return {
        "supervisor_decision": decision,
        "node_outputs": {**node_outputs, "supervisor": decision},
    }


def supervisor_router(state: PipelineState) -> str:
    """根据 supervisor_decision 返回路由目标。"""
    decision = state.get("supervisor_decision", "done")
    # 路由目标: requirement / code_gen / review / test_gen / human_in_loop / done
    # retry 也路由到 code_gen
    if decision == "retry":
        return "code_gen"
    return decision


# ====================================================================== #
#  Human-in-the-Loop
# ====================================================================== #

def human_approval_node(state: PipelineState) -> Dict[str, Any]:
    """HITL 节点：暂停流水线，等待人工审批。

    通过 event_bus 发出 HITL_WAITING 事件，外部 API 调用 approve/reject 来恢复。
    审批通过时阶段标记为之前的阶段继续；拒绝时标记为 FAILED。
    """
    task_id = state.get("task_id", "")
    human_approval = state.get("human_approval") or {}

    logger.info("HITL 等待审批: task_id=%s, data=%s", task_id, human_approval)

    # 发出 HITL 等待事件
    event_bus.emit(
        EventType.HITL_WAITING,
        data={"task_id": task_id, "approval_data": human_approval},
        task_id=task_id,
    )

    # 持久化到 checkpoint：state 已通过 SqliteSaver 自动保存
    status = ensure_status(state)
    status.phase = PipelinePhase.HITL
    status.touch()

    # 检查是否已有审批结果
    approval_status = human_approval.get("status")
    if approval_status == "approved":
        event_bus.emit(EventType.HITL_APPROVED, data=human_approval, task_id=task_id)
        # 清除 human_approval，恢复执行
        return {
            "status": status,
            "human_approval": None,
        }
    elif approval_status == "rejected":
        event_bus.emit(EventType.HITL_REJECTED, data=human_approval, task_id=task_id)
        status.phase = PipelinePhase.FAILED
        status.error_message = human_approval.get("reason", "人工审批未通过")
        status.touch()
        return {
            "status": status,
            "human_approval": None,
        }

    # 仍在等待，返回当前状态（LangGraph 会停留在当前节点）
    return {
        "status": status,
    }


# ====================================================================== #
#  LangGraph 图构建
# ====================================================================== #

def build_graph(checkpointer=None):
    """构造 LangGraph StateGraph。需要 langgraph 已安装；不可用则抛异常。

    方案C：接受 checkpointer 参数（SqliteSaver 单例），compile 后支持断点续跑。
    """
    if not _LANGGRAPH_AVAILABLE:
        raise RuntimeError("langgraph 未安装，请 pip install langgraph 后再使用 LangGraph 执行器。")

    workflow = StateGraph(PipelineState)

    # 节点名故意避开 PipelineState 已有的 key（requirement/code_package/
    # review_report/test_report/status 等），防止 LangGraph 注册冲突。
    workflow.add_node("requirement_step", requirement_node)
    workflow.add_node("code_gen_step", code_gen_node)
    workflow.add_node("review_step", review_node)
    workflow.add_node("test_gen_step", test_gen_node)
    workflow.add_node("retry_step", retry_code_gen_node)

    # Supervisor 节点
    workflow.add_node("supervisor_step", supervisor_node)
    # HITL 节点
    workflow.add_node("human_in_loop_step", human_approval_node)

    # 起点：Supervisor 入口
    workflow.set_entry_point("supervisor_step")

    # Supervisor → 各节点的条件路由
    workflow.add_conditional_edges(
        "supervisor_step",
        supervisor_router,
        {
            "requirement": "requirement_step",
            "code_gen": "code_gen_step",
            "review": "review_step",
            "test_gen": "test_gen_step",
            "human_in_loop": "human_in_loop_step",
            "done": END,  # type: ignore[dict-item]
        },
    )

    # 各节点执行完毕后回到 Supervisor 做下一步决策
    workflow.add_edge("requirement_step", "supervisor_step")
    workflow.add_edge("code_gen_step", "supervisor_step")
    workflow.add_edge("review_step", "supervisor_step")
    workflow.add_edge("test_gen_step", "supervisor_step")
    workflow.add_edge("human_in_loop_step", "supervisor_step")
    workflow.add_edge("retry_step", "supervisor_step")

    # 方案C：传入 checkpointer 启用断点持久化
    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


# ====================================================================== #
#  线性 Fallback 执行器（langgraph 未安装也能跑）
# ====================================================================== #

class LinearPipeline:
    """不依赖 langgraph 的顺序+条件执行器，行为与 build_graph() 编译结果对齐。"""

    @staticmethod
    def run(state: PipelineState) -> PipelineState:
        def _apply(node_name: str, node_fn, s: PipelineState) -> None:
            start = time.perf_counter()
            status = ensure_status(s)
            task_id = s.get("task_id", "")
            phase_map = {
                "requirement": PipelinePhase.REQUIREMENT,
                "code_gen": PipelinePhase.CODE_GEN,
                "review": PipelinePhase.CODE_REVIEW,
                "test_gen": PipelinePhase.TEST_GEN,
                "retry": PipelinePhase.CODE_GEN,
                "supervisor": PipelinePhase.SUPERVISOR,
                "human_in_loop": PipelinePhase.HITL,
            }
            status.phase = phase_map.get(node_name, status.phase)
            status.touch()
            s["status"] = status
            try:
                # 节点执行前发出事件（注：非 requirement 节点用 NODE_STARTED 表示开始执行）
                event_bus.emit(
                    EventType.TASK_STARTED if node_name == "requirement" else EventType.NODE_COMPLETED,
                    data={"node": node_name, "phase": status.phase.value if hasattr(status.phase, 'value') else str(status.phase), "status": "starting"},
                    task_id=task_id,
                )
                patch = node_fn(s)
            except Exception as e:
                logger.exception("节点 %s 执行异常，直接标记为失败并终止：%s", node_name, e)
                event_bus.emit(EventType.NODE_FAILED, data={"node": node_name, "error": str(e)}, task_id=task_id)
                status = ensure_status(s)
                status.phase = PipelinePhase.FAILED
                status.error_message = f"{node_name}: {e}"
                s["status"] = status
                # Bug D5：传入失败阶段，清理后续阶段的中间产物
                db.mark_task_failed(s.get("task_id", ""), str(e), failed_stage=node_name)
                # 发出流水线失败事件
                event_bus.emit(EventType.PIPELINE_FAILED, data={"node": node_name, "error": str(e)}, task_id=task_id)
                raise
            if patch:
                for k, v in patch.items():
                    if v is None:
                        continue
                    s[k] = v  # type: ignore[literal-required]
            # 节点执行完成后发出事件
            event_bus.emit(EventType.NODE_COMPLETED, data={"node": node_name}, task_id=task_id)
            logger.info(">>> %s 完成，耗时 %.2fs", node_name, time.perf_counter() - start)

        # 任务启动事件
        event_bus.emit(EventType.TASK_STARTED, data={"task_id": state.get("task_id", "")}, task_id=state.get("task_id", ""))

        # Supervisor 驱动的循环执行
        max_iterations = 50  # 防止无限循环
        iteration = 0
        while iteration < max_iterations:
            iteration += 1

            # 1. Supervisor 决策
            supervisor_patch = supervisor_node(state)
            for k, v in supervisor_patch.items():
                if v is not None:
                    state[k] = v  # type: ignore[literal-required]
            decision = state.get("supervisor_decision", "done")

            # 2. 检查 HITL
            if decision == "human_in_loop":
                human_patch = human_approval_node(state)
                for k, v in human_patch.items():
                    if v is not None:
                        state[k] = v  # type: ignore[literal-required]
                # 如果审批通过，继续循环让 Supervisor 重新决策
                # 如果审批拒绝，已经标记为 FAILED
                status = ensure_status(state)
                if status.phase == PipelinePhase.FAILED:
                    event_bus.emit(EventType.PIPELINE_FAILED, data={"reason": "human rejection"}, task_id=state.get("task_id", ""))
                    _finalize(state)
                    return state
                # 审批通过，继续循环
                continue

            # 3. 路由到对应节点
            if decision == "done":
                _finalize(state)
                event_bus.emit(EventType.PIPELINE_DONE, data={}, task_id=state.get("task_id", ""))
                return state

            if decision == "requirement":
                _apply("requirement", requirement_node, state)
            elif decision == "code_gen" or decision == "retry":
                if decision == "retry":
                    _apply("retry", retry_code_gen_node, state)
                _apply("code_gen", code_gen_node, state)
            elif decision == "review":
                _apply("review", review_node, state)
            elif decision == "test_gen":
                _apply("test_gen", test_gen_node, state)
            else:
                logger.warning("未知的 Supervisor 决策: %s，终止", decision)
                break

        # 超出最大迭代次数
        if iteration >= max_iterations:
            logger.error("流水线超出最大迭代次数 %d，强制终止", max_iterations)
            status = ensure_status(state)
            status.phase = PipelinePhase.FAILED
            status.error_message = f"超出最大迭代次数 {max_iterations}"
            state["status"] = status

        _finalize(state)
        event_bus.emit(EventType.PIPELINE_DONE, data={}, task_id=state.get("task_id", ""))
        return state


def _finalize(state: PipelineState, start_time: float = 0.0) -> None:
    status = state.get("status") or PipelineStatus()
    status.touch()
    if start_time > 0:
        status.duration_seconds = round(time.monotonic() - start_time, 2)
    task_id = state.get("task_id", "")

    # 持久化评审/测试摘要，避免服务重启后详情丢失
    summary: Dict[str, Any] = {}
    review_report = state.get("review_report")
    if review_report:
        summary["review"] = {
            "verdict": review_report.verdict.value,
            "score": review_report.score,
            "summary": review_report.summary,
            "issues_count": len(review_report.issues),
        }
    test_report = state.get("test_report")
    if test_report:
        summary["test"] = {
            "status": test_report.status.value,
            "summary": test_report.summary,
            "total": test_report.total,
            "passed": test_report.passed,
            "failed": test_report.failed,
            "error_count": test_report.error_count,
        }

    # Bug S1 修复：显式判定失败场景，避免 rejected / 重试上限被误标为 DONE。
    # review_node 在 rejected 时不会写入 error_message，原逻辑会走 else 分支误标 DONE。
    retry_counter = int(state.get("retry_counter") or 0)
    max_review_retry = settings.agent.max_review_retry
    if review_report and review_report.verdict == ReviewVerdict.REJECTED:
        status.error_message = "代码评审未通过"
    elif (
        review_report
        and review_report.need_fix
        and retry_counter >= max_review_retry
    ):
        status.error_message = "重试次数已达上限"

    if status.error_message:
        # Bug D5：捕获失败阶段，清理后续阶段的中间产物（保留失败阶段本身文件供调试）
        failed_phase = (status.phase.value if hasattr(status.phase, "value")
                        else str(status.phase)) if status.phase else None
        status.phase = PipelinePhase.FAILED
        db.mark_task_failed(task_id, status.error_message, summary=summary,
                            failed_stage=failed_phase)
        event_bus.emit(EventType.PIPELINE_FAILED, data={"error": status.error_message}, task_id=task_id)
    else:
        status.phase = PipelinePhase.DONE
        db.mark_task_done(task_id, summary=summary)
        event_bus.emit(EventType.PIPELINE_DONE, data={}, task_id=task_id)
    state["status"] = status

    # 长期记忆：任务完成后自动沉淀经验
    if settings.memory.enabled:
        try:
            review_verdict = review_report.verdict.value if review_report else ""
            review_score = review_report.score if review_report else 0
            review_issue_count = len(review_report.issues) if review_report else 0
            test_status = test_report.status.value if test_report else ""
            test_pass_rate = (test_report.passed / max(test_report.total, 1)) if test_report else 0.0
            code_pkg = state.get("code_package")
            code_files = [f.path for f in code_pkg.files] if code_pkg and hasattr(code_pkg, "files") else []
            memory.save_experience(
                task_id=task_id,
                user_request=state.get("user_request", ""),
                review_verdict=review_verdict,
                review_score=review_score,
                review_issue_count=review_issue_count,
                test_status=test_status,
                test_pass_rate=test_pass_rate,
                skill_id=state.get("skill_id", ""),
                code_files=code_files,
                error_message=status.error_message,
            )
            # 技能自动沉淀：从高评分经验归纳可复用技能到内存注册表（默认关闭）
            if settings.memory.memory_auto_sediment:
                memory.distill_skills()
        except Exception as e:
            logger.warning("保存任务经验失败（不影响主流程）: %s", e)


# ====================================================================== #
#  流式执行支持（中间状态推送）
# ====================================================================== #

def _stream_event(
    phase: str,
    progress: int,
    message: str,
    task_id: str = "",
    error_type: str = "",
) -> Dict[str, Any]:
    """构造流式事件数据。"""
    from datetime import datetime
    return {
        "phase": phase,
        "progress": progress,
        "message": message,
        "task_id": task_id,
        "error_type": error_type,
        "timestamp": datetime.now().isoformat(),
    }


def _streaming_wrap_node(
    node_name: str,
    node_fn,
    state: PipelineState,
    task_id: str,
    events: List[Dict[str, Any]],
) -> None:
    """包装节点执行，在前后插入流式事件。"""
    phase_map = {
        "requirement": "requirement",
        "code_gen": "code_gen",
        "review": "review",
        "test_gen": "test_gen",
        "retry": "code_gen",
    }
    phase = phase_map.get(node_name, node_name)
    events.append(_stream_event(phase, 0, f"开始 {node_name}...", task_id))

    try:
        patch = node_fn(state)
        if patch:
            for k, v in patch.items():
                if v is None:
                    continue
                state[k] = v  # type: ignore[literal-required]
        events.append(_stream_event(phase, 100, f"{node_name} 完成", task_id))
    except Exception as e:
        events.append(_stream_event(phase, -1, f"{node_name} 失败: {e}", task_id))
        raise


def stream_pipeline(
    *,
    task_id: str,
    user_request: str,
    extra_args: Optional[Dict[str, Any]] = None,
    skill_id: str = "",
    model: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], PipelineState]:
    """执行流水线并返回 (流式事件列表, 最终状态)（用于 SSE 推送 + 最终结果）。

    与 run_pipeline 功能相同，但会将每个节点的开始/结束事件
    收集到列表中返回，供 SSE 端点逐条推送。
    同时返回最终 state，避免调用方再跑一次 run_pipeline。
    """
    events: List[Dict[str, Any]] = []
    _stream_start = time.monotonic()
    state = new_state(
        task_id=task_id,
        user_request=user_request,
        extra_args=extra_args,
        skill_id=skill_id,
        model=model,
    )

    db.update_task_phase(task_id, phase=PipelinePhase.PENDING.value, summary={"start": True})

    # 1. requirement
    events.append(_stream_event("requirement", 0, "需求分析中...", task_id))
    try:
        patch = requirement_node(state)
        if patch:
            for k, v in patch.items():
                if v is None:
                    continue
                state[k] = v  # type: ignore[literal-required]
        events.append(_stream_event("requirement", 100, "需求分析完成", task_id))
    except Exception as e:
        events.append(_stream_event("requirement", -1, f"需求分析失败: {e}", task_id))
        raise

    # 2. (code_gen -> review) × N 轮
    while True:
        # code_gen
        events.append(_stream_event("code_gen", 0, "代码生成中...", task_id))
        try:
            patch = code_gen_node(state)
            if patch:
                for k, v in patch.items():
                    if v is None:
                        continue
                    state[k] = v
            events.append(_stream_event("code_gen", 100, "代码生成完成", task_id))
            if llm.quota_exhausted:
                events.append(_stream_event(
                    "code_gen", -1,
                    "⚠️ API Key 免费额度已用尽，当前为 DryRun 模式。请在右上角更换 API Key。",
                    task_id, error_type="quota_exhausted",
                ))
        except Exception as e:
            events.append(_stream_event("code_gen", -1, f"代码生成失败: {e}", task_id))
            raise

        # review
        events.append(_stream_event("review", 0, "代码审查中...", task_id))
        try:
            patch = review_node(state)
            if patch:
                for k, v in patch.items():
                    if v is None:
                        continue
                    state[k] = v
            events.append(_stream_event("review", 100, "代码审查完成", task_id))
        except Exception as e:
            events.append(_stream_event("review", -1, f"代码审查失败: {e}", task_id))
            raise

        route = _router_after_review(state)
        if route == "retry":
            events.append(_stream_event("code_gen", 0, "代码需修改，重新生成...", task_id))
            retry_code_gen_node(state)
            continue
        if route == "test_gen":
            events.append(_stream_event("test_gen", 0, "测试生成中...", task_id))
            try:
                patch = test_gen_node(state)
                if patch:
                    for k, v in patch.items():
                        if v is None:
                            continue
                        state[k] = v
                events.append(_stream_event("test_gen", 100, "测试生成完成", task_id))
            except Exception as e:
                events.append(_stream_event("test_gen", -1, f"测试生成失败: {e}", task_id))
                raise
            _finalize(state, _stream_start)
            events.append(_stream_event("done", 100, "流水线执行完成", task_id))
            return events, state
        # done
        _finalize(state, _stream_start)
        events.append(_stream_event("done", 100, "流水线执行完成", task_id))
        return events, state


# ====================================================================== #
#  对外统一入口
# ====================================================================== #

def run_pipeline(
    *,
    task_id: str,
    user_request: str,
    extra_args: Optional[Dict[str, Any]] = None,
    prefer_langgraph: bool = True,
    enable_checkpoint: bool = True,
    skill_id: str = "",
    model: Optional[str] = None,
) -> PipelineState:
    """执行完整流水线。

    优先使用 LangGraph 编译图；若未安装则自动 fallback 到 LinearPipeline。

    方案C：enable_checkpoint=True 时传入 SqliteSaver，每个节点执行后自动持久化 state，
    失败后可用 resume_pipeline(task_id) 从断点恢复。
    thread_id 直接用 task_id，保证每个任务有独立的 checkpoint 通道。

    Bug S3：model 参数用于 per-task 模型透传，避免全局 switch_model 在并发任务间串扰。
    """
    _pipeline_start = time.monotonic()
    state = new_state(
        task_id=task_id,
        user_request=user_request,
        extra_args=extra_args,
        skill_id=skill_id,
        model=model,
    )

    # 写一条起始记录
    db.update_task_phase(task_id, phase=PipelinePhase.PENDING.value, summary={"start": True})

    # 发出任务启动事件
    event_bus.emit(EventType.TASK_STARTED, data={"task_id": task_id, "user_request": user_request[:100]}, task_id=task_id)

    # Tracing：以 task_id 为根 span，覆盖整条流水线
    with tracer.start_span("pipeline.run", task_id=task_id, user_request=user_request[:100]) as span:
        if prefer_langgraph and _LANGGRAPH_AVAILABLE:
            try:
                # 方案C：获取 checkpointer（未安装则返回 None，不影响主流程）
                checkpointer = get_checkpointer() if enable_checkpoint else None
                app = build_graph(checkpointer=checkpointer)
                # thread_id = task_id，让 LangGraph 用此 id 区分不同任务的 checkpoint
                config = {"configurable": {"thread_id": task_id}} if checkpointer else None
                result = app.invoke(state, config=config) if config else app.invoke(state)
                # LangGraph 本身不会自动收尾，手动 finalize 一次
                _finalize(result, _pipeline_start)
                span.set_attribute("pipeline.status", "done")
                return result  # type: ignore[return-value]
            except Exception as e:
                logger.warning("LangGraph 执行失败，降级到 LinearPipeline：%s", e)
                span.set_attribute("pipeline.mode", "fallback_linear")
                span.record_exception(e)
                # Bug S2 修复：降级时重建 state，仅保留必要字段，
                # 避免 state 已被部分执行（retry_counter 已自增、code_package 已生成等）
                # 导致 LinearPipeline 从头重跑时重复消耗 token 与 retry_counter 错乱
                fresh_state = new_state(
                    task_id=state.get("task_id", ""),
                    user_request=state.get("user_request", ""),
                    extra_args=state.get("extra_args") or {},
                    skill_id=state.get("skill_id") or "",
                    model=state.get("model"),
                )
                return LinearPipeline.run(fresh_state)

        span.set_attribute("pipeline.mode", "linear")
        result = LinearPipeline.run(state)
        _record_pipeline_metrics(result, _pipeline_start)
        return result


def _record_pipeline_metrics(state: PipelineState, start_time: float) -> None:
    """记录流水线指标并触发告警评估。"""
    if metrics_collector is not None:
        status = state.get("status")
        success = status is not None and getattr(status, "phase", None) == PipelinePhase.DONE
        duration = time.monotonic() - start_time
        metrics_collector.record_pipeline(success=success, duration_seconds=duration)

        # 流水线完成后触发告警评估
        if alert_manager is not None:
            snapshot = metrics_collector.snapshot()
            alerts = alert_manager.evaluate(snapshot)
            for a in alerts:
                logger.warning("流水线告警 [%s]: %s", a.level, a.message)


def resume_pipeline(
    *,
    task_id: str,
    user_request: str = "",
    extra_args: Optional[Dict[str, Any]] = None,
) -> Optional[PipelineState]:
    """方案C：从断点恢复任务执行。

    前提：run_pipeline 时启用了 checkpoint，且任务因异常中断。
    实现：用相同 thread_id（=task_id）再次 invoke，LangGraph 会从最近 checkpoint 恢复。

    返回：
        - 恢复成功并跑完：返回最终 state
        - 无 checkpoint / 无历史 / 恢复失败：返回 None
    """
    if not (_LANGGRAPH_AVAILABLE and _SQLITE_SAVER_AVAILABLE):
        logger.warning("断点续跑不可用：langgraph 或 SqliteSaver 未安装")
        return None

    checkpointer = get_checkpointer()
    if checkpointer is None:
        return None

    try:
        _pipeline_start = time.monotonic()
        app = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": task_id}}
        latest = checkpointer.get_tuple(config)
        if latest is not None and _checkpoint_phase(latest) == PipelinePhase.DONE.value:
            logger.warning("任务 %s 的 checkpoint 已是 DONE，拒绝重复恢复", task_id)
            return None
        # 用 None 作为输入，LangGraph 会从 checkpoint 恢复
        # 如果有 user_request，更新到 state（用于重新触发）
        result = app.invoke(None, config=config)
        _finalize(result, _pipeline_start)
        logger.info("任务 %s 断点续跑完成", task_id)
        return result  # type: ignore[return-value]
    except Exception as e:
        logger.exception("任务 %s 断点续跑失败：%s", task_id, e)
        return None


__all__ = [
    "build_graph",
    "run_pipeline",
    "resume_pipeline",
    "stream_pipeline",
    "LinearPipeline",
    "get_checkpointer",
    "has_checkpoint",
    "close_checkpointer_connections",
    "_checkpoint_phase",
    "_router_after_review",
    "retry_code_gen_node",
]
