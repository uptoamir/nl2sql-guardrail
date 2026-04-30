"""Unit tests for the L4 sql_rewriter (incl. orphan EXISTS injection)."""

from __future__ import annotations

import pytest

from nl2sql.guardrails.errors import GuardrailReject
from nl2sql.guardrails.sql_rewriter import rewrite_sql


def test_standard_inject() -> None:
    """Bare base-Employee SELECT gets WHERE Department=? injected."""
    out, params = rewrite_sql("SELECT * FROM Employee", dept="Engineering")
    assert "Department" in out and "?" in out
    assert "LIMIT" in out.upper()
    assert params == ("Engineering",)


def test_orphan_certification_uses_exists() -> None:
    """P0-6: orphan Cert query gets EXISTS subquery (NOT IN)."""
    out, params = rewrite_sql("SELECT * FROM Certification", dept="Engineering")
    assert "EXISTS" in out.upper()
    assert "allowed_employees" in out
    assert params == ()  # no new param — view is dept-scoped


def test_orphan_benefits_uses_exists() -> None:
    out, params = rewrite_sql("SELECT * FROM Benefits", dept="Engineering")
    assert "EXISTS" in out.upper()
    assert "allowed_employees" in out


def test_self_join_each_alias_gets_filter() -> None:
    """P0-5a: self-joins must inject WHERE Department=? for EVERY alias."""
    out, params = rewrite_sql(
        "SELECT a.Name, b.Name FROM Employee a JOIN Employee b ON a.EmployeeId<b.EmployeeId",
        dept="Engineering",
    )
    assert params == ("Engineering", "Engineering")
    assert out.lower().count("department") >= 2


def test_dept_conflict_raises() -> None:
    """Conflicting WHERE Department='X' must raise — never silent overwrite."""
    with pytest.raises(GuardrailReject) as exc:
        rewrite_sql(
            "SELECT * FROM Employee WHERE Department='Sales'",
            dept="Marketing",
        )
    assert "dept_conflict" in str(exc.value)


def test_limit_preserved() -> None:
    """User-supplied LIMIT must not be clobbered."""
    out, _ = rewrite_sql("SELECT * FROM Employee LIMIT 5", dept="Engineering")
    # LIMIT 5 should still be present
    assert " 5" in out.upper().split("LIMIT")[-1] or " 5\n" in out


def test_idempotent_on_already_scoped() -> None:
    """Already-scoped SQL with matching dept literal should not raise."""
    out, params = rewrite_sql(
        "SELECT * FROM Employee WHERE Department='Engineering'",
        dept="Engineering",
    )
    # No conflict raise; result references Department
    assert "Department" in out
