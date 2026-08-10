"""Pipeline 新版接口：流式执行 / 审计查询 / 指标快照 / 记忆统计 / Agent 消息。

提供基于 unified_engine 的新一代流水线接口：
- POST   /api/v1/pipeline/stream           True token-level 流式执行
- GET    /api/v1/pipeline/{task_id}/stream  SSE 推送正在运行的任务
- GET    /api/v1/audit/events              查询审计日志
- GET    /api/v1/metrics/snapshot          增强版指标快照（含阶段分解）
- GET    /api/v1/memory/stats             记忆系统统计
- POST   /api/v1/agent/{agent_id}/message  向指定 Agent 发送消息
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.core.audit import (
    ActorType,
    AuditEventType,
    audit_logger,
)
from app.core.config import settings
from app.core.guardrails import input_guardrail
from app.core.memory_system import memory_system
from app.core.observability import metrics_collector
from app.db.sqlite_client import db
from app.graph.unified_engine import stream_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["pipeline"])


# ====================================================================== #
#  请求模型
# ====================================================================== #

class PipelineStreamRequest(BaseModel):
    """True token-level 流式执行请求。"""
    user_request: str = Field(..., min_length=1, description="用户自然语言需求")
    task_id: Optional[str] = Field(default=None, description="可选自定义任务 ID")
    model: Optional[str] = Field(default=None, description="指定 LLM 模型")
    extra_args: Dict[str, Any] = Field(default_factory=dict, description="透传额外参数")
    skill_id: str = Field(default="", description="技能 ID")


class AgentMessageRequest(BaseModel):
    """向指定 Agent 发送消息的请求。"""
    message: str = Field(..., min_length=1, description="发送给 Agent 的消息内容")
    context: Dict[str, Any] = Field(default_factory=dict, description="执行上下文")
    use_code_model: bool = Field(default=False, description="是否使用代码专用模型")


# ====================================================================== #
#  内存状态（与 tasks.py 中的模式一致）
# ====================================================================== #
_running: set[str] = set()
_stream_events: Dict[str, List[Dict[str, Any]]] = {}
_final_states: Dict[str, Any] = {}
_done_events: Dict[str, asyncio.Event] = {}
_lock = asyncio.Lock()
_background_tasks: set = set()
_token_costs: Dict[str, Dict[str, float]] = {}


def _spawn_background(coro):
    """创建后台任务并持有引用，完成时自动从集合中移除。"""
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


async def _delayed_cleanup(task_id: str, delay: float = 300.0) -> None:
    """延迟清理已完成任务的内存在态，避免内存泄漏。"""
    await asyncio.sleep(delay)
    async with _lock:
        if task_id in _running:
            return
        _final_states.pop(task_id, None)
        _done_events.pop(task_id, None)
        _stream_events.pop(task_id, None)
        _token_costs.pop(task_id, None)


async def _run_stream_bg(
    task_id: str, user_request: str,
    extra_args: Optional[Dict[str, Any]],
    skill_id: str = "",
    model: Optional[str] = None,
) -> None:
    """后台执行流式引擎，收集事件与最终状态。

    使用 unified_engine.stream_engine 替代旧 pipeline，
    提供 token 级用量追踪和审计日志。
    """
    start_time = time.monotonic()
    try:
        loop = asyncio.get_running_loop()
        events, state = await loop.run_in_executor(
            None,
            lambda: stream_engine(
                task_id=task_id,
                user_request=user_request,
                extra_args=extra_args,
                skill_id=skill_id,
                model=model,
            ),
        )
        async with _lock:
            _stream_events[task_id] = events
            _final_states[task_id] = state

        status = state.get("status") or {}
        total_tokens = 0
        if isinstance(status, dict):
            total_tokens = status.get("total_tokens", 0) or 0
        elif hasattr(status, "total_tokens"):
            total_tokens = status.total_tokens or 0

        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

        _token_costs[task_id] = {
            "total_tokens": float(total_tokens),
            "duration_ms": elapsed_ms,
            "estimated_cost": 0.0,
        }

        audit_logger.log(
            event_type=AuditEventType.PIPELINE_END,
            actor_type=ActorType.SYSTEM,
            actor_id="unified_engine",
            action="stream_engine_complete",
            output_data={"task_id": task_id, "total_tokens": total_tokens},
            duration_ms=elapsed_ms,
            task_id=task_id,
        )

    except Exception as e:
        logger.exception("任务 %s 执行失败：%s", task_id, e)
        db.mark_task_failed(task_id, str(e))
        audit_logger.log(
            event_type=AuditEventType.PIPELINE_END,
            actor_type=ActorType.SYSTEM,
            actor_id="unified_engine",
            action="stream_engine_failed",
            output_data={"task_id": task_id, "error": str(e)},
            task_id=task_id,
            metadata={"error_type": type(e).__name__},
        )
    finally:
        async with _lock:
            _running.discard(task_id)
            ev = _done_events.get(task_id)
            if ev:
                ev.set()
        _spawn_background(_delayed_cleanup(task_id))


# ====================================================================== #
#  接口实现
# ====================================================================== #

@router.post(
    "/pipeline/stream",
    summary="True token-level 流式执行",
    description=(
        "基于 unified_engine 的真正 token 级流式执行端点。"
        "接收需求后立即返回 task_id，通过 SSE 逐条推送事件，"
        "每条事件包含 token 用量、阶段、进度等细粒度信息。"
    ),
)
async def pipeline_stream(req: PipelineStreamRequest):
    """True token-level 流式执行端点。

    特性：
    - 使用 unified_engine替代旧 pipeline
    - 逐条推送 token 级事件
    - 自动记录审计日志与 token 成本
    - 支持后台异步执行
    """
    if settings.guardrail.input_enabled:
        guard_result = input_guardrail.check(req.user_request)
        if not guard_result.passed and guard_result.blocked:
            raise HTTPException(400, f"输入校验未通过：{guard_result.reason}")

    task_id = req.task_id or uuid.uuid4().hex[:12]

    async with _lock:
        if task_id in _running:
            raise HTTPException(409, f"任务 {task_id} 正在执行中，请勿重复提交")
        _running.add(task_id)
        _done_events[task_id] = asyncio.Event()

    db.create_task(
        task_id=task_id,
        user_request=req.user_request,
        extra_args=req.extra_args,
    )

    audit_logger.log(
        event_type=AuditEventType.PIPELINE_START,
        actor_type=ActorType.USER,
        actor_id="api_client",
        action="pipeline_stream_start",
        input_data={
            "user_request": req.user_request[:200],
            "skill_id": req.skill_id,
            "model": req.model or "",
        },
        task_id=task_id,
    )

    _spawn_background(_run_stream_bg(
        task_id, req.user_request, req.extra_args,
        skill_id=req.skill_id or "",
        model=req.model,
    ))

    async def _gen():
        sent_events = 0
        last_event_time = asyncio.get_event_loop().time()

        while True:
            async with _lock:
                events = _stream_events.get(task_id, [])

            while sent_events < len(events):
                event = events[sent_events]
                sent_events += 1

                token_info = {
                    "phase": event.get("phase", ""),
                    "progress": event.get("progress", 0),
                    "message": event.get("message", ""),
                    "task_id": event.get("task_id", task_id),
                    "timestamp": event.get("timestamp", ""),
                }

                if "token_usage" in event:
                    token_info["token_usage"] = event["token_usage"]

                yield {
                    "event": "token_event",
                    "data": json.dumps(token_info, ensure_ascii=False),
                }
                last_event_time = asyncio.get_event_loop().time()

            state = _final_states.get(task_id)
            if state is not None:
                status = state.get("status")
                is_done = False
                if isinstance(status, dict):
                    phase = status.get("phase", "")
                    is_done = phase in ("done", "failed")
                elif hasattr(status, "phase"):
                    phase_val = status.phase.value if hasattr(status.phase, "value") else str(status.phase)
                    is_done = phase_val in ("done", "failed")

                if is_done or (_done_events.get(task_id) and _done_events[task_id].is_set()):
                    break

            now = asyncio.get_event_loop().time()
            if now - last_event_time > 15:
                yield {"event": "ping", "data": ""}
                last_event_time = now

            await asyncio.sleep(0.3)

        final_state = _final_states.get(task_id)
        if final_state is not None:
            try:
                state_dict = dict(final_state) if not isinstance(final_state, dict) else final_state
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "task_id": task_id,
                        "final_state": str(state_dict)[:2000],
                        "token_cost": _token_costs.get(task_id, {}),
                    }, ensure_ascii=False, default=str),
                }
            except Exception:
                yield {
                    "event": "done",
                    "data": json.dumps({"task_id": task_id, "status": "completed"}),
                }

    return EventSourceResponse(_gen(), ping=15)


@router.get(
    "/pipeline/{task_id}/stream",
    summary="SSE 推送运行中任务",
    description=(
        "SSE 实时推送指定 pipeline 任务的执行进度。"
        "支持 phase / token_event / done / error / ping 事件类型。"
    ),
)
async def pipeline_task_stream(task_id: str):
    """SSE 实时推送 pipeline 任务执行进度。

    推送事件类型：
    - phase: 阶段变更
    - token_event: token 级事件（含 token 用量、进度）
    - done: 任务完成
    - error: 错误
    - ping: 心跳
    """
    task = db.get_task(task_id)
    if task is None and task_id not in _final_states:
        raise HTTPException(404, f"任务 {task_id} 不存在")

    ev = _done_events.get(task_id)
    if ev is None:
        ev = asyncio.Event()
        _done_events[task_id] = ev

    async def _gen():
        last_phase = None
        last_event_time = asyncio.get_event_loop().time()
        sent_events = 0

        while True:
            async with _lock:
                events = _stream_events.get(task_id, [])

            while sent_events < len(events):
                event = events[sent_events]
                sent_events += 1
                yield {
                    "event": "token_event",
                    "data": json.dumps(event, ensure_ascii=False, default=str),
                }
                last_event_time = asyncio.get_event_loop().time()

            current_task = db.get_task(task_id)
            if current_task is None and task_id not in _final_states:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "任务不存在或已删除"}),
                }
                return

            phase = current_task.phase if current_task else "unknown"
            if phase != last_phase:
                yield {"event": "phase", "data": phase}
                last_phase = phase
                last_event_time = asyncio.get_event_loop().time()

            if ev.is_set() or (current_task and current_task.status in ("done", "failed")):
                break

            now = asyncio.get_event_loop().time()
            if now - last_event_time > 15:
                yield {"event": "ping", "data": ""}
                last_event_time = now

            await asyncio.sleep(0.5)

        final_state = _final_states.get(task_id)
        if final_state is not None:
            try:
                state_data = {}
                if hasattr(final_state, "get"):
                    status = final_state.get("status")
                    if status:
                        state_data["status"] = str(status)[:500]
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "task_id": task_id,
                        "state": state_data,
                        "token_cost": _token_costs.get(task_id, {}),
                    }, ensure_ascii=False, default=str),
                }
            except Exception as e:
                yield {"event": "error", "data": str(e)}

    return EventSourceResponse(_gen(), ping=15)


@router.get(
    "/audit/events",
    summary="查询审计日志",
    description=(
        "查询系统级审计日志。支持按事件类型、操作者类型、任务 ID、"
        "时间范围等条件过滤，返回最近 N 条记录。"
    ),
)
async def query_audit_events(
    event_type: str = Query("", description="按审计事件类型过滤"),
    actor_type: str = Query("", description="按操作者类型过滤"),
    task_id: str = Query("", description="按任务 ID 过滤"),
    action: str = Query("", description="按动作名称过滤"),
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
) -> Dict[str, Any]:
    """查询审计日志。

    支持的过滤参数：
    - event_type: agent_execute / tool_call / llm_call / state_transition / human_approval / pipeline_start / pipeline_end
    - actor_type: agent / user / system / tool
    - task_id: 关联的任务 ID
    - action: 具体动作名称
    """
    audit_event = None
    actor = None

    if event_type:
        try:
            audit_event = AuditEventType(event_type)
        except ValueError:
            valid = [e.value for e in AuditEventType]
            raise HTTPException(400, f"非法审计事件类型: {event_type}，可选: {valid}")

    if actor_type:
        try:
            actor = ActorType(actor_type)
        except ValueError:
            valid = [e.value for e in ActorType]
            raise HTTPException(400, f"非法操作者类型: {actor_type}，可选: {valid}")

    entries = audit_logger.query(
        event_type=audit_event,
        actor_type=actor,
        task_id=task_id or None,
        action=action or None,
        limit=limit,
    )

    items = [e.to_dict() for e in entries]
    return {
        "total": len(items),
        "items": items,
        "filters": {
            "event_type": event_type or "all",
            "actor_type": actor_type or "all",
            "task_id": task_id or "all",
            "action": action or "all",
        },
    }


@router.get(
    "/metrics/snapshot",
    summary="增强版指标快照",
    description=(
        "获取包含 LLM 调用、流水线、工具调用、阶段分解的完整指标快照。"
        "比基础 /metrics 端点提供更丰富的阶段级 token/cost 分解。"
    ),
)
async def enhanced_metrics_snapshot() -> Dict[str, Any]:
    """增强版指标快照（含阶段分解）。

    返回内容：
    - llm: LLM 调用统计（调用次数、Token 用量、成本、延迟）
    - pipeline: 流水线执行统计（成功/失败数、成功率、平均耗时）
    - tool: 工具调用统计（调用次数、失败率）
    - stage_breakdown: 按阶段（requirement/code_gen/review/test_gen）分解的 Token/成本
    - token_cost_tracking: 本次会话追踪的 token 成本明细
    """
    snapshot_data = metrics_collector.snapshot()

    stage_breakdown = snapshot_data.get("stage_breakdown", {})

    tracked_costs = {
        tid: {
            "total_tokens": c.get("total_tokens", 0),
            "duration_ms": c.get("duration_ms", 0),
            "estimated_cost": c.get("estimated_cost", 0.0),
        }
        for tid, c in list(_token_costs.items())[-20:]
    }

    return {
        "snapshot": snapshot_data,
        "stage_breakdown": stage_breakdown,
        "token_cost_tracking": tracked_costs,
        "tracked_task_count": len(_token_costs),
        "timestamp": datetime.now().isoformat(),
    }


@router.get(
    "/memory/stats",
    summary="记忆系统统计",
    description=(
        "获取三层记忆系统（工作记忆/情景记忆/语义记忆）的统计信息，"
        "包括条目数、容量使用率、情景事件摘要、语义记忆 RAG 状态等。"
    ),
)
async def memory_stats() -> Dict[str, Any]:
    """记忆系统统计。

    返回内容：
    - working: 工作记忆统计（条目数、容量上限）
    - episodic: 情景记忆统计（事件数、类型分布、时间跨度摘要）
    - semantic: 语义记忆统计（RAG 状态、知识条目数）
    """
    try:
        stats = memory_system.get_system_stats()
        return {
            "status": "ok",
            "stats": stats,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.exception("获取记忆统计失败：%s", e)
        raise HTTPException(
            500,
            detail={"message": f"获取记忆统计失败: {e}", "error_code": "MEMORY_STATS_ERROR"},
        )


@router.post(
    "/agent/{agent_id}/message",
    summary="向指定 Agent 发送消息",
    description=(
        "向系统中指定 ID 的 Agent 发送一条消息，Agent 会基于自身角色和能力进行处理，"
        "返回执行结果。支持所有已注册的 Agent（architect / coder / reviewer / tester / orchestrator）。"
    ),
)
async def send_agent_message(agent_id: str, req: AgentMessageRequest):
    """向指定 Agent 发送消息并获取执行结果。

    Args:
        agent_id: 目标 Agent 的 ID（如 architect_executor、coder_executor 等）
        req: 包含 message 和可选 context 的请求体

    Returns:
        Agent 执行结果，包含 success / output / metrics / error 等字段
    """
    if not req.message.strip():
        raise HTTPException(400, "消息内容不能为空")

    pool = _get_or_create_pool()

    executor = pool.get(agent_id)
    if executor is None:
        available = pool.list_available()
        raise HTTPException(
            404,
            detail={
                "message": f"Agent '{agent_id}' 不存在",
                "available_agents": available,
            },
        )

    task_id = f"msg_{uuid.uuid4().hex[:12]}"
    task_desc = req.message.strip()

    audit_logger.log(
        event_type=AuditEventType.AGENT_EXECUTE,
        actor_type=ActorType.USER,
        actor_id="api_client",
        action="agent_message_send",
        input_data={"message": task_desc[:500], "agent_id": agent_id},
        task_id=task_id,
        metadata={"agent_id": agent_id, "context_keys": list(req.context.keys())},
    )

    loop = asyncio.get_running_loop()
    start_time = time.monotonic()

    try:
        from app.agents.agent_protocol import AgentTask
        task = AgentTask(
            task_id=task_id,
            description=task_desc,
            assigned_to=agent_id,
        )

        context = {
            "user_request": task_desc,
            **req.context,
        }

        result = await loop.run_in_executor(
            None,
            lambda: executor.execute_task(task, context),
        )

        elapsed_ms = round((time.monotonic() - start_time) * 1000, 2)

        audit_logger.log(
            event_type=AuditEventType.AGENT_EXECUTE,
            actor_type=ActorType.AGENT,
            actor_id=agent_id,
            action="agent_message_complete",
            input_data={"message": task_desc[:500]},
            output_data={
                "success": result.success,
                "output_length": len(result.output),
                "error": result.error[:500] if result.error else "",
            },
            duration_ms=elapsed_ms,
            task_id=task_id,
        )

        return {
            "task_id": task_id,
            "agent_id": agent_id,
            "success": result.success,
            "output": result.output,
            "artifacts": result.artifacts,
            "metrics": result.metrics,
            "error": result.error,
            "execution_time": round(result.execution_time, 3),
            "token_usage": {
                "prompt_tokens": result.metrics.get("prompt_tokens", 0),
                "completion_tokens": result.metrics.get("completion_tokens", 0),
                "total_tokens": result.metrics.get("total_tokens", 0),
            },
        }

    except Exception as e:
        logger.exception("Agent %s 消息执行失败：%s", agent_id, e)
        audit_logger.log(
            event_type=AuditEventType.AGENT_EXECUTE,
            actor_type=ActorType.AGENT,
            actor_id=agent_id,
            action="agent_message_failed",
            output_data={"error": str(e)},
            duration_ms=round((time.monotonic() - start_time) * 1000, 2),
            task_id=task_id,
            metadata={"error_type": type(e).__name__},
        )
        raise HTTPException(
            500,
            detail={
                "message": f"Agent '{agent_id}' 执行失败: {e}",
                "error_code": "AGENT_EXECUTION_FAILED",
                "task_id": task_id,
            },
        )


# ====================================================================== #
#  辅助：AgentPool 惰性初始化
# ====================================================================== #

_agent_pool_instance = None


def _get_or_create_pool():
    """获取或创建 AgentPool 单例（避免循环导入）。"""
    global _agent_pool_instance
    if _agent_pool_instance is not None:
        return _agent_pool_instance

    from app.agent_executor import (
        AgentPool,
        ArchitectExecutor,
        CoderExecutor,
        OrchestratorExecutor,
        ReviewerExecutor,
        TesterExecutor,
    )

    pool = AgentPool()
    pool.register(ArchitectExecutor())
    pool.register(CoderExecutor())
    pool.register(ReviewerExecutor())
    pool.register(TesterExecutor())
    pool.register(OrchestratorExecutor())
    _agent_pool_instance = pool
    return pool


__all__ = ["router"]