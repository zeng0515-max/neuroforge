"""Default runtime policy rules must actually deny dangerous actions."""

from __future__ import annotations

from app.core.policy_rules import (
    install_default_policy_rules,
    reset_default_policy_rules_for_tests,
)
from app.core.runtime_policy import ActionKind, RuntimePolicy


def _policy_with_defaults() -> RuntimePolicy:
    reset_default_policy_rules_for_tests()
    policy = RuntimePolicy()
    assert install_default_policy_rules(policy) > 0
    return policy


def test_default_rules_install_once() -> None:
    reset_default_policy_rules_for_tests()
    policy = RuntimePolicy()
    first = install_default_policy_rules(policy)
    assert first == 5
    assert install_default_policy_rules(policy) == 0


def test_secret_reads_are_denied() -> None:
    policy = _policy_with_defaults()
    denied = policy.allow(ActionKind.READ, context={"path": "/repo/.env.local"})
    assert denied.allowed is False
    assert denied.policy == "no_secret_reads"

    key = policy.allow(ActionKind.READ, context={"path": "C:/keys/server.PEM"})
    assert key.allowed is False

    allowed = policy.allow(ActionKind.READ, context={"path": "app/main.py"})
    assert allowed.allowed is True


def test_protected_writes_and_traversal_are_denied() -> None:
    policy = _policy_with_defaults()
    assert policy.allow(ActionKind.WRITE, context={"path": ".env"}).allowed is False
    assert policy.allow(ActionKind.WRITE, context={"path": ".git/hooks/pre-commit"}).allowed is False
    assert policy.allow(ActionKind.WRITE, context={"path": "../../etc/passwd"}).allowed is False
    assert policy.allow(ActionKind.WRITE, context={"path": "src/generated/util.py"}).allowed is True


def test_destructive_commands_are_denied_but_generated_code_is_not() -> None:
    policy = _policy_with_defaults()
    for command in ("rm -rf /", "mkfs.ext4 /dev/sda1", ":(){ :|:& };:", "shutdown -h now"):
        assert policy.allow(ActionKind.EXECUTE, context={"command": command}).allowed is False

    ok = policy.allow(ActionKind.EXECUTE, context={"command": "python main.py"})
    assert ok.allowed is True
    # Generated Python is not shell, so literals must not trip the rule.
    literal = policy.allow(
        ActionKind.EXECUTE,
        context={"code": "print('shutdown -h now is just a string')"},
    )
    assert literal.allowed is True


def test_network_denied_unless_configuration_enables_it(monkeypatch) -> None:
    policy = _policy_with_defaults()

    from app.core.config import settings

    monkeypatch.setattr(settings.sandbox, "network_enabled", False, raising=False)
    denied = policy.allow(ActionKind.NETWORK)
    assert denied.allowed is False
    assert denied.policy == "network_must_be_enabled"

    monkeypatch.setattr(settings.sandbox, "network_enabled", True, raising=False)
    assert policy.allow(ActionKind.NETWORK).allowed is True


def test_dangerous_tool_requires_approval_id() -> None:
    policy = _policy_with_defaults()
    denied = policy.allow(
        ActionKind.TOOL_CALL,
        context={"tool": "shell_exec", "permission": "dangerous"},
    )
    assert denied.allowed is False
    assert denied.policy == "dangerous_tool_needs_approval"

    allowed = policy.allow(
        ActionKind.TOOL_CALL,
        context={"tool": "shell_exec", "permission": "dangerous", "approval_id": "appr-1"},
    )
    assert allowed.allowed is True

    observe = policy.allow(
        ActionKind.TOOL_CALL, context={"tool": "read_file", "permission": "observe"}
    )
    assert observe.allowed is True


def test_denials_are_recorded() -> None:
    policy = _policy_with_defaults()
    policy.allow(ActionKind.READ, context={"path": ".env"})
    violations = policy.recent_violations()
    assert violations and violations[-1]["policy"] == "no_secret_reads"
    assert policy.violation_count() >= 1


def test_kill_switch_still_wins_over_rules() -> None:
    policy = _policy_with_defaults()
    policy.set_kill_switch(True, "operator freeze")
    decision = policy.allow(ActionKind.WRITE, context={"path": "src/a.py"})
    assert decision.allowed is False
    assert decision.policy == "kill_switch"
    policy.set_kill_switch(False)
    assert policy.allow(ActionKind.WRITE, context={"path": "src/a.py"}).allowed is True
