from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_FILE)

PROJECT_ROOT_DEFAULT = Path(__file__).resolve().parents[2]


def read_env_file() -> dict:
    """读取 .env 文件内容为 dict（保留注释不解析，仅取 key=value 行）。"""
    result: dict = {}
    if not ENV_FILE.exists():
        return result
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            result[key.strip()] = value.strip()
    except OSError:
        pass
    return result


def update_env_file(updates: dict) -> None:
    """更新 .env 文件中对应键的值（保留注释与顺序，缺失的键追加到末尾）。

    供「页面内配置 API 模型」使用：写入后无需重启，配合 os.environ 同步即可热生效。
    """
    if not updates:
        return
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True) if ENV_FILE.exists() else []
    keys = set(updates.keys())
    written: set = set()
    out: list = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in keys:
                out.append(f"{key}={updates[key]}\n")
                written.add(key)
                continue
        out.append(line)
    # 追加缺失的键
    for k, v in updates.items():
        if k not in written:
            out.extend(["\n", f"{k}={v}\n"])
    ENV_FILE.write_text("".join(out), encoding="utf-8")


class BaseConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class LLMConfig(BaseConfig):
    base_url: str = Field(default="https://ark.cn-beijing.volces.com/api/v3", alias="LLM_BASE_URL")
    api_key: str = Field(default="", alias="LLM_API_KEY")
    model: str = Field(default="doubao-seed-1-6-flash-250828", alias="LLM_MODEL")
    temperature: float = Field(default=0.7, alias="LLM_TEMPERATURE")
    max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")


class CodeLLMConfig(BaseConfig):
    base_url: Optional[str] = Field(default=None, alias="CODE_LLM_BASE_URL")
    api_key: Optional[str] = Field(default=None, alias="CODE_LLM_API_KEY")
    model: Optional[str] = Field(default=None, alias="CODE_LLM_MODEL")
    temperature: float = Field(default=0.2, alias="CODE_LLM_TEMPERATURE")
    max_tokens: int = Field(default=8192, alias="CODE_LLM_MAX_TOKENS")

    def fallback_to(self, main: LLMConfig) -> "CodeLLMConfig":
        self.base_url = self.base_url or main.base_url
        self.api_key = self.api_key or main.api_key
        self.model = self.model or main.model
        return self


class AppConfig(BaseConfig):
    name: str = Field(default="Multi-Agent Code System", alias="APP_NAME")
    version: str = Field(default="3.0.0", alias="APP_VERSION")
    env: str = Field(default="dev", alias="APP_ENV")
    debug: bool = Field(default=True, alias="APP_DEBUG")
    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")


class ServerConfig(BaseConfig):
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")


class DatabaseConfig(BaseConfig):
    path: str = Field(default="data/app.db", alias="DB_PATH")
    echo: bool = Field(default=False, alias="DB_ECHO")

    @property
    def sqlalchemy_url(self) -> str:
        return f"sqlite:///{self.path}"


class ChromaConfig(BaseConfig):
    persist_dir: str = Field(default="data/chroma", alias="CHROMA_PERSIST_DIR")
    collection_name: str = Field(default="code_knowledge", alias="CHROMA_COLLECTION_NAME")
    embedding_model: str = Field(default="bge-m3", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1024, alias="EMBEDDING_DIM")


class SecurityConfig(BaseConfig):
    # 白名单：允许基础语言模块 + 常用工具库 + 项目内部模块
    # 禁止：subprocess/shutil/pickle/tempfile/multiprocessing/threading/shelve/tarfile/gzip/zipfile/
    # fileinput/stat/glob/fnmatch/inspect/dis/types/weakref/configparser 等高危模块
    # os/sys 保留但执行层限制危险API调用
    allowed_modules: Union[str, List[str]] = Field(
        default=(
            "math,re,json,typing,"
            "collections,itertools,functools,"
            "datetime,hashlib,base64,uuid,string,"
            "decimal,fractions,statistics,operator,"
            "heapq,bisect,array,enum,dataclasses,abc,"
            "io,os,sys,"  # os/sys 基础模块，执行层限制危险API
            "pathlib,pytest,unittest,time,logging,argparse,"
            "pprint,copy,random,"
            "numpy,pandas,requests,"
            "app.*,tools.*,agents.*,graph.*,schemas.*,core.*,services.*,"
            "src.*"  # 项目源代码模块（测试代码导入被测模块）
        ),
        alias="AST_ALLOWED_MODULES",
    )
    forbidden_nodes: Union[str, List[str]] = Field(
        default="Eval,Exec,Compile,Globals,Breakpoint",
        alias="AST_FORBIDDEN_NODES",
    )
    project_root: Union[str, Path] = Field(default=PROJECT_ROOT_DEFAULT, alias="PROJECT_ROOT")
    cors_origins: Union[str, List[str]] = Field(default="*", alias="CORS_ORIGINS")
    api_key: str = Field(default="", alias="API_KEY")  # 预留：简单 API key 鉴权开关
    # 运行时治理（kill switch）管理接口专用密钥；留空则管理接口直接 503（fail-closed）
    admin_key: str = Field(default="", alias="ADMIN_API_KEY")

    @field_validator("allowed_modules", "forbidden_nodes", "cors_origins", mode="after")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            if v == "*":
                return ["*"]
            return [s.strip() for s in v.split(",") if s.strip()]
        return list(v)

    @field_validator("project_root", mode="after")
    @classmethod
    def _ensure_path(cls, v):
        if not v:
            return PROJECT_ROOT_DEFAULT
        return Path(v).resolve()


