"""
Azure client factories — local dev.

Auth:
  - OpenAI   : AzureCliCredential via token provider (az login is enough)
  - AI Search: API key

get_openai_client() tries the Foundry inference API first.
If the installed azure-ai-projects version doesn't have .inference
(older than 1.0.0b3), it falls back to a direct AzureOpenAI connection
using the Foundry project endpoint root as the Azure OpenAI base URL.

This means you don't need to find a separate Azure OpenAI endpoint —
the Foundry project endpoint already embeds it.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureCliCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import AzureOpenAI

from shared.config import settings

logger = logging.getLogger(__name__)


def _credential() -> AzureCliCredential:
    return AzureCliCredential()


def _token_provider():
    """Returns a callable that provides a fresh Entra token for Azure OpenAI."""
    cred = _credential()
    def get_token():
        return cred.get_token("https://cognitiveservices.azure.com/.default").token
    return get_token


@lru_cache(maxsize=1)
def get_openai_client() -> AzureOpenAI:
    """
    Returns an AzureOpenAI client connected to your Foundry project.

    Strategy 1 (preferred): use AIProjectClient.inference.get_azure_openai_client()
      — requires azure-ai-projects >= 1.0.0b3

    Strategy 2 (fallback): construct AzureOpenAI directly using the Foundry
      project endpoint. The project endpoint IS an Azure OpenAI endpoint —
      requests to /openai/deployments/... work directly against it.
    """
    endpoint = str(settings.AZURE_FOUNDRY_PROJECT_ENDPOINT)

    # Strategy 1 — try Foundry inference API
    try:
        from azure.ai.projects import AIProjectClient
        client = AIProjectClient(endpoint=endpoint, credential=_credential())
        # Check if inference attribute exists (added in 1.0.0b3)
        if hasattr(client, "inference"):
            oai = client.inference.get_azure_openai_client(
                api_version=settings.AZURE_OPENAI_API_VERSION
            )
            logger.debug("OpenAI client via AIProjectClient.inference")
            return oai
        else:
            logger.warning(
                "azure-ai-projects installed but .inference not available. "
                "Upgrade with: pip install 'azure-ai-projects>=1.0.0b3' --upgrade"
            )
    except Exception as exc:
        logger.warning("AIProjectClient strategy failed: %s", exc)

    # Strategy 2 — direct AzureOpenAI using Foundry endpoint
    # The Foundry project endpoint root works as an Azure OpenAI endpoint.
    # Strip trailing /api/projects/<name> to get the base OpenAI endpoint.
    import re
    # Foundry format: https://<hub>.services.ai.azure.com/api/projects/<project>
    # Azure OpenAI format: https://<hub>.services.ai.azure.com/
    base_endpoint = re.sub(r"/api/projects/[^/]+/?$", "/", endpoint)
    if not base_endpoint.endswith("/"):
        base_endpoint += "/"

    logger.debug("OpenAI client direct to endpoint: %s", base_endpoint)
    return AzureOpenAI(
        azure_endpoint=base_endpoint,
        azure_ad_token_provider=_token_provider(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )


@lru_cache(maxsize=1)
def get_search_client() -> SearchClient:
    return SearchClient(
        endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
        index_name=settings.AZURE_SEARCH_INDEX,
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
