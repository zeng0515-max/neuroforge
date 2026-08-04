"""Agent 评估执行器：从数据库读取任务记录与阶段日志，调用指标引擎计算评估报告。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.db.sqlite_client import db
from app.eval.metrics import (
    EvalReport,
    TaskMetrics,
    compute_eval_report,
    compute_task_metrics,
)

logger = logging.getLogger(__name__)


class Evaluator:
    """Agent 评估执行器：从数据库读取任务，计算评估指标。"""

    def __init__(self, db_client=None):
        # 允许注入自定义 DB 客户端，默认使用全局单例
        self._db = db_client or db

    # ------------------------------------------------------------------ #
    #  单任务评估
    # ------------------------------------------------------------------ #
    def evaluate_task(self, task_id: str) -> Optional[TaskMetrics]:
        """评估单个任务。

        流程：
            1. 从 DB 获取 task，不存在则返回 None
            2. 从 DB 获取 stage_logs（可能为空）
            3. 调用 compute_task_metrics 计算指标
            4. 返回 TaskMetrics
        """
        # 1. 获取任务
        try:
            task = self._db.get_task(task_id)
        except Exception as e:
            logger.exception("读取任务 %s 失败: %s", task_id, e)
            return None
        if task is None:
            logger.warning("任务 %s 不存在", task_id)
            return None

        # 2. 获取阶段日志（可能为空列表）
        try:
            stage_logs = self._db.list_stage_logs(task_id)
        except Exception as e:
            logger.exception("读取任务 %s 阶段日志失败: %s", task_id, e)
            stage_logs = []

        # 3. 计算指标
        return compute_task_metrics(task, stage_logs)

    # ------------------------------------------------------------------ #
    #  批量评估
    # ------------------------------------------------------------------ #
    def evaluate_batch(self, limit: int = 100, offset: int = 0) -> EvalReport:
        """批量评估任务，返回聚合报告。

        流程：
            1. 从 DB 获取任务列表（多取一些以补偿过滤）
            2. 过滤掉 pending/running 状态的任务，只评估 done/failed
            3. 对每个任务调用 compute_task_metrics
            4. 调用 compute_eval_report 聚合
            5. 返回 EvalReport
        """
        # 获取比 limit 更多任务来补偿过滤
        try:
            tasks = self._db.list_tasks(limit=limit * 2, offset=offset)
        except Exception as e:
            logger.exception("读取任务列表失败: %s", e)
            return compute_eval_report([])

        # 过滤已完成任务（status in done/failed）
        completed = [t for t in tasks if t.status in ("done", "failed")]
        completed = completed[:limit]  # 不超过 limit

        metrics_list: List[TaskMetrics] = self._collect_metrics(completed)
        return compute_eval_report(metrics_list)

    # ------------------------------------------------------------------ #
    #  最近任务评估
    # ------------------------------------------------------------------ #
    def evaluate_recent(self, n: int = 20) -> EvalReport:
        """评估最近 N 个已完成任务。

        获取最近 n*3 个任务（因为要过滤 pending/running），取前 n 个已完成的。
        注意：db.list_tasks 返回按 started_at 倒序排列的任务列表。
        """
        try:
            tasks = self._db.list_tasks(limit=n * 3, offset=0)
        except Exception as e:
            logger.exception("读取最近任务列表失败: %s", e)
            return compute_eval_report([])

        completed = [t for t in tasks if t.status in ("done", "failed")]
        completed = completed[:n]

        metrics_list: List[TaskMetrics] = self._collect_metrics(completed)
        return compute_eval_report(metrics_list)

    # ------------------------------------------------------------------ #
    #  轻量级摘要
    # ------------------------------------------------------------------ #
    def get_summary(self) -> Dict[str, Any]:
        """轻量级摘要：不加载明细，只用 DB 聚合。"""
        try:
            stat = self._db.stat()
        except Exception as e:
            logger.exception("读取统计信息失败: %s", e)
            return {"total_tasks": 0, "total_tokens": 0, "total_files": 0}
        return {
            "total_tasks": stat.get("total_tasks", 0),
            "total_tokens": stat.get("total_tokens", 0),
            "total_files": stat.get("total_files", 0),
        }

    # ------------------------------------------------------------------ #
    #  内部工具
    # ------------------------------------------------------------------ #
    def _collect_metrics(self, tasks) -> List[TaskMetrics]:
        """对一批任务计算指标，跳过失败的任务并记录告警。"""
        metrics_list: List[TaskMetrics] = []
        for task in tasks:
            try:
                stage_logs = self._db.list_stage_logs(task.task_id)
                m = compute_task_metrics(task, stage_logs)
                metrics_list.append(m)
            except Exception as e:
                logger.warning("评估任务 %s 失败: %s", task.task_id, e)
        return metrics_list


# 全局单例
evaluator = Evaluator()

__all__ = ["Evaluator", "evaluator"]
