"""
Shared Pydantic models and enums used across all agents.
These are the typed messages that flow through the MAF WorkflowBuilder graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class Domain(StrEnum):
    HR = "hr"
    LEGAL = "legal"
    IT = "it"


class RetrievalTool(StrEnum):
    HYBRID = "hybrid"       # BM25 + dense vector with RRF — default first pass
    HYDE = "hyde"           # Hypothetical Document Embedding — vague / conceptual queries
    DECOMPOSITION = "decomposition"  # Sub-question decomposition — complex / multi-part


class RetrievalStatus(StrEnum):
    SUCCESS = "success"
    RETRY = "retry"
    FAILURE = "failure"


# ── Messages flowing through the workflow graph ──────────────────────────────

@dataclass
class UserQuery:
    """Ingress message from Teams bot."""
    text: str
    conversation_id: str
    user_id: str


@dataclass
class OrchestratorRequest:
    """Issued by Orchestrator → Retrieval after classification."""
    query: str
    domain: Domain
    tool: RetrievalTool
    attempt: int                 # 1-indexed, max 3
    conversation_id: str
    user_id: str


@dataclass
class RetrievalResult:
    """Issued by Retrieval → Orchestrator after a retrieval attempt."""
    query: str
    domain: Domain
    tool: RetrievalTool
    attempt: int
    answer: str
    confidence: float
    sources: list[str]
    conversation_id: str
    user_id: str

    @property
    def passed(self) -> bool:
        from shared.config import settings
        return self.confidence >= settings.CONFIDENCE_THRESHOLD


@dataclass
class FinalResponse:
    """Issued by Orchestrator → Main for delivery to Teams."""
    status: Literal["success", "failure"]
    answer: str                  # populated on success; empty on failure
    domain: Domain | None
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.0
    attempts_used: int = 0
    conversation_id: str = ""
    user_id: str = ""
