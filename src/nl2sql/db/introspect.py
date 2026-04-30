"""Schema introspection — read column metadata for prompt rendering.

Per `final_1.md` §5.1 + §17 file tree. Provides:

- :func:`introspect_schema` — pull table/column/FK metadata via PRAGMA,
  used by the prompt-rendering layer (the LLM sees only the
  ``allowed_*`` views' shape, never base tables).
- :func:`open_introspect_conn` — convenience opener used by
  :func:`bootstrap_shared` to build the SchemaGraph.

Returns plain dicts for easy YAML / JSON serialization.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# Re-export the canonical connection opener from `connection.py` so
# `bootstrap_shared` can import ``open_introspect_conn`` from either module.
from nl2sql.db.connection import open_introspect_conn

__all__ = ["introspect_schema", "open_introspect_conn"]


def introspect_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return ``{tables: {name: {columns: [...], foreign_keys: [...]}}}``."""
    out: dict[str, dict[str, Any]] = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        cols = [
            {
                "cid": row["cid"],
                "name": row["name"],
                "type": row["type"],
                "notnull": bool(row["notnull"]),
                "pk": bool(row["pk"]),
                "default": row["dflt_value"],
            }
            for row in conn.execute(f"PRAGMA table_info({name})").fetchall()
        ]
        fks = [
            {
                "from": row["from"],
                "to_table": row["table"],
                "to_column": row["to"],
            }
            for row in conn.execute(f"PRAGMA foreign_key_list({name})").fetchall()
        ]
        out[name] = {"columns": cols, "foreign_keys": fks}
    return {"tables": out}


def value_domain(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    max_distinct: int = 50,
) -> list[Any]:
    """For a low-cardinality column, return up to ``max_distinct`` distinct values.

    Used to seed the schema graph's ``domain_of`` edges so the prompt can
    show e.g. ``Department ∈ {Sales, Marketing, Engineering}``.

    Returns empty list if the column has > ``max_distinct`` distinct values.
    """
    # First check cardinality cheaply.
    (count,) = conn.execute(f"SELECT COUNT(DISTINCT {column}) FROM {table}").fetchone()
    if count > max_distinct:
        return []
    return [
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT {column} FROM {table} ORDER BY {column}"
        ).fetchall()
    ]
