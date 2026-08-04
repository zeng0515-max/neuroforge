"""插件抽象基类定义。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PluginBase(ABC):
    """所有插件必须继承的抽象基类。

    插件可以：
    - 在 on_load 中初始化资源
    - 在 on_unload 中释放资源
    - 通过 get_prompt_extension 扩展系统提示词
    - 通过 get_tools 注册工具
    """

    name: str = ""
    version: str = "0.1.0"
    description: str = ""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}

    @abstractmethod
    def on_load(self) -> None:
        """插件加载时调用，用于初始化资源、注册钩子等。"""
        ...

    @abstractmethod
    def on_unload(self) -> None:
        """插件卸载时调用，用于释放资源、清理钩子等。"""
        ...

    def get_prompt_extension(self) -> str:
        """返回一段附加到系统提示词末尾的文本，用于扩展 LLM 能力。

        返回空字符串表示不扩展提示词。
        """
        return ""

    def get_tools(self) -> List[Dict[str, Any]]:
        """返回插件提供的工具列表（OpenAI Function Calling 格式）。

        每个工具为 dict，格式同 ToolSpec.to_openai_function() 返回值。
        """
        return []

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} v{self.version}>"