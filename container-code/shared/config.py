"""
Production settings — fully keyless, Managed Identity everywhere.
No API keys. No static secrets in environment variables.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Azure AI Foundry ──────────────────────────────────────────────────────
    AZURE_FOUNDRY_PROJECT_ENDPOINT: AnyHttpUrl
    AZURE_OPENAI_CHAT_DEPLOYMENT: str = "gpt-4o"
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str = "text-embedding-ada-002"
    AZURE_OPENAI_API_VERSION: str = "2024-08-01-preview"

    # ── Azure AI Search — single index, keyless ───────────────────────────────
    AZURE_SEARCH_ENDPOINT: AnyHttpUrl
    AZURE_SEARCH_INDEX: str = "idx-rag"          # single index, domain as metadata field
    AZURE_SEARCH_SEMANTIC_CONFIG: str = "rag-semantic-config"

    # ── Azure Service Bus ─────────────────────────────────────────────────────
    AZURE_SERVICE_BUS_NAMESPACE: str             # e.g. mybus.servicebus.windows.net
    AZURE_SERVICE_BUS_QUEUE_INBOUND: str  = "rag-inbound"
    AZURE_SERVICE_BUS_QUEUE_OUTBOUND: str = "rag-outbound"

    # ── Entra ID token validation ─────────────────────────────────────────────
    AZURE_TENANT_ID: str
    AZURE_CLIENT_ID: str                         # App registration client ID (Teams bot)

    # ── RAG tuning ────────────────────────────────────────────────────────────
    CONFIDENCE_THRESHOLD: float    = Field(default=0.75, ge=0.0, le=1.0)
    MAX_RETRIEVAL_ATTEMPTS: int    = Field(default=3,    ge=1,   le=5)
    RETRIEVAL_TOP_K: int           = Field(default=5,    ge=1,   le=20)
    SYNTHESIS_TEMPERATURE: float   = Field(default=0.0,  ge=0.0, le=1.0)

    # ── Observability ─────────────────────────────────────────────────────────
    APPLICATIONINSIGHTS_CONNECTION_STRING: str | None = None
    LOG_LEVEL: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
