"""基于 collections.deque 的 FIFO 队列实现。"""

from collections import deque
from typing import Any, Optional


class FIFOQueue:
    """使用 collections.deque 实现的先进先出（FIFO）队列。

    支持 enqueue（入队）和 dequeue（出队）操作，并处理边界条件。
    """

    def __init__(self, maxlen: Optional[int] = None) -> None:
        """初始化队列。

        Args:
            maxlen: 队列最大长度，None 表示无限制。

        Raises:
            TypeError: maxlen 不是整数或 None。
            ValueError: maxlen 为负数。
        """
        if maxlen is not None and not isinstance(maxlen, int):
            raise TypeError("maxlen 必须是整数或 None")
        if maxlen is not None and maxlen < 0:
            raise ValueError("maxlen 不能为负数")
        self._queue: deque = deque(maxlen=maxlen)

    def enqueue(self, item: Any) -> None:
        """将元素加入队尾。

        Args:
            item: 要入队的元素。
        """
        self._queue.append(item)

    def dequeue(self) -> Any:
        """从队首移除并返回元素。

        Returns:
            队首元素。

        Raises:
            IndexError: 队列为空时调用。
        """
        if self.is_empty():
            raise IndexError("无法从空队列中出队")
        return self._queue.popleft()

    def peek(self) -> Any:
        """查看队首元素但不移除。

        Returns:
            队首元素。

        Raises:
            IndexError: 队列为空时调用。
        """
        if self.is_empty():
            raise IndexError("无法查看空队列的队首元素")
        return self._queue[0]

    def is_empty(self) -> bool:
        """判断队列是否为空。"""
        return len(self._queue) == 0

    def size(self) -> int:
        """返回队列中元素个数。"""
        return len(self._queue)

    def clear(self) -> None:
        """清空队列中的所有元素。"""
        self._queue.clear()

    def __len__(self) -> int:
        """支持 len() 调用。"""
        return len(self._queue)

    def __repr__(self) -> str:
        """返回队列的字符串表示。"""
        return f"FIFOQueue({list(self._queue)!r})"
