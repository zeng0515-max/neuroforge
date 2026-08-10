from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_jobs_roundtrip_via_api() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/jobs",
            json={"user_request": "write a thread-safe LRU", "priority": 7},
        )
        assert created.status_code == 200
        body = created.json()
        task_id = body["task_id"]

        fetched = client.get(f"/api/v1/jobs/{task_id}")
        assert fetched.status_code == 200
        assert fetched.json()["payload"]["user_request"] == "write a thread-safe LRU"

        stats = client.get("/api/v1/jobs/stats")
        assert stats.status_code == 200
        assert stats.json()["queued"] >= 1

        cancelled = client.post(f"/api/v1/jobs/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
