"""Audit-record reading helpers — extract specific fields without
hard-coding indexes.

Per `final_1.md` §41.11. The Streamlit and CLI ``/prompt`` slash command
both need to find the drafter's rendered prompt, but ``model_calls[2]``
isn't reliable (critic might be skipped). These helpers locate fields
robustly.
"""

from __future__ import annotations

from typing import Any


def get_drafter_prompt(audit_record: dict[str, Any]) -> str | None:
    """Find the drafter call's ``rendered_prompt`` in the audit record.

    Returns None if no drafter call is present (e.g., raw_sql turn or
    refused turn before the drafter ran).
    """
    for call in audit_record.get("model_calls", []):
        if call.get("stage") == "drafter":
            prompt = call.get("rendered_prompt")
            return prompt if prompt is None else str(prompt)
    return None


def get_stage_call(audit_record: dict[str, Any], stage: str) -> dict[str, Any] | None:
    """Return the StageCall dict for ``stage``, or None."""
    for call in audit_record.get("model_calls", []):
        if call.get("stage") == stage:
            return dict(call)
    return None
