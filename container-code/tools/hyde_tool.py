"""HyDE — Hypothetical Document Embedding."""
from __future__ import annotations
import logging
from shared.azure_clients import get_openai_client
from shared.config import settings

logger = logging.getLogger(__name__)

def generate_hypothetical_document(query: str) -> str:
    response = get_openai_client().chat.completions.create(
        model=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Write a concise factual passage (3-5 sentences) that directly answers "
                    "the question. Write as if from an internal policy document. No caveats."
                ),
            },
            {"role": "user", "content": f"Question: {query}"},
        ],
        temperature=0.4,
        max_tokens=300,
    )
    return response.choices[0].message.content.strip()
