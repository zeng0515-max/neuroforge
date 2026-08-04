"""工具注册表单元测试。

重点测：
- @register 装饰器（带/不带括号、name/description 覆盖）
- has / list_names / get_spec 查询
- call 调用（参数校验、错误处理）
- to_openai_tools 导出 OpenAI Function Calling 格式
- _extract_params 从签名推导参数
- _pytype_to_json 类型映射
- 内置工具 validate_code / write_file / list_dir 已注册

注意：本文件**不**使用 `from __future__ import annotations`，
否则函数注解会变成字符串，_extract_params 无法通过 _PY_TYPE_TO_JSON
正确映射 int/float/bool 等类型（这是被测代码的已知行为）。
"""

from typing import List, Optional
from unittest.mock import patch

import pytest

from app.tools.tool_registry import (
    ToolParamSpec,
    ToolRegistry,
    ToolSpec,
    _extract_params,
    _pytype_to_json,
    pname_clean,
    tool_registry as global_registry,
)


# ------------------------------------------------------------------ #
#  注册：装饰器多种用法
# ------------------------------------------------------------------ #
def test_register_without_parentheses() -> None:
    """@registry.register 无括号用法应正确注册。"""
    reg = ToolRegistry()

    @reg.register
    def foo(x: int) -> str:
        """foo 工具"""
        return str(x)

    assert reg.has("foo")
    spec = reg.get_spec("foo")
    assert spec is not None
    assert spec.description == "foo 工具"
    assert spec.params[0].name == "x"
    assert spec.params[0].type == "integer"


def test_register_with_parentheses_and_overrides() -> None:
    """@registry.register(name=, description=) 应覆盖默认值。"""
    reg = ToolRegistry()

    @reg.register(name="custom_name", description="自定义说明")
    def bar(y: str = "default") -> str:
        """原始 docstring（应被 description 覆盖）"""
        return y

    assert reg.has("custom_name")
    assert not reg.has("bar")
    spec = reg.get_spec("custom_name")
    assert spec.description == "自定义说明"
    # y 有默认值 → required=False
    assert spec.params[0].required is False
    assert spec.params[0].default == "default"


def test_register_warns_on_missing_docstring(caplog: pytest.LogCaptureFixture) -> None:
    """无 docstring 且未传 description 应记录 warning。"""
    reg = ToolRegistry()

    @reg.register
    def no_doc(z: int) -> int:  # noqa: D401 - 故意没有 docstring
        return z

    assert reg.has("no_doc")
    # warning 通过 logger 发出，caplog 应捕获
    assert any("没有 docstring" in r.message for r in caplog.records)


# ------------------------------------------------------------------ #
#  查询
# ------------------------------------------------------------------ #
def test_list_names_sorted() -> None:
    """list_names 应返回排序后的工具名列表。"""
    reg = ToolRegistry()

    @reg.register
    def zebra() -> str:
        """z"""
        return "z"

    @reg.register
    def alpha() -> str:
        """a"""
        return "a"

    names = reg.list_names()
    assert names == sorted(names)
    assert "zebra" in names
    assert "alpha" in names


def test_get_spec_unknown_returns_none() -> None:
    """未注册工具应返回 None。"""
    reg = ToolRegistry()
    assert reg.get_spec("does_not_exist") is None


# ------------------------------------------------------------------ #
#  调用
# ------------------------------------------------------------------ #
def test_call_with_valid_kwargs() -> None:
    """call 应执行 handler 并返回结果。"""
    reg = ToolRegistry()

    @reg.register
    def add(a: int, b: int) -> int:
        """相加"""
        return a + b

    assert reg.call("add", {"a": 1, "b": 2}) == 3


def test_call_with_default_args() -> None:
    """有默认值的参数可以省略。"""
    reg = ToolRegistry()

    @reg.register
    def greet(name: str, prefix: str = "Hello") -> str:
        """greet"""
        return f"{prefix}, {name}"

    assert reg.call("greet", {"name": "World"}) == "Hello, World"
    assert reg.call("greet", {"name": "World", "prefix": "Hi"}) == "Hi, World"


def test_call_unknown_raises_keyerror() -> None:
    """调用未注册工具应抛 KeyError。"""
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="未注册的工具"):
        reg.call("nope", {})


def test_call_with_wrong_type_raises_valueerror() -> None:
    """参数类型不匹配应被 pydantic 校验拦截，抛 ValueError。"""
    reg = ToolRegistry()

    @reg.register
    def strict(x: int) -> int:
        """strict"""
        return x

    # 传字符串给 int 参数应失败
    with pytest.raises(ValueError, match="参数错误"):
        reg.call("strict", {"x": "not_an_int"})


