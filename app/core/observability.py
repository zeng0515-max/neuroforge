"""可观测性与监控告警模块。

包含：
- MetricsCollector：收集 LLM 调用、流水线、工具调用、阶段级 Token 等指标
- AlertManager：基于规则的告警管理与历史存储
- _StageMetricsCollector：按阶段追踪 Token 使用（requirement/code_gen/review/test_gen）
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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


class _StageMetricsCollector:
    """按流水线阶段（requirement/code_gen/review/test_gen）追踪 Token 用量。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "calls": 0, "cost": 0.0, "duration_ms": 0.0,
            "by_model": defaultdict(int), "by_agent": defaultdict(int),
        })

    def record(self, stage: str, *, prompt: int = 0, completion: int = 0,
               total: int = 0, model: str = "", agent: str = "",
               cost: float = 0.0, duration_ms: float = 0.0) -> None:
        with self._lock:
            s = self._data[stage]
            s["prompt_tokens"] += prompt
            s["completion_tokens"] += completion
            s["total_tokens"] += total or (prompt + completion)
            s["calls"] += 1
            s["cost"] += cost
            s["duration_ms"] += duration_ms
            if model:
                s["by_model"][model] += 1
            if agent:
                s["by_agent"][agent] += 1

    def breakdown(self) -> Dict[str, Any]:
        with self._lock:
            return {
                k: {**v, "by_model": dict(v["by_model"]), "by_agent": dict(v["by_agent"])}
                for k, v in self._data.items()
            }


