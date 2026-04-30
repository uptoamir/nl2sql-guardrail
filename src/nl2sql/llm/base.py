"""LLMClient Protocol — every provider implements this.

Per `final_1.md` §35.10 + §41.1 (stage-aware contract).

The :func:`complete_json` method takes an explicit ``stage`` parameter
so providers (including the mock) don't have to detect stage from the
schema title — clean contract per §C0-3 / §41.10.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol


class LLMClient(Protocol):
    """Provider-agnostic LLM client used by every agent stage."""

    model: str

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        stage: str,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Send ``prompt`` and return a JSON object matching ``schema``.

        ``stage`` is one of ``intent | linker | drafter | critic | interpreter``.

        Raises:
            BudgetExceeded: pre-call token budget exceeded
            ModelDriftError: response.model != expected
            openai.RateLimitError / Timeout / etc. — bubble up for the
              fallback router (§7.2) to handle
        """
        ...

    def complete_streaming(
        self,
        prompt: str,
        *,
        stage: str,
        max_output_tokens: int | None = None,
    ) -> Iterator[str]:
        """Streaming text completion (no JSON parsing).

        Used only by the interpreter stage. Yields narrative tokens.
        """
        ...
