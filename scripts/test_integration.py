"""
Integration smoke test — runs against live local agents.
Requires all three agents to be running (start_local.sh or docker-compose up).

Usage:
    python scripts/test_integration.py
"""
from __future__ import annotations

import asyncio
import sys

import httpx

MAIN_AGENT_URL = "http://localhost:8000"
ORCHESTRATOR_URL = "http://localhost:8001"
RETRIEVAL_URL = "http://localhost:8002"

TEST_QUERIES = [
    ("What is the annual leave policy for full-time employees?", "hr"),
    ("How do I reset my VPN credentials?", "it"),
    ("What are the GDPR data retention obligations for employee records?", "legal"),
    ("raise_ticket", None),
    ("connect_sme", None),
]


async def check_health(client: httpx.AsyncClient, url: str, name: str) -> bool:
    try:
        resp = await client.get(f"{url}/health", timeout=5.0)
        resp.raise_for_status()
        print(f"  ✅ {name}: {resp.json()}")
        return True
    except Exception as exc:
        print(f"  ❌ {name}: {exc}")
        return False


async def run_query(client: httpx.AsyncClient, text: str) -> dict:
    resp = await client.post(
        f"{MAIN_AGENT_URL}/query",
        json={"text": text, "conversation_id": "test-conv-001", "user_id": "test-user"},
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()


async def main():
    print("=" * 60)
    print("RAG Enterprise — Integration Smoke Test")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        print("\n── Health Checks ──────────────────────────────────────────")
        results = await asyncio.gather(
            check_health(client, RETRIEVAL_URL, "Retrieval Agent"),
            check_health(client, ORCHESTRATOR_URL, "Orchestrator Agent"),
            check_health(client, MAIN_AGENT_URL, "Main Agent"),
        )
        if not all(results):
            print("\nERROR: One or more agents are not healthy. Start them first.")
            sys.exit(1)

        print("\n── Query Tests ────────────────────────────────────────────")
        for query, expected_domain in TEST_QUERIES:
            print(f"\nQ: {query[:70]}")
            try:
                result = await run_query(client, query)
                reply: str = result.get("reply", "")
                print(f"A: {reply[:200]}{'...' if len(reply) > 200 else ''}")
                print("   ✅ OK")
            except Exception as exc:
                print(f"   ❌ FAILED: {exc}")

    print("\n" + "=" * 60)
    print("Smoke test complete.")


if __name__ == "__main__":
    asyncio.run(main())
