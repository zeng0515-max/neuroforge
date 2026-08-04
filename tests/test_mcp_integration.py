"""MCP 集成核心功能单元测试。

覆盖模块：
- app/tools/mcp_server.py：create_server()、_to_text_content()
- app/tools/mcp_client.py：MCPClient、_build_params_from_schema()、_json_type()、_safe_exit()
- app/core/config.py：MCPConfig 默认值、settings.mcp
- app/agents/code_gen_agent.py：导入与实例化

测试策略：
- 不依赖外部进程或网络，全部在 DryRun 模式下运行
- MCP server handler 不直接测试（需要完整 MCP 协议握手），
  改为测试其底层依赖 tool_registry 的行为
- MCP client 不测试实际连接，仅测试实例化与静态/辅助方法
- 异步测试使用 @pytest.mark.asyncio（项目已配置 pytest-asyncio，asyncio_mode=auto）
"""

import json
from typing import Any

import pytest
import mcp.types as types
from mcp.server import Server

from app.core.config import MCPConfig, settings
from app.tools.mcp_client import MCPClient
from app.tools.mcp_server import SERVER_NAME, SERVER_VERSION, _to_text_content, create_server
from app.tools.tool_registry import ToolParamSpec, ToolRegistry, tool_registry


# ------------------------------------------------------------------ #
#  app/tools/mcp_server.py：create_server() 与常量
# ------------------------------------------------------------------ #

def test_create_server_returns_server_instance() -> None:
    """create_server() 应返回 mcp.server.Server 实例。"""
    server = create_server()
    assert isinstance(server, Server)


def test_create_server_does_not_raise() -> None:
    """create_server() 不应抛出异常。"""
    create_server()


def test_create_server_returns_independent_instances() -> None:
    """多次调用 create_server() 应返回独立实例。"""
    s1 = create_server()
    s2 = create_server()
    assert s1 is not s2


def test_server_name_constant() -> None:
    """SERVER_NAME 常量应为项目名称。"""
    assert SERVER_NAME == "multi-agent-code-system"


def test_server_version_constant() -> None:
    """SERVER_VERSION 常量应为 0.1.0。"""
    assert SERVER_VERSION == "0.1.0"


# ------------------------------------------------------------------ #
#  app/tools/mcp_server.py：_to_text_content()
# ------------------------------------------------------------------ #

def test_to_text_content_with_string() -> None:
    """字符串输入应原样使用。"""
    result = _to_text_content("hello world")
    assert isinstance(result, types.TextContent)
    assert result.type == "text"
    assert result.text == "hello world"


def test_to_text_content_with_dict() -> None:
    """dict 输入应序列化为 JSON。"""
    data = {"key": "value", "num": 42}
    result = _to_text_content(data)
    assert isinstance(result, types.TextContent)
    assert json.loads(result.text) == data


def test_to_text_content_with_dict_chinese() -> None:
    """dict 含中文应正确序列化（ensure_ascii=False）。"""
    data = {"msg": "你好世界"}
    result = _to_text_content(data)
    assert "你好世界" in result.text


def test_to_text_content_with_list() -> None:
    """list 输入应序列化为 JSON。"""
    data = [1, "two", {"three": 3}]
    result = _to_text_content(data)
    assert isinstance(result, types.TextContent)
    assert json.loads(result.text) == data


def test_to_text_content_with_int() -> None:
    """整数输入应转为字符串。"""
    result = _to_text_content(42)
    assert isinstance(result, types.TextContent)
    assert result.text == "42"


def test_to_text_content_with_none() -> None:
    """None 输入应转为字符串 'None'。"""
    result = _to_text_content(None)
    assert isinstance(result, types.TextContent)
    assert result.text == "None"


def test_to_text_content_with_bool() -> None:
    """布尔输入应转为字符串。"""
    result = _to_text_content(True)
    assert isinstance(result, types.TextContent)
    assert result.text == "True"


# ------------------------------------------------------------------ #
#  MCP Server 与 tool_registry 集成（验证 handler 底层行为）
#  不直接调用 server handler（需要完整 MCP 协议握手），
#  而是验证 handler 内部调用的 tool_registry 接口
# ------------------------------------------------------------------ #

def test_registry_has_four_builtin_tools() -> None:
    """tool_registry 应包含 4 个内置工具（list_tools handler 会返回这些）。"""
    names = tool_registry.list_names()
    expected = {"validate_code", "write_file", "read_file", "list_dir"}
    assert expected.issubset(set(names))


