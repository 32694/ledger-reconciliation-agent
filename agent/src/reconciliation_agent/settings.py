"""Application settings."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Database configuration for internal persistence callers."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SecretStr 防止数据库口令在日志和异常对象中被直接打印。
    database_url: SecretStr

    @property
    def database_url_for_sqlalchemy(self) -> str:
        """Return the raw database URL for internal SQLAlchemy callers."""
        return self.database_url.get_secret_value()


class Settings(DatabaseSettings):
    """Runtime configuration loaded from environment variables."""

    # 模型地址、密钥和名称全部由 .env/环境变量提供，迁移电脑时无需修改代码。
    model_base_url: str
    model_api_key: SecretStr = Field(min_length=1)
    model_name: str
    model_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    model_max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    model_max_retries: int = Field(default=2, ge=0, le=10)
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    confirmation_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    max_tool_calls: int = Field(default=8, ge=1, le=12)


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return cached database-only settings."""
    return DatabaseSettings()  # type: ignore[call-arg]


@lru_cache
def get_settings() -> Settings:
    """Return cached runtime settings."""
    # 缓存可避免每次工具或节点调用都重新读取并校验环境变量。
    return Settings()  # type: ignore[call-arg]
