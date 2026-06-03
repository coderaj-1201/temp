"""
Centralised, validated settings via pydantic-settings.
All values come from environment variables or .env file.
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
    )

    # ── Azure AI Foundry ──────────────────────────────────────────────────────
    # Format: https://<hub>.services.ai.azure.com/api/projects/<project>
    AZURE_FOUNDRY_PROJECT_ENDPOINT: AnyHttpUrl

    # Deployment names inside your Foundry project
    AZURE_OPENAI_CHAT_DEPLOYMENT: str = "gpt-4o"
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str = "text-embedding-ada-002"
    AZURE_OPENAI_API_VERSION: str = "2024-08-01-preview"

    # ── Azure AI Search ───────────────────────────────────────────────────────
    AZURE_SEARCH_ENDPOINT: AnyHttpUrl
    AZURE_SEARCH_API_KEY: SecretStr
    AZURE_SEARCH_INDEX_HR: str = "idx-hr"
    AZURE_SEARCH_INDEX_LEGAL: str = "idx-legal"
    AZURE_SEARCH_INDEX_IT: str = "idx-it"
    AZURE_SEARCH_SEMANTIC_CONFIG: str = "default"

    # ── Azure Service Bus (only needed in production servicebus mode) ─────────
    AZURE_SERVICE_BUS_CONNECTION_STR: SecretStr | None = None
    AZURE_SERVICE_BUS_QUEUE_INBOUND: str = "rag-inbound"
    AZURE_SERVICE_BUS_QUEUE_OUTBOUND: str = "rag-outbound"

    # ── RAG tuning ────────────────────────────────────────────────────────────
    CONFIDENCE_THRESHOLD: float = Field(default=0.75, ge=0.0, le=1.0)
    MAX_RETRIEVAL_ATTEMPTS: int = Field(default=3, ge=1, le=5)
    RETRIEVAL_TOP_K: int = Field(default=5, ge=1, le=20)
    SYNTHESIS_TEMPERATURE: float = Field(default=0.0, ge=0.0, le=1.0)

    # ── Observability ─────────────────────────────────────────────────────────
    APPLICATIONINSIGHTS_CONNECTION_STRING: str | None = None
    LOG_LEVEL: str = "INFO"

    # ── Teams Bot ─────────────────────────────────────────────────────────────
    TEAMS_APP_ID: str | None = None
    TEAMS_APP_PASSWORD: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level convenience alias
settings = get_settings()