def test_to_openai_tools_contains_builtin_tools() -> None:
    """to_openai_tools() 应返回包含 4 个内置工具的列表。"""
    tools = tool_registry.to_openai_tools()
    tool_names = {t["function"]["name"] for t in tools}
    expected = {"validate_code", "write_file", "read_file", "list_dir"}
    assert expected.issubset(tool_names)


def test_list_dir_tool_returns_result() -> None:
    """调用 list_dir 工具应返回非异常结果（call_tool handler 的底层行为）。"""
    result = tool_registry.call("list_dir", {"path": "."})
    assert isinstance(result, str)
    # 不应以 ERROR 开头
    assert not result.startswith("ERROR")


def test_call_nonexistent_tool_raises_keyerror() -> None:
    """调用不存在的工具应抛 KeyError（call_tool handler 会捕获并返回 isError=True）。"""
    with pytest.raises(KeyError, match="未注册的工具"):
        tool_registry.call("nonexistent_tool_xyz", {})


# ------------------------------------------------------------------ #
#  app/tools/mcp_client.py：MCPClient 实例化
# ------------------------------------------------------------------ #

def test_mcp_client_init_default_registry() -> None:
    """默认使用全局 tool_registry。"""
    client = MCPClient()
    assert client._registry is tool_registry


def test_mcp_client_init_custom_registry() -> None:
    """可传入自定义 registry。"""
    reg = ToolRegistry()
    client = MCPClient(registry=reg)
    assert client._registry is reg
    assert client._registry is not tool_registry


def test_mcp_client_init_empty_state() -> None:
    """初始化后内部状态应为空。"""
    client = MCPClient()
    assert client._sessions == {}
    assert client._tools_map == {}
    assert client._contexts == {}
    assert client._loops == {}
    assert client._loop_threads == {}


# ------------------------------------------------------------------ #
#  app/tools/mcp_client.py：_build_params_from_schema()
# ------------------------------------------------------------------ #

def test_build_params_basic_schema() -> None:
    """基本 JSON Schema 应正确解析为参数列表。"""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "名称"},
            "age": {"type": "integer", "description": "年龄"},
        },
        "required": ["name"],
    }
    params = MCPClient._build_params_from_schema(schema)
    assert len(params) == 2
    assert params[0].name == "name"
    assert params[0].type == "string"
    assert params[0].required is True
    assert params[0].description == "名称"
    assert params[1].name == "age"
    assert params[1].type == "integer"
    assert params[1].required is False
    assert params[1].description == "年龄"


def test_build_params_with_default_values() -> None:
    """带 default 的参数应正确解析。"""
    schema = {
        "properties": {
            "limit": {"type": "integer", "default": 10},
        },
    }
    params = MCPClient._build_params_from_schema(schema)
    assert len(params) == 1
    assert params[0].name == "limit"
    assert params[0].type == "integer"
    assert params[0].default == 10
    assert params[0].required is False


def test_build_params_empty_schema() -> None:
    """空 schema 应返回空列表。"""
    assert MCPClient._build_params_from_schema({}) == []


def test_build_params_none_schema() -> None:
    """None 输入应返回空列表。"""
    assert MCPClient._build_params_from_schema(None) == []


def test_build_params_non_dict_schema() -> None:
    """非 dict 输入应返回空列表。"""
    assert MCPClient._build_params_from_schema("not a dict") == []
    assert MCPClient._build_params_from_schema(123) == []


def test_build_params_without_properties() -> None:
    """没有 properties 字段应返回空列表。"""
    schema = {"type": "object", "required": ["name"]}
    assert MCPClient._build_params_from_schema(schema) == []


def test_build_params_returns_tool_param_spec() -> None:
    """返回的应是 ToolParamSpec 实例。"""
    schema = {"properties": {"x": {"type": "string"}}}
    params = MCPClient._build_params_from_schema(schema)
    assert isinstance(params[0], ToolParamSpec)


def test_build_params_property_without_type_defaults_to_string() -> None:
    """属性没有 type 字段应默认为 string。"""
    schema = {"properties": {"x": {"description": "no type"}}}
    params = MCPClient._build_params_from_schema(schema)
    assert params[0].type == "string"


def test_build_params_property_without_description() -> None:
    """属性没有 description 应默认为空字符串。"""
    schema = {"properties": {"x": {"type": "string"}}}
    params = MCPClient._build_params_from_schema(schema)
    assert params[0].description == ""


# ------------------------------------------------------------------ #
#  app/tools/mcp_client.py：_json_type()
# ------------------------------------------------------------------ #

def test_json_type_string() -> None:
    assert MCPClient._json_type("string") == "string"


def test_json_type_integer() -> None:
    assert MCPClient._json_type("integer") == "integer"


