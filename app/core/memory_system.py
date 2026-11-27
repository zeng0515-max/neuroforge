from __future__ import annotations

import logging
import time
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from app.core.config import settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  类型定义
# ------------------------------------------------------------------ #

MemoryLevel = str
LEVEL_WORKING: MemoryLevel = "working"
LEVEL_EPISODIC: MemoryLevel = "episodic"
LEVEL_SEMANTIC: MemoryLevel = "semantic"
LEVEL_SHORT_TERM: MemoryLevel = "short_term"
LEVEL_LONG_TERM: MemoryLevel = "long_term"

VALID_LEVELS = (LEVEL_WORKING, LEVEL_EPISODIC, LEVEL_SEMANTIC,
                LEVEL_SHORT_TERM, LEVEL_LONG_TERM)

EventFilter = Callable[[Dict[str, Any]], bool]


@dataclass
class EventRecord:
    event_type: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    event_id: str = ""
    what_happened: str = ""
    why: str = ""
    outcome: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            import uuid
            self.event_id = f"evt_{uuid.uuid4().hex[:12]}"


# ------------------------------------------------------------------ #
#  ShortTermMemory —— 会话级短期记忆（滑动窗口 + Token 感知）
# ------------------------------------------------------------------ #

@dataclass
class ChatMessage:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    token_estimate: int = 0

    def __post_init__(self) -> None:
        if not self.token_estimate:
            self.token_estimate = max(1, len(self.content) // 4)


class ShortTermMemory:
    """会话级短期记忆：滑动窗口 + Token 感知裁剪 + 自动摘要。

    维护最近 N 条消息，当 Token 总量超过阈值时自动裁剪最旧消息，
    并将裁剪消息摘要后存入 EpisodicMemory。
    """

    def __init__(
        self,
        max_messages: int = 50,
        max_tokens: int = 8000,
        episodic_memory: Optional["EpisodicMemory"] = None,
    ) -> None:
        self._max_messages = max(1, max_messages)
        self._max_tokens = max(100, max_tokens)
        self._messages: List[ChatMessage] = []
        self._episodic = episodic_memory
        self._lock = threading.Lock()
        self._total_tokens: int = 0
        self._trim_count: int = 0
        self._summarize_count: int = 0
        self._logger = logging.getLogger(f"{__name__}.ShortTermMemory")

    @property
    def max_messages(self) -> int:
        return self._max_messages

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def add_message(self, role: str, content: str) -> ChatMessage:
        msg = ChatMessage(role=role, content=content)
        with self._lock:
            self._messages.append(msg)
            self._total_tokens += msg.token_estimate
            self._do_trim()
        return msg

    def get_messages(self, last_n: Optional[int] = None) -> List[ChatMessage]:
        with self._lock:
            if last_n is not None:
                return list(self._messages[-last_n:])
            return list(self._messages)

    def get_context_window(self) -> str:
        with self._lock:
            return "\n".join(
                f"[{m.role}]: {m.content}" for m in self._messages
            )

    def clear(self) -> None:
        with self._lock:
            count = len(self._messages)
            self._messages.clear()
            self._total_tokens = 0
        self._logger.info("短期记忆已清空: %d 条消息", count)

    def _do_trim(self) -> None:
        while (len(self._messages) > self._max_messages
               or self._total_tokens > self._max_tokens):
            if not self._messages:
                break
            evicted = self._messages.pop(0)
            self._total_tokens = max(0, self._total_tokens - evicted.token_estimate)
            self._trim_count += 1
        if self._trim_count > 0 and self._episodic:
            self._auto_summarize()

    def _auto_summarize(self) -> None:
        if not self._messages:
            return
        parts = [f"{m.role}: {m.content[:100]}" for m in self._messages[-10:]]
        summary_text = " | ".join(parts)
        try:
            self._episodic.record_event(
                event_type="conversation_summary",
                data={
                    "summary": summary_text,
                    "message_count": len(self._messages),
                    "total_tokens": self._total_tokens,
                },
                what_happened="短期记忆自动摘要",
                why="窗口裁剪触发",
                outcome="摘要存入情景记忆",
            )
            self._summarize_count += 1
            self._logger.info(
                "自动摘要: %d 条消息, %d tokens",
                len(self._messages), self._total_tokens,
            )
        except Exception as e:
            self._logger.warning("自动摘要失败: %s", e)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "messages": len(self._messages),
            "total_tokens": self._total_tokens,
            "max_messages": self._max_messages,
            "max_tokens": self._max_tokens,
            "trim_count": self._trim_count,
            "summarize_count": self._summarize_count,
        }

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return (f"ShortTermMemory(messages={len(self._messages)}, "
                f"tokens={self._total_tokens}/{self._max_tokens})")


