# Multi-stage Dockerfile — same image, different entrypoint per agent.
# Build args:
#   AGENT_MODULE  e.g. agents.main_agent, agents.orchestrator_agent, agents.retrieval_agent
#   AGENT_PORT    e.g. 8000, 8001, 8002

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS base

# Security hardening
RUN useradd -m -u 1000 appuser && \
    apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY shared/ ./shared/
COPY tools/   ./tools/
COPY agents/  ./agents/
COPY app.py   ./app.py

# Switch to non-root user
USER appuser

# Runtime args
ARG AGENT_MODULE=agents.main_agent
ARG AGENT_PORT=8000
ENV AGENT_MODULE=${AGENT_MODULE}
ENV AGENT_PORT=${AGENT_PORT}
ENV RUNNING_IN_AZURE=true

EXPOSE ${AGENT_PORT}

# Health check (ACA uses this)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${AGENT_PORT}/health || exit 1

CMD ["sh", "-c", "python -m uvicorn ${AGENT_MODULE}:app --host 0.0.0.0 --port ${AGENT_PORT} --workers 2"]
