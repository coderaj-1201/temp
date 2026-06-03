"""
Main Agent — Production Container Version
=========================================
MAF Functional Workflow.

New in this version:
  - Fires EvaluationPayload to Service Bus after every successful answer (non-blocking)
  - /api/feedback proxied to Feedback Agent
  - /api/telemetry logged to App Insights
  - /api/analytics/* proxied to Feedback Agent (frontend dashboard)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import uvicorn
from agent_framework import step, workflow
from azure.servicebus import ServiceBusMessage
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from shared.auth import verify_token
from shared.config import settings
from shared.logging_config import configure_logging, get_logger
from shared.models import (
    ChatRequest,
    ChatResponse,
    EvaluationPayload,
    FeedbackRequest,
    FinalResponse,
    SourceDocument,
    TelemetryRequest,
    UserQuery,
)
from shared.telemetry import emit_feedback

configure_logging("rag-main")
logger = get_logger(__name__)

_ORCHESTRATOR_URL  = os.getenv("ORCHESTRATOR_AGENT_URL",  "http://rag-orchestrator:8001")
_FEEDBACK_AGENT_URL = os.getenv("FEEDBACK_AGENT_URL",     "http://rag-feedback:8004")

_FAILURE_MESSAGE = (
    "I wasn't able to find a confident answer after exhausting all retrieval strategies.\n\n"
    "📋 **Option 1 — Raise a Support Ticket**\nReply with: `raise_ticket`\n\n"
    "👤 **Option 2 — Connect with a Subject Matter Expert**\nReply with: `connect_sme`"
)


# ── Evaluation fire-and-forget ────────────────────────────────────────────────

async def _enqueue_evaluation(final: FinalResponse, query: str, elapsed_ms: int) -> None:
    """
    Send evaluation payload to Service Bus queue — non-blocking.
    Called after answer is returned to user.
    """
    if final.status != "success" or not final.answer:
        return
    try:
        payload = EvaluationPayload(
            answer_id          = final.answer_id,
            query              = query,
            answer             = final.answer,
            domain             = str(final.domain) if final.domain else "unknown",
            sources            = final.sources,
            confidence         = final.confidence,
            attempts_used      = final.attempts_used,
            processing_time_ms = elapsed_ms,
            user_id            = final.user_id,
            conversation_id    = final.conversation_id,
            evaluated_at       = datetime.now(timezone.utc).isoformat(),
        )
        from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
        from azure.servicebus.aio import ServiceBusClient as AsyncSBClient

        credential = (
            ManagedIdentityCredential() if os.getenv("RUNNING_IN_AZURE")
            else AzureCliCredential()
        )
        async with AsyncSBClient(
            fully_qualified_namespace=settings.AZURE_SERVICE_BUS_NAMESPACE,
            credential=credential,
        ) as sb:
            async with sb.get_queue_sender(settings.AZURE_SERVICE_BUS_QUEUE_EVALUATION) as sender:
                msg = ServiceBusMessage(
                    body=json.dumps(payload.model_dump()),
                    content_type="application/json",
                    message_id=final.answer_id,
                )
                await sender.send_messages(msg)
                logger.debug("Evaluation queued for answer_id=%s", final.answer_id)
    except Exception as exc:
        # Non-critical — don't fail the user response if eval queue fails
        logger.warning("Failed to queue evaluation: %s", exc)


# ── Steps ─────────────────────────────────────────────────────────────────────

@step
async def call_orchestrator(user_query: UserQuery) -> FinalResponse:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{_ORCHESTRATOR_URL}/orchestrate",
            json=user_query.__dict__,
        )
        resp.raise_for_status()
        return FinalResponse(**resp.json())


@step
async def handle_raise_ticket(user_id: str, conversation_id: str) -> str:
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    logger.info("Ticket raised ticket_id=%s user_id=%s", ticket_id, user_id)
    return (f"✅ **Ticket raised!** Reference: `{ticket_id}`\n"
            f"Expected response: **4 business hours**.")


@step
async def handle_connect_sme(user_id: str, domain: str | None) -> str:
    domain_label = (domain or "general").upper()
    return (f"✅ **Connecting you with a {domain_label} SME.**\n"
            f"Expected response: **2 business hours**.")


# ── Main workflow ─────────────────────────────────────────────────────────────

@workflow(name="main_agent_workflow")
async def main_agent_workflow(user_query: UserQuery) -> FinalResponse:
    text = user_query.text.strip().lower()

    if text == "raise_ticket":
        answer = await handle_raise_ticket(user_query.user_id, user_query.conversation_id)
        return FinalResponse(status="success", answer=answer, domain=None,
                             conversation_id=user_query.conversation_id,
                             user_id=user_query.user_id)

    if text == "connect_sme":
        answer = await handle_connect_sme(user_query.user_id, None)
        return FinalResponse(status="success", answer=answer, domain=None,
                             conversation_id=user_query.conversation_id,
                             user_id=user_query.user_id)

    try:
        return await call_orchestrator(user_query)
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        return FinalResponse(
            status="failure", answer=_FAILURE_MESSAGE, domain=None,
            conversation_id=user_query.conversation_id, user_id=user_query.user_id,
        )


# ── FastAPI ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Main Agent started.")
    yield
    logger.info("Main Agent stopped.")


app = FastAPI(title="RAG Main Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "main"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    claims: dict = Depends(verify_token),
) -> ChatResponse:
    import time
    start    = time.monotonic()
    user_id  = claims.get("oid", request.user_id)
    user_email = claims.get("unique_name", "")

    user_query = UserQuery(
        text            = request.query,
        conversation_id = request.conversation_id,
        user_id         = user_id,
        user_email      = user_email,
        program         = request.program,
        source          = request.source,
        jurisdiction    = request.jurisdiction,
    )

    result_obj = await main_agent_workflow.run(user_query)
    outputs    = result_obj.get_outputs()
    final: FinalResponse = outputs[0] if outputs else FinalResponse(
        status="failure", answer=_FAILURE_MESSAGE, domain=None,
        conversation_id=request.conversation_id, user_id=user_id,
    )

    elapsed = int((time.monotonic() - start) * 1000)
    answer  = final.answer if final.status == "success" else _FAILURE_MESSAGE

    # Fire evaluation async — does NOT block the response
    asyncio.create_task(_enqueue_evaluation(final, request.query, elapsed))

    return ChatResponse(
        answer             = answer,
        source_documents   = [SourceDocument(**s) for s in final.sources],
        confidence         = final.confidence,
        answer_id          = final.answer_id,
        processing_time_ms = elapsed,
    )


@app.post("/api/feedback")
async def feedback(
    request: FeedbackRequest,
    claims: dict = Depends(verify_token),
) -> dict:
    """Proxy to Feedback Agent — which stores in CosmosDB + App Insights."""
    domain = "hr"   # TODO: pass domain through ChatResponse so frontend can send it back
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_FEEDBACK_AGENT_URL}/feedback",
                json=request.model_dump(),
                params={"domain": domain},
                headers={"Authorization": f"Bearer {claims.get('_raw_token', '')}"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("Feedback agent call failed: %s", exc)
        # Don't fail user — feedback is non-critical
        return {"status": "queued", "feedback_id": f"fb-{uuid.uuid4().hex[:8]}"}


@app.post("/api/telemetry")
async def telemetry(
    request: TelemetryRequest,
    claims: dict = Depends(verify_token),
) -> dict:
    """Log telemetry events directly to App Insights."""
    logger.info(
        "telemetry event_type=%s user_id=%s",
        request.event_type, request.user_id,
    )
    # Emit as custom App Insights event
    try:
        from shared.telemetry import _get_telemetry_client
        client = _get_telemetry_client()
        if client:
            client.track_event(
                f"rag_telemetry_{request.event_type}",
                properties={
                    "user_id":    request.user_id,
                    "session_id": request.session_id,
                    **{k: str(v) for k, v in request.metadata.items()},
                },
            )
            client.flush()
    except Exception as exc:
        logger.warning("Telemetry emit failed: %s", exc)

    return {"status": "success", "event_id": f"evt-{uuid.uuid4().hex[:8]}"}


# ── Analytics proxy (frontend dashboard) ──────────────────────────────────────

@app.get("/api/analytics/stats/{domain}")
async def analytics_stats(
    domain: str,
    claims: dict = Depends(verify_token),
) -> dict:
    """Proxy to Feedback Agent analytics endpoint."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{_FEEDBACK_AGENT_URL}/analytics/stats/{domain}")
        resp.raise_for_status()
        return resp.json()


@app.get("/api/analytics/evaluations/{domain}")
async def analytics_evaluations(
    domain: str,
    limit: int = 50,
    claims: dict = Depends(verify_token),
) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_FEEDBACK_AGENT_URL}/analytics/evaluations/{domain}",
            params={"limit": limit},
        )
        resp.raise_for_status()
        return resp.json()


@app.get("/api/analytics/feedback/{domain}")
async def analytics_feedback(
    domain: str,
    limit: int = 50,
    claims: dict = Depends(verify_token),
) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_FEEDBACK_AGENT_URL}/analytics/feedback/{domain}",
            params={"limit": limit},
        )
        resp.raise_for_status()
        return resp.json()


@app.get("/api/analytics/low-scoring")
async def analytics_low_scoring(
    threshold: float = 3.0,
    domain: str = "",
    limit: int = 20,
    claims: dict = Depends(verify_token),
) -> dict:
    """Knowledge gap detection — queries the system answered poorly."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_FEEDBACK_AGENT_URL}/analytics/low-scoring",
            params={"threshold": threshold, "domain": domain, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    uvicorn.run("agents.main_agent:app", host="0.0.0.0", port=8000, workers=2)