# ------------------------------------------------------------------ #
#  WorkingMemory —— 工作记忆（任务级 LRU 容器）
# ------------------------------------------------------------------ #

class WorkingMemory:
    """工作记忆：任务级短期记忆容器，LRU 淘汰策略。"""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity: int = max(1, capacity)
        self._store: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._logger = logging.getLogger(f"{__name__}.WorkingMemory")

    @property
    def capacity(self) -> int:
        return self._capacity

    def add_item(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = value
            else:
                if len(self._store) >= self._capacity:
                    evicted_key, _ = self._store.popitem(last=False)
                    self._logger.info("LRU 淘汰: key=%s", evicted_key)
                self._store[key] = value

    def get_item(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return self._store[key]
            return default

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._store)

    def clear(self) -> None:
        with self._lock:
            count = len(self._store)
            self._store.clear()
        self._logger.info("工作记忆已清空: %d 条", count)

    def snapshot(self) -> Dict[str, Any]:
        import copy
        with self._lock:
            return {
                "timestamp": time.time(),
                "items": copy.deepcopy(dict(self._store)),
                "capacity": self._capacity,
            }

    def restore(self, snapshot: Dict[str, Any]) -> None:
        import copy
        with self._lock:
            self._store.clear()
            for k, v in snapshot.get("items", {}).items():
                self._store[k] = copy.deepcopy(v)
            self._capacity = snapshot.get("capacity", self._capacity)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._store

    def __repr__(self) -> str:
        with self._lock:
            return f"WorkingMemory(items={len(self._store)}, capacity={self._capacity})"


# ------------------------------------------------------------------ #
#  EpisodicMemory —— 情景记忆（会话级事件 + 元数据）
# ------------------------------------------------------------------ #

EVENT_TYPE_DECISION = "decision"
EVENT_TYPE_TOOL_CALL = "tool_call"
EVENT_TYPE_ERROR = "error"
EVENT_TYPE_INSIGHT = "insight"
EVENT_TYPE_MILESTONE = "milestone"
EVENT_TYPE_SUMMARY = "conversation_summary"

VALID_EVENT_TYPES = (
    EVENT_TYPE_DECISION, EVENT_TYPE_TOOL_CALL, EVENT_TYPE_ERROR,
    EVENT_TYPE_INSIGHT, EVENT_TYPE_MILESTONE, EVENT_TYPE_SUMMARY,
)


class EpisodicMemory:
    """情景记忆：会话级事件存储，支持 what_happened / why / outcome 元数据。"""

    def __init__(self, max_events: int = 500) -> None:
        self._max_events: int = max(1, max_events)
        self._events: List[EventRecord] = []
        self._lock = threading.Lock()
        self._logger = logging.getLogger(f"{__name__}.EpisodicMemory")

    def record_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        timestamp: Optional[float] = None,
        what_happened: str = "",
        why: str = "",
        outcome: str = "",
    ) -> str:
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"非法事件类型: {event_type}, 支持: {VALID_EVENT_TYPES}")
        ts = timestamp if timestamp is not None else time.time()
        event = EventRecord(
            event_type=event_type, data=data, timestamp=ts,
            what_happened=what_happened, why=why, outcome=outcome,
        )
        with self._lock:
            if len(self._events) >= self._max_events:
                evicted = self._events.pop(0)
                self._logger.info("淘汰最旧事件: type=%s, id=%s", evicted.event_type, evicted.event_id)
            self._events.append(event)
        return event.event_id

    def query_events(
        self,
        filter: Optional[EventFilter] = None,
        event_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[EventRecord]:
        results: List[EventRecord] = []
        with self._lock:
            for evt in self._events:
                if event_type and evt.event_type != event_type:
                    continue
                if filter and not filter(evt):
                    continue
                results.append(evt)
                if limit and len(results) >= limit:
                    break
        return results

    def get_timeline(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "event_id": evt.event_id, "event_type": evt.event_type,
                    "timestamp": evt.timestamp, "data": evt.data,
                    "what_happened": evt.what_happened, "why": evt.why,
                    "outcome": evt.outcome,
                }
                for evt in self._events
            ]

    def summarize(self) -> Dict[str, Any]:
        with self._lock:
            if not self._events:
                return {"total_events": 0, "type_counts": {}, "time_range": None, "latest": None}
            type_counts: Dict[str, int] = {}
            for evt in self._events:
                type_counts[evt.event_type] = type_counts.get(evt.event_type, 0) + 1
            timestamps = [e.timestamp for e in self._events]
            return {
                "total_events": len(self._events),
                "type_counts": type_counts,
                "time_range": {
                    "start": min(timestamps), "end": max(timestamps),
                    "duration_seconds": round(max(timestamps) - min(timestamps), 2),
                },
                "latest": {
                    "event_id": self._events[-1].event_id,
                    "event_type": self._events[-1].event_type,
                    "timestamp": self._events[-1].timestamp,
                    "data": self._events[-1].data,
                },
            }

    def clear(self) -> None:
        with self._lock:
            count = len(self._events)
            self._events.clear()
        self._logger.info("情景记忆已清空: %d 条", count)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def __repr__(self) -> str:
        with self._lock:
            return f"EpisodicMemory(events={len(self._events)}, max={self._max_events})"