# ------------------------------------------------------------------ #
#  to_openai_tools 导出
# ------------------------------------------------------------------ #
def test_to_openai_tools_format() -> None:
    """导出的工具应符合 OpenAI Function Calling 结构。"""
    reg = ToolRegistry()

    @reg.register
    def search(query: str, top_k: int = 5) -> str:
        """搜索"""
        return query

    tools = reg.to_openai_tools()
    assert len(tools) == 1
    t = tools[0]
    assert t["type"] == "function"
    fn = t["function"]
    assert fn["name"] == "search"
    assert fn["description"] == "搜索"
    props = fn["parameters"]["properties"]
    assert "query" in props
    assert props["query"]["type"] == "string"
    assert "top_k" in props
    assert props["top_k"]["type"] == "integer"
    # query 必填，top_k 选填
    assert "query" in fn["parameters"]["required"]
    assert "top_k" not in fn["parameters"]["required"]


def test_to_openai_tools_filtered_by_names() -> None:
    """names 过滤应只返回指定工具。"""
    reg = ToolRegistry()

    @reg.register
    def tool_a() -> str:
        """a"""
        return "a"

    @reg.register
    def tool_b() -> str:
        """b"""
        return "b"

    tools = reg.to_openai_tools(names=["tool_a"])
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "tool_a"


def test_tool_spec_to_openai_function_includes_default() -> None:
    """带默认值的参数应在 properties 中包含 default 字段。"""
    spec = ToolSpec(
        name="t",
        description="d",
        params=[ToolParamSpec(name="x", type="integer", required=False, default=10)],
    )
    fn = spec.to_openai_function()
    assert fn["function"]["parameters"]["properties"]["x"]["default"] == 10


# ------------------------------------------------------------------ #
#  _extract_params / _pytype_to_json
# ------------------------------------------------------------------ #
def test_pytype_to_json_mappings() -> None:
    """常见 Python 类型应映射到 JSON Schema 类型。"""
    assert _pytype_to_json(str) == "string"
    assert _pytype_to_json(int) == "integer"
    assert _pytype_to_json(float) == "number"
    assert _pytype_to_json(bool) == "boolean"
    assert _pytype_to_json(list) == "array"
    assert _pytype_to_json(dict) == "object"


def test_pytype_to_json_optional() -> None:
    """Optional[str] 应映射到 string。"""
    assert _pytype_to_json(Optional[str]) == "string"
    assert _pytype_to_json(Optional[int]) == "integer"


def test_extract_params_skips_self() -> None:
    """_extract_params 应跳过 self/cls 参数。"""
    def method(self, x: int, name: str = "n") -> None:
        ...

    params = _extract_params(method)
    names = [p.name for p in params]
    assert "self" not in names
    assert names == ["x", "name"]
    assert params[0].required is True
    assert params[1].required is False


def test_pname_clean_invalid_identifier() -> None:
    """非法标识符应被清洗为合法 python 字段名。"""
    assert pname_clean("valid_name") == "valid_name"
    # 含特殊字符
    cleaned = pname_clean("foo-bar.baz")
    assert cleaned.isidentifier()
    # 数字开头应加前缀
    cleaned2 = pname_clean("123abc")
    assert cleaned2.isidentifier()
    assert cleaned2.startswith("f_")


# ------------------------------------------------------------------ #
#  全局 tool_registry 内置工具
# ------------------------------------------------------------------ #
def test_global_registry_has_builtin_tools() -> None:
    """全局注册表应已加载内置工具 validate_code / write_file / read_file / list_dir。"""
    names = global_registry.list_names()
    assert "validate_code" in names
    assert "write_file" in names
    assert "read_file" in names
    assert "list_dir" in names


def test_global_registry_call_validate_code() -> None:
    """调用内置 validate_code 应返回 JSON 字符串（AST 校验结果）。"""
    result = global_registry.call(
        "validate_code",
        {"source": "x = 1\n", "allowed_modules": ""},
    )
    assert isinstance(result, str)
    # 返回的是 JSON 字符串，应能被解析
    import json
    parsed = json.loads(result)
    assert isinstance(parsed, dict)


def test_global_registry_call_read_file_not_found() -> None:
    """读取不存在的文件应返回 ERROR 字符串。"""
    result = global_registry.call("read_file", {"path": "this_file_does_not_exist_xyz.py"})
    assert isinstance(result, str)
    assert "ERROR" in result
