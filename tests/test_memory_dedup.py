"""记忆精炼增强单元测试：去重、反馈精炼、技能沉淀。

覆盖：
- 去重：相同/高度相似需求更新旧记录而非新增，按质量分保留更优者
- 去重开关关闭时行为不变
- RAG 语义相似度去重
- 反馈精炼：quality_score 标注、低质量过滤、高质量优先排序
- 技能沉淀：从高评分经验归纳可复用技能写入内存注册表
- 配置项默认值

测试约束：
- 全部本地可运行，不依赖网络
- 使用临时 DB + mock RAG，避免污染真实数据
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import MemoryConfig
from app.core.memory import MemoryManager
from app.tools.rag_service import RAGDocument
from app.tools.skill_registry import skill_registry


@pytest.fixture()
def tmp_memory(tmp_path):
    """构造指向临时 DB 的 MemoryManager，并 mock 掉 RAG。"""
    db_path = str(tmp_path / "test_memory_dedup.db")
    mm = MemoryManager(db_path=db_path)
    mock_rag = MagicMock()
    mock_rag.add_texts.return_value = None
    mock_rag.query.return_value = []
    mm._rag = mock_rag
    return mm


# ================================================================== #
#  配置默认值
# ================================================================== #
def test_memory_config_refinement_defaults():
    """MemoryConfig 新增精炼配置项默认值。"""
    cfg = MemoryConfig()
    assert cfg.memory_dedup_enabled is True
    assert cfg.memory_quality_threshold == 80
    assert cfg.memory_dedup_similarity_threshold > 0
    assert cfg.memory_filter_low_quality is False


# ================================================================== #
#  去重：相同需求
# ================================================================== #
def test_memory_dedup_exact_match_updates(tmp_memory):
    """相同 user_request 保存两次应更新旧记录而非新增。"""
    mm = tmp_memory
    id1 = mm.save_experience(task_id="t1", user_request="实现登录", review_score=50)
    id2 = mm.save_experience(task_id="t2", user_request="实现登录", review_score=90)
    # 返回同一 id，且记录数不膨胀
    assert id1 == id2
    assert mm.get_stats()["total_experiences"] == 1
    # 保留更优者
    sqlite_hits = [r for r in mm.recall_experience("登录") if r["source"] == "sqlite"]
    assert sqlite_hits[0]["review_score"] == 90


def test_memory_dedup_keeps_better_quality(tmp_memory):
    """低质量先存、高质量后存，应保留高质量记录。"""
    mm = tmp_memory
    mm.save_experience(
        task_id="t1", user_request="实现登录",
        review_verdict="rejected", review_score=30, test_status="failed",
    )
    mm.save_experience(
        task_id="t2", user_request="实现登录",
        review_verdict="approved", review_score=85, test_status="passed",
    )
    assert mm.get_stats()["total_experiences"] == 1
    sqlite_hits = [r for r in mm.recall_experience("登录") if r["source"] == "sqlite"]
    assert sqlite_hits[0]["review_verdict"] == "approved"
    assert sqlite_hits[0]["review_score"] == 85


def test_memory_dedup_skips_worse(tmp_memory):
    """高质量先存、低质量后存，应保留高质量原记录。"""
    mm = tmp_memory
    mm.save_experience(
        task_id="t1", user_request="实现登录",
        review_verdict="approved", review_score=90,
    )
    mm.save_experience(
        task_id="t2", user_request="实现登录",
        review_verdict="rejected", review_score=20,
    )
    assert mm.get_stats()["total_experiences"] == 1
    sqlite_hits = [r for r in mm.recall_experience("登录") if r["source"] == "sqlite"]
    assert sqlite_hits[0]["review_score"] == 90
    assert sqlite_hits[0]["review_verdict"] == "approved"


def test_memory_dedup_disabled_appends(tmp_memory):
    """去重开关关闭时，相同需求应新增而非更新。"""
    mm = tmp_memory
    original = mm._rag  # noqa: F841 - 保留引用
    from app.core.config import settings
    saved = settings.memory.memory_dedup_enabled
    try:
        settings.memory.memory_dedup_enabled = False
        mm.save_experience(task_id="t1", user_request="实现登录", review_score=50)
        mm.save_experience(task_id="t2", user_request="实现登录", review_score=70)
        assert mm.get_stats()["total_experiences"] == 2
    finally:
        settings.memory.memory_dedup_enabled = saved


def test_memory_dedup_rag_similarity(tmp_memory):
    """RAG 相似度超过阈值应视为重复并更新旧记录。"""
    mm = tmp_memory
    mm.save_experience(task_id="t1", user_request="实现用户登录", review_score=80)
    # 覆写 RAG：返回相似文档（score 超过 0.92 阈值，experience_id=1）
    mm._rag.query.return_value = [
        RAGDocument(
            id="exp1",
            content="需求: 实现用户登录",
            metadata={
                "score": 0.95, "source": "experience",
                "task_id": "t1", "experience_id": "1",
            },
        )
    ]
    mm.save_experience(task_id="t2", user_request="实现用户登录功能", review_score=95)
    assert mm.get_stats()["total_experiences"] == 1
    sqlite_hits = [r for r in mm.recall_experience("登录") if r["source"] == "sqlite"]
    assert sqlite_hits[0]["review_score"] == 95


# ================================================================== #
#  反馈精炼：质量标注 / 过滤 / 排序
# ================================================================== #
def test_memory_quality_score_computed(tmp_memory):
    """quality_score 应按评审分 + 测试通过率启发式计算并存储。"""
    mm = tmp_memory
    mm.save_experience(
        task_id="t1", user_request="好需求",
        review_verdict="approved", review_score=80,
        test_status="passed", test_pass_rate=1.0,
    )
    sqlite_hits = [r for r in mm.recall_experience("好需求") if r["source"] == "sqlite"]
    # 80 + 通过率奖励 20 = 100
    assert sqlite_hits[0]["quality_score"] == 100
    assert sqlite_hits[0]["is_low_quality"] is False


def test_memory_low_quality_marked(tmp_memory):
    """低质量经验（rejected + failed + 低分）应被标注 is_low_quality=True。"""
    mm = tmp_memory
    mm.save_experience(
        task_id="low", user_request="废需求",
        review_verdict="rejected", review_score=10, test_status="failed",
    )
    sqlite_hits = [r for r in mm.recall_experience("废需求") if r["source"] == "sqlite"]
    assert sqlite_hits[0]["is_low_quality"] is True
    assert sqlite_hits[0]["quality_score"] == 0


def test_memory_filter_low_quality(tmp_memory):
    """filter_low_quality=True 时低质量经验被过滤，高质量保留。"""
    mm = tmp_memory
    mm.save_experience(
        task_id="low", user_request="废需求",
        review_verdict="rejected", review_score=10, test_status="failed",
    )
    mm.save_experience(
        task_id="high", user_request="好需求",
        review_verdict="approved", review_score=90,
    )
    all_sqlite = [r for r in mm.recall_experience("需求") if r["source"] == "sqlite"]
    assert {r["task_id"] for r in all_sqlite} == {"low", "high"}

    filtered = [r for r in mm.recall_experience("需求", filter_low_quality=True)
                if r["source"] == "sqlite"]
    tasks = {r["task_id"] for r in filtered}
    assert "low" not in tasks
    assert "high" in tasks


def test_memory_recall_high_quality_first(tmp_memory):
    """检索结果应按质量分降序，高质量经验优先。"""
    mm = tmp_memory
    mm.save_experience(task_id="a", user_request="需求低", review_score=40)
    mm.save_experience(task_id="b", user_request="需求高", review_score=95)
    sqlite_hits = [r for r in mm.recall_experience("需求") if r["source"] == "sqlite"]
    assert sqlite_hits[0]["task_id"] == "b"
    assert sqlite_hits[0]["quality_score"] > sqlite_hits[1]["quality_score"]


# ================================================================== #
#  技能沉淀
# ================================================================== #
def test_memory_distill_skills(tmp_memory):
    """从高评分经验归纳可复用技能写入内存注册表。"""
    mm = tmp_memory
    sid = f"rt_{uuid.uuid4().hex[:6]}"
    mm.save_experience(
        task_id="t1", user_request="实现数据库连接池",
        review_verdict="approved", review_score=90, skill_id=sid,
    )
    mm.save_experience(
        task_id="t2", user_request="实现数据库索引优化",
        review_verdict="approved", review_score=85, skill_id=sid,
    )
    registered = mm.distill_skills(min_score=80)
    skill_id = f"memory_{sid}"
    assert skill_id in registered
    skill = skill_registry.get_skill(skill_id)
    assert skill is not None
    assert skill.name  # 名称非空
    assert skill.keywords  # 关键词非空
    assert skill.best_practices  # 最佳实践非空


def test_memory_distill_skills_below_threshold(tmp_memory):
    """低于评分阈值的经验不参与技能沉淀。"""
    mm = tmp_memory
    sid = f"rt_{uuid.uuid4().hex[:6]}"
    mm.save_experience(
        task_id="t1", user_request="低分需求",
        review_verdict="rejected", review_score=50, skill_id=sid,
    )
    registered = mm.distill_skills(min_score=80)
    assert f"memory_{sid}" not in registered
    assert skill_registry.get_skill(f"memory_{sid}") is None