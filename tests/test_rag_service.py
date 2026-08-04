"""RAG 服务单元测试。

重点测：
- _hash_embedding 维度与确定性
- _ChromaDefaultEmbedding 兜底实现
- _simple_chunks 切分逻辑
- RAGService.add_texts / query / count / clear（mock _collection）
- collection 维度变化时的自动重建
- RAGDocument 属性访问
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.tools.rag_service import (
    BaseEmbeddingProvider,
    RAGDocument,
    RAGService,
    _ChromaDefaultEmbedding,
    _hash_embedding,
    _select_embedding_provider,
    _simple_chunks,
)


# ------------------------------------------------------------------ #
#  _hash_embedding
# ------------------------------------------------------------------ #
def test_hash_embedding_correct_dimension() -> None:
    """hash embedding 长度应等于 dim 参数。"""
    vec = _hash_embedding("hello", dim=64)
    assert len(vec) == 64
    assert all(-1.0 <= v <= 1.0 for v in vec)


def test_hash_embedding_deterministic() -> None:
    """相同输入应产出相同向量。"""
    a = _hash_embedding("same text", dim=128)
    b = _hash_embedding("same text", dim=128)
    assert a == b


def test_hash_embedding_different_inputs_differ() -> None:
    """不同输入应产出不同向量。"""
    a = _hash_embedding("foo", dim=128)
    b = _hash_embedding("bar", dim=128)
    assert a != b


def test_hash_embedding_supports_large_dim() -> None:
    """dim=1536（DashScope v2 默认）也应正常产出。"""
    vec = _hash_embedding("x", dim=1536)
    assert len(vec) == 1536


# ------------------------------------------------------------------ #
#  _ChromaDefaultEmbedding
# ------------------------------------------------------------------ #
def test_chroma_default_embedding_dim_matches_settings() -> None:
    """_ChromaDefaultEmbedding 维度应与 settings.chroma.embedding_dim 一致。"""
    from app.core.config import settings
    emb = _ChromaDefaultEmbedding()
    assert emb._dim == settings.chroma.embedding_dim
    vecs = emb.embed_texts(["a", "b"])
    assert len(vecs) == 2
    assert all(len(v) == emb._dim for v in vecs)


def test_chroma_default_embedding_query_returns_single_vec() -> None:
    """embed_query 应返回单条向量（embed_texts 的第一条）。"""
    emb = _ChromaDefaultEmbedding()
    v = emb.embed_query("hello")
    assert isinstance(v, list)
    assert len(v) == emb._dim


# ------------------------------------------------------------------ #
#  _select_embedding_provider
# ------------------------------------------------------------------ #
def test_select_provider_hash_returns_chroma_default() -> None:
    """model='hash' 应返回 _ChromaDefaultEmbedding。"""
    with patch("app.tools.rag_service.settings") as mock_s:
        mock_s.chroma.embedding_model = "hash"
        mock_s.chroma.embedding_dim = 64
        provider = _select_embedding_provider()
        assert isinstance(provider, _ChromaDefaultEmbedding)


def test_select_provider_empty_returns_chroma_default() -> None:
    """空 model 应回退 hash。"""
    with patch("app.tools.rag_service.settings") as mock_s:
        mock_s.chroma.embedding_model = ""
        mock_s.chroma.embedding_dim = 64
        provider = _select_embedding_provider()
        assert isinstance(provider, _ChromaDefaultEmbedding)


# ------------------------------------------------------------------ #
#  _simple_chunks
# ------------------------------------------------------------------ #
def test_simple_chunks_empty_returns_empty() -> None:
    """空文本应返回空列表。"""
    assert _simple_chunks("", chunk_size=100, chunk_overlap=10) == []


def test_simple_chunks_single_paragraph() -> None:
    """单段短文本应作为单个 chunk。"""
    text = "hello world"
    chunks = _simple_chunks(text, chunk_size=100, chunk_overlap=0)
    assert len(chunks) == 1
    assert "hello" in chunks[0]


def test_simple_chunks_multiple_paragraphs_merge() -> None:
    """多个短段落应合并到接近 chunk_size 的 chunk。"""
    text = "para1\npara2\npara3"
    chunks = _simple_chunks(text, chunk_size=100, chunk_overlap=0)
    assert len(chunks) >= 1
    # 三个段落都应在某个 chunk 中
    combined = "\n".join(chunks)
    assert "para1" in combined
    assert "para3" in combined


# ------------------------------------------------------------------ #
#  RAGDocument 属性
# ------------------------------------------------------------------ #
def test_rag_document_score_and_source_from_metadata() -> None:
    """score/source/file_path 应从 metadata 推导。"""
    doc = RAGDocument(
        id="d1",
        content="hello",
        metadata={"score": 0.85, "source": "wiki", "file_path": "/tmp/a.py"},
    )
    assert doc.score == 0.85
    assert doc.source == "wiki"
    assert doc.file_path == "/tmp/a.py"


def test_rag_document_default_score_zero() -> None:
    """无 score 字段时默认 0.0。"""
    doc = RAGDocument(id="d2", content="x", metadata={})
    assert doc.score == 0.0
    assert doc.source == ""


# ------------------------------------------------------------------ #
#  RAGService：mock _collection 测 add/query/count/clear
# ------------------------------------------------------------------ #
def _make_rag_with_mock_collection() -> RAGService:
    """构造一个 RAGService，但 _collection 与 embedding 都被替换为 mock，不触碰真实 chromadb。"""
    svc = RAGService.__new__(RAGService)  # 跳过 __init__ 的真实 chroma 初始化
    svc.embedding = _ChromaDefaultEmbedding()
    svc._chroma_client = None
    svc._collection = MagicMock()
    return svc


def test_rag_add_texts_returns_count() -> None:
    """add_texts 应返回入库条数，并调用 collection.add。"""
    svc = _make_rag_with_mock_collection()
    n = svc.add_texts(texts=["a", "b", "c"], source="test")
    assert n == 3
    svc._collection.add.assert_called_once()
    call_kwargs = svc._collection.add.call_args.kwargs
    assert len(call_kwargs["ids"]) == 3
    assert len(call_kwargs["documents"]) == 3
    assert len(call_kwargs["embeddings"]) == 3


def test_rag_add_texts_empty_returns_zero() -> None:
    """空 texts 应直接返回 0，不调用 collection。"""
    svc = _make_rag_with_mock_collection()
    assert svc.add_texts(texts=[]) == 0
    svc._collection.add.assert_not_called()


def test_rag_query_returns_documents() -> None:
    """query 应把 collection.query 结果转成 RAGDocument 列表。"""
    svc = _make_rag_with_mock_collection()
    svc._collection.query.return_value = {
        "ids": [["d1", "d2"]],
        "documents": [["hello", "world"]],
        "metadatas": [[{"source": "a"}, {"source": "b"}]],
        "distances": [[0.1, 0.5]],
    }
    docs = svc.query("hello", top_k=2)
    assert len(docs) == 2
    assert docs[0].id == "d1"
    assert docs[0].content == "hello"
    # distance=0.1 → score=1.0-0.1=0.9
    assert abs(docs[0].score - 0.9) < 1e-6
    assert docs[1].source == "b"


def test_rag_query_empty_text_returns_empty() -> None:
    """空 query_text 应返回空列表。"""
    svc = _make_rag_with_mock_collection()
    assert svc.query("") == []
    svc._collection.query.assert_not_called()


def test_rag_count_returns_int() -> None:
    """count 应返回 _collection.count() 的整数值。"""
    svc = _make_rag_with_mock_collection()
    svc._collection.count.return_value = 42
    assert svc.count() == 42


def test_rag_clear_calls_delete_with_all_ids() -> None:
    """clear 应取出所有 id 并调用 delete。"""
    svc = _make_rag_with_mock_collection()
    svc._collection.count.return_value = 2
    svc._collection.get.return_value = {"ids": ["d1", "d2"]}
    svc.clear()
    svc._collection.delete.assert_called_once_with(ids=["d1", "d2"])


def test_rag_clear_empty_does_not_delete() -> None:
    """count=0 时 clear 不应调用 delete。"""
    svc = _make_rag_with_mock_collection()
    svc._collection.count.return_value = 0
    svc.clear()
    svc._collection.delete.assert_not_called()


def test_rag_to_context_string_formats_docs() -> None:
    """to_context_string 应把 docs 拼成带 score/src/file 标签的上下文文本。"""
    svc = _make_rag_with_mock_collection()
    docs = [
        RAGDocument(id="1", content="hello", metadata={"score": 0.9, "source": "s1"}),
        RAGDocument(id="2", content="world", metadata={"score": 0.5, "source": "s2"}),
    ]
    text = svc.to_context_string(docs)
    assert "hello" in text
    assert "world" in text
    assert "score=0.900" in text


def test_rag_to_context_string_empty_returns_empty() -> None:
    """空列表应返回空字符串。"""
    svc = _make_rag_with_mock_collection()
    assert svc.to_context_string([]) == ""


# ------------------------------------------------------------------ #
#  collection 自动重建（mock chromadb client）
# ------------------------------------------------------------------ #
def test_collection_rebuild_on_dim_change() -> None:
    """检测到 embedding_dim 变化时应删除旧 collection 并 get_or_create 新的。"""
    svc = RAGService.__new__(RAGService)
    svc.embedding = _ChromaDefaultEmbedding()

    mock_client = MagicMock()
    existing_coll = MagicMock()
    existing_coll.metadata = {"embedding_dim": "512", "embedding_model": "old"}
    mock_client.get_collection.return_value = existing_coll
    new_coll = MagicMock()
    new_coll.count.return_value = 0
    mock_client.get_or_create_collection.return_value = new_coll

    with patch("app.tools.rag_service.settings") as mock_s, \
            patch("chromadb.PersistentClient", return_value=mock_client):
        mock_s.chroma.persist_dir = "/tmp/test_chroma_xyz"
        mock_s.chroma.collection_name = "test_coll"
        mock_s.chroma.embedding_model = "hash"
        mock_s.chroma.embedding_dim = 1024  # 与旧 512 不同 → 触发重建
        svc._init_chroma()

    # 旧 collection 应被删除
    mock_client.delete_collection.assert_called_once_with(name="test_coll")
    # 新 collection 应被 get_or_create
    mock_client.get_or_create_collection.assert_called_once()
    assert svc._collection is new_coll
