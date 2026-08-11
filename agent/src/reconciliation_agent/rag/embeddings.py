"""Local normalized text embeddings for reconciliation rules."""

from functools import lru_cache
from typing import Protocol, cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EncodedVectors(Protocol):
    """Array-like encoded vectors returned by sentence-transformers."""

    def tolist(self) -> list[list[float]]:
        """Convert vectors to plain Python lists."""


class SentenceEncoder(Protocol):
    """Subset of SentenceTransformer used by the application."""

    def encode(self, texts: list[str], *, normalize_embeddings: bool) -> EncodedVectors:
        """Encode and optionally normalize text vectors."""


class EmbeddingSettings(BaseSettings):
    """Embedding-only settings that do not require a chat model key."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    embedding_model: str = Field(default="BAAI/bge-small-zh-v1.5", min_length=1)


class EmbeddingUnavailableError(RuntimeError):
    """Raised when the local embedding model cannot be loaded."""


@lru_cache
def get_embedding_settings() -> EmbeddingSettings:
    """Return cached embedding-only settings."""
    return EmbeddingSettings()


@lru_cache
def _load_model(model_name: str) -> SentenceEncoder:
    from sentence_transformers import SentenceTransformer

    return cast(SentenceEncoder, SentenceTransformer(model_name))


class EmbeddingService:
    """Encode text with one cached, normalized local model."""

    def __init__(self, model_name: str) -> None:
        """Load the named model through the shared cache."""
        try:
            self._model = _load_model(model_name)
        except OSError as error:
            raise EmbeddingUnavailableError("embedding model unavailable") from error

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return normalized vectors for the supplied texts."""
        if not texts:
            return []
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()
