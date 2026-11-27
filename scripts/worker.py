"""Background worker that consumes durable jobs from the SQLite task queue.

The worker is defensive on purpose: a single bad job, a transient SQLite lock
or a crashed agent must never take the process down. Failures are logged, the
job is marked failed (or retried while attempts remain), and the loop continues.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.task_queue import task_queue

logger = logging.getLogger("neuroforge.worker")


def _run_job(job: Dict[str, Any]) -> Dict[str, Any]:
    from app.graph.unified_engine import stream_engine

    payload = job.get("payload", {})
    task_id = job["task_id"]
    user_request = str(payload.get("user_request", ""))
    if not user_request:
        raise ValueError(f"job {task_id} has empty user_request")

    _, state = stream_engine(
        task_id=task_id,
        user_request=user_request,
        extra_args={k: v for k, v in payload.items() if k != "user_request"},
    )
    status = state.get("status") or {}
    phase = (
        status.get("phase")
        if isinstance(status, dict)
        else getattr(status, "phase", "done")
    )
    if hasattr(phase, "value"):
        phase = phase.value
    return {"phase": phase, "summary": str(state.get("summary", ""))[:2000]}


class _LeaseHeartbeat:
    """Renew the job lease on a background thread while the job runs."""

    def __init__(self, task_id: str, worker_id: str, lock_token: str, interval: float) -> None:
        self._task_id = task_id
        self._worker_id = worker_id
        self._lock_token = lock_token
        self._interval = max(interval, 0.05)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"lease-{task_id}", daemon=True
        )

    def __enter__(self) -> "_LeaseHeartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=max(self._interval, 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not task_queue.heartbeat(self._task_id, self._worker_id, self._lock_token):
                    logger.warning(
                        "Lost lease for job %s (cancelled or reclaimed); stopping heartbeat",
                        self._task_id,
                    )
                    return
            except Exception:  # noqa: BLE001 - heartbeat must never kill the worker
                logger.exception("Heartbeat failed for job %s", self._task_id)
                return


def _process_once(worker_id: str, lease_seconds: int) -> int:
    jobs = task_queue.claim(worker_id, limit=1)
    if not jobs:
        return 0
    job = jobs[0]
    task_id = job["task_id"]
    lock_token = job.get("lock_token", "")
    with _LeaseHeartbeat(task_id, worker_id, lock_token, lease_seconds / 3.0):
        try:
            result = _run_job(job)
            if not task_queue.complete(task_id, worker_id, result=result, lock_token=lock_token):
                logger.warning(
                    "Job %s could not be completed (cancelled or lease lost)", task_id
                )
        except Exception as exc:  # noqa: BLE001 - worker must always mark failure
            logger.exception("Job %s failed", task_id)
            task_queue.fail(
                task_id,
                worker_id,
                f"{type(exc).__name__}: {exc}",
                lock_token=lock_token,
            )
    return 1


def _reap_stale(lease_seconds: int) -> int:
    requeued = task_queue.requeue_stale(timeout_seconds=lease_seconds)
    if requeued:
        logger.warning("Requeued %d stale job(s) after %ss lease timeout", requeued, lease_seconds)
    return requeued


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NeuroForge durable job worker")
    parser.add_argument("--worker-id", default=f"worker-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=600)
    parser.add_argument("--once", action="store_true", help="process one job and exit")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # The worker executes code and tools too, so it needs the same policy rules
    # as the API process (idempotent).
    try:
        from app.core.policy_rules import install_default_policy_rules

        install_default_policy_rules()
    except Exception:  # noqa: BLE001 - startup must survive a policy import error
        logger.exception("Failed to install default policy rules")

    # Restore a kill switch that an operator persisted earlier (for example via
    # the admin API), so a worker started during a freeze takes the freeze over.
    # Read once at startup on purpose: the switch is single-process semantics, a
    # running worker does not notice later toggles until it is restarted.
    try:
        from app.core.policy_state import load_state

        if load_state():
            logger.warning("Runtime kill switch is ENABLED; this worker will refuse mutating actions")
    except Exception:  # noqa: BLE001 - startup must survive a bad state file
        logger.exception("Failed to restore persisted runtime policy state; continuing with the switch off")

    # Reclaim leases left behind by crashed workers before taking new jobs.
    try:
        _reap_stale(args.lease_seconds)
    except Exception:  # noqa: BLE001 - startup must survive a locked DB
        logger.exception("Startup stale-job reaping failed; continuing")

    reap_interval = max(args.lease_seconds / 3.0, 1.0)
    next_reap = time.monotonic() + reap_interval
    print(f"[worker] {args.worker_id} started")

    while True:
        try:
            if time.monotonic() >= next_reap:
                _reap_stale(args.lease_seconds)
                next_reap = time.monotonic() + reap_interval
            processed = _process_once(args.worker_id, args.lease_seconds)
            if processed:
                print(f"[worker] processed {processed} job(s)")
        except Exception:  # noqa: BLE001 - the loop must never die
            logger.exception("Worker iteration failed; backing off %ss", args.poll_seconds)
            if args.once:
                print(f"[worker] {args.worker_id} stopped after failure")
                return 1
            time.sleep(max(args.poll_seconds, 0.5))
            continue
        if args.once:
            break
        time.sleep(args.poll_seconds)

    print(f"[worker] {args.worker_id} stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
