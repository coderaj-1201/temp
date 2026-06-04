"""
CosmosDB helper.
Both containers (evaluations + feedback) use /domain as partition key
so you can query "all feedback for hr domain" efficiently.

Uses the long-lived singleton from azure_clients — does NOT use `async with`.
"""
from __future__ import annotations

import logging
from typing import Any

from shared.azure_clients import get_cosmos_client
from shared.config import settings

logger = logging.getLogger(__name__)


def _evals_container():
    cosmos = get_cosmos_client()
    db = cosmos.get_database_client(settings.AZURE_COSMOS_DATABASE)
    return db.get_container_client(settings.AZURE_COSMOS_CONTAINER_EVALUATIONS)


def _feedback_container():
    cosmos = get_cosmos_client()
    db = cosmos.get_database_client(settings.AZURE_COSMOS_DATABASE)
    return db.get_container_client(settings.AZURE_COSMOS_CONTAINER_FEEDBACK)


async def upsert_evaluation(record: dict) -> None:
    """Upsert an evaluation record. Partition key = /domain."""
    await _evals_container().upsert_item(record)
    logger.debug("Upserted evaluation id=%s", record.get("id"))


async def upsert_feedback(record: dict) -> None:
    """Upsert a feedback record. Partition key = /domain."""
    await _feedback_container().upsert_item(record)
    logger.debug("Upserted feedback id=%s", record.get("id"))


async def get_evaluation(answer_id: str, domain: str) -> dict | None:
    """Fetch a single evaluation by answer_id."""
    try:
        item = await _evals_container().read_item(item=answer_id, partition_key=domain)
        return item
    except Exception:
        return None


async def query_evaluations(domain: str, limit: int = 100) -> list[dict]:
    """Query recent evaluations for a domain."""
    query = (
        "SELECT * FROM c WHERE c.domain = @domain "
        "ORDER BY c.evaluated_at DESC OFFSET 0 LIMIT @limit"
    )
    params = [{"name": "@domain", "value": domain}, {"name": "@limit", "value": limit}]
    items = []
    async for item in _evals_container().query_items(query=query, parameters=params):
        items.append(item)
    return items


async def query_feedback(domain: str, limit: int = 100) -> list[dict]:
    """Query recent feedback for a domain."""
    query = (
        "SELECT * FROM c WHERE c.domain = @domain "
        "ORDER BY c.submitted_at DESC OFFSET 0 LIMIT @limit"
    )
    params = [{"name": "@domain", "value": domain}, {"name": "@limit", "value": limit}]
    items = []
    async for item in _feedback_container().query_items(query=query, parameters=params):
        items.append(item)
    return items


async def get_domain_stats(domain: str) -> dict:
    """Aggregate evaluation scores + feedback ratings for a domain."""
    eval_q = """
        SELECT
            COUNT(1) AS total_queries,
            AVG(c.overall_score)    AS avg_overall_score,
            AVG(c.groundedness)     AS avg_groundedness,
            AVG(c.relevance)        AS avg_relevance,
            AVG(c.coherence)        AS avg_coherence,
            AVG(c.confidence)       AS avg_rag_confidence,
            AVG(c.processing_time_ms) AS avg_latency_ms
        FROM c WHERE c.domain = @domain
    """
    fb_q = """
        SELECT
            COUNT(1)       AS total_feedback,
            AVG(c.rating)  AS avg_rating,
            SUM(c.is_accurate ? 1 : 0) AS accurate_count,
            SUM(c.is_complete ? 1 : 0) AS complete_count
        FROM c WHERE c.domain = @domain
    """
    params = [{"name": "@domain", "value": domain}]

    eval_stats, fb_stats = {}, {}
    async for item in _evals_container().query_items(query=eval_q, parameters=params):
        eval_stats = item
    async for item in _feedback_container().query_items(query=fb_q, parameters=params):
        fb_stats = item

    return {"domain": domain, "evaluations": eval_stats, "feedback": fb_stats}