class AgentConfig(BaseConfig):
    max_review_retry: int = Field(default=3, alias="MAX_REVIEW_RETRY")
    max_total_tokens: int = Field(default=32768, alias="MAX_TOTAL_TOKENS")
    enable_test_gen: bool = Field(default=True, alias="ENABLE_TEST_GEN")
    behavioral_test_enabled: bool = Field(default=True, alias="BEHAVIORAL_TEST_ENABLED")
    """是否启用行为测试生成：用 LLM 代码模型为每个顶层函数生成带真实断言的测试；
    关闭或 LLM 不可用时自动降级到纯本地 AST 模板（0 Token）。"""
    react_enabled: bool = Field(default=True, alias="REACT_ENABLED")
    """CodeGen Agent 是否启用 ReAct（推理-行动）模式"""
    verify_enabled: bool = Field(default=True, alias="VERIFY_ENABLED")
    """代码生成后是否执行验证 + 自愈闭环（execute_code 验证失败时回喂 LLM 修复）"""
    verify_max_retry: int = Field(default=2, alias="VERIFY_MAX_RETRY")
    """执行验证自愈的最大修复轮次"""
    plugin_dirs: str = Field(default="", alias="PLUGIN_DIRS")
    """插件目录列表，逗号分隔；默认扫描内置插件包"""


class MCPConfig(BaseConfig):
    """MCP 协议配置"""
    server_enabled: bool = Field(default=False, alias="MCP_SERVER_ENABLED")
    """是否启动 MCP Server（stdio 传输），供 Claude Desktop / Cursor 等外部客户端连接"""

    client_servers: str = Field(default="", alias="MCP_CLIENT_SERVERS")
    """外部 MCP Server 配置，JSON 格式字符串。
    示例：[{"name":"filesystem","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}]"
    """


class TracingConfig(BaseConfig):
    """OpenTelemetry 追踪配置"""
    enabled: bool = Field(default=False, alias="TRACING_ENABLED")
    """是否启用 tracing（OTel 优先，未安装则降级自研 trace_id）"""

    service_name: str = Field(default="multi-agent-code-system", alias="TRACING_SERVICE_NAME")
    """服务名称，用于标识 trace 来源"""


class ObservabilityConfig(BaseConfig):
    """可观测性配置"""
    enabled: bool = Field(default=True, alias="OBSERVABILITY_ENABLED")
    """是否启用指标收集"""
    retention_days: int = Field(default=30, alias="METRICS_RETENTION_DAYS")
    """指标保留天数"""


class RateLimitConfig(BaseConfig):
    """速率限制配置"""
    api_rate: float = Field(default=10.0, alias="RATE_LIMIT_API_RATE")
    """API 请求速率（每秒请求数）"""
    api_burst: int = Field(default=20, alias="RATE_LIMIT_API_BURST")
    """API 突发大小"""
    llm_rate: float = Field(default=5.0, alias="RATE_LIMIT_LLM_RATE")
    """LLM 调用速率（每秒请求数）"""
    llm_burst: int = Field(default=10, alias="RATE_LIMIT_LLM_BURST")
    """LLM 突发大小"""


class GuardrailConfig(BaseConfig):
    """注入防护配置"""
    input_enabled: bool = Field(default=True, alias="GUARDRAIL_INPUT_ENABLED")
    """是否启用输入注入防护"""
    output_enabled: bool = Field(default=True, alias="GUARDRAIL_OUTPUT_ENABLED")
    """是否启用输出安全检测"""
    auto_sanitize: bool = Field(default=False, alias="GUARDRAIL_AUTO_SANITIZE")
    """是否自动脱敏输出中的敏感内容"""
    whitelist: str = Field(default="", alias="GUARDRAIL_WHITELIST")
    """输入白名单关键词（逗号分隔）"""
    blacklist: str = Field(default="", alias="GUARDRAIL_BLACKLIST")
    """输入黑名单关键词（逗号分隔）"""


