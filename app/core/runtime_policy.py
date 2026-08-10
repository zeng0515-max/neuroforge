"""Deterministic runtime governance for agent actions.

This module is intentionally small and dependency-free. It enforces policy
before an action reaches a sandbox, tool registry, or network boundary, so a
blocked action is structurally impossible rather than merely discouraged.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, Optional

logger = logging.getLogger(__name__)


class ActionKind(str, Enum):
    """Action types that can cross a runtime boundary."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"


@dataclass
class PolicyDecision:
    """Result of one policy check."""

    allowed: bool
    kind: ActionKind
    actor: str
    policy: str = ""
    reason: str = ""
    context: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "kind": self.kind.value,
            "actor": self.actor,
            "policy": self.policy,
            "reason": self.reason,
            "context": self.context,
        }


class RuntimePolicy:
    """Thread-safe policy engine with a kill switch and extensible rules.

    The kill switch is the highest-priority control: once enabled, mutating and
    execution actions are denied until an operator explicitly disables it.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._kill_switch = False
        self._kill_reason = ""
        self._rules: Dict[ActionKind, list[Callable[[Dict[str, Any]], Optional[str]]]] = {
            kind: [] for kind in ActionKind
        }
        self._violations: Deque[Dict[str, Any]] = deque(maxlen=2000)

    # -- kill switch ---------------------------------------------------------

    def set_kill_switch(self, enabled: bool, reason: str = "") -> None:
        with self._lock:
            self._kill_switch = bool(enabled)
            self._kill_reason = reason or ""
        level = logging.WARNING if enabled else logging.INFO
        logger.log(level, "Runtime kill switch %s (%s)", "ENABLED" if enabled else "disabled", reason)

    @property
    def kill_switch(self) -> bool:
        with self._lock:
            return self._kill_switch

    # -- rules ---------------------------------------------------------------

    def add_rule(
        self,
        name: str,
        kinds: Iterable[ActionKind],
        predicate: Callable[[Dict[str, Any]], Optional[str]],
    ) -> None:
        """Register a deterministic predicate.

        The predicate receives the action context and returns ``None`` when the
        action is allowed, or a deny reason string when it must be blocked.
        """

        def _wrapped(ctx: Dict[str, Any]) -> Optional[str]:
            try:
                return predicate(ctx)
            except Exception:  # noqa: BLE001 - policy must never throw
                logger.exception("Policy rule %s raised an exception; denying action", name)
                return f"policy rule {name} failed closed"

        _wrapped.__name__ = name
        for kind in kinds:
            with self._lock:
                self._rules[kind].append(_wrapped)
        logger.info("Registered policy rule %s for %s kinds", name, len(list(kinds)))

    # -- checks --------------------------------------------------------------

    def allow(
        self,
        kind: ActionKind,
        actor: str = "system",
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyDecision:
        ctx: Dict[str, Any] = dict(context or {})
        ctx["actor"] = actor
        ctx["kind"] = kind.value

        with self._lock:
            kill_enabled = self._kill_switch
            kill_reason = self._kill_reason
            rules = list(self._rules[kind])

        if kill_enabled and kind in (
            ActionKind.WRITE,
            ActionKind.EXECUTE,
            ActionKind.NETWORK,
            ActionKind.LLM_CALL,
            ActionKind.TOOL_CALL,
        ):
            decision = PolicyDecision(
                allowed=False,
                kind=kind,
                actor=actor,
                policy="kill_switch",
                reason=kill_reason or "runtime kill switch is enabled",
                context=ctx,
            )
            return self._record(decision)

        for rule in rules:
            reason = rule(ctx)
            if reason:
                decision = PolicyDecision(
                    allowed=False,
                    kind=kind,
                    actor=actor,
                    policy=rule.__name__ if hasattr(rule, "__name__") else "custom_rule",
                    reason=reason,
                    context=ctx,
                )
                return self._record(decision)

        return PolicyDecision(
            allowed=True,
            kind=kind,
            actor=actor,
            policy="default",
            reason="allowed",
            context=ctx,
        )

    # -- audit ---------------------------------------------------------------

    def recent_violations(self, limit: int = 50) -> list[Dict[str, Any]]:
        with self._lock:
            return list(self._violations)[-limit:]

    def violation_count(self) -> int:
        with self._lock:
            return len(self._violations)

    def _record(self, decision: PolicyDecision) -> PolicyDecision:
        if not decision.allowed:
            entry = decision.as_dict()
            entry["timestamp"] = time.time()
            with self._lock:
                self._violations.append(entry)
            logger.warning(
                "Policy denied %s for actor=%s policy=%s reason=%s",
                decision.kind.value,
                decision.actor,
                decision.policy,
                decision.reason,
            )
        return decision


runtime_policy = RuntimePolicy()

__all__ = [
    "ActionKind",
    "PolicyDecision",
    "RuntimePolicy",
    "runtime_policy",
]
