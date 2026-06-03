"""
Main Agent — Production Container Version
=========================================
MAF Functional Workflow.

Exposes the exact API contract the frontend expects:
  POST /api/chat       — main RAG query
  POST /api/feedback   — thumbs up/down from Teams card
  POST /api/telemetry  — usage events
  GET  /health         — ACA probe

New vs local:
  - Entra ID token validation on all /api/* routes
  - ChatRequest / ChatResponse match frontend doc exactly
  - Feedback + Telemetry stubs ready for CosmosDB / App Insights wiring
  - Keyless auth throughout
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

import httpx
import uvicorn
from agent_framework import step, workflow
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from shared.auth import verify_token
from shared.config import settings
from shared.logging_config import configure_logging, get_logger
from shared.models import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    FinalResponse,
    SourceDocument,
    TelemetryRequest,
    UserQuery,
)

configure_logging("rag-main")
logger = get_logger(__name__)

_ORCHESTRATOR_URL = "http://rag-orchestrator:8001"   # ACA internal DNS

_FAILURE_MESSAGE = (
    "I wasn't able to find a confident answer after exhausting all retrieval strategies.\n\n"
    "Please choose an option:\n\n"
    "📋 **Option 1 — Raise a Support Ticket**\nReply with: `raise_ticket`\n\n"
    "👤 **Option 2 — Connect with a Subject Matter Expert**\nReply with: `connect_sme`"
)


# ── Step functions ────────────────────────────────────────────────────────────

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
    # TODO: POST to ServiceNow / Jira REST API
    return (
        f"✅ **Ticket raised!** Reference: `{ticket_id}`\n"
        f"Expected response: **4 business hours**."
    )


@step
async def handle_connect_sme(user_id: str, domain: str | None) -> str:
    domain_label = (domain or "general").upper()
    logger.info("SME connect user_id=%s domain=%s", user_id, domain_label)
    # TODO: POST to Teams channel / SME routing API
    return (
        f"✅ **Connecting you with a {domain_label} SME.**\n"
        f"You'll receive a Teams message within **2 business hours**."
    )


# ── Main workflow ─────────────────────────────────────────────────────────────

@workflow(name="main_agent_workflow")
async def main_agent_workflow(user_query: UserQuery) -> FinalResponse:
    text = user_query.text.strip().lower()

    if text == "raise_ticket":
        answer = await handle_raise_ticket(user_query.user_id, user_query.conversation_id)
        return FinalResponse(status="success", answer=answer, domain=None,
                             conversation_id=user_query.conversation_id, user_id=user_query.user_id)

    if text == "connect_sme":
        answer = await handle_connect_sme(user_query.user_id, None)
        return FinalResponse(status="success", answer=answer, domain=None,
                             conversation_id=user_query.conversation_id, user_id=user_query.user_id)

    try:
        return await call_orchestrator(user_query)
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        return FinalResponse(status="failure", answer=_FAILURE_MESSAGE, domain=None,
                             conversation_id=user_query.conversation_id, user_id=user_query.user_id)


# ── FastAPI ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Main Agent started.")
    yield
    logger.info("Main Agent shut down.")


app = FastAPI(title="RAG Main Agent", lifespan=lifespan)

# CORS — tighten allowed_origins in production to your frontend domain
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
    """
    Primary RAG endpoint — matches frontend POST /api/chat contract.
    Token validated via Entra ID; user_id resolved from token claims.
    """
    import time
    start = time.monotonic()

    # Trust token claims over request body for user identity
    user_id    = claims.get("oid", request.user_id)
    user_email = claims.get("unique_name", "")

    logger.info(
        "chat query='%.80s' user_id=%s",
        request.query, user_id,
        extra={"conversation_id": request.conversation_id, "user_id": user_id},
    )

    user_query = UserQuery(
        text=request.query,
        conversation_id=request.conversation_id,
        user_id=user_id,
        user_email=user_email,
        program=request.program,
        source=request.source,
        jurisdiction=request.jurisdiction,
    )

    result_obj = await main_agent_workflow.run(user_query)
    outputs    = result_obj.get_outputs()
    final: FinalResponse = outputs[0] if outputs else FinalResponse(
        status="failure", answer=_FAILURE_MESSAGE, domain=None,
        conversation_id=request.conversation_id, user_id=user_id,
    )

    answer  = final.answer if final.status == "success" else _FAILURE_MESSAGE
    elapsed = int((time.monotonic() - start) * 1000)

    return ChatResponse(
        answer=answer,
        source_documents=[SourceDocument(**s) for s in final.sources],
        confidence=final.confidence,
        answer_id=final.answer_id,
        processing_time_ms=elapsed,
    )


@app.post("/api/feedback")
async def feedback(
    request: FeedbackRequest,
    claims: dict = Depends(verify_token),
) -> dict:
    """Record user feedback on an answer."""
    feedback_id = f"fb-{uuid.uuid4().hex[:8]}"
    logger.info(
        "feedback answer_id=%s rating=%d accurate=%s",
        request.answer_id, request.rating, request.is_accurate,
    )
    # TODO: persist to CosmosDB / Azure Table Storage
    return {"status": "success", "feedback_id": feedback_id}


@app.post("/api/telemetry")
async def telemetry(
    request: TelemetryRequest,
    claims: dict = Depends(verify_token),
) -> dict:
    """Record usage telemetry events."""
    event_id = f"evt-{uuid.uuid4().hex[:8]}"
    logger.info("telemetry event_type=%s user_id=%s", request.event_type, request.user_id)
    # TODO: forward to App Insights custom events
    return {"status": "success", "event_id": event_id}


if __name__ == "__main__":
    uvicorn.run("agents.main_agent:app", host="0.0.0.0", port=8000, workers=2)
