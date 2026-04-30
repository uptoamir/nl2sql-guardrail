"""Typed exception hierarchy for the guardrail + agent layers.

Per `final_1.md` §41.1. Single source of truth for every typed exception
the agent + CLI can raise. Each exception carries a ``layer`` attribute
that maps to the 8-layer guardrail (§3) so audit logs and metrics can
attribute failures correctly.
"""

from __future__ import annotations

from dataclasses import dataclass


# ─── Guardrail-layer rejections ──────────────────────────────────────────────
class GuardrailError(Exception):
    """Base class for any guardrail-layer rejection or breach."""

    layer: str = "?"


class ValidationError(GuardrailError):
    """L3 — sqlglot AST validator rejected the SQL."""

    layer = "L3"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"L3 reject: {reason} {detail}".strip())
        self.reason = reason
        self.detail = detail


class GuardrailReject(GuardrailError):
    """L4 — rewriter rejected the SQL (e.g. dept conflict)."""

    layer = "L4"

    def __init__(self, reason: str) -> None:
        super().__init__(f"L4 reject: {reason}")
        self.reason = reason


class CostExceeded(GuardrailReject):
    """L7a — EXPLAIN QUERY PLAN cost gate flagged a runaway query."""

    layer = "L7a"


class GuardrailBreach(GuardrailError):
    """L7 — post-execution row check found a row from the wrong department.

    The keystone exception. SLO is 100.000% — any raise is P1.
    """

    layer = "L7b"

    def __init__(self, *, layer: str, count: int, indexes: list[int]) -> None:
        super().__init__(f"BREACH at {layer}: {count} stray row(s) at indexes {indexes[:5]}")
        self.breach_layer = layer
        self.count = count
        self.indexes = indexes


# ─── Configuration / scope rejections ────────────────────────────────────────
class InvalidScopeError(GuardrailError):
    """Raised by ``views.py`` if dept/workspace_id fails allow-list validation.

    This is the canonical catch for SQL-injection attempts via dept override.
    """


class UnsupportedSQLite(GuardrailError):
    """``sqlite_version_info < (3, 39, 0)`` — set_authorizer's
    ``trigger_or_view`` argument behavior is unsafe on older versions."""


class DatabaseIntegrityError(GuardrailError):
    """``PRAGMA quick_check`` (or ``integrity_check``) returned non-ok."""


class UnknownWorkspaceError(GuardrailError):
    """``workspace_id`` not in the tenant DB map.

    In production this is potentially attack reconnaissance — increments
    ``nl2sql_unknown_workspace_total`` and is logged at WARNING.
    """


class DataResidencyViolation(GuardrailError):
    """Workspace's region != current service region. PIPEDA / GDPR concern."""


# ─── Agent / LLM layer ───────────────────────────────────────────────────────
class BudgetExceeded(GuardrailError):
    """tiktoken pre-call estimate exceeded ``Settings.input_max``."""

    def __init__(self, estimated: int, allowed: int) -> None:
        super().__init__(f"prompt {estimated} tokens > budget {allowed}")
        self.estimated = estimated
        self.allowed = allowed


class ModelDriftError(GuardrailError):
    """``response.model != settings.expected_model`` after first LLM call.

    OpenAI rotates aliases silently; this catches drift early.
    """


class InvalidTokenError(Exception):
    """JWT validation failed. Production-only — v0 fixture path doesn't use JWTs."""


class ServiceUnavailableError(Exception):
    """Both LLM-provider circuit breakers open + semantic cache miss.

    Returned to API callers as HTTP 503 with ``Retry-After``.
    """

    def __init__(self, msg: str, retry_after_s: int = 60) -> None:
        super().__init__(msg)
        self.retry_after_s = retry_after_s


class AgentError(Exception):
    """Top-level agent-loop error. ``outcome`` distinguishes terminal states.

    outcome ∈ {"refused", "exhausted_repairs", "error", "llm_timeout", ...}
    """

    def __init__(self, outcome: str, **details: object) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.details = details


# ─── Sentinel result types (validator + result-audit) ────────────────────────
@dataclass(frozen=True, slots=True)
class ValidatorOk:
    """Returned by ``validate_sql`` when the SQL is acceptable."""


@dataclass(frozen=True, slots=True)
class ValidatorReject:
    """Returned by ``validate_sql`` when the SQL is rejected.

    Note: the validator NEVER raises on input — always returns a typed
    result. This is the property-test invariant in §19.3 / §41.9.
    """

    reason: str
    detail: str = ""


ValidatorOutcome = ValidatorOk | ValidatorReject


@dataclass(frozen=True, slots=True)
class AuditOk:
    """Returned by ``audit_rows`` when all rows match the expected dept
    (or there's no Department column to verify against)."""

    reason: str = "all_match"
