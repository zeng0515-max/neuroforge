"""安全审查插件：对生成的代码做安全扫描，发现潜在风险。"""
from __future__ import annotations

import ast
import logging
from typing import Any, Dict, List

from app.plugins.plugin_base import PluginBase

logger = logging.getLogger(__name__)

# 已知的危险函数/模块调用模式
_DANGEROUS_PATTERNS = {
    "eval": "使用 eval 执行动态代码，存在代码注入风险",
    "exec": "使用 exec 执行动态代码，存在代码注入风险",
    "os.system": "直接调用系统命令，建议使用 subprocess 并严格校验参数",
    "os.popen": "使用 os.popen 执行命令，存在命令注入风险",
    "subprocess.Popen": "使用 subprocess.Popen 执行命令，需校验 shell=True 参数",
    "subprocess.call": "使用 subprocess.call 执行命令，需校验 shell=True 参数",
    "pickle.loads": "反序列化不可信数据可能导致任意代码执行",
    "pickle.load": "反序列化不可信数据可能导致任意代码执行",
    "yaml.load": "使用不安全的 yaml.load，建议改用 yaml.safe_load",
    "shutil.rmtree": "递归删除目录，需确保路径安全",
    "requests.request": "HTTP 请求，需校验 URL 避免 SSRF",
}


class SecurityScannerPlugin(PluginBase):
    """安全审查插件：扫描代码中的安全风险。"""

    name = "security_scanner"
    version = "1.0.0"
    description = "对生成的代码做安全扫描，发现潜在注入、命令执行等风险"

    def on_load(self) -> None:
        logger.info("安全审查插件已加载")

    def on_unload(self) -> None:
        logger.info("安全审查插件已卸载")

    def get_prompt_extension(self) -> str:
        return (
            "【安全审查要求】\n"
            "生成代码时请注意以下安全规范：\n"
            "- 避免使用 eval/exec 动态执行代码\n"
            "- 避免直接拼接系统命令，使用 subprocess 并严格校验参数\n"
            "- 避免反序列化不可信数据（pickle/yaml.load）\n"
            "- 文件操作注意路径穿越防护\n"
            "- HTTP 请求注意 SSRF 防护\n"
            "- 使用参数化查询防范 SQL 注入"
        )

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "scan_code_security",
                    "description": "扫描代码中的安全风险",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_code": {
                                "type": "string",
                                "description": "待扫描的源代码",
                            },
                        },
                        "required": ["source_code"],
                    },
                },
            }
        ]

    def scan_code(self, source_code: str) -> List[Dict[str, Any]]:
        """扫描源代码中的安全风险。返回问题列表。"""
        issues: List[Dict[str, Any]] = []
        lines = source_code.split("\n")

        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for pattern, risk_desc in _DANGEROUS_PATTERNS.items():
                if pattern in stripped:
                    issues.append({
                        "line": line_no,
                        "pattern": pattern,
                        "risk": risk_desc,
                        "severity": "high" if pattern in ("eval", "exec", "pickle.loads", "pickle.load") else "medium",
                    })

        # 尝试 AST 解析检查
        try:
            tree = ast.parse(source_code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and hasattr(node.func, "id"):
                    if node.func.id in ("eval", "exec", "compile"):
                        lineno = getattr(node, "lineno", 0)
                        if not any(i["line"] == lineno for i in issues):
                            issues.append({
                                "line": lineno,
                                "pattern": node.func.id,
                                "risk": f"使用 {node.func.id} 执行动态代码，存在代码注入风险",
                                "severity": "high",
                            })
        except SyntaxError:
            issues.append({
                "line": 0,
                "pattern": "syntax_error",
                "risk": "源代码存在语法错误，无法完整扫描",
                "severity": "info",
            })

        return issues