# ------------------------------------------------------------------ #
#  SemanticMemory —— 语义记忆（跨任务长期记忆）
# ------------------------------------------------------------------ #

class SemanticMemory:
    """语义记忆：基于向量相似度的跨任务长期知识存储与检索。"""

    def __init__(self, quality_threshold: int = 60) -> None:
        self._rag_available: bool = False
        self._rag: Any = None
        self._memory: Any = None
        self._quality_threshold: int = quality_threshold
        self._logger = logging.getLogger(f"{__name__}.SemanticMemory")
        self._init_dependencies()

    def _init_dependencies(self) -> None:
        try:
            from app.tools.rag_service import rag
            self._rag = rag
            self._rag_available = self._rag.is_available()
            if self._rag_available:
                self._logger.info("语义记忆: RAG 服务可用")
            else:
                self._logger.warning("语义记忆: RAG 服务未就绪")
        except Exception as e:
            self._logger.warning("语义记忆: RAG 初始化失败: %s", e)
            self._rag_available = False
        try:
            from app.core.memory import memory
            self._memory = memory
        except Exception as e:
            self._logger.warning("语义记忆: MemoryManager 初始化失败: %s", e)

    @property
    def quality_threshold(self) -> int:
        return self._quality_threshold

    def store_knowledge(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        quality_score: int = 0,
    ) -> str:
        import uuid
        knowledge_id = f"km_{uuid.uuid4().hex[:12]}"
        meta = metadata or {}
        enriched_meta = {
            "source": "semantic_memory", "knowledge_id": knowledge_id,
            "quality_score": str(quality_score), **meta,
        }
        if self._rag_available and self._rag:
            try:
                self._rag.add_texts(
                    texts=[content], metadatas=[enriched_meta],
                    ids=[knowledge_id], source="semantic_memory",
                )
            except Exception as e:
                self._logger.warning("语义知识存入 RAG 失败: %s", e)
        if self._memory:
            try:
                self._memory.save_experience(
                    task_id=knowledge_id, user_request=content[:500],
                    review_score=quality_score, review_verdict="semantic_store",
                    test_status="stored",
                )
            except Exception as e:
                self._logger.warning("语义知识存入 MemoryManager 失败: %s", e)
        self._logger.info("语义知识已存储: id=%s, quality=%d", knowledge_id, quality_score)
        return knowledge_id

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self._rag_available and self._rag:
            try:
                docs = self._rag.query(
                    query_text=query, top_k=top_k,
                    where={"source": "semantic_memory"},
                )
                for doc in docs:
                    if doc.score < min_score:
                        continue
                    results.append({
                        "id": doc.id, "content": doc.content,
                        "score": doc.score, "metadata": doc.metadata,
                        "source": "rag",
                    })
            except Exception as e:
                self._logger.warning("语义检索(RAG)失败: %s", e)
        if len(results) < top_k and self._memory:
            try:
                recall_results = self._memory.recall_experience(
                    query=query, limit=top_k - len(results),
                )
                for r in recall_results:
                    results.append({
                        "id": r.get("task_id", ""),
                        "content": r.get("user_request", ""),
                        "score": r.get("quality_score", 0) / 100.0,
                        "metadata": r, "source": "memory",
                    })
            except Exception as e:
                self._logger.warning("语义检索(Memory)失败: %s", e)
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:top_k]

    def get_stats(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "rag_available": self._rag_available, "rag_count": 0,
            "memory_stats": None, "quality_threshold": self._quality_threshold,
        }
        if self._rag_available and self._rag:
            try:
                stats["rag_count"] = self._rag.count()
            except Exception:
                pass
        if self._memory:
            try:
                stats["memory_stats"] = self._memory.get_stats()
            except Exception:
                pass
        return stats

    def clean_expired(self, max_age_days: int = 90) -> int:
        cleaned = 0
        cutoff_ts = time.time() - (max_age_days * 86400)
        if self._rag_available and self._rag:
            try:
                all_data = self._rag._collection.get(include=["metadatas"])
                if not all_data or not all_data.get("ids"):
                    return 0
                ids_to_delete: List[str] = []
                metas = all_data.get("metadatas") or []
                for i, eid in enumerate(all_data["ids"]):
                    meta = metas[i] if i < len(metas) else {}
                    quality = int(meta.get("quality_score", 0))
                    ts = float(meta.get("timestamp", 0))
                    if quality < self._quality_threshold and ts < cutoff_ts:
                        ids_to_delete.append(eid)
                if ids_to_delete:
                    self._rag.delete_ids(ids_to_delete)
                    cleaned = len(ids_to_delete)
            except Exception as e:
                self._logger.warning("清理过期知识失败: %s", e)
        return cleaned

    def __repr__(self) -> str:
        status = "online" if self._rag_available else "offline"
        return f"SemanticMemory(rag={status}, threshold={self._quality_threshold})"


