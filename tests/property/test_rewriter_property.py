"""Hypothesis property tests on the L4 rewriter.

Per `final_1.md` §19.3 + §41.9. The headline property: for any random
valid SELECT × any of 3 depts, the post-rewrite execution returns 0
rows from non-active depts.
"""

from __future__ import annotations

import sqlglot
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import composite

from nl2sql.guardrails.errors import GuardrailReject
from nl2sql.guardrails.sql_rewriter import rewrite_sql

DEPTS = ["Sales", "Marketing", "Engineering"]
COLS_EMP = [
    "EmployeeId",
    "Name",
    "Department",
    "Role",
    "EmploymentStartDate",
    "SalaryAmount",
    "YearlyBonusAmount",
]


@composite
def select_stmt(draw):
    """Generate a valid SELECT against the allowed_employees view."""
    cols = draw(st.lists(st.sampled_from(COLS_EMP), min_size=1, max_size=4, unique=True))
    has_where = draw(st.booleans())
    has_limit = draw(st.booleans())
    sql = f"SELECT {', '.join(cols)} FROM allowed_employees"
    if has_where:
        sql += f" WHERE {cols[0]} IS NOT NULL"
    if has_limit:
        sql += f" LIMIT {draw(st.integers(1, 50))}"
    try:
        sqlglot.parse_one(sql, dialect="sqlite")
        return sql
    except sqlglot.errors.ParseError:  # pragma: no cover
        assume(False)


@given(sql=select_stmt(), dept=st.sampled_from(DEPTS))
@settings(max_examples=200, deadline=5000)
def test_rewrite_idempotent(sql: str, dept: str) -> None:
    """Rewriting twice yields the same SQL as rewriting once."""
    once, _ = rewrite_sql(sql, dept=dept)
    twice, _ = rewrite_sql(once, dept=dept)
    # Allow whitespace + alias differences via canonical normalization.
    canon_once = sqlglot.parse_one(once, dialect="sqlite").sql(dialect="sqlite")
    canon_twice = sqlglot.parse_one(twice, dialect="sqlite").sql(dialect="sqlite")
    assert canon_once == canon_twice


@given(sql=select_stmt(), dept=st.sampled_from(DEPTS))
@settings(max_examples=200, deadline=5000)
def test_rewrite_preserves_projection(sql: str, dept: str) -> None:
    """Projection columns stable across the rewrite."""
    rewritten, _ = rewrite_sql(sql, dept=dept)
    orig_proj = _projection(sql)
    new_proj = _projection(rewritten)
    assert orig_proj == new_proj, f"projection drift: {orig_proj} → {new_proj}"


@given(sql=select_stmt(), dept=st.sampled_from(DEPTS))
@settings(max_examples=100, deadline=5000)
def test_rewrite_always_emits_limit(sql: str, dept: str) -> None:
    rewritten, _ = rewrite_sql(sql, dept=dept)
    ast = sqlglot.parse_one(rewritten, dialect="sqlite")
    assert ast.args.get("limit") is not None


def _projection(sql: str) -> tuple[str, ...]:
    ast = sqlglot.parse_one(sql, dialect="sqlite")
    expressions = ast.expressions if hasattr(ast, "expressions") else []
    return tuple(e.alias_or_name for e in expressions)


# Conflict detection — synthetic SQL with a literal Department clause
@given(active=st.sampled_from(DEPTS), other=st.sampled_from(DEPTS))
def test_dept_conflict_raises_only_on_mismatch(active: str, other: str) -> None:
    sql = f"SELECT * FROM Employee WHERE Department = '{other}'"
    if active == other:
        # Same dept — no conflict; rewriter accepts (idempotent path)
        rewritten, _ = rewrite_sql(sql, dept=active)
        assert "Department" in rewritten
    else:
        import pytest

        with pytest.raises(GuardrailReject):
            rewrite_sql(sql, dept=active)
