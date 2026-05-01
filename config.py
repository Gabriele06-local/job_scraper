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

    # Connector auth
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    jooble_api_key: str = ""
    linkedin_max_results: int = 25

    # Search config — override via comma-separated env vars
    scrape_languages: list[str] = ["it", "en", "es", "fr", "de"]
    scrape_keywords: list[str] = [
        "software engineer",
        "software developer",
        "web developer",
        "frontend",
        "backend",
        "fullstack",
        "devops",
        "mobile developer",
        "data scientist",
        "data engineer",
        "cloud engineer",
        "python",
        "javascript",
        "java",
        "programmatore",
        "sviluppatore",
    ]

    # Connectors disabled at runtime (comma-separated connector names)
    disabled_connectors: list[str] = []

    # Expiration checker
    expiration_concurrency: int = 10
    expiration_limit: int = 500
    expiration_max_age_days: int = 60
    expiration_user_agent: str = "DevBoardsLinkProbe/1.0 (+https://devboards.io/probe)"

    # Health check
    health_check_file: str = "/tmp/health.json"


settings = Settings()
