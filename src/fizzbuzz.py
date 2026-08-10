"""FizzBuzz 实现模块。"""

from typing import List


def fizzbuzz(n: int) -> List[str]:
    """返回 1 到 n 的 FizzBuzz 替换列表。

    规则：
      - 能被 3 整除 -> "Fizz"
      - 能被 5 整除 -> "Buzz"
      - 能被 3 和 5 整除 -> "FizzBuzz"
      - 其他情况 -> 数字的字符串形式

    Args:
        n: 正整数上限。

    Returns:
        长度为 n 的字符串列表。

    Raises:
        TypeError: 当 n 不是整数时。
        ValueError: 当 n 小于 1 时。
    """
    if not isinstance(n, int):
        raise TypeError("n 必须是整数")
    if n < 1:
        raise ValueError("n 必须大于等于 1")

    result: List[str] = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return result
