"""
Hybrid Search — single index with domain metadata filter.
BM25 + dense vector with RRF fusion + Azure AI Search semantic reranker.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from azure.search.documents.models import VectorizedQuery

from shared.azure_clients import get_openai_client, get_search_client
from shared.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchDocument:
    id: str
    content: str
    source: str
    domain: str
    score: float


def _embed(text: str) -> list[float]:
    resp = get_openai_client().embeddings.create(
        input=text,
        model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
    )
    return resp.data[0].embedding


def hybrid_search(query: str, domain: str, top_k: int | None = None) -> list[SearchDocument]:
    """
    Single index search with OData domain filter.
    Returns docs sorted by descending reranker score.
    """
    k = top_k or settings.RETRIEVAL_TOP_K
    client = get_search_client()

    vector_query = VectorizedQuery(
        vector=_embed(query),
        k_nearest_neighbors=k,
        fields="content_vector",
        exhaustive=False,
    )

    try:
        results = client.search(
            search_text=query,
            vector_queries=[vector_query],
            filter=f"domain eq '{domain}'",
            query_type="semantic",
            semantic_configuration_name=settings.AZURE_SEARCH_SEMANTIC_CONFIG,
            top=k,
            select=["id", "content", "source", "domain"],
        )
        docs = [
            SearchDocument(
                id=r["id"],
                content=r["content"],
                source=r.get("source", "unknown"),
                domain=r.get("domain", domain),
                score=r.get("@search.reranker_score") or r.get("@search.score", 0.0),
            )
            for r in results
        ]
        docs.sort(key=lambda d: d.score, reverse=True)
        logger.debug("hybrid_search domain=%s docs=%d top_score=%.3f",
                     domain, len(docs), docs[0].score if docs else 0.0)
        return docs
    except Exception as exc:
        logger.error("Search error domain=%s: %s", domain, exc, exc_info=True)
        return []
