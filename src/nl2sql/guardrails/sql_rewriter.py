"""Layer L4 of the 8-layer guardrail — defensive AST rewriter.

Per `final_1.md` §3.3 + §35.3 (P0-5 shape coverage) + §40.2 (P0-6 EXISTS).

The validator (L3) rejects any reference to base tables, so in normal
operation the rewriter is a no-op — it walks the AST and finds nothing
to inject. Its job is to be the **defensive fallback**: if anything
upstream missed a base-table reference, the rewriter still scopes it
correctly before execution.

Two non-trivial cases beyond the trivial WHERE injection:

1. **Orphan Cert/Benefits queries** (P0-6): a query like
   ``SELECT * FROM Certification`` doesn't mention ``Employee`` at all,
   so a naive ``WHERE Department=?`` injector has nothing to attach to.
   The rewriter injects ``EXISTS (SELECT 1 FROM allowed_employees ae
   WHERE ae.EmployeeId = Certification.EmployeeId)`` — index-friendly at
   warehouse scale.

2. **Self-joins on Employee**: each alias gets its own filter.

Also handles UNION / INTERSECT / EXCEPT (each branch is its own Select),
correlated subqueries (each Select walked independently), and CTEs.

Plus :func:`ensure_limit` injects ``LIMIT N`` if absent (cap at
``settings.result_row_limit``).
"""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp

from nl2sql.guardrails.errors import GuardrailReject

ALLOWED_VIEWS_EMP: frozenset[str] = frozenset({"allowed_employees"})
BASE_EMP = "Employee"
BASE_CERT = "Certification"
BASE_BEN = "Benefits"


def rewrite_sql(
    sql: str,
    *,
    dept: str,
    row_limit: int = 100,
) -> tuple[str, tuple[object, ...]]:
    """Rewrite ``sql`` to enforce the active-department scope.

    Returns ``(rewritten_sql, params)``. Parameters are SQLite-style
    ``?`` placeholders — values are in ``params``, never interpolated.

    Raises :class:`GuardrailReject` if the input contains a literal
    ``WHERE Department='Sales'`` clause that conflicts with ``dept``.
    """
    ast = sqlglot.parse_one(sql, dialect="sqlite")
    params: list[object] = []

    # Reject obvious dept-conflict literals BEFORE rewriting so the user
    # sees a clear error rather than a silent overwrite.
    _reject_dept_conflict(ast, dept)

    # Walk every Select node — this includes subqueries, CTEs, UNION/
    # INTERSECT/EXCEPT branches, correlated subqueries.
    for select in ast.find_all(exp.Select):
        emp_aliases = _aliases_for_table(select, BASE_EMP)
        cert_aliases = _aliases_for_table(select, BASE_CERT)
        ben_aliases = _aliases_for_table(select, BASE_BEN)

        # Standard case: any base-Employee reference gets WHERE Department=?
        # injected for that alias.
        for alias in emp_aliases:
            _and_merge_dept_filter(select, alias=alias, dept=dept)
            params.append(dept)

        # Orphan case (P0-6): Cert/Benefits without an Employee join.
        # Inject EXISTS (SELECT 1 FROM allowed_employees ae WHERE ae.EmployeeId
        # = <alias>.EmployeeId). This re-routes the read through the dept-scoped
        # view at the engine level, with an EXPLAIN-friendly plan shape.
        if not emp_aliases:
            for alias in cert_aliases + ben_aliases:
                exists_clause = sqlglot.parse_one(
                    f"EXISTS (SELECT 1 FROM allowed_employees ae "
                    f"WHERE ae.EmployeeId = {alias}.EmployeeId)",
                    dialect="sqlite",
                )
                _and_merge(select, exists_clause)

    # ensure_limit on the outermost statement
    ensure_limit(ast, cap=row_limit)

    return ast.sql(dialect="sqlite"), tuple(params)


def ensure_limit(ast: exp.Expression, *, cap: int = 100) -> None:
    """Ensure the outermost statement has ``LIMIT cap`` if it has none.

    If a LIMIT is already present, it's kept (don't clobber user intent).
    Modifies ``ast`` in place.
    """
    # set-operations don't accept LIMIT directly — wrap if needed.
    if isinstance(ast, exp.SetOperation) and ast.args.get("limit") is None:
        ast.set("limit", exp.Limit(expression=exp.Literal.number(cap)))
        return
    if isinstance(ast, exp.Select) and ast.args.get("limit") is None:
        ast.set("limit", exp.Limit(expression=exp.Literal.number(cap)))


def _aliases_for_table(select: exp.Select, table_name: str) -> list[str]:
    """Find every reference to ``table_name`` in this Select; return their
    aliases (or table name itself if no alias)."""
    aliases: list[str] = []
    for table in select.find_all(exp.Table):
        if table.name == table_name:
            aliases.append(table.alias or table.name)
    return aliases


def _and_merge(select: exp.Select, clause: exp.Expression) -> None:
    """AND-merge ``clause`` into ``select``'s WHERE."""
    existing = select.args.get("where")
    if existing is None:
        select.set("where", exp.Where(this=clause))
    else:
        select.set(
            "where",
            exp.Where(this=exp.And(this=existing.this, expression=clause)),
        )


def _and_merge_dept_filter(
    select: exp.Select,
    *,
    alias: str,
    dept: str,  # noqa: ARG001 - dept goes into params, not the SQL string
) -> None:
    """AND-merge ``WHERE <alias>.Department = ?`` into the Select's WHERE."""
    placeholder = exp.Placeholder()
    column = exp.Column(this=exp.Identifier(this="Department"), table=exp.Identifier(this=alias))
    eq_clause = exp.EQ(this=column, expression=placeholder)
    _and_merge(select, eq_clause)


def _reject_dept_conflict(ast: exp.Expression, dept: str) -> None:
    """Raise GuardrailReject if the input has a literal Department='X' that
    differs from ``dept``. Prevents a silent overwrite where the user
    asked for one dept and we returned another."""
    for eq in ast.find_all(exp.EQ):
        col = eq.this
        if not isinstance(col, exp.Column):
            continue
        if col.name != "Department":
            continue
        rhs = eq.expression
        if isinstance(rhs, exp.Literal) and rhs.is_string and rhs.this != dept:
            raise GuardrailReject(
                f"dept_conflict: query asks for Department={rhs.this!r} "
                f"but active scope is {dept!r}"
            )
