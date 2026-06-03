"""
Main Agent — Local Dev
=======================
MAF Functional Workflow (@workflow / @step).
Entry point for all queries. Calls Orchestrator via HTTP.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import httpx
import uvicorn
from agent_framework import step, workflow
from fastapi import FastAPI, Request, Response

from shared.logging_config import configure_logging, get_logger
from shared.models import FinalResponse, UserQuery

configure_logging()
logger = get_logger(__name__)

_ORCHESTRATOR_URL = "http://localhost:8001"

_FAILURE_MSG = (
    "I wasn't able to find a confident answer after exhausting all retrieval strategies.\n\n"
    "📋 **Option 1 — Raise a Support Ticket**\nReply with: `raise_ticket`\n\n"
    "👤 **Option 2 — Connect with a Subject Matter Expert**\nReply with: `connect_sme`"
)


@step
async def call_orchestrator(user_query: UserQuery) -> FinalResponse:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{_ORCHESTRATOR_URL}/orchestrate", json=user_query.__dict__)
        resp.raise_for_status()
        return FinalResponse(**resp.json())


@step
async def handle_raise_ticket(user_id: str) -> str:
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    logger.info("Ticket raised ticket_id=%s user_id=%s", ticket_id, user_id)
    return (f"✅ **Ticket raised!** Reference: `{ticket_id}`\n"
            f"Expected response: **4 business hours**.")


@step
async def handle_connect_sme(user_id: str) -> str:
    logger.info("SME connect requested user_id=%s", user_id)
    return "✅ **Connecting you with an SME.** Expected response: **2 business hours**."


@workflow(name="main_agent_workflow")
async def main_agent_workflow(user_query: UserQuery) -> str:
    text = user_query.text.strip().lower()

    if text == "raise_ticket":
        return await handle_raise_ticket(user_query.user_id)
    if text == "connect_sme":
        return await handle_connect_sme(user_query.user_id)

    try:
        final: FinalResponse = await call_orchestrator(user_query)
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        return _FAILURE_MSG

    if final.status == "success":
        sources_text = ""
        if final.sources:
            bullets = "\n".join(f"  • {s.get('title', s.get('source', ''))}" for s in final.sources)
            sources_text = f"\n\n📚 **Sources:**\n{bullets}"
        meta = (f"*Domain: {final.domain.upper() if final.domain else 'N/A'} | "
                f"Confidence: {final.confidence:.0%} | Attempts: {final.attempts_used}*")
        return f"{final.answer}{sources_text}\n\n{meta}"

    return _FAILURE_MSG


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Main Agent started.")
    yield
    logger.info("Main Agent stopped.")


app = FastAPI(title="RAG Main Agent — Local", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "main"}


@app.post("/query")
async def query(raw: Request) -> Response:
    body = await raw.json()
    user_query = UserQuery(
        text=body["text"],
        conversation_id=body.get("conversation_id", str(uuid.uuid4())),
        user_id=body.get("user_id", "anonymous"),
    )
    result_obj = await main_agent_workflow.run(user_query)
    outputs    = result_obj.get_outputs()
    reply: str = outputs[0] if outputs else _FAILURE_MSG
    return Response(content=json.dumps({"reply": reply}), media_type="application/json")


if __name__ == "__main__":
    uvicorn.run("agents.main_agent:app", host="0.0.0.0", port=8000, reload=False)
