"""
setup_cosmos.py
===============
Creates the CosmosDB database and containers for the RAG analytics pipeline.

Run once before deploying:
  cd container-code
  python scripts/setup_cosmos.py

Auth: az login (Contributor access is enough)

Containers created:
  evaluations  — partition key /domain — stores LLM evaluation scores per answer
  feedback     — partition key /domain — stores user feedback (thumbs up/down)
"""
from __future__ import annotations

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from azure.cosmos.aio import CosmosClient
from azure.cosmos import PartitionKey
from azure.identity.aio import AzureCliCredential

from shared.config import settings


async def main():
    print(f"\n▶ Connecting to CosmosDB: {settings.AZURE_COSMOS_ENDPOINT}")

    async with CosmosClient(
        url=str(settings.AZURE_COSMOS_ENDPOINT),
        credential=AzureCliCredential(),
    ) as client:

        # Create database
        try:
            db = await client.create_database(settings.AZURE_COSMOS_DATABASE)
            print(f"  ✅ Created database: {settings.AZURE_COSMOS_DATABASE}")
        except Exception as e:
            if "409" in str(e) or "already exists" in str(e).lower():
                db = client.get_database_client(settings.AZURE_COSMOS_DATABASE)
                print(f"  ✓  Database already exists: {settings.AZURE_COSMOS_DATABASE}")
            else:
                print(f"  ❌ Failed to create database: {e}")
                raise

        # Create evaluations container
        for container_name in [
            settings.AZURE_COSMOS_CONTAINER_EVALUATIONS,
            settings.AZURE_COSMOS_CONTAINER_FEEDBACK,
        ]:
            try:
                await db.create_container(
                    id=container_name,
                    partition_key=PartitionKey(path="/domain"),
                    offer_throughput=400,   # minimum — scale up in prod
                )
                print(f"  ✅ Created container: {container_name} (partition: /domain)")
            except Exception as e:
                if "409" in str(e) or "already exists" in str(e).lower():
                    print(f"  ✓  Container already exists: {container_name}")
                else:
                    print(f"  ❌ Failed: {container_name} — {e}")

    print("\n✅ CosmosDB setup complete.")
    print(f"   Database   : {settings.AZURE_COSMOS_DATABASE}")
    print(f"   Containers : {settings.AZURE_COSMOS_CONTAINER_EVALUATIONS}, {settings.AZURE_COSMOS_CONTAINER_FEEDBACK}")


if __name__ == "__main__":
    asyncio.run(main())
