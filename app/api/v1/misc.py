"""辅助接口：健康检查 / 知识库 / 工具调用 / LLM 配置 / 可观测性 / 告警。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from app.core.config import read_env_file, settings, update_env_file
from app.core.llm_adapter import llm
from app.core.llm_providers import _PROVIDER_REGISTRY
from app.core.observability import alert_manager, metrics_collector
from app.db.sqlite_client import db
from app.schemas.pipeline_schemas import (
    AddToKnowledgeRequest,
    HealthDebugInfo,
    HealthResponse,
    KnowledgeAddResponse,
    KnowledgeDoc,
    KnowledgeQueryRequest,
    KnowledgeQueryResponse,
    LLMConfigStatusResponse,
    LLMConfigUpdateRequest,
    LLMConfigUpdateResponse,
    ModelSwitchRequest,
    ModelSwitchResponse,
    ModelsListResponse,
    ProviderConfigOut,
    ToolCallRequest,
    ToolCallResponse,
)
from app.tools.rag_service import rag
from app.tools.tool_registry import tool_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["misc"])


# ====================================================================== #
#  健康检查
# ====================================================================== #
@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """健康检查：LLM / RAG / DB / 新架构状态自检。"""
    new_engine_version = ""
    new_engine_mode = "standalone"
    agent_count = 0
    new_engine_available = False
    orchestrator_status = "unavailable"

    try:
        from app.unified_orchestrator import unified_orchestrator
        status = unified_orchestrator.get_system_status()
        new_engine_version = "0.1.0"
        new_engine_mode = status.engine
        agent_count = len(status.agents)
        new_engine_available = True
        orchestrator_status = "ready"
    except Exception:
        pass

    try:
        _agent_pool = getattr(request.app.state, "agent_pool", None)
        if _agent_pool is not None:
            agent_count = _agent_pool.count
    except Exception:
        pass

    debug = HealthDebugInfo(
        llm_error=llm.init_error,
        rag_error="",
        db_error="",
        llm_model=settings.llm.model,
        code_llm_model=settings.code_llm.model or settings.llm.model,
        app_env=settings.app.env,
        new_engine_version=new_engine_version,
        new_engine_mode=new_engine_mode,
        agent_count=agent_count,
    )
    return HealthResponse(
        status="ok",
        llm="dry-run" if llm.dry_run else llm.current_model,
        rag_available=rag.is_available(),
        rag_count=rag.count(),
        db_ok=True,
        debug=debug,
        new_engine_available=new_engine_available,
        agent_count=agent_count,
        orchestrator_status=orchestrator_status,
    )


# ====================================================================== #
#  知识库管理
# ====================================================================== #
@router.post("/knowledge", response_model=KnowledgeAddResponse)
async def add_knowledge(req: AddToKnowledgeRequest) -> KnowledgeAddResponse:
    """灌入知识到向量库（文本或项目文件路径）。"""
    if not rag.is_available():
        raise HTTPException(503, "RAG 向量库不可用")
    added = 0
    if req.texts:
        added += rag.add_texts(texts=req.texts, source=req.source)
    if req.project_files:
        added += rag.add_files(file_paths=req.project_files, source=req.source)
    return KnowledgeAddResponse(added=added, total=rag.count())


@router.post("/knowledge/query", response_model=KnowledgeQueryResponse)
async def query_knowledge(req: KnowledgeQueryRequest) -> KnowledgeQueryResponse:
    """检索向量库。"""
    if not rag.is_available():
        raise HTTPException(503, "RAG 向量库不可用")
    docs = rag.query(req.query, top_k=req.top_k)
    return KnowledgeQueryResponse(
        query=req.query,
        items=[
            KnowledgeDoc(
                content=d.content, score=d.score,
                source=d.source, file_path=d.file_path,
            )
            for d in docs
        ],
    )


# ====================================================================== #
#  工具调用
# ====================================================================== #
@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest) -> ToolCallResponse:
    """直接调用已注册到 tool_registry 的工具。"""
    if not tool_registry.has(req.tool_name):
        raise HTTPException(404, f"未注册的工具: {req.tool_name}")
    try:
        result = tool_registry.call(req.tool_name, req.arguments)
        return ToolCallResponse(tool_name=req.tool_name, ok=True, result=result)
    except Exception as e:
        logger.exception("工具调用失败：%s", e)
        return ToolCallResponse(
            tool_name=req.tool_name, ok=False, error=str(e),
        )


# ====================================================================== #
#  模型管理
# ====================================================================== #
@router.get("/models", response_model=ModelsListResponse)
async def list_models() -> ModelsListResponse:
    """列出可用模型 + 当前主/代码模型。"""
    return ModelsListResponse(
        current_main=llm.current_model,
        current_code=llm.current_code_model,
        base_url=llm.base_url,
        available=llm.available_models(),
    )


@router.post("/models/switch", response_model=ModelSwitchResponse)
async def switch_model(req: ModelSwitchRequest) -> ModelSwitchResponse:
    """运行时切换模型（不重启服务）。"""
    try:
        result = llm.switch_model(req.model, for_code=req.for_code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    provider = result.get("provider", "")
    api_key_url = result.get("api_key_url", "")
    is_fallback = result.get("api_key_fallback", False)

    # 构建提示消息
    if is_fallback and provider:
        message = (
            f"已切换为 {req.model}（{provider}）。"
            f"⚠️ 未检测到 {provider} 专用 API Key，当前使用主 LLM_API_KEY 兜底，"
            f"跨厂商调用可能失败。请点击获取 key 链接配置后重启。"
        )
    elif provider:
        message = f"已切换为 {req.model}（{provider} 专用 Key 已配置）"
    else:
        message = f"已切换为 {req.model}" + ("（代码模型）" if req.for_code else "（主模型）")

    return ModelSwitchResponse(
        status=result.get("status", "ok"),
        model=result.get("model", req.model),
        for_code=req.for_code,
        base_url=llm.base_url,
        message=message,
        provider=provider,
        api_key_url=api_key_url,
        api_key_fallback=is_fallback,
    )


# ====================================================================== #
#  LLM 配置（页面内配置 API 大模型）
# ====================================================================== #
def _build_providers() -> List[ProviderConfigOut]:
    """聚合厂商注册表为配置状态列表（不重复厂商，按厂商聚合模型）。"""
    env_map = read_env_file()
    by_env: Dict[str, ProviderConfigOut] = {}
    for model, (base_url, env_key, provider_name, key_url) in _PROVIDER_REGISTRY.items():
        p = by_env.setdefault(
            env_key,
            ProviderConfigOut(
                name=provider_name,
                env_key=env_key,
                base_url=base_url,
                key_url=key_url,
                configured=bool(env_map.get(env_key, "").strip()),
            ),
        )
        if model not in p.models:
            p.models.append(model)
    return list(by_env.values())


@router.get("/config/llm", response_model=LLMConfigStatusResponse)
async def get_llm_config() -> LLMConfigStatusResponse:
    """获取当前 LLM 配置状态（是否已配置、模型、厂商列表）。"""
    providers = _build_providers()
    return LLMConfigStatusResponse(
        configured=not llm.dry_run,
        dry_run=llm.dry_run,
        base_url=settings.llm.base_url,
        model=llm.current_model,
        code_model=llm.current_code_model,
        providers=providers,
        quota_exhausted=llm.quota_exhausted,
        quota_detail=llm.quota_detail,
    )


@router.post("/config/llm", response_model=LLMConfigUpdateResponse)
async def update_llm_config(req: LLMConfigUpdateRequest) -> LLMConfigUpdateResponse:
    """页面内配置 API 大模型：写入 .env + 同步环境变量 + 热重载，无需重启。"""
    base_url = req.base_url.strip()
    api_key = req.api_key.strip()
    model = req.model.strip()
    code_model = (req.code_model or "").strip() or model

    if not base_url or not api_key or not model:
        raise HTTPException(400, "base_url / api_key / model 均不能为空")

    updates = {
        "LLM_BASE_URL": base_url,
        "LLM_API_KEY": api_key,
        "LLM_MODEL": model,
        "CODE_LLM_MODEL": code_model,
    }
    # 若指定了厂商专用 key，写入对应环境变量
    if req.provider_env_key:
        env_key = req.provider_env_key.strip().upper()
        allowed_provider_keys = {spec[1] for spec in _PROVIDER_REGISTRY.values()}
        if env_key in allowed_provider_keys:
            updates[env_key] = api_key

    # 1) 写 .env（持久化）
    update_env_file(updates)
    # 2) 同步 os.environ（进程内热生效）
    for k, v in updates.items():
        os.environ[k] = v
    # 3) 更新 settings（pydantic 对象）
    settings.llm.base_url = base_url
    settings.llm.api_key = api_key
    settings.llm.model = model
    settings.code_llm.model = code_model
    settings.code_llm.base_url = base_url
    settings.code_llm.api_key = api_key
    # 4) 重建 LLM 客户端
    llm.reload_config()

    message = "LLM 配置已保存并生效"
    if llm.dry_run:
        message += "（当前仍为 DryRun，请检查 API Key 是否有效）"
    return LLMConfigUpdateResponse(
        status="ok",
        message=message,
        dry_run=llm.dry_run,
        model=llm.current_model,
        base_url=settings.llm.base_url,
    )


# ====================================================================== #
#  可观测性
# ====================================================================== #
@router.get("/metrics")
async def get_metrics() -> Dict[str, Any]:
    """获取当前指标快照（LLM 调用、流水线、工具调用）。"""
    return metrics_collector.snapshot()


# ====================================================================== #
#  告警
# ====================================================================== #
@router.get("/alerts")
async def get_alerts(
    limit: int = 100,
    level: str = "",
) -> List[Dict[str, Any]]:
    """查询告警历史，可按级别过滤（INFO / WARNING / CRITICAL）。"""
    return alert_manager.get_history(limit=limit, level=level or None)


@router.delete("/alerts")
async def clear_alerts() -> Dict[str, Any]:
    """清除所有告警历史。"""
    count = alert_manager.clear_history()
    return {"status": "ok", "cleared": count}
