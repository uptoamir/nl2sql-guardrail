"""Unit tests for the L3 sql_validator.

Per `final_1.md` §35.9 + §41.9. The 16-case parametrized reject matrix
is the load-bearing test for L3.
"""

from __future__ import annotations

import pytest

from nl2sql.guardrails.errors import ValidatorOk, ValidatorReject
from nl2sql.guardrails.sql_validator import validate_sql

REJECT_CASES = [
    ("DROP TABLE allowed_employees", "forbidden_node"),
    ("INSERT INTO Employee VALUES(1,2,3,4,5,6,7)", "forbidden_node"),
    ("UPDATE Employee SET SalaryAmount=0", "forbidden_node"),
    ("DELETE FROM Employee", "forbidden_node"),
    # sqlglot's parser raises ParseError on ATTACH; we accept either parse_error
    # or forbidden_node — both are valid rejections.
    ("ATTACH DATABASE 'x' AS y", None),
    ("SELECT 1; SELECT 2", "multi_statement"),
    ("SELECT * FROM Employee", "base_table_ref"),
    ("SELECT * FROM main.Employee", "base_table_ref"),
    ("SELECT * FROM Certification", "base_table_ref"),
    ("SELECT * FROM Benefits", "base_table_ref"),
    ("SELECT * FROM sqlite_master", "sqlite_internal"),
    ("SELECT * FROM sqlite_schema", "sqlite_internal"),
    ("SELECT load_extension('evil.so')", "blocked_function"),
    ("SELECT readfile('/etc/passwd')", "blocked_function"),
    ("SELECT writefile('/tmp/x', 'pwn')", "blocked_function"),
    (
        "SELECT * FROM allowed_employees UNION SELECT * FROM Employee",
        "base_table_ref",
    ),
    ("SELECT rowid FROM allowed_employees", "rowid_reference_blocked"),
    ("SELECT _rowid_ FROM allowed_employees", "rowid_reference_blocked"),
    ("SELECT oid FROM allowed_employees", "rowid_reference_blocked"),
    ("SELECT * FROM employees_secret", "unknown_table"),
    ("SELECT * FROM Employee WHERE 1", "base_table_ref"),
]

ACCEPT_CASES = [
    "SELECT * FROM allowed_employees",
    "SELECT Name, Role FROM allowed_employees WHERE Role LIKE '%Engineer%'",
    "SELECT e.Name, c.CertificationName FROM allowed_employees e "
    "JOIN allowed_certifications c USING (EmployeeId)",
    "WITH x AS (SELECT * FROM allowed_employees) SELECT COUNT(*) FROM x",
    "SELECT * FROM allowed_employees UNION ALL SELECT * FROM allowed_employees",
    "SELECT Department, AVG(SalaryAmount) FROM allowed_employees "
    "GROUP BY Department HAVING COUNT(*) > 0",
    "SELECT AVG(SalaryAmount + COALESCE(YearlyBonusAmount, 0)) FROM allowed_employees",
]


@pytest.mark.parametrize("sql,reason", REJECT_CASES)
def test_validator_rejects(sql: str, reason: str) -> None:
    out = validate_sql(sql)
    assert isinstance(out, ValidatorReject), f"expected reject for: {sql!r}"
    if reason is not None:
        assert out.reason == reason or "non_select_root" in out.reason or "_root" in out.reason, (
            f"got {out.reason!r}, expected {reason!r} for {sql!r}"
        )


@pytest.mark.parametrize("sql", ACCEPT_CASES)
def test_validator_accepts(sql: str) -> None:
    out = validate_sql(sql)
    assert isinstance(out, ValidatorOk), f"expected accept for {sql!r}, got {out}"


def test_validator_never_raises_on_random_input() -> None:
    """B2-fuzz invariant — validate_sql NEVER raises on any input.

    Always returns a typed ValidatorOutcome.
    """
    import random

    random.seed(42)
    for _ in range(50):
        raw = bytes(random.randint(0, 255) for _ in range(random.randint(0, 200))).decode(
            "utf-8", errors="ignore"
        )
        out = validate_sql(raw)
        assert isinstance(out, (ValidatorOk, ValidatorReject))


def test_validator_handles_non_string() -> None:
    """Non-string inputs should not crash."""
    # Cast through Any to satisfy mypy; real test is runtime behavior
    from typing import Any, cast

    out = validate_sql(cast(Any, 42))
    assert isinstance(out, ValidatorReject)
    assert out.reason == "not_a_string"
