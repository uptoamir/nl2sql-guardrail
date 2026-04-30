"""Database layer.

Per `final_1.md` §3.2 + §35.7 + §41.13. Provides:

- :func:`open_session` — the canonical (and only) function to open an
  authorized agent session connection. Order matters: RO → quick_check →
  TEMP VIEWs → set_authorizer.
- :func:`create_scope_views` — TEMP VIEW DDL with strict allow-list +
  workspace-id validation. The SQL-injection surface (P0-2).
- :func:`introspect_schema` — read-only metadata extraction for prompt
  rendering.
"""
