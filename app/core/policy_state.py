"""Durable state for the runtime kill switch.

``app.core.runtime_policy`` holds the kill switch in memory, which means an
operator freeze would silently disappear on the next restart. This module gives
that flag a durable home so an emergency stop survives a process restart, a
container redeploy or a crashed worker.

Design notes:
- State lives in a small JSON document (``enabled`` / ``reason`` /
  ``updated_at`` / ``actor``) and is written atomically: a temp file in the same
  directory followed by ``os.replace``, so a crash mid-write can never leave a
  truncated file behind.
- Reading is strictly best effort. A missing, unreadable or corrupt state file
  degrades to the safe default (kill switch off) and never raises into
  application startup.
- Only an explicitly enabled switch denies actions; a broken state file must not
  silently start blocking every write, and it must not stop the API from booting
  either.
- The path defaults to ``<repo>/data/runtime_policy_state.json`` and can be
  overridden with the ``POLICY_STATE_PATH`` environment variable. Relative
  overrides resolve against the repository root so the API, the CLI and the
  worker always agree on one file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.runtime_policy import RuntimePolicy, runtime_policy

logger = logging.getLogger(__name__)

ENV_STATE_PATH = "POLICY_STATE_PATH"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "runtime_policy_state.json"

_LOCK = threading.RLock()
_STATE: Dict[str, Any] = {
    "enabled": False,
    "reason": "",
    "updated_at": "",
    "actor": "",
}

_MAX_REASON = 500
_MAX_ACTOR = 200


def _default_state() -> Dict[str, Any]:
    return {"enabled": False, "reason": "", "updated_at": "", "actor": ""}


def state_path() -> Path:
    """Return the active state file path (honours ``POLICY_STATE_PATH``)."""
    override = (os.environ.get(ENV_STATE_PATH) or "").strip()
    if not override:
        return DEFAULT_STATE_PATH
    candidate = Path(override).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a persisted mapping into the canonical shape (never raises)."""
    state = _default_state()
    state["enabled"] = bool(raw.get("enabled", False))
    state["reason"] = str(raw.get("reason") or "")[:_MAX_REASON]
    state["updated_at"] = str(raw.get("updated_at") or "")
    state["actor"] = str(raw.get("actor") or "")[:_MAX_ACTOR]
    return state


def _apply(state: Dict[str, Any], policy: RuntimePolicy) -> None:
    """Push the persisted state into a policy engine instance."""
    reason = state["reason"] if state["enabled"] else ""
    policy.set_kill_switch(bool(state["enabled"]), reason)


def _remember(state: Dict[str, Any]) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(state)


def load_state(policy: Optional[RuntimePolicy] = None) -> bool:
    """Restore the persisted kill switch state into ``policy``.

    Returns whether the kill switch is now enabled. Any read/parse failure is
    caught and reported as "disabled", so an unhealthy state file can never
    block application startup.
    """
    target = policy if policy is not None else runtime_policy
    path = state_path()
    state = _default_state()
    try:
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError(f"policy state must be a JSON object, got {type(raw).__name__}")
            state = _normalize(raw)
        else:
            logger.info("No persisted runtime policy state at %s; kill switch stays off", path)
    except Exception:  # noqa: BLE001 - a broken file must never break startup
        logger.warning("Runtime policy state unreadable at %s; defaulting to disabled", path, exc_info=True)
        state = _default_state()

    _remember(state)
    _apply(state, target)
    if state["enabled"]:
        logger.warning("Runtime kill switch restored as ENABLED (actor=%s reason=%s)", state["actor"], state["reason"])
    return bool(state["enabled"])


def _write_atomically(state: Dict[str, Any]) -> Path:
    """Write ``state`` to the active path via temp file + ``os.replace``."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def save_state(
    enabled: bool,
    reason: str = "",
    actor: str = "",
    policy: Optional[RuntimePolicy] = None,
) -> Dict[str, Any]:
    """Persist and immediately enforce a new kill switch state.

    Persistence is best effort on purpose: an unwritable state file must not stop
    an operator from freezing the system right now. The in-memory switch is
    always applied, and a failed write is logged loudly for follow-up.
    """
    state: Dict[str, Any] = {
        "enabled": bool(enabled),
        "reason": str(reason or "")[:_MAX_REASON],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": str(actor or "")[:_MAX_ACTOR],
    }

    try:
        path = _write_atomically(state)
        logger.info("Runtime policy state persisted to %s (enabled=%s)", path, state["enabled"])
    except Exception:  # noqa: BLE001 - enforcement must not depend on the disk
        logger.error("Unable to persist runtime policy state to %s; enforcing in memory only", state_path(), exc_info=True)

    _remember(state)
    _apply(state, policy if policy is not None else runtime_policy)
    level = logging.WARNING if state["enabled"] else logging.INFO
    logger.log(
        level,
        "Runtime kill switch %s by actor=%s (reason=%s)",
        "ENABLED" if state["enabled"] else "disabled",
        state["actor"],
        state["reason"],
    )
    return dict(state)


def current_state() -> Dict[str, Any]:
    """Return the last known state, reconciled with the live policy singleton.

    The snapshot carries the reason/audit fields; ``enabled`` follows the live
    :data:`app.core.runtime_policy.runtime_policy` flag so an out-of-band
    ``set_kill_switch`` call is reported truthfully instead of being masked by a
    stale snapshot.
    """
    with _LOCK:
        state = dict(_STATE)
    live = bool(runtime_policy.kill_switch)
    if state["enabled"] != live:
        state["enabled"] = live
    return state


__all__ = [
    "DEFAULT_STATE_PATH",
    "ENV_STATE_PATH",
    "current_state",
    "load_state",
    "save_state",
    "state_path",
]
