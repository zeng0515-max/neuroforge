"""API Key 鉴权依赖。

设计要点：
- 当 settings.security.api_key 为空时不强制鉴权（保留开发模式便利）
- 生产部署必须在环境变量中设置 API_KEY
- /api/v1/health 健康检查端点保留匿名，便于负载均衡/探针探测
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request, WebSocket, WebSocketException

from app.core.config import settings

# 健康检查路径白名单：不鉴权，便于负载均衡/容器探针访问
_HEALTH_PATHS = {"/api/v1/health"}


def is_production_env() -> bool:
    """Return True when this process is running in a production environment."""
    return settings.app.env.strip().lower() in {"production", "prod"}


def _unauthorized_response() -> HTTPException:
    if not settings.security.api_key:
        return HTTPException(
            status_code=503,
            detail="API_KEY is not configured; refusing to accept requests",
        )
    return HTTPException(status_code=401, detail="invalid api key")


async def verify_api_key(
    request: Request,
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> None:
    """校验 X-API-Key 请求头是否与配置的 API_KEY 匹配。

    - 健康检查端点直接放行
    - API_KEY 为空时（开发模式）直接放行
    - 否则要求请求头 X-API-Key 与配置值完全一致，否则 401
    """
    # 健康检查不鉴权
    if request.url.path in _HEALTH_PATHS:
        return
    expected = settings.security.api_key
    # Production must fail closed when API_KEY is missing.
    if not expected:
        if is_production_env():
            raise _unauthorized_response()
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


async def verify_api_key_ws(
    websocket: WebSocket,
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> None:
    """WebSocket 版本鉴权依赖，与 REST 依赖保持同一套配置和拒绝策略。"""
    if websocket.url.path in _HEALTH_PATHS:
        return
    expected = settings.security.api_key
    if not expected:
        if is_production_env():
            raise WebSocketException(code=1008, reason="API_KEY is not configured")
        return
    if x_api_key != expected:
        raise WebSocketException(code=1008, reason="invalid api key")


__all__ = ["verify_api_key", "verify_api_key_ws", "is_production_env"]
