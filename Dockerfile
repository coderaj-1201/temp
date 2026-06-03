# Dockerfile for retrieval pipeline (container-code/)
# Build from the container-code/ directory:
#   docker build -f Dockerfile \
#     --build-arg AGENT_MODULE=agents.main_agent --build-arg AGENT_PORT=8000 \
#     -t rag-main .

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS base

RUN useradd -m -u 1000 appuser && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY container-code/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY container-code/shared/    ./shared/
COPY container-code/tools/     ./tools/
COPY container-code/agents/    ./agents/

USER appuser

ARG AGENT_MODULE=agents.main_agent
ARG AGENT_PORT=8000
ENV AGENT_MODULE=${AGENT_MODULE}
ENV AGENT_PORT=${AGENT_PORT}
ENV RUNNING_IN_AZURE=true
ENV PYTHONUNBUFFERED=1

EXPOSE ${AGENT_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${AGENT_PORT}/health || exit 1

CMD ["sh", "-c", "python -m uvicorn ${AGENT_MODULE}:app --host 0.0.0.0 --port ${AGENT_PORT} --workers 2"]
