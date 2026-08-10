"""Prompt 注入防护与输出安全检测模块。

OWASP LLM Top 10 防护覆盖：
- Prompt Injection / Insecure Output Handling / Sensitive Information Disclosure
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Pattern, Set

from app.core.config import settings

logger = logging.getLogger(__name__)


class SecuritySeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class SecurityFinding:
    severity: SecuritySeverity
    category: str
    description: str
    remediation: str = ""
    matched_text: str = ""
    pattern: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


# ====================================================================== #
#  注入模式正则（OWASP LLM Top 10 覆盖）
# ====================================================================== #

_SYSTEM_PROMPT_LEAK_PATTERNS: List[Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|directions|prompts)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|above|prior)\s+(instructions|directions|prompts)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions|directions)", re.IGNORECASE),
    re.compile(r"(print|show|output|reveal|tell\s+me|what\s+is)\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE),
    re.compile(r"repeat\s+(all\s+)?(the\s+)?(above|previous|initial)\s+(text|prompt|message|instructions)", re.IGNORECASE),
    re.compile(r"(你(的\s+)?(系统\s+)?提示词\s+是\s+什么|忽略\s+(以上|之前|所有)\s+(指令|提示|要求)|输出\s+你(的\s+)?(系统\s+)?提示词)", re.IGNORECASE),
]

_ROLE_PLAY_PATTERNS: List[Pattern[str]] = [
    re.compile(r"(you\s+are\s+now|act\s+as\s+if\s+you\s+are|pretend\s+(to\s+be|that\s+you\s+are)|from\s+now\s+on\s+you\s+are)", re.IGNORECASE),
    re.compile(r"(你现在\s+(是|扮演|作为)|假装\s+你\s+是|从现在开始\s+你\s+是|请\s+扮演)", re.IGNORECASE),
]

_DELIMITER_BYPASS_PATTERNS: List[Pattern[str]] = [
    re.compile(r"-{5,}"),
    re.compile(r"_+end\s+(of\s+)?(text|input|prompt|message)_+", re.IGNORECASE),
    re.compile(r"<\|im_end\|>|<\|end\|>|<\s*/\s*s\s*>"),
]

_BASE64_PATTERNS: List[Pattern[str]] = [
    re.compile(r"(?:begin|decode|decode64|base64)\s*(?:decode|base64)?\s*[:\s].{20,}[A-Za-z0-9+/=]{20,}", re.IGNORECASE),
    re.compile(r"(以下\s+(base64|base-64|编码)\s+(内容|文本|指令)|base64\s+解码\s*[:：])", re.IGNORECASE),
]

_SQL_INJECTION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|EXEC|EXECUTE)\b\s+", re.IGNORECASE),
    re.compile(r"(--|#|/\*)\s*(OR|AND)\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+", re.IGNORECASE),
    re.compile(r";\s*(DROP|DELETE|UPDATE|INSERT)\s+", re.IGNORECASE),
    re.compile(r"\b(WAITFOR|DELAY|BENCHMARK|SLEEP)\b", re.IGNORECASE),
    re.compile(r"information_schema|xp_cmdshell", re.IGNORECASE),
    re.compile(r"CHAR\s*\(\s*\d+|CONVERT\s*\(\s*(int|varchar)", re.IGNORECASE),
]

_XSS_INJECTION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<iframe[^>]*>|javascript\s*:", re.IGNORECASE),
    re.compile(r"on\w+\s*=\s*['\"]?[^'\"]*", re.IGNORECASE),
    re.compile(r"<img[^>]*onerror\s*=", re.IGNORECASE),
    re.compile(r"document\.(cookie|location|write)", re.IGNORECASE),
    re.compile(r"alert\s*\(\s*['\"]", re.IGNORECASE),
]

_COMMAND_INJECTION_PATTERNS: List[Pattern[str]] = [
    re.compile(r";\s*(ls|cat|rm|chmod|chown|wget|curl|bash|sh|nc|python|perl|ruby)\b", re.IGNORECASE),
    re.compile(r"\|\s*(ls|cat|rm|chmod|chown|wget|curl|bash|sh|nc)\b", re.IGNORECASE),
    re.compile(r"`[^`]+`|\$\([^)]+\)", re.IGNORECASE),
    re.compile(r">\s*/etc/(passwd|shadow)", re.IGNORECASE),
    re.compile(r"\b(wget|curl)\s+http", re.IGNORECASE),
    re.compile(r"&&\s*(rm|chmod|chown|bash|sh|nc)\b", re.IGNORECASE),
]

_PATH_TRAVERSAL_PATTERNS: List[Pattern[str]] = [
    re.compile(r"(?:\.\.\\|\.\./){2,}"),
    re.compile(r"%2e%2e%2f|%2e%2e/", re.IGNORECASE),
    re.compile(r"etc/(passwd|shadow|hosts)", re.IGNORECASE),
    re.compile(r"boot\.ini|win\.ini|system32", re.IGNORECASE),
    re.compile(r"/proc/self/(environ|cmdline)", re.IGNORECASE),
    re.compile(r"file:///", re.IGNORECASE),
]

_LDAP_INJECTION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"[\*\)\(].*?\)\s*\(", re.IGNORECASE),
    re.compile(r"\|\s*\(.*?=.*?\)", re.IGNORECASE),
    re.compile(r"objectClass\s*=\s*.*\*", re.IGNORECASE),
    re.compile(r"\(\|.*?\(.*?=.*?\)", re.IGNORECASE),
    re.compile(r"admin\s*\)", re.IGNORECASE),
]

_INJECTION_PATTERNS: List[Pattern[str]] = (
    _SYSTEM_PROMPT_LEAK_PATTERNS + _ROLE_PLAY_PATTERNS
    + _DELIMITER_BYPASS_PATTERNS + _BASE64_PATTERNS
    + _SQL_INJECTION_PATTERNS + _XSS_INJECTION_PATTERNS
    + _COMMAND_INJECTION_PATTERNS + _PATH_TRAVERSAL_PATTERNS
    + _LDAP_INJECTION_PATTERNS
)


# ====================================================================== #
#  PII 检测模式
# ====================================================================== #

_EMAIL_PATTERN: Pattern[str] = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_PHONE_PATTERN: Pattern[str] = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}"
)
_CREDIT_CARD_PATTERN: Pattern[str] = re.compile(
    r"\b(?:\d{4}[- ]?){3}\d{4}\b"
)
_SSN_PATTERN: Pattern[str] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CHINA_ID_PATTERN: Pattern[str] = re.compile(
    r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
)
_IP_PATTERN: Pattern[str] = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)
_SECRET_PATTERNS: List[Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"api[_-]?key[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"token[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"AKID[A-Za-z0-9]{15,}", re.IGNORECASE),
    re.compile(r"secret[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"password[=:]['\"]?[^\s'\"]{4,}", re.IGNORECASE),
]


# ====================================================================== #
#  输出敏感内容检测
# ====================================================================== #

_API_KEY_PATTERNS: List[Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"api[_-]?key[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"token[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"AKID[A-Za-z0-9]{15,}", re.IGNORECASE),
    re.compile(r"secret[=:]['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
]
_FILE_PATH_PATTERNS: List[Pattern[str]] = [
    re.compile(r"/etc/(passwd|shadow|hosts|config)", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_]+/\.", re.IGNORECASE),
    re.compile(r"~/?\.(ssh|aws|config|gitconfig)", re.IGNORECASE),
    re.compile(r"/var/log/", re.IGNORECASE),
    re.compile(r"\\\\?[A-Za-z]:\\\\", re.IGNORECASE),
]
_CODE_EXEC_PATTERNS: List[Pattern[str]] = [
    re.compile(r"exec\s*\(.*\)", re.IGNORECASE),
    re.compile(r"eval\s*\(.*\)", re.IGNORECASE),
    re.compile(r"os\.system\s*\(", re.IGNORECASE),
    re.compile(r"subprocess\.(call|Popen|run)\s*\(", re.IGNORECASE),
    re.compile(r"__import__\s*\(", re.IGNORECASE),
    re.compile(r"compile\s*\(.*\)", re.IGNORECASE),
]

_PII_CHECKS = [
    ("email", _EMAIL_PATTERN, "[REDACTED_EMAIL]"),
    ("phone", _PHONE_PATTERN, "[REDACTED_PHONE]"),
    ("credit_card", _CREDIT_CARD_PATTERN, "[REDACTED_CC]"),
    ("ssn", _SSN_PATTERN, "[REDACTED_SSN]"),
    ("china_id", _CHINA_ID_PATTERN, "[REDACTED_ID]"),
    ("ip", _IP_PATTERN, "[REDACTED_IP]"),
]


# ====================================================================== #
#  InputGuardrail
# ====================================================================== #

class InputGuardrailResult:
    def __init__(self, passed: bool = True, blocked: bool = False,
                 reason: str = "", matched_pattern: str = "",
                 details: Optional[Dict[str, Any]] = None) -> None:
        self.passed = passed
        self.blocked = blocked
        self.reason = reason
        self.matched_pattern = matched_pattern
        self.details = details or {}


class InputGuardrail:
    """用户输入注入检测防护栏。"""

    def __init__(self, enabled: bool = True,
                 whitelist: Optional[Set[str]] = None,
                 blacklist: Optional[Set[str]] = None) -> None:
        self._enabled = enabled
        self._whitelist: Set[str] = whitelist or set()
        self._blacklist: Set[str] = blacklist or set()
        self._patterns = _INJECTION_PATTERNS

    def _detect_prompt_injection_advanced(self, text: str) -> List[SecurityFinding]:
        """多策略高级注入检测：关键词密度 + 多模式组合命中。"""
        findings: List[SecurityFinding] = []
        injection_keywords = [
            "ignore", "forget", "disregard", "override", "bypass",
            "unrestricted", "jailbreak", "danmode", "evil",
        ]
        keyword_count = sum(1 for kw in injection_keywords if kw.lower() in text.lower())
        if keyword_count >= 3:
            findings.append(SecurityFinding(
                severity=SecuritySeverity.HIGH, category="prompt_injection",
                description=f"检测到 {keyword_count} 个注入关键词",
                remediation="审查输入上下文，限制指令覆盖",
                matched_text=text[:80], pattern="keyword_density",
            ))
        for i, pattern in enumerate(self._patterns):
            matches = pattern.findall(text)
            if matches:
                findings.append(SecurityFinding(
                    severity=SecuritySeverity.MEDIUM, category="prompt_injection",
                    description=f"正则模式 {i} 命中 {len(matches)} 处",
                    remediation="过滤或转义匹配内容",
                    matched_text=str(matches[0])[:80] if matches else "",
                    pattern=pattern.pattern[:80],
                ))
                if len(findings) >= 5:
                    break
        return findings

    def check(self, text: str) -> InputGuardrailResult:
        if not self._enabled or not text:
            return InputGuardrailResult()
        if self._whitelist:
            for kw in self._whitelist:
                if kw.lower() in text.lower():
                    return InputGuardrailResult()
        if self._blacklist:
            for kw in self._blacklist:
                if kw.lower() in text.lower():
                    return InputGuardrailResult(
                        passed=False, blocked=True,
                        reason=f"输入包含黑名单关键词: {kw}",
                        matched_pattern="blacklist", details={"keyword": kw},
                    )
        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                matched = match.group(0)
                logger.warning("输入注入检测命中: pattern=%s, matched=%s",
                               pattern.pattern[:50], matched[:80])
                return InputGuardrailResult(
                    passed=False, blocked=True,
                    reason=f"检测到疑似注入模式: {matched[:100]}",
                    matched_pattern=pattern.pattern[:80],
                    details={"matched": matched, "pattern": pattern.pattern[:80]},
                )
        return InputGuardrailResult()

    def check_request(self, request_text: str) -> None:
        result = self.check(request_text)
        if not result.passed and result.blocked:
            raise ValueError(result.reason)


# ====================================================================== #
#  OutputGuardrail
# ====================================================================== #

class OutputGuardrailResult:
    def __init__(self, passed: bool = True, sanitized: bool = False,
                 issues: Optional[List[Dict[str, Any]]] = None,
                 sanitized_text: str = "") -> None:
        self.passed = passed
        self.sanitized = sanitized
        self.issues = issues or []
        self.sanitized_text = sanitized_text


class OutputGuardrail:
    """LLM 输出内容安全检测防护栏。"""

    def __init__(self, enabled: bool = True, auto_sanitize: bool = False) -> None:
        self._enabled = enabled
        self._auto_sanitize = auto_sanitize

    def _llm_sanitize_output(self, text: str, findings: List[Dict[str, Any]]) -> str:
        """LLM 输出净化存根 — 标记为需 LLM 审核。"""
        if not findings:
            return text
        logger.warning("LLM 输出需 LLM 审核: %d 个问题", len(findings))
        return f"[LLM_SANITIZE_PENDING] {text}"

    def check(self, text: str) -> OutputGuardrailResult:
        if not self._enabled or not text:
            return OutputGuardrailResult()
        issues: List[Dict[str, Any]] = []
        sanitized = text

        for pattern in _API_KEY_PATTERNS:
            for match in pattern.findall(text):
                issues.append({"type": "api_key_leak", "severity": "CRITICAL",
                               "matched": match[:30] + "...", "pattern": pattern.pattern[:60]})
                if self._auto_sanitize:
                    sanitized = sanitized.replace(match, "[REDACTED_API_KEY]")

        for pattern in _FILE_PATH_PATTERNS:
            m = pattern.search(text)
            if m:
                issues.append({"type": "path_leak", "severity": "WARNING",
                               "matched": m.group(0)[:50], "pattern": pattern.pattern[:60]})
                if self._auto_sanitize:
                    sanitized = sanitized.replace(m.group(0), "[REDACTED_PATH]")

        for pattern in _CODE_EXEC_PATTERNS:
            m = pattern.search(text)
            if m:
                issues.append({"type": "code_exec_instruction", "severity": "WARNING",
                               "matched": m.group(0)[:50], "pattern": pattern.pattern[:60]})
                if self._auto_sanitize:
                    sanitized = sanitized.replace(m.group(0), "[REDACTED_CODE]")

        for pii_type, pattern, replacement in _PII_CHECKS:
            m = pattern.search(text)
            if m:
                issues.append({"type": f"pii_{pii_type}", "severity": "HIGH",
                               "matched": m.group(0)[:50], "pattern": pattern.pattern[:60]})
                if self._auto_sanitize:
                    sanitized = sanitized.replace(m.group(0), replacement)

        if self._auto_sanitize and issues:
            sanitized = self._llm_sanitize_output(sanitized, issues)

        return OutputGuardrailResult(
            passed=len(issues) == 0,
            sanitized=bool(issues) and self._auto_sanitize,
            issues=issues,
            sanitized_text=sanitized if self._auto_sanitize else text,
        )


# ====================================================================== #
#  完整安全扫描
# ====================================================================== #

def run_full_security_scan(input_text: str, output_text: str = "") -> List[SecurityFinding]:
    """运行完整安全扫描，返回所有安全发现。覆盖 OWASP LLM Top 10 主要风险。"""
    findings: List[SecurityFinding] = []
    input_guard = InputGuardrail()
    input_result = input_guard.check(input_text)
    if not input_result.passed:
        findings.append(SecurityFinding(
            severity=SecuritySeverity.HIGH, category="prompt_injection",
            description=input_result.reason, remediation="拒绝或改写注入输入",
            matched_text=input_result.details.get("matched", "")[:100],
            pattern=input_result.matched_pattern,
        ))
    findings.extend(input_guard._detect_prompt_injection_advanced(input_text))

    if output_text:
        output_guard = OutputGuardrail(auto_sanitize=False)
        output_result = output_guard.check(output_text)
        for issue in output_result.issues:
            findings.append(SecurityFinding(
                severity=SecuritySeverity.get(issue.get("severity", "MEDIUM"), SecuritySeverity.MEDIUM),
                category=issue.get("type", "unknown"),
                description=f"输出检测到敏感内容: {issue.get('matched', '')[:60]}",
                remediation="对敏感内容进行脱敏或过滤",
                matched_text=issue.get("matched", ""), pattern=issue.get("pattern", ""),
            ))
        for pii_type, pattern, _ in _PII_CHECKS:
            m = pattern.search(output_text)
            if m:
                findings.append(SecurityFinding(
                    severity=SecuritySeverity.HIGH, category=f"pii_leak_{pii_type}",
                    description=f"输出中发现 PII: {pii_type}",
                    remediation="对 PII 进行掩码或移除",
                    matched_text=m.group(0)[:80], pattern=pattern.pattern[:80],
                ))
    return findings


input_guardrail = InputGuardrail()
output_guardrail = OutputGuardrail()

__all__ = [
    "InputGuardrail", "InputGuardrailResult",
    "OutputGuardrail", "OutputGuardrailResult",
    "SecuritySeverity", "SecurityFinding",
    "run_full_security_scan", "input_guardrail", "output_guardrail",
]
