"""Layer L6 of the 8-layer guardrail — SQLite ``set_authorizer`` callback.

Per `final_1.md` §3 + §3.3 (the showpiece). Engine-level kill-switch.
Independent of L3 (validator) and L4 (rewriter): even if the validator
is bypassed and the rewriter is monkey-patched, this callback denies
forbidden actions at the C level inside SQLite itself.

The 5th argument (``source``, also called ``trigger_or_view``) tells us
whether a base-table read is happening *via* one of our allowed views
— which is the ONLY case we permit reads of Employee / Certification /
Benefits.

▲ NOTE on SQLite version: this argument's semantics are version-dependent.
The :mod:`nl2sql.db.connection` module asserts ``sqlite_version >= 3.39.0``
at import time (P0-4).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

# Re-exported here so callers can import from a single place.
ALLOWED_VIEWS: frozenset[str] = frozenset(
    {"allowed_employees", "allowed_certifications", "allowed_benefits"}
)
BASE_TABLES: frozenset[str] = frozenset({"Employee", "Certification", "Benefits"})
SQLITE_INTERNAL_TABLES: frozenset[str] = frozenset(
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

AuthorizerCallback = Callable[[int, str | None, str | None, str | None, str | None], int]


def make_authorizer(allowed_views: frozenset[str] = ALLOWED_VIEWS) -> AuthorizerCallback:
    """Build a SQLite authorizer callback closed over ``allowed_views``.

    The returned callback follows SQLite's contract:
      - Return :data:`sqlite3.SQLITE_OK` to allow the action.
      - Return :data:`sqlite3.SQLITE_DENY` to abort the SQL statement
        with ``OperationalError("not authorized")``.
      - Return :data:`sqlite3.SQLITE_IGNORE` to skip silently (we never use it).

    The callback is invoked for every SQL action — table reads, function
    calls, transactions, etc. — *during* statement compilation, before
    any rows are touched.
    """
    # Snapshot the frozen set so closure can't be mutated by callers.
    allowed: frozenset[str] = frozenset(allowed_views)

    def authorize(
        action: int,
        arg1: str | None,
        arg2: str | None,
        dbname: str | None,  # noqa: ARG001 - SQLite passes; we don't filter on it in v0
        source: str | None,
    ) -> int:
        # ─── READ — the load-bearing check ──────────────────────────────
        if action == sqlite3.SQLITE_READ:
            # arg1 = table name, arg2 = column name, source = name of the
            # trigger/view through which this read is happening (None if direct).
            if arg1 in BASE_TABLES:
                # Reads of base tables are ONLY allowed when accessed via
                # one of our allowed_* views.
                return sqlite3.SQLITE_OK if source in allowed else sqlite3.SQLITE_DENY
            if arg1 in SQLITE_INTERNAL_TABLES:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        # ─── SELECT / TRANSACTION — straightforward allows ──────────────
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION}:
            return sqlite3.SQLITE_OK

        # ─── FUNCTION calls — blocklist ─────────────────────────────────
        if action == sqlite3.SQLITE_FUNCTION:
            # arg2 holds the function name; lower-case for case-insensitive match.
            if arg2 and arg2.lower() in BLOCKED_FUNCTIONS:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        # ─── Everything else: DDL, DML, ATTACH, PRAGMA, etc. — DENY ─────
        return sqlite3.SQLITE_DENY

    return authorize


__all__: list[str] = [
    "ALLOWED_VIEWS",
    "BASE_TABLES",
    "BLOCKED_FUNCTIONS",
    "SQLITE_INTERNAL_TABLES",
    "AuthorizerCallback",
    "make_authorizer",
]
