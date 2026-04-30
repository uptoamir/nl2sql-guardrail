"""Row sanitization for the interpreter stage.

Per `final_1.md` §13 + §16.1 (Greshake-style indirect injection defense).

When the interpreter LLM sees DB-content (e.g., a row with
``Name = "'); SELECT * FROM Employee --"``), naive concatenation lets
the model "see" attacker-controlled text. We wrap every cell value in
delimiters and strip control characters before passing to the LLM.
"""

from __future__ import annotations

import re
from typing import Any

# Strip control chars (except whitespace), backticks, and template-fence sequences.
_CTRL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _sanitize_value(v: Any) -> str:
    """Sanitize a single cell value for safe inclusion in a prompt."""
    if v is None:
        return "(null)"
    s = str(v)
    s = _CTRL_CHARS.sub("", s)
    # Escape backticks so the value can't break out of code fences in the prompt
    s = s.replace("`", "ʹ")
    # Escape triple-backtick fences
    s = s.replace("```", "ʹʹʹ")
    # Cap absurdly long values
    if len(s) > 200:
        s = s[:200] + "…"
    return s


def sanitize_row_for_interpreter(row: dict[str, Any]) -> dict[str, str]:
    """Wrap every value as ``[USER_DATA] <value>`` and strip control chars.

    The ``[USER_DATA]`` prefix is a textual signal that the interpreter
    should treat the value as data, not instruction. Combined with the
    JSON-mode interpreter prompt, this defends against indirect injection.
    """
    return {k: f"[USER_DATA] {_sanitize_value(v)}" for k, v in row.items()}


def render_rows_preview(rows: list[dict[str, Any]], columns: list[str], max_rows: int = 10) -> str:
    """Render rows as a sanitized markdown-ish preview for the interpreter prompt."""
    if not rows:
        return "(no rows)"
    out: list[str] = [" | ".join(columns)]
    for row in rows[:max_rows]:
        sanitized = sanitize_row_for_interpreter(row)
        out.append(" | ".join(sanitized.get(c, "") for c in columns))
    if len(rows) > max_rows:
        out.append(f"… +{len(rows) - max_rows} more rows")
    return "\n".join(out)
