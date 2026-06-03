"""
test_service_bus.py
===================
Verifies Service Bus connectivity end-to-end:
  1. Sends a test message to rag-inbound
  2. Reads it back from rag-inbound
  3. Sends a test response to rag-outbound
  4. Reads it back from rag-outbound
  5. Checks dead letter queues

Run before deploying containers:
  cd container-code
  python scripts/test_service_bus.py

Auth: az login (requires Azure Service Bus Data Owner role)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from azure.identity import AzureCliCredential

from shared.config import settings


async def test_queue_roundtrip(sb_client: ServiceBusClient, queue_name: str, label: str) -> bool:
    test_id = uuid.uuid4().hex[:8]
    payload = {"test": True, "id": test_id, "label": label}

    print(f"\n  Testing queue: {queue_name}")

    # Send
    async with sb_client.get_queue_sender(queue_name) as sender:
        msg = ServiceBusMessage(
            body=json.dumps(payload),
            message_id=test_id,
            correlation_id=test_id,
        )
        await sender.send_messages(msg)
        print(f"    ✅ Sent message id={test_id}")

    # Receive
    async with sb_client.get_queue_receiver(queue_name, max_wait_time=10) as receiver:
        messages = await receiver.receive_messages(max_message_count=10, max_wait_time=10)
        for msg in messages:
            body = json.loads(b"".join(msg.body))
            if body.get("id") == test_id:
                await receiver.complete_message(msg)
                print(f"    ✅ Received and completed message id={test_id}")
                return True
            else:
                await receiver.abandon_message(msg)

    print(f"    ❌ Did not receive our test message within timeout")
    return False


async def check_dead_letters(sb_client: ServiceBusClient, queue_name: str):
    dlq = f"{queue_name}/$deadletterqueue"
    async with sb_client.get_queue_receiver(dlq, max_wait_time=3) as receiver:
        messages = await receiver.receive_messages(max_message_count=10, max_wait_time=3)
        if messages:
            print(f"    ⚠️  Dead letters in {queue_name}: {len(messages)}")
            for msg in messages:
                print(f"       - id={msg.message_id} reason={msg.dead_letter_reason}")
                await receiver.abandon_message(msg)  # leave in DLQ, don't delete
        else:
            print(f"    ✅ No dead letters in {queue_name}")


async def main():
    print("=" * 55)
    print("Service Bus Connectivity Test")
    print(f"Namespace: {settings.AZURE_SERVICE_BUS_NAMESPACE}")
    print("=" * 55)

    credential = AzureCliCredential()
    async with ServiceBusClient(
        fully_qualified_namespace=settings.AZURE_SERVICE_BUS_NAMESPACE,
        credential=credential,
    ) as sb_client:
        results = []

        results.append(
            await test_queue_roundtrip(sb_client, settings.AZURE_SERVICE_BUS_QUEUE_INBOUND, "inbound")
        )
        results.append(
            await test_queue_roundtrip(sb_client, settings.AZURE_SERVICE_BUS_QUEUE_OUTBOUND, "outbound")
        )

        print("\n  Checking dead letter queues...")
        await check_dead_letters(sb_client, settings.AZURE_SERVICE_BUS_QUEUE_INBOUND)
        await check_dead_letters(sb_client, settings.AZURE_SERVICE_BUS_QUEUE_OUTBOUND)

    print("\n" + "=" * 55)
    if all(results):
        print("✅ All Service Bus tests passed.")
    else:
        print("❌ Some tests failed — check roles and namespace name.")
        print("\nRequired role: 'Azure Service Bus Data Owner' on the namespace")
        print("Assign with:")
        print(f"  USER_ID=$(az ad signed-in-user show --query id -o tsv)")
        print(f"  SB_ID=$(az servicebus namespace show --name <ns> --resource-group <rg> --query id -o tsv)")
        print(f"  az role assignment create --assignee $USER_ID --role 'Azure Service Bus Data Owner' --scope $SB_ID")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
