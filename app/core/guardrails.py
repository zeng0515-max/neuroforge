"""Prompt 注入防护与输出安全检测模块。

包含：
- InputGuardrail：检查用户输入中的注入模式
- OutputGuardrail：检查 LLM 输出中的敏感内容
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Pattern, Set

from app.core.config import settings

logger = logging.getLogger(__name__)


# ====================================================================== #
#  常见注入模式正则
# ====================================================================== #

# System prompt 泄露尝试
_SYSTEM_PROMPT_LEAK_PATTERNS: List[Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|directions|prompts)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|above|prior)\s+(instructions|directions|prompts)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions|directions)", re.IGNORECASE),
    re.compile(r"print\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"show\s+(me\s+)?(your\s+)?(system\s+)?(prompt|instructions)", re.IGNORECASE),
    re.compile(r"output\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"reveal\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"repeat\s+(all\s+)?(the\s+)?(above|previous|initial)\s+(text|prompt|message|instructions)", re.IGNORECASE),
    re.compile(r"what\s+is\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"tell\s+me\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"你(的\s+)?(系统\s+)?提示词\s+是\s+什么", re.IGNORECASE),
    re.compile(r"忽略\s+(以上|之前|所有)\s+(指令|提示|要求)", re.IGNORECASE),
    re.compile(r"输出\s+你(的\s+)?(系统\s+)?提示词", re.IGNORECASE),
]

# 角色扮演注入
_ROLE_PLAY_PATTERNS: List[Pattern[str]] = [
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+are", re.IGNORECASE),
    re.compile(r"pretend\s+(to\s+be|that\s+you\s+are)", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you\s+are", re.IGNORECASE),
    re.compile(r"你现在\s+(是|扮演|作为)", re.IGNORECASE),
    re.compile(r"假装\s+你\s+是", re.IGNORECASE),
    re.compile(r"从现在开始\s+你\s+是", re.IGNORECASE),
    re.compile(r"请\s+扮演", re.IGNORECASE),
]

# 分隔符绕过
_DELIMITER_BYPASS_PATTERNS: List[Pattern[str]] = [
    re.compile(r"-{5,}"),          # 多个连续横线（YAML 分隔符）
    re.compile(r"_+end\s+(of\s+)?(text|input|prompt|message)_+", re.IGNORECASE),
    re.compile(r"<\|im_end\|>"),   # ChatML 格式绕过
    re.compile(r"<\|end\|>"),      # 通用结束标记
    re.compile(r"<\s*/\s*s\s*>"),  # </s> 结束标记
]

# Base64 编码指令
_BASE64_PATTERNS: List[Pattern[str]] = [
    re.compile(r"(?:begin|decode|decode64|base64)\s*(?:decode|base64)?\s*[:\s].{20,}[A-Za-z0-9+/=]{20,}", re.IGNORECASE),
    re.compile(r"以下\s+(base64|base-64|编码)\s+(内容|文本|指令)", re.IGNORECASE),
    re.compile(r"base64\s+解码\s*[:：]", re.IGNORECASE),
]

# 合并所有注入模式
_INPUT_PATTERNS: List[Pattern[str]] = (
    _SYSTEM_PROMPT_LEAK_PATTERNS
    + _ROLE_PLAY_PATTERNS
    + _DELIMITER_BYPASS_PATTERNS
    + _BASE64_PATTERNS
)

# ====================================================================== #
#  输出敏感内容检测模式
# ====================================================================== #

# API Key / Token 泄露
_API_KEY_PATTERNS: List[Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),                # OpenAI 格式
    re.compile(r"api[_-]?key[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"token[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"AKID[A-Za-z0-9]{15,}", re.IGNORECASE),               # 阿里云 AccessKey
    re.compile(r"secret[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
]

# 文件路径泄露
_FILE_PATH_PATTERNS: List[Pattern[str]] = [
    re.compile(r"/etc/(passwd|shadow|hosts|config)", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_]+/\.", re.IGNORECASE),
    re.compile(r"~/?\.(ssh|aws|config|gitconfig)", re.IGNORECASE),
    re.compile(r"/var/log/", re.IGNORECASE),
    re.compile(r"\\\\?[A-Za-z]:\\\\", re.IGNORECASE),  # Windows 路径
]

# 代码执行指令
_CODE_EXEC_PATTERNS: List[Pattern[str]] = [
    re.compile(r"exec\s*\(.*\)", re.IGNORECASE),
    re.compile(r"eval\s*\(.*\)", re.IGNORECASE),
    re.compile(r"os\.system\s*\(", re.IGNORECASE),
    re.compile(r"subprocess\.(call|Popen|run)\s*\(", re.IGNORECASE),
    re.compile(r"__import__\s*\(", re.IGNORECASE),
    re.compile(r"compile\s*\(.*\)", re.IGNORECASE),
]


# ====================================================================== #
#  InputGuardrail
# ====================================================================== #

class InputGuardrailResult:
    """输入校验结果。"""
    passed: bool
    blocked: bool
    reason: str
    matched_pattern: str
    details: Dict[str, Any]

    def __init__(
        self,
        passed: bool = True,
        blocked: bool = False,
        reason: str = "",
        matched_pattern: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.passed = passed
        self.blocked = blocked
        self.reason = reason
        self.matched_pattern = matched_pattern
        self.details = details or {}


class InputGuardrail:
    """用户输入注入检测防护栏。

    检测模式：
    - System prompt 泄露尝试
    - 角色扮演注入
    - 分隔符绕过
    - Base64 编码指令

    支持可配置白名单/黑名单。
    """

    def __init__(
        self,
        enabled: bool = True,
        whitelist: Optional[Set[str]] = None,
        blacklist: Optional[Set[str]] = None,
    ) -> None:
        self._enabled = enabled
        self._whitelist: Set[str] = whitelist or set()
        self._blacklist: Set[str] = blacklist or set()
        self._patterns = _INPUT_PATTERNS

    def check(self, text: str) -> InputGuardrailResult:
        """检查输入文本是否存在注入模式。

        Args:
            text: 用户输入文本

        Returns:
            InputGuardrailResult: 校验结果
        """
        if not self._enabled or not text:
            return InputGuardrailResult()

        # 白名单检查：包含白名单关键词的直接放行
        if self._whitelist:
            for keyword in self._whitelist:
                if keyword.lower() in text.lower():
                    return InputGuardrailResult()

        # 黑名单检查：包含黑名单关键词的直接拦截
        if self._blacklist:
            for keyword in self._blacklist:
                if keyword.lower() in text.lower():
                    return InputGuardrailResult(
                        passed=False,
                        blocked=True,
                        reason=f"输入包含黑名单关键词: {keyword}",
                        matched_pattern="blacklist",
                        details={"keyword": keyword},
                    )

        # 正则模式匹配
        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                matched = match.group(0)
                logger.warning("输入注入检测命中: pattern=%s, matched=%s", pattern.pattern[:50], matched[:80])
                return InputGuardrailResult(
                    passed=False,
                    blocked=True,
                    reason=f"检测到疑似注入模式: {matched[:100]}",
                    matched_pattern=pattern.pattern[:80],
                    details={"matched": matched, "pattern": pattern.pattern[:80]},
                )

        return InputGuardrailResult()

    def check_request(self, request_text: str) -> None:
        """检查请求输入，失败时抛出 ValueError。

        方便 FastAPI 依赖注入使用。
        """
        result = self.check(request_text)
        if not result.passed and result.blocked:
            raise ValueError(result.reason)


# ====================================================================== #
#  OutputGuardrail
# ====================================================================== #

class OutputGuardrailResult:
    """输出校验结果。"""
    passed: bool
    sanitized: bool
    issues: List[Dict[str, Any]]
    sanitized_text: str

    def __init__(
        self,
        passed: bool = True,
        sanitized: bool = False,
        issues: Optional[List[Dict[str, Any]]] = None,
        sanitized_text: str = "",
    ) -> None:
        self.passed = passed
        self.sanitized = sanitized
        self.issues = issues or []
        self.sanitized_text = sanitized_text


class OutputGuardrail:
    """LLM 输出内容安全检测防护栏。

    检测模式：
    - API Key / Token 泄露
    - 文件路径泄露
    - 代码执行指令
    """

    def __init__(self, enabled: bool = True, auto_sanitize: bool = False) -> None:
        self._enabled = enabled
        self._auto_sanitize = auto_sanitize

    def check(self, text: str) -> OutputGuardrailResult:
        """检查 LLM 输出中是否包含敏感内容。

        Args:
            text: LLM 输出文本

        Returns:
            OutputGuardrailResult: 校验结果
        """
        if not self._enabled or not text:
            return OutputGuardrailResult()

        issues: List[Dict[str, Any]] = []
        sanitized = text

        # 1. 检查 API Key 泄露
        for pattern in _API_KEY_PATTERNS:
            matches = pattern.findall(text)
            for match in matches:
                issues.append({
                    "type": "api_key_leak",
                    "severity": "CRITICAL",
                    "matched": match[:30] + "...",
                    "pattern": pattern.pattern[:60],
                })
                if self._auto_sanitize:
                    sanitized = sanitized.replace(match, "[REDACTED_API_KEY]")

        # 2. 检查文件路径泄露
        for pattern in _FILE_PATH_PATTERNS:
            match = pattern.search(text)
            if match:
                issues.append({
                    "type": "path_leak",
                    "severity": "WARNING",
                    "matched": match.group(0)[:50],
                    "pattern": pattern.pattern[:60],
                })
                if self._auto_sanitize:
                    sanitized = sanitized.replace(match.group(0), "[REDACTED_PATH]")

        # 3. 检查代码执行指令
        for pattern in _CODE_EXEC_PATTERNS:
            match = pattern.search(text)
            if match:
                issues.append({
                    "type": "code_exec_instruction",
                    "severity": "WARNING",
                    "matched": match.group(0)[:50],
                    "pattern": pattern.pattern[:60],
                })
                if self._auto_sanitize:
                    sanitized = sanitized.replace(match.group(0), "[REDACTED_CODE]")

        passed = len(issues) == 0
        return OutputGuardrailResult(
            passed=passed,
            sanitized=not passed and self._auto_sanitize,
            issues=issues,
            sanitized_text=sanitized if self._auto_sanitize else text,
        )


# 全局单例
input_guardrail = InputGuardrail()
output_guardrail = OutputGuardrail()

__all__ = [
    "InputGuardrail",
    "InputGuardrailResult",
    "OutputGuardrail",
    "OutputGuardrailResult",
    "input_guardrail",
    "output_guardrail",
]