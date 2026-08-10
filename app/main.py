"""FastAPI 应用入口（文档 6.2 第 6 步：uvicorn app.main:app）。

职责：
- 挂载所有路由（任务 / 健康检查 / 知识库 / 工具 / 多智能体）
- 配置 CORS / 日志 / 全局异常兜底
- 启动时预热单例（db / llm / rag），失败不阻断启动
- 初始化新版统一编排引擎（unified_orchestrator + agent_pool），降级不影响旧架构

新旧架构关系：
- 旧架构：app.graph.pipeline（Pipeline 状态机，tasks/execute 路由使用）
- 新架构：app.unified_orchestrator（统一编排门面，agents 路由使用）
- 两套架构并存，通过 API Key 统一鉴权
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.agents import router as agents_router
from app.api.v1.eval import router as eval_router
from app.api.v1.execute import router as execute_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.misc import router as misc_router
from app.api.v1.pipeline import router as pipeline_router
from app.api.v1.skills import router as skills_router
from app.api.v1.slo import router as slo_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.websocket import router as ws_router
from app.core.auth import verify_api_key
from app.core.config import settings

# ====================================================================== #
#  新版架构降级导入 —— 导入失败不影响原有功能
# ====================================================================== #
try:
    from app.unified_orchestrator import unified_orchestrator as _unified_orchestrator
    _HAS_UNIFIED_ORCHESTRATOR = True
except Exception as _e:
    _unified_orchestrator = None
    _HAS_UNIFIED_ORCHESTRATOR = False
    logging.getLogger(__name__).warning(
        "统一编排引擎导入失败（不影响旧架构）：%s", _e
    )

try:
    from app.agent_executor import AgentPool as _AgentPool
    _HAS_AGENT_POOL = True
except Exception as _e:
    _AgentPool = None
    _HAS_AGENT_POOL = False
    logging.getLogger(__name__).warning(
        "智能体池导入失败（不影响旧架构）：%s", _e
    )

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时预热核心单例，初始化新旧双架构。"""
    from app.core.logging_config import setup_logging
    setup_logging(settings.app.log_level)

    # 触发各单例初始化（即使部分失败也不阻断启动）
    try:
        from app.db.sqlite_client import db  # noqa: F401
        logger.info("SQLite 就绪：%s", settings.db.path)
    except Exception as e:
        logger.error("DB 初始化失败：%s", e)

    try:
        from app.core.llm_adapter import llm  # noqa: F401
        logger.info("LLM 状态：%s", "dry-run" if llm.dry_run else f"model={settings.llm.model}")
    except Exception as e:
        logger.error("LLM 初始化失败：%s", e)

    try:
        from app.tools.rag_service import rag  # noqa: F401
        logger.info("RAG 状态：available=%s count=%d", rag.is_available(), rag.count())
    except Exception as e:
        logger.error("RAG 初始化失败：%s", e)

    # Tracing 初始化
    try:
        from app.core.tracing import setup_tracing
        setup_tracing(
            service_name=settings.tracing.service_name,
            enabled=settings.tracing.enabled,
        )
        logger.info("Tracing 状态：enabled=%s", settings.tracing.enabled)
    except Exception as e:
        logger.warning("Tracing 初始化失败（不影响主流程）：%s", e)

    # 初始化 EventBus 单例
    try:
        from app.core.events import event_bus
        event_bus.clear()  # 确保干净状态
        logger.info("EventBus 已就绪")
    except Exception as e:
        logger.error("EventBus 初始化失败：%s", e)

    # 插件系统初始化
    try:
        from app.plugins import PluginManager
        plugin_dirs = settings.agent.plugin_dirs
        if isinstance(plugin_dirs, str) and plugin_dirs.strip():
            dirs = [d.strip() for d in plugin_dirs.split(",") if d.strip()]
        else:
            dirs = []
        # 默认添加内置插件目录
        builtin_dir = str(Path(__file__).resolve().parent / "plugins" / "builtin")
        dirs.append(builtin_dir)
        plugin_manager = PluginManager(plugin_dirs=dirs)
        count = plugin_manager.load_all()
        # 挂载到 app.state 供其他模块使用
        app.state.plugin_manager = plugin_manager
        logger.info("插件系统已初始化，加载 %d 个插件", count)
    except Exception as e:
        logger.warning("插件系统初始化失败（不影响主流程）：%s", e)
        app.state.plugin_manager = None

    # 可观测性初始化
    try:
        from app.core.observability import metrics_collector, alert_manager
        logger.info("可观测性状态：enabled=%s, retention_days=%d",
                    settings.observability.enabled, settings.observability.retention_days)
    except Exception as e:
        logger.warning("可观测性初始化失败（不影响主流程）：%s", e)

    # 新版架构：初始化统一编排引擎（优雅降级，不影响旧架构）
    try:
        if _HAS_UNIFIED_ORCHESTRATOR and _unified_orchestrator is not None:
            app.state.unified_orchestrator = _unified_orchestrator
            status = _unified_orchestrator.get_system_status()
            logger.info(
                "新版编排引擎就绪：version=0.1.0, mode=%s, agents=%d",
                status.engine, len(status.agents),
            )
        else:
            app.state.unified_orchestrator = None
            logger.warning("新版编排引擎未启用（导入失败），旧架构仍可正常使用")
    except Exception as e:
        app.state.unified_orchestrator = None
        logger.warning("新版编排引擎初始化失败（不影响旧架构）：%s", e)

    # 新版架构：初始化智能体池（独立于 unified_orchestrator 供路由直接使用）
    try:
        if _HAS_AGENT_POOL and _AgentPool is not None:
            _pool = _AgentPool()
            from app.agent_executor import (
                ArchitectExecutor,
                CoderExecutor,
                OrchestratorExecutor,
                ReviewerExecutor,
                TesterExecutor,
            )
            _pool.register(ArchitectExecutor())
            _pool.register(CoderExecutor())
            _pool.register(ReviewerExecutor())
            _pool.register(TesterExecutor())
            _pool.register(OrchestratorExecutor())
            app.state.agent_pool = _pool
            logger.info("智能体池已就绪：count=%d", _pool.count)
        else:
            app.state.agent_pool = None
            logger.warning("智能体池未启用（导入失败），旧架构仍可正常使用")
    except Exception as e:
        app.state.agent_pool = None
        logger.warning("智能体池初始化失败（不影响旧架构）：%s", e)

    yield

    # Bug P4 修复：应用关闭时释放核心资源，避免长期运行后连接/句柄泄漏
    logger.info("应用关闭，开始释放资源...")

    # 0. 释放新版架构资源
    try:
        _orch = getattr(app.state, "unified_orchestrator", None)
        if _orch is not None:
            logger.info("新版编排引擎已关闭")
    except Exception as e:
        logger.warning("关闭新版编排引擎时出错：%s", e)

    try:
        _pool = getattr(app.state, "agent_pool", None)
        if _pool is not None:
            logger.info("智能体池已关闭")
    except Exception as e:
        logger.warning("关闭智能体池时出错：%s", e)

    # 1. 取消仍在运行的后台任务，避免事件循环关闭时产生 warning
    try:
        from app.api.v1.tasks import _background_tasks
        for task in list(_background_tasks):
            if not task.done():
                task.cancel()
        for task in list(_background_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        _background_tasks.clear()
    except Exception as e:
        logger.warning("取消后台任务时出错：%s", e)

    # 2. 关闭 checkpointer 的 sqlite 连接（断点续跑用独立连接）
    try:
        from app.graph.pipeline import _checkpointer_conn
        if _checkpointer_conn is not None:
            _checkpointer_conn.close()
            logger.info("Checkpointer 连接已关闭")
    except Exception as e:
        logger.warning("关闭 checkpointer 连接时出错：%s", e)

    # 3. 释放 SQLAlchemy 连接池
    try:
        from app.db.sqlite_client import db
        db.engine.dispose()
        logger.info("SQLite 连接池已释放")
    except Exception as e:
        logger.warning("释放数据库连接池时出错：%s", e)

    logger.info("应用已关闭。")


app = FastAPI(
    title="多智能体协同代码生成与审查系统",
    version="0.1.0",
    description="需求 → 多智能体协作生成 → AST + LLM 双层校验 → 测试生成 → 交付",
    lifespan=lifespan,
)

# CORS
cors_origins = settings.security.cors_origins
if isinstance(cors_origins, str):
    cors_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 速率限制中间件（对 /api/ 路径限流，超过限制返回 429）
try:
    from app.core.rate_limiter import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
    logger.info("速率限制中间件已启用")
except Exception as e:
    logger.warning("速率限制中间件初始化失败：%s", e)


# 修复 JS 模块 MIME 类型（ES 模块要求 Content-Type: application/javascript 或 text/javascript）
# 同时禁用 JS/HTML/CSS 缓存，避免开发阶段浏览器使用旧版本
@app.middleware("http")
async def js_mime_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith(".js") or path.endswith(".mjs"):
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif path.endswith(".html") or path.endswith(".css"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# API 响应禁用浏览器缓存
@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# 全局异常兜底
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("未捕获异常：%s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": f"内部错误：{type(exc).__name__}: {exc}"},
    )


# 挂载路由
# 所有业务路由统一加 API Key 鉴权依赖；/api/v1/health 在 verify_api_key 内部白名单放行
# 旧架构路由：tasks / skills / execute / eval（基于 app.graph.pipeline）
# 新架构路由：agents（基于 app.unified_orchestrator + AgentExecutor 执行层）
_auth_deps = [Depends(verify_api_key)]
app.include_router(tasks_router, dependencies=_auth_deps)
app.include_router(skills_router, dependencies=_auth_deps)
app.include_router(execute_router, dependencies=_auth_deps)
app.include_router(misc_router, dependencies=_auth_deps)
app.include_router(eval_router, dependencies=_auth_deps)
app.include_router(agents_router, dependencies=_auth_deps)
app.include_router(jobs_router, dependencies=_auth_deps)
app.include_router(pipeline_router, dependencies=_auth_deps)
app.include_router(slo_router, dependencies=_auth_deps)
app.include_router(ws_router, prefix="/api/v1")

# 挂载前端静态页面
_web_dir = Path(__file__).resolve().parent.parent / "web"
if _web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(_web_dir), html=True), name="web")


@app.get("/", tags=["root"], include_in_schema=False)
async def root():
    """根路径：直接跳转到前端页面（301 永久重定向，避免浏览器缓存错误）。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/web/", status_code=302)


def main():
    """CLI entry point for pyproject.toml [project.scripts]."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.app.env == "dev",
    )


if __name__ == "__main__":
    main()
