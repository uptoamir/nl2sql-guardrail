"""Interpreter — stage 6 of the agent pipeline (the only streaming stage).

Per `final_1.md` §4.1 + §41.1. Yields narrative tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from nl2sql.agent.sanitize import render_rows_preview
from nl2sql.agent.types import ExecResult
from nl2sql.prompts import render_interpreter_prompt

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.session import SharedResources


def interpret_streaming(
    exec_result: ExecResult,
    *,
    question: str,
    sql: str,
    shared: SharedResources,
) -> Iterator[str]:
    """Yield narrative tokens describing the result.

    Row content is sanitized first (indirect-injection defense per §16.1).
    """
    rows_preview = render_rows_preview(exec_result.rows, exec_result.columns, max_rows=10)
    prompt = render_interpreter_prompt(
        question=question,
        sql=sql,
        columns=exec_result.columns,
        rows_preview=rows_preview,
    )
    yield from shared.llm_client.complete_streaming(prompt, stage="interpreter")
