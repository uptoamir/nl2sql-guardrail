"""Critic — stage 4 of the agent pipeline (Reflexion-style verifier).

Per `final_1.md` §4.1 + §41.1. Looks at the drafter's SQL plus its
EXPLAIN QUERY PLAN; decides ``ship`` or ``repair``.

v0: skipped if drafter confidence > 0.8 (always in mock mode).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nl2sql.agent.types import CritiqueResult, SqlGenerationResponse, StageCall
from nl2sql.prompts import render_critic_prompt

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.session import SharedResources


def critique(
    draft: SqlGenerationResponse,
    *,
    shared: SharedResources,
    explain_plan: str = "(plan unavailable in v0)",
) -> tuple[CritiqueResult, StageCall]:
    """Run the critic stage. Returns ship/repair verdict + telemetry."""
    prompt = render_critic_prompt(sql=draft.sql, explain_plan=explain_plan)
    raw = shared.llm_client.complete_json(
        prompt, CritiqueResult.model_json_schema(), stage="critic"
    )
    telemetry = raw.pop("_telemetry", {})
    result = CritiqueResult.model_validate(raw)
    call = StageCall(
        stage="critic",
        model=telemetry.get("model", "unknown"),
        tokens_in=telemetry.get("tokens_in", 0),
        tokens_out=telemetry.get("tokens_out", 0),
        cached_tokens=telemetry.get("cached_tokens", 0),
        latency_ms=telemetry.get("latency_ms", 0),
        openai_processing_ms=telemetry.get("openai_processing_ms"),
        llm_request_id=telemetry.get("llm_request_id"),
        rendered_prompt=telemetry.get("rendered_prompt"),
    )
    return result, call
