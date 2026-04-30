"""Structured JSONL audit log — one record per turn.

Per `final_1.md` §12.3 + §41.16 (record_kind: dict). Append-only.
``RotatingFileHandler`` for v0 disk-fill protection (P1-1).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Module-level logger initialized lazily — first emit_audit() call sets up
# the file handler.
_audit_logger: logging.Logger | None = None
_AUDIT_LOG_PATH = Path("logs/audit.jsonl")


def _init_audit_logger() -> logging.Logger:
    """Initialize the audit logger with rotating file handler."""
    global _audit_logger  # noqa: PLW0603 - module-level singleton, lazy-initialized
    if _audit_logger is not None:
        return _audit_logger

    _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        _AUDIT_LOG_PATH,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=30,  # keep 30 (~300 MB total)
        encoding="utf-8",
    )
    # Audit lines ARE the JSON; no formatter prefix.
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("nl2sql.audit")
    logger.setLevel(logging.INFO)
    # Avoid double emission via root logger.
    logger.propagate = False
    logger.addHandler(handler)

    _audit_logger = logger
    return logger


def emit_audit(record_kind: str, **fields: Any) -> None:
    """Emit one structured audit record as JSONL.

    Per §41.16: ``record`` is a dict (not a Pydantic class). Validated
    in CI against ``docs/schemas/audit_record.schema.json``.
    """
    logger = _init_audit_logger()
    record = {
        "kind": record_kind,
        "ts": datetime.now(UTC).isoformat(),
        **fields,
    }
    try:
        logger.info(json.dumps(record, default=str))
    except (TypeError, ValueError) as e:
        # Fall back to a minimal record if something can't be serialized.
        logger.info(
            json.dumps(
                {
                    "kind": "audit_serialization_error",
                    "ts": datetime.now(UTC).isoformat(),
                    "underlying_error": str(e),
                    "record_kind": record_kind,
                }
            )
        )
