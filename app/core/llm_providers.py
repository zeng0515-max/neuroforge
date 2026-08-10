"""Provider registry: model name -> OpenAI-compatible endpoint + API key env."""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from app.core.config import settings

# Tuple shape: (base_url, api_key_env, provider_name, key_url)
ProviderSpec = Tuple[str, str, str, str]

_DASHSCOPE: ProviderSpec = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "DASHSCOPE_API_KEY",
    "阿里云 DashScope",
    "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
)

_DEEPSEEK: ProviderSpec = (
    "https://api.deepseek.com/v1",
    "DEEPSEEK_API_KEY",
    "DeepSeek",
    "https://platform.deepseek.com/api_keys",
)

_ARK: ProviderSpec = (
    "https://ark.cn-beijing.volces.com/api/v3",
    "ARK_API_KEY",
    "火山方舟",
    "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
)

_OPENAI: ProviderSpec = (
    "https://api.openai.com/v1",
    "OPENAI_API_KEY",
    "OpenAI",
    "https://platform.openai.com/api-keys",
)

_GEMINI: ProviderSpec = (
    "https://generativelanguage.googleapis.com/v1beta/openai/",
    "GEMINI_API_KEY",
    "Google Gemini",
    "https://aistudio.google.com/app/apikey",
)

_PROVIDER_REGISTRY: Dict[str, ProviderSpec] = {
    # Aliyun DashScope
    "qwen-max": _DASHSCOPE,
    "qwen-plus": _DASHSCOPE,
    "qwen-turbo": _DASHSCOPE,
    "qwen-coder-plus": _DASHSCOPE,
    "qwen3-max": _DASHSCOPE,
    "qwen3-coder-plus": _DASHSCOPE,
    # DeepSeek
    "deepseek-chat": _DEEPSEEK,
    "deepseek-reasoner": _DEEPSEEK,
    "deepseek-v4-flash": _DEEPSEEK,
    "deepseek-v4-pro": _DEEPSEEK,
    # Volcano Ark / Doubao
    "doubao-seed-1-6-flash-250828": _ARK,
    "doubao-seed-1-6-250615": _ARK,
    # OpenAI
    "gpt-4o": _OPENAI,
    "gpt-4o-mini": _OPENAI,
    "gpt-4.1": _OPENAI,
    "gpt-4.1-mini": _OPENAI,
    "o4-mini": _OPENAI,
    # Google Gemini
    "gemini-2.0-flash": _GEMINI,
    "gemini-2.5-flash": _GEMINI,
    "gemini-2.5-pro": _GEMINI,
}

# New model versions are routed by prefix, so the registry does not need to
# list every released model name. Custom/unknown models still fall back to the
# main LLM_BASE_URL + LLM_API_KEY configured in .env.
_PROVIDER_PREFIX_RULES: Tuple[Tuple[Tuple[str, ...], ProviderSpec], ...] = (
    (("qwen",), _DASHSCOPE),
    (("deepseek",), _DEEPSEEK),
    (("doubao", "seed-", "ep-"), _ARK),
    (("gpt-", "o1", "o3", "o4", "chatgpt-"), _OPENAI),
    (("gemini",), _GEMINI),
)


def _provider_for_model(model: str) -> Optional[ProviderSpec]:
    exact = _PROVIDER_REGISTRY.get(model)
    if exact is not None:
        return exact
    normalized = model.lower()
    for prefixes, provider in _PROVIDER_PREFIX_RULES:
        if any(normalized.startswith(prefix.lower()) for prefix in prefixes):
            return provider
    return None


def _resolve_provider(model: str) -> tuple[str, str, str, str, bool]:
    """Return (base_url, api_key, provider_name, key_url, is_fallback)."""
    entry = _provider_for_model(model)
    if entry is None:
        # Unknown model: use the main config so custom OpenAI-compatible
        # endpoints keep working without code changes.
        return settings.llm.base_url, settings.llm.api_key, "", "", True

    base_url, env_key_name, provider_name, key_url = entry
    api_key = os.environ.get(env_key_name, "").strip()
    is_fallback = False
    if not api_key:
        api_key = settings.llm.api_key
        is_fallback = True
    return base_url, api_key, provider_name, key_url, is_fallback


__all__ = [
    "_PROVIDER_REGISTRY",
    "_PROVIDER_PREFIX_RULES",
    "_resolve_provider",
    "_provider_for_model",
]
