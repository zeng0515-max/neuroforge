"""代码格式化插件：对生成的代码进行格式化检查与建议。"""
from __future__ import annotations

import ast
import logging
from typing import Any, Dict, List, Optional

from app.plugins.plugin_base import PluginBase

logger = logging.getLogger(__name__)


class CodeFormatterPlugin(PluginBase):
    """代码格式化插件：检查代码风格并给出格式化建议。"""

    name = "code_formatter"
    version = "1.0.0"
    description = "对生成的代码进行格式化检查，确保代码风格一致性"

    def on_load(self) -> None:
        logger.info("代码格式化插件已加载")

    def on_unload(self) -> None:
        logger.info("代码格式化插件已卸载")

    def get_prompt_extension(self) -> str:
        return (
            "【代码格式化要求】\n"
            "生成代码时请注意以下规范：\n"
            "- 使用 4 空格缩进\n"
            "- 遵循 PEP 8 命名规范：类名 PascalCase，函数/变量 snake_case，常量 UPPER_CASE\n"
            "- 合理的空行分隔（类/函数间 2 空行，方法间 1 空行）\n"
            "- 导入顺序：标准库 → 第三方库 → 本地模块\n"
            "- 行长度不超过 120 字符\n"
            "- 类型注解：函数参数和返回值应添加类型提示"
        )

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "format_code_check",
                    "description": "检查代码格式问题",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_code": {
                                "type": "string",
                                "description": "待检查的源代码",
                            },
                        },
                        "required": ["source_code"],
                    },
                },
            }
        ]

    def check_format(self, source_code: str) -> List[Dict[str, Any]]:
        """检查代码格式问题，返回格式化建议列表。"""
        suggestions: List[Dict[str, Any]] = []
        lines = source_code.split("\n")

        # 检查缩进
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            # 检查是否混用了 tab 和空格
            if "\t" in line[:1]:
                suggestions.append({
                    "line": line_no,
                    "type": "indentation",
                    "message": "使用了 Tab 缩进，建议统一使用 4 空格",
                    "severity": "warning",
                })
            # 检查行长度
            if len(line) > 120:
                suggestions.append({
                    "line": line_no,
                    "type": "line_length",
                    "message": f"行长度 {len(line)} 超过建议的 120 字符",
                    "severity": "warning",
                })

        # AST 检查命名规范
        try:
            tree = ast.parse(source_code)
            for node in ast.walk(tree):
                # 检查函数名（snake_case）
                if isinstance(node, ast.FunctionDef):
                    if not node.name.islower() and not node.name.startswith("__"):
                        suggestions.append({
                            "line": getattr(node, "lineno", 0),
                            "type": "naming",
                            "message": f"函数名 '{node.name}' 应使用 snake_case 风格",
                            "severity": "info",
                        })
                # 检查类名（PascalCase）
                if isinstance(node, ast.ClassDef):
                    if not node.name[0].isupper():
                        suggestions.append({
                            "line": getattr(node, "lineno", 0),
                            "type": "naming",
                            "message": f"类名 '{node.name}' 应使用 PascalCase 风格",
                            "severity": "info",
                        })
        except SyntaxError:
            suggestions.append({
                "line": 0,
                "type": "syntax",
                "message": "源代码存在语法错误，无法完成格式检查",
                "severity": "info",
            })

        return suggestions