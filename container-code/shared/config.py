"""
Local dev settings.
All Azure resources are real (Service Bus, CosmosDB, AI Search, Foundry).
Auth is AzureCliCredential — run `az login` before starting.
No Managed Identity, no Key Vault, no App Insights required.
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
    AZURE_OPENAI_CHAT_DEPLOYMENT: str       = "gpt-4o"
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str  = "text-embedding-ada-002"
    AZURE_OPENAI_API_VERSION: str           = "2024-08-01-preview"
    AZURE_OPENAI_EVAL_DEPLOYMENT: str       = "gpt-4o-mini"

    # ── Azure AI Search ───────────────────────────────────────────────────────
    AZURE_SEARCH_ENDPOINT: AnyHttpUrl
    AZURE_SEARCH_INDEX: str                 = "idx-rag"
    AZURE_SEARCH_SEMANTIC_CONFIG: str       = "rag-semantic-config"

    # ── Azure Service Bus ─────────────────────────────────────────────────────
    AZURE_SERVICE_BUS_NAMESPACE: str
    AZURE_SERVICE_BUS_QUEUE_INBOUND: str    = "rag-inbound"
    AZURE_SERVICE_BUS_QUEUE_OUTBOUND: str   = "rag-outbound"
    AZURE_SERVICE_BUS_QUEUE_EVALUATION: str = "rag-evaluation"

    # ── Azure Cosmos DB ───────────────────────────────────────────────────────
    AZURE_COSMOS_ENDPOINT: AnyHttpUrl
    AZURE_COSMOS_DATABASE: str              = "rag-analytics"
    AZURE_COSMOS_CONTAINER_EVALUATIONS: str = "evaluations"
    AZURE_COSMOS_CONTAINER_FEEDBACK: str    = "feedback"

    # ── Entra ID — NOT used for local auth (bypassed) but kept so ACA config
    # can share the same .env shape. Leave blank for local dev.
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""

    # ── RAG tuning ────────────────────────────────────────────────────────────
    CONFIDENCE_THRESHOLD: float  = Field(default=0.75, ge=0.0, le=1.0)
    MAX_RETRIEVAL_ATTEMPTS: int  = Field(default=3,    ge=1,   le=5)
    RETRIEVAL_TOP_K: int         = Field(default=5,    ge=1,   le=20)
    SYNTHESIS_TEMPERATURE: float = Field(default=0.0,  ge=0.0, le=1.0)

    # ── Observability — App Insights optional locally ─────────────────────────
    APPLICATIONINSIGHTS_CONNECTION_STRING: str | None = None
    LOG_LEVEL: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
