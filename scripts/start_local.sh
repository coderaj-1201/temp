#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/start_local.sh
# Start all three agents locally (no Docker) for rapid development.
# Requires: .env file in project root with your Azure credentials.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT"

# ── Ensure .env exists ────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.template → .env and fill in values."
  exit 1
fi

# ── Activate venv if present ──────────────────────────────────────────────────
if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export RETRIEVAL_MODE=http
export RETRIEVAL_AGENT_URL=http://localhost:8002
export ORCHESTRATOR_AGENT_URL=http://localhost:8001
export MAIN_AGENT_URL=http://localhost:8000

echo "▶ Starting Retrieval Agent on :8002 ..."
AGENT_PORT=8002 python -m uvicorn agents.retrieval_agent:app --host 0.0.0.0 --port 8002 --reload &
PID_RETRIEVAL=$!

sleep 2

echo "▶ Starting Orchestrator Agent on :8001 ..."
AGENT_PORT=8001 python -m uvicorn agents.orchestrator_agent:app --host 0.0.0.0 --port 8001 --reload &
PID_ORCHESTRATOR=$!

sleep 2

echo "▶ Starting Main Agent on :8000 ..."
AGENT_PORT=8000 python -m uvicorn agents.main_agent:app --host 0.0.0.0 --port 8000 --reload &
PID_MAIN=$!

echo ""
echo "══════════════════════════════════════════════"
echo "  All agents running. Test with:"
echo ""
echo "  curl -X POST http://localhost:8000/query \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"text\": \"What is the annual leave policy?\"}'"
echo ""
echo "  Press Ctrl+C to stop all agents."
echo "══════════════════════════════════════════════"

# Trap Ctrl+C and kill all background processes
trap "echo 'Stopping agents...'; kill $PID_RETRIEVAL $PID_ORCHESTRATOR $PID_MAIN 2>/dev/null; exit" INT TERM

wait
