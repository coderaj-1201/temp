"""
Structured logging with Azure Application Insights via OpenTelemetry.

Locally: JSON logs to stdout (readable in terminal / Docker logs)
In ACA:  Same JSON → picked up by Log Analytics workspace automatically
         + App Insights for distributed tracing across all 3 agents

Log Analytics query (Azure Portal → Log Analytics → Logs):
  ContainerAppConsoleLogs_CL
  | where ContainerName_s contains "rag"
  | project TimeGenerated, Log_s
  | order by TimeGenerated desc
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """Emits each log line as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Any extra fields passed via extra={} in logger calls
        for key in ("conversation_id", "user_id", "domain", "tool", "attempt", "confidence"):
            if hasattr(record, key):
                log_obj[key] = getattr(record, key)
        return json.dumps(log_obj)


def configure_logging(service_name: str = "rag") -> None:
    from shared.config import settings

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy libraries
    for noisy in ("azure.core", "azure.identity", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Wire up Azure Application Insights (works locally too if conn string set)
    if settings.APPLICATIONINSIGHTS_CONNECTION_STRING:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(
                connection_string=settings.APPLICATIONINSIGHTS_CONNECTION_STRING,
                service_name=service_name,
            )
            logging.getLogger(__name__).info(
                "Azure Monitor OpenTelemetry configured for service '%s'.", service_name
            )
        except ImportError:
            logging.getLogger(__name__).warning(
                "azure-monitor-opentelemetry not installed — App Insights skipped."
            )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
