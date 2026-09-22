"""集中配置：所有可变项都从环境变量 / .env 读取，代码中不出现任何凭据。

对应改造方案第三节决策 6：数据库凭据、API Key 一律外置。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SUPPORTED_DB_MODES = ("sqlite", "postgres")
SUPPORTED_EMBEDDING_PROVIDERS = ("local", "dashscope", "bm25")


class Settings(BaseSettings):
    """应用配置。优先级：环境变量 > .env > 默认值。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "个人理财助手 API"
    version: str = "2.0.0"

    # ---------------- 数据层 ----------------
    # sqlite：离线演示路径（零依赖，默认）
    # postgres：正式路径，走 GaussDB/PostgreSQL 的视图、触发器与存储过程
    db_mode: str = "sqlite"
    sqlite_path: str = "data/finance.db"
    pg_host: str = ""
    pg_port: int = 5432
    pg_database: str = "finance_db"
    pg_user: str = ""
    pg_password: str = ""
    pg_schema: str = "public"
    pg_connect_timeout: int = 10

    # 单用户课设场景默认用户；多用户时由登录态提供
    default_user_id: int = 1
    enable_dev_login: bool = True

    # ---------------- AI ----------------
    ai_enabled: bool = True
    deepseek_api_key: str = ""
    chat_api_base: str = "https://api.deepseek.com/v1"
    chat_model: str = "deepseek-chat"
    chat_timeout: int = 60
    chat_max_retries: int = 2

    embedding_provider: str = "local"
    dashscope_api_key: str = ""
    local_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_cache_dir: str = "models/embeddings"
    hf_endpoint: str = "https://hf-mirror.com"

    knowledge_dir: str = "knowledge/chroma"
    retrieval_limit: int = 5
    # AI 写操作必须人工确认后才落库
    require_write_confirmation: bool = True

    # ---------------- 校验 ----------------
    @field_validator("db_mode")
    @classmethod
    def _check_db_mode(cls, value: str) -> str:
        resolved = (value or "sqlite").strip().lower()
        aliases = {"pg": "postgres", "postgresql": "postgres", "gaussdb": "postgres", "opengauss": "postgres"}
        resolved = aliases.get(resolved, resolved)
        if resolved not in SUPPORTED_DB_MODES:
            raise ValueError(f"DB_MODE 只能是 {SUPPORTED_DB_MODES}，当前为 {value!r}")
        return resolved

    @field_validator("embedding_provider")
    @classmethod
    def _check_embedding_provider(cls, value: str) -> str:
        resolved = (value or "local").strip().lower()
        aliases = {"aliyun": "dashscope", "cloud": "dashscope", "keyword": "bm25", "offline": "bm25"}
        resolved = aliases.get(resolved, resolved)
        if resolved not in SUPPORTED_EMBEDDING_PROVIDERS:
            raise ValueError(f"EMBEDDING_PROVIDER 只能是 {SUPPORTED_EMBEDDING_PROVIDERS}，当前为 {value!r}")
        return resolved

    # ---------------- 派生属性 ----------------
    @property
    def sqlite_file(self) -> Path:
        path = Path(self.sqlite_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def knowledge_path(self) -> Path:
        path = Path(self.knowledge_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def embedding_cache_path(self) -> Path:
        path = Path(self.embedding_cache_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def chat_ready(self) -> bool:
        """是否具备真实调用大模型的条件。缺失时全链路降级为规则模式。"""
        return bool(self.ai_enabled and self.deepseek_api_key.strip())

    def pg_dsn(self) -> dict:
        """psycopg2 连接参数。search_path 通过 options 传入，避免每次连接再 SET。"""
        return {
            "host": self.pg_host,
            "port": self.pg_port,
            "dbname": self.pg_database,
            "user": self.pg_user,
            "password": self.pg_password,
            "connect_timeout": self.pg_connect_timeout,
            "options": f"-c search_path={self.pg_schema},public",
        }

    def describe(self) -> dict:
        """给 /api/health 用的自检信息，绝不包含凭据。"""
        return {
            "db_mode": self.db_mode,
            "sqlite_path": str(self.sqlite_file) if self.db_mode == "sqlite" else None,
            "pg_host": self.pg_host if self.db_mode == "postgres" else None,
            "pg_database": self.pg_database if self.db_mode == "postgres" else None,
            "pg_schema": self.pg_schema if self.db_mode == "postgres" else None,
            "ai_enabled": self.ai_enabled,
            "chat_ready": self.chat_ready,
            "chat_model": self.chat_model if self.chat_ready else None,
            "embedding_provider": self.embedding_provider,
            "require_write_confirmation": self.require_write_confirmation,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例，避免每个请求重复解析 .env。"""
    return Settings()


settings: Settings = get_settings()

__all__ = [
    "PROJECT_ROOT",
    "SUPPORTED_DB_MODES",
    "SUPPORTED_EMBEDDING_PROVIDERS",
    "Settings",
    "get_settings",
    "settings",
]
