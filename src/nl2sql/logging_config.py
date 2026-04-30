"""Structlog configuration.

Two outputs:

1. **Console** — pretty, colourised, human-readable (Rich-aware).
2. **Audit log** — newline-delimited JSON, one record per turn, written to
   ``settings.audit_log_path``. This stream is the auditable record of every
   question, generated SQL, validation outcome, and result count — exactly the
   shape an SRE / data-governance team would want to forward to BigQuery,
   ClickHouse, Splunk, etc.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog


def configure_logging(level: str, audit_log_path: Path) -> None:
    """Configure stdlib logging + structlog once at process start."""
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    # Stdlib logging is the substrate; structlog wraps it.
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Console handler — human readable.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)

    # Audit handler — JSON, append-only.
    audit_handler = logging.FileHandler(audit_log_path, mode="a", encoding="utf-8")
    audit_handler.setLevel(logging.INFO)
    audit_handler.addFilter(_AuditOnlyFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    root.addHandler(console_handler)
    root.addHandler(audit_handler)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
    )
    audit_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    console_handler.setFormatter(console_formatter)
    audit_handler.setFormatter(audit_formatter)


class _AuditOnlyFilter(logging.Filter):
    """Audit handler only takes records flagged with extra={'audit': True}."""

    def filter(self, record: logging.LogRecord) -> bool:
        return bool(getattr(record, "audit", False))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
