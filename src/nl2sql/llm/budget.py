"""Token-budget pre-call enforcement.

Per `final_1.md` §9.2 (P1-3). Wraps :mod:`tiktoken` to estimate prompt
size BEFORE making the API call; raises :class:`BudgetExceeded` so the
agent loop can short-circuit before burning tokens.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nl2sql.guardrails.errors import BudgetExceeded

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    pass


# Lazy tiktoken — import is slow and only needed in real-LLM mode.
_TIKTOKEN_ENCODING_CACHE: dict[str, object] = {}


def _get_encoding(model: str) -> object | None:
    """Return a tiktoken encoding for ``model``, or None if unavailable.

    Falls back to the gpt-4 encoding for unknown models (still close
    enough for budget estimates).
    """
    if model in _TIKTOKEN_ENCODING_CACHE:
        return _TIKTOKEN_ENCODING_CACHE[model]
    try:
        import tiktoken
    except ImportError:  # pragma: no cover - tiktoken in core deps
        logger.warning("tiktoken not installed — token budget enforcement disabled")
        _TIKTOKEN_ENCODING_CACHE[model] = None
        return None
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        # Unknown model — fall back to cl100k_base (GPT-4 family encoding)
        enc = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_ENCODING_CACHE[model] = enc
    return enc


class TokenBudget:
    """Pre-call token-count enforcement.

    Construct with ``input_max`` and ``output_max`` thresholds; call
    :meth:`enforce_input` before issuing the API call. Raises
    :class:`BudgetExceeded` on overflow.
    """

    def __init__(self, *, input_max: int = 8000, output_max: int = 1500) -> None:
        self.input_max = input_max
        self.output_max = output_max

    def estimate_input(self, prompt: str, model: str) -> int:
        """Return a token-count estimate for ``prompt`` under ``model``.

        Falls back to ``len(prompt) // 4`` if tiktoken is unavailable
        (rough char-to-token ratio for English text).
        """
        enc = _get_encoding(model)
        if enc is None:
            return len(prompt) // 4
        return len(enc.encode(prompt))  # type: ignore[attr-defined]

    def enforce_input(self, prompt: str, model: str) -> int:
        """Raise :class:`BudgetExceeded` if the estimate exceeds the budget.

        Returns the estimated token count on pass.
        """
        n = self.estimate_input(prompt, model)
        if n > self.input_max:
            try:
                from nl2sql.observability.metrics import BUDGET_ABORTS

                BUDGET_ABORTS.labels(reason="input_overflow").inc()
            except ImportError:
                pass
            raise BudgetExceeded(estimated=n, allowed=self.input_max)
        return n
