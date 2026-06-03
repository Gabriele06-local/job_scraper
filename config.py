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
    groq_model: str = "llama-3.1-8b-instant"  # legacy alias for groq_model_fast
    groq_max_tokens: int = 1024
    groq_temperature: float = 0.1
    groq_timeout: int = 30
    groq_rpm: int = 50  # client-side rate limit (requests per minute)

    # Multi-model tiers (SPEC 05 §4.1). The router maps a task tier -> model;
    # nothing above ai/router.py references these literals.
    groq_model_fast: str = "llama-3.1-8b-instant"
    groq_model_struct: str = "qwen/qwen3-32b"
    groq_model_reason: str = "llama-3.3-70b-versatile"

    # Routing / escalation (SPEC 05 §4.2). EXTRACT enters at FAST and escalates
    # to STRUCT only on low confidence; REASON is reserved for spam/ambiguity
    # and is opt-in. Ceilings keep spend bounded.
    ai_confidence_threshold: float = 0.7  # escalate below this (mirrors gate)
    ai_max_escalation: int = 1  # max tier hops per task (FAST->STRUCT = 1)
    ai_enable_reason: bool = True  # allow the 70b REASON tier
    ai_enable_triage: bool = False  # cheap pre-screen call before EXTRACT

    # AI result cache (SPEC 05 §4.5) — skip re-classifying unchanged postings.
    ai_cache_enabled: bool = True
    ai_cache_ttl_days: int = 30

    # Logging
    log_level: str = "INFO"

    # Connector auth
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    careerjet_api_key: str = ""
    jooble_api_key: str = ""
    reed_api_key: str = ""
    themuse_api_key: str = ""
    rapidapi_key: str = ""

    # RapidAPI jobs-metered plans (Fantastic.Jobs: active_jobs_db, workday_jobs,
    # startup_jobs) bill per job returned. Cap jobs fetched per source per UTC
    # month so a run can't blow the free-tier quota. 0 disables the cap.
    rapidapi_monthly_job_budget: int = 250

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
