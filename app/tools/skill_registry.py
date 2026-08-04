"""技能注册表：加载 YAML 技能文件 + 关键词匹配引擎。

设计要点：
- 启动时一次性加载所有 YAML，运行时 0 IO
- 关键词匹配纯本地，0 Token
- 支持 skill_id 精确选择 + 关键词自动匹配
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / "skills"


@dataclass
class Skill:
    """单个技能定义。"""
    id: str = ""
    name: str = ""
    keywords: List[str] = field(default_factory=list)
    system_prompt: str = ""
    code_template: str = ""
    validation_rules: List[str] = field(default_factory=list)
    best_practices: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "keywords": self.keywords,
            "system_prompt": self.system_prompt,
            "code_template": self.code_template,
            "validation_rules": self.validation_rules,
            "best_practices": self.best_practices,
        }


class SkillRegistry:
    """技能注册表：加载、查询、匹配。"""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._loaded = False

    def load(self, skills_dir: Optional[Path] = None) -> None:
        """加载所有 YAML 技能文件。"""
        skills_dir = skills_dir or _SKILLS_DIR
        if not skills_dir.exists():
            logger.warning("技能目录不存在: %s", skills_dir)
            return

        self._skills.clear()
        for yml_file in sorted(skills_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yml_file.read_text(encoding="utf-8"))
                if not data:
                    continue
                skill = Skill(
                    id=str(data.get("id", yml_file.stem)),
                    name=str(data.get("name", data.get("id", ""))),
                    keywords=[str(k) for k in (data.get("keywords") or [])],
                    system_prompt=str(data.get("system_prompt", "")).strip(),
                    code_template=str(data.get("code_template", "")).strip(),
                    validation_rules=[str(r) for r in (data.get("validation_rules") or [])],
                    best_practices=[str(b) for b in (data.get("best_practices") or [])],
                )
                self._skills[skill.id] = skill
                logger.debug("已加载技能: %s (%s)", skill.id, skill.name)
            except Exception as e:
                logger.error("加载技能文件 %s 失败: %s", yml_file, e)

        self._loaded = True
        logger.info("技能库加载完成: %d 个技能", len(self._skills))

    @property
    def skills(self) -> Dict[str, Skill]:
        if not self._loaded:
            self.load()
        return self._skills

    def list_skills(self) -> List[Dict[str, Any]]:
        """列出所有技能（摘要信息）。"""
        if not self._loaded:
            self.load()
        return [
            {"id": s.id, "name": s.name, "keywords": s.keywords[:5],
             "best_practices_count": len(s.best_practices)}
            for s in self._skills.values()
        ]

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        """按 ID 获取技能。"""
        if not self._loaded:
            self.load()
        return self._skills.get(skill_id)

    def register_skill(self, skill: Skill, *, overwrite: bool = False) -> bool:
        """注册（或覆盖）一个技能到内存注册表。

        用于运行时技能沉淀：只写内存，不写 YAML 文件，避免破坏现有加载逻辑。
        若同 id 已存在且 overwrite=False，返回 False（不覆盖）。
        """
        if not self._loaded:
            self.load()
        if skill.id in self._skills and not overwrite:
            logger.debug("技能 %s 已存在，跳过注册", skill.id)
            return False
        self._skills[skill.id] = skill
        logger.info("已注册内存技能: %s (%s)", skill.id, skill.name)
        return True

    def match_skill(self, text: str) -> Skill:
        """根据文本关键词自动匹配最佳技能（0 Token）。

        匹配策略：
        1. 统计每个技能的关键词命中次数
        2. 取命中最多的技能
        3. 无命中时返回 general
        """
        if not self._loaded:
            self.load()
        if not text:
            return self._skills.get("general") or Skill(id="general", name="通用")

        text_lower = text.lower()
        best_skill: Optional[Skill] = None
        best_score = 0

        for skill in self._skills.values():
            score = 0
            for kw in skill.keywords:
                kw_lower = kw.lower()
                if kw_lower in text_lower:
                    score += 1
                    # 中文关键词额外加权（更精确）
                    if len(kw) >= 2:
                        score += 0.5
            if score > best_score:
                best_score = score
                best_skill = skill

        if best_skill is None or best_score == 0:
            return self._skills.get("general") or Skill(id="general", name="通用")

        logger.info("技能匹配: '%s' → %s (score=%.1f)", text[:30], best_skill.id, best_score)
        return best_skill

    def get_system_prompt(self, skill_id: Optional[str] = None, text: str = "") -> str:
        """获取技能的系统提示词。

        优先用 skill_id 精确选择，否则用 text 自动匹配。
        """
        if skill_id:
            skill = self.get_skill(skill_id)
            if skill:
                return skill.system_prompt

        if text:
            skill = self.match_skill(text)
            return skill.system_prompt

        # 默认
        skill = self._skills.get("general")
        return skill.system_prompt if skill else ""


# 全局单例
skill_registry = SkillRegistry()
