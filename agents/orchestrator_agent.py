"""
Orchestrator Agent
==================
Microsoft Agent Framework — Functional Workflow API (@workflow / @step).

Responsibilities:
  - Classify incoming query → domain (hr / legal / it) + initial tool
  - Drive the retry loop (max 3 attempts) calling the Retrieval Agent
  - Escalate retrieval tool on each retry: hybrid → hyde → decomposition
  - Return FinalResponse (success or failure) to Main Agent

Communication with Retrieval Agent:
  - LOCAL DEV:  direct HTTP call to retrieval_agent FastAPI (no Service Bus needed)
  - PRODUCTION: Azure Service Bus (async, decoupled, observable)
  The RETRIEVAL_MODE env var switches between modes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from agent_framework import step, workflow
from fastapi import FastAPI, Request, Response

from shared.azure_clients import get_openai_client
from shared.config import settings
from shared.logging_config import configure_logging, get_logger
from shared.models import (
    Domain,
    FinalResponse,
    OrchestratorRequest,
    RetrievalResult,
    RetrievalTool,
    UserQuery,
)

configure_logging()
logger = get_logger(__name__)

# Tool escalation ladder — softest first, most expensive last
_TOOL_LADDER: list[RetrievalTool] = [
    RetrievalTool.HYBRID,
    RetrievalTool.HYDE,
    RetrievalTool.DECOMPOSITION,
]

_RETRIEVAL_BASE_URL = os.getenv("RETRIEVAL_AGENT_URL", "http://localhost:8002")
_RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "http")  # "http" | "servicebus"


# ── Query classification ──────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """You are an enterprise query router.
Classify the user question into:
  - domain: "hr" (people, leave, payroll, benefits, onboarding)
           "legal" (contracts, compliance, GDPR, NDA, regulatory)
           "it" (software, hardware, access, VPN, security, infrastructure)
  - tool:   "hybrid" for clear, direct questions
            "hyde" for vague, conceptual, or hypothetical questions
            "decomposition" for complex, multi-part questions

Return ONLY valid JSON. No markdown, no explanation.
Format: {"domain": "hr|legal|it", "tool": "hybrid|hyde|decomposition", "reason": "brief"}"""


@step
async def classify_query(query: str) -> tuple[Domain, RetrievalTool]:
    client = get_openai_client()
    response = await asyncio.to_thread(
        client.chat.completions.create,
        model=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": f"Question: {query}"},
        ],
        temperature=0,
        max_tokens=120,
        response_format={"type": "json_object"},
    )
    raw = json.loads(response.choices[0].message.content)
    domain = Domain(raw.get("domain", "it"))
    tool = RetrievalTool(raw.get("tool", "hybrid"))
    logger.info("classify_query domain=%s tool=%s reason='%s'", domain, tool, raw.get("reason", ""))
    return domain, tool


# ── Retrieval dispatch ────────────────────────────────────────────────────────

@step
async def call_retrieval_agent(req: OrchestratorRequest) -> RetrievalResult:
    """
    Dispatch to the Retrieval Agent.
    In local dev mode: direct HTTP.
    In production: Azure Service Bus (see service_bus.py helper).
    """
    if _RETRIEVAL_MODE == "servicebus":
        from shared.service_bus import send_and_receive_retrieval
        return await send_and_receive_retrieval(req)

    # Default: direct HTTP (local dev + same-network ACA)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_RETRIEVAL_BASE_URL}/retrieve",
            json=req.__dict__,
        )
        resp.raise_for_status()
        data = resp.json()
        return RetrievalResult(**data)


# ── Orchestration Workflow ────────────────────────────────────────────────────

@workflow(name="orchestrator_workflow")
async def orchestrator_workflow(user_query: UserQuery) -> FinalResponse:
    """
    MAF Functional Workflow implementing the classify → retrieve → retry loop.

    Loop invariant:
      - attempt index maps to _TOOL_LADDER (attempt 0 = hybrid, 1 = hyde, 2 = decomposition)
      - if confidence >= threshold → success
      - after MAX_RETRIEVAL_ATTEMPTS failures → return failure FinalResponse
    """
    logger.info(
        "orchestrator_workflow started conversation_id=%s query='%s'",
        user_query.conversation_id,
        user_query.text[:80],
    )

    # Step 1: Classify once; tool may be overridden by the escalation ladder
    domain, initial_tool = await classify_query(user_query.text)

    last_result: RetrievalResult | None = None

    for attempt_idx in range(settings.MAX_RETRIEVAL_ATTEMPTS):
        # Escalate tool on each retry regardless of initial classification
        tool = _TOOL_LADDER[attempt_idx]
        attempt_number = attempt_idx + 1

        logger.info(
            "orchestrator attempt=%d/%d domain=%s tool=%s",
            attempt_number,
            settings.MAX_RETRIEVAL_ATTEMPTS,
            domain,
            tool,
        )

        req = OrchestratorRequest(
            query=user_query.text,
            domain=domain,
            tool=tool,
            attempt=attempt_number,
            conversation_id=user_query.conversation_id,
            user_id=user_query.user_id,
        )

        try:
            result = await call_retrieval_agent(req)
            last_result = result
        except Exception as exc:
            logger.error("Retrieval agent call failed on attempt %d: %s", attempt_number, exc, exc_info=True)
            continue

        if result.passed:
            logger.info(
                "orchestrator SUCCESS attempt=%d confidence=%.3f",
                attempt_number,
                result.confidence,
            )
            return FinalResponse(
                status="success",
                answer=result.answer,
                domain=domain,
                sources=result.sources,
                confidence=result.confidence,
                attempts_used=attempt_number,
                conversation_id=user_query.conversation_id,
                user_id=user_query.user_id,
            )

        logger.warning(
            "orchestrator attempt=%d BELOW THRESHOLD confidence=%.3f < %.3f",
            attempt_number,
            result.confidence,
            settings.CONFIDENCE_THRESHOLD,
        )

    # All retries exhausted
    logger.error(
        "orchestrator FAILED after %d attempts conversation_id=%s",
        settings.MAX_RETRIEVAL_ATTEMPTS,
        user_query.conversation_id,
    )
    return FinalResponse(
        status="failure",
        answer="",
        domain=domain,
        sources=last_result.sources if last_result else [],
        confidence=last_result.confidence if last_result else 0.0,
        attempts_used=settings.MAX_RETRIEVAL_ATTEMPTS,
        conversation_id=user_query.conversation_id,
        user_id=user_query.user_id,
    )


# ── FastAPI host ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Orchestrator Agent starting up.")
    yield
    logger.info("Orchestrator Agent shutting down.")


app = FastAPI(title="RAG Orchestrator Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "orchestrator"}


@app.post("/orchestrate")
async def orchestrate(raw: Request) -> Response:
    body = await raw.json()
    user_query = UserQuery(**body)
    result = await orchestrator_workflow.run(user_query)
    outputs = result.get_outputs()
    final: FinalResponse = outputs[0] if outputs else FinalResponse(
        status="failure", answer="", domain=None,
        conversation_id=user_query.conversation_id,
        user_id=user_query.user_id,
    )
    return Response(
        content=json.dumps(final.__dict__),
        media_type="application/json",
    )


if __name__ == "__main__":
    uvicorn.run("agents.orchestrator_agent:app", host="0.0.0.0", port=8001, reload=False)
