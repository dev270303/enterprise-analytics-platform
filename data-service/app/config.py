"""Settings read from environment variables (12-factor). Nothing secret has a default."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # libpq URL, e.g. postgresql://user:pass@host/db?sslmode=require (Neon's string works as-is)
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/eap"))
    # Shared secret the Spring Boot backend sends in X-Internal-Token
    internal_token: str | None = field(default_factory=lambda: _env("INTERNAL_API_TOKEN"))

    # Any OpenAI-compatible chat/embeddings API (Gemini, Groq, OpenRouter, OpenAI, local Ollama ...)
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"))
    llm_api_key: str | None = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gemini-2.5-flash"))
    embedding_base_url: str | None = field(default_factory=lambda: _env("EMBEDDING_BASE_URL"))  # defaults to llm_base_url
    embedding_api_key: str | None = field(default_factory=lambda: _env("EMBEDDING_API_KEY"))    # defaults to llm_api_key
    embedding_model: str | None = field(default_factory=lambda: _env("EMBEDDING_MODEL", "gemini-embedding-001"))
    embedding_dim: int = 768  # fixed by the rag_chunks.embedding column

    ai_daily_limit: int = field(default_factory=lambda: _int("AI_DAILY_LIMIT", 300))
    ai_row_limit: int = field(default_factory=lambda: _int("AI_ROW_LIMIT", 200))
    ai_statement_timeout_ms: int = field(default_factory=lambda: _int("AI_STATEMENT_TIMEOUT_MS", 8000))

    smtp_host: str | None = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str | None = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    report_from: str | None = field(default_factory=lambda: _env("REPORT_FROM"))
    report_recipients: str | None = field(default_factory=lambda: _env("REPORT_RECIPIENTS"))  # comma separated
    public_app_url: str = field(default_factory=lambda: _env("PUBLIC_APP_URL", "https://enterprise-analytics.onrender.com"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def embeddings_enabled(self) -> bool:
        return bool((self.embedding_api_key or self.llm_api_key) and self.embedding_model)

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.report_recipients)


def get_settings() -> Settings:
    return Settings()
