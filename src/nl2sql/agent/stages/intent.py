"""Intent classifier — stage 1 of the agent pipeline.

Per `final_1.md` §4.1 + §41.1. Returns :class:`Intent` Pydantic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nl2sql.agent.types import Intent, StageCall, Turn
from nl2sql.prompts import render_intent_prompt

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.session import SharedResources


def classify(
    question: str,
    history: list[Turn],
    *,
    shared: SharedResources,
    active_dept: str = "",
) -> tuple[Intent, StageCall]:
    """Classify the user's question into one of 6 intent kinds.

    ``active_dept`` is shown to the classifier so it can flag any question
    that references a different department as ``cross_dept_attempt`` —
    catches A1 ("And in Sales?" when scope is Engineering) and F3
    ("NOT in Marketing") follow-ups that previously slipped through.

    Returns ``(Intent, StageCall)`` so the pipeline can record telemetry.
    """
    prompt = render_intent_prompt(
        question=question, history=history, active_dept=active_dept
    )
    raw = shared.llm_client.complete_json(prompt, Intent.model_json_schema(), stage="intent")
    telemetry = raw.pop("_telemetry", {})
    intent = Intent.model_validate(raw)
    call = StageCall(
        stage="intent",
        model=telemetry.get("model", "unknown"),
        tokens_in=telemetry.get("tokens_in", 0),
        tokens_out=telemetry.get("tokens_out", 0),
        cached_tokens=telemetry.get("cached_tokens", 0),
        latency_ms=telemetry.get("latency_ms", 0),
        openai_processing_ms=telemetry.get("openai_processing_ms"),
        llm_request_id=telemetry.get("llm_request_id"),
        rendered_prompt=telemetry.get("rendered_prompt"),
    )
    return intent, call
