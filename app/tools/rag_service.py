from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  数据结构
# ------------------------------------------------------------------ #

@dataclass
class RAGDocument:
    """向量库中存储的一条文档。"""

    id: str
    content: str
    metadata: Dict[str, Any]

    @property
    def score(self) -> float:
        return float(self.metadata.get("score", 0.0))

    @property
    def source(self) -> str:
        return str(self.metadata.get("source", ""))

    @property
    def file_path(self) -> str:
        return str(self.metadata.get("file_path", "") or self.metadata.get("path", ""))


# ------------------------------------------------------------------ #
#  Embedding Provider（可拔插）
# ------------------------------------------------------------------ #

class BaseEmbeddingProvider:
    """嵌入向量提供方的抽象接口。

    embed_texts 的返回值约定：
    - _ChromaDefaultEmbedding 返回 List[List[float]]（hash 兜底，无失败概念）
    - DashScopeEmbeddingProvider 返回 Tuple[List[List[float]], int]（向量列表 + 失败条数）
    embed_query 兼容两种返回类型。
    """

    def embed_texts(self, texts: List[str]):
        raise NotImplementedError

    def embed_query(self, text: str) -> List[float]:
        result = self.embed_texts([text])
        # 兼容 DashScopeEmbeddingProvider 返回 (embeddings, failed_count) 元组
        if isinstance(result, tuple):
            embeddings: List[List[float]] = result[0]
        else:
            embeddings = result
        return embeddings[0] if embeddings else []


class _ChromaDefaultEmbedding(BaseEmbeddingProvider):
    """
    开发态兜底 embedding：使用 hash 向量，零依赖、不触发模型下载。

    说明：
    - ChromaDB 默认 all-MiniLM-L6-v2 首次调用会同步下载 79MB ONNX 模型，
      在离线/慢网环境会阻塞 RAG 检索进而卡住整个流水线。
    - 本类改用 hash 兜底，保证系统始终可运行；语义检索质量较低。
    - 生产环境应使用 DashScopeEmbeddingProvider 接入云端真实语义 embedding。
    """

    def __init__(self) -> None:
        # 兜底向量维度与配置保持一致，避免维度不匹配导致 ChromaDB 报错
        self._dim = settings.chroma.embedding_dim
        logger.warning("RAG 嵌入函数：hash 兜底（开发态，无语义检索能力，dim=%d）", self._dim)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [_hash_embedding(t, self._dim) for t in texts]


class DashScopeEmbeddingProvider(BaseEmbeddingProvider):
    """基于 DashScope OpenAI 兼容协议的真实语义 Embedding Provider。

    设计要点：
    - 复用 LLM_API_KEY / LLM_BASE_URL，避免单独配置 embedding 凭据
    - 通过 openai SDK 调用 /v1/embeddings 接口（DashScope 已兼容）
    - 单次请求最多 25 条文本（DashScope 限制），自动分批
    - Bug P7 修复：批次失败时不再填充零向量（零向量在 cosine 空间相似度未定义，会污染检索），
      改为跳过该批 + 日志告警，返回 (embeddings, failed_count) 让调用方只入库成功的部分
    - 维度由 settings.chroma.embedding_dim 决定（v2=1536, v3=1024）
    """

    MAX_BATCH = 25

    def __init__(self) -> None:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("openai SDK 未安装，无法接入 DashScope Embedding") from e

        api_key = settings.llm.api_key
        base_url = settings.llm.base_url
        if not api_key or not base_url:
            raise RuntimeError("LLM_API_KEY / LLM_BASE_URL 未配置，无法初始化 DashScope Embedding")

        self.model = settings.chroma.embedding_model
        self.dim = settings.chroma.embedding_dim
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        logger.info(
            "RAG 嵌入函数：DashScope %s (dim=%d, base_url=%s)",
            self.model, self.dim, base_url,
        )

    def embed_texts(self, texts: List[str]) -> Tuple[List[List[float]], int]:
        """批量生成 embedding 向量。

        返回 (embeddings, failed_count)：
        - embeddings: 成功生成的向量列表（长度 <= len(texts)）
        - failed_count: 因 API 调用失败而跳过的条数

        Bug P7：批次失败时不填零向量，直接跳过，避免零向量污染检索。
        """
        if not texts:
            return [], 0
        results: List[List[float]] = []
        failed_count = 0
        for i in range(0, len(texts), self.MAX_BATCH):
            batch = texts[i:i + self.MAX_BATCH]
            try:
                resp = self._client.embeddings.create(model=self.model, input=batch)
                # DashScope 返回的 index 不一定有序，按 index 排序保证与输入对齐
                sorted_data = sorted(resp.data, key=lambda x: x.index)
                for d in sorted_data:
                    results.append(list(d.embedding))
            except Exception as e:
                logger.warning(
                    "Embedding 批次失败，跳过该批 %d 条: %s", len(batch), e,
                )
                failed_count += len(batch)
                # Bug P7：不填零向量，继续下一批
                continue
        return results, failed_count


