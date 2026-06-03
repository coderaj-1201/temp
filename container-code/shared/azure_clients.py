"""
Azure client factories — production grade.

Key differences from local version:
  - ManagedIdentityCredential (no az login, no API keys)
  - No lru_cache — safe under multiple uvicorn workers (each process gets its own)
  - Single AI Search index with domain metadata filter
  - Service Bus via Managed Identity
"""
from __future__ import annotations

import os

from azure.ai.projects import AIProjectClient
from azure.identity import ManagedIdentityCredential, AzureCliCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.servicebus.aio import ServiceBusClient
from openai import AzureOpenAI

from shared.config import settings


def _credential():
    """Managed Identity in ACA; CLI credential for local container testing."""
    if os.getenv("RUNNING_IN_AZURE"):
        return ManagedIdentityCredential()
    return AzureCliCredential()


# ── Per-process singletons (module-level, worker-safe) ───────────────────────
# Initialised once per worker process on first import.

_foundry_client: AIProjectClient | None = None
_openai_client: AzureOpenAI | None = None
_search_client: SearchClient | None = None
_search_index_client: SearchIndexClient | None = None


def get_foundry_client() -> AIProjectClient:
    global _foundry_client
    if _foundry_client is None:
        _foundry_client = AIProjectClient(
            endpoint=str(settings.AZURE_FOUNDRY_PROJECT_ENDPOINT),
            credential=_credential(),
        )
    return _foundry_client


def get_openai_client() -> AzureOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = get_foundry_client().inference.get_azure_openai_client(
            api_version=settings.AZURE_OPENAI_API_VERSION
        )
    return _openai_client


def get_search_client() -> SearchClient:
    """Single index — domain filtering applied at query time via $filter."""
    global _search_client
    if _search_client is None:
        _search_client = SearchClient(
            endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
            index_name=settings.AZURE_SEARCH_INDEX,
            credential=_credential(),
        )
    return _search_client


def get_search_index_client() -> SearchIndexClient:
    global _search_index_client
    if _search_index_client is None:
        _search_index_client = SearchIndexClient(
            endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
            credential=_credential(),
        )
    return _search_index_client


def get_service_bus_client() -> ServiceBusClient:
    """New instance per use — ServiceBusClient is an async context manager."""
    return ServiceBusClient(
        fully_qualified_namespace=settings.AZURE_SERVICE_BUS_NAMESPACE,
        credential=_credential(),
    )
