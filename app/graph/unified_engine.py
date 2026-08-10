"""NeuroForge V2 统一编排引擎。

替代 pipeline.py + unified_orchestrator.py 的双架构，提供单一入口：
- run_engine()     同步执行
- stream_engine()  流式执行（SSE）
- resume_engine()  断点恢复
- build_engine()   构建 LangGraph StateGraph

核心特性：
- SupervisorAgent 智能路由（不可用时回退到规则路由）
- PlannerAgent Plan-Execute-Reflect 循环（可选）
- LangGraph 主模式 + LinearEngine 线性兜底
- SqliteSaver 断点续跑 / EventBus 事件 / MetricsCollector Token 追踪
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from langgraph.graph import END, StateGraph

    _LANGGRAPH_AVAILABLE = True
except Exception:
    _LANGGRAPH_AVAILABLE, END = False, "__end__"

try:
    from langgraph.checkpoint.sqlite import SqliteSaver

    _SQLITE_SAVER_AVAILABLE = True
except Exception:
    _SQLITE_SAVER_AVAILABLE, SqliteSaver = False, None

from app.agents.code_gen_agent import code_gen_node
from app.agents.requirement_agent import requirement_node
from app.agents.review_agent import review_node
from app.agents.test_gen_agent import test_gen_node
from app.core.config import settings
from app.core.events import EventType, event_bus
from app.core.llm_adapter import LLMUsage, llm
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
)

try:
    from app.agents.supervisor_agent import SupervisorAgent, SupervisorDecision
    from app.agents.planner_agent import PlannerAgent

    _SUPERVISOR_AVAILABLE, _PLANNER_AVAILABLE = True, True
except Exception:
    SupervisorAgent, SupervisorDecision, PlannerAgent = None, None, None
    _SUPERVISOR_AVAILABLE, _PLANNER_AVAILABLE = False, False

try:
    from app.core.observability import metrics_collector
except ImportError:
    metrics_collector = None

logger = logging.getLogger(__name__)

_CHECKPOINT_DB_PATH = Path("data/checkpoints.db")
_checkpointer_singleton: Any = None
_checkpointer_lock = threading.Lock()


def _get_checkpointer():
    """获取 SqliteSaver 单例，不可用时返回 None。"""
    global _checkpointer_singleton
    if not _SQLITE_SAVER_AVAILABLE:
        return None
    if _checkpointer_singleton is not None:
        return _checkpointer_singleton
    with _checkpointer_lock:
        if _checkpointer_singleton is not None:
            return _checkpointer_singleton
        try:
            _CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _checkpointer_singleton = SqliteSaver(sqlite3.connect(str(_CHECKPOINT_DB_PATH), check_same_thread=False))
            logger.info("SqliteSaver 初始化完成：%s", _CHECKPOINT_DB_PATH)
            return _checkpointer_singleton
        except Exception as e:
            logger.warning("SqliteSaver 初始化失败：%s", e)
            return None


class _StageMetrics:
    """分阶段 Token 用量收集器。"""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, int]] = {}

    def record(self, stage: str, usage: LLMUsage) -> None:
        entry = self._data.setdefault(stage, {"prompt": 0, "completion": 0, "total": 0})
        entry["prompt"] += usage.prompt_tokens
        entry["completion"] += usage.completion_tokens
        entry["total"] += usage.total_tokens

    def to_dict(self) -> Dict[str, Dict[str, int]]:
        return {k: dict(v) for k, v in self._data.items()}


def _emit(task_id: str, evt_type: EventType, data: Dict[str, Any]) -> None:
    event_bus.emit(evt_type, data=data, task_id=task_id)


def _stream_evt(phase: str, progress: int, message: str, task_id: str = "", **extra: Any) -> Dict[str, Any]:
    evt: Dict[str, Any] = {
        "phase": phase,
        "progress": progress,
        "message": message,
        "task_id": task_id,
        "timestamp": datetime.now().isoformat(),
    }
    evt.update(extra)
    return evt


def _supervisor_entry(state: PipelineState) -> Dict[str, Any]:
    """Supervisor 智能路由节点，优先使用 SupervisorAgent，不可用时回退规则路由。"""
    task_id = state.get("task_id", "")
    node_outputs = state.get("node_outputs", {})
    human_approval = state.get("human_approval")

    if human_approval is not None:
        decision = "human_in_loop"
    elif _SUPERVISOR_AVAILABLE and SupervisorAgent is not None:
        try:
            result: SupervisorDecision = SupervisorAgent().decide(state)
            decision = getattr(result, "action", str(result))
        except Exception as e:
            logger.warning("SupervisorAgent 决策失败，回退规则路由：%s", e)
            decision = _rule_based_route(state)
    else:
        decision = _rule_based_route(state)

    logger.info("Supervisor 决策: %s (phase=%s)", decision, ensure_status(state).phase)
    return {"supervisor_decision": decision, "node_outputs": {**node_outputs, "supervisor": decision}}


def _rule_based_route(state: PipelineState) -> str:
    """规则路由（SupervisorAgent 不可用时的兜底）。"""
    phase = ensure_status(state).phase
    report = state.get("review_report") or ReviewReport()
    counter = int(state.get("retry_counter") or 0)

    if phase == PipelinePhase.PENDING:
        return "requirement"
    if phase == PipelinePhase.REQUIREMENT:
        return "code_gen"
    if phase == PipelinePhase.CODE_GEN:
        return "review"
    if phase == PipelinePhase.CODE_REVIEW:
        if report.verdict == ReviewVerdict.REJECTED:
            return "done"
        if report.need_fix and counter < settings.agent.max_review_retry:
            return "code_gen"
        if report.need_fix:
            return "done"
        if report.verdict == ReviewVerdict.APPROVED:
            return "test_gen" if should_enter_test_gen(state) else "done"
        return "done"
    if phase == PipelinePhase.TEST_GEN:
        return "done"
    return "done"


def _planner_node(state: PipelineState) -> Dict[str, Any]:
    """PlannerAgent Plan-Execute-Reflect 循环节点（可选）。"""
    if not _PLANNER_AVAILABLE or PlannerAgent is None:
        return {}
    try:
        return {"planner_output": PlannerAgent().plan(state)}
    except Exception as e:
        logger.warning("PlannerAgent 规划失败：%s", e)
        return {}


def _retry_inc_node(state: PipelineState) -> Dict[str, Any]:
    counter = int(state.get("retry_counter") or 0)
    status = ensure_status(state)
    status.phase = PipelinePhase.CODE_GEN
    status.touch()
    return {"retry_counter": counter + 1, "status": status}


def _human_approval_node(state: PipelineState) -> Dict[str, Any]:
    """Human-in-the-Loop 审批节点。"""
    task_id = state.get("task_id", "")
    human_approval = state.get("human_approval") or {}
    _emit(task_id, EventType.HITL_WAITING, {"approval_data": human_approval})
    status = ensure_status(state)
    status.phase = PipelinePhase.HITL
    status.touch()
    approval_status = human_approval.get("status")
    if approval_status == "approved":
        _emit(task_id, EventType.HITL_APPROVED, human_approval)
        return {"status": status, "human_approval": None}
    if approval_status == "rejected":
        _emit(task_id, EventType.HITL_REJECTED, human_approval)
        status.phase = PipelinePhase.FAILED
        status.error_message = human_approval.get("reason", "人工审批未通过")
        status.touch()
        return {"status": status, "human_approval": None}
    return {"status": status}


def build_engine(checkpointer: Any = None, enable_planner: bool = True) -> Any:
    """构建 LangGraph StateGraph。

    Args:
        checkpointer: SqliteSaver 实例，None 则不持久化
        enable_planner: 是否启用 PlannerAgent Plan-Execute-Reflect 节点

    Returns:
        编译后的 LangGraph 应用；未安装 langgraph 时抛 RuntimeError
    """
    if not _LANGGRAPH_AVAILABLE:
        raise RuntimeError("langgraph 未安装，请 pip install langgraph")
    wf = StateGraph(PipelineState)
    wf.add_node("supervisor", _supervisor_entry)
    wf.add_node("requirement", requirement_node)
    wf.add_node("code_gen", code_gen_node)
    wf.add_node("review", review_node)
    wf.add_node("test_gen", test_gen_node)
    wf.add_node("retry_inc", _retry_inc_node)
    wf.add_node("human_in_loop", _human_approval_node)
    if enable_planner and _PLANNER_AVAILABLE:
        wf.add_node("planner", _planner_node)
    wf.set_entry_point("supervisor")
    wf.add_conditional_edges(
        "supervisor",
        lambda s: s.get("supervisor_decision", "done"),
        {
            "requirement": "requirement",
            "code_gen": "code_gen",
            "review": "review",
            "test_gen": "test_gen",
            "human_in_loop": "human_in_loop",
            "done": END,
        },
    )
    for step in ("requirement", "code_gen", "review", "test_gen", "human_in_loop"):
        wf.add_edge(step, "supervisor")
    wf.add_edge("retry_inc", "code_gen")
    return wf.compile(checkpointer=checkpointer) if checkpointer is not None else wf.compile()


def _finalize_state(state: PipelineState, start_time: float = 0.0) -> None:
    """收尾：持久化状态、发事件、沉淀记忆。"""
    status = state.get("status") or PipelineStatus()
    status.touch()
    if start_time > 0:
        status.duration_seconds = round(time.monotonic() - start_time, 2)
    task_id = state.get("task_id", "")
    summary: Dict[str, Any] = {}
    report = state.get("review_report")
    if report:
        summary["review"] = {
            "verdict": report.verdict.value,
            "score": report.score,
            "summary": report.summary,
            "issues_count": len(report.issues),
        }
    test_r = state.get("test_report")
    if test_r:
        summary["test"] = {
            "status": test_r.status.value,
            "summary": test_r.summary,
            "total": test_r.total,
            "passed": test_r.passed,
            "failed": test_r.failed,
            "error_count": test_r.error_count,
        }
    retry_counter = int(state.get("retry_counter") or 0)
    is_failed = False
    if report and report.verdict == ReviewVerdict.REJECTED:
        status.error_message, is_failed = "代码评审未通过", True
    elif report and report.need_fix and retry_counter >= settings.agent.max_review_retry:
        status.error_message, is_failed = "重试次数已达上限", True
    if is_failed:
        failed_phase = status.phase.value if hasattr(status.phase, "value") else str(status.phase)
        status.phase = PipelinePhase.FAILED
        db.mark_task_failed(task_id, status.error_message, summary=summary, failed_stage=failed_phase)
        _emit(task_id, EventType.PIPELINE_FAILED, {"error": status.error_message})
    else:
        status.phase = PipelinePhase.DONE
        db.mark_task_done(task_id, summary=summary)
        _emit(task_id, EventType.PIPELINE_DONE, {})
    state["status"] = status
    if metrics_collector is not None:
        try:
            metrics_collector.record_pipeline(
                success=status.phase == PipelinePhase.DONE, duration_seconds=status.duration_seconds
            )
        except Exception:
            pass
    if settings.memory.enabled:
        try:
            from app.core.memory import memory

            code_pkg = state.get("code_package")
            memory.save_experience(
                task_id=task_id,
                user_request=state.get("user_request", ""),
                review_verdict=report.verdict.value if report else "",
                review_score=report.score if report else 0,
                review_issue_count=len(report.issues) if report else 0,
                test_status=test_r.status.value if test_r else "",
                test_pass_rate=(test_r.passed / max(test_r.total, 1)) if test_r else 0.0,
                skill_id=state.get("skill_id", ""),
                code_files=[f.path for f in code_pkg.files] if code_pkg else [],
                error_message=status.error_message,
            )
        except Exception as e:
            logger.warning("记忆沉淀失败：%s", e)


def run_engine(
    task_id: str,
    user_request: str,
    *,
    extra_args: Optional[Dict[str, Any]] = None,
    mode: str = "auto",
    enable_checkpoint: bool = True,
    skill_id: str = "",
    model: Optional[str] = None,
) -> PipelineState:
    """同步执行引擎。

    Args:
        task_id: 任务唯一标识
        user_request: 用户原始需求
        extra_args: 透传参数
        mode: "auto" | "langgraph" | "linear"
        enable_checkpoint: 是否启用断点续跑
        skill_id: 技能 ID
        model: per-task 模型名

    Returns:
        最终 PipelineState
    """
    state = new_state(task_id=task_id, user_request=user_request, extra_args=extra_args, skill_id=skill_id, model=model)
    db.update_task_phase(task_id, phase=PipelinePhase.PENDING.value, summary={"start": True})
    _emit(task_id, EventType.TASK_STARTED, {"task_id": task_id})
    start = time.monotonic()
    if mode in ("auto", "langgraph") and _LANGGRAPH_AVAILABLE:
        try:
            return _run_langgraph(state, enable_checkpoint, start)
        except Exception as e:
            if mode == "langgraph":
                raise
            logger.warning("LangGraph 执行失败，降级到 LinearEngine：%s", e)
    return LinearEngine.run(state, start_time=start)


def stream_engine(
    task_id: str,
    user_request: str,
    *,
    extra_args: Optional[Dict[str, Any]] = None,
    skill_id: str = "",
    model: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], PipelineState]:
    """流式执行引擎，返回 (事件列表, 最终状态)。SSE 推送时逐条发送事件列表中的条目。"""
    events: List[Dict[str, Any]] = []
    state = new_state(task_id=task_id, user_request=user_request, extra_args=extra_args, skill_id=skill_id, model=model)
    db.update_task_phase(task_id, phase=PipelinePhase.PENDING.value, summary={"start": True})
    events.append(_stream_evt("pending", 5, "任务启动", task_id))

    def _apply(node_name: str, fn: Callable) -> None:
        events.append(_stream_evt(node_name, 10, f"开始 {node_name}", task_id))
        try:
            patch = fn(state)
            if patch:
                for k, v in patch.items():
                    if v is not None:
                        state[k] = v  # type: ignore[literal-required]
            events.append(_stream_evt(node_name, 100, f"{node_name} 完成", task_id))
        except Exception as e:
            events.append(_stream_evt(node_name, -1, f"{node_name} 失败: {e}", task_id))
            raise

    try:
        for name, fn in [("requirement", requirement_node), ("code_gen", code_gen_node), ("review", review_node)]:
            _apply(name, fn)
        route = _rule_based_route(state)
        if route == "code_gen":
            counter = int(state.get("retry_counter") or 0)
            status = ensure_status(state)
            status.phase = PipelinePhase.CODE_GEN
            state["retry_counter"], state["status"] = counter + 1, status
            events.append(_stream_evt("code_gen", 10, "代码需修改，重新生成", task_id))
            _apply("code_gen", code_gen_node)
            _apply("review", review_node)
            route = _rule_based_route(state)
        if route == "test_gen":
            _apply("test_gen", test_gen_node)
        _finalize_state(state, time.monotonic())
        events.append(_stream_evt("done", 100, "执行完成", task_id))
    except Exception as e:
        _finalize_state(state, time.monotonic())
        events.append(_stream_evt("done", -1, f"执行失败: {e}", task_id))
    return events, state


def resume_engine(
    task_id: str,
    user_request: str = "",
    *,
    extra_args: Optional[Dict[str, Any]] = None,
) -> Optional[PipelineState]:
    """从断点恢复执行，要求启用了 SqliteSaver checkpoint。"""
    if not (_LANGGRAPH_AVAILABLE and _SQLITE_SAVER_AVAILABLE):
        logger.warning("断点续跑不可用：langgraph 或 SqliteSaver 未安装")
        return None
    checkpointer = _get_checkpointer()
    if checkpointer is None:
        return None
    try:
        app = build_engine(checkpointer=checkpointer)
        result = app.invoke(None, config={"configurable": {"thread_id": task_id}})
        _finalize_state(result, time.monotonic())
        logger.info("任务 %s 断点续跑完成", task_id)
        return result  # type: ignore[return-value]
    except Exception as e:
        logger.exception("任务 %s 断点续跑失败：%s", task_id, e)
        return None


def _run_langgraph(state: PipelineState, enable_checkpoint: bool, start: float) -> PipelineState:
    task_id = state.get("task_id", "")
    checkpointer = _get_checkpointer() if enable_checkpoint else None
    app = build_engine(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": task_id}} if checkpointer else None
    result = app.invoke(state, config=config) if config else app.invoke(state)
    _finalize_state(result, start)
    return result  # type: ignore[return-value]


class LinearEngine:
    """不依赖 langgraph 的线性+条件执行器，与 build_engine 行为对齐。"""

    @staticmethod
    def run(state: PipelineState, *, start_time: float = 0.0) -> PipelineState:
        task_id = state.get("task_id", "")
        for _ in range(50):
            for k, v in _supervisor_entry(state).items():
                if v is not None:
                    state[k] = v  # type: ignore[literal-required]
            decision = state.get("supervisor_decision", "done")
            if decision == "human_in_loop":
                for k, v in _human_approval_node(state).items():
                    if v is not None:
                        state[k] = v  # type: ignore[literal-required]
                if ensure_status(state).phase == PipelinePhase.FAILED:
                    _finalize_state(state, start_time)
                    return state
                continue
            if decision == "done":
                _finalize_state(state, start_time)
                return state
            node_map = {
                "requirement": requirement_node,
                "code_gen": code_gen_node,
                "review": review_node,
                "test_gen": test_gen_node,
            }
            if decision in node_map:
                _apply_linear(decision, node_map[decision], state, task_id)
            else:
                logger.warning("未知决策: %s", decision)
                break
        status = ensure_status(state)
        status.phase, status.error_message = PipelinePhase.FAILED, "超出最大迭代次数 50"
        state["status"] = status
        _finalize_state(state, start_time)
        return state


def _apply_linear(node_name: str, fn: Callable, state: PipelineState, task_id: str) -> None:
    _emit(task_id, EventType.TASK_STARTED, {"node": node_name, "phase": node_name})
    try:
        patch = fn(state)
    except Exception as e:
        _emit(task_id, EventType.NODE_FAILED, {"node": node_name, "error": str(e)})
        raise
    if patch:
        for k, v in patch.items():
            if v is not None:
                state[k] = v  # type: ignore[literal-required]
    _emit(task_id, EventType.NODE_COMPLETED, {"node": node_name})


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
    """旧 API：执行完整流水线（向后兼容）。"""
    return run_engine(
        task_id=task_id,
        user_request=user_request,
        extra_args=extra_args,
        mode="langgraph" if prefer_langgraph else "linear",
        enable_checkpoint=enable_checkpoint,
        skill_id=skill_id,
        model=model,
    )


def resume_pipeline(
    *, task_id: str, user_request: str = "", extra_args: Optional[Dict[str, Any]] = None
) -> Optional[PipelineState]:
    """旧 API：从断点恢复（向后兼容）。"""
    return resume_engine(task_id, user_request, extra_args=extra_args)


def stream_pipeline(
    *,
    task_id: str,
    user_request: str,
    extra_args: Optional[Dict[str, Any]] = None,
    skill_id: str = "",
    model: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], PipelineState]:
    """旧 API：流式执行（向后兼容）。"""
    return stream_engine(
        task_id=task_id, user_request=user_request, extra_args=extra_args, skill_id=skill_id, model=model
    )


__all__ = [
    "build_engine",
    "run_engine",
    "stream_engine",
    "resume_engine",
    "LinearEngine",
    "run_pipeline",
    "resume_pipeline",
    "stream_pipeline",
]