def _select_embedding_provider() -> BaseEmbeddingProvider:
    """根据 settings.chroma.embedding_model 选择合适的 Embedding Provider。

    选择策略：
    - "hash" 或空 → hash 兜底（开发态，无语义）
    - text-embedding-v* 系列 → DashScopeEmbeddingProvider
    - 初始化失败时自动回退 hash 兜底，保证系统始终可启动
    """
    model = (settings.chroma.embedding_model or "").strip().lower()

    if not model or model == "hash":
        return _ChromaDefaultEmbedding()

    # 已知 DashScope 模型或以 text-embedding 开头的模型名
    dashscope_prefixes = ("text-embedding-",)
    if model.startswith(dashscope_prefixes):
        try:
            return DashScopeEmbeddingProvider()
        except Exception as e:
            logger.warning(
                "DashScope Embedding 初始化失败，回退到 hash 兜底（RAG 仍可用但无语义检索）：%s", e,
            )
            return _ChromaDefaultEmbedding()

    # 其他未知模型：尝试用 DashScope 协议，失败回退
    try:
        return DashScopeEmbeddingProvider()
    except Exception as e:
        logger.warning("未知 embedding model '%s'，回退 hash 兜底：%s", model, e)
        return _ChromaDefaultEmbedding()


def _hash_embedding(text: str, dim: int = 1536) -> List[float]:
    """极简非语义化 embedding，仅用于本地无 API Key 时的开发测试。

    通过 SHA256 把文本哈希成 dim 维向量，仅保证可入库可检索，无语义相关性。
    """
    import hashlib
    vec = [0.0] * dim
    bs = text.encode("utf-8")
    for i in range(max(1, dim // 16)):
        digest = hashlib.sha256(bs + i.to_bytes(4, "little")).digest()
        for j in range(min(16, dim - i * 16)):
            vec[i * 16 + j] = (digest[j] / 127.5) - 1.0
    return vec


# ------------------------------------------------------------------ #
#  RAG 主服务
# ------------------------------------------------------------------ #

class RAGService:
    """
    基于 ChromaDB 的 RAG 检索服务：
    - 持久化存储在 settings.chroma.persist_dir
    - 提供 add / query / upsert / delete / list 标准操作
    - add 文本时自动简单切分（按段落）
    """

    def __init__(self) -> None:
        # 通过工厂选择 Embedding Provider：真实 DashScope 优先，失败回退 hash
        self.embedding: BaseEmbeddingProvider = _select_embedding_provider()
        self._chroma_client: Optional[Any] = None
        self._collection: Optional[Any] = None
        self._init_chroma()

    def _init_chroma(self) -> None:
        try:
            import chromadb  # type: ignore
        except ImportError:
            logger.warning("ChromaDB 未安装，RAG 服务将进入空实现（不报错但无效果）。")
            return
        try:
            persist_dir = Path(settings.chroma.persist_dir).resolve()
            persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(persist_dir))

            # 把 embedding 模型与维度写进 collection metadata，
            # 用于启动时检测维度/模型变化并自动重建（防止维度不匹配导致 query 报错）
            current_meta = {
                "hnsw:space": "cosine",
                "embedding_model": settings.chroma.embedding_model,
                "embedding_dim": str(settings.chroma.embedding_dim),
            }

            # 检测已存在的 collection 是否与当前 embedding 配置兼容
            try:
                existing = client.get_collection(name=settings.chroma.collection_name)
                old_meta = dict(existing.metadata or {})
                old_dim = old_meta.get("embedding_dim", "")
                old_model = old_meta.get("embedding_model", "")
                need_rebuild = False
                if old_dim and old_dim != current_meta["embedding_dim"]:
                    logger.warning(
                        "检测到 embedding 维度变化：%s → %s，自动删除旧 collection 重建（旧向量数据将丢失，需重新灌入）",
                        old_dim, current_meta["embedding_dim"],
                    )
                    need_rebuild = True
                elif old_model and old_model != current_meta["embedding_model"]:
                    logger.warning(
                        "检测到 embedding 模型变化：%s → %s，自动删除旧 collection 重建",
                        old_model, current_meta["embedding_model"],
                    )
                    need_rebuild = True
                if need_rebuild:
                    client.delete_collection(name=settings.chroma.collection_name)
            except Exception:
                # collection 不存在是正常情况，直接进入 get_or_create
                pass

            collection = client.get_or_create_collection(
                name=settings.chroma.collection_name,
                metadata=current_meta,
            )
            self._chroma_client = client
            self._collection = collection
            logger.info(
                "ChromaDB 就绪：path=%s  collection=%s  count=%d  embedding=%s(dim=%s)",
                persist_dir, settings.chroma.collection_name, collection.count(),
                settings.chroma.embedding_model, settings.chroma.embedding_dim,
            )
        except Exception as e:
            logger.exception("初始化 ChromaDB 失败：%s", e)

    # ------------------------------------------------------------------ #
    #  公共 API
    # ------------------------------------------------------------------ #
    def is_available(self) -> bool:
        return self._collection is not None

    def add_texts(
        self,
        *,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
        source: str = "user",
    ) -> int:
        """批量入库。返回实际入库条数。"""
        if not self.is_available():
            return 0
        if not texts:
            return 0
        metadatas = metadatas or [{"source": source} for _ in texts]
        if ids is None:
            import uuid
            ids = [f"doc_{uuid.uuid4().hex}" for _ in texts]
        try:
            result = self.embedding.embed_texts(texts)
            # Bug P7：兼容 DashScopeEmbeddingProvider 返回 (embeddings, failed_count) 元组
            if isinstance(result, tuple):
                embeddings, failed_count = result
            else:
                embeddings, failed_count = result, 0

            if failed_count > 0:
                logger.warning(
                    "RAG add_texts: %d/%d 条文本 embedding 失败，仅入库成功部分",
                    failed_count, len(texts),
                )

            if not embeddings:
                logger.warning("RAG add_texts: 无可用 embedding，跳过入库")
                return 0

            # embeddings 长度可能 < texts 长度（部分批次失败），只入库成功的部分
            success_count = len(embeddings)
            if success_count < len(texts):
                texts = texts[:success_count]
                ids = ids[:success_count]
                metadatas = metadatas[:success_count]

            self._collection.add(  # type: ignore[union-attr]
                ids=ids,
                documents=texts,
                metadatas=[{**({"source": source}), **m} for m in metadatas],
                embeddings=embeddings,
            )
            return success_count
        except Exception as e:
            logger.exception("RAG add_texts 失败：%s", e)
            return 0

    def add_files(
        self,
        *,
        file_paths: List[str],
        chunk_size: int = 600,
        chunk_overlap: int = 100,
        source: str = "file",
    ) -> int:
        """读取本地文件，做简单 chunk 切片后入库。"""
        chunks: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        for fp in file_paths:
            try:
                p = Path(fp).resolve()
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("读取文件失败 %s：%s", fp, e)
                continue
            pieces = _simple_chunks(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            for i, piece in enumerate(pieces):
                chunks.append(piece)
                metadatas.append({
                    "source": source,
                    "file": str(p),
                    "chunk_idx": i,
                    "chunks_total": len(pieces),
                })
        return self.add_texts(texts=chunks, metadatas=metadatas, source=source)

    def query(
        self,
        query_text: str,
        *,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[RAGDocument]:
        if not self.is_available() or not query_text:
            return []
        try:
            qvec = self.embedding.embed_query(query_text)
            results = self._collection.query(  # type: ignore[union-attr]
                query_embeddings=[qvec],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.exception("RAG query 失败：%s", e)
            return []
        docs: List[RAGDocument] = []
        ids = results.get("ids", [[]])[0]
        contents = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0] or [0.0] * len(ids)
        for i in range(len(ids)):
            meta = dict(metas[i]) if i < len(metas) else {}
            if i < len(dists):
                try:
                    score = 1.0 - float(dists[i])  # distance -> similarity
                except Exception:
                    score = 0.0
                meta.setdefault("score", score)
            docs.append(RAGDocument(id=ids[i], content=contents[i], metadata=meta))
        return docs

    def count(self) -> int:
        if not self.is_available():
            return 0
        try:
            return int(self._collection.count())  # type: ignore[union-attr]
        except Exception:
            return 0

    def delete_ids(self, ids: List[str]) -> None:
        if not self.is_available() or not ids:
            return
        try:
            self._collection.delete(ids=ids)  # type: ignore[union-attr]
        except Exception as e:
            logger.exception("RAG delete_ids 失败：%s", e)

    def clear(self) -> None:
        if not self.is_available():
            return
        try:
            count = self.count()
            if count <= 0:
                return
            r = self._collection.get(limit=count)  # type: ignore[union-attr]
            ids = r.get("ids", [])
            if ids:
                self._collection.delete(ids=ids)  # type: ignore[union-attr]
        except Exception as e:
            logger.exception("RAG clear 失败：%s", e)

    def to_context_string(self, docs: List[RAGDocument]) -> str:
        """把检索结果拼成 prompt 友好的 context 文本。"""
        if not docs:
            return ""
        parts = []
        for i, d in enumerate(docs, 1):
            src = d.metadata.get("source", "")
            fp = d.metadata.get("file", "")
            score = d.score
            tag = f"[#{i} score={score:.3f} src={src or '?'} file={fp or '-'}]"
            parts.append(f"{tag}\n{d.content}")
        return "\n\n".join(parts)


# ------------------------------------------------------------------ #
#  辅助：简单文本切分
# ------------------------------------------------------------------ #

def _simple_chunks(text: str, *, chunk_size: int, chunk_overlap: int) -> List[str]:
    """按段落先切，再按字符长度合并，保持 chunk_size 左右。"""
    if not text:
        return []
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    if not paragraphs:
        paragraphs = [text]
    chunks: List[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 1 <= chunk_size:
            buf = (buf + "\n" + p).strip() if buf else p
        else:
            if buf:
                chunks.append(buf)
            # 带上 overlap：末尾 overlap 长度 + 新段
            overlap = buf[-chunk_overlap:] if chunk_overlap > 0 and len(buf) > chunk_overlap else ""
            buf = (overlap + "\n" + p).strip() if overlap else p
    if buf:
        chunks.append(buf)
    return chunks


# ------------------------------------------------------------------ #
#  全局单例
# ------------------------------------------------------------------ #

rag = RAGService()

__all__ = [
    "RAGService",
    "RAGDocument",
    "BaseEmbeddingProvider",
    "DashScopeEmbeddingProvider",
    "rag",
]
