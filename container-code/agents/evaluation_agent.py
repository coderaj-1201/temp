"""
Evaluation Agent
================
MAF Functional Workflow (@workflow / @step).

Triggered asynchronously AFTER the user receives their answer.
Zero latency impact on the user — runs in background via Service Bus.

Evaluates 3 dimensions using Azure AI Foundry evaluation SDK:
  Groundedness (1-5): Is the answer supported by the retrieved context?
  Relevance    (1-5): Did retrieval return the right chunks for the query?
  Coherence    (1-5): Is the answer well-formed, clear, and logical?

Stores results in:
  - CosmosDB (evaluations container) — queryable for analytics
  - App Insights (custom events)     — Log Analytics dashboards
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from agent_framework import step, workflow
from fastapi import FastAPI

from shared.azure_clients import get_openai_client
from shared.config import settings
from shared.cosmos_db import upsert_evaluation
from shared.logging_config import configure_logging, get_logger
from shared.models import EvaluationPayload, EvaluationResult
from shared.telemetry import emit_evaluation

configure_logging("rag-evaluation")
logger = get_logger(__name__)

# ── Evaluation prompt ─────────────────────────────────────────────────────────

_EVAL_SYSTEM = """You are an expert RAG system evaluator.
Evaluate the answer against the query and context on three dimensions.

Score each dimension from 1 to 5:
  5 = Excellent
  4 = Good
  3 = Acceptable
  2 = Poor
  1 = Unacceptable

Dimensions:
  GROUNDEDNESS  — Is every claim in the answer directly supported by the context?
                  5=fully grounded, 1=mostly hallucinated
  RELEVANCE     — Does the retrieved context actually answer the query?
                  5=perfect retrieval, 1=completely off-topic
  COHERENCE     — Is the answer clear, well-structured, and logically consistent?
                  5=excellent clarity, 1=confusing/incoherent

Return ONLY valid JSON, no markdown:
{
  "groundedness": <1-5>,
  "relevance": <1-5>,
  "coherence": <1-5>,
  "reasoning": "<2-3 sentences explaining the scores>"
}"""


@step
async def run_evaluation(payload: EvaluationPayload) -> dict:
    """
    Run LLM-based evaluation using the light eval model (gpt-4o-mini).
    Returns dict with groundedness, relevance, coherence, reasoning.
    """
    # Build context string from sources
    context = "\n\n".join(
        f"[{i+1}] {s.get('title','')}: {s.get('excerpt','')}"
        for i, s in enumerate(payload.sources[:5])
    )

    user_msg = f"""Query: {payload.query}

Context retrieved:
{context}

Answer given:
{payload.answer}

Evaluate the answer."""

    client = get_openai_client()
    resp = await asyncio.to_thread(
        client.chat.completions.create,
        model=settings.AZURE_OPENAI_EVAL_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _EVAL_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0,
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    try:
        scores = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Eval LLM returned non-JSON — using defaults")
        scores = {"groundedness": 3, "relevance": 3, "coherence": 3, "reasoning": "Parse error"}

    # Clamp all scores to 1–5
    for key in ("groundedness", "relevance", "coherence"):
        scores[key] = max(1.0, min(5.0, float(scores.get(key, 3))))

    # Weighted overall: groundedness 40%, relevance 35%, coherence 25%
    scores["overall_score"] = round(
        scores["groundedness"] * 0.40 +
        scores["relevance"]    * 0.35 +
        scores["coherence"]    * 0.25,
        3,
    )
    return scores


@step
async def persist_evaluation(payload: EvaluationPayload, scores: dict) -> EvaluationResult:
    """Build EvaluationResult, store in CosmosDB, emit to App Insights."""
    doc_names = list({s.get("title", "") for s in payload.sources if s.get("title")})

    result = EvaluationResult(
        id                   = payload.answer_id,   # CosmosDB item id = answer_id
        answer_id            = payload.answer_id,
        query                = payload.query,
        answer               = payload.answer,
        domain               = payload.domain,
        doc_names            = doc_names,
        confidence           = payload.confidence,
        attempts_used        = payload.attempts_used,
        processing_time_ms   = payload.processing_time_ms,
        user_id              = payload.user_id,
        conversation_id      = payload.conversation_id,
        groundedness         = scores["groundedness"],
        relevance            = scores["relevance"],
        coherence            = scores["coherence"],
        overall_score        = scores["overall_score"],
        evaluation_reasoning = scores.get("reasoning", ""),
        evaluated_at         = datetime.now(timezone.utc).isoformat(),
        evaluator_model      = settings.AZURE_OPENAI_EVAL_DEPLOYMENT,
    )

    record = result.model_dump()

    # Store in CosmosDB
    await upsert_evaluation(record)

    # Emit to App Insights → Log Analytics
    emit_evaluation(record)

    logger.info(
        "Evaluation complete answer_id=%s overall=%.2f ground=%.1f rel=%.1f coh=%.1f",
        result.answer_id, result.overall_score,
        result.groundedness, result.relevance, result.coherence,
        extra={"answer_id": result.answer_id, "domain": result.domain},
    )
    return result


# ── Workflow ──────────────────────────────────────────────────────────────────

@workflow(name="evaluation_workflow")
async def evaluation_workflow(payload: EvaluationPayload) -> EvaluationResult:
    scores = await run_evaluation(payload)
    return await persist_evaluation(payload, scores)


# ── Service Bus listener ──────────────────────────────────────────────────────

async def _sb_listener():
    logger.info("Evaluation Agent SB listener on queue '%s'",
                settings.AZURE_SERVICE_BUS_QUEUE_EVALUATION)
    from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
    from azure.servicebus.aio import ServiceBusClient as AsyncSBClient

    while True:
        try:
            credential = (
                ManagedIdentityCredential() if os.getenv("RUNNING_IN_AZURE")
                else AzureCliCredential()
            )
            async with AsyncSBClient(
                fully_qualified_namespace=settings.AZURE_SERVICE_BUS_NAMESPACE,
                credential=credential,
            ) as sb:
                async with sb.get_queue_receiver(
                    settings.AZURE_SERVICE_BUS_QUEUE_EVALUATION,
                    max_wait_time=30,
                ) as receiver:
                    async for msg in receiver:
                        try:
                            data    = json.loads(b"".join(msg.body))
                            payload = EvaluationPayload(**data)
                            result_obj = await evaluation_workflow.run(payload)
                            await receiver.complete_message(msg)
                        except Exception as exc:
                            logger.error("Evaluation failed: %s", exc, exc_info=True)
                            await receiver.abandon_message(msg)
        except Exception as exc:
            logger.error("SB listener crashed, retrying in 5s: %s", exc, exc_info=True)
            await asyncio.sleep(5)


# ── FastAPI ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_sb_listener())
    logger.info("Evaluation Agent started — SB listener active.")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    from shared.azure_clients import close_cosmos_client
    await close_cosmos_client()
    logger.info("Evaluation Agent stopped.")


app = FastAPI(title="RAG Evaluation Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "evaluation"}


if __name__ == "__main__":
    uvicorn.run("agents.evaluation_agent:app", host="0.0.0.0", port=8003, reload=False)
