from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.task_queue import QueueStatus, TaskQueue
from app.db.models import Base, QueueTask


def _make_queue(tmp_path) -> tuple[TaskQueue, sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return TaskQueue(session_factory=factory), factory


def test_enqueue_claim_complete(tmp_path) -> None:
    queue, _ = _make_queue(tmp_path)
    assert queue.enqueue("job-1", {"user_request": "LRU"}, priority=8) is True
    assert queue.enqueue("job-1", {}) is False

    claimed = queue.claim("worker-a", limit=5)
    assert len(claimed) == 1
    assert claimed[0]["status"] == QueueStatus.RUNNING.value
    assert claimed[0]["attempts"] == 1
    assert claimed[0]["worker_id"] == "worker-a"

    lock = claimed[0].get("lock_token")
    assert queue.complete("job-1", "worker-a", result={"ok": True}, lock_token=lock) is True
    item = queue.get("job-1")
    assert item["status"] == QueueStatus.SUCCEEDED.value
    assert item["result"] == {"ok": True}


def test_fail_retries_then_marks_failed(tmp_path) -> None:
    queue, _ = _make_queue(tmp_path)
    queue.enqueue("job-retry", {}, max_attempts=2)

    for attempt in (1, 2):
        claimed = queue.claim("worker-a", limit=5)
        assert len(claimed) == 1
        assert claimed[0]["attempts"] == attempt
        assert queue.fail(
            "job-retry",
            "worker-a",
            f"boom {attempt}",
            lock_token=claimed[0]["lock_token"],
        ) is True

    item = queue.get("job-retry")
    assert item["status"] == QueueStatus.FAILED.value
    assert item["attempts"] == 2
    assert "boom 2" in item["error"]


def test_cancel_and_stats(tmp_path) -> None:
    queue, _ = _make_queue(tmp_path)
    queue.enqueue("job-cancel", {})
    queue.enqueue("job-done", {})
    done = queue.claim("worker-a", limit=5)
    done_by_id = {job["task_id"]: job for job in done}
    queue.complete(
        "job-done",
        "worker-a",
        {},
        lock_token=done_by_id["job-done"]["lock_token"],
    )
    assert queue.cancel("job-cancel") is True
    stats = queue.stats().as_dict()
    assert stats["queued"] == 0
    assert stats["succeeded"] == 1
    assert stats["cancelled"] == 1
    assert stats["total"] == 2


def test_requeue_stale_leases(tmp_path) -> None:
    queue, factory = _make_queue(tmp_path)
    queue.enqueue("job-stale", {})
    queue.claim("dead-worker", limit=5)

    with factory() as s:
        row = s.query(QueueTask).filter(QueueTask.task_id == "job-stale").one()
        row.started_at = datetime.now() - timedelta(seconds=7200)
        s.commit()

    assert queue.requeue_stale(timeout_seconds=60) == 1
    item = queue.get("job-stale")
    assert item["status"] == QueueStatus.QUEUED.value
    assert item["worker_id"] == ""
