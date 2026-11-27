"""Built-in runtime policy rules.

The policy engine is only useful if it actually has rules. This module ships a
small, deterministic default rule set and installs it on the shared
``runtime_policy`` singleton (idempotently) from every process entry point:
the FastAPI app, the durable worker and the CLI helpers.

Each rule returns ``None`` to allow an action, or a short deny reason. Rules
must be pure and cheap: they run before the action reaches a tool, a sandbox or
the network, so a deny is structural rather than advisory.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app.core.runtime_policy import ActionKind, RuntimePolicy, runtime_policy

logger = logging.getLogger(__name__)

# Names already installed on a policy instance, so repeated imports/startups
# never stack duplicate rules (which would double-count violations).
_INSTALLED: Dict[int, set] = {}

_DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[rf]{1,2}\s+/(\s|$)"),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\s+if=.*\bof=/dev/"),
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"),
    re.compile(r"\bformat\s+[a-zA-Z]:"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;:"),  # fork bomb
    re.compile(r"\bchmod\s+-R\s+777\s+/(\s|$)"),
]

_SECRET_PATH_PATTERNS = [
    re.compile(r"(^|[\\/])\.env(\.[\w-]+)?$", re.IGNORECASE),
    re.compile(r"(^|[\\/])id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", re.IGNORECASE),
    re.compile(r"\.(pem|key|pfx|p12|jks)$", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.ssh([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.aws([\\/]|$)", re.IGNORECASE),
]

# Paths agents may not write even though they are inside the workspace.
_PROTECTED_WRITE_TARGETS = (
    ".env",
    ".git/",
    ".github/workflows/",
    "docker-compose.yml",
    "Dockerfile",
)


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else str(value or "")


def rule_no_secret_reads(ctx: Dict[str, Any]) -> Optional[str]:
    """Block reads of credential material through the policy boundary."""
    target = _as_str(ctx.get("path") or ctx.get("file") or ctx.get("target"))
    if not target:
        return None
    for pattern in _SECRET_PATH_PATTERNS:
        if pattern.search(target):
            return f"reading credential material is not allowed: {target[:200]}"
    return None


def rule_no_protected_writes(ctx: Dict[str, Any]) -> Optional[str]:
    """Block writes to secrets, VCS internals and delivery configuration."""
    target = _as_str(ctx.get("path") or ctx.get("file") or ctx.get("target"))
    if not target:
        return None
    normalized = target.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    for protected in _PROTECTED_WRITE_TARGETS:
        if normalized == protected.rstrip("/") or normalized.startswith(protected):
            return f"writing to protected path is not allowed: {target[:200]}"
    if ".." in normalized.split("/"):
        return f"path traversal is not allowed: {target[:200]}"
    return None


def rule_no_destructive_commands(ctx: Dict[str, Any]) -> Optional[str]:
    """Block obviously destructive shell commands before they reach a sandbox.

    Only an explicit ``command`` string is inspected. Generated Python ``code``
    is executed through an interpreter, so scanning it here would produce false
    positives on harmless literals (e.g. a string containing "shutdown").
    """
    command = _as_str(ctx.get("command"))
    if not command:
        return None
    for pattern in _DESTRUCTIVE_COMMAND_PATTERNS:
        if pattern.search(command):
            return "destructive command blocked by runtime policy"
    return None


def rule_network_must_be_enabled(ctx: Dict[str, Any]) -> Optional[str]:
    """Deny outbound network actions unless the sandbox explicitly allows them."""
    try:
        from app.core.config import settings

        sandbox = getattr(settings, "sandbox", settings)
        enabled = bool(getattr(sandbox, "network_enabled", False))
    except Exception:  # noqa: BLE001 - config unavailable means "closed"
        enabled = False
    if enabled:
        return None
    return "network access is disabled by configuration"


def rule_dangerous_tool_needs_approval(ctx: Dict[str, Any]) -> Optional[str]:
    """Dangerous tools require an approved approval id in the same context."""
    if _as_str(ctx.get("permission")).lower() != "dangerous":
        return None
    approval_id = _as_str(ctx.get("approval_id"))
    if approval_id:
        return None
    return "dangerous tool requires an approved approval_id"


DEFAULT_RULES = (
    ("no_secret_reads", (ActionKind.READ,), rule_no_secret_reads),
    ("no_protected_writes", (ActionKind.WRITE,), rule_no_protected_writes),
    ("no_destructive_commands", (ActionKind.EXECUTE,), rule_no_destructive_commands),
    ("network_must_be_enabled", (ActionKind.NETWORK,), rule_network_must_be_enabled),
    ("dangerous_tool_needs_approval", (ActionKind.TOOL_CALL,), rule_dangerous_tool_needs_approval),
)


def install_default_policy_rules(policy: Optional[RuntimePolicy] = None) -> int:
    """Install the default rule set on ``policy`` (defaults to the singleton).

    Returns the number of rules installed by this call; calling it twice is a
    no-op, which keeps app startup, worker startup and tests compatible.
    """
    target = policy or runtime_policy
    installed = _INSTALLED.setdefault(id(target), set())
    added = 0
    for name, kinds, predicate in DEFAULT_RULES:
        if name in installed:
            continue
        target.add_rule(name, kinds, predicate)
        installed.add(name)
        added += 1
    if added:
        logger.info("Installed %d runtime policy rule(s)", added)
    return added


def reset_default_policy_rules_for_tests() -> None:
    """Forget the installed-rule bookkeeping (tests only)."""
    _INSTALLED.clear()


__all__ = [
    "DEFAULT_RULES",
    "install_default_policy_rules",
    "reset_default_policy_rules_for_tests",
]
