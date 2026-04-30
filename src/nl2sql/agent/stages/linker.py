"""Schema linker — stage 2 of the agent pipeline.

Per `final_1.md` §4.1 + §41.1. v0 implementation uses the SchemaGraph's
token-match :meth:`relevant_tables_for_question` directly (no LLM call —
the LLM is overkill for 3 tables). v1 will swap in dense retrieval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nl2sql.agent.types import ColumnRef, Intent, SchemaLink, StageCall

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.session import SharedResources


def link(
    question: str,
    intent: Intent,  # noqa: ARG001 - reserved for v1 follow-up resolution
    *,
    shared: SharedResources,
) -> tuple[SchemaLink, StageCall | None]:
    """Pick the minimum set of allowed_* views needed to answer ``question``.

    v0: token-match via SchemaGraph (no LLM).
    Returns ``(SchemaLink, None)`` since no LLM call is made.
    """
    tables = shared.schema_graph.relevant_tables_for_question(question)
    # Pull representative columns from each chosen table for the prompt.
    columns: list[ColumnRef] = []
    for view in tables:
        # Map view → base table to look up columns
        base_for_view = {
            "allowed_employees": "Employee",
            "allowed_certifications": "Certification",
            "allowed_benefits": "Benefits",
        }.get(view)
        if not base_for_view:
            continue
        cols = (
            shared.schema_graph.schema.get("tables", {}).get(base_for_view, {}).get("columns", [])
        )
        for col in cols:
            columns.append(ColumnRef(table=view, name=col["name"]))

    return SchemaLink(tables=tables, columns=columns), None
