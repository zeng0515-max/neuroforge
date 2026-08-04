"""技能库 API（文档 3.6）：
- GET  /api/v1/tasks/skills/list   列出所有可用技能
- POST /api/v1/tasks/skills/match  根据需求文本自动匹配最佳技能
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("/skills/list")
async def list_skills() -> Dict[str, Any]:
    """列出所有可用技能（0 Token）。"""
    from app.tools.skill_registry import skill_registry
    return {"skills": skill_registry.list_skills()}


@router.post("/skills/match")
async def match_skill(body: Dict[str, Any]) -> Dict[str, Any]:
    """根据需求文本自动匹配最佳技能（0 Token）。"""
    from app.tools.skill_registry import skill_registry
    text = str(body.get("text") or "")
    skill = skill_registry.match_skill(text)
    return {"skill_id": skill.id, "skill_name": skill.name, "skill": skill.to_dict()}