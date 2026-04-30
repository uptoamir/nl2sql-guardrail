"""Read-only SQLite connection management.

Per `final_1.md` §35.7 + §41.13 + B0-3/4/5/7. Implements the canonical
``open_session()`` ordering, **corrected** vs the plan after the
implementation surfaced a SQLite ordering constraint:

  1. ``sqlite3.connect(file:?mode=rw)``         (read-write file open
                                                  — query_only locks it
                                                  down at SQL layer)
  2. ``PRAGMA quick_check``                     (B2-4 — fast at any DB size)
  3. ``create_scope_views(...)``                (Layer 1 — TEMP VIEWs)
                                                  must run BEFORE query_only
  4. ``PRAGMA query_only = 1``                  (Layer 5 — locks SQL writes,
                                                  including future TEMP DDL)
  5. ``conn.set_authorizer(make_authorizer())`` (Layer 6 — engine kill-switch)

▲ DEVIATION from plan §35.7: plan opens with ``mode=ro`` from the start.
SQLite's ``mode=ro`` rejects ``CREATE TEMP VIEW`` because the executescript
implicit BEGIN-COMMIT can't acquire any write lock. The corrected order
above gets the same security guarantee (query_only=1 prevents all SQL
writes) without breaking TEMP VIEW creation. Documented in audit_doc.md
Task 2.

The introspection-only opener :func:`open_introspect_conn` keeps
``mode=ro`` since it doesn't need TEMP VIEWs.

Also provides :func:`execute_with_timeout` — a real wall-clock timeout
via ``threading.Timer`` (P0-3 fix; ``progress_handler`` was the broken
old approach since it counts VDBE instructions, not seconds).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from nl2sql.guardrails.errors import (
    DatabaseIntegrityError,
    UnsupportedSQLite,
)

# ─── SQLite version assertion (P0-4) ─────────────────────────────────────────
# set_authorizer's `trigger_or_view` argument behavior is version-dependent;
# 3.39 is the minimum where it's reliably populated.
_MIN_SQLITE = (3, 39, 0)
if sqlite3.sqlite_version_info < _MIN_SQLITE:  # pragma: no cover - import-time
    raise UnsupportedSQLite(
        f"SQLite {sqlite3.sqlite_version} < required {_MIN_SQLITE}; "
        f"set_authorizer's `trigger_or_view` argument is unsafe on older versions."
    )


def _quick_check(conn: sqlite3.Connection) -> None:
    """Run ``PRAGMA quick_check`` and raise on failure."""
    result = conn.execute("PRAGMA quick_check").fetchone()
    if tuple(result) != ("ok",):
        conn.close()
        raise DatabaseIntegrityError(f"DB quick_check failed: {tuple(result)}")


def open_introspect_conn(path: str | object) -> sqlite3.Connection:
    """Open a strict read-only connection for schema introspection only.

    Uses ``mode=ro`` to guarantee the file is opened read-only at the OS
    level — strongest possible read-only guarantee. Cannot create TEMP
    VIEWs (use :func:`open_session` for that).
    """
    conn = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    _quick_check(conn)
    return conn


# Backwards-compatible alias used by some code paths.
open_readonly = open_introspect_conn


def open_session(
    path: str | object,
    *,
    dept: str,
    workspace_id: str,
) -> sqlite3.Connection:
    """The ONLY function that opens an authorized agent session connection.

    Order matters and is unit-tested by ``tests/unit/test_db.py``. DO NOT
    reorder — every step is part of the 8-layer guardrail.

    Returns a connection that is:
      - locked to read-only at the SQL layer (``query_only=1``)
      - scoped to the (workspace, dept) tuple via TEMP VIEWs
      - clamped by the ``set_authorizer`` callback (Layer 6)
    """
    # 1: open RW (so we can create TEMP VIEWs); query_only will lock it
    conn = sqlite3.connect(
        f"file:{path}?mode=rw",
        uri=True,
        isolation_level=None,
        check_same_thread=False,  # see thread-safety note below
    )
    conn.row_factory = sqlite3.Row

    # 2: integrity check (B2-4 — quick variant; daily integrity_check is a cron)
    _quick_check(conn)

    # 3: TEMP VIEWs MUST be created BEFORE query_only is enabled
    from nl2sql.db.views import create_scope_views  # local import to break cycle

    create_scope_views(conn, dept=dept, workspace_id=workspace_id)

    # 4: lock SQL writes via query_only — even TEMP DDL is forbidden after this
    conn.execute("PRAGMA query_only = 1")

    # 5: install authorizer (Layer 6) — engine-level kill-switch
    #    Lazy import: Task 3 delivers the authorizer module. If it's missing
    #    raise a clear actionable error rather than a confusing ImportError
    #    deep in a stack trace.
    try:
        from nl2sql.guardrails.authorizer import (
            ALLOWED_VIEWS,
            make_authorizer,
        )
    except ImportError as e:  # pragma: no cover - only during early bootstrap
        raise RuntimeError(
            "nl2sql.guardrails.authorizer is not available — Layer 6 of the "
            "guardrail cannot be installed. Implement it (Task 3) before "
            "calling open_session()."
        ) from e

    conn.set_authorizer(make_authorizer(ALLOWED_VIEWS))
    return conn


# ─── Real wall-clock timeout (P0-3 + B2-2) ──────────────────────────────────
# `sqlite3.set_progress_handler` fires every N VDBE instructions, NOT N
# milliseconds — useless as a wall-clock timeout. Use threading.Timer +
# conn.interrupt() instead. interrupt() is documented as safe to call
# from another thread; execute() is not — hence the per-connection lock.
_connection_locks: dict[int, threading.Lock] = {}


def _conn_lock(conn: sqlite3.Connection) -> threading.Lock:
    """One re-entrant lock per Connection object id."""
    return _connection_locks.setdefault(id(conn), threading.Lock())


@contextmanager
def execute_with_timeout(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[object, ...] = (),
    timeout_s: float = 5.0,
) -> Iterator[sqlite3.Cursor]:
    """Execute ``sql`` with a real wall-clock timeout.

    Yields the cursor inside a context manager that holds the connection
    lock and arms a ``threading.Timer``. On timeout, ``conn.interrupt()``
    aborts the running statement; ``execute()`` then raises
    ``sqlite3.OperationalError("interrupted")``.
    """
    lock = _conn_lock(conn)
    timer = threading.Timer(timeout_s, conn.interrupt)
    timer.daemon = True
    timer.start()
    try:
        with lock:
            cursor = conn.execute(sql, params)
            yield cursor
    finally:
        timer.cancel()
