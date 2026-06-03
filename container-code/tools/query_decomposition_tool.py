"""Query Decomposition — splits complex queries into sub-questions."""
from __future__ import annotations
import json
import logging
from shared.azure_clients import get_openai_client
from shared.config import settings

logger = logging.getLogger(__name__)

_SYSTEM = """Decompose the question into 2-4 simple sub-questions.
Return ONLY a JSON array of strings. No markdown, no explanation."""

def decompose_query(query: str) -> list[str]:
    try:
        resp = get_openai_client().chat.completions.create(
            model=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Question: {query}"},
            ],
            temperature=0,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        raw = json.loads(resp.choices[0].message.content)
        if isinstance(raw, list):
            return raw or [query]
        return next((v for v in raw.values() if isinstance(v, list)), [query])
    except Exception as exc:
        logger.warning("Decomposition failed: %s — using original query", exc)
        return [query]
