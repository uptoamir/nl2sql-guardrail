"""Drafter — stage 3 of the agent pipeline (the LLM call that writes SQL).

Per `final_1.md` §4.1 + §41.1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nl2sql.agent.types import (
    RepairStep,
    SchemaLink,
    SqlGenerationResponse,
    StageCall,
    Turn,
)
from nl2sql.prompts import render_drafter_prompt

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.session import SharedResources


def draft(
    question: str,
    schema_link: SchemaLink,
    history: list[Turn],
    *,
    repair_history: list[RepairStep],
    workspace_id: str,
    dept: str,
    shared: SharedResources,
) -> tuple[SqlGenerationResponse, StageCall]:
    """Generate SQL via JSON-mode LLM call."""
    # Render prompt — schema description + few-shot retrieval + history
    schema_md = shared.schema_graph.render_for_prompt(schema_link.tables)
    examples = shared.retriever.retrieve(question, k=3)
    few_shot = [(ex.question, ex.sql) for ex in examples]

    prompt = render_drafter_prompt(
        workspace_id=workspace_id,
        dept=dept,
        schema_md=schema_md,
        few_shot=few_shot,
        history=history,
        repair_history=repair_history,
        question=question,
    )

    raw = shared.llm_client.complete_json(
        prompt, SqlGenerationResponse.model_json_schema(), stage="drafter"
    )
    telemetry = raw.pop("_telemetry", {})

    if "_parse_error" in raw:
        # The LLM emitted invalid JSON. Return a stub draft + a stage call
        # so the pipeline can flag this as a repair attempt.
        draft_obj = SqlGenerationResponse(
            sql="-- LLM emitted invalid JSON",
            rationale=f"parse_error: {raw['_parse_error']}",
            confidence=0.0,
        )
    else:
        draft_obj = SqlGenerationResponse.model_validate(raw)

    call = StageCall(
        stage="drafter",
        model=telemetry.get("model", "unknown"),
        tokens_in=telemetry.get("tokens_in", 0),
        tokens_out=telemetry.get("tokens_out", 0),
        cached_tokens=telemetry.get("cached_tokens", 0),
        latency_ms=telemetry.get("latency_ms", 0),
        openai_processing_ms=telemetry.get("openai_processing_ms"),
        llm_request_id=telemetry.get("llm_request_id"),
        rendered_prompt=telemetry.get("rendered_prompt"),
    )
    return draft_obj, call
