"""
Azure client factories — production grade.
Fully keyless. Worker-safe per-process singletons.
"""
from __future__ import annotations

import os

from azure.ai.projects import AIProjectClient
from azure.cosmos.aio import CosmosClient
from azure.identity import ManagedIdentityCredential, AzureCliCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.servicebus.aio import ServiceBusClient
from openai import AzureOpenAI

from shared.config import settings


def _credential():
    if os.getenv("RUNNING_IN_AZURE"):
        return ManagedIdentityCredential()
    return AzureCliCredential()


_foundry: AIProjectClient | None = None
_openai: AzureOpenAI | None = None
_search: SearchClient | None = None
_search_index: SearchIndexClient | None = None
_cosmos: CosmosClient | None = None


def get_foundry_client() -> AIProjectClient:
    global _foundry
    if _foundry is None:
        _foundry = AIProjectClient(
            endpoint=str(settings.AZURE_FOUNDRY_PROJECT_ENDPOINT),
            credential=_credential(),
        )
    return _foundry


def get_openai_client() -> AzureOpenAI:
    global _openai
    if _openai is None:
        _openai = get_foundry_client().inference.get_azure_openai_client(
            api_version=settings.AZURE_OPENAI_API_VERSION
        )
    return _openai


def get_search_client() -> SearchClient:
    global _search
    if _search is None:
        _search = SearchClient(
            endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
            index_name=settings.AZURE_SEARCH_INDEX,
            credential=_credential(),
        )
    return _search


def get_search_index_client() -> SearchIndexClient:
    global _search_index
    if _search_index is None:
        _search_index = SearchIndexClient(
            endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
            credential=_credential(),
        )
    return _search_index


def get_cosmos_client() -> CosmosClient:
    """Async CosmosDB client — use as async context manager or call aclose() on shutdown."""
    global _cosmos
    if _cosmos is None:
        _cosmos = CosmosClient(
            url=str(settings.AZURE_COSMOS_ENDPOINT),
            credential=_credential(),
        )
    return _cosmos


def get_service_bus_client() -> ServiceBusClient:
    """New instance per use — always use as async context manager."""
    return ServiceBusClient(
        fully_qualified_namespace=settings.AZURE_SERVICE_BUS_NAMESPACE,
        credential=_credential(),
    )
