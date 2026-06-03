"""
Main Agent
==========
Microsoft Agent Framework — Functional Workflow API (@workflow / @step).

Responsibilities:
  - Receive user messages from the Teams Bot
  - Forward queries to the Orchestrator Agent
  - Format successful answers for Teams Adaptive Cards
  - On failure: present two escalation options (raise ticket | connect SME)
  - Handle user selections for escalation

The Teams Bot (app.py) calls this agent's HTTP endpoint.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
import uvicorn
from agent_framework import step, workflow
from fastapi import FastAPI, Request, Response

from shared.config import settings
from shared.logging_config import configure_logging, get_logger
from shared.models import FinalResponse, UserQuery

configure_logging()
logger = get_logger(__name__)

_ORCHESTRATOR_BASE_URL = os.getenv("ORCHESTRATOR_AGENT_URL", "http://localhost:8001")

# ── Escalation command keywords ───────────────────────────────────────────────
_CMD_RAISE_TICKET = "raise_ticket"
_CMD_CONNECT_SME = "connect_sme"

_FAILURE_CARD = """I wasn't able to find a confident answer after exhausting all retrieval strategies. \
Please choose an option:

📋 **Option 1 — Raise a Support Ticket**
Reply with: `raise_ticket`

👤 **Option 2 — Connect with a Subject Matter Expert**
Reply with: `connect_sme`"""


# ── Step functions ────────────────────────────────────────────────────────────

@step
async def call_orchestrator(user_query: UserQuery) -> FinalResponse:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{_ORCHESTRATOR_BASE_URL}/orchestrate",
            json=user_query.__dict__,
        )
        resp.raise_for_status()
        return FinalResponse(**resp.json())


@step
async def handle_raise_ticket(user_id: str, conversation_id: str) -> str:
    """
    Production stub: integrate with your ITSM (ServiceNow / Jira) here.
    Returns a confirmation message string.
    """
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    logger.info("Ticket raised ticket_id=%s user_id=%s", ticket_id, user_id)
    # TODO: call ServiceNow / Jira REST API here
    return (
        f"✅ **Ticket raised successfully!**\n\n"
        f"**Reference:** `{ticket_id}`\n"
        f"You'll receive an email confirmation shortly. "
        f"Expected response time: **4 business hours**."
    )


@step
async def handle_connect_sme(user_id: str, domain: str | None) -> str:
    """
    Production stub: integrate with your SME routing / Teams channel here.
    Returns a confirmation message string.
    """
    domain_label = (domain or "general").upper()
    logger.info("SME connection requested user_id=%s domain=%s", user_id, domain_label)
    # TODO: post to Teams channel or trigger SME notification here
    return (
        f"✅ **Connecting you with a {domain_label} Subject Matter Expert.**\n\n"
        f"You'll receive a Teams message within **2 business hours**. "
        f"Please keep this chat open."
    )


# ── Main Workflow ─────────────────────────────────────────────────────────────

@workflow(name="main_agent_workflow")
async def main_agent_workflow(user_query: UserQuery) -> str:
    """
    MAF Functional Workflow — entry point for all Teams messages.
    Returns a formatted string ready to send back to the Teams user.
    """
    text = user_query.text.strip().lower()

    # Handle escalation command shortcuts
    if text == _CMD_RAISE_TICKET:
        return await handle_raise_ticket(user_query.user_id, user_query.conversation_id)

    if text == _CMD_CONNECT_SME:
        # We don't have domain context here; a production system would
        # persist it in a session store keyed on conversation_id
        return await handle_connect_sme(user_query.user_id, domain=None)

    # Forward to orchestrator
    try:
        final: FinalResponse = await call_orchestrator(user_query)
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        return _FAILURE_CARD

    if final.status == "success":
        sources_section = ""
        if final.sources:
            bullet_sources = "\n".join(f"  • {s}" for s in final.sources)
            sources_section = f"\n\n📚 **Sources:**\n{bullet_sources}"

        meta = (
            f"*Domain: {final.domain.upper() if final.domain else 'N/A'} | "
            f"Confidence: {final.confidence:.0%} | "
            f"Attempts: {final.attempts_used}*"
        )
        return f"{final.answer}{sources_section}\n\n{meta}"

    # Failure path — present escalation options
    return _FAILURE_CARD


# ── FastAPI host ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Main Agent starting up.")
    yield
    logger.info("Main Agent shutting down.")


app = FastAPI(title="RAG Main Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "main"}


@app.post("/query")
async def query(raw: Request) -> Response:
    """
    Accepts: {"text": "...", "conversation_id": "...", "user_id": "..."}
    Returns: {"reply": "..."}
    """
    body = await raw.json()
    # Ensure required fields with sensible defaults
    user_query = UserQuery(
        text=body["text"],
        conversation_id=body.get("conversation_id", str(uuid.uuid4())),
        user_id=body.get("user_id", "anonymous"),
    )
    result = await main_agent_workflow.run(user_query)
    outputs = result.get_outputs()
    reply: str = outputs[0] if outputs else _FAILURE_CARD
    return Response(
        content=json.dumps({"reply": reply}),
        media_type="application/json",
    )


if __name__ == "__main__":
    uvicorn.run("agents.main_agent:app", host="0.0.0.0", port=8000, reload=False)
