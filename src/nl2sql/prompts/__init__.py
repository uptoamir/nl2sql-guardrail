"""Prompt templates + rendering helpers.

Per `final_1.md` §35.8 + §41.10 (P0-G prompt-cache safety).

Each stage has its own template at ``prompts/<stage>.md``. The
:func:`render_prompt` function fills placeholders from the typed
arguments. Critical: the **prefix block ordering** is fixed so OpenAI
prompt caching can hash a stable workspace-scoped prefix (P0-G).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from nl2sql.agent.types import RepairStep, Turn

_PROMPT_CACHE: dict[str, str] = {}


def _load_template(stage: str) -> str:
    """Load `prompts/<stage>.md` from package resources."""
    if stage in _PROMPT_CACHE:
        return _PROMPT_CACHE[stage]
    try:
        text = resources.files("nl2sql.prompts").joinpath(f"{stage}.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        here = Path(__file__).parent / f"{stage}.md"
        if not here.exists():
            raise
        text = here.read_text(encoding="utf-8")
    _PROMPT_CACHE[stage] = text
    return text


def render_drafter_prompt(
    *,
    workspace_id: str,
    dept: str,
    schema_md: str,
    few_shot: list[tuple[str, str]],  # (question, sql) pairs
    history: list[Turn],
    repair_history: list[RepairStep],
    question: str,
) -> str:
    """Render the drafter prompt with prefix-cache-safe block ordering.

    Per §41.10 P0-G: prefix MUST start with workspace+dept binding so
    the cache key is workspace-scoped. Suffix (question, history,
    repair) is per-turn and never cached.
    """
    template = _load_template("drafter")
    few_shot_block = (
        "\n\n".join(f"### Q: {q}\n```sql\n{s.strip()}\n```" for q, s in few_shot)
        if few_shot
        else "(none)"
    )
    history_block = (
        "\n".join(
            f"- {t.timestamp:%H:%M:%S} {t.question!r} → {t.brief_result_summary}" for t in history
        )
        if history
        else "(no recent turns)"
    )
    repair_block = (
        "\n".join(
            f"- attempt {s.attempt}: rejected at {s.layer} ({s.error_class}): {s.error_message}\n"
            f"  prev_sql: {s.prev_sql}"
            for s in repair_history
        )
        if repair_history
        else "(no prior repair attempts)"
    )

    return template.format(
        workspace_id=workspace_id,
        dept=dept,
        schema_md=schema_md,
        few_shot=few_shot_block,
        history=history_block,
        repair_history=repair_block,
        question=question,
    )


def render_intent_prompt(question: str, history: list[Turn], active_dept: str = "") -> str:
    template = _load_template("intent")
    history_block = (
        "\n".join(f"- {t.timestamp:%H:%M:%S} {t.question!r}" for t in history) or "(none)"
    )
    return template.format(question=question, history=history_block, active_dept=active_dept)


def render_linker_prompt(question: str, schema_md: str) -> str:
    template = _load_template("linker")
    return template.format(question=question, schema_md=schema_md)


def render_critic_prompt(sql: str, explain_plan: str) -> str:
    template = _load_template("critic")
    return template.format(sql=sql, explain_plan=explain_plan)


def render_interpreter_prompt(
    *, question: str, sql: str, columns: list[str], rows_preview: str
) -> str:
    template = _load_template("interpreter")
    return template.format(
        question=question,
        sql=sql,
        columns=", ".join(columns),
        rows_preview=rows_preview,
    )
