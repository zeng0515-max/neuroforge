"""CSV 解析器：从原始文本解析为行列表。

支持标准 CSV 格式，包括：
- 引号包裹的字段（含逗号、换行、转义引号）
- 自定义分隔符
- 空行跳过
- 类型转换
"""

from typing import List, Optional, Any


class CSVParseError(Exception):
    """CSV 解析异常基类。"""


class UnclosedQuoteError(CSVParseError):
    """引号未闭合异常。"""


def parse_csv(
    text: str,
    delimiter: str = ",",
    quotechar: str = '"',
    skip_blank_lines: bool = True,
    convert_types: bool = True,
) -> List[List[Any]]:
    """从原始文本解析 CSV 数据，返回行列表。

    Args:
        text: 原始 CSV 文本。
        delimiter: 字段分隔符，默认逗号。
        quotechar: 引号字符，默认双引号。
        skip_blank_lines: 是否跳过空行，默认 True。
        convert_types: 是否尝试类型转换（int/float），默认 True。

    Returns:
        解析后的行列表，每行为字段列表。

    Raises:
        TypeError: 参数类型不合法。
        ValueError: delimiter/quotechar 参数不合法。
        UnclosedQuoteError: 存在未闭合的引号。
    """
    # ---- 输入参数校验 ----
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise ValueError("delimiter 必须是单个字符")
    if not isinstance(quotechar, str) or len(quotechar) != 1:
        raise ValueError("quotechar 必须是单个字符")
    if delimiter == quotechar:
        raise ValueError("delimiter 与 quotechar 不能相同")
    if not isinstance(skip_blank_lines, bool):
        raise TypeError("skip_blank_lines 必须是布尔值")
    if not isinstance(convert_types, bool):
        raise TypeError("convert_types 必须是布尔值")

    # ---- 边界条件：空文本 ----
    if not text:
        return []

    rows: List[List[Any]] = []
    current_row: List[str] = []
    current_field: List[str] = []
    in_quotes: bool = False
    field_started: bool = False

    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if in_quotes:
            # 处于引号内部
            if ch == quotechar:
                # 可能是转义引号或引号结束
                if i + 1 < n and text[i + 1] == quotechar:
                    # 转义引号（两个连续引号表示一个引号）
                    current_field.append(quotechar)
                    i += 2
                    continue
                else:
                    # 引号结束
                    in_quotes = False
                    i += 1
                    continue
            else:
                # 普通字符（含换行、分隔符）
                current_field.append(ch)
                i += 1
                continue
        else:
            # 不在引号内
            if ch == quotechar:
                # 引号只能出现在字段开头
                if not field_started:
                    in_quotes = True
                    field_started = True
                    i += 1
                    continue
                else:
                    # 引号出现在字段中间，按字面处理
                    current_field.append(ch)
                    i += 1
                    continue
            elif ch == delimiter:
                # 字段结束
                current_row.append("".join(current_field))
                current_field = []
                field_started = False
                i += 1
                continue
            elif ch == "\n":
                # 行结束
                current_row.append("".join(current_field))
                current_field = []
                field_started = False
                rows.append(current_row)
                current_row = []
                i += 1
                continue
            elif ch == "\r":
                # 处理 CRLF 或 CR
                if i + 1 < n and text[i + 1] == "\n":
                    i += 1  # 跳过 \n，由下一轮处理
                current_row.append("".join(current_field))
                current_field = []
                field_started = False
                rows.append(current_row)
                current_row = []
                i += 1
                continue
            else:
                # 普通字符
                current_field.append(ch)
                field_started = True
                i += 1
                continue

    # ---- 处理末尾残留 ----
    if in_quotes:
        raise UnclosedQuoteError("CSV 中存在未闭合的引号")

    if field_started or current_field:
        current_row.append("".join(current_field))
        rows.append(current_row)
    elif current_row:
        rows.append(current_row)

    # ---- 跳过空行 ----
    if skip_blank_lines:
        rows = [r for r in rows if not (len(r) == 1 and r[0] == "")]

    # ---- 类型转换 ----
    if convert_types:
        rows = [_convert_field(f) for f in rows]

    return rows


def _convert_field(field: str) -> Any:
    """尝试将字段转换为 int/float，失败则保留字符串。"""
    if field == "":
        return ""
    try:
        return int(field)
    except ValueError:
        pass
    try:
        return float(field)
    except ValueError:
        pass
    return field


def parse_csv_file(
    filepath: str,
    delimiter: str = ",",
    quotechar: str = '"',
    encoding: str = "utf-8",
    convert_types: bool = True,
) -> List[List[Any]]:
    """从文件读取并解析 CSV。

    Args:
        filepath: CSV 文件路径。
        delimiter: 字段分隔符。
        quotechar: 引号字符。
        encoding: 文件编码，默认 utf-8。
        convert_types: 是否类型转换。

    Returns:
        解析后的行列表。

    Raises:
        TypeError: 参数类型不合法。
        FileNotFoundError: 文件不存在。
        UnclosedQuoteError: 引号未闭合。
    """
    if not isinstance(filepath, str) or not filepath:
        raise TypeError("filepath 必须是非空字符串")

    with open(filepath, "r", encoding=encoding, newline="") as f:
        content = f.read()

    return parse_csv(
        content,
        delimiter=delimiter,
        quotechar=quotechar,
        convert_types=convert_types,
    )
