"""Unit tests for L7 result_audit (raise-only contract per §41.9 / C0-1)."""

from __future__ import annotations

import pytest

from nl2sql.guardrails.errors import AuditOk, GuardrailBreach
from nl2sql.guardrails.result_audit import audit_rows


def test_all_match_returns_ok() -> None:
    rows = [
        {"Name": "X", "Department": "Engineering"},
        {"Name": "Y", "Department": "Engineering"},
    ]
    out = audit_rows(rows, ["Name", "Department"], "Engineering", workspace_id="local-fixture")
    assert isinstance(out, AuditOk)


def test_no_dept_column_passes() -> None:
    """Aggregates without Department column trivially pass."""
    out = audit_rows(
        [{"avg_salary": 100000}], ["avg_salary"], "Engineering", workspace_id="local-fixture"
    )
    assert isinstance(out, AuditOk)
    assert out.reason == "no_department_column"


def test_single_stray_row_raises() -> None:
    rows = [
        {"Name": "X", "Department": "Engineering"},
        {"Name": "Y", "Department": "Marketing"},  # ← stray
        {"Name": "Z", "Department": "Engineering"},
    ]
    with pytest.raises(GuardrailBreach) as exc:
        audit_rows(rows, ["Name", "Department"], "Engineering", workspace_id="local-fixture")
    assert exc.value.count == 1
    assert exc.value.indexes == [1]
    assert exc.value.breach_layer == "audit"


def test_multiple_strays_count_correctly() -> None:
    rows = [
        {"Name": "A", "Department": "Sales"},
        {"Name": "B", "Department": "Engineering"},
        {"Name": "C", "Department": "Marketing"},
    ]
    with pytest.raises(GuardrailBreach) as exc:
        audit_rows(rows, ["Name", "Department"], "Engineering", workspace_id="local-fixture")
    assert exc.value.count == 2
    assert sorted(exc.value.indexes) == [0, 2]


def test_empty_rows_passes() -> None:
    out = audit_rows([], ["Name", "Department"], "Engineering", workspace_id="local-fixture")
    assert isinstance(out, AuditOk)
