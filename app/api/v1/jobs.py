"""Durable job queue API backed by SQLite."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.task_queue import task_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


class JobEnqueueRequest(BaseModel):
    user_request: str = Field(..., min_length=1, max_length=20000)
    task_id: Optional[str] = Field(default=None)
    payload: Dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)
    max_attempts: int = Field(default=3, ge=1, le=10)


class JobEnqueueResponse(BaseModel):
    task_id: str
    status: str
    queued: bool


@router.post("", response_model=JobEnqueueResponse)
async def enqueue_job(req: JobEnqueueRequest) -> JobEnqueueResponse:
    task_id = req.task_id or uuid.uuid4().hex[:12]
    payload = dict(req.payload)
    payload["user_request"] = req.user_request
    queued = task_queue.enqueue(
        task_id,
        payload,
        priority=req.priority,
        max_attempts=req.max_attempts,
    )
    if not queued:
        raise HTTPException(409, f"job {task_id} already exists")
    return JobEnqueueResponse(task_id=task_id, status="queued", queued=True)


@router.get("")
async def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    return {"items": task_queue.list_tasks(limit=limit, offset=offset)}


@router.get("/stats")
async def job_stats() -> Dict[str, Any]:
    return task_queue.stats().as_dict()


@router.get("/{task_id}")
async def get_job(task_id: str) -> Dict[str, Any]:
    job = task_queue.get(task_id)
    if job is None:
        raise HTTPException(404, f"job {task_id} not found")
    return job


@router.post("/{task_id}/cancel")
async def cancel_job(task_id: str) -> Dict[str, Any]:
    if not task_queue.cancel(task_id):
        raise HTTPException(409, f"job {task_id} is not queued/running")
    return {"task_id": task_id, "status": "cancelled"}
