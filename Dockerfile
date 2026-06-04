# ─────────────────────────────────────────────────────────────────────────────
# Retrieval Pipeline — Single Dockerfile, one image per agent via build args
#
# Build from REPO ROOT (where this Dockerfile lives):
#   docker build \
#     --build-arg AGENT_MODULE=agents.main_agent \
#     --build-arg AGENT_PORT=8000 \
#     -t rag-main-agent:latest .
#
#   docker build \
#     --build-arg AGENT_MODULE=agents.orchestrator_agent \
#     --build-arg AGENT_PORT=8001 \
#     -t rag-orchestrator-agent:latest .
#
#   docker build \
#     --build-arg AGENT_MODULE=agents.retrieval_agent \
#     --build-arg AGENT_PORT=8002 \
#     -t rag-retrieval-agent:latest .
#
#   docker build \
#     --build-arg AGENT_MODULE=agents.evaluation_agent \
#     --build-arg AGENT_PORT=8003 \
#     -t rag-evaluation-agent:latest .
#
#   docker build \
#     --build-arg AGENT_MODULE=agents.feedback_agent \
#     --build-arg AGENT_PORT=8004 \
#     -t rag-feedback-agent:latest .
# ─────────────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS base

RUN useradd -m -u 1000 appuser && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY container-code/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY container-code/shared/    ./shared/
COPY container-code/tools/     ./tools/
COPY container-code/agents/    ./agents/

# Clean up bytecode
RUN find . -name "*.pyc" -delete && \
    find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

USER appuser

ARG AGENT_MODULE=agents.main_agent
ARG AGENT_PORT=8000
ENV AGENT_MODULE=${AGENT_MODULE}
ENV AGENT_PORT=${AGENT_PORT}
ENV RUNNING_IN_AZURE=true
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE ${AGENT_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${AGENT_PORT}/health || exit 1

CMD ["sh", "-c", "python -m uvicorn ${AGENT_MODULE}:app --host 0.0.0.0 --port ${AGENT_PORT} --workers 2"]
