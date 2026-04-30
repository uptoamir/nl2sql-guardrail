"""Layer L7 of the 8-layer guardrail — post-execution row-level audit.

Per `final_1.md` §3 L7 + §35.9 + §41.9 (raise-only contract per C0-1).

The keystone exception. SLO 100.000% — any raise is a P1 PagerDuty page
in production (§13.1 ``NL2SQLGuardrailBreach`` alert).

**Contract**: :func:`audit_rows` returns :class:`AuditOk` on pass,
**raises** :class:`GuardrailBreach` on fail. NEVER returns a Breach
result type — making it impossible for a caller to forget to check.
"""

from __future__ import annotations

import logging
from typing import Any

from nl2sql.guardrails.errors import AuditOk, GuardrailBreach

logger = logging.getLogger(__name__)


def audit_rows(
    rows: list[dict[str, Any]],
    columns: list[str],
    dept: str,
    *,
    workspace_id: str,
) -> AuditOk:
    """Verify every row's ``Department`` matches ``dept``.

    Returns :class:`AuditOk` on pass — either the result has no
    ``Department`` column, or every row matches.

    Raises :class:`GuardrailBreach` on any stray row. Increments the
    keystone metric ``nl2sql_guardrail_breaches_total{layer=audit,
    workspace_id}`` (PagerDuty alert at >0). PII redacted from the log
    line — only row indexes are recorded, never raw row payloads.
    """
    if "Department" not in columns:
        return AuditOk(reason="no_department_column")

    mismatched: list[int] = []
    for i, row in enumerate(rows):
        if row.get("Department") != dept:
            mismatched.append(i)

    if not mismatched:
        return AuditOk()

    # ★ Increment the keystone metric — lazy import so the guardrails module
    # doesn't hard-depend on observability being wired up at import time
    # (Task 12 delivers observability/metrics.py).
    try:
        from nl2sql.observability.metrics import GUARDRAIL_BREACHES_TOTAL

        GUARDRAIL_BREACHES_TOTAL.labels(layer="audit", workspace_id=workspace_id).inc(
            len(mismatched)
        )
    except ImportError:  # pragma: no cover - metrics not yet wired
        pass

    # PII-redacted log: row indexes only, NEVER raw row data.
    logger.error(
        "guardrail_breach_audit_layer "
        "workspace_id=%s dept_expected=%s mismatch_count=%d row_indexes=%s",
        workspace_id,
        dept,
        len(mismatched),
        mismatched[:10],  # cap log volume
    )

    raise GuardrailBreach(
        layer="audit",
        count=len(mismatched),
        indexes=mismatched,
    )
