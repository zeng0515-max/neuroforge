"""Background worker that consumes durable jobs from the SQLite task queue."""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.task_queue import task_queue


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


def _process_once(worker_id: str, lease_seconds: int) -> int:
    jobs = task_queue.claim(worker_id, limit=1)
    if not jobs:
        return 0
    job = jobs[0]
    task_id = job["task_id"]
    try:
        result = _run_job(job)
        task_queue.complete(
            task_id,
            worker_id,
            result=result,
            lock_token=job.get("lock_token", ""),
        )
    except Exception as exc:  # noqa: BLE001 - worker must always mark failure
        task_queue.fail(
            task_id,
            worker_id,
            f"{type(exc).__name__}: {exc}",
            lock_token=job.get("lock_token", ""),
        )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="NeuroForge durable job worker")
    parser.add_argument("--worker-id", default=f"worker-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=600)
    parser.add_argument("--once", action="store_true", help="process one job and exit")
    args = parser.parse_args()

    requeued = task_queue.requeue_stale(timeout_seconds=args.lease_seconds)
    if requeued:
        print(f"[worker] requeued {requeued} stale job(s)")

    print(f"[worker] {args.worker_id} started")
    while True:
        processed = _process_once(args.worker_id, args.lease_seconds)
        if processed:
            print(f"[worker] processed {processed} job(s)")
        if args.once:
            break
        time.sleep(args.poll_seconds)
    print(f"[worker] {args.worker_id} stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
