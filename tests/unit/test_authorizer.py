"""Unit tests for the L6 set_authorizer callback (action × context matrix)."""

from __future__ import annotations

import sqlite3

import pytest

from nl2sql.guardrails.authorizer import ALLOWED_VIEWS, make_authorizer

cb = make_authorizer(ALLOWED_VIEWS)

CASES = [
    # (action, arg1, arg2, dbname, source, expected)
    # ── Reads of base tables ─────────────────────────────────────────────
    (sqlite3.SQLITE_READ, "Employee", "Name", "main", "allowed_employees", sqlite3.SQLITE_OK),
    (sqlite3.SQLITE_READ, "Employee", "Name", "main", "allowed_certifications", sqlite3.SQLITE_OK),
    (sqlite3.SQLITE_READ, "Employee", "Name", "main", "allowed_benefits", sqlite3.SQLITE_OK),
    (sqlite3.SQLITE_READ, "Employee", "Name", "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_READ, "Employee", "SalaryAmount", "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_READ, "Certification", "EmployeeId", "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_READ, "Benefits", "RemainingBalance", "main", None, sqlite3.SQLITE_DENY),
    # ── sqlite_master / sqlite_schema ────────────────────────────────────
    (sqlite3.SQLITE_READ, "sqlite_master", "name", "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_READ, "sqlite_schema", "name", "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_READ, "sqlite_temp_master", "name", "temp", None, sqlite3.SQLITE_DENY),
    # ── Reads of allowed views directly ─────────────────────────────────
    (sqlite3.SQLITE_READ, "allowed_employees", "Name", "temp", None, sqlite3.SQLITE_OK),
    # ── DDL / DML ───────────────────────────────────────────────────────
    (sqlite3.SQLITE_INSERT, "Employee", None, "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_UPDATE, "Employee", "SalaryAmount", "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_DELETE, "Employee", None, "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_DROP_TABLE, "Employee", None, "main", None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_CREATE_TABLE, "evil_table", None, "main", None, sqlite3.SQLITE_DENY),
    # ── ATTACH / PRAGMA ─────────────────────────────────────────────────
    (sqlite3.SQLITE_ATTACH, "evil.db", None, None, None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_DETACH, "evil", None, None, None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_PRAGMA, "table_info", "Employee", None, None, sqlite3.SQLITE_DENY),
    # ── SELECT / TRANSACTION (always OK) ────────────────────────────────
    (sqlite3.SQLITE_SELECT, None, None, None, None, sqlite3.SQLITE_OK),
    (sqlite3.SQLITE_TRANSACTION, "BEGIN", None, None, None, sqlite3.SQLITE_OK),
    # ── Functions ───────────────────────────────────────────────────────
    (sqlite3.SQLITE_FUNCTION, None, "load_extension", None, None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_FUNCTION, None, "readfile", None, None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_FUNCTION, None, "writefile", None, None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_FUNCTION, None, "fts3_tokenizer", None, None, sqlite3.SQLITE_DENY),
    (sqlite3.SQLITE_FUNCTION, None, "count", None, None, sqlite3.SQLITE_OK),
    (sqlite3.SQLITE_FUNCTION, None, "avg", None, None, sqlite3.SQLITE_OK),
    (sqlite3.SQLITE_FUNCTION, None, "upper", None, None, sqlite3.SQLITE_OK),
    # ── Reads of unknown tables (allowed; sqlglot validator rejects upstream)
    (sqlite3.SQLITE_READ, "some_other_table", "col", "main", None, sqlite3.SQLITE_OK),
]


@pytest.mark.parametrize(
    "action,arg1,arg2,dbname,source,expected",
    CASES,
    ids=[f"{c[0]}-{c[1]}-{c[4]}" for c in CASES],
)
def test_authorizer_matrix(
    action: int,
    arg1: str | None,
    arg2: str | None,
    dbname: str | None,
    source: str | None,
    expected: int,
) -> None:
    got = cb(action, arg1, arg2, dbname, source)
    assert got == expected, (
        f"authorizer({action}, {arg1!r}, {arg2!r}, {dbname!r}, {source!r}) = "
        f"{got}, expected {expected}"
    )
