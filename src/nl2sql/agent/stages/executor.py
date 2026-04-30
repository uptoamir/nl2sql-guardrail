"""Executor — stage 5 of the agent pipeline. DETERMINISTIC, no LLM.

Per `final_1.md` §4.1 + §41.4. Runs the L3-L7 guardrail chain:
    validator → rewriter → execute_with_timeout → result_audit
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from nl2sql.agent.types import (
    DEPT,
    ExecResult,
    GuardrailLayerOutcome,
)
from nl2sql.db.connection import execute_with_timeout
from nl2sql.guardrails.errors import (
    AuditOk,
    GuardrailReject,
    ValidationError,
    ValidatorReject,
)
from nl2sql.guardrails.result_audit import audit_rows
from nl2sql.guardrails.sql_rewriter import rewrite_sql
from nl2sql.guardrails.sql_validator import validate_sql

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.session import SharedResources


def execute(
    sql: str,
    *,
    workspace_id: str,
    dept: DEPT,
    shared: SharedResources,
    timeout_s: float = 5.0,
) -> tuple[ExecResult, list[GuardrailLayerOutcome]]:
    """Run SQL through L3 → L4 → L5 → L6 → L7. Returns ``(ExecResult, layers)``.

    ``layers`` is the list of :class:`GuardrailLayerOutcome` for L3-L7
    (L1, L2, L8 are recorded by the calling Pipeline).

    Raises:
        ValidationError: L3 rejected
        GuardrailReject: L4 rejected (e.g., dept conflict)
        sqlite3.DatabaseError: L6 authorizer denied at engine level
        GuardrailBreach: L7 found a stray row
    """
    layers: list[GuardrailLayerOutcome] = []

    # L3: validate
    t0 = time.monotonic()
    val = validate_sql(sql)
    elapsed = (time.monotonic() - t0) * 1000
    if isinstance(val, ValidatorReject):
        layers.append(
            GuardrailLayerOutcome(
                layer_id="L3",
                name="AST validate",
                passed=False,
                reason=f"{val.reason}: {val.detail}",
                elapsed_ms=elapsed,
            )
        )
        raise ValidationError(val.reason, val.detail)
    layers.append(
        GuardrailLayerOutcome(
            layer_id="L3",
            name="AST validate",
            passed=True,
            elapsed_ms=elapsed,
        )
    )

    # L4: rewrite (defensive — should be no-op since validator already
    # rejected base-table refs)
    t0 = time.monotonic()
    try:
        rewritten, params = rewrite_sql(sql, dept=dept)
        layers.append(
            GuardrailLayerOutcome(
                layer_id="L4",
                name="AST rewrite",
                passed=True,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        )
    except GuardrailReject as e:
        layers.append(
            GuardrailLayerOutcome(
                layer_id="L4",
                name="AST rewrite",
                passed=False,
                reason=e.reason,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        )
        raise

    # L5+L6: execute through RO-locked connection with authorizer installed
    if shared.session_conn is None:
        raise RuntimeError("shared.session_conn is None — open_session was not called")
    t0 = time.monotonic()
    with execute_with_timeout(
        shared.session_conn, rewritten, params, timeout_s=timeout_s
    ) as cursor:
        rows_raw = cursor.fetchall()
        cols = [d[0] for d in cursor.description] if cursor.description else []
    rows = [dict(r) for r in rows_raw]
    exec_ms = (time.monotonic() - t0) * 1000
    layers.append(
        GuardrailLayerOutcome(
            layer_id="L5",
            name="RO connection + timeout",
            passed=True,
            elapsed_ms=0.5,  # opening was earlier; this layer's instant
        )
    )
    layers.append(
        GuardrailLayerOutcome(
            layer_id="L6",
            name="set_authorizer",
            passed=True,
            elapsed_ms=exec_ms,  # authorizer fires inline with execute
        )
    )

    # L7: result audit (raises GuardrailBreach on stray rows)
    t0 = time.monotonic()
    audit_outcome = audit_rows(rows, cols, dept, workspace_id=workspace_id)
    layers.append(
        GuardrailLayerOutcome(
            layer_id="L7",
            name="Result audit",
            passed=isinstance(audit_outcome, AuditOk),
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )
    )

    truncated = False
    if len(rows) >= shared.settings.result_row_limit:
        truncated = True

    return (
        ExecResult(
            rows=rows,
            columns=cols,
            row_count=len(rows),
            truncated=truncated,
            latency_ms=int(exec_ms),
        ),
        layers,
    )
