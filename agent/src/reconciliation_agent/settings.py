"""Application settings."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    model_base_url: str
    model_api_key: SecretStr = Field(min_length=1)
    model_name: str
    model_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    model_max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    confirmation_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    max_tool_calls: int = Field(default=8, ge=1, le=12)


@lru_cache
def get_settings() -> Settings:
    """Return cached runtime settings."""
    return Settings()  # type: ignore[call-arg]
