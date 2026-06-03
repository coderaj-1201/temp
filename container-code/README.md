# container-code — Production RAG (Service Bus + Keyless + Single Index)

## Key differences from local code

| | `local code` | `container-code` |
|---|---|---|
| Auth | `az login` + Search API key | Fully keyless — Managed Identity only |
| AI Search | 3 separate indexes | 1 index (`idx-rag`) + `domain` OData filter |
| Agent comms | Direct HTTP | Azure Service Bus (durable, async) |
| Token validation | None | Entra ID Bearer token on all `/api/*` routes |
| API contract | `/query` (internal) | `/api/chat`, `/api/feedback`, `/api/telemetry` (frontend contract) |
| Logging | Basic | Structured JSON → Log Analytics via App Insights OTel |
| Workers | 1 | 2 per container |
| Service Bus listener | None | Background asyncio task in Retrieval Agent |

---

## Why one index instead of three?

- Same document schema across domains — no reason for separate indexes
- One semantic configuration maintained once
- Cross-domain queries possible in future
- Domain scoping via OData `$filter=domain eq 'hr'` — zero performance difference
- 3× cheaper on index quota

---

## Setup sequence (first time)

### 1. Assign yourself roles (ask your admin if you only have Contributor)

```bash
USER_ID=$(az ad signed-in-user show --query id -o tsv)

# AI Search — for keyless search
SEARCH_ID=$(az search service show --name <name> --resource-group <rg> --query id -o tsv)
az role assignment create --assignee $USER_ID --role "Search Index Data Contributor" --scope $SEARCH_ID
az role assignment create --assignee $USER_ID --role "Search Index Data Reader" --scope $SEARCH_ID

# Service Bus — for keyless messaging
SB_ID=$(az servicebus namespace show --name <name> --resource-group <rg> --query id -o tsv)
az role assignment create --assignee $USER_ID --role "Azure Service Bus Data Owner" --scope $SB_ID
```

### 2. Configure .env
```bash
cp .env.template .env
# Fill in: AZURE_FOUNDRY_PROJECT_ENDPOINT, AZURE_SEARCH_ENDPOINT,
#          AZURE_SERVICE_BUS_NAMESPACE, AZURE_TENANT_ID, AZURE_CLIENT_ID
```

### 3. Create the search index
```bash
pip install -r requirements.txt
python scripts/create_search_index.py
```

### 4. Test Service Bus connectivity
```bash
python scripts/test_service_bus.py
```

### 5. Start agents (3 terminals)
```bash
python -m uvicorn agents.retrieval_agent:app --port 8002
python -m uvicorn agents.orchestrator_agent:app --port 8001
python -m uvicorn agents.main_agent:app --port 8000
```

---

## Log Analytics — viewing logs locally

If `APPLICATIONINSIGHTS_CONNECTION_STRING` is set in `.env`, logs go to App Insights immediately.

**In Azure Portal → Application Insights → Logs:**
```kusto
// All RAG agent logs last 1 hour
traces
| where timestamp > ago(1h)
| where cloud_RoleName startswith "rag-"
| project timestamp, cloud_RoleName, message, severityLevel
| order by timestamp desc

// Failed retrievals
traces
| where message contains "FAILED" or severityLevel >= 3
| project timestamp, cloud_RoleName, message
```

**In Log Analytics (ACA logs):**
```kusto
ContainerAppConsoleLogs_CL
| where ContainerName_s contains "rag"
| project TimeGenerated, ContainerName_s, Log_s
| order by TimeGenerated desc
```

---

## API contract (matches frontend doc)

### POST /api/chat
```json
{
  "user_id": "user@company.com",
  "query": "What is the annual leave policy?",
  "program": ["all"],
  "source": ["all"],
  "jurisdiction": ["all"],
  "conversation_id": "conv-123"
}
```
Headers: `Authorization: Bearer <teams-token>`

### POST /api/feedback
```json
{"answer_id": "ans-abc123", "user_id": "...", "rating": 4, "is_accurate": true}
```

### POST /api/telemetry
```json
{"event_type": "query_submitted", "user_id": "...", "session_id": "..."}
```
