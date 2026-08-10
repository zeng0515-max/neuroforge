"""线程安全计数器模块。

提供线程安全的计数器实现，支持并发环境下的自增与读取操作。
"""

import threading
from typing import Optional


class ThreadSafeCounter:
    """线程安全的计数器。

    使用互斥锁保证 increment 与 get_value 操作的原子性，
    适用于多线程并发场景。

    Attributes:
        _value: 当前计数值。
        _lock: 保护计数值的互斥锁。
    """

    def __init__(self, initial: int = 0) -> None:
        """初始化计数器。

        Args:
            initial: 初始计数值，默认为 0。

        Raises:
            TypeError: 当 initial 不是整数时抛出。
            ValueError: 当 initial 为负数时抛出。
        """
        if not isinstance(initial, int):
            raise TypeError("initial 必须是整数")
        if initial < 0:
            raise ValueError("initial 不能为负数")
        self._value: int = initial
        self._lock: threading.Lock = threading.Lock()

    def increment(self, step: int = 1) -> int:
        """将计数器增加指定步长，并返回更新后的值。

        Args:
            step: 增加步长，默认为 1。

        Returns:
            更新后的计数值。

        Raises:
            TypeError: 当 step 不是整数时抛出。
            ValueError: 当 step 为负数时抛出。
        """
        if not isinstance(step, int):
            raise TypeError("step 必须是整数")
        if step < 0:
            raise ValueError("step 不能为负数")
        with self._lock:
            self._value += step
            return self._value

    def get_value(self) -> int:
        """返回当前计数值。

        Returns:
            当前计数值。
        """
        with self._lock:
            return self._value

    def reset(self, value: int = 0) -> None:
        """将计数器重置为指定值。

        Args:
            value: 重置后的计数值，默认为 0。

        Raises:
            TypeError: 当 value 不是整数时抛出。
            ValueError: 当 value 为负数时抛出。
        """
        if not isinstance(value, int):
            raise TypeError("value 必须是整数")
        if value < 0:
            raise ValueError("value 不能为负数")
        with self._lock:
            self._value = value

    def __repr__(self) -> str:
        """返回计数器的字符串表示。"""
        return f"ThreadSafeCounter(value={self.get_value()})"
