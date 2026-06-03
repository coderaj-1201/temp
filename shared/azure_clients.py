"""
Lazy, cached Azure client factories.

Uses Azure AI Foundry (AIProjectClient) as the single entry point for:
  - Chat completions  → client.inference.get_chat_completions_client()
  - Embeddings        → client.inference.get_embeddings_client()

Auth:
  - Local dev  : AzureCliCredential (az login)
  - Azure (ACA): ManagedIdentityCredential
"""
from __future__ import annotations

import os
from functools import lru_cache

from azure.ai.projects import AIProjectClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import AzureOpenAI

from shared.config import settings


def _credential():
    """AzureCliCredential locally (az login); ManagedIdentity inside ACA."""
    if os.getenv("RUNNING_IN_AZURE"):
        return ManagedIdentityCredential()
    return AzureCliCredential()


@lru_cache(maxsize=1)
def get_foundry_client() -> AIProjectClient:
    """
    Single AIProjectClient for the AI Foundry project.
    Endpoint format: https://<hub>.services.ai.azure.com/api/projects/<project>
    """
    return AIProjectClient(
        endpoint=str(settings.AZURE_FOUNDRY_PROJECT_ENDPOINT),
        credential=_credential(),
    )


@lru_cache(maxsize=1)
def get_openai_client() -> AzureOpenAI:
    """
    Returns an openai.AzureOpenAI client pre-wired to your Foundry project.
    All tools (HyDE, decomposition, synthesis, embeddings) use this.
    """
    foundry = get_foundry_client()
    # get_azure_openai_client() returns an openai.AzureOpenAI instance
    # scoped to the project's Azure OpenAI connection — no separate endpoint needed.
    return foundry.inference.get_azure_openai_client(api_version=settings.AZURE_OPENAI_API_VERSION)


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
