"""
Azure Service Bus messaging — production grade.

Uses Managed Identity (no connection strings).
Handles:
  - Send with correlation ID
  - Receive with correlation filtering
  - Dead letter queue monitoring
  - Proper message settlement (complete / abandon / dead-letter)

Message flow:
  Orchestrator  → INBOUND queue  → Retrieval agent consumes
  Retrieval     → OUTBOUND queue → Orchestrator correlates by correlation_id
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient

from shared.config import settings

logger = logging.getLogger(__name__)

_RESPONSE_TIMEOUT = 90   # seconds to wait for a retrieval response
_MAX_WAIT_TIME    = 5    # seconds per receive batch poll


async def send_retrieval_request(sb_client: ServiceBusClient, payload: dict, correlation_id: str) -> None:
    async with sb_client.get_queue_sender(settings.AZURE_SERVICE_BUS_QUEUE_INBOUND) as sender:
        msg = ServiceBusMessage(
            body=json.dumps(payload),
            correlation_id=correlation_id,
            content_type="application/json",
            message_id=str(uuid.uuid4()),
        )
        await sender.send_messages(msg)
        logger.debug("SB sent inbound correlation_id=%s", correlation_id)


async def receive_retrieval_response(sb_client: ServiceBusClient, correlation_id: str) -> dict:
    """
    Poll the outbound queue for a message matching our correlation_id.
    Messages not matching are abandoned (left for other consumers / next poll).
    Raises asyncio.TimeoutError after _RESPONSE_TIMEOUT seconds.
    """
    deadline = asyncio.get_event_loop().time() + _RESPONSE_TIMEOUT

    async with sb_client.get_queue_receiver(
        settings.AZURE_SERVICE_BUS_QUEUE_OUTBOUND,
        max_wait_time=_MAX_WAIT_TIME,
    ) as receiver:
        while asyncio.get_event_loop().time() < deadline:
            messages = await receiver.receive_messages(max_message_count=10, max_wait_time=_MAX_WAIT_TIME)
            for msg in messages:
                if msg.correlation_id == correlation_id:
                    data = json.loads(b"".join(msg.body))
                    await receiver.complete_message(msg)
                    logger.debug("SB received outbound correlation_id=%s", correlation_id)
                    return data
                else:
                    # Not ours — put back for the rightful consumer
                    await receiver.abandon_message(msg)

    raise asyncio.TimeoutError(
        f"No SB response within {_RESPONSE_TIMEOUT}s for correlation_id={correlation_id}"
    )


async def send_retrieval_response(sb_client: ServiceBusClient, payload: dict, correlation_id: str) -> None:
    async with sb_client.get_queue_sender(settings.AZURE_SERVICE_BUS_QUEUE_OUTBOUND) as sender:
        msg = ServiceBusMessage(
            body=json.dumps(payload),
            correlation_id=correlation_id,
            content_type="application/json",
            message_id=str(uuid.uuid4()),
        )
        await sender.send_messages(msg)
        logger.debug("SB sent outbound response correlation_id=%s", correlation_id)


async def check_dead_letter_queue(queue_name: str) -> list[dict]:
    """
    Drain and return messages from a queue's dead letter sub-queue.
    Call this from a monitoring endpoint or scheduled job.
    """
    from shared.azure_clients import get_service_bus_client
    dlq_name = f"{queue_name}/$deadletterqueue"
    dead_letters = []
    async with get_service_bus_client() as sb_client:
        async with sb_client.get_queue_receiver(dlq_name, max_wait_time=5) as receiver:
            messages = await receiver.receive_messages(max_message_count=50, max_wait_time=5)
            for msg in messages:
                dead_letters.append({
                    "message_id": msg.message_id,
                    "correlation_id": msg.correlation_id,
                    "dead_letter_reason": msg.dead_letter_reason,
                    "body": json.loads(b"".join(msg.body)),
                })
                await receiver.complete_message(msg)  # remove from DLQ after inspection
    return dead_letters
