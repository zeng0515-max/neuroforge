"""Admin surface and durability of the runtime kill switch.

Covers the operator-facing control plane added on top of
``app.core.runtime_policy``:

- fail-closed behaviour when ``ADMIN_API_KEY`` is missing (503);
- admin-key authentication (401) and its happy path;
- the switch actually denying ``ActionKind.WRITE`` with ``policy == "kill_switch"``;
- persistence to disk, restore into a fresh ``RuntimePolicy`` and on startup;
- tolerance of a missing / corrupt / undecodable state file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.audit import AuditEventType, audit_logger
from app.core.config import settings
from app.core.policy_state import current_state, load_state, save_state, state_path
from app.core.runtime_policy import ActionKind, RuntimePolicy, runtime_policy

ADMIN_KEY = "test-admin-key-do-not-reuse"
STATE_HEADERS = {"X-Admin-Key": ADMIN_KEY}
TOGGLE_URL = "/api/v1/policy/kill-switch"
STATE_URL = "/api/v1/policy/state"


@pytest.fixture(autouse=True)
def isolated_policy_state(monkeypatch, tmp_path) -> Path:
    """Point the state file at tmp_path and leave the global switch clean."""
    state_file = tmp_path / "runtime_policy_state.json"
    monkeypatch.setenv("POLICY_STATE_PATH", str(state_file))
    monkeypatch.setattr(settings.security, "admin_key", "", raising=False)
    # Keep the generic API-key gate out of the way: these tests target X-Admin-Key.
    monkeypatch.setattr(settings.security, "api_key", "", raising=False)
    runtime_policy.set_kill_switch(False, "")
    try:
        yield state_file
    finally:
        runtime_policy.set_kill_switch(False, "")


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _write_state(path: Path, **overrides) -> dict:
    payload = {
        "enabled": True,
        "reason": "pre-existing freeze",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "actor": "sre",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


# --------------------------------------------------------------------------- #
#  Authentication
# --------------------------------------------------------------------------- #
def test_admin_endpoints_are_503_without_admin_key() -> None:
    """No configured admin key means no administration at all (fail-closed)."""
    with _client() as client:
        state_resp = client.get(STATE_URL)
        toggle_resp = client.post(TOGGLE_URL, json={"enabled": True, "reason": "no key configured"})

    assert state_resp.status_code == 503
    assert toggle_resp.status_code == 503
    assert "ADMIN_API_KEY" in state_resp.json()["detail"]
    # A rejected call must not touch the runtime flag.
    assert runtime_policy.kill_switch is False


def test_wrong_or_missing_admin_key_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings.security, "admin_key", ADMIN_KEY, raising=False)

    with _client() as client:
        wrong = {"X-Admin-Key": "definitely-not-the-key"}
        assert client.get(STATE_URL, headers=wrong).status_code == 401
        assert client.post(TOGGLE_URL, json={"enabled": True, "reason": "x"}, headers=wrong).status_code == 401
        # The header is mandatory too.
        assert client.get(STATE_URL).status_code == 401
        assert client.post(TOGGLE_URL, json={"enabled": True, "reason": "x"}).status_code == 401

    assert runtime_policy.kill_switch is False


# --------------------------------------------------------------------------- #
#  Toggling
# --------------------------------------------------------------------------- #
def test_admin_key_can_toggle_kill_switch(monkeypatch) -> None:
    monkeypatch.setattr(settings.security, "admin_key", ADMIN_KEY, raising=False)
    audit_before = audit_logger.count(AuditEventType.POLICY_KILL_SWITCH)

    with _client() as client:
        enabled_resp = client.post(TOGGLE_URL, json={"enabled": True, "reason": "incident drill"}, headers=STATE_HEADERS)
        assert enabled_resp.status_code == 200
        enabled_body = enabled_resp.json()
        assert enabled_body["enabled"] is True
        assert enabled_body["reason"] == "incident drill"
        assert enabled_body["actor"] == "admin"
        assert enabled_body["updated_at"]

        # Enforcement, not just bookkeeping.
        assert runtime_policy.kill_switch is True
        assert runtime_policy.allow(ActionKind.READ).allowed is True
        denied = runtime_policy.allow(ActionKind.WRITE, context={"path": "src/app.py"})
        assert denied.allowed is False
        assert denied.policy == "kill_switch"

        state_resp = client.get(STATE_URL, headers=STATE_HEADERS)
        assert state_resp.status_code == 200
        assert state_resp.json()["enabled"] is True
        assert state_resp.json()["reason"] == "incident drill"

        disabled_resp = client.post(TOGGLE_URL, json={"enabled": False, "reason": "incident over"}, headers=STATE_HEADERS)
        assert disabled_resp.status_code == 200
        assert disabled_resp.json()["enabled"] is False
        assert runtime_policy.kill_switch is False
        assert runtime_policy.allow(ActionKind.WRITE, context={"path": "src/app.py"}).allowed is True

        # A missing reason is filled with a readable operator default.
        defaulted = client.post(TOGGLE_URL, json={"enabled": True}, headers=STATE_HEADERS)
        assert defaulted.status_code == 200
        assert defaulted.json()["reason"]
        assert "operator" in defaulted.json()["reason"]
        client.post(TOGGLE_URL, json={"enabled": False, "reason": "cleanup"}, headers=STATE_HEADERS)

    entries = audit_logger.get_recent(AuditEventType.POLICY_KILL_SWITCH)
    assert len(entries) >= audit_before + 2
    # Newest first: the last toggle was the cleanup disable.
    assert entries[0].actor_id == "admin"
    assert entries[0].action == "kill_switch_disabled"
    assert any(e.action == "kill_switch_enabled" and e.actor_id == "admin" for e in entries[:3])


# --------------------------------------------------------------------------- #
#  Persistence
# --------------------------------------------------------------------------- #
def test_state_is_persisted_and_restored_into_a_new_policy(isolated_policy_state: Path) -> None:
    saved = save_state(True, "durable freeze", "cli")
    assert saved["enabled"] is True
    assert saved["reason"] == "durable freeze"
    assert state_path() == isolated_policy_state

    raw = json.loads(isolated_policy_state.read_text(encoding="utf-8"))
    assert raw["enabled"] is True
    assert raw["reason"] == "durable freeze"
    assert raw["actor"] == "cli"
    assert raw["updated_at"]

    fresh = RuntimePolicy()
    assert fresh.kill_switch is False
    assert load_state(fresh) is True
    assert fresh.kill_switch is True
    assert fresh.allow(ActionKind.WRITE).policy == "kill_switch"
    assert current_state()["reason"] == "durable freeze"


def test_startup_restores_persisted_state(monkeypatch, isolated_policy_state: Path) -> None:
    monkeypatch.setattr(settings.security, "admin_key", ADMIN_KEY, raising=False)
    _write_state(isolated_policy_state)
    runtime_policy.set_kill_switch(False, "")

    with _client() as client:
        # lifespan -> load_state() must re-arm the switch from disk.
        assert runtime_policy.kill_switch is True
        denied = runtime_policy.allow(ActionKind.WRITE, context={"path": "src/app.py"})
        assert denied.allowed is False
        assert denied.policy == "kill_switch"

        state_resp = client.get(STATE_URL, headers=STATE_HEADERS)
    assert state_resp.status_code == 200
    assert state_resp.json()["enabled"] is True
    assert state_resp.json()["reason"] == "pre-existing freeze"
    assert state_resp.json()["actor"] == "sre"


def test_missing_state_file_keeps_switch_off(isolated_policy_state: Path) -> None:
    assert not isolated_policy_state.exists()

    assert load_state() is False
    assert runtime_policy.kill_switch is False
    # Reading must never create the file as a side effect.
    assert not isolated_policy_state.exists()


@pytest.mark.parametrize(
    "payload",
    [
        "{not valid json",
        "[1, 2, 3]",
        '"just a string"',
    ],
)
def test_corrupt_state_file_degrades_to_disabled(isolated_policy_state: Path, payload: str) -> None:
    isolated_policy_state.write_text(payload, encoding="utf-8")

    policy = RuntimePolicy()
    policy.set_kill_switch(True, "was armed")

    assert load_state(policy) is False
    assert policy.kill_switch is False
    assert current_state()["enabled"] is False


def test_undecodable_state_file_degrades_to_disabled(isolated_policy_state: Path) -> None:
    isolated_policy_state.write_bytes(b"\xff\xfe\x00not utf-8")

    policy = RuntimePolicy()
    assert load_state(policy) is False
    assert policy.kill_switch is False