# ------------------------------------------------------------------ #
#  LongTermMemory —— 持久化长期记忆（语义检索 + 标签管理）
# ------------------------------------------------------------------ #

class LongTermMemory:
    """长期记忆：封装 SemanticMemory，增加标签、分类与命中统计。"""

    def __init__(self, quality_threshold: int = 60) -> None:
        self.semantic = SemanticMemory(quality_threshold=quality_threshold)
        self._tag_index: Dict[str, List[str]] = {}
        self._hit_count: int = 0
        self._store_count: int = 0
        self._logger = logging.getLogger(f"{__name__}.LongTermMemory")

    def store(
        self,
        content: str,
        tags: Optional[List[str]] = None,
        quality_score: int = 0,
        category: str = "general",
    ) -> str:
        metadata: Dict[str, Any] = {"category": category}
        if tags:
            metadata["tags"] = tags
            for tag in tags:
                self._tag_index.setdefault(tag, [])
                self._tag_index[tag].append(content[:200])
        kid = self.semantic.store_knowledge(
            content=content, metadata=metadata, quality_score=quality_score,
        )
        self._store_count += 1
        self._logger.info("长期记忆存储: id=%s, category=%s", kid, category)
        return kid

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        tag_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        results = self.semantic.search(query, top_k=top_k)
        if tag_filter and tag_filter in self._tag_index:
            tag_contents = set(self._tag_index[tag_filter])
            results = [r for r in results if any(tc in r.get("content", "") for tc in tag_contents)]
        self._hit_count += len(results)
        return results

    def get_stats(self) -> Dict[str, Any]:
        base = self.semantic.get_stats()
        base.update({
            "hit_count": self._hit_count, "store_count": self._store_count,
            "tag_count": len(self._tag_index), "tags": list(self._tag_index.keys()),
        })
        return base

    def __repr__(self) -> str:
        return (f"LongTermMemory(store={self._store_count}, "
                f"hits={self._hit_count}, tags={len(self._tag_index)})")


