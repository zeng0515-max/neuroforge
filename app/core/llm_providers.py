"""厂商注册表：模型名 → (base_url, api_key 环境变量名, 厂商名, 获取 key 的链接)。"""
from __future__ import annotations

import os
from typing import Dict

from app.core.config import settings

# ------------------------------------------------------------------ #
#  厂商注册表：模型名 → (base_url, api_key 环境变量名, 厂商名, 获取 key 的链接)
#  切换模型时自动匹配对应厂商的 API 地址和密钥
# ------------------------------------------------------------------ #
_PROVIDER_REGISTRY: Dict[str, tuple] = {
    # 阿里云 DashScope
    "qwen3.7-max":          ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "阿里云 DashScope", "https://bailian.console.aliyun.com/?apiKey=1#/api-key"),
    "qwen-max":             ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "阿里云 DashScope", "https://bailian.console.aliyun.com/?apiKey=1#/api-key"),
    "qwen-plus":            ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "阿里云 DashScope", "https://bailian.console.aliyun.com/?apiKey=1#/api-key"),
    "qwen-turbo":           ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "阿里云 DashScope", "https://bailian.console.aliyun.com/?apiKey=1#/api-key"),
    "qwen-coder-plus":      ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "阿里云 DashScope", "https://bailian.console.aliyun.com/?apiKey=1#/api-key"),
    # DeepSeek
    "deepseek-v3":          ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "DeepSeek", "https://platform.deepseek.com/api_keys"),
    "deepseek-r1":          ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "DeepSeek", "https://platform.deepseek.com/api_keys"),
    # 字节火山引擎（豆包）
    "doubao-seed-1-6-flash-250828": ("https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", "火山方舟", "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey"),
    "doubao-seed-1-6-250615":       ("https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", "火山方舟", "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey"),
    # OpenAI
    "gpt-4o-mini":          ("https://api.openai.com/v1", "OPENAI_API_KEY", "OpenAI", "https://platform.openai.com/api-keys"),
    "gpt-4o":               ("https://api.openai.com/v1", "OPENAI_API_KEY", "OpenAI", "https://platform.openai.com/api-keys"),
}


def _resolve_provider(model: str) -> tuple[str, str, str, str, bool]:
    """根据模型名返回 (base_url, api_key, 厂商名, 获取key链接, key是否fallback)。
    优先从环境变量读取对应厂商密钥；若未配置则 fallback 到主配置。
    """
    entry = _PROVIDER_REGISTRY.get(model)
    if entry is None:
        # 未知模型，用主配置
        return settings.llm.base_url, settings.llm.api_key, "", "", True

    base_url, env_key_name, provider_name, key_url = entry
    # 优先读专用 key，没有就 fallback 到主 LLM_API_KEY
    api_key = os.environ.get(env_key_name, "").strip()
    is_fallback = False
    if not api_key:
        api_key = settings.llm.api_key
        is_fallback = True
    return base_url, api_key, provider_name, key_url, is_fallback