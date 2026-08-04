"""浏览器自动化工具（轻量实现）。

使用 requests 和 subprocess 实现基本的浏览器导航和快照能力，
避免引入 Playwright 等重型依赖。
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False


def browser_navigate(url: str, timeout: int = 30) -> Dict[str, Any]:
    """导航到指定 URL 并获取页面基本信息。

    使用 requests 获取页面内容，返回状态码、标题、文本摘要等信息。

    Args:
        url: 目标 URL
        timeout: 超时秒数

    Returns:
        {"status": int, "title": str, "text_preview": str, "url": str, "ok": bool}
    """
    from app.tools.tool_registry import tool_registry

    if not _REQUESTS_AVAILABLE:
        return {
            "ok": False,
            "error": "requests 库未安装，无法执行浏览器导航",
            "status": 0,
            "title": "",
            "text_preview": "",
            "url": url,
        }

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            allow_redirects=True,
        )
        resp.raise_for_status()

        # 提取标题
        title = ""
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            import re
            match = re.search(r"<title[^>]*>([^<]+)</title>", resp.text, re.IGNORECASE)
            if match:
                title = match.group(1).strip()

        # 提取文本预览
        text_preview = ""
        if "text/html" in content_type:
            import re
            # 去除 script/style 标签
            text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            # 提取纯文本
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            text_preview = text[:2000]

        result = {
            "ok": True,
            "status": resp.status_code,
            "title": title,
            "text_preview": text_preview,
            "url": str(resp.url),
            "content_length": len(resp.content),
            "content_type": content_type,
        }
        logger.info("浏览器导航成功：%s (status=%d, title=%s)", url, resp.status_code, title)
        return result

    except requests.RequestException as e:
        logger.warning("浏览器导航失败：%s - %s", url, e)
        return {
            "ok": False,
            "error": str(e),
            "status": 0,
            "title": "",
            "text_preview": "",
            "url": url,
        }


def browser_snapshot(url: Optional[str] = None, output_path: Optional[str] = None) -> Dict[str, Any]:
    """对页面进行快照。

    如果提供 url，先导航到该页面再截图；否则截图当前页面。
    使用系统浏览器在无头模式下截图，或返回页面 HTML 快照。

    轻量实现：保存页面 HTML 到临时文件并打开系统浏览器。

    Args:
        url: 目标 URL（可选，不提供则使用当前页面）
        output_path: 截图保存路径（可选）

    Returns:
        {"ok": bool, "path": str, "url": str, "message": str}
    """
    from app.tools.tool_registry import tool_registry

    if not url:
        return {
            "ok": False,
            "error": "请提供 URL 参数",
            "path": "",
            "url": "",
            "message": "",
        }

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # 先获取页面内容
    nav_result = browser_navigate(url)
    if not nav_result.get("ok"):
        return {
            "ok": False,
            "error": nav_result.get("error", "导航失败"),
            "path": "",
            "url": url,
            "message": "",
        }

    # 保存页面 HTML 快照
    save_path = output_path or str(
        Path(tempfile.gettempdir()) / f"browser_snapshot_{hash(url)}.html"
    )
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        Path(save_path).write_text(resp.text, encoding="utf-8")
        logger.info("浏览器快照已保存：%s", save_path)
        return {
            "ok": True,
            "path": save_path,
            "url": url,
            "message": f"页面 HTML 快照已保存到 {save_path}",
            "title": nav_result.get("title", ""),
            "status": nav_result.get("status", 0),
        }
    except Exception as e:
        logger.warning("浏览器快照失败：%s", e)
        return {
            "ok": False,
            "error": str(e),
            "path": "",
            "url": url,
            "message": "",
        }


# 注册工具
from app.tools.tool_registry import tool_registry, ToolPermissionLevel  # noqa: E402

@tool_registry.register(
    description="导航到指定 URL 并获取页面基本信息（标题、文本摘要、状态码等）",
    permission_level=ToolPermissionLevel.OBSERVE,
)
def browser_navigate_tool(url: str, timeout: int = 30) -> str:
    """导航到指定 URL。

    使用 requests 获取页面内容，返回结构化信息。
    适用于查看网页内容、API 文档、在线资源等。

    Args:
        url: 目标 URL
        timeout: 超时秒数（默认 30）

    Returns:
        JSON 格式的页面信息
    """
    result = browser_navigate(url, timeout=timeout)
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool_registry.register(
    description="对指定 URL 进行页面快照，保存 HTML 到本地文件",
    permission_level=ToolPermissionLevel.MODIFY,
)
def browser_snapshot_tool(url: str, output_path: str = "") -> str:
    """对页面进行快照。

    获取页面 HTML 内容并保存到本地文件。

    Args:
        url: 目标 URL
        output_path: 可选的输出路径

    Returns:
        JSON 格式的快照结果
    """
    result = browser_snapshot(url, output_path=output_path or None)
    return json.dumps(result, ensure_ascii=False, indent=2)


__all__ = [
    "browser_navigate",
    "browser_snapshot",
    "browser_navigate_tool",
    "browser_snapshot_tool",
]