"""Agent 评估 API：提供任务质量、决策轨迹、自愈率等评估指标。"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict

from fastapi import APIRouter, HTTPException, Query

from app.eval.ci_gate import ci_gate
from app.eval.evaluator import evaluator

if TYPE_CHECKING:
    # 仅用于类型提示，运行时无需导入（避免循环依赖 / 模块未就绪问题）
    from app.eval.metrics import EvalReport, TaskMetrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/eval", tags=["eval"])


# ====================================================================== #
#  评估端点
# ====================================================================== #
@router.get("/summary")
async def get_eval_summary() -> Dict[str, Any]:
    """评估摘要：任务总数、Token 消耗、文件总数"""
    return evaluator.get_summary()


@router.get("/batch")
async def eval_batch(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    """批量评估已完成任务，返回聚合报告"""
    report = evaluator.evaluate_batch(limit=limit, offset=offset)
    return _report_to_dict(report)


@router.get("/recent")
async def eval_recent(
    n: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """评估最近 N 个已完成任务"""
    report = evaluator.evaluate_recent(n=n)
    return _report_to_dict(report)


@router.get("/task/{task_id}")
async def eval_task(task_id: str) -> Dict[str, Any]:
    """评估单个任务"""
    metrics = evaluator.evaluate_task(task_id)
    if metrics is None:
        raise HTTPException(404, f"任务不存在: {task_id}")
    return _metrics_to_dict(metrics)


# ====================================================================== #
#  辅助函数：dataclass -> dict
# ====================================================================== #
def _metrics_to_dict(m: "TaskMetrics") -> Dict[str, Any]:
    """TaskMetrics 转 dict"""
    return asdict(m)


def _report_to_dict(r: "EvalReport") -> Dict[str, Any]:
    """EvalReport 转 dict（不含 task_metrics 明细，避免响应过大）"""
    return {
        "total_tasks": r.total_tasks,
        "evaluated_tasks": r.evaluated_tasks,
        "success_rate": round(r.success_rate, 4),
        "failure_rate": round(r.failure_rate, 4),
        "avg_review_score": round(r.avg_review_score, 2),
        "review_pass_rate": round(r.review_pass_rate, 4),
        "avg_retry_count": round(r.avg_retry_count, 2),
        "avg_issue_count": round(r.avg_issue_count, 2),
        "avg_test_pass_rate": round(r.avg_test_pass_rate, 4),
        "test_pass_rate": round(r.test_pass_rate, 4),
        "avg_golden_pass_rate": round(r.avg_golden_pass_rate, 4),
        "golden_pass_rate": round(r.golden_pass_rate, 4),
        "golden_gate_counts": r.golden_gate_counts,
        "self_heal_rate": round(r.self_heal_rate, 4),
        "avg_heal_attempts": round(r.avg_heal_attempts, 2),
        "avg_duration_seconds": round(r.avg_duration_seconds, 2),
        "avg_tokens": round(r.avg_tokens, 2),
        "stage_error_rate": {k: round(v, 4) for k, v in r.stage_error_rate.items()},
        "task_count": len(r.task_metrics),
}


# ====================================================================== #
#  CI 门禁
# ====================================================================== #
@router.post("/ci-check")
async def ci_check(task_id: str = Query(..., description="任务 ID")) -> Dict[str, Any]:
    """对指定任务执行 CI 门禁检查。

    检查项：
    - 测试覆盖率 >= 80%
    - 评审评分 >= 70
    - 无 CRITICAL 问题
    - 任务执行成功
    """
    metrics = evaluator.evaluate_task(task_id)
    if metrics is None:
        raise HTTPException(404, f"任务不存在: {task_id}")
    return ci_gate.check(metrics)
