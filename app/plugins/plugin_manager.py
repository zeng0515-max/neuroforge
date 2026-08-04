"""插件管理器：加载、卸载、注册插件，支持从目录扫描。"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from app.plugins.plugin_base import PluginBase

logger = logging.getLogger(__name__)


class PluginManager:
    """插件管理器，管理插件的加载/卸载/注册。

    支持：
    - 从指定目录扫描并加载插件
    - 按名称加载/卸载单个插件
    - 获取所有已加载插件的提示词扩展
    - 获取所有已加载插件的工具列表
    """

    def __init__(self, plugin_dirs: Optional[List[str]] = None) -> None:
        self._plugins: Dict[str, PluginBase] = {}
        self._plugin_dirs: List[str] = plugin_dirs or []
        self._loaded: bool = False

    # ------------------------------------------------------------------ #
    #  加载 / 卸载
    # ------------------------------------------------------------------ #
    def load_all(self) -> int:
        """扫描所有插件目录并加载插件。返回成功加载的插件数。"""
        count = 0
        for plugin_dir in self._plugin_dirs:
            count += self._scan_directory(plugin_dir)
        self._loaded = True
        logger.info("插件管理器：共加载 %d 个插件", count)
        return count

    def load_plugin(self, plugin_class: Type[PluginBase], config: Optional[Dict[str, Any]] = None) -> bool:
        """加载单个插件实例。"""
        name = plugin_class.name or plugin_class.__name__
        if name in self._plugins:
            logger.warning("插件 %s 已加载，跳过", name)
            return False
        try:
            instance = plugin_class(config=config)
            instance.on_load()
            self._plugins[name] = instance
            logger.info("插件已加载：%s v%s", name, instance.version)
            return True
        except Exception as e:
            logger.error("加载插件 %s 失败：%s", name, e)
            return False

    def unload_plugin(self, name: str) -> bool:
        """卸载指定名称的插件。"""
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            logger.warning("插件 %s 未加载，无法卸载", name)
            return False
        try:
            plugin.on_unload()
            logger.info("插件已卸载：%s", name)
            return True
        except Exception as e:
            logger.error("卸载插件 %s 时出错：%s", name, e)
            return False

    def unload_all(self) -> None:
        """卸载所有已加载的插件。"""
        for name in list(self._plugins.keys()):
            self.unload_plugin(name)
        self._loaded = False

    # ------------------------------------------------------------------ #
    #  查询
    # ------------------------------------------------------------------ #
    def get_plugin(self, name: str) -> Optional[PluginBase]:
        """按名称获取已加载的插件实例。"""
        return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有已加载的插件信息。"""
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "class_name": p.__class__.__name__,
            }
            for p in self._plugins.values()
        ]

    # ------------------------------------------------------------------ #
    #  提示词 / 工具扩展
    # ------------------------------------------------------------------ #
    def get_prompt_extensions(self) -> str:
        """聚合所有已加载插件的提示词扩展。"""
        parts = []
        for name, plugin in self._plugins.items():
            try:
                ext = plugin.get_prompt_extension()
                if ext:
                    parts.append(f"--- {name} ---\n{ext}")
            except Exception as e:
                logger.warning("插件 %s 的 get_prompt_extension 失败：%s", name, e)
        return "\n\n".join(parts)

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """聚合所有已加载插件的工具列表。"""
        tools: List[Dict[str, Any]] = []
        for name, plugin in self._plugins.items():
            try:
                tools.extend(plugin.get_tools())
            except Exception as e:
                logger.warning("插件 %s 的 get_tools 失败：%s", name, e)
        return tools

    # ------------------------------------------------------------------ #
    #  内部
    # ------------------------------------------------------------------ #
    def _scan_directory(self, plugin_dir: str) -> int:
        """扫描单个目录，加载其中的插件类。"""
        path = Path(plugin_dir)
        if not path.exists() or not path.is_dir():
            logger.warning("插件目录不存在：%s", plugin_dir)
            return 0

        count = 0
        # 将目录加入 sys.path 以便 importlib 能找到
        import sys
        parent = str(path.parent.resolve())
        if parent not in sys.path:
            sys.path.insert(0, parent)

        # 遍历目录下的 .py 文件（排除 __init__）
        for item in path.iterdir():
            if item.suffix != ".py" or item.name == "__init__.py":
                continue
            module_name = item.stem
            try:
                module = importlib.import_module(f"{path.name}.{module_name}")
                # 查找模块中 PluginBase 的子类
                for _name, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, PluginBase)
                        and obj is not PluginBase
                        and not getattr(obj, "__abstractmethods__", None)
                    ):
                        if self.load_plugin(obj):
                            count += 1
            except Exception as e:
                logger.warning("扫描插件模块 %s 失败：%s", module_name, e)

        return count