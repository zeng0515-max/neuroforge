"""速率限制模块：基于 Token Bucket 算法的 RateLimiter。

支持 per-IP 和 per-API-Key 限流，使用 defaultdict 和 time.monotonic 实现，
无外部依赖。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.core.config import settings

logger = logging.getLogger(__name__)


class TokenBucket:
    """令牌桶算法实现。

    以恒定速率 r（requests/second）向桶中添加令牌，桶容量上限为 burst。
    每次请求消耗一个令牌，令牌不足时拒绝请求。
    """

    __slots__ = ("_rate", "_burst", "_tokens", "_last_refill")

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """按时间间隔补充令牌。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self._rate
        if new_tokens > 0:
            self._tokens = min(self._burst, self._tokens + new_tokens)
            self._last_refill = now

    def consume(self) -> bool:
        """尝试消耗一个令牌。成功返回 True，失败返回 False。"""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def burst(self) -> int:
        return self._burst


class RateLimiter:
    """速率限制器。

    维护 per-IP 和 per-API-Key 的令牌桶，支持不同速率配置。
    使用 time.monotonic 保证时间单调递增，不受系统时间调整影响。
    """

    def __init__(self) -> None:
        self._ip_buckets: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                rate=settings.rate_limit.api_rate,
                burst=settings.rate_limit.api_burst,
            )
        )
        self._key_buckets: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                rate=settings.rate_limit.api_rate,
                burst=settings.rate_limit.api_burst,
            )
        )
        self._llm_buckets: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                rate=settings.rate_limit.llm_rate,
                burst=settings.rate_limit.llm_burst,
            )
        )

    def check_ip(self, ip: str) -> bool:
        """检查 IP 是否超过速率限制。"""
        return self._ip_buckets[ip].consume()

    def check_api_key(self, api_key: str) -> bool:
        """检查 API Key 是否超过速率限制。"""
        return self._key_buckets[api_key].consume()

    def check_llm_ip(self, ip: str) -> bool:
        """检查 IP 的 LLM 调用速率限制。"""
        return self._llm_buckets[ip].consume()

    def get_bucket_status(self, ip: str) -> Dict[str, Any]:
        """获取指定 IP 的当前桶状态（调试用）。"""
        ip_bucket = self._ip_buckets.get(ip)
        return {
            "ip_bucket": {
                "rate": ip_bucket.rate if ip_bucket else settings.rate_limit.api_rate,
                "burst": ip_bucket.burst if ip_bucket else settings.rate_limit.api_burst,
            },
        }


# 全局单例
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI 中间件：对 /api/ 路径下的请求应用速率限制。

    超过限制时返回 429 Too Many Requests。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 只对 /api/ 路径限流
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        # 获取客户端 IP
        forwarded = request.headers.get("X-Forwarded-For", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else request.client.host if request.client else "unknown"
        if not client_ip or client_ip == "unknown":
            client_ip = "127.0.0.1"

        # 获取 API Key（如果有）
        api_key = request.headers.get("X-API-Key", "")

        # 检查 IP 级别限流
        if not rate_limiter.check_ip(client_ip):
            logger.warning("IP 限流触发: %s, path=%s", client_ip, path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too Many Requests",
                    "message": "请求过于频繁，请稍后重试",
                    "retry_after": "1s",
                },
                headers={"Retry-After": "1"},
            )

        # 如果有 API Key，额外检查 API Key 级别限流
        if api_key and not rate_limiter.check_api_key(api_key):
            logger.warning("API Key 限流触发: %s, path=%s", api_key[:8], path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too Many Requests",
                    "message": "API Key 请求频率超限，请稍后重试",
                    "retry_after": "1s",
                },
                headers={"Retry-After": "1"},
            )

        return await call_next(request)


__all__ = [
    "TokenBucket",
    "RateLimiter",
    "RateLimitMiddleware",
    "rate_limiter",
]