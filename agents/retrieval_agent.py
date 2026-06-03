"""
Retrieval Agent
===============
Microsoft Agent Framework — Functional Workflow API (@workflow / @step).

Responsibilities:
  - Receive an OrchestratorRequest
  - Execute the specified retrieval tool (hybrid / HyDE / decomposition)
  - Synthesize an answer via Azure OpenAI with a self-reported confidence score
  - Return a RetrievalResult

Hosted as a standalone FastAPI service; consumed via Azure Service Bus by the
Orchestrator agent (or directly for local integration testing).
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import uvicorn
from agent_framework import step, workflow
from fastapi import FastAPI, Request, Response

from shared.azure_clients import get_openai_client
from shared.config import settings
from shared.logging_config import configure_logging, get_logger
from shared.models import OrchestratorRequest, RetrievalResult
from tools.hybrid_search_tool import SearchDocument, hybrid_search
from tools.hyde_tool import generate_hypothetical_document
from tools.query_decomposition_tool import decompose_query

configure_logging()
logger = get_logger(__name__)

# ── Step functions (cached across HITL resumes / retries) ────────────────────

@step
async def run_hybrid(query: str, domain: str) -> list[SearchDocument]:
    return await asyncio.to_thread(hybrid_search, query, domain)


@step
async def run_hyde(query: str, domain: str) -> list[SearchDocument]:
    hypothetical = await asyncio.to_thread(generate_hypothetical_document, query)
    return await asyncio.to_thread(hybrid_search, hypothetical, domain)


@step
async def run_decomposition(query: str, domain: str) -> list[SearchDocument]:
    sub_queries = await asyncio.to_thread(decompose_query, query)
    # Fan-out: retrieve for each sub-question in parallel
    result_sets = await asyncio.gather(
        *[asyncio.to_thread(hybrid_search, sq, domain) for sq in sub_queries]
    )
    # Merge and deduplicate by id, preserving highest score
    seen: dict[str, SearchDocument] = {}
    for docs in result_sets:
        for doc in docs:
            if doc.id not in seen or doc.score > seen[doc.id].score:
                seen[doc.id] = doc
    merged = sorted(seen.values(), key=lambda d: d.score, reverse=True)
    return merged[: settings.RETRIEVAL_TOP_K]


_SYNTHESIS_SYSTEM = """You are an enterprise knowledge assistant.
Answer the question using ONLY the provided context passages.
Be concise and factual. Cite source names when possible.

After your answer, on a NEW LINE output ONLY this JSON object (no markdown):
{"confidence": <float 0.0-1.0>}

Confidence scoring guide:
  0.9-1.0 — context directly and completely answers the question
  0.7-0.89 — context largely answers the question with minor gaps
  0.5-0.69 — context partially answers the question
  0.0-0.49 — context is insufficient or tangential"""


@step
async def synthesize_answer(query: str, docs: list[SearchDocument]) -> tuple[str, float]:
    """
    Returns (answer_text, confidence_score).
    Splits the LLM response on the last newline to extract the JSON blob.
    """
    if not docs:
        return "I could not find any relevant information in the knowledge base.", 0.0

    context_blocks = "\n\n".join(
        f"[{i+1}] Source: {d.source}\n{d.content}" for i, d in enumerate(docs)
    )
    client = get_openai_client()
    response = await asyncio.to_thread(
        client.chat.completions.create,
        model=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _SYNTHESIS_SYSTEM},
            {
                "role": "user",
                "content": f"Context:\n{context_blocks}\n\nQuestion: {query}",
            },
        ],
        temperature=settings.SYNTHESIS_TEMPERATURE,
        max_tokens=800,
    )

    full_text: str = response.choices[0].message.content.strip()

    # Split answer from the trailing JSON confidence line
    try:
        split_idx = full_text.rfind("\n{")
        if split_idx == -1:
            split_idx = full_text.rfind("{\"confidence\"")
        answer_text = full_text[:split_idx].strip() if split_idx > 0 else full_text
        json_part = full_text[split_idx:].strip() if split_idx > 0 else "{}"
        confidence = float(json.loads(json_part).get("confidence", 0.0))
    except (json.JSONDecodeError, ValueError, KeyError):
        logger.warning("Failed to parse confidence JSON from synthesis output; defaulting to 0.5")
        answer_text = full_text
        confidence = 0.5

    return answer_text, round(min(max(confidence, 0.0), 1.0), 3)


# ── Functional Workflow ───────────────────────────────────────────────────────

@workflow(name="retrieval_workflow")
async def retrieval_workflow(request: OrchestratorRequest) -> RetrievalResult:
    """
    MAF Functional Workflow that wires tool selection → retrieval → synthesis.
    The @step decorators ensure results are cached if this workflow is resumed.
    """
    logger.info(
        "retrieval_workflow attempt=%d domain=%s tool=%s query='%s'",
        request.attempt,
        request.domain,
        request.tool,
        request.query[:80],
    )

    # Step 1: Retrieve documents using the specified tool
    match request.tool:
        case "hyde":
            docs = await run_hyde(request.query, request.domain)
        case "decomposition":
            docs = await run_decomposition(request.query, request.domain)
        case _:  # hybrid (default)
            docs = await run_hybrid(request.query, request.domain)

    # Step 2: Synthesize answer + confidence
    answer, confidence = await synthesize_answer(request.query, docs)

    sources = list({d.source for d in docs[:3]})

    logger.info(
        "retrieval_workflow complete confidence=%.3f sources=%s",
        confidence,
        sources,
    )

    return RetrievalResult(
        query=request.query,
        domain=request.domain,
        tool=request.tool,
        attempt=request.attempt,
        answer=answer,
        confidence=confidence,
        sources=sources,
        conversation_id=request.conversation_id,
        user_id=request.user_id,
    )


# ── FastAPI host (HTTP interface for local dev + ACA health probe) ────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Retrieval Agent starting up.")
    yield
    logger.info("Retrieval Agent shutting down.")


app = FastAPI(title="RAG Retrieval Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "retrieval"}


@app.post("/retrieve")
async def retrieve(raw: Request) -> Response:
    """HTTP endpoint for direct calls (local testing / Orchestrator HTTP mode)."""
    body = await raw.json()
    request = OrchestratorRequest(**body)
    result = await retrieval_workflow.run(request)
    # result.get_outputs() returns list; first element is our RetrievalResult dataclass
    outputs = result.get_outputs()
    retrieval_result: RetrievalResult = outputs[0] if outputs else RetrievalResult(
        query=request.query,
        domain=request.domain,
        tool=request.tool,
        attempt=request.attempt,
        answer="Internal workflow error.",
        confidence=0.0,
        sources=[],
        conversation_id=request.conversation_id,
        user_id=request.user_id,
    )
    return Response(
        content=json.dumps(retrieval_result.__dict__),
        media_type="application/json",
    )


if __name__ == "__main__":
    uvicorn.run("agents.retrieval_agent:app", host="0.0.0.0", port=8002, reload=False)