class MemoryConfig(BaseConfig):
    """长期记忆配置"""
    enabled: bool = Field(default=True, alias="MEMORY_ENABLED")
    """是否启用跨任务长期记忆"""

    recall_on_gen: bool = Field(default=True, alias="MEMORY_RECALL_ON_GEN")
    """代码生成时是否检索历史经验"""

    memory_dedup_enabled: bool = Field(default=True, alias="MEMORY_DEDUP_ENABLED")
    """是否启用经验去重：重复需求更新旧记录而非新增，避免记录膨胀"""

    memory_dedup_similarity_threshold: float = Field(
        default=0.92, alias="MEMORY_DEDUP_SIMILARITY_THRESHOLD"
    )
    """RAG 语义相似度去重阈值（0-1），高于该值视为重复需求"""

    memory_quality_threshold: int = Field(default=80, alias="MEMORY_QUALITY_THRESHOLD")
    """技能沉淀的评审分阈值：review_score >= 该值才归纳为可复用技能"""

    memory_min_quality_score: int = Field(
        default=40, alias="MEMORY_MIN_QUALITY_SCORE"
    )
    """低质量经验的质量分下限，低于该值在检索时被标注为低质量（可降权/过滤）"""

    memory_filter_low_quality: bool = Field(
        default=False, alias="MEMORY_FILTER_LOW_QUALITY"
    )
    """检索时是否过滤低质量经验；False 时仅降权/标注而不删除"""

    memory_auto_sediment: bool = Field(default=False, alias="MEMORY_AUTO_SEDIMENT")
    """任务完成后是否自动从高评分经验沉淀技能（写内存注册表，不落 YAML）"""


class SemanticCacheConfig(BaseConfig):
    """语义缓存配置"""
    enabled: bool = Field(default=True, alias="SEMANTIC_CACHE_ENABLED")
    """是否启用语义缓存"""
    similarity_threshold: float = Field(default=0.95, alias="SEMANTIC_CACHE_THRESHOLD")
    """向量相似度匹配阈值（0-1），高于该值命中缓存"""
    collection_name: str = Field(default="semantic_cache", alias="SEMANTIC_CACHE_COLLECTION")
    """ChromaDB 集合名称"""
    max_entries: int = Field(default=10000, alias="SEMANTIC_CACHE_MAX_ENTRIES")
    """最大缓存条目数"""


class ModelRoutingConfig(BaseConfig):
    """多模型路由配置"""
    rules: str = Field(
        default="code_gen:code,review:main,test_gen:code,chat:main",
        alias="MODEL_ROUTING_RULES",
    )
    """路由规则：task_type:model_role 逗号分隔，如 code_gen:code 表示代码生成用 code_model"""
    fallback_enabled: bool = Field(default=True, alias="MODEL_ROUTING_FALLBACK")
    """主模型失败时是否自动降级到备用模型"""


class SandboxConfig(BaseConfig):
    """Docker / Local sandbox 安全隔离执行配置（借鉴 OpenHands V1 workspace 设计）"""
    mode: str = Field(default="docker", alias="SANDBOX_MODE")
    """执行模式: local（子进程）| docker（容器隔离）"""
    image: str = Field(default="python:3.11-slim", alias="SANDBOX_IMAGE")
    """Docker 镜像"""
    timeout: int = Field(default=30, alias="SANDBOX_TIMEOUT")
    """执行超时（秒）"""
    max_memory_mb: int = Field(default=512, alias="SANDBOX_MAX_MEMORY_MB")
    """内存上限（MB）"""
    max_processes: int = Field(default=64, alias="SANDBOX_MAX_PROCESSES")
    """进程数上限"""
    network_enabled: bool = Field(default=False, alias="SANDBOX_NETWORK")
    """是否允许网络访问"""


class Settings:
    """
    全局配置聚合入口，全项目统一通过此对象读取配置。
    使用方式：
        from app.core.config import settings
        print(settings.llm.api_key)
    """

    def __init__(self) -> None:
        self.llm = LLMConfig()
        self.code_llm = CodeLLMConfig().fallback_to(self.llm)
        self.app = AppConfig()
        self.server = ServerConfig()
        self.db = DatabaseConfig()
        self.chroma = ChromaConfig()
        self.security = SecurityConfig()
        self.agent = AgentConfig()
        self.mcp = MCPConfig()
        self.tracing = TracingConfig()
        self.memory = MemoryConfig()
        self.semantic_cache = SemanticCacheConfig()
        self.model_routing = ModelRoutingConfig()
        self.observability = ObservabilityConfig()
        self.rate_limit = RateLimitConfig()
        self.guardrail = GuardrailConfig()
        self.sandbox = SandboxConfig()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        db_dir = Path(self.db.path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        Path(self.chroma.persist_dir).mkdir(parents=True, exist_ok=True)


settings = Settings()

__all__ = [
    "settings",
    "BaseConfig",
    "LLMConfig",
    "CodeLLMConfig",
    "AppConfig",
    "ServerConfig",
    "DatabaseConfig",
    "ChromaConfig",
    "SecurityConfig",
    "AgentConfig",
    "MCPConfig",
    "TracingConfig",
    "MemoryConfig",
    "SemanticCacheConfig",
    "ModelRoutingConfig",
    "ObservabilityConfig",
    "RateLimitConfig",
    "GuardrailConfig",
    "SandboxConfig",
]
