"""
Lazy, cached Azure client factories.
Using ManagedIdentityCredential in production per MAF recommendation.
Falls back to DefaultAzureCredential for local dev (az login).
"""
from __future__ import annotations

import os
from functools import lru_cache

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import AzureOpenAI

from shared.config import settings


def _credential():
    """Use Managed Identity in ACA; fall back to CLI/env for local dev."""
    if os.getenv("RUNNING_IN_AZURE"):
        return ManagedIdentityCredential()
    return DefaultAzureCredential()


@lru_cache(maxsize=1)
def get_openai_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=str(settings.AZURE_OPENAI_ENDPOINT),
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_ad_token_provider=lambda: _credential().get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token,
    )


@lru_cache(maxsize=8)
def get_search_client(index_name: str) -> SearchClient:
    return SearchClient(
        endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
        index_name=index_name,
        credential=AzureKeyCredential(
            settings.AZURE_SEARCH_API_KEY.get_secret_value()
        ),
    )


@lru_cache(maxsize=1)
def get_search_index_client() -> SearchIndexClient:
    return SearchIndexClient(
        endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
        credential=AzureKeyCredential(
            settings.AZURE_SEARCH_API_KEY.get_secret_value()
        ),
    )


INDEX_MAP: dict[str, str] = {
    "hr":    settings.AZURE_SEARCH_INDEX_HR,
    "legal": settings.AZURE_SEARCH_INDEX_LEGAL,
    "it":    settings.AZURE_SEARCH_INDEX_IT,
}
