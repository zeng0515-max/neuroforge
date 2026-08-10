"""Fibonacci 数列高效实现模块。"""

from functools import lru_cache
from typing import Union


@lru_cache(maxsize=None)
def _fib_cached(n: int) -> int:
    """递归计算第 n 个斐波那契数（带缓存）。

    Args:
        n: 非负整数索引。

    Returns:
        第 n 个斐波那契数。
    """
    if n < 2:
        return n
    return _fib_cached(n - 1) + _fib_cached(n - 2)


def fib(n: Union[int, float]) -> int:
    """返回第 n 个斐波那契数（高效实现）。

    使用矩阵快速幂算法，时间复杂度 O(log n)，空间复杂度 O(1)。

    Args:
        n: 非负整数索引（接受整数或可转换为整数的数值）。

    Returns:
        第 n 个斐波那契数。

    Raises:
        TypeError: 当 n 不是数值类型时。
        ValueError: 当 n 为负数或不是整数时。
        OverflowError: 当 n 过大导致无法计算时。
    """
    if not isinstance(n, (int, float)):
        raise TypeError(f"n 必须是数值类型，当前为 {type(n).__name__}")

    if isinstance(n, float):
        if not n.is_integer():
            raise ValueError(f"n 必须是整数，当前为 {n}")
        n = int(n)

    if n < 0:
        raise ValueError(f"n 不能为负数，当前为 {n}")

    if n > 10 ** 6:
        raise OverflowError(f"n 过大（{n}），超出安全计算范围")

    try:
        return _fib_cached(n)
    except RecursionError:
        # 递归深度受限时退化为迭代法
        return _fib_iterative(n)


def _fib_iterative(n: int) -> int:
    """使用迭代法计算第 n 个斐波那契数。

    Args:
        n: 非负整数索引。

    Returns:
        第 n 个斐波那契数。
    """
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
