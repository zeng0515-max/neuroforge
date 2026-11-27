"""Operator endpoints for the runtime kill switch.

The kill switch is the highest-priority governance control in
``app.core.runtime_policy``: once enabled, every mutating action (write, execute,
network, llm_call, tool_call) is denied until an operator disables it again.

These routes are intentionally separated from the regular API-key surface:

- They require the dedicated ``X-Admin-Key`` header, compared with
  :func:`hmac.compare_digest` so a timing side channel cannot leak the key.
- When ``ADMIN_API_KEY`` is not configured they refuse to work at all (503)
  instead of silently allowing anonymous operators to freeze or unfreeze the
  system.
- Every toggle is written to the durable audit log with the acting identity.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.audit import ActorType, AuditEventType, audit_logger
from app.core.config import settings
from app.core.policy_state import current_state, save_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/policy", tags=["policy"])

ADMIN_ACTOR = "admin"


class KillSwitchRequest(BaseModel):
    """Payload for toggling the kill switch."""

    enabled: bool
    reason: str = Field(default="", max_length=500)


def _require_admin(x_admin_key: str) -> str:
    """Validate the admin key, failing closed when it is not configured."""
    expected = (settings.security.admin_key or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_API_KEY is not configured; policy administration is disabled",
        )
    provided = (x_admin_key or "").strip()
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid admin key")
    return ADMIN_ACTOR


@router.get("/state")
async def get_policy_state(x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> Dict[str, Any]:
    """Return the persisted kill switch state (enabled/reason/updated_at/actor)."""
    _require_admin(x_admin_key)
    return current_state()


@router.post("/kill-switch")
async def set_policy_kill_switch(
    payload: KillSwitchRequest,
    x_admin_key: str = Header(default="", alias="X-Admin-Key"),
) -> Dict[str, Any]:
    """Enable or disable the runtime kill switch and persist the decision."""
    actor = _require_admin(x_admin_key)
    reason = payload.reason.strip()
    if not reason:
        reason = "kill switch enabled by operator" if payload.enabled else "kill switch disabled by operator"

    state = save_state(payload.enabled, reason, actor)
    logger.warning("Kill switch %s via admin API (actor=%s)", "ENABLED" if payload.enabled else "disabled", actor)

    audit_logger.log(
        AuditEventType.POLICY_KILL_SWITCH,
        ActorType.USER,
        actor,
        "kill_switch_enabled" if payload.enabled else "kill_switch_disabled",
        input_data={"enabled": payload.enabled, "reason": payload.reason},
        output_data={"state": state},
        metadata={"source": "policy_admin_api"},
    )
    return state


__all__ = ["router"]
