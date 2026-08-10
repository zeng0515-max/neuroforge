"""电话号码处理工具模块。"""


def clean_phone(phone: str) -> str:
    """从电话号码字符串中移除所有非数字字符。

    仅保留字符串中的数字（0-9），其余字符（空格、括号、连字符、
    加号等）全部移除。空字符串或仅含非数字字符的输入返回空字符串。

    Args:
        phone: 待清理的电话号码字符串。

    Returns:
        仅包含数字字符的清理后字符串。

    Raises:
        TypeError: 当 phone 不是字符串类型时抛出。
    """
    if not isinstance(phone, str):
        raise TypeError(f"phone 必须是字符串，实际为 {type(phone).__name__}")

    try:
        return "".join(ch for ch in phone if ch.isdigit())
    except (TypeError, AttributeError):
        return ""