class MetricsCollector:
    """轻量级指标收集器，dict + threading.Lock 保证并发安全。

    收集四类指标：
    - LLM 调用指标：total_calls, total_tokens, total_cost, calls_by_model, calls_by_agent
    - 流水线指标：total_pipelines, success_count, failure_count, avg_duration
    - 工具调用指标：total_tool_calls, tool_calls_by_name, tool_failures
    - 阶段指标：按 requirement/code_gen/review/test_gen 追踪 token 与成本
    """

    def __init__(
        self,
        enabled: bool = True,
        retention_days: int = 30,
        audit_logger: Any = None,
    ) -> None:
        self._enabled = enabled
        self._retention_days = retention_days
        self._audit_logger = audit_logger
        self._lock = threading.Lock()
        self.llm_total_calls: int = 0
        self.llm_total_tokens: int = 0
        self.llm_total_cost: float = 0.0
        self.llm_calls_by_model: Dict[str, int] = defaultdict(int)
        self.llm_calls_by_agent: Dict[str, int] = defaultdict(int)
        self.llm_total_duration_ms: float = 0.0
        self.pipeline_total: int = 0
        self.pipeline_success: int = 0
        self.pipeline_failure: int = 0
        self.pipeline_total_duration: float = 0.0
        self.tool_total_calls: int = 0
        self.tool_calls_by_name: Dict[str, int] = defaultdict(int)
        self.tool_failures: int = 0
        self.tool_failures_by_name: Dict[str, int] = defaultdict(int)
        self._stage_metrics = _StageMetricsCollector()
        self._traces: Dict[str, Dict[str, Any]] = {}
        self._pipeline_durations: deque = deque(maxlen=1000)

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
        if self._audit_logger:
            try:
                self._audit_logger.log_action(
                    actor=agent or "unknown", action="llm_call",
                    input_data={"model": model},
                    output_data={"tokens": tokens, "cost": cost},
                    duration_ms=duration_ms,
                )
            except Exception:
                pass

    def record_llm_call_with_stage(
        self,
        stage: str,
        model: str = "",
        tokens: int = 0,
        cost: float = 0.0,
        duration_ms: float = 0.0,
        agent: str = "",
        trace_id: str = "",
    ) -> None:
        """记录带阶段标注的 LLM 调用，可选关联 trace_id。"""
        self.record_llm_call(
            model=model, tokens=tokens, cost=cost,
            duration_ms=duration_ms, agent=agent,
        )
        self._stage_metrics.record(
            stage, total=tokens, model=model, agent=agent,
            cost=cost, duration_ms=duration_ms,
        )
        if trace_id:
            self._traces[trace_id] = {
                "id": trace_id,
                "name": f"{stage}-{agent or 'agent'}",
                "timestamp": time.time(),
                "usage": {"promptTokens": 0, "completionTokens": 0, "totalTokens": tokens},
                "metadata": {"stage": stage, "model": model, "agent": agent},
                "observations": [],
            }

    def record_token_breakdown(
        self,
        stage: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int = 0,
    ) -> None:
        """记录指定阶段的 token 明细拆解。"""
        self._stage_metrics.record(
            stage, prompt=prompt_tokens,
            completion=completion_tokens, total=total_tokens,
        )

    def get_breakdown_by_stage(self) -> Dict[str, Any]:
        """返回按阶段聚合的 token/cost 明细。"""
        return self._stage_metrics.breakdown()

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
            self._pipeline_durations.append(duration_seconds)
        if self._audit_logger:
            try:
                self._audit_logger.log_action(
                    actor="pipeline", action="run",
                    input_data={},
                    output_data={"success": success, "duration_s": duration_seconds},
                    duration_ms=duration_seconds * 1000,
                )
            except Exception:
                pass

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

    def export_trace(self, trace_id: str) -> Dict[str, Any]:
        """导出 LangFuse 兼容的 trace 字典。"""
        return self._traces.get(trace_id, {
            "id": trace_id, "timestamp": time.time(),
            "usage": {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0},
            "metadata": {}, "observations": [],
        })

    def snapshot(self) -> Dict[str, Any]:
        """导出当前指标快照。"""
        with self._lock:
            llm_avg = (self.llm_total_duration_ms / self.llm_total_calls
                       if self.llm_total_calls > 0 else 0.0)
            pipe_avg = (self.pipeline_total_duration / self.pipeline_total
                        if self.pipeline_total > 0 else 0.0)
            sorted_durations = sorted(self._pipeline_durations)
            p95 = (
                sorted_durations[int(len(sorted_durations) * 0.95) - 1]
                if sorted_durations
                else 0.0
            )
            return {
                "llm": {
                    "total_calls": self.llm_total_calls,
                    "total_tokens": self.llm_total_tokens,
                    "total_cost": round(self.llm_total_cost, 6),
                    "avg_duration_ms": round(llm_avg, 2),
                    "calls_by_model": dict(self.llm_calls_by_model),
                    "calls_by_agent": dict(self.llm_calls_by_agent),
                },
                "pipeline": {
                    "total": self.pipeline_total,
                    "success": self.pipeline_success,
                    "failure": self.pipeline_failure,
                    "success_rate": round(
                        self.pipeline_success / max(self.pipeline_total, 1), 4),
                    "avg_duration_seconds": round(pipe_avg, 2),
                    "p95_duration_seconds": round(p95, 2),
                    "sample_size": len(sorted_durations),
                },
                "tool": {
                    "total_calls": self.tool_total_calls,
                    "calls_by_name": dict(self.tool_calls_by_name),
                    "failures": self.tool_failures,
                    "failures_by_name": dict(self.tool_failures_by_name),
                    "failure_rate": round(
                        self.tool_failures / max(self.tool_total_calls, 1), 4),
                },
                "stage_breakdown": self._stage_metrics.breakdown(),
                "enabled": self._enabled,
                "retention_days": self._retention_days,
            }