# ------------------------------------------------------------------ #
#  MemoryConsolidator —— 记忆巩固器（短期 → 长期）
# ------------------------------------------------------------------ #

class MemoryConsolidator:
    """记忆巩固器：周期性将短期记忆提升至长期存储。"""

    def __init__(
        self,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
        episodic: EpisodicMemory,
        trigger_message_count: int = 20,
        trigger_token_count: int = 4000,
    ) -> None:
        self._short = short_term
        self._long = long_term
        self._episodic = episodic
        self._trigger_msgs = trigger_message_count
        self._trigger_tokens = trigger_token_count
        self._promoted_count: int = 0
        self._lock = threading.Lock()
        self._logger = logging.getLogger(f"{__name__}.MemoryConsolidator")

    @property
    def promoted_count(self) -> int:
        return self._promoted_count

    def should_consolidate(self) -> bool:
        stats = self._short.get_stats()
        return (stats["messages"] >= self._trigger_msgs
                or stats["total_tokens"] >= self._trigger_tokens)

    def consolidate(self) -> Dict[str, Any]:
        with self._lock:
            if not self.should_consolidate():
                return {"promoted": 0, "reason": "threshold_not_met"}
            messages = self._short.get_messages()
            if not messages:
                return {"promoted": 0, "reason": "no_messages"}
            summary = self._generate_summary(messages)
            try:
                kid = self._long.store(
                    content=summary,
                    tags=["auto_consolidated", "session_summary"],
                    quality_score=70, category="consolidated",
                )
                self._episodic.record_event(
                    event_type=EVENT_TYPE_SUMMARY,
                    data={
                        "summary": summary[:500],
                        "message_count": len(messages),
                        "knowledge_id": kid,
                    },
                    what_happened="记忆巩固",
                    why="达到触发阈值",
                    outcome=f"生成知识 {kid}",
                )
                self._promoted_count += 1
                self._logger.info("记忆巩固: %d 条消息 → 知识 %s", len(messages), kid)
            except Exception as e:
                self._logger.error("记忆巩固失败: %s", e)
                return {"promoted": 0, "reason": str(e)}
            return {
                "promoted": 1, "message_count": len(messages),
                "summary_length": len(summary),
                "total_promoted": self._promoted_count,
            }

    def _generate_summary(self, messages: List[ChatMessage]) -> str:
        parts: List[str] = []
        for m in messages[-15:]:
            content = m.content.strip()
            if len(content) > 150:
                content = content[:150] + "..."
            parts.append(f"{m.role}: {content}")
        return "\n".join(parts)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "promoted_count": self._promoted_count,
            "trigger_message_count": self._trigger_msgs,
            "trigger_token_count": self._trigger_tokens,
            "should_consolidate": self.should_consolidate(),
        }

    def __repr__(self) -> str:
        return f"MemoryConsolidator(promoted={self._promoted_count})"


# ------------------------------------------------------------------ #
#  MemorySearcher —— 统一跨层搜索（短期 + 长期 + 情景）
# ------------------------------------------------------------------ #

