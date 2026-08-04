from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.tools.rag_service import rag
from app.tools.skill_registry import Skill, skill_registry

logger = logging.getLogger(__name__)


class MemoryManager:
    """长期记忆管理器：跨任务经验存储与检索。

    设计要点：
    - 复用现有 SQLite DB（settings.db.path）存结构化经验，用原生 sqlite3 连接，
      与 SQLAlchemy ORM 分离，不影响现有模型与测试。
    - 复用现有 RAG 单例做语义检索，RAG 失败时降级为纯 SQLite 关键词检索，
      不影响主流程。
    """

    def __init__(self, db_path: str = None):
        self._db_path = db_path or settings.db.path
        self._init_table()
        self._rag = rag  # 复用 RAG 做语义检索

    def _init_table(self):
        """创建 experience 表（如果不存在）"""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experience (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    user_request TEXT NOT NULL,
                    review_verdict TEXT DEFAULT '',
                    review_score INTEGER DEFAULT 0,
                    review_issue_count INTEGER DEFAULT 0,
                    test_status TEXT DEFAULT '',
                    test_pass_rate REAL DEFAULT 0.0,
                    skill_id TEXT DEFAULT '',
                    code_files TEXT DEFAULT '[]',
                    error_message TEXT DEFAULT '',
                    quality_score INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experience_request ON experience(user_request)"
            )
            # 迁移：老表缺少 quality_score 列时补列（避免破坏已有 DB）
            cols = [r[1] for r in conn.execute("PRAGMA table_info(experience)").fetchall()]
            if "quality_score" not in cols:
                conn.execute("ALTER TABLE experience ADD COLUMN quality_score INTEGER DEFAULT 0")
            conn.commit()
        finally:
            conn.close()

    def save_experience(
        self,
        task_id: str,
        user_request: str,
        review_verdict: str = "",
        review_score: int = 0,
        review_issue_count: int = 0,
        test_status: str = "",
        test_pass_rate: float = 0.0,
        skill_id: str = "",
        code_files: list = None,
        error_message: str = "",
    ) -> int:
        """保存一条任务经验，返回经验记录 id。

        同时写入 SQLite（结构化）与 RAG（语义检索），RAG 失败不影响主流程。
        若启用去重（memory_dedup_enabled）且找到高度相似的历史需求，
        则更新旧记录而非新增（按质量分保留更优者），避免记录膨胀。
        """
        quality_score = self._compute_quality_score(
            review_score, review_verdict, test_status, test_pass_rate
        )

        # 去重：命中相似历史需求时更新旧记录
        dup = self._find_duplicate(user_request)
        if dup is not None:
            old_score = dup.get("quality_score", 0)
            if quality_score >= old_score:
                self._update_experience(
                    dup["id"], task_id, user_request, review_verdict, review_score,
                    review_issue_count, test_status, test_pass_rate, skill_id,
                    code_files, error_message, quality_score,
                )
                logger.info(
                    "经验去重：更新已有记录 id=%d（new_score=%d >= old_score=%d）",
                    dup["id"], quality_score, old_score,
                )
            else:
                logger.info(
                    "经验去重：新经验质量较低，保留旧记录 id=%d（new_score=%d < old_score=%d）",
                    dup["id"], quality_score, old_score,
                )
            return dup["id"]

        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """INSERT INTO experience
                   (task_id, user_request, review_verdict, review_score,
                    review_issue_count, test_status, test_pass_rate,
                    skill_id, code_files, error_message, quality_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    user_request,
                    review_verdict,
                    review_score,
                    review_issue_count,
                    test_status,
                    test_pass_rate,
                    skill_id,
                    json.dumps(code_files or [], ensure_ascii=False),
                    error_message,
                    quality_score,
                ),
            )
            conn.commit()
            experience_id = cursor.lastrowid
        finally:
            conn.close()

        # 同时存入 RAG 做语义检索
        try:
            experience_text = (
                f"需求: {user_request}\n"
                f"评审: {review_verdict}({review_score}分)\n"
                f"测试: {test_status}(通过率{test_pass_rate:.0%})"
            )
            if error_message:
                experience_text += f"\n错误: {error_message}"
            self._rag.add_texts(
                texts=[experience_text],
                metadatas=[
                    {
                        "source": "experience",
                        "task_id": task_id,
                        "experience_id": str(experience_id),
                    }
                ],
            )
        except Exception as e:
            logger.warning("经验存入 RAG 失败（不影响主流程）: %s", e)

        logger.info(
            "经验已保存: task_id=%s, verdict=%s, score=%d",
            task_id, review_verdict, review_score,
        )
        return experience_id

    def _find_duplicate(self, user_request: str) -> Optional[Dict[str, Any]]:
        """查找与指定 user_request 高度相似的历史经验。

        判定顺序（任一命中即视为重复）：
        1. SQLite 精确匹配（user_request 完全相同）
        2. SQLite 去空白后匹配（容忍首尾/内部空格差异）
        3. RAG 语义相似度 >= memory_dedup_similarity_threshold

        返回命中记录的 dict；未命中返回 None。去重开关关闭或空请求时直接返回 None。
        """
        if not user_request or not settings.memory.memory_dedup_enabled:
            return None

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM experience WHERE user_request = ? ORDER BY id LIMIT 1",
                (user_request,),
            ).fetchone()
            if row:
                return dict(row)
            row = conn.execute(
                "SELECT * FROM experience WHERE REPLACE(user_request, ' ', '') = "
                "REPLACE(?, ' ', '') ORDER BY id LIMIT 1",
                (user_request,),
            ).fetchone()
            if row:
                return dict(row)
        finally:
            conn.close()

        # RAG 语义相似度去重
        try:
            docs = self._rag.query(
                query_text=user_request, top_k=1, where={"source": "experience"}
            )
            if docs:
                doc = docs[0]
                if getattr(doc, "score", 0) >= settings.memory.memory_dedup_similarity_threshold:
                    exp_id = (getattr(doc, "metadata", {}) or {}).get("experience_id")
                    if exp_id:
                        conn = sqlite3.connect(self._db_path)
                        conn.row_factory = sqlite3.Row
                        try:
                            row = conn.execute(
                                "SELECT * FROM experience WHERE id = ?", (int(exp_id),)
                            ).fetchone()
                            if row:
                                return dict(row)
                        finally:
                            conn.close()
        except Exception as e:
            logger.debug("RAG 去重检索失败（忽略）: %s", e)
        return None

    def _update_experience(
        self,
        exp_id: int,
        task_id: str,
        user_request: str,
        review_verdict: str,
        review_score: int,
        review_issue_count: int,
        test_status: str,
        test_pass_rate: float,
        skill_id: str,
        code_files: list,
        error_message: str,
        quality_score: int,
    ) -> None:
        """覆盖更新一条已有经验记录（去重时用）。"""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """UPDATE experience SET
                     task_id=?, user_request=?, review_verdict=?, review_score=?,
                     review_issue_count=?, test_status=?, test_pass_rate=?,
                     skill_id=?, code_files=?, error_message=?, quality_score=?
                   WHERE id=?""",
                (
                    task_id, user_request, review_verdict, review_score,
                    review_issue_count, test_status, test_pass_rate,
                    skill_id, json.dumps(code_files or [], ensure_ascii=False),
                    error_message, quality_score, exp_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _compute_quality_score(
        review_score: int,
        review_verdict: str = "",
        test_status: str = "",
        test_pass_rate: float = 0.0,
    ) -> int:
        """启发式质量分：以评审分为基础，叠加评审/测试状态修正。

        规则（无需 LLM，纯规则）：
        - 基础 = review_score（0-100）
        - 评审 rejected 扣 30
        - 测试 failed 扣 20
        - 测试通过率越高加分越多（最高 +20）
        结果裁剪到 [0, 100]。
        """
        score = float(review_score or 0)
        if review_verdict == "rejected":
            score -= 30
        if test_status == "failed":
            score -= 20
        if test_pass_rate and test_pass_rate > 0:
            score += min(test_pass_rate * 20, 20)
        return max(0, min(100, int(round(score))))

    def recall_experience(
        self,
        query: str,
        limit: int = 5,
        filter_low_quality: bool = None,
    ) -> list:
        """检索相关历史经验。

        双通道检索：RAG 语义检索 + SQLite 关键词检索，合并后截断返回。
        任一通道失败均降级为另一通道，不影响主流程。

        反馈精炼：
        - 每条结果附 quality_score（启发式质量分）与 is_low_quality（是否低质量）
        - filter_low_quality=True 时直接丢弃低质量经验（默认由配置控制，默认 False）
        - 结果按质量分降序排列，让高质量经验优先
        """
        if filter_low_quality is None:
            filter_low_quality = settings.memory.memory_filter_low_quality

        results: List[Dict[str, Any]] = []
        rag_task_ids: List[str] = []

        # 1. RAG 语义检索
        try:
            docs = self._rag.query(
                query_text=query, top_k=limit, where={"source": "experience"}
            )
            for doc in docs:
                meta = getattr(doc, "metadata", {}) or {}
                tid = meta.get("task_id", "")
                results.append(
                    {
                        "source": "rag",
                        "content": getattr(doc, "content", str(doc)),
                        "score": getattr(doc, "score", 0),
                        "task_id": tid,
                        "quality_score": None,
                        "is_low_quality": False,
                    }
                )
                if tid:
                    rag_task_ids.append(tid)
        except Exception as e:
            logger.warning("RAG 经验检索失败: %s", e)

        # 2. SQLite 关键词检索（补充 RAG 可能的遗漏）
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """SELECT * FROM experience
                       WHERE user_request LIKE ?
                       ORDER BY review_score DESC, created_at DESC
                       LIMIT ?""",
                    (f"%{query}%", limit),
                ).fetchall()
                for row in rows:
                    qs = row["quality_score"]
                    results.append(
                        {
                            "source": "sqlite",
                            "task_id": row["task_id"],
                            "user_request": row["user_request"],
                            "review_verdict": row["review_verdict"],
                            "review_score": row["review_score"],
                            "test_status": row["test_status"],
                            "test_pass_rate": row["test_pass_rate"],
                            "skill_id": row["skill_id"],
                            "code_files": json.loads(row["code_files"]),
                            "error_message": row["error_message"],
                            "quality_score": qs,
                            "is_low_quality": self._is_low_quality(qs),
                        }
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.warning("SQLite 经验检索失败: %s", e)

        # 3. 精炼：用 SQLite 质量分回填 RAG 结果（单次查询，避免 N 次查询）
        if rag_task_ids:
            qmap = self._fetch_quality_map(rag_task_ids)
            for r in results:
                if r["source"] == "rag" and r["task_id"] in qmap:
                    r["quality_score"] = qmap[r["task_id"]]["quality_score"]
                    r["is_low_quality"] = qmap[r["task_id"]]["is_low_quality"]

        # 4. 过滤低质量 + 高质量优先排序
        if filter_low_quality:
            results = [r for r in results if not r.get("is_low_quality")]
        results.sort(
            key=lambda r: r.get("quality_score")
            if r.get("quality_score") is not None else -1,
            reverse=True,
        )

        return results[:limit]

    def _is_low_quality(self, quality_score) -> bool:
        """判断质量分是否低于低质量阈值。"""
        try:
            return int(quality_score or 0) < settings.memory.memory_min_quality_score
        except (TypeError, ValueError):
            return False

    def _fetch_quality_map(self, task_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """按 task_id 批量回填质量信息（单次 SQL 查询）。"""
        if not task_ids:
            return {}
        placeholders = ",".join("?" * len(task_ids))
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        result: Dict[str, Dict[str, Any]] = {}
        try:
            rows = conn.execute(
                f"SELECT task_id, quality_score FROM experience "
                f"WHERE task_id IN ({placeholders})",
                task_ids,
            ).fetchall()
            for row in rows:
                qs = row["quality_score"]
                result[row["task_id"]] = {
                    "quality_score": qs,
                    "is_low_quality": self._is_low_quality(qs),
                }
        finally:
            conn.close()
        return result

    def distill_skills(self, min_score: int = None) -> List[str]:
        """从高评分经验中归纳出可复用技能，写入内存技能注册表。

        启发式（不调用 LLM，token 经济）：
        - 取 review_score >= min_score（默认 memory_quality_threshold）的经验
        - 按 skill_id 分组，从 user_request 提取关键词与描述
        - 生成 Skill 条目并 register_skill 到内存注册表（不写 YAML）
        返回本次新注册的技能 id 列表；已存在（memory_*）的技能跳过。
        """
        if min_score is None:
            min_score = settings.memory.memory_quality_threshold

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT * FROM experience
                   WHERE review_score >= ?
                   ORDER BY review_score DESC, created_at DESC""",
                (min_score,),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return []

        # 按 skill_id 分组
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            sid = r["skill_id"] or "general"
            groups.setdefault(sid, []).append(dict(r))

        registered: List[str] = []
        for sid, exps in groups.items():
            skill_id = f"memory_{sid}"
            if skill_registry.get_skill(skill_id):
                continue  # 已沉淀过，跳过
            name, keywords, description = self._build_skill_from_experiences(exps)
            avg_score = sum(e["review_score"] for e in exps) / len(exps)
            skill = Skill(
                id=skill_id,
                name=name,
                keywords=keywords,
                system_prompt=f"复用以下高评分经验解决【{name}】类需求，优先遵循其已验证的写法。",
                code_template="",
                validation_rules=[],
                best_practices=[
                    f"基于 {len(exps)} 条高评分经验沉淀，平均评审分 {avg_score:.0f}。",
                    "优先复用 code_files 中已验证的代码结构与命名。",
                    "避免重复踩坑：结合历史 error_message 做边界防护。",
                ],
            )
            if skill_registry.register_skill(skill):
                logger.info("技能沉淀：%s 由 %d 条经验归纳", skill_id, len(exps))
                registered.append(skill_id)
        return registered

    @staticmethod
    def _build_skill_from_experiences(
        exps: List[Dict[str, Any]],
    ) -> tuple:
        """从一组高评分经验启发式归纳技能的名称、关键词与描述。"""
        # 合并所有 user_request 提取关键词
        keyword_counter: Dict[str, int] = {}
        for e in exps:
            for kw in MemoryManager._extract_keywords(e["user_request"]):
                keyword_counter[kw] = keyword_counter.get(kw, 0) + 1
        # 按出现频次排序，取前 N 个
        keywords = [k for k, _ in sorted(
            keyword_counter.items(), key=lambda kv: kv[1], reverse=True)]
        keywords = keywords[:6]
        # 名称：取频次最高的关键词作为识别标签
        name = keywords[0] if keywords else exps[0]["skill_id"] or "通用"
        # 描述：取第一条经验的需求
        description = exps[0]["user_request"] or name
        return name, keywords, description

    @staticmethod
    def _extract_keywords(text: str, top_n: int = 8) -> List[str]:
        """从文本中提取关键词（中文词 + 英文/数字 token），纯规则、去重保序。"""
        if not text:
            return []
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{1,}", text)
        seen: set = set()
        out: List[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out[:top_n]

    def get_stats(self) -> dict:
        """获取记忆统计：总经验数、平均评审分、通过数、通过率"""
        conn = sqlite3.connect(self._db_path)
        try:
            total = conn.execute("SELECT COUNT(*) FROM experience").fetchone()[0]
            avg_score = (
                conn.execute(
                    "SELECT AVG(review_score) FROM experience WHERE review_score > 0"
                ).fetchone()[0]
                or 0
            )
            approved = conn.execute(
                "SELECT COUNT(*) FROM experience WHERE review_verdict = 'approved'"
            ).fetchone()[0]
        finally:
            conn.close()
        return {
            "total_experiences": total,
            "avg_review_score": round(avg_score, 2),
            "approved_count": approved,
            "approval_rate": round(approved / max(total, 1), 4),
        }


# 全局单例
memory = MemoryManager()

__all__ = ["memory", "MemoryManager"]
