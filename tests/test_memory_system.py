"""三层记忆系统单元测试。

覆盖:
- WorkingMemory: LRU 淘汰策略、读写、快照/恢复
- EpisodicMemory: 事件记录、查询、时间线、摘要
- SemanticMemory: 知识存储、检索（通过 facade 测试）
- MemoryFacade: 三层统一入口、自动流转（commit_session / rollback）
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.core.memory_system import (
    EVENT_TYPE_DECISION,
    EVENT_TYPE_ERROR,
    EVENT_TYPE_INSIGHT,
    EVENT_TYPE_MILESTONE,
    EVENT_TYPE_TOOL_CALL,
    EpisodicMemory,
    EventRecord,
    MemoryFacade,
    SemanticMemory,
    WorkingMemory,
)


# ====================================================================== #
#  WorkingMemory —— LRU 淘汰
# ====================================================================== #


class TestWorkingMemory:
    """WorkingMemory LRU 淘汰与基础操作。"""

    def test_add_and_get(self):
        """基础读写。"""
        wm = WorkingMemory(capacity=10)
        wm.add_item("key1", "value1")
        assert wm.get_item("key1") == "value1"
        assert wm.get_item("nonexistent") is None
        assert wm.get_item("nonexistent", "default") == "default"

    def test_update_existing_key(self):
        """更新已有 key 应刷新最近使用标记。"""
        wm = WorkingMemory(capacity=3)
        wm.add_item("a", 1)
        wm.add_item("b", 2)
        wm.add_item("c", 3)
        wm.add_item("a", 10)
        assert wm.get_item("a") == 10

    def test_lru_eviction(self):
        """超出容量时应淘汰最久未使用的条目。"""
        wm = WorkingMemory(capacity=3)
        wm.add_item("a", 1)
        wm.add_item("b", 2)
        wm.add_item("c", 3)
        wm.add_item("d", 4)
        assert wm.get_item("a") is None
        assert wm.get_item("b") == 2
        assert wm.get_item("c") == 3
        assert wm.get_item("d") == 4

    def test_lru_refresh_on_get(self):
        """get_item 应刷新最近使用标记，防止被淘汰。"""
        wm = WorkingMemory(capacity=3)
        wm.add_item("a", 1)
        wm.add_item("b", 2)
        wm.add_item("c", 3)
        wm.get_item("a")
        wm.add_item("d", 4)
        assert wm.get_item("a") == 1
        assert wm.get_item("b") is None
        assert wm.get_item("c") == 3
        assert wm.get_item("d") == 4

    def test_get_all(self):
        """get_all 应返回所有条目浅拷贝。"""
        wm = WorkingMemory(capacity=10)
        wm.add_item("x", 10)
        wm.add_item("y", 20)
        all_items = wm.get_all()
        assert len(all_items) == 2
        assert all_items["x"] == 10
        assert all_items["y"] == 20

    def test_clear(self):
        """clear 应清空所有条目。"""
        wm = WorkingMemory(capacity=10)
        wm.add_item("a", 1)
        wm.add_item("b", 2)
        wm.clear()
        assert len(wm) == 0
        assert wm.get_item("a") is None

    def test_contains(self):
        """__contains__ 应正确判断 key 是否存在。"""
        wm = WorkingMemory(capacity=10)
        wm.add_item("k", "v")
        assert "k" in wm
        assert "x" not in wm

    def test_len(self):
        """__len__ 应返回条目数。"""
        wm = WorkingMemory(capacity=10)
        assert len(wm) == 0
        wm.add_item("a", 1)
        assert len(wm) == 1

    def test_snapshot_and_restore(self):
        """快照与恢复。"""
        wm = WorkingMemory(capacity=10)
        wm.add_item("a", 1)
        wm.add_item("b", {"nested": [1, 2, 3]})
        snap = wm.snapshot()

        wm.clear()
        assert len(wm) == 0

        wm.restore(snap)
        assert len(wm) == 2
        assert wm.get_item("a") == 1
        assert wm.get_item("b") == {"nested": [1, 2, 3]}

    def test_snapshot_deep_copy(self):
        """快照应为深拷贝，修改原数据不影响快照。"""
        wm = WorkingMemory(capacity=10)
        data = {"counter": 1}
        wm.add_item("data", data)
        snap = wm.snapshot()
        data["counter"] = 999
        restored = {
            k: v for k, v in snap["items"].items()
        }
        assert restored["data"]["counter"] == 1

    def test_minimum_capacity(self):
        """容量最小为 1。"""
        wm = WorkingMemory(capacity=0)
        assert wm.capacity == 1

    def test_repr(self):
        """__repr__ 应包含条目数和容量。"""
        wm = WorkingMemory(capacity=50)
        wm.add_item("a", 1)
        r = repr(wm)
        assert "1" in r
        assert "50" in r


# ====================================================================== #
#  EpisodicMemory —— 事件记录
# ====================================================================== #


class TestEpisodicMemory:
    """EpisodicMemory 事件记录与查询。"""

    def test_record_event(self):
        """记录事件应返回 event_id。"""
        em = EpisodicMemory(max_events=10)
        eid = em.record_event(
            EVENT_TYPE_DECISION,
            {"action": "select_strategy", "choice": "parallel"},
        )
        assert eid.startswith("evt_")
        assert len(em) == 1

    def test_record_event_invalid_type(self):
        """非法事件类型应抛 ValueError。"""
        em = EpisodicMemory()
        with pytest.raises(ValueError, match="非法事件类型"):
            em.record_event("invalid_type", {"data": "x"})

    def test_query_all_events(self):
        """查询所有事件。"""
        em = EpisodicMemory()
        em.record_event(EVENT_TYPE_DECISION, {"a": 1})
        em.record_event(EVENT_TYPE_TOOL_CALL, {"b": 2})
        em.record_event(EVENT_TYPE_ERROR, {"c": 3})
        results = em.query_events()
        assert len(results) == 3

    def test_query_by_type(self):
        """按类型过滤。"""
        em = EpisodicMemory()
        em.record_event(EVENT_TYPE_DECISION, {"a": 1})
        em.record_event(EVENT_TYPE_TOOL_CALL, {"b": 2})
        em.record_event(EVENT_TYPE_DECISION, {"c": 3})
        results = em.query_events(event_type=EVENT_TYPE_DECISION)
        assert len(results) == 2
        assert all(e.event_type == EVENT_TYPE_DECISION for e in results)

    def test_query_with_filter(self):
        """自定义过滤函数。"""
        em = EpisodicMemory()
        em.record_event(EVENT_TYPE_DECISION, {"score": 90})
        em.record_event(EVENT_TYPE_DECISION, {"score": 30})
        em.record_event(EVENT_TYPE_DECISION, {"score": 70})
        results = em.query_events(
            filter=lambda e: e.data.get("score", 0) > 50,
        )
        assert len(results) == 2

    def test_query_with_limit(self):
        """限制返回条数。"""
        em = EpisodicMemory()
        for i in range(10):
            em.record_event(EVENT_TYPE_TOOL_CALL, {"i": i})
        results = em.query_events(limit=3)
        assert len(results) == 3

    def test_get_timeline(self):
        """时间线应按时间升序。"""
        em = EpisodicMemory()
        t1 = time.time()
        em.record_event(EVENT_TYPE_DECISION, {"order": 1}, timestamp=t1)
        t2 = t1 + 10
        em.record_event(EVENT_TYPE_DECISION, {"order": 2}, timestamp=t2)
        timeline = em.get_timeline()
        assert len(timeline) == 2
        assert timeline[0]["timestamp"] <= timeline[1]["timestamp"]

    def test_summarize_empty(self):
        """空事件列表的摘要。"""
        em = EpisodicMemory()
        summary = em.summarize()
        assert summary["total_events"] == 0
        assert summary["type_counts"] == {}

    def test_summarize_with_events(self):
        """有事件时的摘要。"""
        em = EpisodicMemory()
        em.record_event(EVENT_TYPE_DECISION, {"a": 1})
        em.record_event(EVENT_TYPE_TOOL_CALL, {"b": 2})
        em.record_event(EVENT_TYPE_DECISION, {"c": 3})
        summary = em.summarize()
        assert summary["total_events"] == 3
        assert summary["type_counts"]["decision"] == 2
        assert summary["type_counts"]["tool_call"] == 1
        assert summary["time_range"] is not None

    def test_max_events_eviction(self):
        """超出 max_events 应淘汰最旧事件。"""
        em = EpisodicMemory(max_events=3)
        em.record_event(EVENT_TYPE_DECISION, {"i": 1})
        em.record_event(EVENT_TYPE_DECISION, {"i": 2})
        em.record_event(EVENT_TYPE_DECISION, {"i": 3})
        em.record_event(EVENT_TYPE_DECISION, {"i": 4})
        assert len(em) == 3
        timeline = em.get_timeline()
        assert timeline[0]["data"]["i"] == 2

    def test_clear(self):
        """清空事件。"""
        em = EpisodicMemory()
        em.record_event(EVENT_TYPE_DECISION, {"a": 1})
        em.clear()
        assert len(em) == 0

    def test_event_record_post_init(self):
        """EventRecord 自动生成 event_id。"""
        rec = EventRecord(event_type="decision", data={"x": 1})
        assert rec.event_id.startswith("evt_")


# ====================================================================== #
#  SemanticMemory —— 检索
# ====================================================================== #


class TestSemanticMemory:
    """SemanticMemory 基础功能（不依赖真实 RAG）。"""

    def test_init_without_rag(self):
        """无 RAG 时应降级运行。"""
        sm = SemanticMemory()
        assert isinstance(sm._rag_available, bool)
        assert sm.quality_threshold == 60

    def test_quality_threshold(self):
        """质量阈值属性。"""
        sm = SemanticMemory(quality_threshold=80)
        assert sm.quality_threshold == 80

    def test_store_knowledge_returns_id(self):
        """存储知识应返回 ID。"""
        sm = SemanticMemory()
        kid = sm.store_knowledge("test knowledge")
        assert kid.startswith("km_")

    def test_store_knowledge_with_metadata(self):
        """带元数据存储。"""
        sm = SemanticMemory()
        kid = sm.store_knowledge(
            "test",
            metadata={"source": "test_case", "tags": ["unit"]},
            quality_score=90,
        )
        assert kid is not None

    def test_search_without_rag(self):
        """无 RAG 时搜索应降级不报错。"""
        sm = SemanticMemory()
        results = sm.search("anything")
        assert isinstance(results, list)

    def test_get_stats(self):
        """get_stats 应返回统计信息。"""
        sm = SemanticMemory()
        stats = sm.get_stats()
        assert "rag_available" in stats
        assert "quality_threshold" in stats

    def test_repr(self):
        """__repr__ 应包含 RAG 状态。"""
        sm = SemanticMemory()
        r = repr(sm)
        assert "SemanticMemory" in r


# ====================================================================== #
#  MemoryFacade —— 自动流转
# ====================================================================== #


class TestMemoryFacade:
    """MemoryFacade 三层统一入口与自动流转。"""

    @pytest.fixture
    def facade(self):
        """创建独立的 MemoryFacade 实例。"""
        return MemoryFacade(
            working_capacity=50,
            episodic_max=100,
            semantic_quality_threshold=60,
        )

    def test_write_working(self, facade):
        """写入工作记忆。"""
        fid = facade.write("working", "task_state", {"step": 3})
        assert fid == "task_state"
        assert facade.working.get_item("task_state") == {"step": 3}

    def test_read_working(self, facade):
        """读取工作记忆。"""
        facade.write("working", "k", "v")
        val = facade.read("working", "k")
        assert val == "v"

    def test_write_episodic(self, facade):
        """写入情景记忆。"""
        eid = facade.write("episodic", "decision", {"action": "test"})
        assert eid.startswith("evt_")

    def test_read_episodic_by_type(self, facade):
        """按类型读取情景记忆。"""
        facade.write("episodic", "milestone", {"desc": "完成分析"})
        facade.write("episodic", "decision", {"action": "test"})
        events = facade.read("episodic", "", event_type="milestone")
        assert len(events) >= 1

    def test_write_semantic(self, facade):
        """写入语义记忆。"""
        kid = facade.write("semantic", "", "Python list sort用法", quality_score=80)
        assert kid.startswith("km_")

    def test_invalid_level_raises(self, facade):
        """非法层级应抛 ValueError。"""
        with pytest.raises(ValueError, match="非法记忆层级"):
            facade.write("invalid_level", "k", "v")

    def test_commit_session_sediments(self, facade):
        """commit_session 应将高价值事件流转至语义层。"""
        facade.write(
            "episodic", "milestone",
            {"content": "完成登录模块", "quality_score": 85},
        )
        facade.write(
            "episodic", "insight",
            {"content": "发现新的设计模式", "quality_score": 75},
        )
        facade.write(
            "episodic", "tool_call",
            {"tool": "python", "quality_score": 30},
        )
        stats = facade.commit_session(semantic_threshold=70)
        assert stats["episodic_to_semantic"] >= 2

    def test_commit_session_working_sediment(self, facade):
        """工作记忆中带 semantic_candidate 的条目应流转。"""
        facade.write(
            "working", "k1",
            {"content": "可复用模式", "semantic_candidate": True, "quality_score": 80},
        )
        stats = facade.commit_session()
        assert stats["working_to_semantic"] == 1

    def test_rollback(self, facade):
        """rollback 应清空工作/情景记忆，保留语义。"""
        facade.write("working", "k", "v")
        facade.write("episodic", "decision", {"a": 1})
        facade.write("semantic", "", "知识")
        summary = facade.rollback()
        assert summary["working_cleared"] == 1
        assert summary["episodic_cleared"] == 1
        assert summary["semantic_preserved"] is True
        assert len(facade.working) == 0
        assert len(facade.episodic) == 0

    def test_get_system_stats(self, facade):
        """get_system_stats 应返回三层统计。"""
        facade.write("working", "a", 1)
        facade.write("episodic", "decision", {"x": 1})
        stats = facade.get_system_stats()
        assert "working" in stats
        assert "episodic" in stats
        assert "semantic" in stats
        assert stats["working"]["items"] == 1

    def test_cross_layer_search(self, facade):
        """跨层级搜索。"""
        facade.write("episodic", "milestone", {"content": "实现登录功能完成"})
        results = facade.search("登录")
        assert "episodic" in results

    def test_repr(self, facade):
        """__repr__ 应包含三层状态。"""
        r = repr(facade)
        assert "MemoryFacade" in r
