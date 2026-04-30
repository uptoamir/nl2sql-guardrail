"""Unit tests for the DB layer (open_session ordering + scope correctness)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nl2sql.db.connection import open_introspect_conn, open_session
from nl2sql.db.views import (
    ALLOWED_DEPARTMENTS,
    create_scope_views,
    validate_department,
    validate_workspace_id,
)
from nl2sql.guardrails.authorizer import ALLOWED_VIEWS, make_authorizer
from nl2sql.guardrails.errors import (
    DatabaseIntegrityError,
    InvalidScopeError,
)


def test_introspect_conn_blocks_writes(fixture_db_path: Path) -> None:
    conn = open_introspect_conn(fixture_db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO Employee VALUES(999,'X','Sales','X','2025-01-01',1,1)")
    conn.close()


def test_open_session_creates_temp_views(session_conn: sqlite3.Connection) -> None:
    """All 3 allowed_* views exist + correctly scope to Engineering."""
    n = session_conn.execute("SELECT COUNT(*) FROM allowed_employees").fetchone()[0]
    assert n == 34  # 102 / 3


def test_open_session_scope_is_engineering_only(
    session_conn: sqlite3.Connection,
) -> None:
    depts = {
        r[0]
        for r in session_conn.execute(
            "SELECT DISTINCT Department FROM allowed_employees"
        ).fetchall()
    }
    assert depts == {"Engineering"}


def test_writes_blocked_after_session_open(
    session_conn: sqlite3.Connection,
) -> None:
    """query_only=1 (set at step 4) blocks any subsequent writes."""
    with pytest.raises(sqlite3.DatabaseError):
        session_conn.execute("INSERT INTO Employee VALUES(999,'X','Sales','X','2025-01-01',1,1)")


def test_authorizer_blocks_direct_base_table_read(
    session_conn: sqlite3.Connection,
) -> None:
    """The L6 showpiece — direct base-table read denied at engine level."""
    with pytest.raises(sqlite3.DatabaseError) as exc:
        session_conn.execute("SELECT COUNT(*) FROM Employee").fetchone()
    assert "not authorized" in str(exc.value).lower() or "prohibited" in str(exc.value).lower()


def test_authorizer_blocks_sqlite_master(
    session_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        session_conn.execute("SELECT name FROM sqlite_master").fetchone()


def test_authorizer_blocks_load_extension(
    session_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        session_conn.execute("SELECT load_extension('evil.so')").fetchone()


def test_authorizer_install_before_views_fails(fixture_db_path: Path) -> None:
    """Regression: any reordering must break this test (per §35.7)."""
    conn = sqlite3.connect(f"file:{fixture_db_path}?mode=rw", uri=True, isolation_level=None)
    conn.set_authorizer(make_authorizer(ALLOWED_VIEWS))
    with pytest.raises(sqlite3.DatabaseError):
        create_scope_views(conn, dept="Sales", workspace_id="local-fixture")
    conn.close()


def test_validate_department_rejects_unknown() -> None:
    with pytest.raises(InvalidScopeError):
        validate_department("HR")


def test_validate_department_rejects_injection() -> None:
    """P0-2 SQL-injection-via-dept must be blocked at validation."""
    with pytest.raises(InvalidScopeError):
        validate_department("'; DROP TABLE Employee; --")


def test_validate_workspace_id_accepts_uuid_and_slug() -> None:
    validate_workspace_id("local-fixture")
    validate_workspace_id("f0b5b3e0-8a1b-4d9e-9b7e-1c2d3e4f5a6b")


def test_validate_workspace_id_rejects_injection() -> None:
    with pytest.raises(InvalidScopeError):
        validate_workspace_id("'; DROP TABLE Employee; --")


def test_views_omit_rowid(session_conn: sqlite3.Connection) -> None:
    """P0-1 — rowid not in view projection.

    PRAGMA is denied by the authorizer once installed, so we check via
    cursor.description on a 0-row select instead.
    """
    cur = session_conn.execute("SELECT * FROM allowed_employees LIMIT 0")
    cols = [d[0] for d in cur.description] if cur.description else []
    assert all(c.lower() not in {"rowid", "_rowid_", "oid"} for c in cols)
    assert "EmployeeId" in cols  # sanity: real columns ARE projected


def test_all_three_departments_open_correctly(fixture_db_path: Path) -> None:
    """Each department gets exactly 34 employees."""
    for dept in ALLOWED_DEPARTMENTS:
        conn = open_session(fixture_db_path, dept=dept, workspace_id="local-fixture")
        n = conn.execute("SELECT COUNT(*) FROM allowed_employees").fetchone()[0]
        assert n == 34, f"{dept}: got {n}"
        conn.close()


def test_quick_check_corrupt_db_raises(tmp_path: Path) -> None:
    """A corrupt DB should fail the quick_check at startup."""
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"this is not a sqlite database file")
    with pytest.raises((DatabaseIntegrityError, sqlite3.DatabaseError)):
        open_introspect_conn(bad)
