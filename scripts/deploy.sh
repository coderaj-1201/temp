#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — One-shot build + push + Azure deploy
#
# Prerequisites:
#   az login
#   docker (running)
#   jq
#
# Usage:
#   chmod +x scripts/deploy.sh
#   RESOURCE_GROUP=my-rg ENV=prod ./scripts/deploy.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config (override via env vars) ───────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:?Set RESOURCE_GROUP}"
ENV="${ENV:-prod}"
LOCATION="${LOCATION:-eastus}"
BICEP_FILE="infra/main.bicep"
BICEP_PARAMS="infra/main.bicepparam"

# ── Ensure logged in ──────────────────────────────────────────────────────────
echo "▶ Verifying Azure CLI login..."
az account show --output none || { echo "Run 'az login' first."; exit 1; }

# ── Create resource group if needed ──────────────────────────────────────────
echo "▶ Ensuring resource group '$RESOURCE_GROUP' in '$LOCATION'..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

# ── Deploy infrastructure (Bicep) ────────────────────────────────────────────
echo "▶ Deploying infrastructure via Bicep..."
DEPLOY_OUTPUT=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$BICEP_FILE" \
  --parameters "@${BICEP_PARAMS}" \
  --name "rag-deploy-$(date +%Y%m%d%H%M%S)" \
  --output json)

ACR_LOGIN_SERVER=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.acrLoginServer.value')
echo "  ACR: $ACR_LOGIN_SERVER"

# ── Login to ACR ──────────────────────────────────────────────────────────────
echo "▶ Logging into ACR '$ACR_LOGIN_SERVER'..."
az acr login --name "${ACR_LOGIN_SERVER%%.*}"

# ── Build & push each agent image ────────────────────────────────────────────
declare -A AGENTS=(
  ["rag-retrieval-agent"]="agents.retrieval_agent:8002"
  ["rag-orchestrator-agent"]="agents.orchestrator_agent:8001"
  ["rag-main-agent"]="agents.main_agent:8000"
  ["rag-teams-bot"]="app:3978"
)

for IMAGE_NAME in "${!AGENTS[@]}"; do
  IFS=':' read -r MODULE PORT <<< "${AGENTS[$IMAGE_NAME]}"
  FULL_TAG="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:latest"

  echo "▶ Building $IMAGE_NAME (module=$MODULE port=$PORT)..."
  docker build \
    --build-arg AGENT_MODULE="$MODULE" \
    --build-arg AGENT_PORT="$PORT" \
    --tag "$FULL_TAG" \
    --platform linux/amd64 \
    .

  echo "▶ Pushing $FULL_TAG..."
  docker push "$FULL_TAG"
done

# ── Update Container Apps to pull new images ──────────────────────────────────
echo "▶ Updating Container Apps with new images..."
PREFIX="rag-${ENV}"

az containerapp update \
  --name "${PREFIX}-retrieval" \
  --resource-group "$RESOURCE_GROUP" \
  --image "${ACR_LOGIN_SERVER}/rag-retrieval-agent:latest" \
  --output none

az containerapp update \
  --name "${PREFIX}-orchestrator" \
  --resource-group "$RESOURCE_GROUP" \
  --image "${ACR_LOGIN_SERVER}/rag-orchestrator-agent:latest" \
  --output none

az containerapp update \
  --name "${PREFIX}-main" \
  --resource-group "$RESOURCE_GROUP" \
  --image "${ACR_LOGIN_SERVER}/rag-main-agent:latest" \
  --output none

# ── Print summary ─────────────────────────────────────────────────────────────
MAIN_FQDN=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.mainAgentFqdn.value')
SEARCH_EP=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.searchEndpoint.value')

echo ""
echo "══════════════════════════════════════════════════════"
echo "  ✅ Deployment complete"
echo "  Main Agent (Teams Bot endpoint): https://${MAIN_FQDN}/api/messages"
echo "  Azure AI Search:                 ${SEARCH_EP}"
echo "══════════════════════════════════════════════════════"
