"""Concurrency regressions for the durable queue and worker leases.

These tests simulate *two* worker processes by giving each one its own engine
against the same SQLite file — the failure mode the previous
select-then-update implementation had (two workers claiming one job).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.task_queue import QueueStatus, TaskQueue
from app.db.models import Base, QueueTask


def _make_independent_queue(db_path, *, busy_timeout: float = 5.0) -> tuple[TaskQueue, sessionmaker]:
    """One queue per engine/connection, like one OS process per worker."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": busy_timeout},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return TaskQueue(session_factory=factory), factory


def _seed(db_path, task_ids) -> TaskQueue:
    queue, _ = _make_independent_queue(db_path)
    for task_id in task_ids:
        queue.enqueue(task_id, {"user_request": f"job {task_id}"})
    return queue


def test_claim_is_atomic_across_two_workers(tmp_path) -> None:
    db_path = tmp_path / "queue.db"
    task_ids = [f"job-{i:02d}" for i in range(20)]
    _seed(db_path, task_ids)

    queue_a, _ = _make_independent_queue(db_path)
    queue_b, _ = _make_independent_queue(db_path)

    claimed_by: dict[str, str] = {}
    claimed_lock = threading.Lock()
    start = threading.Barrier(2)

    def drain(queue: TaskQueue, worker_id: str) -> None:
        start.wait()
        while True:
            jobs = queue.claim(worker_id, limit=1)
            if not jobs:
                return
            job = jobs[0]
            with claimed_lock:
                previous = claimed_by.setdefault(job["task_id"], worker_id)
            assert previous == worker_id, f"{job['task_id']} claimed by both workers"
            queue.complete(
                job["task_id"],
                worker_id,
                result={"ok": True},
                lock_token=job["lock_token"],
            )

    threads = [
        threading.Thread(target=drain, args=(queue_a, "worker-a")),
        threading.Thread(target=drain, args=(queue_b, "worker-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "worker thread hung"

    assert sorted(claimed_by) == sorted(task_ids), "some jobs were never claimed"
    assert len(set(claimed_by.values())) == 2, "only one worker did any work"

    stats = queue_a.stats().as_dict()
    assert stats["succeeded"] == len(task_ids)
    assert stats["running"] == 0
    assert stats["queued"] == 0


def test_claim_stops_when_queue_is_drained(tmp_path) -> None:
    db_path = tmp_path / "queue.db"
    queue, _ = _make_independent_queue(db_path)
    assert queue.claim("worker-a", limit=3) == []
    queue.enqueue("only-one", {})
    assert len(queue.claim("worker-a", limit=3)) == 1
    assert queue.claim("worker-a", limit=3) == []


def test_cancelled_job_cannot_be_completed(tmp_path) -> None:
    db_path = tmp_path / "queue.db"
    queue, _ = _make_independent_queue(db_path)
    queue.enqueue("job-cancel", {})
    claimed = queue.claim("worker-a", limit=1)[0]

    assert queue.cancel("job-cancel") is True
    item = queue.get("job-cancel")
    assert item["status"] == QueueStatus.CANCELLED.value
    assert item["worker_id"] == ""

    assert (
        queue.complete(
            "job-cancel", "worker-a", result={"ok": True}, lock_token=claimed["lock_token"]
        )
        is False
    )
    assert queue.get("job-cancel")["status"] == QueueStatus.CANCELLED.value

    assert queue.fail("job-cancel", "worker-a", "late error", lock_token=claimed["lock_token"]) is False


def test_stale_lease_is_not_completable_by_old_worker(tmp_path) -> None:
    db_path = tmp_path / "queue.db"
    queue, factory = _make_independent_queue(db_path)
    queue.enqueue("job-stale", {})
    claimed = queue.claim("worker-old", limit=1)[0]

    with factory() as session:
        row = session.query(QueueTask).filter(QueueTask.task_id == "job-stale").one()
        row.started_at = datetime.now() - timedelta(hours=2)
        session.commit()

    assert queue.requeue_stale(timeout_seconds=60) == 1
    assert queue.get("job-stale")["status"] == QueueStatus.QUEUED.value
    # The old worker lost its lease: it may not write a result any more.
    assert (
        queue.complete(
            "job-stale", "worker-old", result={}, lock_token=claimed["lock_token"]
        )
        is False
    )
    new_claim = queue.claim("worker-new", limit=1)[0]
    assert new_claim["worker_id"] == "worker-new"
    assert new_claim["attempts"] == 2


def test_heartbeat_renews_lease_and_reports_lost_ownership(tmp_path) -> None:
    db_path = tmp_path / "queue.db"
    queue, factory = _make_independent_queue(db_path)
    queue.enqueue("job-live", {})
    claimed = queue.claim("worker-a", limit=1)[0]
    token = claimed["lock_token"]

    with factory() as session:
        row = session.query(QueueTask).filter(QueueTask.task_id == "job-live").one()
        row.started_at = datetime.now() - timedelta(seconds=500)
        session.commit()
    before = queue.get("job-live")["started_at"]

    assert queue.heartbeat("job-live", "worker-a", token) is True
    after = queue.get("job-live")["started_at"]
    assert after > before

    assert queue.heartbeat("job-live", "worker-b", token) is False
    assert queue.heartbeat("job-live", "worker-a", "wrong-token") is False

    queue.cancel("job-live")
    assert queue.heartbeat("job-live", "worker-a", token) is False
