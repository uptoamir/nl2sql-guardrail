"""Prometheus metric definitions — single source of truth.

Per `final_1.md` §12.2 + §41.15. Every ``metrics.X`` reference elsewhere
in the codebase must resolve to one of these names; a CI test
(``tests/unit/test_metrics_defined.py`` per §41.15) enforces it.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ─── Core agent metrics ─────────────────────────────────────────────────────
TURNS_TOTAL = Counter(
    "nl2sql_turns_total",
    "Total agent turns",
    ["workspace_id_bucket", "dept", "outcome"],
)

TURN_DURATION_SECONDS = Histogram(
    "nl2sql_turn_duration_seconds",
    "End-to-end turn latency by stage",
    ["dept", "stage"],
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30),
)

TOKENS_TOTAL = Counter(
    "nl2sql_tokens_total",
    "LLM tokens consumed",
    ["stage", "model", "direction"],
)

# ─── Guardrail metrics ─────────────────────────────────────────────────────
GUARDRAIL_REJECTS_TOTAL = Counter(
    "nl2sql_guardrail_rejects_total",
    "Guardrail rejections by layer",
    ["layer", "reason"],
)

# THE keystone metric. SLO 100.000% — any non-zero is a P1 PagerDuty page.
GUARDRAIL_BREACHES_TOTAL = Counter(
    "nl2sql_guardrail_breaches_total",
    "Guardrail breaches — MUST stay 0; PagerDuty alert at >0",
    ["layer", "workspace_id"],
)

# ─── Repair / cache / cost / drift ─────────────────────────────────────────
AGENT_REPAIRS_TOTAL = Counter(
    "nl2sql_agent_repairs_total",
    "Repair-loop attempts",
    ["outcome", "attempts"],
)

CACHE_HITS_TOTAL = Counter("nl2sql_cache_hits_total", "Cache hits", ["cache"])
CACHE_MISSES_TOTAL = Counter("nl2sql_cache_misses_total", "Cache misses", ["cache"])

PROVIDER_FALLBACKS = Counter(
    "nl2sql_provider_fallbacks_total",
    "Times we failed over from primary to secondary LLM",
    ["reason"],
)
DUAL_PROVIDER_OUTAGE = Counter(
    "nl2sql_dual_provider_outage_total",
    "Both LLM providers' breakers open simultaneously",
    [],
)

MODEL_DRIFT_TOTAL = Counter(
    "nl2sql_model_drift_total",
    "LLM returned a different model than requested",
    ["expected", "actual"],
)

BUDGET_ABORTS = Counter(
    "nl2sql_budget_aborts_total",
    "Token-budget pre-call aborts",
    ["reason"],
)

# ─── Session lifecycle ────────────────────────────────────────────────────
ACTIVE_SESSIONS = Gauge(
    "nl2sql_active_sessions",
    "Currently open Sessions",
    ["workspace_id_bucket"],
)

UNKNOWN_WORKSPACE_TOTAL = Counter(
    "nl2sql_unknown_workspace_total",
    "Requests for workspace_id we don't know about",
    ["workspace_id"],
)
TENANT_LOOKUP_FAILS = Counter(
    "nl2sql_tenant_lookup_fails_total",
    "Failures resolving workspace_id → tenant DB path",
    ["workspace_id"],
)

# ─── Mock / suspected-leak / SCIM / residency / retention (per §41.15) ─────
MOCK_HITS = Counter("nl2sql_mock_hits_total", "Canned-response hits for mock LLM", [])
MOCK_MISSES = Counter(
    "nl2sql_mock_misses_total",
    "Mock fallthrough — question not in canned set",
    [],
)

SUSPECTED_CACHE_LEAK = Counter(
    "nl2sql_suspected_cache_leak_total",
    "First-turn-of-workspace had cached_tokens > 0 — possible cross-tenant",
    ["workspace_id"],
)

JWT_REJECT_TOTAL = Counter(
    "nl2sql_jwt_rejects_total",
    "JWT validation rejections by reason",
    ["reason"],
)
SCIM_DEPROVISIONS_TOTAL = Counter(
    "nl2sql_scim_deprovisions_total",
    "SCIM PATCH active=false events",
    [],
)

RESIDENCY_VIOLATION_ATTEMPTS = Counter(
    "nl2sql_residency_violation_attempts_total",
    "Workspace requests routed to wrong-region service",
    ["expected_region", "current_region"],
)
RETENTION_VIOLATION_TOTAL = Counter(
    "nl2sql_retention_violation_total",
    "Quarterly cron found objects older than 7y+30d",
    ["bucket"],
)

DB_INTEGRITY_CHECK_TOTAL = Counter(
    "nl2sql_db_integrity_check_total",
    "Daily integrity_check cron outcome",
    ["outcome"],
)

LLM_ERRORS_TOTAL = Counter("nl2sql_llm_errors_total", "LLM API errors by kind", ["model", "kind"])
