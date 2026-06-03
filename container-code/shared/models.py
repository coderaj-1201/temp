"""
Shared Pydantic models.
API contracts match the frontend integration doc exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Domain / Tool enums ───────────────────────────────────────────────────────

class Domain(StrEnum):
    HR    = "hr"
    LEGAL = "legal"
    IT    = "it"


class RetrievalTool(StrEnum):
    HYBRID       = "hybrid"
    HYDE         = "hyde"
    DECOMPOSITION = "decomposition"


# ── Internal workflow messages (dataclasses — fast, no serialisation overhead) ─

@dataclass
class UserQuery:
    text: str
    conversation_id: str
    user_id: str
    user_email: str = ""
    # Frontend filters (passed through, not used in RAG yet)
    program: list[str] = field(default_factory=lambda: ["all"])
    source: list[str]  = field(default_factory=lambda: ["all"])
    jurisdiction: list[str] = field(default_factory=lambda: ["all"])


@dataclass
class OrchestratorRequest:
    query: str
    domain: Domain
    tool: RetrievalTool
    attempt: int
    conversation_id: str
    user_id: str


@dataclass
class RetrievalResult:
    query: str
    domain: Domain
    tool: RetrievalTool
    attempt: int
    answer: str
    confidence: float
    sources: list[dict]   # {title, excerpt, url, relevance}
    conversation_id: str
    user_id: str

    @property
    def passed(self) -> bool:
        from shared.config import settings
        return self.confidence >= settings.CONFIDENCE_THRESHOLD


@dataclass
class FinalResponse:
    status: str           # "success" | "failure"
    answer: str
    domain: Domain | None
    sources: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    attempts_used: int = 0
    conversation_id: str = ""
    user_id: str = ""
    answer_id: str = field(default_factory=lambda: f"ans-{uuid4().hex[:8]}")
    processing_time_ms: int = 0


# ── External API models (Pydantic — validated at HTTP boundary) ───────────────

class ChatRequest(BaseModel):
    """Matches frontend POST /api/chat exactly."""
    user_id: str
    query: str
    program: list[str]      = Field(default_factory=lambda: ["all"])
    source: list[str]       = Field(default_factory=lambda: ["all"])
    jurisdiction: list[str] = Field(default_factory=lambda: ["all"])
    conversation_id: str    = Field(default_factory=lambda: str(uuid4()))


class SourceDocument(BaseModel):
    title: str
    excerpt: str
    url: str = ""
    relevance: float = 0.0


class ChatResponse(BaseModel):
    """Matches frontend expected response shape."""
    answer: str
    source_documents: list[SourceDocument]
    confidence: float
    answer_id: str
    processing_time_ms: int


class FeedbackRequest(BaseModel):
    answer_id: str
    user_id: str
    rating: int = Field(ge=1, le=5)
    feedback: str = ""
    is_accurate: bool = True
    is_complete: bool = True


class TelemetryRequest(BaseModel):
    event_type: str
    user_id: str
    session_id: str = ""
    timestamp: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Evaluation models ─────────────────────────────────────────────────────────

class EvaluationPayload(BaseModel):
    """
    Sent to the async evaluation queue after every successful answer.
    Contains everything the Evaluation Agent needs — no DB lookups required.
    """
    answer_id: str
    query: str
    answer: str
    domain: str
    sources: list[dict]           # [{title, excerpt, url, relevance}]
    confidence: float
    attempts_used: int
    processing_time_ms: int
    user_id: str
    conversation_id: str
    evaluated_at: str = ""        # filled by eval agent


class EvaluationResult(BaseModel):
    """Stored in CosmosDB evaluations container + emitted to App Insights."""
    id: str                       # = answer_id (CosmosDB item id)
    answer_id: str
    query: str
    answer: str
    domain: str
    doc_names: list[str]
    confidence: float
    attempts_used: int
    processing_time_ms: int
    user_id: str
    conversation_id: str
    # ── Evaluation scores (1–5) ───────────────────────────────────────────────
    groundedness: float           # is the answer supported by the retrieved context?
    relevance: float              # did retrieval return the right chunks?
    coherence: float              # is the answer well-formed and clear?
    overall_score: float          # weighted average
    evaluation_reasoning: str     # short LLM explanation of the scores
    evaluated_at: str
    evaluator_model: str


class FeedbackRecord(BaseModel):
    """Stored in CosmosDB feedback container."""
    id: str                       # = feedback_id
    feedback_id: str
    answer_id: str
    user_id: str
    domain: str
    rating: int                   # 1–5
    is_accurate: bool
    is_complete: bool
    comment: str
    submitted_at: str