class AlertManager:
    """基于规则的告警管理器。

    支持可配置告警规则、INFO/WARNING/CRITICAL 告警级别、
    日志通知 + API 查询、环形缓冲区历史存储（最多 1000 条）。
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._lock = threading.Lock()
        self._max_history = max_history
        self._history: List[Alert] = []
        self._next_index: int = 0
        self.error_rate_threshold: float = 0.3
        self.cost_threshold: float = 10.0
        self.latency_threshold: float = 30.0

    def get_rules(self) -> List[Dict[str, Any]]:
        """返回当前告警规则列表。"""
        return [
            {"name": "error_rate", "description": "工具调用错误率超过阈值",
             "level": AlertLevel.WARNING.value, "threshold": self.error_rate_threshold, "enabled": True},
            {"name": "cost_threshold", "description": "LLM 累计费用超过阈值",
             "level": AlertLevel.WARNING.value, "threshold": self.cost_threshold, "enabled": True},
            {"name": "latency_threshold", "description": "LLM 平均调用延迟超过阈值（秒）",
             "level": AlertLevel.WARNING.value, "threshold": self.latency_threshold, "enabled": True},
        ]

    def evaluate(self, metrics: Dict[str, Any]) -> List[Alert]:
        """根据当前指标快照评估告警规则，返回新触发的告警列表。"""
        new_alerts: List[Alert] = []
        llm = metrics.get("llm", {})
        tool = metrics.get("tool", {})
        tool_failure_rate = tool.get("failure_rate", 0.0)
        if tool_failure_rate > self.error_rate_threshold:
            level = (AlertLevel.CRITICAL
                     if tool_failure_rate > self.error_rate_threshold * 2
                     else AlertLevel.WARNING)
            new_alerts.append(Alert(
                rule_name="error_rate", level=level,
                message=f"工具调用错误率 {tool_failure_rate:.1%} 超过阈值 {self.error_rate_threshold:.0%}",
                details={"failure_rate": tool_failure_rate, "threshold": self.error_rate_threshold},
            ))
        total_cost = llm.get("total_cost", 0.0)
        if total_cost > self.cost_threshold:
            level = (AlertLevel.CRITICAL
                     if total_cost > self.cost_threshold * 3
                     else AlertLevel.WARNING)
            new_alerts.append(Alert(
                rule_name="cost_threshold", level=level,
                message=f"LLM 累计费用 ${total_cost:.2f} 超过阈值 ${self.cost_threshold:.2f}",
                details={"total_cost": total_cost, "threshold": self.cost_threshold},
            ))
        avg_dur_s = llm.get("avg_duration_ms", 0.0) / 1000.0
        if avg_dur_s > self.latency_threshold:
            level = (AlertLevel.CRITICAL
                     if avg_dur_s > self.latency_threshold * 2
                     else AlertLevel.WARNING)
            new_alerts.append(Alert(
                rule_name="latency_threshold", level=level,
                message=f"LLM 平均延迟 {avg_dur_s:.1f}s 超过阈值 {self.latency_threshold:.1f}s",
                details={"avg_duration_seconds": avg_dur_s, "threshold": self.latency_threshold},
            ))
        for alert in new_alerts:
            self._push_alert(alert)
        return new_alerts

    def alert(
        self,
        rule_name: str,
        level: AlertLevel,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """手动触发一条告警。"""
        alert = Alert(rule_name=rule_name, level=level, message=message, details=details or {})
        self._push_alert(alert)
        logger.log(
            logging.WARNING if level in (AlertLevel.WARNING, AlertLevel.CRITICAL) else logging.INFO,
            "告警 [%s] %s: %s", level.value, rule_name, message,
        )

    def get_history(
        self,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查询告警历史，可按级别过滤。"""
        with self._lock:
            if not self._history:
                return []
            sorted_history = sorted(self._history, key=lambda a: a.timestamp, reverse=True)
            result = []
            for alert in sorted_history:
                if level and alert.level.value != level:
                    continue
                result.append({
                    "rule_name": alert.rule_name, "level": alert.level.value,
                    "message": alert.message, "timestamp": alert.timestamp,
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

    def _push_alert(self, alert: Alert) -> None:
        """将告警写入环形缓冲区。"""
        with self._lock:
            if len(self._history) < self._max_history:
                self._history.append(alert)
            else:
                self._history[self._next_index % self._max_history] = alert
            self._next_index += 1
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
    "_StageMetricsCollector",
    "metrics_collector",
    "alert_manager",
]
