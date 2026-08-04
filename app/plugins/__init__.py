"""插件系统包。"""
from __future__ import annotations

from app.plugins.plugin_base import PluginBase
from app.plugins.plugin_manager import PluginManager

__all__ = [
    "PluginBase",
    "PluginManager",
]