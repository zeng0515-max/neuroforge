"""LLM 适配层单元测试。

重点测：
- DryRun 模式触发与降级
- LLMUsage token 统计的累加
- 模型切换（DryRun 路径 + 真实路径 mock）
- to_messages 消息构造
- _extract_usage 从 usage_metadata 解析
- _is_real_key 黑名单判定
- chat / achat 在 DryRun 下返回内容包含 [DryRun]
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.llm_adapter import (
    ChatResponse,
    LLMAdapter,
    LLMUsage,
    llm as global_llm,
)


# ------------------------------------------------------------------ #
#  DryRun 模式
# ------------------------------------------------------------------ #
def test_global_llm_in_dry_run_under_test_env() -> None:
    """conftest 已强制 DryRun，全局单例应处于 dry_run=True。"""
    assert global_llm.dry_run is True
    assert global_llm._main is None
    assert global_llm._code is None


def test_dry_run_chat_returns_marker_content() -> None:
    """DryRun 下 chat 应返回带 [DryRun] 标记的占位响应，且不调用任何外部接口。"""
    adapter = LLMAdapter()
    # 强制 DryRun，避免本地 .env 残留
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None

    messages = [
        SystemMessage(content="你是代码助手"),
        HumanMessage(content="写个加法函数"),
    ]
    resp = adapter.chat(messages)
    assert isinstance(resp, ChatResponse)
    assert "[DryRun]" in resp.content
    assert resp.model == "dry-run"


@pytest.mark.asyncio
async def test_dry_run_achat_returns_marker() -> None:
    """异步 achat 在 DryRun 下也应返回 [DryRun] 占位。"""
    adapter = LLMAdapter()
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None

    messages = [HumanMessage(content="hello")]
    resp = await adapter.achat(messages)
    assert "[DryRun]" in resp.content


@pytest.mark.asyncio
async def test_dry_run_astream_yields_marker() -> None:
    """DryRun 流式调用应产出一条 [DryRun] 提示后正常结束。"""
    adapter = LLMAdapter()
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None

    chunks = [c async for c in adapter.astream([HumanMessage(content="x")])]
    assert len(chunks) == 1
    assert "[DryRun]" in chunks[0]


# ------------------------------------------------------------------ #
#  LLMUsage token 统计
# ------------------------------------------------------------------ #
def test_llm_usage_add_accumulates() -> None:
    """LLMUsage.add 应将 prompt/completion/total 三项分别累加。"""
    u = LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    u.add(LLMUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10))
    assert u.prompt_tokens == 15
    assert u.completion_tokens == 25
    assert u.total_tokens == 40


def test_llm_usage_default_zero() -> None:
    """无参构造的 LLMUsage 各项应为 0。"""
    u = LLMUsage()
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0
    assert u.total_tokens == 0


def test_adapter_reset_usage() -> None:
    """reset_usage 应将 usage 重置为空的 LLMUsage。"""
    adapter = LLMAdapter()
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None
    adapter.usage.add(LLMUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
    assert adapter.usage.total_tokens == 150
    adapter.reset_usage()
    assert adapter.usage.total_tokens == 0


# ------------------------------------------------------------------ #
#  _is_real_key 黑名单
# ------------------------------------------------------------------ #
def test_is_real_key_rejects_placeholder() -> None:
    """占位符 / 过短值不应被视为真实 key。"""
    for v in ["", "   ", "your_api_key_here", "xxx", "null", "none", "placeholder", "sk-xx"]:
        assert LLMAdapter._is_real_key(v) is False, f"应拒绝: {v!r}"


def test_is_real_key_accepts_normal_key() -> None:
    """长度 >=8 且不在黑名单的字符串应被视为真实 key。"""
    assert LLMAdapter._is_real_key("sk-abcdef1234567890") is True


# ------------------------------------------------------------------ #
#  to_messages 消息构造
# ------------------------------------------------------------------ #
def test_to_messages_with_system_history_user() -> None:
    """system/history/user 三段都应按顺序拼到 messages 列表。"""
    msgs = LLMAdapter.to_messages(
        system="sys",
        user="hi",
        history=[HumanMessage(content="prev")],
    )
    assert len(msgs) == 3
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)
    assert isinstance(msgs[2], HumanMessage)
    assert msgs[2].content == "hi"


def test_to_messages_empty_returns_empty_list() -> None:
    """全空入参应返回空列表。"""
    assert LLMAdapter.to_messages() == []


# ------------------------------------------------------------------ #
#  _extract_usage 解析
# ------------------------------------------------------------------ #
def test_extract_usage_with_langchain_metadata() -> None:
    """AIMessage 上的 usage_metadata 应被解析为 LLMUsage。"""
    msg = AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": 12,
            "output_tokens": 8,
            "total_tokens": 20,
        },
    )
    usage = LLMAdapter._extract_usage(msg)
    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 8
    assert usage.total_tokens == 20


def test_extract_usage_fallback_to_zero() -> None:
    """没有 usage_metadata 时应返回零值 LLMUsage，不抛异常。"""
    msg = AIMessage(content="x")
    usage = LLMAdapter._extract_usage(msg)
    assert usage.prompt_tokens == 0
    assert usage.total_tokens == 0


# ------------------------------------------------------------------ #
#  模型切换：DryRun 路径
# ------------------------------------------------------------------ #
def test_switch_model_dry_run_path() -> None:
    """DryRun 下切换模型只更新 _current_main_model，返回 mode=dry-run。"""
    adapter = LLMAdapter()
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None
    result = adapter.switch_model("qwen-plus", for_code=False)
    assert result["status"] == "ok"
    assert result["model"] == "qwen-plus"
    assert result["mode"] == "dry-run"
    assert adapter.current_model == "qwen-plus"


def test_switch_model_empty_name_raises() -> None:
    """空模型名应抛 ValueError。"""
    adapter = LLMAdapter()
    adapter.dry_run = True
    with pytest.raises(ValueError):
        adapter.switch_model("   ")


def test_switch_model_for_code_updates_code_model() -> None:
    """for_code=True 时应更新 code 模型字段。"""
    adapter = LLMAdapter()
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None
    adapter.switch_model("deepseek-chat", for_code=True)
    assert adapter.current_code_model == "deepseek-chat"


# ------------------------------------------------------------------ #
#  available_models
# ------------------------------------------------------------------ #
def test_available_models_returns_list_with_defaults() -> None:
    """available_models 应返回非空列表，且包含若干预设模型。"""
    models = LLMAdapter.available_models()
    assert isinstance(models, list)
    assert len(models) > 0
    # 当前默认应至少包含 qwen 系列中的一个
    assert any("qwen" in m.lower() for m in models)


# ------------------------------------------------------------------ #
#  chat_structured：DryRun 下返回 None + ChatResponse
# ------------------------------------------------------------------ #
def test_chat_structured_dry_run_returns_none() -> None:
    """DryRun 下 chat_structured 无法真正结构化输出，应返回 (None, ChatResponse)。"""
    from pydantic import BaseModel

    class _Out(BaseModel):
        name: str = ""

    adapter = LLMAdapter()
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None

    parsed, resp = adapter.chat_structured(
        [HumanMessage(content="hi")], _Out,
    )
    assert parsed is None
    assert isinstance(resp, ChatResponse)
    assert "[DryRun]" in resp.content


def test_chat_structured_rejects_non_pydantic_model() -> None:
    """response_model 非 Pydantic BaseModel 子类应抛 ValueError。"""
    adapter = LLMAdapter()
    adapter.dry_run = True
    adapter._main = None
    adapter._code = None

    with pytest.raises(ValueError):
        adapter.chat_structured([HumanMessage(content="hi")], dict)  # type: ignore[arg-type]
