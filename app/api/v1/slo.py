"""SLO endpoint."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.core.observability import metrics_collector
from app.core.slo import SLOConfig, evaluate_slo

router = APIRouter(prefix="/api/v1", tags=["slo"])


@router.get("/slo")
async def slo_status() -> Dict[str, Any]:
    return evaluate_slo(metrics_collector.snapshot(), SLOConfig())
