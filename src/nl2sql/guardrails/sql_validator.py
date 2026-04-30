"""Layer L3 of the 8-layer guardrail — sqlglot AST validator.

Per `final_1.md` §3 L3 + §35.9 (B1-2 full code) + §41.9 (canonical
implementation). Whitelist allowed_* views; blocklist DML/DDL/PRAGMA/
ATTACH/multi-statement/sqlite_master/blocked-functions/rowid.

INVARIANT (also a property test in §19.3): :func:`validate_sql` NEVER
raises on input. It always returns a typed :class:`ValidatorOutcome`.
The fuzz test (§19.7) hammers this with random bytes.
"""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp

from nl2sql.guardrails.errors import (
    ValidatorOk,
    ValidatorOutcome,
    ValidatorReject,
)

# ─── Allow-lists / block-lists (per §35.9) ──────────────────────────────────
ALLOWED_VIEWS: frozenset[str] = frozenset(
    {"allowed_employees", "allowed_certifications", "allowed_benefits"}
)

BASE_TABLES: frozenset[str] = frozenset({"Employee", "Certification", "Benefits"})

SQLITE_INTERNAL: frozenset[str] = frozenset(
    {
        "sqlite_master",
        "sqlite_schema",
        "sqlite_temp_master",
        "sqlite_temp_schema",
        "sqlite_sequence",
        "sqlite_stat1",
        "sqlite_stat4",
    }
)

BLOCKED_FUNCTIONS: frozenset[str] = frozenset(
    {"load_extension", "readfile", "writefile", "fts3_tokenizer", "edit"}
)

ROWID_REFS: frozenset[str] = frozenset({"rowid", "_rowid_", "oid"})

# Forbidden AST node types (DDL / DML / PRAGMA / ATTACH at any depth).
# Note: sqlglot uses ``Attach`` not ``AttachDatabase`` (verified ≥25.16).
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Pragma,
    exp.Attach,
)

# Top-level SELECT-class nodes that are valid roots.
# UNION/INTERSECT/EXCEPT all subclass exp.SetOperation in current sqlglot;
# Select itself is always valid.
_VALID_ROOT_TYPES: tuple[type[exp.Expression], ...] = (exp.Select, exp.SetOperation)


# ─── The validator ──────────────────────────────────────────────────────────
def validate_sql(sql: str) -> ValidatorOutcome:
    """Validate ``sql`` against the L3 whitelist + blocklist.

    Returns:
      :class:`ValidatorOk` if the SQL is acceptable.
      :class:`ValidatorReject` with ``reason`` + ``detail`` otherwise.

    NEVER raises on input. Property-test invariant (`§19.3`).
    """
    if not isinstance(sql, str):
        return ValidatorReject(reason="not_a_string", detail=type(sql).__name__)

    try:
        statements = sqlglot.parse(sql, dialect="sqlite")
    except sqlglot.errors.ParseError as e:
        return ValidatorReject(reason="parse_error", detail=str(e)[:200])
    except Exception as e:  # noqa: BLE001 - sqlglot can raise various errors on adversarial input
        return ValidatorReject(
            reason="parse_unexpected",
            detail=f"{type(e).__name__}: {str(e)[:200]}",
        )

    # `parse` returns a list with possibly None for trailing comment-only stmts.
    statements = [s for s in statements if s is not None]

    if not statements:
        return ValidatorReject(reason="empty_after_comments")
    if len(statements) > 1:
        return ValidatorReject(reason="multi_statement", detail=f"got {len(statements)}")

    root = statements[0]
    if not isinstance(root, _VALID_ROOT_TYPES):
        return ValidatorReject(reason="non_select_root", detail=type(root).__name__)

    # Collect CTE aliases declared anywhere in the tree so the table-walk
    # below doesn't mistake them for unknown tables.
    cte_aliases: set[str] = set()
    for cte in root.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            cte_aliases.add(alias)

    # Walk every Select node (including subqueries, CTEs, UNION arms).
    for select in root.find_all(exp.Select):
        outcome = _check_select(select, cte_aliases=cte_aliases)
        if isinstance(outcome, ValidatorReject):
            return outcome

    # Defense in depth — reject any forbidden node anywhere in the tree.
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return ValidatorReject(reason="forbidden_node", detail=type(node).__name__)

    return ValidatorOk()


def _check_select(
    select: exp.Select,
    *,
    cte_aliases: frozenset[str] | set[str] = frozenset(),
) -> ValidatorOutcome:
    """Per-Select-node checks: tables, columns, functions, windows.

    ``cte_aliases`` is the set of names declared by ``WITH`` clauses
    anywhere in the parent statement. References to these are allowed
    and not flagged as ``unknown_table``.
    """
    # Tables: only allowed_* views; no base tables; no sqlite_*; no unknowns.
    for table in select.find_all(exp.Table):
        name = table.name
        if name in BASE_TABLES:
            return ValidatorReject(reason="base_table_ref", detail=f"base_table_{name}")
        if name in SQLITE_INTERNAL:
            return ValidatorReject(reason="sqlite_internal", detail=name)
        if name in cte_aliases:
            continue  # CTE-declared alias — allowed
        if name not in ALLOWED_VIEWS:
            return ValidatorReject(reason="unknown_table", detail=name)

    # Columns: no rowid / oid / _rowid_ refs (P0-1 — global rowid leak).
    for col in select.find_all(exp.Column):
        if col.name.lower() in ROWID_REFS:
            return ValidatorReject(reason="rowid_reference_blocked", detail=col.name)

    # Functions: blocklist load_extension/readfile/writefile/etc.
    # sqlglot represents these as exp.Anonymous (unknown function names).
    for func in select.find_all(exp.Anonymous):
        if func.name.lower() in BLOCKED_FUNCTIONS:
            return ValidatorReject(reason="blocked_function", detail=func.name)

    # Window functions: reject if PARTITION BY / ORDER BY references a base table
    # (P0-5b — window can leak across dept if the partition key is unscoped).
    for window in select.find_all(exp.Window):
        for col in window.find_all(exp.Column):
            if col.table in BASE_TABLES:
                return ValidatorReject(reason="window_over_base_table", detail=col.sql())

    return ValidatorOk()
