"""Integration tests — full pipeline with mock LLM."""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_happy_path_average_salary(session) -> None:
    """The 5 spec example questions should all return scoped results."""
    result = session.ask("What is the average salary?")
    assert result.outcome == "ok"
    assert "AVG" in result.sql_used.upper()
    assert "allowed_employees" in result.sql_used
    assert result.row_count == 1


@pytest.mark.integration
def test_happy_path_software_engineers(session) -> None:
    result = session.ask("Who are the software engineers?")
    assert result.outcome == "ok"
    assert "Software Engineer" in result.sql_used
    # Engineering scope should have ~5+ SE roles
    assert result.row_count > 0


@pytest.mark.integration
def test_happy_path_aws_certs(session) -> None:
    result = session.ask("Which employees have an AWS certification?")
    assert result.outcome == "ok"
    assert "AWS" in result.sql_used


@pytest.mark.integration
def test_happy_path_orphan_cert_uses_exists_or_join(session) -> None:
    """The orphan-Cert demo (P0-6) — 'list all certifications' should
    NOT leak certs from other depts."""
    result = session.ask("List all certifications")
    assert result.outcome == "ok"
    # Either uses allowed_certifications view directly OR uses EXISTS with
    # allowed_employees — both are scoped.
    assert "allowed_certifications" in result.sql_used or "EXISTS" in result.sql_used.upper()


@pytest.mark.integration
def test_all_eight_layers_recorded(session) -> None:
    result = session.ask("count of employees")
    assert result.outcome == "ok"
    layer_ids = {ly.layer_id for ly in result.guardrail_layers}
    # All 8 layers should be recorded as passed.
    expected = {"L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"}
    assert layer_ids == expected
    assert all(ly.passed for ly in result.guardrail_layers)


@pytest.mark.integration
def test_session_history_appends(session) -> None:
    session.ask("count of employees")
    session.ask("average salary")
    assert len(session.history) == 2
    assert session.history[0].question == "count of employees"


@pytest.mark.integration
def test_session_cost_accumulates(session) -> None:
    initial = session.session_cost["tokens"]
    session.ask("count of employees")
    after = session.session_cost["tokens"]
    assert after > initial


@pytest.mark.integration
def test_authorizer_alone_blocks_base_table(session_conn) -> None:
    """Defense-in-depth proof — the authorizer alone (Layer 6) blocks
    base-table reads even if validator + rewriter were bypassed."""
    import sqlite3

    with pytest.raises(sqlite3.DatabaseError):
        session_conn.execute("SELECT COUNT(*) FROM Employee").fetchone()


@pytest.mark.integration
def test_session_returns_only_active_dept(session) -> None:
    """Run multiple questions; verify no row's Department deviates."""
    for q in [
        "count of employees",
        "list all certifications",
        "average salary",
    ]:
        result = session.ask(q)
        if result.outcome != "ok":
            continue
        for row in result.supporting_rows:
            if "Department" in row:
                assert row["Department"] == session.dept
