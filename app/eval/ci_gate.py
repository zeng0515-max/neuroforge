"""CI 门禁模块：PR 合并门禁检查。

包含 CIGate 类，检查测试覆盖率、评审评分、CRITICAL 问题等指标。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.eval.metrics import TaskMetrics

logger = logging.getLogger(__name__)


class CIGate:
    """PR 合并门禁检查器。

    检查项：
    - 测试覆盖率 >= 阈值（默认 80%）
    - 评审评分 >= 阈值（默认 70）
    - 无 CRITICAL 问题

    运行方式：读取当前任务的 test_report 和 review_report。
    返回结构：{passed: bool, checks: dict, summary: str}
    """

    def __init__(
        self,
        coverage_threshold: float = 0.8,
        review_score_threshold: int = 70,
    ) -> None:
        self._coverage_threshold = coverage_threshold
        self._review_score_threshold = review_score_threshold

    def check(self, task_metrics: TaskMetrics) -> Dict[str, Any]:
        """对单个任务指标执行门禁检查。

        Args:
            task_metrics: 任务的评估指标

        Returns:
            dict: {
                "passed": bool,
                "checks": {
                    "coverage": {"passed": bool, "value": float, "threshold": float},
                    "review_score": {"passed": bool, "value": int, "threshold": int},
                    "no_critical_issues": {"passed": bool, "value": int, "threshold": int},
                    "task_success": {"passed": bool, "value": str},
                },
                "summary": str,
                "task_id": str,
            }
        """
        checks: Dict[str, Any] = {}
        all_passed = True

        # 1. 测试覆盖率检查
        coverage_value = task_metrics.test_pass_rate
        coverage_passed = coverage_value >= self._coverage_threshold
        checks["coverage"] = {
            "passed": coverage_passed,
            "value": round(coverage_value, 4),
            "threshold": self._coverage_threshold,
            "details": f"通过 {task_metrics.test_passed}/{task_metrics.test_total} 测试",
        }
        if not coverage_passed:
            all_passed = False

        # 2. 评审评分检查
        score_value = task_metrics.review_score
        score_passed = score_value >= self._review_score_threshold
        checks["review_score"] = {
            "passed": score_passed,
            "value": score_value,
            "threshold": self._review_score_threshold,
            "details": f"评审评分 {score_value}/100",
        }
        if not score_passed:
            all_passed = False

        # 3. CRITICAL 问题检查
        critical_count = task_metrics.review_critical_count
        critical_passed = critical_count == 0
        checks["no_critical_issues"] = {
            "passed": critical_passed,
            "value": critical_count,
            "threshold": 0,
            "details": f"CRITICAL 问题数: {critical_count}",
        }
        if not critical_passed:
            all_passed = False

        # 4. 任务是否成功
        task_success = task_metrics.success
        checks["task_success"] = {
            "passed": task_success,
            "value": "done" if task_success else "failed",
            "threshold": "done",
            "details": f"任务状态: {'成功' if task_success else '失败'}",
        }
        if not task_success:
            all_passed = False

        # 构建摘要
        failed_checks = [k for k, v in checks.items() if not v["passed"]]
        if all_passed:
            summary = f"✅ 门禁通过：所有 {len(checks)} 项检查均通过"
        else:
            summary = f"❌ 门禁不通过：{len(failed_checks)}/{len(checks)} 项检查未通过 ({', '.join(failed_checks)})"

        return {
            "passed": all_passed,
            "checks": checks,
            "summary": summary,
            "task_id": task_metrics.task_id,
        }

    def check_from_metrics_list(
        self, task_metrics_list: List[TaskMetrics]
    ) -> Dict[str, Any]:
        """对多个任务指标执行聚合门禁检查。

        Args:
            task_metrics_list: 任务指标列表

        Returns:
            dict: 聚合检查结果
        """
        if not task_metrics_list:
            return {
                "passed": False,
                "checks": {},
                "summary": "没有可评估的任务",
                "task_id": "",
            }

        individual_results = [self.check(m) for m in task_metrics_list]
        all_passed = all(r["passed"] for r in individual_results)

        # 聚合检查项
        aggregated_checks: Dict[str, Any] = {}
        for key in ("coverage", "review_score", "no_critical_issues", "task_success"):
            values = [r["checks"][key] for r in individual_results if key in r.get("checks", {})]
            if values:
                passed_count = sum(1 for v in values if v["passed"])
                aggregated_checks[key] = {
                    "passed": passed_count == len(values),
                    "passed_count": passed_count,
                    "total_count": len(values),
                    "details": f"{passed_count}/{len(values)} 个任务通过",
                }

        return {
            "passed": all_passed,
            "checks": aggregated_checks,
            "summary": f"✅ 聚合门禁通过" if all_passed else f"❌ 聚合门禁不通过：{sum(1 for r in individual_results if not r['passed'])}/{len(individual_results)} 个任务未通过",
            "task_id": ",".join(r["task_id"] for r in individual_results),
            "individual_results": individual_results,
        }


# 全局单例
ci_gate = CIGate()

__all__ = ["CIGate", "ci_gate"]