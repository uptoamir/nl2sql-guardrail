"""Pydantic types — canonical handoff schemas for every agent stage.

Per `final_1.md` §35.2 + §41.9 (StageCall canonical) + §41.10 (schema_link
shape) + §40.2 (FinalAnswer expanded for UI consumers) + §41.13 (StreamChunk).

These types are the *contract* between stages and between the agent and
its consumers (CLI, Streamlit UI, FastAPI, eval harness). Every reference
elsewhere in the codebase resolves to one of these definitions.
"""

from __future__ import annotations

import re
from collections.abc import Iterator  # noqa: F401  (re-exported for type hints)
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.config import Department

# ─── Constants used across modules (from §41.1) ──────────────────────────────
DEPT = Department  # alias — published name used by the plan
ALLOWED_DEPARTMENTS: frozenset[str] = frozenset({"Sales", "Marketing", "Engineering"})

UUID_RE: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ─── Stage 1: Intent classifier ──────────────────────────────────────────────
class Intent(BaseModel):
    """Output of the Intent stage — what kind of question is this?"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "data_query",
        "schema_question",
        "out_of_scope",
        "cross_dept_attempt",
        "ambiguous",
        "follow_up",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    clarification_needed: str | None = None
    follow_up_resolves_to: str | None = None  # for "their certs" → "X's certs"


# ─── Stage 2: Schema linker ──────────────────────────────────────────────────
class ColumnRef(BaseModel):
    """A column reference inside a SchemaLink."""

    model_config = ConfigDict(extra="forbid")

    table: str
    name: str


class JoinHint(BaseModel):
    """A join the linker thinks the drafter should use."""

    model_config = ConfigDict(extra="forbid")

    left: ColumnRef
    right: ColumnRef
    kind: Literal["INNER", "LEFT", "FULL"] = "INNER"


class SchemaLink(BaseModel):
    """Output of the Schema-linker stage."""

    model_config = ConfigDict(extra="forbid")

    tables: list[str]
    columns: list[ColumnRef]
    joins: list[JoinHint] = Field(default_factory=list)
    filters_implied: list[str] = Field(default_factory=list)


# ─── Stage 3: Drafter (the LLM contract — Layer 2 of the guardrail) ──────────
class SqlGenerationResponse(BaseModel):
    """The LLM response contract. ``extra="forbid"`` is what makes Layer 2
    of the guardrail real — any unexpected key from the model breaks parsing
    and triggers the repair loop.
    """

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=4000)
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)


# ─── Stage 4: Critic ─────────────────────────────────────────────────────────
class CritiqueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["ship", "repair"]
    issues: list[str] = Field(default_factory=list)
    suggested_sql: str | None = None
    explain_plan_summary: str


# ─── Stage 5: Executor (deterministic — no LLM) ──────────────────────────────
class ExecResult(BaseModel):
    """Deterministic output of the executor stage. No LLM involved."""

    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    truncated: bool
    latency_ms: int
    redacted: bool = False


# ─── Stage 6: Interpreter (only streaming stage) ─────────────────────────────
class InterpreterResponse(BaseModel):
    """Output of the Interpreter stage — natural-language answer over rows."""

    model_config = ConfigDict(extra="forbid")

    narrative: str
    caveats: list[str] = Field(default_factory=list)


# ─── Telemetry types (per §41.9 canonical StageCall + §35.2 layer outcomes) ──
class StageCall(BaseModel):
    """Per-LLM-call telemetry. One per agent-turn × stage that uses LLM.

    Stored in :attr:`FinalAnswer.model_calls` and ``audit_record["model_calls"]``.
    Captures everything we need for ``/tokens``, ``/cost``, ``/prompt`` slash
    commands and downstream cost dashboards.
    """

    model_config = ConfigDict(extra="forbid")

    stage: Literal["intent", "linker", "drafter", "critic", "interpreter"]
    model: str
    tokens_in: int
    tokens_out: int
    cached_tokens: int = 0  # OpenAI prompt-cache hits (P0-G monitoring)
    latency_ms: int
    openai_processing_ms: int | None = None  # x-openai-processing-ms header
    llm_request_id: str | None = None  # x-request-id header (P1-2)
    rendered_prompt: str | None = None  # captured for /prompt (§41.9)


class GuardrailLayerOutcome(BaseModel):
    """Per-layer pass/fail trace for the 8 guardrail layers (§3)."""

    model_config = ConfigDict(extra="forbid")

    layer_id: Literal["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
    name: str
    passed: bool
    reason: str | None = None  # set when passed=False
    elapsed_ms: float


class RepairStep(BaseModel):
    """One iteration of the Reflexion repair loop."""

    model_config = ConfigDict(extra="forbid")

    attempt: int
    prev_sql: str
    error_class: str
    error_message: str
    layer: str  # "L3" / "L4" / "L6" / "L7" / "db" / "llm"


# ─── The canonical FinalAnswer (per §40.2 — every UI consumer reads this) ────
class FinalAnswer(BaseModel):
    """Structured response returned to every consumer (CLI, UI, FastAPI, eval).

    Replaces all earlier stub definitions. Every field is consumed by at least
    one rendering path; nothing here is decorative.
    """

    model_config = ConfigDict(extra="forbid")

    # — Identifiers —
    run_id: str
    session_id: str
    trace_id: str  # OTel root-span trace_id hex
    workspace_id: str
    department: DEPT
    user_id: str | None = None  # JWT sub claim; None for v0 fixture

    # — Core answer —
    narrative: str
    sql_used: str
    columns: list[str]
    supporting_rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False
    caveats: list[str] = Field(default_factory=list)

    # — Confidence (UI-friendly label + raw float) —
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_label: Literal["Confident", "Best guess", "Not sure"]

    # — Timing —
    exec_time_ms: int  # SQL execution only
    total_latency_ms: int  # end-to-end agent.turn

    # — Tokens —
    total_tokens_in: int
    total_tokens_out: int

    # — Guardrail trace (THE Streamlit demo showpiece) —
    guardrail_layers: list[GuardrailLayerOutcome]  # all 8 layers

    # — Repair history —
    repair_attempts: int = 0
    repair_history: list[RepairStep] = Field(default_factory=list)

    # — Cache state —
    cache: dict[str, Literal["hit", "miss", "not_initialized"]] = Field(default_factory=dict)

    # — Per-stage telemetry —
    model_calls: list[StageCall] = Field(default_factory=list)

    # — Outcome —
    outcome: Literal["ok", "refused", "exhausted_repairs", "error"]

    # — Full audit record (consumed by /trace + audit.jsonl emission) —
    audit_record: dict[str, Any]

    # ─── Convenience properties ──────────────────────────────────────────────
    @classmethod
    def label_for_confidence(cls, c: float) -> Literal["Confident", "Best guess", "Not sure"]:
        if c >= 0.8:
            return "Confident"
        if c >= 0.5:
            return "Best guess"
        return "Not sure"

    @property
    def guardrail_layers_passed(self) -> list[str]:
        return [g.layer_id for g in self.guardrail_layers if g.passed]

    @property
    def guardrail_layers_rejected(self) -> list[str]:
        return [g.layer_id for g in self.guardrail_layers if not g.passed]


# ─── Conversation history (per §35.1) ────────────────────────────────────────
class Turn(BaseModel):
    """One turn appended to ``Session.history`` (deque maxlen=3)."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    question: str
    sql_used: str | None = None
    row_count: int = 0
    brief_result_summary: str = ""  # 1-line for prompt re-rendering
    intent_kind: str
    outcome: Literal["ok", "refused", "exhausted_repairs", "error"]
    audit_record: dict[str, Any] | None = None


# ─── Streaming chunks (per §41.13 — `kind="refusal"` distinct from "error") ──
@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One chunk yielded by ``Session.ask_streaming()``.

    Streamlit's streaming consumer (§35.4) and the CLI Live region (§37.4)
    both consume this same shape. Stages run synchronously between chunks;
    only the interpreter stage yields token-by-token.
    """

    kind: Literal[
        "status",  # progress text (e.g. "Generating SQL…")
        "sql",  # generated SQL (code block)
        "rows",  # execution result (dataframe)
        "narrative_token",  # interpreter streaming
        "refusal",  # out_of_scope / authority refusal (info, not error)
        "error",  # actual error (red banner)
        "done",  # final answer ready
    ]
    text: str | None = None
    payload: object | None = None
    final: FinalAnswer | None = None  # only on kind="done"


# ─── Repair loop result (per §7.1) ───────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class GiveUpReason:
    attempted: int
    last_error: RepairStep


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    success: bool
    repair_history: list[RepairStep]
    draft: SqlGenerationResponse | None = None
    give_up: GiveUpReason | None = None
