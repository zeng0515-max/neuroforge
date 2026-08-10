"""多智能体系统 API 路由层（新一代编排引擎）。

提供基于统一编排器的多智能体协作接口，覆盖：
- 同步执行 / SSE 流式执行
- 需求复杂度分析 / 任务拆解
- 系统状态查询 / 智能体列表
- 记忆系统管理
- 兼容原有 Pipeline

路由前缀: /api/v1/agents
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.core.auth import verify_api_key
from app.core.config import settings
from app.core.guardrails import input_guardrail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# ====================================================================== #
#  延迟导入 —— 兼容模块尚未就绪的情况
# ====================================================================== #

try:
    from app.unified_orchestrator import UnifiedOrchestrator, UnifiedResult  # type: ignore[import-untyped]
    _HAS_UNIFIED = True
except Exception:
    _HAS_UNIFIED = False

from app.agents.agent_protocol import (
    AgentCapability,
    AgentProfile,
    AgentRole,
    AgentStatus,
)
from app.agent_executor import (
    AgentCollaborationManager,
    AgentPool,
    ArchitectExecutor,
    CoderExecutor,
    OrchestratorExecutor,
    ReviewerExecutor,
    TesterExecutor,
)
from app.core.memory_system import (
    LEVEL_EPISODIC,
    LEVEL_SEMANTIC,
    LEVEL_WORKING,
    MemoryFacade,
    memory_system,
)
from app.graph.orchestrator import Orchestrator, OrchestratorResult
from app.graph.task_decomposer import (
    ComplexityScore,
    DecisionEngine,
    RequirementAnalysis,
    Strategy,
    TaskDecomposer,
    TaskTree,
    TaskTreeNode,
)


# ====================================================================== #
#  请求模型
# ====================================================================== #

class AgentRunOptions(BaseModel):
    """多智能体执行选项。"""
    max_parallel: int = Field(default=3, ge=1, le=10, description="最大并行度")
    strategy: Strategy = Field(default=Strategy.PIPELINE, description="执行策略")
    enable_memory: bool = Field(default=True, description="是否启用记忆系统")
    enable_guardrail: bool = Field(default=True, description="是否启用输入防护")
    model: Optional[str] = Field(default=None, description="指定 LLM 模型")
    extra_handlers: Dict[str, Any] = Field(default_factory=dict, description="自定义任务处理器")
    timeout_seconds: int = Field(default=600, ge=30, le=3600, description="执行超时")


class AgentRunRequest(BaseModel):
    """运行多智能体编排系统请求。"""
    user_request: str = Field(..., min_length=1, description="用户自然语言需求")
    options: AgentRunOptions = Field(default_factory=AgentRunOptions)


class AgentAnalyzeRequest(BaseModel):
    """需求分析请求。"""
    user_request: str = Field(..., min_length=1, description="用户自然语言需求")


class AgentDecomposeRequest(BaseModel):
    """任务拆解请求。"""
    user_request: str = Field(..., min_length=1, description="用户自然语言需求")
    max_depth: int = Field(default=3, ge=1, le=5, description="最大拆解深度")


class AgentLegacyRequest(BaseModel):
    """兼容原有 Pipeline 请求。"""
    user_request: str = Field(..., min_length=1, description="用户自然语言需求")
    task_id: Optional[str] = Field(default=None, description="可选自定义任务 ID")
    model: Optional[str] = Field(default=None, description="指定 LLM 模型")
    extra_args: Dict[str, Any] = Field(default_factory=dict, description="透传额外参数")


class AgentMemoryQueryRequest(BaseModel):
    """记忆查询请求（用于 POST 方式的灵活查询）。"""
    query: str = Field(..., min_length=1, description="查询文本")
    level: str = Field(default="all", description="记忆层级: all / working / episodic / semantic")
    top_k: int = Field(default=5, ge=1, le=20, description="返回条数")


class AgentMemoryCleanRequest(BaseModel):
    """记忆清理请求。"""
    level: str = Field(default="all", description="清理层级: all / working / episodic / semantic")
    max_age_days: int = Field(default=90, ge=1, le=365, description="最大保留天数（仅语义层）")


# ====================================================================== #
#  响应模型
# ====================================================================== #

class ComplexityScoreOut(BaseModel):
    """复杂度评分响应。"""
    score: float = 0.0
    level: str = "simple"
    factors: Dict[str, float] = Field(default_factory=dict)
    reasoning: str = ""


class RequirementAnalysisOut(BaseModel):
    """需求分析响应。"""
    goal: str = ""
    scope: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    complexity: ComplexityScoreOut = Field(default_factory=ComplexityScoreOut)
    suggested_roles: List[str] = Field(default_factory=list)
    estimated_steps: int = 0
    keywords: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)


class TaskTreeNodeOut(BaseModel):
    """任务树节点响应。"""
    node_id: str = ""
    description: str = ""
    agent_type: str = "general"
    agent_role: Optional[str] = None
    complexity: ComplexityScoreOut = Field(default_factory=ComplexityScoreOut)
    children: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"


class TaskTreeOut(BaseModel):
    """任务树响应。"""
    root: str = ""
    nodes: Dict[str, TaskTreeNodeOut] = Field(default_factory=dict)
    edges: List[List[str]] = Field(default_factory=list)
    depth: int = 0
    total_tasks: int = 0


class AgentProfileOut(BaseModel):
    """智能体画像响应。"""
    name: str
    role: str
    capabilities: List[str] = Field(default_factory=list)
    description: str = ""
    has_system_prompt: bool = False


class AgentStatusResponse(BaseModel):
    """系统状态响应。"""
    active_agents: Dict[str, str] = Field(default_factory=dict)
    task_stats: Dict[str, Any] = Field(default_factory=dict)
    memory_status: Dict[str, Any] = Field(default_factory=dict)
    orchestrator: Dict[str, Any] = Field(default_factory=dict)
    uptime_seconds: float = 0.0


class UnifiedResultOut(BaseModel):
    """统一编排执行结果响应。"""
    success: bool = False
    user_request: str = ""
    strategy_used: str = ""
    total_duration: float = 0.0
    completed_count: int = 0
    failed_count: int = 0
    results: Dict[str, Any] = Field(default_factory=dict)
    errors: Dict[str, str] = Field(default_factory=dict)
    execution_plan: Dict[str, Any] = Field(default_factory=dict)
    requirement_analysis: Optional[RequirementAnalysisOut] = None
    task_tree: Optional[TaskTreeOut] = None
    agent_outputs: Dict[str, Any] = Field(default_factory=dict)


class LegacyRunResponse(BaseModel):
    """兼容 Pipeline 响应。"""
    task_id: str
    status: str = "submitted"
    message: str = ""


class MemorySearchResult(BaseModel):
    """单条记忆搜索结果。"""
    id: str = ""
    content: str = ""
    score: float = 0.0
    source: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryQueryResponse(BaseModel):
    """记忆查询响应。"""
    level: str = ""
    results: List[MemorySearchResult] = Field(default_factory=list)
    total: int = 0


class MemoryCleanResponse(BaseModel):
    """记忆清理响应。"""
    level: str = ""
    cleared: int = 0
    details: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """错误响应。"""
    detail: str
    error_code: str = ""


# ====================================================================== #
#  内部辅助
# ====================================================================== #

_orchestrator = Orchestrator()
_decomposer = TaskDecomposer()
_pool = AgentPool()
_pool.register(ArchitectExecutor())
_pool.register(CoderExecutor())
_pool.register(ReviewerExecutor())
_pool.register(TesterExecutor())
_pool.register(OrchestratorExecutor())

_start_time = time.monotonic()

_execution_stats: Dict[str, int] = {
    "total_runs": 0,
    "successful": 0,
    "failed": 0,
    "stream_sessions": 0,
}


def _complexity_to_out(c: ComplexityScore) -> ComplexityScoreOut:
    return ComplexityScoreOut(
        score=c.score,
        level=c.level,
        factors=dict(c.factors),
        reasoning=c.reasoning,
    )


def _analysis_to_out(a: RequirementAnalysis) -> RequirementAnalysisOut:
    return RequirementAnalysisOut(
        goal=a.goal,
        scope=list(a.scope),
        constraints=list(a.constraints),
        assumptions=list(a.assumptions),
        complexity=_complexity_to_out(a.complexity),
        suggested_roles=[r.value for r in a.suggested_roles],
        estimated_steps=a.estimated_steps,
        keywords=list(a.keywords),
        tech_stack=list(a.tech_stack),
    )


def _tree_node_to_out(n: TaskTreeNode) -> TaskTreeNodeOut:
    return TaskTreeNodeOut(
        node_id=n.node_id,
        description=n.description,
        agent_type=n.agent_type,
        agent_role=n.agent_role.value if n.agent_role else None,
        complexity=_complexity_to_out(n.complexity),
        children=list(n.children),
        metadata=dict(n.metadata),
        status=n.status,
    )


def _tree_to_out(t: TaskTree) -> TaskTreeOut:
    return TaskTreeOut(
        root=t.root,
        nodes={nid: _tree_node_to_out(n) for nid, n in t.nodes.items()},
        edges=[list(e) for e in t.edges],
        depth=t.depth,
        total_tasks=t.total_tasks,
    )


def _profile_to_out(p: AgentProfile) -> AgentProfileOut:
    return AgentProfileOut(
        name=p.name,
        role=p.role.value,
        capabilities=[c.value for c in p.capabilities],
        description=p.description,
        has_system_prompt=bool(p.system_prompt),
    )


def _get_system_status() -> AgentStatusResponse:
    mem_stats = memory_system.get_system_stats()
    llm = _decomposer._llm
    return AgentStatusResponse(
        active_agents={},
        task_stats=dict(_execution_stats),
        memory_status=mem_stats,
        orchestrator={
            "available": True,
            "registered_agents": _pool.count,
            "strategy": llm.current_model if hasattr(llm, 'current_model') else "default",
        },
        uptime_seconds=round(time.monotonic() - _start_time, 2),
    )


def _run_orchestration(
    user_request: str,
    options: AgentRunOptions,
) -> UnifiedResultOut:
    """执行多智能体编排的核心逻辑。"""
    _execution_stats["total_runs"] += 1

    try:
        analysis = _decomposer.analyze_request(user_request)

        tree = _decomposer.decompose(analysis)

        task_graph = _decomposer.generate_dependencies(tree.flatten())

        plan = _orchestrator.schedule_tasks(task_graph)

        state = _orchestrator.run(
            user_request=user_request,
            extra_handlers=options.extra_handlers or None,
        )

        success = state.success if hasattr(state, 'success') else False
        total_duration = state.total_duration if hasattr(state, 'total_duration') else 0.0
        completed = state.completed_count if hasattr(state, 'completed_count') else 0
        failed = state.failed_count if hasattr(state, 'failed_count') else 0

        if success:
            _execution_stats["successful"] += 1
        else:
            _execution_stats["failed"] += 1

        return UnifiedResultOut(
            success=success,
            user_request=user_request,
            strategy_used=options.strategy.value,
            total_duration=round(total_duration, 3),
            completed_count=completed,
            failed_count=failed,
            results={k: v for k, v in state.results.items()} if hasattr(state, 'results') else {},
            errors=dict(state.errors) if hasattr(state, 'errors') else {},
            execution_plan={
                "execution_order": plan.execution_order if hasattr(plan, 'execution_order') else [],
                "estimated_duration": plan.estimated_duration if hasattr(plan, 'estimated_duration') else 0.0,
                "critical_path": plan.critical_path if hasattr(plan, 'critical_path') else [],
                "total_tasks": plan.total_tasks if hasattr(plan, 'total_tasks') else 0,
            },
            requirement_analysis=_analysis_to_out(analysis),
            task_tree=_tree_to_out(tree),
            agent_outputs={
                task_id: result for task_id, result in
                (state.results.items() if hasattr(state, 'results') else [])
            },
        )
    except Exception as e:
        _execution_stats["failed"] += 1
        logger.exception("编排执行失败: %s", e)
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                detail=f"编排执行失败: {e}",
                error_code="ORCHESTRATION_FAILED",
            ).model_dump(),
        )


# ====================================================================== #
#  接口实现
# ====================================================================== #

@router.post(
    "/run",
    response_model=UnifiedResultOut,
    summary="运行多智能体编排系统",
    description="接收用户需求，通过统一编排器进行需求分析、任务拆解、智能体分配与执行。",
)
async def run_agents(
    req: AgentRunRequest,
) -> UnifiedResultOut:
    """运行多智能体编排系统（同步执行，返回完整结果）。"""
    if req.options.enable_guardrail and settings.guardrail.input_enabled:
        guard_result = input_guardrail.check(req.user_request)
        if not guard_result.passed and guard_result.blocked:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    detail=f"输入校验未通过：{guard_result.reason}",
                    error_code="GUARDRAIL_BLOCKED",
                ).model_dump(),
            )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _run_orchestration(req.user_request, req.options),
    )


@router.post(
    "/stream",
    summary="流式执行（SSE）",
    description="以 SSE 方式推送多智能体执行过程中的实时事件。",
)
async def stream_agents(
    req: AgentRunRequest,
):
    """SSE 流式推送多智能体执行过程。"""
    if req.options.enable_guardrail and settings.guardrail.input_enabled:
        guard_result = input_guardrail.check(req.user_request)
        if not guard_result.passed and guard_result.blocked:
            raise HTTPException(
                status_code=400,
                detail=f"输入校验未通过：{guard_result.reason}",
            )

    _execution_stats["stream_sessions"] += 1
    session_id = f"sse_{uuid.uuid4().hex[:8]}"

    async def _gen():
        yield {
            "event": "session_start",
            "data": json.dumps({
                "session_id": session_id,
                "user_request": req.user_request[:100],
                "strategy": req.options.strategy.value,
                "max_parallel": req.options.max_parallel,
            }),
        }

        yield {
            "event": "analysis_start",
            "data": json.dumps({"timestamp": datetime.now().isoformat()}),
        }

        try:
            loop = asyncio.get_running_loop()
            analysis = await loop.run_in_executor(
                None,
                lambda: _decomposer.analyze_request(req.user_request),
            )
            yield {
                "event": "analysis_complete",
                "data": json.dumps({
                    "complexity": analysis.complexity.level,
                    "score": analysis.complexity.score,
                    "roles": [r.value for r in analysis.suggested_roles],
                    "steps": analysis.estimated_steps,
                }),
            }

            yield {
                "event": "decomposition_start",
                "data": json.dumps({"timestamp": datetime.now().isoformat()}),
            }

            tree = await loop.run_in_executor(
                None,
                lambda: _decomposer.decompose(analysis),
            )
            yield {
                "event": "decomposition_complete",
                "data": json.dumps({
                    "total_tasks": tree.total_tasks,
                    "depth": tree.depth,
                    "leaf_tasks": len(tree.get_leaf_tasks()),
                }),
            }

            yield {
                "event": "execution_start",
                "data": json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    "task_count": tree.total_tasks,
                }),
            }

            result = await loop.run_in_executor(
                None,
                lambda: _run_orchestration(req.user_request, req.options),
            )

            if result.success:
                yield {
                    "event": "execution_complete",
                    "data": json.dumps({
                        "success": True,
                        "duration": result.total_duration,
                        "completed": result.completed_count,
                        "failed": result.failed_count,
                    }),
                }
            else:
                yield {
                    "event": "execution_failed",
                    "data": json.dumps({
                        "success": False,
                        "errors": result.errors,
                    }),
                }

            yield {
                "event": "done",
                "data": json.dumps(result.model_dump()),
            }

        except Exception as e:
            logger.exception("SSE 执行异常: %s", e)
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": str(e),
                    "type": type(e).__name__,
                }),
            }

        yield {"event": "close", "data": ""}

    return EventSourceResponse(_gen(), ping=15)


@router.post(
    "/analyze",
    response_model=RequirementAnalysisOut,
    summary="仅分析需求复杂度",
    description="对用户需求进行深度分析，返回复杂度评分、推荐角色、技术栈识别等。",
)
async def analyze_requirement(
    req: AgentAnalyzeRequest,
) -> RequirementAnalysisOut:
    """分析需求复杂度，不执行实际编排。"""
    loop = asyncio.get_running_loop()
    analysis = await loop.run_in_executor(
        None,
        lambda: _decomposer.analyze_request(req.user_request),
    )
    return _analysis_to_out(analysis)


@router.post(
    "/decompose",
    response_model=TaskTreeOut,
    summary="仅任务拆解",
    description="将用户需求拆解为层级化的任务树结构，不执行实际编排。",
)
async def decompose_task(
    req: AgentDecomposeRequest,
) -> TaskTreeOut:
    """将需求拆解为任务树。"""
    loop = asyncio.get_running_loop()

    analysis = await loop.run_in_executor(
        None,
        lambda: _decomposer.analyze_request(req.user_request),
    )

    tree = await loop.run_in_executor(
        None,
        lambda: _decomposer.decompose(analysis, max_depth=req.max_depth),
    )

    return _tree_to_out(tree)


@router.get(
    "/status",
    response_model=AgentStatusResponse,
    summary="系统状态",
    description="查询系统中活跃智能体、任务统计、记忆状态等运行时信息。",
)
async def get_agent_status() -> AgentStatusResponse:
    """获取多智能体系统运行时状态。"""
    return _get_system_status()


@router.get(
    "/list",
    response_model=List[AgentProfileOut],
    summary="列出所有可用智能体",
    description="返回系统中所有已注册的智能体画像列表，包含角色、能力、描述等。",
)
async def list_agents() -> List[AgentProfileOut]:
    """列出所有可用智能体画像。"""
    profiles: List[AgentProfileOut] = []

    for executor in [
        ArchitectExecutor(),
        CoderExecutor(),
        ReviewerExecutor(),
        TesterExecutor(),
        OrchestratorExecutor(),
    ]:
        profiles.append(_profile_to_out(executor.agent_profile))

    for prof in _pool._executors.values():
        out = _profile_to_out(prof.agent_profile)
        if not any(p.name == out.name for p in profiles):
            profiles.append(out)

    return profiles


@router.post(
    "/legacy",
    response_model=LegacyRunResponse,
    summary="兼容原有 Pipeline",
    description="透传到原有 run_pipeline，保持向后兼容。",
)
async def run_legacy_pipeline(
    req: AgentLegacyRequest,
) -> LegacyRunResponse:
    """兼容原有 Pipeline 接口。"""
    if settings.guardrail.input_enabled:
        guard_result = input_guardrail.check(req.user_request)
        if not guard_result.passed and guard_result.blocked:
            raise HTTPException(
                status_code=400,
                detail=f"输入校验未通过：{guard_result.reason}",
            )

    from app.graph.pipeline import run_pipeline

    task_id = req.task_id or uuid.uuid4().hex[:12]

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: run_pipeline(
                task_id=task_id,
                user_request=req.user_request,
                extra_args=req.extra_args or None,
                model=req.model,
            ),
        )
        return LegacyRunResponse(
            task_id=task_id,
            status="completed",
            message="Pipeline 执行完成",
        )
    except Exception as e:
        logger.exception("Legacy Pipeline 执行失败: %s", e)
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                detail=f"Pipeline 执行失败: {e}",
                error_code="PIPELINE_FAILED",
            ).model_dump(),
        )


@router.get(
    "/memory",
    response_model=MemoryQueryResponse,
    summary="记忆系统查询",
    description="查询/搜索三层记忆（工作记忆/情景记忆/语义记忆）。",
)
async def query_memory(
    q: str = Query(..., min_length=1, alias="q", description="查询文本"),
    level: str = Query("all", description="记忆层级: all / working / episodic / semantic"),
    top_k: int = Query(5, ge=1, le=20, description="返回条数上限"),
) -> MemoryQueryResponse:
    """查询记忆系统。"""
    if level == "all":
        results = memory_system.search(q, top_k=top_k)
        all_items: List[MemorySearchResult] = []
        for lvl, items in results.items():
            for item in items:
                all_items.append(MemorySearchResult(
                    id=item.get("id", ""),
                    content=item.get("content", ""),
                    score=item.get("score", 0.0),
                    source=item.get("source", lvl),
                    metadata=item.get("metadata", {}),
                ))
        return MemoryQueryResponse(
            level="all",
            results=all_items[:top_k],
            total=len(all_items),
        )

    if level == LEVEL_SEMANTIC:
        items = memory_system.semantic.search(q, top_k=top_k)
        return MemoryQueryResponse(
            level=level,
            results=[
                MemorySearchResult(
                    id=i.get("id", ""),
                    content=i.get("content", ""),
                    score=i.get("score", 0.0),
                    source=i.get("source", "semantic"),
                    metadata=i.get("metadata", {}),
                )
                for i in items
            ],
            total=len(items),
        )

    if level == LEVEL_EPISODIC:
        events = memory_system.episodic.query_events()
        q_lower = q.lower()
        matched: List[MemorySearchResult] = []
        for evt in events:
            evt_text = str(evt.data).lower()
            if q_lower in evt_text:
                matched.append(MemorySearchResult(
                    id=evt.event_id,
                    content=str(evt.data),
                    score=1.0,
                    source="episodic",
                    metadata={"event_type": evt.event_type, "timestamp": evt.timestamp},
                ))
        return MemoryQueryResponse(
            level=level,
            results=matched[:top_k],
            total=len(matched),
        )

    if level == LEVEL_WORKING:
        all_items = memory_system.working.get_all()
        matched: List[MemorySearchResult] = []
        q_lower = q.lower()
        for k, v in all_items.items():
            v_str = str(v).lower()
            if q_lower in k.lower() or q_lower in v_str:
                matched.append(MemorySearchResult(
                    id=k,
                    content=str(v),
                    score=1.0,
                    source="working",
                    metadata={"key": k},
                ))
        return MemoryQueryResponse(
            level=level,
            results=matched[:top_k],
            total=len(matched),
        )

    raise HTTPException(
        status_code=400,
        detail=ErrorResponse(
            detail=f"非法记忆层级: {level}",
            error_code="INVALID_LEVEL",
        ).model_dump(),
    )


@router.delete(
    "/memory",
    response_model=MemoryCleanResponse,
    summary="清理记忆",
    description="清理指定层级的记忆数据，语义层支持按天数过期清理。",
)
async def clean_memory(
    level: str = Query("all", description="清理层级: all / working / episodic / semantic"),
    max_age_days: int = Query(90, ge=1, le=365, description="最大保留天数（仅语义层）"),
) -> MemoryCleanResponse:
    """清理指定层级的记忆。"""
    details: Dict[str, Any] = {}
    total_cleared = 0

    if level in ("all", LEVEL_WORKING):
        w_count = len(memory_system.working)
        memory_system.working.clear()
        details["working"] = {"cleared": w_count}
        total_cleared += w_count

    if level in ("all", LEVEL_EPISODIC):
        e_count = len(memory_system.episodic)
        memory_system.episodic.clear()
        details["episodic"] = {"cleared": e_count}
        total_cleared += e_count

    if level in ("all", LEVEL_SEMANTIC):
        cleared = memory_system.semantic.clean_expired(max_age_days=max_age_days)
        details["semantic"] = {"cleared": cleared, "max_age_days": max_age_days}
        total_cleared += cleared

    return MemoryCleanResponse(
        level=level,
        cleared=total_cleared,
        details=details,
    )


@router.post(
    "/memory/clean",
    response_model=MemoryCleanResponse,
    summary="清理记忆（POST 方式）",
    description="通过 POST 请求体清理记忆，支持更灵活的参数控制。",
    deprecated=True,
)
async def clean_memory_post(req: AgentMemoryCleanRequest) -> MemoryCleanResponse:
    """清理记忆（POST 方式，推荐使用 DELETE 接口）。"""
    return await clean_memory(level=req.level, max_age_days=req.max_age_days)


__all__ = [
    "router",
]