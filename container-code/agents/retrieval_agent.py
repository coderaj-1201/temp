"""
Retrieval Agent — Production Container Version
===============================================
MAF Functional Workflow (@workflow / @step).

New in this version vs local:
  - Service Bus listener runs as FastAPI background task (lifespan)
  - Consumes from rag-inbound, publishes to rag-outbound
  - Keyless auth (Managed Identity)
  - Single index with domain filter
  - Sources returned in SourceDocument format matching frontend contract
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from agent_framework import step, workflow
from fastapi import FastAPI

from shared.azure_clients import get_service_bus_client
from shared.azure_clients import get_openai_client
from shared.config import settings
from shared.logging_config import configure_logging, get_logger
from shared.models import OrchestratorRequest, RetrievalResult, SourceDocument
from shared.service_bus import send_retrieval_response
from tools.hybrid_search_tool import SearchDocument, fetch_parent_chunk, hybrid_search
from tools.hyde_tool import generate_hypothetical_document
from tools.query_decomposition_tool import decompose_query

configure_logging("rag-retrieval")
logger = get_logger(__name__)


# ── Step functions ────────────────────────────────────────────────────────────

@step
async def run_hybrid(query: str, domain: str) -> list[SearchDocument]:
    return await asyncio.to_thread(hybrid_search, query, domain)


@step
async def run_hyde(query: str, domain: str) -> list[SearchDocument]:
    hypo = await asyncio.to_thread(generate_hypothetical_document, query)
    return await asyncio.to_thread(hybrid_search, hypo, domain)


@step
async def run_decomposition(query: str, domain: str) -> list[SearchDocument]:
    sub_queries = await asyncio.to_thread(decompose_query, query)
    result_sets = await asyncio.gather(
        *[asyncio.to_thread(hybrid_search, sq, domain) for sq in sub_queries]
    )
    seen: dict[str, SearchDocument] = {}
    for docs in result_sets:
        for doc in docs:
            if doc.id not in seen or doc.score > seen[doc.id].score:
                seen[doc.id] = doc
    return sorted(seen.values(), key=lambda d: d.score, reverse=True)[: settings.RETRIEVAL_TOP_K]


_SYNTHESIS_SYSTEM = """You are an enterprise knowledge assistant.
Answer using ONLY the context below. Be concise and cite source names.

After your answer output ONLY this JSON on a new line (no markdown):
{"confidence": <0.0-1.0>}

Confidence: 0.9+ = context fully answers, 0.7-0.89 = mostly answers,
0.5-0.69 = partial, <0.5 = insufficient."""


@step
async def synthesize(query: str, all_docs: list[SearchDocument]) -> tuple[str, float, list[SourceDocument]]:
    if not all_docs:
        return "No relevant information found in the knowledge base.", 0.0, []

    context_parts = []
    for i, d in enumerate(all_docs):
        heading = getattr(d, "section_heading", "")
        page    = getattr(d, "page_number", 0)
        label   = f"[{i+1}] Source: {d.source}" + (f" (p.{page})" if page else "") + (f" | {heading}" if heading else "")
        if getattr(d, "chunk_type", "") == "table" and getattr(d, "table_raw", ""):
            context_parts.append(f"{label}\nSummary: {d.content}\nTable:\n{d.table_raw}")
        else:
            context_parts.append(f"{label}\n{d.content}")
    context = "\n\n".join(context_parts)

    resp = await asyncio.to_thread(
        get_openai_client().chat.completions.create,
        model=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _SYNTHESIS_SYSTEM},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
        ],
        temperature=settings.SYNTHESIS_TEMPERATURE,
        max_tokens=800,
    )

    full_text = resp.choices[0].message.content.strip()
    try:
        split_idx = full_text.rfind("\n{")
        if split_idx == -1:
            split_idx = full_text.rfind('{"confidence"')
        answer = full_text[:split_idx].strip() if split_idx > 0 else full_text
        confidence = float(json.loads(full_text[split_idx:]).get("confidence", 0.0))
    except (json.JSONDecodeError, ValueError):
        answer, confidence = full_text, 0.5

    sources = [
        SourceDocument(
            title=d.source,
            excerpt=d.content[:200],
            url="",
            relevance=round(d.score, 3),
        )
        for d in all_docs[:3]
    ]
    return answer, round(min(max(confidence, 0.0), 1.0), 3), sources


# ── Workflow ──────────────────────────────────────────────────────────────────

@workflow(name="retrieval_workflow")
async def retrieval_workflow(request: OrchestratorRequest) -> RetrievalResult:
    logger.info(
        "retrieval attempt=%d domain=%s tool=%s",
        request.attempt, request.domain, request.tool,
        extra={"conversation_id": request.conversation_id, "attempt": request.attempt},
    )
    match request.tool:
        case "hyde":         docs = await run_hyde(request.query, request.domain)
        case "decomposition": docs = await run_decomposition(request.query, request.domain)
        case _:              docs = await run_hybrid(request.query, request.domain)

    answer, confidence, source_docs = await synthesize(request.query, docs)

    logger.info(
        "retrieval complete confidence=%.3f",
        confidence,
        extra={"confidence": confidence, "domain": request.domain},
    )
    return RetrievalResult(
        query=request.query,
        domain=request.domain,
        tool=request.tool,
        attempt=request.attempt,
        answer=answer,
        confidence=confidence,
        sources=[s.model_dump() for s in source_docs],
        conversation_id=request.conversation_id,
        user_id=request.user_id,
    )


# ── Service Bus listener (background task) ────────────────────────────────────

async def _service_bus_listener():
    """
    Continuously consume from rag-inbound queue.
    For each message: run retrieval workflow → publish result to rag-outbound.
    Runs as a background asyncio task for the lifetime of the process.
    """
    logger.info("Service Bus listener starting on queue '%s'", settings.AZURE_SERVICE_BUS_QUEUE_INBOUND)
    while True:
        try:
            async with get_service_bus_client() as sb_client:
                async with sb_client.get_queue_receiver(
                    settings.AZURE_SERVICE_BUS_QUEUE_INBOUND,
                    max_wait_time=30,
                ) as receiver:
                    async for msg in receiver:
                        correlation_id = msg.correlation_id
                        try:
                            payload = json.loads(b"".join(msg.body))
                            request = OrchestratorRequest(**payload)
                            result_obj = await retrieval_workflow.run(request)
                            outputs = result_obj.get_outputs()
                            result: RetrievalResult = outputs[0]
                            await send_retrieval_response(sb_client, result.__dict__, correlation_id)
                            await receiver.complete_message(msg)
                        except Exception as exc:
                            logger.error("Failed processing SB message: %s", exc, exc_info=True)
                            await receiver.abandon_message(msg)
        except Exception as exc:
            logger.error("Service Bus listener crashed, restarting in 5s: %s", exc, exc_info=True)
            await asyncio.sleep(5)   # backoff before reconnect


# ── FastAPI ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    listener_task = asyncio.create_task(_service_bus_listener())
    logger.info("Retrieval Agent started — SB listener active.")
    yield
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass
    logger.info("Retrieval Agent shut down.")


app = FastAPI(title="RAG Retrieval Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "retrieval"}


@app.get("/admin/dead-letters")
async def dead_letters(queue: str = "rag-inbound") -> dict:
    """Ops endpoint — inspect dead letter queue."""
    from shared.service_bus import check_dead_letter_queue
    msgs = await check_dead_letter_queue(queue)
    return {"queue": queue, "count": len(msgs), "messages": msgs}


if __name__ == "__main__":
    uvicorn.run("agents.retrieval_agent:app", host="0.0.0.0", port=8002, workers=2)