def test_json_type_number() -> None:
    assert MCPClient._json_type("number") == "number"


def test_json_type_boolean() -> None:
    assert MCPClient._json_type("boolean") == "boolean"


def test_json_type_array() -> None:
    assert MCPClient._json_type("array") == "array"


def test_json_type_object() -> None:
    assert MCPClient._json_type("object") == "object"


def test_json_type_null_maps_to_string() -> None:
    """null 应映射为 string。"""
    assert MCPClient._json_type("null") == "string"


def test_json_type_unknown_defaults_to_string() -> None:
    """未知类型应默认为 string。"""
    assert MCPClient._json_type("unknown") == "string"
    assert MCPClient._json_type("custom") == "string"


def test_json_type_list_takes_first() -> None:
    """列表类型（如 ["string", "null"]）应取第一个元素。"""
    assert MCPClient._json_type(["string", "null"]) == "string"
    assert MCPClient._json_type(["integer", "null"]) == "integer"


def test_json_type_empty_list_defaults_to_string() -> None:
    """空列表应默认为 string。"""
    assert MCPClient._json_type([]) == "string"


# ------------------------------------------------------------------ #
#  app/tools/mcp_client.py：_safe_exit()（异步）
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_safe_exit_with_none() -> None:
    """_safe_exit(None) 应不抛异常。"""
    await MCPClient._safe_exit(None)


@pytest.mark.asyncio
async def test_safe_exit_calls_aexit() -> None:
    """_safe_exit 应调用 context manager 的 __aexit__。"""
    called = False

    class MockCM:
        async def __aexit__(self, *args: Any) -> None:
            nonlocal called
            called = True

    await MCPClient._safe_exit(MockCM())
    assert called is True


@pytest.mark.asyncio
async def test_safe_exit_swallows_exceptions() -> None:
    """_safe_exit 应吞掉 __aexit__ 抛出的异常。"""
    class FailingCM:
        async def __aexit__(self, *args: Any) -> None:
            raise RuntimeError("intentional failure")

    # 不应抛异常
    await MCPClient._safe_exit(FailingCM())


# ------------------------------------------------------------------ #
#  app/core/config.py：MCPConfig
# ------------------------------------------------------------------ #

def test_mcp_config_default_server_enabled_is_false() -> None:
    """server_enabled 默认应为 False。"""
    config = MCPConfig()
    assert config.server_enabled is False


def test_mcp_config_default_client_servers_is_empty() -> None:
    """client_servers 默认应为空字符串。"""
    config = MCPConfig()
    assert config.client_servers == ""


def test_settings_mcp_exists() -> None:
    """settings.mcp 应存在且为 MCPConfig 类型。"""
    assert hasattr(settings, "mcp")
    assert isinstance(settings.mcp, MCPConfig)


def test_settings_mcp_defaults() -> None:
    """settings.mcp 应有正确的默认值。"""
    assert settings.mcp.server_enabled is False
    assert isinstance(settings.mcp.client_servers, str)


# ------------------------------------------------------------------ #
#  app/agents/code_gen_agent.py：导入与实例化
# ------------------------------------------------------------------ #

def test_code_gen_agent_imports_succeed() -> None:
    """验证关键导入不报错。"""
    from app.tools.tool_registry import tool_registry as _tr
    from langchain_core.messages import AIMessage, ToolMessage
    assert _tr is not None
    assert AIMessage is not None
    assert ToolMessage is not None


def test_aimessage_toolmessage_usable() -> None:
    """验证 AIMessage / ToolMessage 可正常构造。"""
    from langchain_core.messages import AIMessage, ToolMessage
    ai = AIMessage(content="test")
    assert ai.content == "test"
    tm = ToolMessage(content="result", tool_call_id="tc1")
    assert tm.content == "result"


def test_code_gen_agent_instantiation() -> None:
    """CodeGenAgent 应能正常实例化。"""
    from app.agents.code_gen_agent import CodeGenAgent
    agent = CodeGenAgent()
    assert agent is not None
    assert hasattr(agent, "llm")
    assert hasattr(agent, "use_code_model")


def test_code_gen_agent_use_code_model_default_true() -> None:
    """CodeGenAgent 默认 use_code_model=True。"""
    from app.agents.code_gen_agent import CodeGenAgent
    agent = CodeGenAgent()
    assert agent.use_code_model is True


def test_code_gen_agent_use_code_model_false() -> None:
    """CodeGenAgent 可设置 use_code_model=False。"""
    from app.agents.code_gen_agent import CodeGenAgent
    agent = CodeGenAgent(use_code_model=False)
    assert agent.use_code_model is False
