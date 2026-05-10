"""Settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    moodle_base_url: str = Field("http://localhost:8080", validation_alias="MOODLE_BASE_URL")
    moodle_token: str = Field("", validation_alias="MOODLE_TOKEN")
    # Optional override for the HTTP `Host` header sent to Moodle. Useful when the API
    # reaches Moodle on a different hostname than its installed `wwwroot` (e.g. via a
    # Docker service alias) — Moodle compares Host against wwwroot and 303-redirects on
    # mismatch. Set to "localhost:8080" when wwwroot is http://localhost:8080.
    moodle_host_header: str = Field("", validation_alias="MOODLE_HOST_HEADER")

    qdrant_url: str = Field("http://localhost:6333", validation_alias="QDRANT_URL")
    qdrant_collection: str = Field("course_content", validation_alias="QDRANT_COLLECTION")

    ollama_host: str = Field("http://localhost:11434", validation_alias="OLLAMA_HOST")
    ollama_chat_model: str = Field("llama3.2", validation_alias="OLLAMA_CHAT_MODEL")
    ollama_embed_model: str = Field("nomic-embed-text", validation_alias="OLLAMA_EMBED_MODEL")
    # httpx read/connect/write cap for every Ollama HTTP call (synthetic course can exceed 120s on cold load).
    ollama_http_timeout_s: float = Field(3600.0, validation_alias="OLLAMA_HTTP_TIMEOUT_S")

    # Comma-separated browser origins allowed to call the API (Moodle site URL(s)).
    neuro_api_cors_origins: str = Field("", validation_alias="NEURO_API_CORS_ORIGINS")

    # Phase 2: shared secret for the Moodle event webhook.
    # If empty, the webhook accepts any caller (fine on a private network only).
    neuro_event_secret: str = Field("", validation_alias="NEURO_EVENT_SECRET")

    # Phase 3: enable per-learner memory boost in `rag.ask` when learner_id is sent.
    neuro_enable_learner_memory: bool = Field(True, validation_alias="NEURO_ENABLE_LEARNER_MEMORY")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
