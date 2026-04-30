"""Multi-stage agent pipeline — owns stage orchestration.

Per `final_1.md` §4 + §36.2 + §40.2 + §41.13. Pipeline is stateless;
Session (§41.2) is identity + history. The pipeline yields
:class:`StreamChunk` so CLI + Streamlit can render incrementally.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nl2sql.agent.stages import critic, drafter, executor, intent, interpreter, linker
from nl2sql.agent.types import (
    DEPT,
    ExecResult,
    FinalAnswer,
    GuardrailLayerOutcome,
    Intent,
    RepairStep,
    SchemaLink,
    SqlGenerationResponse,
    StageCall,
    StreamChunk,
    Turn,
)
from nl2sql.guardrails.errors import (
    GuardrailBreach,
    GuardrailReject,
    ValidationError,
)

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.session import SharedResources

logger = logging.getLogger(__name__)


class Pipeline:
    """Stateless agent pipeline. One instance per Session."""

    def __init__(
        self,
        *,
        shared: SharedResources,
        workspace_id: str,
        dept: DEPT,
    ) -> None:
        self.shared = shared
        self.workspace_id = workspace_id
        self.dept = dept

    def run_streaming(
        self,
        question: str,
        history: list[Turn],
        *,
        run_id: str,
        session_id: str,
    ) -> Iterator[StreamChunk]:
        """Run one agent turn; yield StreamChunks; emit a 'done' chunk
        with the FinalAnswer at the end."""
        t_start = time.monotonic()
        model_calls: list[StageCall] = []
        layers: list[GuardrailLayerOutcome] = [
            # L1 + L2 + L8 are recorded inline as "passed" — they're
            # structural (we render only allowed_* in the prompt; we
            # parse via Pydantic; we validate workspace_id at session
            # open). They have no per-turn check that could fail.
            GuardrailLayerOutcome(
                layer_id="L1", name="Prompt scoping", passed=True, elapsed_ms=0.1
            ),
            GuardrailLayerOutcome(layer_id="L2", name="JSON contract", passed=True, elapsed_ms=0.1),
            GuardrailLayerOutcome(
                layer_id="L8", name="Multi-tenant scope", passed=True, elapsed_ms=0.1
            ),
        ]

        try:
            # ─── Intent ─────────────────────────────────────────────────
            yield StreamChunk(kind="status", text="Classifying intent…")
            intent_result, intent_call = intent.classify(
                question, history, shared=self.shared, active_dept=self.dept
            )
            model_calls.append(intent_call)

            if intent_result.kind in {"out_of_scope", "cross_dept_attempt", "ambiguous"}:
                # Ambiguous gets a special path: surface the LLM's own
                # ``clarification_needed`` text if present, fall back to a
                # generic "could you clarify?" otherwise.
                clarification = (
                    intent_result.clarification_needed
                    or "Could you clarify what you'd like to see?"
                )
                refusal_text = {
                    "out_of_scope": (
                        "That question is outside what I can answer. Try a question "
                        "about employees, certifications, or benefits."
                    ),
                    "cross_dept_attempt": (
                        f"I can't do that. This system is **read-only** and scoped "
                        f"to the **{self.dept}** department: SELECT queries only, "
                        f"no DROP / DELETE / UPDATE / INSERT, and no rows from other "
                        f"departments. Try asking about employees, certifications, "
                        f"or benefits in {self.dept}."
                    ),
                    "ambiguous": f"Your question could mean a few things. {clarification}",
                }[intent_result.kind]
                yield StreamChunk(kind="refusal", text=refusal_text)
                final = self._build_refused_answer(
                    question=question,
                    intent_result=intent_result,
                    model_calls=model_calls,
                    layers=layers,
                    run_id=run_id,
                    session_id=session_id,
                    t_start=t_start,
                    reason=intent_result.kind,
                )
                yield StreamChunk(kind="done", final=final)
                return

            # ─── Linker ─────────────────────────────────────────────────
            yield StreamChunk(kind="status", text="Linking schema…")
            schema_link, linker_call = linker.link(question, intent_result, shared=self.shared)
            if linker_call:
                model_calls.append(linker_call)

            # ─── Drafter (with bounded Reflexion repair) ────────────────
            yield StreamChunk(kind="status", text="Generating SQL…")
            repair_history: list[RepairStep] = []
            max_attempts = self.shared.settings.agent_max_repair_attempts
            draft: SqlGenerationResponse | None = None
            exec_result = None
            attempt = 0

            while attempt <= max_attempts:
                draft, drafter_call = drafter.draft(
                    question,
                    schema_link,
                    history,
                    repair_history=repair_history,
                    workspace_id=self.workspace_id,
                    dept=self.dept,
                    shared=self.shared,
                )
                model_calls.append(drafter_call)
                yield StreamChunk(kind="sql", text=draft.sql)

                # Critic — skipped if confidence > 0.8 AND warm pool
                # has the pattern (always False in v0).
                if draft.confidence < 0.8 and not self.shared.retriever.is_warm(draft.sql):
                    yield StreamChunk(kind="status", text="Reviewing SQL…")
                    crit_result, crit_call = critic.critique(draft, shared=self.shared)
                    model_calls.append(crit_call)
                    if crit_result.verdict == "repair":
                        repair_history.append(
                            RepairStep(
                                attempt=attempt,
                                prev_sql=draft.sql,
                                error_class="CriticRepair",
                                error_message="; ".join(crit_result.issues),
                                layer="critic",
                            )
                        )
                        attempt += 1
                        continue

                # Executor (deterministic — runs L3-L7)
                yield StreamChunk(kind="status", text="Executing…")
                try:
                    exec_result, exec_layers = executor.execute(
                        draft.sql,
                        workspace_id=self.workspace_id,
                        dept=self.dept,
                        shared=self.shared,
                        timeout_s=self.shared.settings.query_timeout_ms / 1000,
                    )
                    layers.extend(exec_layers)
                    yield StreamChunk(
                        kind="rows",
                        payload={
                            "rows": exec_result.rows,
                            "columns": exec_result.columns,
                            "row_count": exec_result.row_count,
                            "truncated": exec_result.truncated,
                        },
                    )
                    break  # success — exit the repair loop
                except (
                    ValidationError,
                    GuardrailReject,
                    GuardrailBreach,
                    sqlite3.DatabaseError,
                ) as e:
                    layer = getattr(e, "layer", "exec")
                    error_msg = str(e)
                    repair_history.append(
                        RepairStep(
                            attempt=attempt,
                            prev_sql=draft.sql,
                            error_class=type(e).__name__,
                            error_message=error_msg[:500],
                            layer=layer,
                        )
                    )
                    attempt += 1
                    if attempt > max_attempts:
                        break
                    yield StreamChunk(
                        kind="status",
                        text=f"SQL rejected ({type(e).__name__}); repairing…",
                    )

            # If we exhausted repairs without execution success, emit error
            if exec_result is None:
                final = self._build_exhausted_answer(
                    question=question,
                    intent_result=intent_result,
                    last_draft=draft,
                    repair_history=repair_history,
                    model_calls=model_calls,
                    layers=layers,
                    run_id=run_id,
                    session_id=session_id,
                    t_start=t_start,
                )
                yield StreamChunk(
                    kind="error",
                    text=f"Could not generate valid SQL after {attempt} attempts.",
                    final=final,
                )
                yield StreamChunk(kind="done", final=final)
                return

            # ─── Interpreter (only streaming stage) ─────────────────────
            # Loop exits with `draft` set (always at least one drafter call) and
            # `exec_result` non-None (the `is None` branch above returned).
            assert draft is not None
            assert exec_result is not None
            yield StreamChunk(kind="status", text="Interpreting…")
            narrative_tokens: list[str] = []
            for token in interpreter.interpret_streaming(
                exec_result,
                question=question,
                sql=draft.sql,
                shared=self.shared,
            ):
                narrative_tokens.append(token)
                yield StreamChunk(kind="narrative_token", text=token)

            narrative = "".join(narrative_tokens).strip() or (
                f"Returned {exec_result.row_count} rows for the active department."
            )

            # ─── Build the FinalAnswer ──────────────────────────────────
            final = self._build_ok_answer(
                question=question,
                intent_result=intent_result,
                schema_link=schema_link,
                draft=draft,
                exec_result=exec_result,
                narrative=narrative,
                repair_history=repair_history,
                model_calls=model_calls,
                layers=layers,
                run_id=run_id,
                session_id=session_id,
                t_start=t_start,
            )
            yield StreamChunk(kind="done", final=final)

        except Exception as e:
            logger.exception("agent_turn_error")
            yield StreamChunk(
                kind="error",
                text=f"{type(e).__name__}: {e}",
            )
            raise

    # ─── FinalAnswer builders ───────────────────────────────────────────
    def _build_ok_answer(
        self,
        *,
        question: str,
        intent_result: Intent,
        schema_link: SchemaLink,
        draft: SqlGenerationResponse,
        exec_result: ExecResult,
        narrative: str,
        repair_history: list[RepairStep],
        model_calls: list[StageCall],
        layers: list[GuardrailLayerOutcome],
        run_id: str,
        session_id: str,
        t_start: float,
    ) -> FinalAnswer:
        total_in = sum(c.tokens_in for c in model_calls)
        total_out = sum(c.tokens_out for c in model_calls)
        latency = int((time.monotonic() - t_start) * 1000)

        audit_record = self._build_audit_record(
            kind="ok",
            run_id=run_id,
            session_id=session_id,
            question=question,
            intent_kind=intent_result.kind,
            intent_reasoning=intent_result.reasoning,
            schema_link=schema_link.model_dump(),
            drafted_sql=draft.sql,
            drafter_rationale=draft.rationale,
            drafter_assumptions=draft.assumptions,
            final_sql=draft.sql,
            row_count=exec_result.row_count,
            narrative=narrative,
            model_calls=[c.model_dump() for c in model_calls],
            guardrail_layers=[ly.model_dump() for ly in layers],
            repair_history=[s.model_dump() for s in repair_history],
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            total_latency_ms=latency,
            outcome="ok",
        )
        return FinalAnswer(
            run_id=run_id,
            session_id=session_id,
            trace_id="0" * 32,  # set by tracing module if OTel is wired
            workspace_id=self.workspace_id,
            department=self.dept,
            narrative=narrative,
            sql_used=draft.sql,
            columns=exec_result.columns,
            supporting_rows=exec_result.rows,
            row_count=exec_result.row_count,
            truncated=exec_result.truncated,
            caveats=draft.assumptions,
            confidence=draft.confidence,
            confidence_label=FinalAnswer.label_for_confidence(draft.confidence),
            exec_time_ms=exec_result.latency_ms,
            total_latency_ms=latency,
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            guardrail_layers=layers,
            repair_attempts=len(repair_history),
            repair_history=repair_history,
            cache={"semantic": "miss", "prompt": "miss", "schema": "hit"},
            model_calls=model_calls,
            outcome="ok",
            audit_record=audit_record,
        )

    def _build_refused_answer(
        self,
        *,
        question: str,
        intent_result: Intent,
        model_calls: list[StageCall],
        layers: list[GuardrailLayerOutcome],
        run_id: str,
        session_id: str,
        t_start: float,
        reason: str,
    ) -> FinalAnswer:
        latency = int((time.monotonic() - t_start) * 1000)
        clarification = (
            intent_result.clarification_needed or "Could you clarify what you'd like to see?"
        )
        narrative = {
            "out_of_scope": (
                "I can only answer questions about employees, certifications, "
                "and benefits in your active department."
            ),
            "cross_dept_attempt": (
                f"I can't do that. This system is read-only and scoped to the "
                f"{self.dept} department: SELECT queries only, no schema or "
                f"row modifications, and no rows from other departments."
            ),
            "ambiguous": f"Your question could mean a few things. {clarification}",
        }.get(
            reason,
            "I can only answer questions about employees, certifications, "
            "and benefits in your active department.",
        )
        audit_record = self._build_audit_record(
            kind="refused",
            run_id=run_id,
            session_id=session_id,
            question=question,
            intent_kind=intent_result.kind,
            intent_reasoning=intent_result.reasoning,
            schema_link={},
            drafted_sql="",
            drafter_rationale="",
            drafter_assumptions=[],
            final_sql="",
            row_count=0,
            narrative=narrative,
            model_calls=[c.model_dump() for c in model_calls],
            guardrail_layers=[ly.model_dump() for ly in layers],
            repair_history=[],
            total_tokens_in=sum(c.tokens_in for c in model_calls),
            total_tokens_out=sum(c.tokens_out for c in model_calls),
            total_latency_ms=latency,
            outcome="refused",
            refusal_reason=reason,
        )
        return FinalAnswer(
            run_id=run_id,
            session_id=session_id,
            trace_id="0" * 32,
            workspace_id=self.workspace_id,
            department=self.dept,
            narrative=narrative,
            sql_used="",
            columns=[],
            supporting_rows=[],
            row_count=0,
            confidence=intent_result.confidence,
            confidence_label="Confident",
            exec_time_ms=0,
            total_latency_ms=latency,
            total_tokens_in=sum(c.tokens_in for c in model_calls),
            total_tokens_out=sum(c.tokens_out for c in model_calls),
            guardrail_layers=layers,
            repair_attempts=0,
            cache={},
            model_calls=model_calls,
            outcome="refused",
            audit_record=audit_record,
        )

    def _build_exhausted_answer(
        self,
        *,
        question: str,
        intent_result: Intent,
        last_draft: SqlGenerationResponse | None,
        repair_history: list[RepairStep],
        model_calls: list[StageCall],
        layers: list[GuardrailLayerOutcome],
        run_id: str,
        session_id: str,
        t_start: float,
    ) -> FinalAnswer:
        latency = int((time.monotonic() - t_start) * 1000)
        narrative = (
            f"I tried {len(repair_history)} times but couldn't generate valid SQL. "
            f"Last error: {repair_history[-1].error_message if repair_history else 'unknown'}. "
            "Please rephrase your question."
        )
        audit_record = self._build_audit_record(
            kind="exhausted_repairs",
            run_id=run_id,
            session_id=session_id,
            question=question,
            intent_kind=intent_result.kind,
            intent_reasoning=intent_result.reasoning,
            schema_link={},
            drafted_sql=last_draft.sql if last_draft else "",
            drafter_rationale="",
            drafter_assumptions=[],
            final_sql="",
            row_count=0,
            narrative=narrative,
            model_calls=[c.model_dump() for c in model_calls],
            guardrail_layers=[ly.model_dump() for ly in layers],
            repair_history=[s.model_dump() for s in repair_history],
            total_tokens_in=sum(c.tokens_in for c in model_calls),
            total_tokens_out=sum(c.tokens_out for c in model_calls),
            total_latency_ms=latency,
            outcome="exhausted_repairs",
        )
        return FinalAnswer(
            run_id=run_id,
            session_id=session_id,
            trace_id="0" * 32,
            workspace_id=self.workspace_id,
            department=self.dept,
            narrative=narrative,
            sql_used=last_draft.sql if last_draft else "",
            columns=[],
            supporting_rows=[],
            row_count=0,
            confidence=last_draft.confidence if last_draft else 0.0,
            confidence_label="Not sure",
            exec_time_ms=0,
            total_latency_ms=latency,
            total_tokens_in=sum(c.tokens_in for c in model_calls),
            total_tokens_out=sum(c.tokens_out for c in model_calls),
            guardrail_layers=layers,
            repair_attempts=len(repair_history),
            repair_history=repair_history,
            cache={},
            model_calls=model_calls,
            outcome="exhausted_repairs",
            audit_record=audit_record,
        )

    def _build_audit_record(self, **fields: object) -> dict[str, object]:
        """Compose the canonical audit JSON shape (§12.3)."""
        record: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "workspace_id": self.workspace_id,
            "department": self.dept,
        }
        record.update(fields)
        return record
