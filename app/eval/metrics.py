"""Agent 评估指标引擎。

从 TaskRecord + StageLog 记录中提取评估指标，对齐“基于决策轨迹、
工具准确率、自愈率的自动化监控闭环”的评估诉求。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.db.models import StageLog, TaskRecord

logger = logging.getLogger(__name__)

# ====================================================================== #
#  隐藏测试门禁阈值
# ====================================================================== #
# golden_pass_rate >= GOLDEN_PASS_THRESHOLD  → PASS
# GOLDEN_WARN_THRESHOLD <= golden_pass_rate < GOLDEN_PASS_THRESHOLD → WARN
# golden_pass_rate < GOLDEN_WARN_THRESHOLD   → BLOCK
GOLDEN_PASS_THRESHOLD = 0.8
GOLDEN_WARN_THRESHOLD = 0.5


def evaluate_gate(golden_pass_rate: float) -> str:
    """根据黄金通过率判定门禁：PASS / WARN / BLOCK。"""
    if golden_pass_rate >= GOLDEN_PASS_THRESHOLD:
        return "PASS"
    if golden_pass_rate >= GOLDEN_WARN_THRESHOLD:
        return "WARN"
    return "BLOCK"


# ====================================================================== #
#  数据类定义
# ====================================================================== #


@dataclass
class TaskMetrics:
    """单任务评估指标。"""

    task_id: str
    # 任务级指标
    success: bool                            # status == "done" 且无 error
    duration_seconds: float
    total_tokens: int
    # 阶段级指标（决策轨迹）
    stages: List[Dict[str, Any]]             # [{stage, duration_ms, tokens, has_error}]
    stage_count: int
    failed_stages: List[str]                 # 失败的阶段名列表
    # 评审指标
    review_verdict: str                      # approved/needs_changes/rejected/无
    review_score: int                        # 0-100
    review_retry_count: int                  # 评审重试次数
    review_issue_count: int
    review_critical_count: int
    # 测试指标
    test_status: str                         # passed/failed/partial/not_run
    test_total: int
    test_passed: int
    test_failed: int
    test_pass_rate: float                    # test_passed / max(test_total, 1)
    # 隐藏测试指标（黄金预期输出）
    golden_total: int = 0                    # 黄金用例总数
    golden_passed: int = 0                   # 黄金用例通过数
    golden_pass_rate: float = 0.0            # golden_passed / max(golden_total, 1)
    golden_gate: str = "SKIP"                # PASS / WARN / BLOCK / SKIP
    # 自愈指标
    self_healed: bool = False                # 有重试但最终成功
    heal_attempts: int = 0                   # 总重试次数


@dataclass
class EvalReport:
    """批量评估报告。"""

    # 统计范围
    total_tasks: int
    evaluated_tasks: int                     # 排除 pending/running
    # 成功率指标
    success_rate: float                      # 成功任务 / 已评估任务
    failure_rate: float
    # 评审指标
    avg_review_score: float
    review_pass_rate: float                  # verdict == approved 的比例
    avg_retry_count: float
    avg_issue_count: float
    # 测试指标
    avg_test_pass_rate: float
    test_pass_rate: float                    # test_status == passed 的比例
    # 隐藏测试指标
    avg_golden_pass_rate: float              # 有黄金用例任务的平均 golden_pass_rate
    golden_pass_rate: float                  # golden_gate == PASS 的比例
    golden_gate_counts: Dict[str, int]       # {PASS: n, WARN: n, BLOCK: n, SKIP: n}
    # 自愈指标
    self_heal_rate: float                    # self_healed / 已评估任务
    avg_heal_attempts: float
    # 效率指标
    avg_duration_seconds: float
    avg_tokens: float
    # 轨迹指标
    stage_error_rate: Dict[str, float]       # {stage_name: error_count / total}
    # 明细
    task_metrics: List[TaskMetrics] = field(default_factory=list)


# ====================================================================== #
#  指标计算函数
# ====================================================================== #


def _parse_summary(task: TaskRecord) -> Dict[str, Any]:
    """安全解析 task.summary_json，失败时返回空 dict 并记录告警。"""
    raw = getattr(task, "summary_json", "") or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("解析 summary_json 失败: task_id=%s", getattr(task, "task_id", "?"))
        return {}
    return data if isinstance(data, dict) else {}


def compute_task_metrics(
    task: TaskRecord,
    stage_logs: List[StageLog],
) -> TaskMetrics:
    """从单个任务记录 + 阶段日志计算评估指标。"""
    summary = _parse_summary(task)

    # --- 阶段级指标（决策轨迹）---
    stages: List[Dict[str, Any]] = [
        {
            "stage": s.stage,
            "duration_ms": s.duration_ms,
            "tokens": s.total_tokens,
            "has_error": bool(s.error),
        }
        for s in stage_logs
    ]
    failed_stages = [s.stage for s in stage_logs if s.error]

    # --- 评审指标 ---
    review_summary = summary.get("review_summary") or {}
    review_verdict = str(review_summary.get("verdict", "") or "")
    review_score = int(review_summary.get("score", 0) or 0)
    review_retry_count = int(review_summary.get("retry_count", 0) or 0)
    review_issue_count = int(review_summary.get("issue_count", 0) or 0)
    review_critical_count = int(review_summary.get("critical_count", 0) or 0)

    # --- 测试指标 ---
    test_summary = summary.get("test_summary") or {}
    test_status = str(test_summary.get("status", "") or "")
    test_total = int(test_summary.get("total", 0) or 0)
    test_passed = int(test_summary.get("passed", 0) or 0)
    test_failed = int(test_summary.get("failed", 0) or 0)
    test_pass_rate = test_passed / max(test_total, 1)

    # --- 隐藏测试指标（黄金预期输出）---
    hidden = summary.get("hidden_eval") or {}
    golden_total = int(hidden.get("golden_total", 0) or 0)
    golden_passed = int(hidden.get("golden_passed", 0) or 0)
    golden_pass_rate = golden_passed / max(golden_total, 1)
    golden_gate = evaluate_gate(golden_pass_rate) if golden_total > 0 else "SKIP"

    # --- 任务级指标 ---
    success = task.status == "done" and not task.error_message

    # --- 自愈指标：有评审重试但最终成功 ---
    self_healed = review_retry_count > 0 and success
    heal_attempts = review_retry_count

    return TaskMetrics(
        task_id=task.task_id,
        success=success,
        duration_seconds=float(task.duration_seconds or 0),
        total_tokens=int(task.total_tokens or 0),
        stages=stages,
        stage_count=len(stages),
        failed_stages=failed_stages,
        review_verdict=review_verdict,
        review_score=review_score,
        review_retry_count=review_retry_count,
        review_issue_count=review_issue_count,
        review_critical_count=review_critical_count,
        test_status=test_status,
        test_total=test_total,
        test_passed=test_passed,
        test_failed=test_failed,
        test_pass_rate=test_pass_rate,
        golden_total=golden_total,
        golden_passed=golden_passed,
        golden_pass_rate=golden_pass_rate,
        golden_gate=golden_gate,
        self_healed=self_healed,
        heal_attempts=heal_attempts,
    )


def compute_eval_report(
    task_metrics_list: List[TaskMetrics],
) -> EvalReport:
    """从多个任务指标聚合计算批量评估报告。"""
    total_tasks = len(task_metrics_list)
    # 所有任务都有明确的 success True/False，故 evaluated_tasks == total_tasks
    evaluated_tasks = total_tasks
    n = max(evaluated_tasks, 1)

    # 成功率指标
    success_rate = sum(1 for t in task_metrics_list if t.success) / n
    failure_rate = 1 - success_rate

    # 评审指标：avg_review_score 仅统计 review_score > 0 的任务
    scored = [t for t in task_metrics_list if t.review_score > 0]
    avg_review_score = (
        sum(t.review_score for t in scored) / max(len(scored), 1) if scored else 0.0
    )
    review_pass_rate = sum(1 for t in task_metrics_list if t.review_verdict == "approved") / n
    avg_retry_count = sum(t.review_retry_count for t in task_metrics_list) / n
    avg_issue_count = sum(t.review_issue_count for t in task_metrics_list) / n

    # 测试指标：avg_test_pass_rate 仅统计有测试用例的任务
    with_tests = [t for t in task_metrics_list if t.test_total > 0]
    avg_test_pass_rate = (
        sum(t.test_pass_rate for t in with_tests) / max(len(with_tests), 1)
        if with_tests
        else 0.0
    )
    test_pass_rate = sum(1 for t in task_metrics_list if t.test_status == "passed") / n

    # 隐藏测试指标：avg_golden_pass_rate 仅统计有黄金用例的任务
    with_golden = [t for t in task_metrics_list if t.golden_total > 0]
    avg_golden_pass_rate = (
        sum(t.golden_pass_rate for t in with_golden) / max(len(with_golden), 1)
        if with_golden
        else 0.0
    )
    golden_pass_rate = sum(1 for t in task_metrics_list if t.golden_gate == "PASS") / n
    golden_gate_counts: Dict[str, int] = {"PASS": 0, "WARN": 0, "BLOCK": 0, "SKIP": 0}
    for t in task_metrics_list:
        gate = t.golden_gate if t.golden_gate in golden_gate_counts else "SKIP"
        golden_gate_counts[gate] += 1

    # 自愈指标
    self_heal_rate = sum(1 for t in task_metrics_list if t.self_healed) / n
    avg_heal_attempts = sum(t.heal_attempts for t in task_metrics_list) / n

    # 效率指标
    avg_duration_seconds = sum(t.duration_seconds for t in task_metrics_list) / n
    avg_tokens = sum(t.total_tokens for t in task_metrics_list) / n

    # 轨迹指标：按阶段名聚合错误率
    stage_total: Dict[str, int] = {}
    stage_errors: Dict[str, int] = {}
    for t in task_metrics_list:
        for s in t.stages:
            name = str(s.get("stage", ""))
            if not name:
                continue
            stage_total[name] = stage_total.get(name, 0) + 1
            if s.get("has_error"):
                stage_errors[name] = stage_errors.get(name, 0) + 1
    stage_error_rate = {
        name: stage_errors.get(name, 0) / max(stage_total[name], 1)
        for name in stage_total
    }

    return EvalReport(
        total_tasks=total_tasks,
        evaluated_tasks=evaluated_tasks,
        success_rate=success_rate,
        failure_rate=failure_rate,
        avg_review_score=avg_review_score,
        review_pass_rate=review_pass_rate,
        avg_retry_count=avg_retry_count,
        avg_issue_count=avg_issue_count,
        avg_test_pass_rate=avg_test_pass_rate,
        test_pass_rate=test_pass_rate,
        avg_golden_pass_rate=avg_golden_pass_rate,
        golden_pass_rate=golden_pass_rate,
        golden_gate_counts=golden_gate_counts,
        self_heal_rate=self_heal_rate,
        avg_heal_attempts=avg_heal_attempts,
        avg_duration_seconds=avg_duration_seconds,
        avg_tokens=avg_tokens,
        stage_error_rate=stage_error_rate,
        task_metrics=list(task_metrics_list),
    )


__all__ = [
    "TaskMetrics",
    "EvalReport",
    "compute_task_metrics",
    "compute_eval_report",
    "evaluate_gate",
    "GOLDEN_PASS_THRESHOLD",
    "GOLDEN_WARN_THRESHOLD",
]
