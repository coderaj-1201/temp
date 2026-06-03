"""
Azure client factories — local dev.

Auth:
  - Foundry / OpenAI : AzureCliCredential (az login)
  - AI Search        : API key (no role assignment needed with Contributor access)

lru_cache is fine here — single uvicorn worker in local dev.
"""
from __future__ import annotations

from functools import lru_cache

from azure.ai.projects import AIProjectClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureCliCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import AzureOpenAI

from shared.config import settings


def _credential() -> AzureCliCredential:
    return AzureCliCredential()


@lru_cache(maxsize=1)
def get_foundry_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=str(settings.AZURE_FOUNDRY_PROJECT_ENDPOINT),
        credential=_credential(),
    )


@lru_cache(maxsize=1)
def get_openai_client() -> AzureOpenAI:
    return get_foundry_client().inference.get_azure_openai_client(
        api_version=settings.AZURE_OPENAI_API_VERSION
    )


@lru_cache(maxsize=1)
def get_search_client() -> SearchClient:
    """Single index — domain filtered at query time via $filter."""
    return SearchClient(
        endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
        index_name=settings.AZURE_SEARCH_INDEX,
        credential=AzureKeyCredential(settings.AZURE_SEARCH_API_KEY.get_secret_value()),
    )


@lru_cache(maxsize=1)
def get_search_index_client() -> SearchIndexClient:
    return SearchIndexClient(
        endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
        credential=AzureKeyCredential(settings.AZURE_SEARCH_API_KEY.get_secret_value()),
    )
