"""
Feedback Agent
==============
MAF Functional Workflow (@workflow / @step).

Handles all user feedback from the Teams card (thumbs up/down + comment).
Stores in CosmosDB feedback container and emits to App Insights.

Also exposes analytics endpoints for the frontend dashboard:
  GET /analytics/stats/{domain}      — aggregate scores + ratings
  GET /analytics/evaluations/{domain} — recent evaluation records
  GET /analytics/feedback/{domain}    — recent feedback records
  GET /analytics/low-scoring         — queries with low eval scores (knowledge gaps)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from agent_framework import step, workflow
from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse

from shared.auth import verify_token
from shared.config import settings
from shared.cosmos_db import (
    get_domain_stats,
    query_evaluations,
    query_feedback,
    upsert_feedback,
)
from shared.logging_config import configure_logging, get_logger
from shared.models import FeedbackRecord, FeedbackRequest
from shared.telemetry import emit_feedback

configure_logging("rag-feedback")
logger = get_logger(__name__)


# ── Step ─────────────────────────────────────────────────────────────────────

@step
async def store_feedback(request: FeedbackRequest, domain: str) -> FeedbackRecord:
    """Persist feedback to CosmosDB and emit to App Insights."""
    feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
    submitted_at = datetime.now(timezone.utc).isoformat()

    record = FeedbackRecord(
        id          = feedback_id,
        feedback_id = feedback_id,
        answer_id   = request.answer_id,
        user_id     = request.user_id,
        domain      = domain,
        rating      = request.rating,
        is_accurate = request.is_accurate,
        is_complete = request.is_complete,
        comment     = request.feedback,
        submitted_at = submitted_at,
    )

    await upsert_feedback(record.model_dump())
    emit_feedback(record.model_dump())

    logger.info(
        "Feedback stored feedback_id=%s answer_id=%s rating=%d",
        feedback_id, request.answer_id, request.rating,
        extra={"answer_id": request.answer_id, "domain": domain},
    )
    return record


# ── Workflow ──────────────────────────────────────────────────────────────────

@workflow(name="feedback_workflow")
async def feedback_workflow(request: FeedbackRequest, domain: str) -> FeedbackRecord:
    return await store_feedback(request, domain)


# ── FastAPI ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Feedback Agent started.")
    yield
    from shared.azure_clients import close_cosmos_client
    await close_cosmos_client()
    logger.info("Feedback Agent stopped.")


app = FastAPI(title="RAG Feedback & Analytics Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "feedback"}


# ── Feedback endpoint (called by main agent, proxied from /api/feedback) ─────

@app.post("/feedback")
async def submit_feedback(
    request: FeedbackRequest,
    domain: str = Query(default="hr", description="hr | legal | it"),
    claims: dict = Depends(verify_token),
) -> dict:
    """
    Store feedback from Teams card.
    Called internally by the main agent's /api/feedback endpoint.
    domain is passed as a query param since the main agent knows it from context.
    """
    result_obj = await feedback_workflow.run(request, domain)
    outputs    = result_obj.get_outputs()
    record: FeedbackRecord = outputs[0]
    return {"status": "success", "feedback_id": record.feedback_id}


# ── Analytics endpoints (frontend dashboard) ──────────────────────────────────

@app.get("/analytics/stats/{domain}")
async def domain_stats(
    domain: str,
    claims: dict = Depends(verify_token),
) -> dict:
    """
    Aggregate evaluation scores + feedback ratings for a domain.
    Use this for a domain health dashboard.
    """
    stats = await get_domain_stats(domain)
    return stats


@app.get("/analytics/evaluations/{domain}")
async def recent_evaluations(
    domain: str,
    limit: int = Query(default=50, ge=1, le=500),
    claims: dict = Depends(verify_token),
) -> dict:
    """
    Recent evaluation records for a domain.
    Useful for identifying patterns in low-scoring queries.
    """
    records = await query_evaluations(domain, limit)
    return {"domain": domain, "count": len(records), "records": records}


@app.get("/analytics/feedback/{domain}")
async def recent_feedback(
    domain: str,
    limit: int = Query(default=50, ge=1, le=500),
    claims: dict = Depends(verify_token),
) -> dict:
    """Recent user feedback for a domain."""
    records = await query_feedback(domain, limit)
    return {"domain": domain, "count": len(records), "records": records}


@app.get("/analytics/low-scoring")
async def low_scoring_queries(
    threshold: float = Query(default=3.0, ge=1.0, le=5.0),
    domain: str      = Query(default=""),
    limit: int       = Query(default=20, ge=1, le=100),
    claims: dict     = Depends(verify_token),
) -> dict:
    """
    Queries where overall_score < threshold.
    These are knowledge gaps — documents missing from the index.
    Use this to prioritise what to ingest next.
    """
    domains = [domain] if domain else ["hr", "legal", "it"]
    low_scoring = []

    for d in domains:
        records = await query_evaluations(d, limit=200)
        low = [
            {
                "domain":        r.get("domain"),
                "query":         r.get("query"),
                "overall_score": r.get("overall_score"),
                "groundedness":  r.get("groundedness"),
                "relevance":     r.get("relevance"),
                "answer_id":     r.get("answer_id"),
                "evaluated_at":  r.get("evaluated_at"),
            }
            for r in records
            if r.get("overall_score", 5) < threshold
        ]
        low_scoring.extend(low)

    low_scoring.sort(key=lambda x: x.get("overall_score", 5))
    return {
        "threshold":    threshold,
        "count":        len(low_scoring),
        "queries":      low_scoring[:limit],
    }


if __name__ == "__main__":
    uvicorn.run("agents.feedback_agent:app", host="0.0.0.0", port=8004, reload=False)
