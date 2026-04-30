"""Guardrail layer.

Three independent layers, any one of which is sufficient to block a
cross-department leak. Defence in depth — if a future LLM upgrade or
prompt-injection lands a bypass through one layer, the others still catch it.

1. ``department.pick_department`` — startup-time random pick + override hygiene.
2. ``sql_validator.validate_sql`` — sqlglot AST inspection. No base-table
   reads, no DDL/DML, no ``sqlite_master`` probes, no compound bypasses.
3. ``authorizer.install_authorizer`` — SQLite ``set_authorizer`` callback that
   denies anything our SQL validator hypothetically missed at engine level.
"""
