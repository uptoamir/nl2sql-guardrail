"""Per-(workspace, dept) TEMP VIEWs — Layer 1 of the 8-layer guardrail.

Per `final_1.md` §3.2 (P0-2 SQL-injection-surface fix) + §3 L1.

The TEMP VIEWs are how the prompt only ever describes ``allowed_*`` and
never the base tables. Combined with :mod:`nl2sql.guardrails.authorizer`
(L6 set_authorizer callback) the database engine itself denies any read
of base tables that doesn't go through one of these views.

★ The ``dept`` parameter IS the SQL-injection surface. It's interpolated
into DDL as a string literal. **Validate against the allow-list
IMMEDIATELY before string-formatting** — this is the load-bearing check.
DO NOT relax it.
"""

from __future__ import annotations

import re
import sqlite3

from nl2sql.guardrails.errors import InvalidScopeError

# ─── Allow-list constants ────────────────────────────────────────────────────
ALLOWED_DEPARTMENTS: frozenset[str] = frozenset({"Sales", "Marketing", "Engineering"})

ALLOWED_VIEWS: frozenset[str] = frozenset(
    {
        "allowed_employees",
        "allowed_certifications",
        "allowed_benefits",
    }
)

BASE_TABLES: frozenset[str] = frozenset({"Employee", "Certification", "Benefits"})


# ─── workspace_id validator (UUID or fixture slug) ───────────────────────────
# Plan §3.2 originally required strict UUIDv4. The v0 fixture path uses
# ``local-fixture`` (per §10.3) which isn't a UUID. Accept both:
# - UUID format (production)
# - Slug format ``[a-z][a-z0-9-]{2,63}`` (v0 fixtures)
# Either way, only safe characters — no injection surface.
_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SLUG_PATTERN = r"[a-z][a-z0-9-]{2,63}"
_VALID_WORKSPACE_RE = re.compile(
    rf"^({_UUID_PATTERN}|{_SLUG_PATTERN})$",
    re.IGNORECASE,
)


def validate_workspace_id(workspace_id: str) -> None:
    """Raise :class:`InvalidScopeError` if ``workspace_id`` is unsafe.

    Even though ``workspace_id`` isn't currently interpolated into DDL,
    we validate it defensively — future code paths might use it for log
    field values, file paths, or attribute names.
    """
    if not isinstance(workspace_id, str) or not _VALID_WORKSPACE_RE.fullmatch(workspace_id):
        raise InvalidScopeError(f"workspace_id is not a UUID or safe slug: {workspace_id!r}")


def validate_department(dept: str) -> None:
    """Raise :class:`InvalidScopeError` if ``dept`` is not in the allow-list."""
    if dept not in ALLOWED_DEPARTMENTS:
        raise InvalidScopeError(f"dept must be one of {sorted(ALLOWED_DEPARTMENTS)}, got {dept!r}")


# ─── TEMP VIEW DDL ──────────────────────────────────────────────────────────
def create_scope_views(
    conn: sqlite3.Connection,
    *,
    dept: str,
    workspace_id: str,
) -> None:
    """Create the three per-(workspace, dept) TEMP VIEWs.

    MUST be called before :func:`install_authorizer` — once the authorizer
    is installed, ``CREATE VIEW`` is denied. See ``open_session`` for the
    canonical ordering.

    Explicit projections (no ``SELECT *``) — and rowid is omitted on
    purpose (P0-1). Without this, ``SELECT rowid FROM allowed_employees``
    would leak the global Employee.rowid across departments.
    """
    validate_department(dept)
    validate_workspace_id(workspace_id)

    # ★ At this point dept is one of the 3 hardcoded allow-list values, so
    # interpolating it into the DDL string is safe. The earlier validation
    # is the load-bearing check.
    conn.executescript(f"""
        CREATE TEMP VIEW allowed_employees AS
            SELECT EmployeeId, Name, Department, Role,
                   EmploymentStartDate, SalaryAmount, YearlyBonusAmount
            FROM Employee
            WHERE Department = '{dept}';

        CREATE TEMP VIEW allowed_certifications AS
            SELECT c.CertificationId, c.EmployeeId,
                   c.CertificationName, c.DateAchieved
            FROM Certification c
            INNER JOIN Employee e ON e.EmployeeId = c.EmployeeId
            WHERE e.Department = '{dept}';

        CREATE TEMP VIEW allowed_benefits AS
            SELECT b.BenefitId, b.EmployeeId,
                   b.BenefitsPackage, b.RemainingBalance
            FROM Benefits b
            INNER JOIN Employee e ON e.EmployeeId = b.EmployeeId
            WHERE e.Department = '{dept}';
    """)
