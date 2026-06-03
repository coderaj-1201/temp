# RAG Enterprise — Microsoft Agent Framework

Multi-agent RAG system on Azure using **Microsoft Agent Framework (MAF) v1.0**.

## Architecture

```
Teams Bot (port 3978)
    │  HTTP
    ▼
Main Agent (port 8000)          ← MAF @workflow + @step
    │  HTTP
    ▼
Orchestrator Agent (port 8001)  ← MAF @workflow + @step
    │                                  │  classify query (domain + tool)
    │                                  │  retry loop ≤ 3 times
    │                                  │  tool escalation: hybrid → hyde → decomposition
    │  HTTP (local) / Service Bus (prod)
    ▼
Retrieval Agent (port 8002)     ← MAF @workflow + @step
    │  hybrid_search (BM25 + vector + RRF)
    │  hyde (hypothetical document generation)
    │  decomposition (sub-question fan-out)
    ▼
Azure AI Search (idx-hr / idx-legal / idx-it)
    + Azure OpenAI (synthesis + confidence scoring)
```

**Confidence loop:** If retrieval confidence < 0.75, orchestrator retries with next tool.
After 3 failed attempts, main agent returns failure + two escalation options:
- `raise_ticket` — creates ITSM ticket (ServiceNow stub)
- `connect_sme` — routes to Subject Matter Expert

## Quick Start (Local — no Docker)

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.template .env
# Fill in AZURE_OPENAI_ENDPOINT, AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY

# 3. Start all agents
chmod +x scripts/start_local.sh
./scripts/start_local.sh

# 4. Test
python scripts/test_integration.py
# Or directly:
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"text": "What is the maternity leave policy?"}'
```

## Quick Start (Local — Docker Compose)

```bash
cp .env.template .env  # fill in values
docker compose up --build
```

## Deploy to Azure

```bash
# 1. Fill in infra params
cp infra/main.bicepparam.template infra/main.bicepparam
# Edit main.bicepparam with your OpenAI endpoint + Search key

# 2. Deploy everything (infrastructure + images)
chmod +x scripts/deploy.sh
RESOURCE_GROUP=my-rag-rg ENV=prod ./scripts/deploy.sh
```

The deploy script:
1. Provisions all Azure resources via Bicep (ACR, ACA, AI Search, Service Bus, Key Vault, App Insights)
2. Builds and pushes all 3 agent images to ACR
3. Updates Container Apps with new images

## Project Structure

```
├── agents/
│   ├── main_agent.py          # Entry point, Teams response formatting
│   ├── orchestrator_agent.py  # Classification, retry loop, tool escalation
│   └── retrieval_agent.py     # Tool execution, synthesis, confidence scoring
├── tools/
│   ├── hybrid_search_tool.py  # BM25 + vector + RRF via Azure AI Search
│   ├── hyde_tool.py           # Hypothetical Document Embedding
│   └── query_decomposition_tool.py  # Sub-question decomposition
├── shared/
│   ├── models.py              # Typed dataclasses (message contracts)
│   ├── config.py              # Pydantic settings (env validation)
│   ├── azure_clients.py       # Cached Azure client factories
│   ├── service_bus.py         # Production Service Bus messaging
│   └── logging_config.py     # Structured logging + App Insights
├── infra/
│   └── main.bicep             # Full Azure IaC
├── scripts/
│   ├── deploy.sh              # Build + push + deploy
│   ├── start_local.sh         # Local dev without Docker
│   └── test_integration.py   # End-to-end smoke tests
├── app.py                     # Teams Bot adapter
├── Dockerfile                 # Multi-agent single Dockerfile (build-arg)
├── docker-compose.yml         # Local multi-agent stack
└── requirements.txt
```

## MAF Concepts Used

| Concept | Where used |
|---|---|
| `@workflow` | All 3 agents — converts async function to `FunctionalWorkflow` |
| `@step` | All expensive calls (LLM classify, retrieval, synthesis) — adds caching + observability |
| `WorkflowRunResult.get_outputs()` | HTTP endpoints extract typed outputs |
| Functional control flow (`for` loop, `asyncio.gather`) | Orchestrator retry loop, decomposition fan-out |
| `ManagedIdentityCredential` | Production Azure auth — no stored credentials |

## Switching to Service Bus in Production

Set `RETRIEVAL_MODE=servicebus` in your environment.
The Orchestrator will use `shared/service_bus.py` instead of direct HTTP.
The Retrieval Agent must run a Service Bus listener (add a background task to its lifespan).
