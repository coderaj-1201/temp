"""
Hybrid Search tool — BM25 keyword + dense vector with Reciprocal Rank Fusion (RRF).
Uses Azure AI Search's built-in semantic ranker as the final re-ranking step.

Reference:
  https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from azure.search.documents.models import VectorizedQuery

from shared.azure_clients import INDEX_MAP, get_openai_client, get_search_client
from shared.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchDocument:
    id: str
    content: str
    source: str
    score: float  # semantic reranker score (0–4 range for Azure AI Search)


def _embed(text: str) -> list[float]:
    client = get_openai_client()
    response = client.embeddings.create(
        input=text,
        model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
    )
    return response.data[0].embedding


def hybrid_search(query: str, domain: str, top_k: int | None = None) -> list[SearchDocument]:
    """
    Execute a hybrid BM25 + vector query with semantic reranking.

    Returns up to `top_k` documents sorted by descending reranker score.
    The Azure AI Search semantic ranker applies cross-encoder re-ranking on the
    merged BM25 + vector result set (RRF fusion is applied internally by the service).
    """
    k = top_k or settings.RETRIEVAL_TOP_K
    index_name = INDEX_MAP[domain]
    client = get_search_client(index_name)

    vector_query = VectorizedQuery(
        vector=_embed(query),
        k_nearest_neighbors=k,
        fields="content_vector",
        exhaustive=False,  # use HNSW index (ANN) for production speed
    )

    try:
        results = client.search(
            search_text=query,              # BM25 keyword leg
            vector_queries=[vector_query],  # Dense vector leg (RRF fusion in service)
            query_type="semantic",
            semantic_configuration_name=settings.AZURE_SEARCH_SEMANTIC_CONFIG,
            top=k,
            select=["id", "content", "source"],
        )

        docs: list[SearchDocument] = []
        for r in results:
            docs.append(
                SearchDocument(
                    id=r["id"],
                    content=r["content"],
                    source=r.get("source", "unknown"),
                    score=r.get("@search.reranker_score") or r.get("@search.score", 0.0),
                )
            )

        docs.sort(key=lambda d: d.score, reverse=True)
        logger.debug(
            "hybrid_search domain=%s query='%s' returned %d docs (top score=%.3f)",
            domain,
            query[:60],
            len(docs),
            docs[0].score if docs else 0.0,
        )
        return docs

    except Exception as exc:
        logger.error("Azure AI Search error for domain=%s: %s", domain, exc, exc_info=True)
        return []