class MemorySearcher:
    """统一跨层搜索：在所有记忆层中检索并返回相关性排序结果。"""

    def __init__(
        self,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
        episodic: EpisodicMemory,
    ) -> None:
        self._short = short_term
        self._long = long_term
        self._episodic = episodic
        self._search_count: int = 0
        self._total_results: int = 0
        self._logger = logging.getLogger(f"{__name__}.MemorySearcher")

    def search(
        self,
        query: str,
        top_k: int = 10,
        include_short: bool = True,
        include_long: bool = True,
        include_episodic: bool = True,
    ) -> List[Dict[str, Any]]:
        all_results: List[Dict[str, Any]] = []
        query_lower = query.lower()

        if include_short:
            for i, m in enumerate(self._short.get_messages()):
                if query_lower in m.content.lower():
                    score = 1.0 if i >= len(self._short) - 5 else 0.8
                    all_results.append({
                        "id": f"st_msg_{i}", "content": m.content,
                        "layer": "short_term", "role": m.role,
                        "relevance_score": score, "source": "short_term",
                    })

        if include_long:
            for r in self._long.retrieve(query, top_k=top_k):
                r["layer"] = "long_term"
                r["relevance_score"] = r.get("score", 0.5) * 0.9
                all_results.append(r)

        if include_episodic:
            for evt in self._episodic.query_events():
                if query_lower in str(evt.data).lower():
                    all_results.append({
                        "id": evt.event_id, "content": str(evt.data),
                        "layer": "episodic", "event_type": evt.event_type,
                        "relevance_score": 0.6, "source": "episodic",
                    })

        all_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        final = all_results[:top_k]
        self._search_count += 1
        self._total_results += len(final)
        return final

    def get_stats(self) -> Dict[str, Any]:
        return {
            "search_count": self._search_count,
            "total_results": self._total_results,
            "avg_results_per_search": round(
                self._total_results / max(1, self._search_count), 2,
            ),
        }

    def __repr__(self) -> str:
        return f"MemorySearcher(searches={self._search_count})"


# ------------------------------------------------------------------ #
#  AuditLogger 集成辅助
# ------------------------------------------------------------------ #

def _log_memory_operation(
    action: str,
    layer: str,
    details: Dict[str, Any],
    actor_id: str = "memory_system",
) -> None:
    try:
        from app.core.audit import AuditEventType, audit_logger
        audit_logger.log(
            event_type=AuditEventType.STATE_TRANSITION,
            actor_type="system",
            actor_id=actor_id,
            action=f"memory_{action}",
            input_data=details,
            metadata={"memory_layer": layer},
        )
    except Exception:
        pass


# ------------------------------------------------------------------ #
#  MemoryFacade —— 门面类（统一入口，保持向后兼容）
# ------------------------------------------------------------------ #

