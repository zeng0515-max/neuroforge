"""可观测性与监控告警模块。

包含：
- MetricsCollector：收集 LLM 调用、流水线、工具调用等指标
- AlertManager：基于规则的告警管理与历史存储
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ====================================================================== #
#  告警级别与规则
# ====================================================================== #

class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class AlertRule:
    """告警规则定义。"""
    name: str
    description: str
    level: AlertLevel
    enabled: bool = True


@dataclass
class Alert:
    """单条告警记录。"""
    rule_name: str
    level: AlertLevel
    message: str
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


# ====================================================================== #
#  指标收集器
# ====================================================================== #

class MetricsCollector:
    """轻量级指标收集器，使用 dict + threading.Lock 保证并发安全。

    收集三类指标：
    - LLM 调用指标：total_calls, total_tokens, total_cost, calls_by_model, calls_by_agent
    - 流水线指标：total_pipelines, success_count, failure_count, avg_duration
    - 工具调用指标：total_tool_calls, tool_calls_by_name, tool_failures
    """

    def __init__(self, enabled: bool = True, retention_days: int = 30) -> None:
        self._enabled = enabled
        self._retention_days = retention_days
        self._lock = threading.Lock()

        # LLM 调用指标
        self.llm_total_calls: int = 0
        self.llm_total_tokens: int = 0
        self.llm_total_cost: float = 0.0
        self.llm_calls_by_model: Dict[str, int] = defaultdict(int)
        self.llm_calls_by_agent: Dict[str, int] = defaultdict(int)
        self.llm_total_duration_ms: float = 0.0

        # 流水线指标
        self.pipeline_total: int = 0
        self.pipeline_success: int = 0
        self.pipeline_failure: int = 0
        self.pipeline_total_duration: float = 0.0

        # 工具调用指标
        self.tool_total_calls: int = 0
        self.tool_calls_by_name: Dict[str, int] = defaultdict(int)
        self.tool_failures: int = 0
        self.tool_failures_by_name: Dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ #
    #  LLM 调用埋点
    # ------------------------------------------------------------------ #
    def record_llm_call(
        self,
        *,
        model: str = "",
        tokens: int = 0,
        cost: float = 0.0,
        duration_ms: float = 0.0,
        agent: str = "",
    ) -> None:
        """记录一次 LLM 调用。"""
        if not self._enabled:
            return
        with self._lock:
            self.llm_total_calls += 1
            self.llm_total_tokens += tokens
            self.llm_total_cost += cost
            self.llm_total_duration_ms += duration_ms
            if model:
                self.llm_calls_by_model[model] += 1
            if agent:
                self.llm_calls_by_agent[agent] += 1

    # ------------------------------------------------------------------ #
    #  流水线埋点
    # ------------------------------------------------------------------ #
    def record_pipeline(
        self,
        *,
        success: bool = True,
        duration_seconds: float = 0.0,
    ) -> None:
        """记录一次流水线执行结果。"""
        if not self._enabled:
            return
        with self._lock:
            self.pipeline_total += 1
            if success:
                self.pipeline_success += 1
            else:
                self.pipeline_failure += 1
            self.pipeline_total_duration += duration_seconds

    # ------------------------------------------------------------------ #
    #  工具调用埋点
    # ------------------------------------------------------------------ #
    def record_tool_call(
        self,
        *,
        tool_name: str = "",
        success: bool = True,
    ) -> None:
        """记录一次工具调用。"""
        if not self._enabled:
            return
        with self._lock:
            self.tool_total_calls += 1
            if tool_name:
                self.tool_calls_by_name[tool_name] += 1
            if not success:
                self.tool_failures += 1
                if tool_name:
                    self.tool_failures_by_name[tool_name] += 1

    # ------------------------------------------------------------------ #
    #  快照导出
    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        """导出当前指标快照。"""
        with self._lock:
            llm_avg_duration = 0.0
            if self.llm_total_calls > 0:
                llm_avg_duration = self.llm_total_duration_ms / self.llm_total_calls

            pipeline_avg_duration = 0.0
            if self.pipeline_total > 0:
                pipeline_avg_duration = self.pipeline_total_duration / self.pipeline_total

            return {
                "llm": {
                    "total_calls": self.llm_total_calls,
                    "total_tokens": self.llm_total_tokens,
                    "total_cost": round(self.llm_total_cost, 6),
                    "avg_duration_ms": round(llm_avg_duration, 2),
                    "calls_by_model": dict(self.llm_calls_by_model),
                    "calls_by_agent": dict(self.llm_calls_by_agent),
                },
                "pipeline": {
                    "total": self.pipeline_total,
                    "success": self.pipeline_success,
                    "failure": self.pipeline_failure,
                    "success_rate": round(
                        self.pipeline_success / max(self.pipeline_total, 1), 4
                    ),
                    "avg_duration_seconds": round(pipeline_avg_duration, 2),
                },
                "tool": {
                    "total_calls": self.tool_total_calls,
                    "calls_by_name": dict(self.tool_calls_by_name),
                    "failures": self.tool_failures,
                    "failures_by_name": dict(self.tool_failures_by_name),
                    "failure_rate": round(
                        self.tool_failures / max(self.tool_total_calls, 1), 4
                    ),
                },
                "enabled": self._enabled,
                "retention_days": self._retention_days,
            }


# ====================================================================== #
#  告警管理器
# ====================================================================== #

class AlertManager:
    """基于规则的告警管理器。

    支持：
    - 可配置告警规则（error_rate_threshold, cost_threshold, latency_threshold）
    - 告警级别：INFO, WARNING, CRITICAL
    - 告警通知方式：日志记录 + API 端点查询
    - 告警历史存储在内存环形缓冲区（最多 1000 条）
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._lock = threading.Lock()
        self._max_history = max_history
        self._history: List[Alert] = []
        self._next_index: int = 0

        # 默认告警规则阈值
        self.error_rate_threshold: float = 0.3  # 30% 错误率触发 WARNING
        self.cost_threshold: float = 10.0        # 10 美元触发 WARNING
        self.latency_threshold: float = 30.0     # 30 秒平均延迟触发 WARNING

    # ------------------------------------------------------------------ #
    #  规则配置
    # ------------------------------------------------------------------ #
    def get_rules(self) -> List[Dict[str, Any]]:
        """返回当前告警规则列表。"""
        return [
            {
                "name": "error_rate",
                "description": "工具调用错误率超过阈值",
                "level": AlertLevel.WARNING.value,
                "threshold": self.error_rate_threshold,
                "enabled": True,
            },
            {
                "name": "cost_threshold",
                "description": "LLM 累计费用超过阈值",
                "level": AlertLevel.WARNING.value,
                "threshold": self.cost_threshold,
                "enabled": True,
            },
            {
                "name": "latency_threshold",
                "description": "LLM 平均调用延迟超过阈值（秒）",
                "level": AlertLevel.WARNING.value,
                "threshold": self.latency_threshold,
                "enabled": True,
            },
        ]

    # ------------------------------------------------------------------ #
    #  告警评估（基于指标快照自动触发）
    # ------------------------------------------------------------------ #
    def evaluate(self, metrics: Dict[str, Any]) -> List[Alert]:
        """根据当前指标快照评估告警规则，返回新触发的告警列表。"""
        new_alerts: List[Alert] = []
        llm = metrics.get("llm", {})
        tool = metrics.get("tool", {})

        # 1. 错误率检查
        tool_failure_rate = tool.get("failure_rate", 0.0)
        if tool_failure_rate > self.error_rate_threshold:
            level = (
                AlertLevel.CRITICAL
                if tool_failure_rate > self.error_rate_threshold * 2
                else AlertLevel.WARNING
            )
            new_alerts.append(Alert(
                rule_name="error_rate",
                level=level,
                message=f"工具调用错误率 {tool_failure_rate:.1%} 超过阈值 {self.error_rate_threshold:.0%}",
                details={"failure_rate": tool_failure_rate, "threshold": self.error_rate_threshold},
            ))

        # 2. 费用检查
        total_cost = llm.get("total_cost", 0.0)
        if total_cost > self.cost_threshold:
            level = (
                AlertLevel.CRITICAL
                if total_cost > self.cost_threshold * 3
                else AlertLevel.WARNING
            )
            new_alerts.append(Alert(
                rule_name="cost_threshold",
                level=level,
                message=f"LLM 累计费用 ${total_cost:.2f} 超过阈值 ${self.cost_threshold:.2f}",
                details={"total_cost": total_cost, "threshold": self.cost_threshold},
            ))

        # 3. 延迟检查
        avg_duration_seconds = llm.get("avg_duration_ms", 0.0) / 1000.0
        if avg_duration_seconds > self.latency_threshold:
            level = (
                AlertLevel.CRITICAL
                if avg_duration_seconds > self.latency_threshold * 2
                else AlertLevel.WARNING
            )
            new_alerts.append(Alert(
                rule_name="latency_threshold",
                level=level,
                message=f"LLM 平均延迟 {avg_duration_seconds:.1f}s 超过阈值 {self.latency_threshold:.1f}s",
                details={"avg_duration_seconds": avg_duration_seconds, "threshold": self.latency_threshold},
            ))

        # 记录新告警到历史环形缓冲区
        for alert in new_alerts:
            self._push_alert(alert)

        return new_alerts

    # ------------------------------------------------------------------ #
    #  手动触发告警
    # ------------------------------------------------------------------ #
    def alert(
        self,
        rule_name: str,
        level: AlertLevel,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """手动触发一条告警。"""
        alert = Alert(
            rule_name=rule_name,
            level=level,
            message=message,
            details=details or {},
        )
        self._push_alert(alert)
        logger.log(
            logging.WARNING if level in (AlertLevel.WARNING, AlertLevel.CRITICAL) else logging.INFO,
            "告警 [%s] %s: %s", level.value, rule_name, message,
        )

    # ------------------------------------------------------------------ #
    #  历史查询与清除
    # ------------------------------------------------------------------ #
    def get_history(
        self,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查询告警历史，可按级别过滤。"""
        with self._lock:
            if not self._history:
                return []
            # 从最旧到最新排序
            sorted_history = sorted(self._history, key=lambda a: a.timestamp, reverse=True)
            result = []
            for alert in sorted_history:
                if level and alert.level.value != level:
                    continue
                result.append({
                    "rule_name": alert.rule_name,
                    "level": alert.level.value,
                    "message": alert.message,
                    "timestamp": alert.timestamp,
                    "details": alert.details,
                })
                if len(result) >= limit:
                    break
            return result

    def clear_history(self) -> int:
        """清除所有告警历史，返回清除数量。"""
        with self._lock:
            count = len(self._history)
            self._history.clear()
            self._next_index = 0
            return count

    # ------------------------------------------------------------------ #
    #  内部：环形缓冲区
    # ------------------------------------------------------------------ #
    def _push_alert(self, alert: Alert) -> None:
        """将告警写入环形缓冲区。"""
        with self._lock:
            if len(self._history) < self._max_history:
                self._history.append(alert)
            else:
                # 环形覆盖最旧的告警
                self._history[self._next_index % self._max_history] = alert
            self._next_index += 1

        # 日志记录
        log_level = (
            logging.ERROR if alert.level == AlertLevel.CRITICAL
            else logging.WARNING if alert.level == AlertLevel.WARNING
            else logging.INFO
        )
        logger.log(log_level, "告警 [%s] %s: %s", alert.level.value, alert.rule_name, alert.message)


# 全局单例
metrics_collector = MetricsCollector()
alert_manager = AlertManager()

__all__ = [
    "MetricsCollector",
    "AlertManager",
    "Alert",
    "AlertRule",
    "AlertLevel",
    "metrics_collector",
    "alert_manager",
]