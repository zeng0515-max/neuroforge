"""Tests for multi-provider model resolution and the local semantic cache."""
from __future__ import annotations

import tempfile

from app.core.config import settings
from app.core.llm_adapter import LLMAdapter
from app.core.llm_providers import _PROVIDER_REGISTRY, _resolve_provider
from app.core.semantic_cache import SemanticCache, _HashEmbeddingFunction


def test_registry_routes_exact_models() -> None:
    base, _key, provider, _key_url, _fallback = _resolve_provider("deepseek-chat")
    assert base == "https://api.deepseek.com/v1"
    assert provider == "DeepSeek"


def test_prefix_routes_new_model_versions() -> None:
    expected = {
        "qwen3-max": "阿里云 DashScope",
        "deepseek-v4-flash": "DeepSeek",
        "gpt-5": "OpenAI",
        "gemini-3-pro": "Google Gemini",
        "ep-20260801": "火山方舟",
    }
    for model, provider in expected.items():
        _base, _key, resolved, _key_url, _fallback = _resolve_provider(model)
        assert resolved == provider, model


def test_unknown_model_uses_main_config() -> None:
    base, _key, provider, _key_url, fallback = _resolve_provider("my-custom-model")
    assert provider == ""
    assert fallback is True
    assert base == settings.llm.base_url


def test_available_models_drops_invalid_names() -> None:
    models = LLMAdapter.available_models()
    assert "deepseek-chat" in models
    assert "qwen3.7-max" not in models
    assert "deepseek-v3" not in models
    assert settings.llm.model in models
    assert all(m in models for m in _PROVIDER_REGISTRY)
    assert len(models) >= len(_PROVIDER_REGISTRY)


def test_hash_embedding_is_deterministic_and_offline() -> None:
    fn = _HashEmbeddingFunction(dim=64)
    first = fn(["hello world"])
    second = fn(["hello world"])
    assert first == second
    assert len(first[0]) == 64
    assert abs(sum(x * x for x in first[0]) - 1.0) < 1e-9


def test_semantic_cache_roundtrip_with_local_embedding() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        cache = SemanticCache(
            persist_dir=tmp,
            collection_name="test_semantic_cache",
            embedding_dim=64,
        )
        cache._enabled = True
        assert cache.lookup("hello world") is None
        cache.store("hello world", "cached-response", model="deepseek-chat")
        assert cache.lookup("hello world") == "cached-response"
