"""任务相关接口（文档 3.6）：
- POST   /api/v1/tasks                 提交代码生成需求，异步执行
- GET    /api/v1/tasks                  任务列表
- GET    /api/v1/tasks/{task_id}        查询任务状态与结果
- GET    /api/v1/tasks/{task_id}/stream  SSE 实时推送执行进度
- GET    /api/v1/tasks/{task_id}/logs    全链路执行日志
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.guardrails import input_guardrail
from app.core.llm_adapter import llm
from app.db.sqlite_client import db
from app.graph.pipeline import run_pipeline, stream_pipeline
from app.graph.state import (
    CodePackage,
    PipelinePhase,
    PipelineState,
    PipelineStatus,
    RequirementSpec,
    ReviewReport,
    TestReport,
)
from app.schemas.pipeline_schemas import (
    ApprovalRequest,
    ApprovalResponse,
    CreatePipelineRequest,
    CreatePipelineResponse,
    GeneratedFileOut,
    PipelineListResponse,
    PipelineStatusOut,
    RequirementSpecOut,
    ReviewIssueOut,
    ReviewReportOut,
    TaskDetailResponse,
    TestCaseOut,
    TestReportOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


# ====================================================================== #
#  内存任务存储（与 DB 互为补充：DB 存可追溯日志，内存存最终 Pydantic 产物）
# ====================================================================== #
_final_states: Dict[str, PipelineState] = {}
_done_events: Dict[str, asyncio.Event] = {}
_running: set[str] = set()
_lock = asyncio.Lock()

# 流式事件存储：task_id -> List[Dict]，供 SSE 端点推送中间状态
_stream_events: Dict[str, List[Dict[str, Any]]] = {}

# Bug S5 修复：持有后台 Task 引用，避免被事件循环 GC 回收导致静默中断
_background_tasks: set = set()


def _spawn_background(coro):
    """创建后台任务并持有引用，完成时自动从集合中移除。

    Python 文档明确警告：asyncio.create_task 返回的 Task 若不持有引用，
    可能被事件循环垃圾回收，导致后台任务静默中断。
    """
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


async def _delayed_cleanup(task_id: str, delay: float = 300.0) -> None:
    """Bug P5 修复：延迟清理已完成任务的内存在态，避免 _final_states / _done_events 无限增长。

    delay 期间若该 task_id 被重新使用（新任务复用同 ID），_running 中会出现该 ID，
    此时跳过清理以避免误删新任务的 state。
    """
    await asyncio.sleep(delay)
    async with _lock:
        # 若该 task_id 仍在新任务中运行，跳过清理（避免误删）
        if task_id in _running:
            return
        _final_states.pop(task_id, None)
        _done_events.pop(task_id, None)


async def _run_pipeline_bg(task_id: str, user_request: str,
                           extra_args: Optional[Dict[str, Any]],
                           prefer_langgraph: bool,
                           skill_id: str = "",
                           model: Optional[str] = None) -> None:
    """后台执行流水线：同步 stream_pipeline 放线程池跑，避免阻塞事件循环。

    stream_pipeline 同时返回流式事件和最终 state，避免了原先
    跑 stream_pipeline + run_pipeline 两遍导致的 LLM 重复调用。
    """
    loop = asyncio.get_running_loop()
    try:
        # 同时获取流式事件和最终状态（一次执行，双重用途）
        events, state = await loop.run_in_executor(
            None,
            lambda: stream_pipeline(
                task_id=task_id,
                user_request=user_request,
                extra_args=extra_args,
                skill_id=skill_id,
                model=model,
            ),
        )
        # 存储流式事件供 SSE 使用
        async with _lock:
            _stream_events[task_id] = events
        # 存储最终状态供 API 查询
        _final_states[task_id] = state
    except Exception as e:
        logger.exception("任务 %s 执行失败：%s", task_id, e)
        db.mark_task_failed(task_id, str(e))
    finally:
        async with _lock:
            _running.discard(task_id)
            ev = _done_events.get(task_id)
            if ev:
                ev.set()
        # Bug P5：延迟 300s 清理内存在态，避免内存泄漏
        _spawn_background(_delayed_cleanup(task_id))


# ====================================================================== #
#  内部模型 -> 对外 Schema 转换
# ====================================================================== #
def _status_out(s: Optional[PipelineStatus]) -> PipelineStatusOut:
    s = s or PipelineStatus()
    return PipelineStatusOut(
        phase=s.phase.value if hasattr(s.phase, "value") else str(s.phase),
        started_at=s.started_at,
        updated_at=s.updated_at,
        prompt_tokens=s.prompt_tokens,
        completion_tokens=s.completion_tokens,
        total_tokens=s.total_tokens,
        duration_seconds=s.duration_seconds,
        error_message=s.error_message,
    )


def _req_out(req: Optional[RequirementSpec]) -> RequirementSpecOut:
    req = req or RequirementSpec()
    return RequirementSpecOut(
        title=req.title,
        goal=req.goal,
        functional_requirements=list(req.functional_requirements),
        non_functional_requirements=list(req.non_functional_requirements),
        tech_stack=list(req.tech_stack),
        acceptance_criteria=list(req.acceptance_criteria),
        assumptions=list(req.assumptions),
    )


def _files_out(pkg: Optional[CodePackage]) -> List[GeneratedFileOut]:
    if not pkg or not pkg.files:
        return []
    return [
        GeneratedFileOut(
            path=f.path, language=f.language,
            content=f.content, description=f.description,
            line_count=f.line_count,
        )
        for f in pkg.files
    ]


def _review_out(r: Optional[ReviewReport]) -> Optional[ReviewReportOut]:
    if not r:
        return None
    return ReviewReportOut(
        verdict=r.verdict.value,
        summary=r.summary,
        score=r.score,
        coverage_check=dict(r.coverage_check or {}),
        issues=[
            ReviewIssueOut(
                severity=str(i.severity).lower() if not hasattr(i.severity, "value")
                else i.severity.value,
                file_path=i.file_path,
                line_start=i.line_start,
                rule_id=i.rule_id,
                message=i.message,
                suggestion=i.suggestion,
            )
            for i in r.issues
        ],
    )


def _test_out(t: Optional[TestReport]) -> Optional[TestReportOut]:
    if not t:
        return None
    files = []
    for f in (t.test_files or []):
        if isinstance(f, dict):
            files.append(GeneratedFileOut(
                path=str(f.get("path", "")),
                language=str(f.get("language", "python")),
                content=str(f.get("content", "")),
                description=str(f.get("description", "")),
            ))
    return TestReportOut(
        status=t.status.value,
        summary=t.summary,
        total=t.total_tests_count,
        passed=t.passed,
        failed=t.failed,
        skipped=t.skipped,
        error_count=t.error_count,
        duration_ms=t.duration_ms,
        run_command=t.run_command,
        test_cases=[
            TestCaseOut(
                name=c.name, description=c.description,
                target_file=c.target_file, target_function=c.target_function,
                priority=c.priority, status=c.status,
                error_message=c.error_message, output=c.output,
                duration_ms=c.duration_ms, code_snippet=c.code_snippet,
            )
            for c in (t.test_cases or [])
        ],
        test_files=files,
    )


# ====================================================================== #
#  接口实现
# ====================================================================== #
@router.post("", response_model=CreatePipelineResponse)
async def create_task(req: CreatePipelineRequest) -> CreatePipelineResponse:
    """提交一个代码生成任务，立即返回 task_id，后台异步执行。"""
    # Prompt 注入防护检查
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

    # Bug S3：改为 per-task 模型透传，避免全局 switch_model 在并发任务间串扰。
    # 不再切换全局模型，而是把 req.model 透传给 run_pipeline → state["model"]，
    # 由 LLMAdapter._resolve_client 在调用时构建临时 client（解析失败回退默认 client）。
    task_model: Optional[str] = req.model or None
    if task_model:
        logger.info("任务 %s 指定模型：%s（per-task 透传）", task_id, task_model)
    else:
        logger.info("任务 %s 使用默认模型：%s", task_id, llm.current_model)

    db.create_task(
        task_id=task_id, user_request=req.user_request,
        extra_args=req.extra_args,
    )
    # Bug S5：用 _spawn_background 持有 Task 引用，避免被 GC 回收
    _spawn_background(_run_pipeline_bg(
        task_id, req.user_request, req.extra_args, req.prefer_langgraph,
        skill_id=req.skill_id or "",
        model=task_model,
    ))

    return CreatePipelineResponse(
        task_id=task_id,
        status=PipelineStatusOut(phase=PipelinePhase.PENDING.value),
        requirement=RequirementSpecOut(),
        code=[],
        review=None,
        test=None,
        retry_counter=0,
    )


@router.get("", response_model=PipelineListResponse)
async def list_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PipelineListResponse:
    """任务列表（按提交时间倒序）。"""
    rows = db.list_tasks(limit=limit, offset=offset)
    items = []
    for r in rows:
        items.append({
            "task_id": r.task_id,
            "user_request": (r.user_request[:80] + "…") if len(r.user_request) > 80 else r.user_request,
            "phase": r.phase,
            "status": r.status,
            "total_tokens": r.total_tokens,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_seconds": r.duration_seconds,
        })
    return PipelineListResponse(items=items, total=len(items))


def _build_detail(task_id: str) -> TaskDetailResponse:
    """从内存 final state + DB 组装任务详情。"""
    state = _final_states.get(task_id)
    task = db.get_task(task_id)
    if task is None and state is None:
        raise HTTPException(404, f"任务 {task_id} 不存在")

    if state is not None:
        status = state.get("status") or PipelineStatus()
        req = state.get("requirement")
        pkg = state.get("code_package")
        review = state.get("review_report")
        test = state.get("test_report")
        retry = int(state.get("retry_counter") or 0)
        review_out = _review_out(review)
        test_out = _test_out(test)
    else:
        status = PipelineStatus(phase=PipelinePhase(task.phase) if task else PipelinePhase.PENDING)
        req, pkg, retry = None, None, 0
        review_out, test_out = None, None
        # 服务重启后 _final_states 丢失，从 DB summary 中恢复 review/test 摘要
        if task:
            summary = task.summary or {}
            review_summary = summary.get("review")
            test_summary = summary.get("test")
            if review_summary:
                review_out = ReviewReportOut(
                    verdict=review_summary.get("verdict", "approved"),
                    summary=review_summary.get("summary", ""),
                    score=review_summary.get("score", 0),
                    coverage_check={},
                    issues=[],
                )
            if test_summary:
                test_out = TestReportOut(
                    status=test_summary.get("status", "generated"),
                    summary=test_summary.get("summary", ""),
                    total=test_summary.get("total", 0),
                    passed=test_summary.get("passed", 0),
                    failed=test_summary.get("failed", 0),
                    skipped=0,
                    error_count=test_summary.get("error_count", 0),
                    duration_ms=0,
                    run_command="",
                    test_cases=[],
                    test_files=[],
                )

    # 如果内存中没有代码包，从 DB 读取文件内容
    code_files = _files_out(pkg)
    if not code_files:
        db_files = db.list_files(task_id)
        code_files = [
            GeneratedFileOut(
                path=f.path,
                language=f.language or "python",
                content=f.content or "",
                description=f.description or "",
                line_count=f.line_count or 0,
            )
            for f in db_files
        ]

    logs = [
        {
            "stage": l.stage,
            "prompt_tokens": l.prompt_tokens,
            "completion_tokens": l.completion_tokens,
            "total_tokens": l.total_tokens,
            "duration_ms": l.duration_ms,
            "error": l.error,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in db.list_stage_logs(task_id)
    ]
    files = [
        {"path": f.path, "language": f.language, "line_count": f.line_count,
         "source_stage": f.source_stage, "content": f.content or ""}
        for f in db.list_files(task_id)
    ]

    task_dict = {
        "task_id": task_id,
        "user_request": task.user_request if task else "",
        "phase": status.phase.value if hasattr(status.phase, "value") else str(status.phase),
        "status": task.status if task else "running",
        "total_tokens": task.total_tokens if task else status.total_tokens,
    }

    return TaskDetailResponse(
        task=task_dict,
        requirement=_req_out(req),
        code=code_files,
        review=review_out,
        test=test_out,
        files=files,
        logs=logs,
    )


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str) -> TaskDetailResponse:
    """查询任务状态与结果。"""
    return _build_detail(task_id)


@router.delete("")
async def clear_history() -> Dict[str, Any]:
    """清空所有任务历史（含日志与文件记录）。"""
    count = db.clear_all_tasks()
    # 同时清空内存中的 final state
    _final_states.clear()
    async with _lock:
        for k in list(_done_events.keys()):
            _done_events[k].set()
        _done_events.clear()
        _running.clear()
    return {"status": "ok", "deleted": count}


@router.delete("/{task_id}")
async def delete_task(task_id: str) -> Dict[str, Any]:
    """删除单个任务。"""
    ok = db.delete_task(task_id)
    if not ok:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    _final_states.pop(task_id, None)
    ev = _done_events.pop(task_id, None)
    if ev:
        ev.set()
    return {"status": "ok", "deleted": 1}


@router.get("/{task_id}/logs")
async def get_task_logs(task_id: str) -> List[Dict[str, Any]]:
    """全链路执行日志。"""
    if db.get_task(task_id) is None and task_id not in _final_states:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    return [
        {
            "stage": l.stage,
            "prompt_text": (l.prompt_text[:500] + "…") if len(l.prompt_text) > 500 else l.prompt_text,
            "llm_output": (l.llm_output[:500] + "…") if len(l.llm_output) > 500 else l.llm_output,
            "prompt_tokens": l.prompt_tokens,
            "completion_tokens": l.completion_tokens,
            "total_tokens": l.total_tokens,
            "duration_ms": l.duration_ms,
            "error": l.error,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in db.list_stage_logs(task_id)
    ]


@router.post("/{task_id}/resume")
async def resume_task(task_id: str) -> Dict[str, Any]:
    """方案C：从断点恢复失败/中断的任务。

    前提：任务之前用 run_pipeline 跑过（启用了 checkpoint），
    因异常或服务重启中断。用相同 thread_id（=task_id）从最近 checkpoint 恢复。
    """
    # 校验任务存在
    task = db.get_task(task_id)
    if task is None and task_id not in _final_states:
        raise HTTPException(404, f"任务 {task_id} 不存在，无法恢复")

    # 已完成或正在跑的任务不需要恢复
    if task and task.status in ("done",):
        return {"status": "already_done", "task_id": task_id, "phase": task.phase}
    if task_id in _running:
        return {"status": "already_running", "task_id": task_id}

    # 后台触发 resume
    async def _resume_bg() -> None:
        try:
            _running.add(task_id)
            _done_events.pop(task_id, None)
            ev = asyncio.Event()
            _done_events[task_id] = ev
            from app.graph.pipeline import resume_pipeline
            # 在 thread executor 中跑同步的 resume_pipeline
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: resume_pipeline(task_id=task_id, user_request=task.user_request if task else ""),
            )
            if result is not None:
                _final_states[task_id] = result
            else:
                # resume 不可用或失败，标记任务
                db.mark_task_failed(task_id, "断点续跑不可用或无历史 checkpoint")
        except Exception as e:
            logger.exception("任务 %s 断点续跑失败：%s", task_id, e)
            db.mark_task_failed(task_id, str(e))
        finally:
            ev = _done_events.get(task_id)
            if ev:
                ev.set()
            _running.discard(task_id)
            # Bug P5：延迟 300s 清理内存在态，避免内存泄漏
            _spawn_background(_delayed_cleanup(task_id))

    # Bug S5：用 _spawn_background 持有 Task 引用，避免被 GC 回收
    _spawn_background(_resume_bg())
    return {"status": "resuming", "task_id": task_id, "message": "断点续跑已在后台启动"}


@router.get("/{task_id}/stream")
async def stream_task(task_id: str):
    """SSE 实时推送任务执行进度。

    推送事件类型：
    - phase: 阶段变更（requirement/code_gen/review/test_gen）
    - stream_event: 中间状态（含 phase/progress/message）
    - done: 任务完成（含完整 TaskDetailResponse）
    - error: 错误
    - ping: 心跳
    """
    if db.get_task(task_id) is None and task_id not in _final_states:
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
            # 推送流式中间事件
            async with _lock:
                events = _stream_events.get(task_id, [])
            while sent_events < len(events):
                event = events[sent_events]
                sent_events += 1
                yield {
                    "event": "stream_event",
                    "data": json.dumps(event),
                }
                last_event_time = asyncio.get_event_loop().time()

            task = db.get_task(task_id)
            # Bug D7：任务被删除后立即退出，避免无限轮询
            if task is None and task_id not in _final_states:
                yield {"event": "error",
                       "data": json.dumps({"message": "任务不存在或已删除"})}
                return
            phase = task.phase if task else "unknown"
            if phase != last_phase:
                yield {"event": "phase", "data": phase}
                last_phase = phase
                last_event_time = asyncio.get_event_loop().time()
            if ev.is_set() or (task and task.status in ("done", "failed")):
                break
            # Bug D3：超过 15 秒无状态变化，发送心跳避免被反向代理断开
            now = asyncio.get_event_loop().time()
            if now - last_event_time > 15:
                yield {"event": "ping", "data": ""}
                last_event_time = now
            await asyncio.sleep(0.5)
        # 推送最终结果
        try:
            detail = _build_detail(task_id)
            yield {
                "event": "done",
                "data": detail.model_dump_json(),
            }
        except Exception as e:
            yield {"event": "error", "data": str(e)}

    # Bug D3：ping=15 让 sse-starlette 每 15 秒自动发送心跳注释，防止长任务被反向代理断开
    return EventSourceResponse(_gen(), ping=15)


# ====================================================================== #
#  Human-in-the-Loop (HITL) 审批接口
# ====================================================================== #
@router.post("/{task_id}/approve", response_model=ApprovalResponse)
async def approve_task(task_id: str, req: ApprovalRequest) -> ApprovalResponse:
    """审批通过：恢复流水线执行。"""
    task = db.get_task(task_id)
    if task is None and task_id not in _final_states:
        raise HTTPException(404, f"任务 {task_id} 不存在")

    # 更新 state 中的 human_approval 字段
    state = _final_states.get(task_id)
    if state is not None:
        state["human_approval"] = {
            "status": "approved",
            "reason": req.reason or "",
        }
        from app.core.events import EventType, event_bus
        event_bus.emit(EventType.HITL_APPROVED, data={"task_id": task_id, "reason": req.reason}, task_id=task_id)
        logger.info("任务 %s 审批通过，reason=%s", task_id, req.reason)
        return ApprovalResponse(status="ok", task_id=task_id, message="审批通过，流水线将继续执行")

    # 如果 state 不在内存中，尝试从 checkpoint 恢复后审批
    logger.warning("任务 %s 不在内存中，无法直接审批", task_id)
    raise HTTPException(400, f"任务 {task_id} 当前不在等待审批状态")


@router.post("/{task_id}/reject", response_model=ApprovalResponse)
async def reject_task(task_id: str, req: ApprovalRequest) -> ApprovalResponse:
    """审批拒绝：标记流水线为 FAILED。"""
    task = db.get_task(task_id)
    if task is None and task_id not in _final_states:
        raise HTTPException(404, f"任务 {task_id} 不存在")

    reason = req.reason or "人工审批拒绝"
    state = _final_states.get(task_id)
    if state is not None:
        state["human_approval"] = {
            "status": "rejected",
            "reason": reason,
        }
        from app.core.events import EventType, event_bus
        event_bus.emit(EventType.HITL_REJECTED, data={"task_id": task_id, "reason": reason}, task_id=task_id)
        logger.info("任务 %s 审批拒绝，reason=%s", task_id, reason)
        db.mark_task_failed(task_id, reason)
        return ApprovalResponse(status="rejected", task_id=task_id, message=f"审批拒绝: {reason}")

    logger.warning("任务 %s 不在内存中，无法拒绝", task_id)
    raise HTTPException(400, f"任务 {task_id} 当前不在等待审批状态")
