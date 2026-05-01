"""Pydantic Settings — single source of truth for all env-driven config."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # MongoDB
    database_url: str = "mongodb://localhost:27017"
    mongo_db: str = "itjobhub"

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    groq_max_tokens: int = 1024
    groq_temperature: float = 0.1
    groq_timeout: int = 30
    groq_rpm: int = 30  # client-side rate limit (requests per minute)

    # Logging
    log_level: str = "INFO"


settings = Settings()
