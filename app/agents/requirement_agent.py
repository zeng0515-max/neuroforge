from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List

from app.db.sqlite_client import db
from app.graph.state import PipelinePhase, PipelineState, RequirementSpec, ensure_status

logger = logging.getLogger(__name__)


# ====================================================================== #
#  纯本地需求分析（0 Token）
#  规则：从用户自然语言中提取标题、目标、功能点
# ====================================================================== #

# 关键词→技术栈映射
_TECH_KEYWORDS = {
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "sqlite": "SQLite", "mysql": "MySQL", "redis": "Redis",
    "requests": "requests", "pandas": "pandas", "numpy": "numpy",
    "pytest": "pytest", "asyncio": "asyncio", "aiohttp": "aiohttp",
    "websocket": "WebSocket", "socket": "Socket",
    "递归": "Python", "迭代": "Python", "排序": "Python",
    "链表": "Python", "树": "Python", "图": "Python",
}

# 功能性关键词提取
_FUNC_PATTERNS = [
    (r"实现|编写|写一个|做一个|生成|创建", "实现核心功能"),
    (r"校验|验证|检查", "输入校验与异常处理"),
    (r"缓存|cache|memo", "缓存优化"),
    (r"日志|log", "日志记录"),
    (r"测试|test", "单元测试覆盖"),
    (r"并发|异步|async|多线程", "并发/异步支持"),
    (r"排序|查找|搜索", "算法实现"),
    (r"转换|解析|parse|convert", "数据转换处理"),
    (r"计算|compute|calculate", "计算逻辑"),
    (r"导出|导入|export|import", "数据导入导出"),
    (r"接口|api|endpoint", "API 接口"),
    (r"数据库|db|database|sql", "数据库操作"),
]


def _local_parse_requirement(user_request: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    """纯本地规则解析：从自然语言提取结构化需求（0 Token）。"""
    text = user_request.strip()

    # 标题：取前 15 个字或第一个逗号/句号前的内容
    title = text[:15]
    for sep in ["，", "。", "；", ",", ".", ";", "\n"]:
        if sep in text:
            title = text[:text.index(sep)][:15]
            break
    if len(text) > 15 and len(title) >= 15:
        title = title[:14] + "…"

    # 目标：完整用户请求
    goal = text

    # 功能性需求：关键词匹配
    func_reqs: List[str] = []
    for pattern, desc in _FUNC_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE) and desc not in func_reqs:
            func_reqs.append(desc)
    # 至少保证 3 条
    defaults = ["实现用户描述的核心功能", "输入参数校验与异常处理", "代码符合 PEP8 规范"]
    for d in defaults:
        if len(func_reqs) >= 3:
            break
        if d not in func_reqs:
            func_reqs.append(d)

    # 非功能需求
    non_func = ["代码可在 Python 3.10+ 运行", "禁止使用 eval/exec"]
    if re.search(r"性能|高效|快速|performance", text, re.IGNORECASE):
        non_func.append("性能优化")

    # 技术栈：关键词匹配
    tech_stack = ["Python 3.10+"]
    text_lower = text.lower()
    for kw, tech in _TECH_KEYWORDS.items():
        if kw.lower() in text_lower and tech not in tech_stack:
            tech_stack.append(tech)

    # 附加约束
    if extra:
        for v in (extra.get("tech_stack") or []):
            if v not in tech_stack:
                tech_stack.append(v)

    # 验收标准
    criteria = [
        "代码语法正确，可正常导入",
        "核心功能按预期工作",
        "边界条件和异常情况有处理",
    ]

    # 假设
    assumptions = ["默认使用 Python 标准库", "如需第三方依赖会注明"]

    return {
        "title": title,
        "goal": goal,
        "functional_requirements": func_reqs[:5],
        "non_functional_requirements": non_func,
        "tech_stack": tech_stack,
        "acceptance_criteria": criteria,
        "assumptions": assumptions,
    }


class RequirementAgent:
    """需求分析 Agent：纯本地规则解析，0 Token 消耗。"""

    def run(self, state: PipelineState) -> RequirementSpec:
        task_id = state.get("task_id", "")
        from app.graph.state import normalize_state
        state = normalize_state(state)
        user_request = state.get("user_request", "").strip()
        if not user_request:
            return RequirementSpec()

        t0 = time.perf_counter()
        extra = state.get("extra_args") or {}
        parsed = _local_parse_requirement(user_request, extra)
        duration_ms = int((time.perf_counter() - t0) * 1000)

        # 记录日志（0 Token）
        db.add_stage_log(
            task_id=task_id,
            stage=PipelinePhase.REQUIREMENT.value,
            prompt_text=user_request,
            llm_output=f"[纯本地解析] {parsed['title']}",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            duration_ms=duration_ms,
        )
        db.update_task_phase(
            task_id,
            phase=PipelinePhase.REQUIREMENT.value,
        )

        spec = RequirementSpec(**parsed)
        logger.info("需求分析完成（本地）：%s，耗时 %dms，0 Token", spec.title, duration_ms)
        return spec


# ------------------------------------------------------------------ #
#  LangGraph 节点函数
# ------------------------------------------------------------------ #

def requirement_node(state: PipelineState) -> Dict[str, Any]:
    """LangGraph 节点：执行需求分析并更新状态。"""
    task_id = state.get("task_id", "")
    agent = RequirementAgent()
    spec = agent.run(state)

    status = ensure_status(state)
    status.phase = PipelinePhase.REQUIREMENT
    status.touch()
    db.update_task_phase(task_id, phase=PipelinePhase.REQUIREMENT.value)
    return {
        "requirement": spec,
        "status": status,
    }


__all__ = [
    "RequirementAgent",
    "requirement_node",
    "RequirementSpec",
]
