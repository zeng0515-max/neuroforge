"""Local operator CLI for the runtime kill switch.

Use this when you have shell access to the host but no ``ADMIN_API_KEY`` (or no
running API process at all): it talks to ``runtime_policy`` + ``policy_state``
directly, persists the decision to the same state file the API reads, and writes
a best-effort audit entry.

Examples::

    python scripts/policy_cli.py --enable --reason "release incident"
    python scripts/policy_cli.py --disable --reason "incident resolved" --actor sre
    python scripts/policy_cli.py --status
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.policy_state import current_state, load_state, save_state, state_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or change the persisted runtime kill switch state.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--enable", action="store_true", help="block mutating/execution actions")
    action.add_argument("--disable", action="store_true", help="lift the kill switch")
    action.add_argument("--status", action="store_true", help="print the persisted state and exit")
    parser.add_argument("--reason", default="", help="why the switch changes (persisted and audited)")
    parser.add_argument("--actor", default="cli", help="operator identity recorded with the change")
    return parser


def _audit(enabled: bool, reason: str, actor: str, state: dict) -> None:
    """Mirror the change into the durable audit log (best effort)."""
    try:
        from app.core.audit import ActorType, AuditEventType, audit_logger

        audit_logger.log(
            AuditEventType.POLICY_KILL_SWITCH,
            ActorType.USER,
            actor,
            "kill_switch_enabled" if enabled else "kill_switch_disabled",
            input_data={"enabled": enabled, "reason": reason},
            output_data={"state": state},
            metadata={"source": "policy_cli"},
        )
    except Exception as exc:  # noqa: BLE001 - auditing must not block an emergency stop
        print(f"warning: audit entry was not written: {exc}", file=sys.stderr)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args: argparse.Namespace = _build_parser().parse_args(argv)

    if args.status:
        load_state()
        print(json.dumps(current_state(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    enabled = bool(args.enable)
    reason = (args.reason or "").strip()
    if not reason:
        reason = "kill switch enabled by CLI" if enabled else "kill switch disabled by CLI"

    state = save_state(enabled, reason, args.actor or "cli")
    _audit(enabled, reason, args.actor or "cli", state)
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"state file: {state_path()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
