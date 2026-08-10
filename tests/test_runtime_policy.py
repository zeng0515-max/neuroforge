from __future__ import annotations

from app.core.runtime_policy import ActionKind, RuntimePolicy


def test_default_allows_read_and_execute() -> None:
    policy = RuntimePolicy()
    assert policy.allow(ActionKind.READ).allowed is True
    assert policy.allow(ActionKind.EXECUTE).allowed is True


def test_kill_switch_blocks_mutating_actions() -> None:
    policy = RuntimePolicy()
    policy.set_kill_switch(True, reason="incident")

    assert policy.allow(ActionKind.READ).allowed is True
    assert policy.allow(ActionKind.WRITE).allowed is False
    assert policy.allow(ActionKind.EXECUTE).allowed is False
    assert policy.allow(ActionKind.NETWORK).allowed is False
    assert policy.allow(ActionKind.LLM_CALL).allowed is False
    assert policy.allow(ActionKind.TOOL_CALL).allowed is False

    policy.set_kill_switch(False)
    assert policy.allow(ActionKind.EXECUTE).allowed is True


def test_custom_rule_fails_closed() -> None:
    policy = RuntimePolicy()
    policy.add_rule(
        "no_private_path",
        [ActionKind.WRITE],
        lambda ctx: "private path blocked" if str(ctx.get("path", "")).startswith("private/") else None,
    )

    assert policy.allow(ActionKind.WRITE, context={"path": "tmp/file.txt"}).allowed is True
    denied = policy.allow(ActionKind.WRITE, context={"path": "private/secret.txt"})
    assert denied.allowed is False
    assert denied.policy == "no_private_path"
    assert policy.violation_count() == 1


def test_rule_exception_denies_action() -> None:
    policy = RuntimePolicy()

    def broken(_ctx: dict) -> str:
        raise RuntimeError("boom")

    policy.add_rule("broken", [ActionKind.TOOL_CALL], broken)
    decision = policy.allow(ActionKind.TOOL_CALL, context={"tool": "x"})
    assert decision.allowed is False
    assert "failed closed" in decision.reason


def test_recent_violations_are_ordered() -> None:
    policy = RuntimePolicy()
    policy.add_rule("deny_all", [ActionKind.NETWORK], lambda ctx: "no network")
    policy.allow(ActionKind.NETWORK)
    policy.allow(ActionKind.NETWORK)
    violations = policy.recent_violations()
    assert len(violations) == 2
    assert violations[-1]["kind"] == "network"
