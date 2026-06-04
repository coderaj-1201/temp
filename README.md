# RAG Retrieval Pipeline — Local Dev

Same code as the ACA production pipeline. Only differences:
- **Auth**: `AzureCliCredential` instead of Managed Identity — run `az login` once
- **JWT**: bypassed — no Entra app registration needed, every request authenticates as `dev@local`
- **Logging**: human-readable stdout instead of JSON

All Azure resources (Service Bus, CosmosDB, AI Search, Foundry) are real.

---

## Prerequisites

```bash
pip install azure-cli           # or: winget install Microsoft.AzureCLI
az login
az account set --subscription <your-subscription-id>
```

Assign yourself these roles (one-time, replace placeholders):
```bash
# AI Search — needed for keyless search
az role assignment create \
  --role "Search Index Data Reader" \
  --assignee $(az ad signed-in-user show --query id -o tsv) \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Search/searchServices/<search-name>

# CosmosDB — needed for eval/feedback storage
az cosmosdb sql role assignment create \
  --account-name <cosmos-account> --resource-group <rg> \
  --role-definition-name "Cosmos DB Built-in Data Contributor" \
  --principal-id $(az ad signed-in-user show --query id -o tsv) \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.DocumentDB/databaseAccounts/<cosmos-account>

# Service Bus — needed for queue send/receive
az role assignment create \
  --role "Azure Service Bus Data Owner" \
  --assignee $(az ad signed-in-user show --query id -o tsv) \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ServiceBus/namespaces/<sb-namespace>
```

> **Note**: rag-inbound and rag-outbound Service Bus queues must have **Sessions enabled**.

---

## Setup

```bash
cd container-code
cp ../.env.template .env
# Fill in .env — see comments inside
pip install -r requirements.txt
```

---

## Running (5 terminals)

Each agent is a standalone FastAPI process. Open 5 terminals, all from `container-code/`:

```bash
# Terminal 1 — Main Agent (public-facing API)
uvicorn agents.main_agent:app --port 8000 --reload

# Terminal 2 — Orchestrator Agent
uvicorn agents.orchestrator_agent:app --port 8001 --reload

# Terminal 3 — Retrieval Agent
uvicorn agents.retrieval_agent:app --port 8002 --reload

# Terminal 4 — Evaluation Agent
uvicorn agents.evaluation_agent:app --port 8003 --reload

# Terminal 5 — Feedback Agent
uvicorn agents.feedback_agent:app --port 8004 --reload
```

---

## Test

```bash
# Health check all agents
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/health

# Query (no Authorization header needed locally)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "dev-user",
    "query": "What is the leave policy?",
    "conversation_id": "test-001"
  }'

# Feedback
curl -X POST http://localhost:8000/api/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "answer_id": "<answer_id from chat response>",
    "user_id": "dev-user",
    "rating": 4
  }'
```

---

## Swagger UI

http://localhost:8000/docs — full interactive API docs for the main agent.
