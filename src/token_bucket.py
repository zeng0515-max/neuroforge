"""令牌桶限流器实现。"""

import threading
import time
from typing import Optional


class TokenBucket:
    """基于令牌桶算法的限流器。

    令牌以固定速率补充，桶容量有限。每次调用 allow 时消耗一个令牌，
    若桶中无令牌则拒绝请求。

    Attributes:
        capacity: 桶的最大容量（最大突发令牌数）。
        rate: 令牌补充速率（每秒补充的令牌数）。
    """

    def __init__(self, capacity: float, rate: float) -> None:
        """初始化令牌桶。

        Args:
            capacity: 桶容量，必须为正数。
            rate: 令牌补充速率（个/秒），必须为正数。

        Raises:
            ValueError: 当 capacity 或 rate 非正数时。
            TypeError: 当 capacity 或 rate 不是数值类型时。
        """
        if not isinstance(capacity, (int, float)) or isinstance(capacity, bool):
            raise TypeError("capacity 必须是数值类型")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            raise TypeError("rate 必须是数值类型")
        if capacity <= 0:
            raise ValueError("capacity 必须为正数")
        if rate <= 0:
            raise ValueError("rate 必须为正数")

        self.capacity = float(capacity)
        self.rate = float(rate)
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """按时间流逝补充令牌（调用方需持有锁）。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

    def allow(self, tokens: int = 1) -> bool:
        """尝试消耗指定数量的令牌。

        若桶中有足够令牌则消耗并返回 True，否则返回 False。

        Args:
            tokens: 本次请求消耗的令牌数，默认 1。

        Returns:
            bool: 是否允许通过。

        Raises:
            TypeError: 当 tokens 不是整数时。
            ValueError: 当 tokens 小于等于 0 时。
        """
        if not isinstance(tokens, int) or isinstance(tokens, bool):
            raise TypeError("tokens 必须是整数")
        if tokens <= 0:
            raise ValueError("tokens 必须为正整数")

        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    @property
    def available_tokens(self) -> float:
        """返回当前可用令牌数（近似值）。"""
        with self._lock:
            self._refill()
            return self._tokens

    def reset(self) -> None:
        """重置令牌桶为满状态。"""
        with self._lock:
            self._tokens = self.capacity
            self._last_refill = time.monotonic()
