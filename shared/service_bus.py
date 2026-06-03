"""
Azure Service Bus helper.
Used by the Orchestrator Agent in production mode to communicate with
the Retrieval Agent asynchronously and durably.

Message flow:
  Orchestrator → sb://rag-inbound  → Retrieval Agent listens
  Retrieval    → sb://rag-outbound → Orchestrator listens (correlation by message_id)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage

from shared.config import settings
from shared.models import OrchestratorRequest, RetrievalResult

logger = logging.getLogger(__name__)

_RESPONSE_TIMEOUT_SECONDS = 90
_POLL_INTERVAL_SECONDS = 0.5


async def send_and_receive_retrieval(req: OrchestratorRequest) -> RetrievalResult:
    """
    Send OrchestratorRequest to Service Bus inbound queue.
    Poll outbound queue for matching correlation_id response.
    Raises asyncio.TimeoutError if no response within timeout.
    """
    correlation_id = str(uuid.uuid4())
    conn_str = settings.AZURE_SERVICE_BUS_CONNECTION_STR.get_secret_value()

    async with ServiceBusClient.from_connection_string(conn_str) as sb_client:
        # Send request
        async with sb_client.get_queue_sender(settings.AZURE_SERVICE_BUS_QUEUE_INBOUND) as sender:
            message = ServiceBusMessage(
                body=json.dumps(req.__dict__),
                correlation_id=correlation_id,
                content_type="application/json",
            )
            await sender.send_messages(message)
            logger.debug("Sent retrieval request correlation_id=%s", correlation_id)

        # Poll for response
        async with sb_client.get_queue_receiver(
            settings.AZURE_SERVICE_BUS_QUEUE_OUTBOUND,
            max_wait_time=_RESPONSE_TIMEOUT_SECONDS,
        ) as receiver:
            deadline = asyncio.get_event_loop().time() + _RESPONSE_TIMEOUT_SECONDS
            async for msg in receiver:
                if msg.correlation_id == correlation_id:
                    data = json.loads(str(msg))
                    await receiver.complete_message(msg)
                    logger.debug("Received retrieval response correlation_id=%s", correlation_id)
                    return RetrievalResult(**data)
                else:
                    # Not our message — abandon so another consumer can pick it up
                    await receiver.abandon_message(msg)

                if asyncio.get_event_loop().time() > deadline:
                    break

    raise asyncio.TimeoutError(
        f"No retrieval response received within {_RESPONSE_TIMEOUT_SECONDS}s "
        f"for correlation_id={correlation_id}"
    )
