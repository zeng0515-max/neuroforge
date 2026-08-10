"""语义缓存：基于 ChromaDB 的 LLM 请求-响应缓存。

通过向量相似度检索，对语义相同的请求直接返回缓存结果，
减少重复 LLM 调用，降低延迟与成本。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    import os as _os
    _CHROMA_DISABLED = _os.environ.get("CHROMA_DISABLED", "").lower() in ("1", "true", "yes")
    if not _CHROMA_DISABLED:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        _CHROMA_AVAILABLE = True
    else:
        _CHROMA_AVAILABLE = False
        ChromaSettings = None
except ImportError:
    _CHROMA_AVAILABLE = False
    ChromaSettings = None  # type: ignore


class _HashEmbeddingFunction:
    """Deterministic local embedding used by the semantic cache.

    ChromaDB's default embedding function tries to download an ONNX model on
    first query, which can block behind a restricted network. A hash-based
    vector keeps the cache fully offline while still allowing approximate
    similarity matching for near-duplicate prompts.
    """

    def __init__(self, dim: int = 768) -> None:
        self._dim = max(16, int(dim))

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for doc in input:
            vec = [0.0] * self._dim
            for token in str(doc).lower().split():
                digest = hashlib.md5(token.encode("utf-8")).hexdigest()
                idx = int(digest, 16) % self._dim
                vec[idx] += 1.0
            norm = math.sqrt(sum(x * x for x in vec))
            if norm <= 0:
                vec[0] = 1.0
            else:
                vec = [x / norm for x in vec]
            vectors.append(vec)
        return vectors


@dataclass
class CacheEntry:
    """单条缓存记录。"""
    query: str
    response: str
    model: str
    timestamp: float = field(default_factory=time.time)
    hit_count: int = 1


class SemanticCache:
    """基于 ChromaDB 的语义缓存。

    工作流程：
    1. 查询时：对输入做 embedding，向量相似度检索
    2. 匹配（相似度 >= threshold）则返回缓存结果
    3. 不匹配则调用 LLM 后，将 (query, response, model) 存入缓存

    使用方式：
        cache = SemanticCache()
        hit = cache.lookup("写一个快排算法")
        if hit:
            return hit
        response = llm.call(...)
        cache.store("写一个快排算法", response, "qwen-max")
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: Optional[str] = None,
        similarity_threshold: Optional[float] = None,
        embedding_dim: int = 768,
    ) -> None:
        self._config = settings.semantic_cache
        self._persist_dir = persist_dir or self._config.collection_name  # use collection name as default
        self._collection_name = collection_name or self._config.collection_name
        # Keep the offline embedding in its own collection so an existing
        # collection created with ChromaDB's default embedding never blocks.
        self._collection_name = f"{self._collection_name}_hash"
        self._threshold = similarity_threshold if similarity_threshold is not None else self._config.similarity_threshold
        self._max_entries = self._config.max_entries
        self._embedding_dim = embedding_dim
        self._embedding_fn = _HashEmbeddingFunction(dim=self._embedding_dim)
        self._lock = threading.Lock()
        self._collection = None
        self._client = None
        self._enabled = self._config.enabled
        self._initialized: bool = False
        # 惰性初始化：不在 __init__ 调用 _init_client，避免触发 ChromaDB
        # ONNX 模型下载（79MB）阻塞 import 和测试启动

    def _init_client(self) -> None:
        """初始化 ChromaDB 客户端和集合。"""
        if not _CHROMA_AVAILABLE:
            logger.warning("chromadb 未安装，语义缓存不可用")
            self._enabled = False
            return

        if not self._enabled:
            return

        try:
            persist_dir = str(self._persist_dir)
            self._client = chromadb.PersistentClient(
                path=persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            # 获取或创建集合（get_or_create_collection 是 chromadb 惯用法，
            # 避免 get_collection 在集合不存在时抛 ValueError 而 create_collection
            # 在部分版本下又二次失败，导致整个缓存被禁用）
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=self._embedding_fn,
            )
            logger.info(
                "语义缓存集合就绪：%s，条目数：%d",
                self._collection_name,
                self._collection.count(),
            )
        except Exception as e:
            logger.warning("语义缓存初始化失败（不影响主流程）：%s", e)
            self._enabled = False

    def _ensure_initialized(self) -> None:
        """惰性初始化：仅在首次实际使用时才初始化 ChromaDB。"""
        if not self._initialized:
            self._initialized = True
            self._init_client()

    # ------------------------------------------------------------------ #
    #  公共接口
    # ------------------------------------------------------------------ #
    def lookup(self, query: str, model: Optional[str] = None) -> Optional[str]:
        """查询缓存：返回匹配的响应内容，未命中返回 None。

        Args:
            query: 用户请求文本
            model: 可选的模型名过滤条件

        Returns:
            缓存命中时返回响应内容，未命中返回 None
        """
        self._ensure_initialized()
        if not self._enabled or self._collection is None:
            return None

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=3,
                include=["documents", "distances", "metadatas"],
            )

            if not results["distances"] or not results["distances"][0]:
                return None

            distances = results["distances"][0]
            documents = results["documents"][0]
            metadatas = results["metadatas"][0]

            for i, distance in enumerate(distances):
                # ChromaDB 余弦距离 0=完全相同, 2=完全不同
                # 转换为相似度: similarity = 1 - distance/2
                similarity = 1.0 - distance / 2.0
                if similarity < self._threshold:
                    continue

                doc_meta = metadatas[i] if metadatas else {}
                # 如果指定了模型，检查是否匹配
                if model and doc_meta.get("model") and doc_meta["model"] != model:
                    continue

                logger.info("语义缓存命中：similarity=%.4f, threshold=%.2f",
                            similarity, self._threshold)
                # 更新命中计数
                self._update_hit_count(doc_meta)
                return doc_meta.get("response") or documents[i]

            return None
        except Exception as e:
            logger.warning("语义缓存查询失败：%s", e)
            return None

    def store(self, query: str, response: str, model: str = "") -> None:
        """存储请求-响应对到缓存。

        Args:
            query: 用户请求文本
            response: LLM 响应内容
            model: 使用的模型名称
        """
        self._ensure_initialized()
        if not self._enabled or self._collection is None:
            return

        try:
            # 检查是否已有近似条目，避免重复存储
            existing = self._collection.query(
                query_texts=[query],
                n_results=1,
                include=["distances"],
            )
            if existing["distances"] and existing["distances"][0]:
                dist = existing["distances"][0][0]
                sim = 1.0 - dist / 2.0
                if sim >= self._threshold:
                    logger.debug("语义缓存：已有近似条目（similarity=%.4f），跳过存储", sim)
                    return

            # 生成唯一 ID
            unique_id = hashlib.md5(
                f"{query}|{model}|{time.time()}".encode()
            ).hexdigest()[:12]

            metadata = {
                "model": model,
                "response": response,
                "timestamp": str(time.time()),
                "hit_count": "1",
            }

            self._collection.add(
                documents=[query],
                metadatas=[metadata],
                ids=[unique_id],
            )

            # 检查条目数，超过上限时清理最旧的
            count = self._collection.count()
            if count > self._max_entries:
                self._prune_oldest()

            logger.debug("语义缓存已存储：model=%s, id=%s", model, unique_id)
        except Exception as e:
            logger.warning("语义缓存存储失败：%s", e)

    # ------------------------------------------------------------------ #
    #  管理
    # ------------------------------------------------------------------ #
    def clear(self) -> int:
        """清空缓存，返回删除的条目数。"""
        if not self._enabled or self._collection is None:
            return 0
        try:
            count = self._collection.count()
            self._client.delete_collection(self._collection_name)
            self._collection = self._client.create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("语义缓存已清空，删除 %d 条", count)
            return count
        except Exception as e:
            logger.warning("清空语义缓存失败：%s", e)
            return 0

    def stats(self) -> Dict[str, Any]:
        """返回缓存统计信息。"""
        self._ensure_initialized()
        if not self._enabled or self._collection is None:
            return {"enabled": False, "count": 0}
        try:
            return {
                "enabled": self._enabled,
                "count": self._collection.count(),
                "threshold": self._threshold,
                "collection": self._collection_name,
            }
        except Exception as e:
            return {"enabled": self._enabled, "count": 0, "error": str(e)}

    # ------------------------------------------------------------------ #
    #  内部
    # ------------------------------------------------------------------ #
    def _update_hit_count(self, metadata: Dict[str, Any]) -> None:
        """更新命中计数（仅打印日志，不写回 ChromaDB 以避免频繁写入）。"""
        current = int(metadata.get("hit_count", "1"))
        logger.debug("缓存条目命中计数：%d", current + 1)

    def _prune_oldest(self) -> None:
        """清理最旧的缓存条目。"""
        if self._collection is None:
            return
        try:
            # 获取所有数据按时间戳排序
            all_data = self._collection.get(include=["metadatas"])
            if not all_data["ids"]:
                return
            # 按时间戳排序，删除最旧的 10%
            meta_list = all_data["metadatas"]
            id_list = all_data["ids"]
            if not meta_list:
                return
            sorted_pairs = sorted(
                zip(id_list, meta_list),
                key=lambda x: float(x[1].get("timestamp", "0")),
            )
            to_delete = max(1, len(sorted_pairs) // 10)
            delete_ids = [p[0] for p in sorted_pairs[:to_delete]]
            self._collection.delete(ids=delete_ids)
            logger.info("语义缓存清理：删除 %d 条最旧条目", len(delete_ids))
        except Exception as e:
            logger.warning("语义缓存清理失败：%s", e)


# 全局单例
semantic_cache = SemanticCache()

__all__ = [
    "semantic_cache",
    "SemanticCache",
    "CacheEntry",
]