class MemoryFacade:
    """记忆系统门面：统一入口，协调五层记忆 + 巩固 + 搜索。

    保留所有旧 API（working / episodic / semantic），
    新增短期、长期、巩固器和搜索器。
    """

    def __init__(
        self,
        working_capacity: int = 200,
        episodic_max: int = 500,
        semantic_quality_threshold: int = 60,
        short_term_max_messages: int = 50,
        short_term_max_tokens: int = 8000,
    ) -> None:
        self.working = WorkingMemory(capacity=working_capacity)
        self.episodic = EpisodicMemory(max_events=episodic_max)
        self.semantic = SemanticMemory(quality_threshold=semantic_quality_threshold)
        self.short_term = ShortTermMemory(
            max_messages=short_term_max_messages,
            max_tokens=short_term_max_tokens,
            episodic_memory=self.episodic,
        )
        self.long_term = LongTermMemory(quality_threshold=semantic_quality_threshold)
        self.consolidator = MemoryConsolidator(
            short_term=self.short_term, long_term=self.long_term,
            episodic=self.episodic,
        )
        self.searcher = MemorySearcher(
            short_term=self.short_term, long_term=self.long_term,
            episodic=self.episodic,
        )
        self._logger = logging.getLogger(f"{__name__}.MemoryFacade")

    def write(
        self,
        level: MemoryLevel,
        key: str,
        value: Any = None,
        **kwargs: Any,
    ) -> str:
        if level == LEVEL_WORKING:
            self.working.add_item(key, value)
            _log_memory_operation("write", level, {"key": key})
            return key
        elif level == LEVEL_EPISODIC:
            data = value if isinstance(value, dict) else {"content": value}
            event_id = self.episodic.record_event(
                event_type=key or EVENT_TYPE_INSIGHT, data=data,
                timestamp=kwargs.get("timestamp"),
                what_happened=kwargs.get("what_happened", ""),
                why=kwargs.get("why", ""),
                outcome=kwargs.get("outcome", ""),
            )
            _log_memory_operation("write", level, {"event_id": event_id})
            return event_id
        elif level == LEVEL_SEMANTIC:
            semantic_content = key if value is None else str(value)
            kid = self.semantic.store_knowledge(
                content=semantic_content, metadata=kwargs.get("metadata", {}),
                quality_score=kwargs.get("quality_score", 0),
            )
            _log_memory_operation("write", level, {"knowledge_id": kid})
            return kid
        elif level == LEVEL_SHORT_TERM:
            msg = self.short_term.add_message(
                role=kwargs.get("role", "user"),
                content=key if value is None else str(value),
            )
            _log_memory_operation("write", level, {"role": msg.role})
            return f"st_{msg.timestamp}"
        elif level == LEVEL_LONG_TERM:
            kid = self.long_term.store(
                content=key if value is None else str(value), tags=kwargs.get("tags"),
                quality_score=kwargs.get("quality_score", 0),
                category=kwargs.get("category", "general"),
            )
            _log_memory_operation("write", level, {"knowledge_id": kid})
            return kid
        else:
            raise ValueError(f"非法记忆层级: {level}, 支持: {VALID_LEVELS}")

    def read(
        self,
        level: MemoryLevel,
        key: str,
        default: Any = None,
        **kwargs: Any,
    ) -> Any:
        if level == LEVEL_WORKING:
            return self.working.get_item(key, default)
        elif level == LEVEL_EPISODIC:
            events = self.episodic.query_events(event_type=kwargs.get("event_type"))
            if key:
                for evt in events:
                    if evt.event_id == key:
                        return evt
                return default
            return events if events else default
        elif level == LEVEL_SEMANTIC:
            results = self.semantic.search(query=key, top_k=kwargs.get("top_k", 5))
            return results if results else default
        elif level == LEVEL_SHORT_TERM:
            return self.short_term.get_messages(last_n=kwargs.get("last_n"))
        elif level == LEVEL_LONG_TERM:
            return self.long_term.retrieve(query=key, top_k=kwargs.get("top_k", 5))
        else:
            raise ValueError(f"非法记忆层级: {level}, 支持: {VALID_LEVELS}")

    def search(
        self,
        query: str,
        top_k: int = 5,
        include_episodic: bool = True,
        include_semantic: bool = True,
    ) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        if include_semantic:
            result["semantic"] = self.semantic.search(query, top_k=top_k)
        if include_episodic:
            matched: List[Dict[str, Any]] = []
            query_lower = query.lower()
            for evt in self.episodic.query_events():
                if query_lower in str(evt.data).lower():
                    matched.append({
                        "id": evt.event_id, "event_type": evt.event_type,
                        "data": evt.data, "timestamp": evt.timestamp,
                        "score": 1.0, "source": "episodic",
                    })
            result["episodic"] = matched[:top_k]
        return result

    def unified_search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """统一跨层搜索：使用 MemorySearcher。"""
        return self.searcher.search(query, top_k=top_k)

    def commit_session(self, semantic_threshold: int = 70) -> Dict[str, int]:
        stats = {
            "episodic_to_semantic": 0, "working_to_semantic": 0,
            "short_to_long": 0, "errors": 0,
        }
        priority_types = (EVENT_TYPE_MILESTONE, EVENT_TYPE_INSIGHT, EVENT_TYPE_DECISION)
        for evt in self.episodic.query_events():
            try:
                quality_score = int(
                    evt.data.get("quality_score", 0) or evt.data.get("review_score", 0)
                )
                if evt.event_type in priority_types and quality_score >= semantic_threshold:
                    content = f"[{evt.event_type}] {evt.data.get('content', evt.data.get('description', ''))}"
                    if content.strip():
                        self.semantic.store_knowledge(
                            content=content,
                            metadata={
                                "source": "episodic_sediment", "event_type": evt.event_type,
                                "event_id": evt.event_id,
                            },
                            quality_score=quality_score,
                        )
                        stats["episodic_to_semantic"] += 1
            except Exception as e:
                self._logger.warning("情景→语义流转失败: %s", e)
                stats["errors"] += 1

        for key, value in self.working.get_all().items():
            try:
                if isinstance(value, dict) and value.get("semantic_candidate"):
                    self.semantic.store_knowledge(
                        content=str(value.get("content", value)),
                        metadata={"source": "working_sediment", "key": key},
                        quality_score=int(value.get("quality_score", semantic_threshold)),
                    )
                    stats["working_to_semantic"] += 1
            except Exception as e:
                self._logger.warning("工作→语义流转失败: %s", e)
                stats["errors"] += 1

        stats["short_to_long"] = self.consolidator.consolidate().get("promoted", 0)
        self._logger.info(
            "会话提交: epi→sem=%d, work→sem=%d, short→long=%d",
            stats["episodic_to_semantic"], stats["working_to_semantic"],
            stats["short_to_long"],
        )
        return stats

    def rollback(self) -> Dict[str, Any]:
        working_count = len(self.working)
        episodic_count = len(self.episodic)
        short_count = len(self.short_term)
        self.working.clear()
        self.episodic.clear()
        self.short_term.clear()
        return {
            "working_cleared": working_count,
            "episodic_cleared": episodic_count,
            "short_term_cleared": short_count,
            "semantic_preserved": True,
            "long_term_preserved": True,
        }

    def get_system_stats(self) -> Dict[str, Any]:
        return {
            "working": {"items": len(self.working), "capacity": self.working.capacity},
            "episodic": {"events": len(self.episodic), "summary": self.episodic.summarize()},
            "semantic": self.semantic.get_stats(),
            "short_term": self.short_term.get_stats(),
            "long_term": self.long_term.get_stats(),
            "consolidator": self.consolidator.get_stats(),
            "searcher": self.searcher.get_stats(),
        }

    def get_memory_stats(self) -> Dict[str, Any]:
        """获取各层记忆大小与命中率统计。"""
        short_s = self.short_term.get_stats()
        long_s = self.long_term.get_stats()
        search_s = self.searcher.get_stats()
        consolidator_s = self.consolidator.get_stats()
        total_hits = search_s.get("total_results", 0)
        total_searches = max(1, search_s.get("search_count", 0))
        return {
            "layer_sizes": {
                "working": len(self.working),
                "episodic": len(self.episodic),
                "short_term": short_s["messages"],
                "long_term": long_s.get("store_count", 0),
            },
            "hit_rates": {
                "overall": round(total_hits / total_searches, 2),
                "search_count": search_s.get("search_count", 0),
                "total_hits": total_hits,
                "consolidations": consolidator_s.get("promoted_count", 0),
            },
            "token_usage": {
                "short_term_total": short_s["total_tokens"],
                "short_term_max": short_s["max_tokens"],
            },
        }

    def __repr__(self) -> str:
        return (
            f"MemoryFacade(working={len(self.working)}/{self.working.capacity}, "
            f"episodic={len(self.episodic)}, short_term={len(self.short_term)}, "
            f"long_term={self.long_term})"
        )


# ------------------------------------------------------------------ #
#  全局单例
# ------------------------------------------------------------------ #

memory_system = MemoryFacade()

__all__ = [
    "WorkingMemory", "EpisodicMemory", "SemanticMemory",
    "LongTermMemory", "ShortTermMemory",
    "MemoryConsolidator", "MemorySearcher",
    "MemoryFacade", "EventRecord", "ChatMessage",
    "memory_system",
    "LEVEL_WORKING", "LEVEL_EPISODIC", "LEVEL_SEMANTIC",
    "LEVEL_SHORT_TERM", "LEVEL_LONG_TERM",
    "VALID_LEVELS", "VALID_EVENT_TYPES",
    "EVENT_TYPE_DECISION", "EVENT_TYPE_TOOL_CALL", "EVENT_TYPE_ERROR",
    "EVENT_TYPE_INSIGHT", "EVENT_TYPE_MILESTONE", "EVENT_TYPE_SUMMARY",
]
