"""
Teams Bot Adapter
=================
Bridges the Microsoft Bot Framework (Teams) to the Main Agent HTTP endpoint.

This file runs as a separate process or can be collocated with main_agent.
For local testing, it starts on port 3978 (Bot Framework default).

Production: deploy behind Azure Bot Service + configure Teams channel.
"""
from __future__ import annotations

import json
import logging
import os
import uuid

import httpx
import uvicorn
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.schema import Activity, ActivityTypes
from fastapi import FastAPI, Request, Response

from shared.config import settings
from shared.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

_MAIN_AGENT_URL = os.getenv("MAIN_AGENT_URL", "http://localhost:8000")

# Bot Framework adapter
_adapter_settings = BotFrameworkAdapterSettings(
    app_id=settings.TEAMS_APP_ID or "",
    app_password=settings.TEAMS_APP_PASSWORD.get_secret_value()
    if settings.TEAMS_APP_PASSWORD
    else "",
)
adapter = BotFrameworkAdapter(_adapter_settings)


async def _on_error(context: TurnContext, error: Exception):
    logger.error("BotFrameworkAdapter error: %s", error, exc_info=True)
    await context.send_activity("Sorry, an internal error occurred. Please try again.")


adapter.on_turn_error = _on_error


class RAGBot:
    async def on_turn(self, turn_context: TurnContext) -> None:
        if turn_context.activity.type != ActivityTypes.message:
            return

        user_text: str = (turn_context.activity.text or "").strip()
        if not user_text:
            return

        user_id = turn_context.activity.from_property.id or "anonymous"
        conversation_id = turn_context.activity.conversation.id or str(uuid.uuid4())

        logger.info("Teams message from user_id=%s: '%s'", user_id, user_text[:80])

        # Show typing indicator
        await turn_context.send_activity(Activity(type="typing"))

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{_MAIN_AGENT_URL}/query",
                    json={
                        "text": user_text,
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                    },
                )
                resp.raise_for_status()
                reply: str = resp.json().get("reply", "No response received.")
        except Exception as exc:
            logger.error("Main agent call failed: %s", exc, exc_info=True)
            reply = "⚠️ The knowledge service is temporarily unavailable. Please try again."

        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, text=reply)
        )


bot = RAGBot()
app = FastAPI(title="RAG Teams Bot")


@app.post("/api/messages")
async def messages(req: Request) -> Response:
    """Bot Framework messages endpoint. Must be publicly reachable by Azure Bot Service."""
    if "application/json" not in req.headers.get("Content-Type", ""):
        return Response(status_code=415)

    body = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    try:
        await adapter.process_activity(activity, auth_header, bot.on_turn)
        return Response(status_code=200)
    except Exception as exc:
        logger.error("Error processing activity: %s", exc, exc_info=True)
        return Response(status_code=500)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agent": "teams-bot"}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=3978, reload=False)
