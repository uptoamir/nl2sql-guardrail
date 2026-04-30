"""Hypothesis property tests for the L3 validator.

Per `final_1.md` §19.3 + §41.9. The headline property: validator
NEVER raises on input.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from nl2sql.guardrails.errors import ValidatorOk, ValidatorReject
from nl2sql.guardrails.sql_validator import validate_sql

settings.register_profile("ci", max_examples=300, deadline=5000)
settings.register_profile("dev", max_examples=50, deadline=2000)


@given(sql=st.text(min_size=0, max_size=200))
@settings(max_examples=300, deadline=5000)
def test_validator_never_raises(sql: str) -> None:
    """For any text input, validate_sql returns a typed result."""
    out = validate_sql(sql)
    assert isinstance(out, (ValidatorOk, ValidatorReject))


_DML_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|PRAGMA|CREATE)\b",
    re.IGNORECASE,
)


@given(
    leading=st.text(alphabet="abc ", min_size=0, max_size=20),
    keyword=st.sampled_from(
        ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "ATTACH", "PRAGMA", "CREATE"]
    ),
    trailing=st.text(alphabet="xyz 1234567890;", min_size=0, max_size=50),
)
@settings(max_examples=100, deadline=5000)
def test_validator_rejects_any_dml_keyword(leading: str, keyword: str, trailing: str) -> None:
    """Any input containing a DML/DDL/PRAGMA keyword (outside quotes) is rejected."""
    sql = f"{leading} {keyword} {trailing}"
    out = validate_sql(sql)
    assert isinstance(out, ValidatorReject), (
        f"DML keyword {keyword!r} should be rejected: {sql!r} → {out}"
    )


@given(table_name=st.sampled_from(["Employee", "Certification", "Benefits"]))
def test_validator_rejects_base_table_refs(table_name: str) -> None:
    out = validate_sql(f"SELECT * FROM {table_name}")
    assert isinstance(out, ValidatorReject)
    assert out.reason == "base_table_ref"


@given(view=st.sampled_from(["allowed_employees", "allowed_certifications", "allowed_benefits"]))
def test_validator_accepts_allowed_views(view: str) -> None:
    out = validate_sql(f"SELECT * FROM {view}")
    assert isinstance(out, ValidatorOk)


@given(rowid_col=st.sampled_from(["rowid", "_rowid_", "oid", "ROWID", "OID"]))
def test_validator_blocks_rowid_refs(rowid_col: str) -> None:
    """P0-1 — any rowid-style column reference is blocked."""
    out = validate_sql(f"SELECT {rowid_col} FROM allowed_employees")
    assert isinstance(out, ValidatorReject)
    assert out.reason == "rowid_reference_blocked"
