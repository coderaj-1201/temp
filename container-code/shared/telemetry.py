"""
App Insights telemetry helper.
Emits custom events and metrics that appear in Log Analytics.

Log Analytics queries (Portal → Log Analytics → Logs):

── Average RAG scores by domain (last 7 days) ────────────────────────────────
customEvents
| where name == "rag_evaluation"
| where timestamp > ago(7d)
| extend domain          = tostring(customDimensions.domain)
| extend overall_score   = todouble(customDimensions.overall_score)
| extend groundedness    = todouble(customDimensions.groundedness)
| extend relevance       = todouble(customDimensions.relevance)
| extend coherence       = todouble(customDimensions.coherence)
| summarize
    avg_overall    = avg(overall_score),
    avg_ground     = avg(groundedness),
    avg_relevance  = avg(relevance),
    avg_coherence  = avg(coherence),
    total_queries  = count()
  by domain
| order by avg_overall desc

── Feedback ratings trend ────────────────────────────────────────────────────
customEvents
| where name == "rag_feedback"
| where timestamp > ago(30d)
| extend domain  = tostring(customDimensions.domain)
| extend rating  = toint(customDimensions.rating)
| summarize avg_rating = avg(rating), count = count()
  by domain, bin(timestamp, 1d)
| order by timestamp desc

── Low confidence answers (potential gaps in knowledge base) ─────────────────
customEvents
| where name == "rag_evaluation"
| extend confidence   = todouble(customDimensions.confidence)
| extend domain       = tostring(customDimensions.domain)
| extend query        = tostring(customDimensions.query)
| where confidence < 0.6
| project timestamp, domain, query, confidence
| order by timestamp desc

── P95 latency by domain ─────────────────────────────────────────────────────
customEvents
| where name == "rag_evaluation"
| extend latency_ms = toint(customDimensions.processing_time_ms)
| extend domain     = tostring(customDimensions.domain)
| summarize p95_latency = percentile(latency_ms, 95) by domain
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ai_client = None   # opencensus / azure-monitor telemetry client


def _get_telemetry_client():
    """Lazy init — only available when App Insights connection string is set."""
    global _ai_client
    if _ai_client is not None:
        return _ai_client
    try:
        from applicationinsights import TelemetryClient
        from shared.config import settings
        if settings.APPLICATIONINSIGHTS_CONNECTION_STRING:
            # Extract instrumentation key from connection string
            conn = settings.APPLICATIONINSIGHTS_CONNECTION_STRING
            ikey = next(
                (part.split("=", 1)[1] for part in conn.split(";")
                 if part.startswith("InstrumentationKey")),
                None,
            )
            if ikey:
                _ai_client = TelemetryClient(ikey)
                return _ai_client
    except ImportError:
        pass
    return None


def emit_evaluation(record: dict) -> None:
    """
    Emit evaluation result as a custom App Insights event.
    Appears in Log Analytics as customEvents where name == 'rag_evaluation'.
    """
    client = _get_telemetry_client()
    if not client:
        return

    properties = {
        "answer_id":           record.get("answer_id", ""),
        "domain":              record.get("domain", ""),
        "query":               record.get("query", "")[:200],   # truncate for storage
        "groundedness":        str(record.get("groundedness", 0)),
        "relevance":           str(record.get("relevance", 0)),
        "coherence":           str(record.get("coherence", 0)),
        "overall_score":       str(record.get("overall_score", 0)),
        "confidence":          str(record.get("confidence", 0)),
        "attempts_used":       str(record.get("attempts_used", 0)),
        "processing_time_ms":  str(record.get("processing_time_ms", 0)),
        "evaluator_model":     record.get("evaluator_model", ""),
    }
    measurements = {
        "groundedness":       float(record.get("groundedness", 0)),
        "relevance":          float(record.get("relevance", 0)),
        "coherence":          float(record.get("coherence", 0)),
        "overall_score":      float(record.get("overall_score", 0)),
        "confidence":         float(record.get("confidence", 0)),
        "processing_time_ms": float(record.get("processing_time_ms", 0)),
    }
    try:
        client.track_event("rag_evaluation", properties, measurements)
        client.flush()
        logger.debug("Emitted rag_evaluation to App Insights answer_id=%s", record.get("answer_id"))
    except Exception as exc:
        logger.warning("Failed to emit evaluation telemetry: %s", exc)


def emit_feedback(record: dict) -> None:
    """
    Emit feedback as a custom App Insights event.
    Appears in Log Analytics as customEvents where name == 'rag_feedback'.
    """
    client = _get_telemetry_client()
    if not client:
        return

    properties = {
        "feedback_id":  record.get("feedback_id", ""),
        "answer_id":    record.get("answer_id", ""),
        "domain":       record.get("domain", ""),
        "user_id":      record.get("user_id", ""),
        "rating":       str(record.get("rating", 0)),
        "is_accurate":  str(record.get("is_accurate", True)),
        "is_complete":  str(record.get("is_complete", True)),
    }
    measurements = {
        "rating": float(record.get("rating", 0)),
    }
    try:
        client.track_event("rag_feedback", properties, measurements)
        client.flush()
        logger.debug("Emitted rag_feedback to App Insights feedback_id=%s", record.get("feedback_id"))
    except Exception as exc:
        logger.warning("Failed to emit feedback telemetry: %s", exc)
