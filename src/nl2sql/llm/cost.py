"""LLM token unit prices + per-turn cost computation.

Per `final_1.md` §41.6 (D0-6 fix). Source: openai.com/api/pricing as of
pin date — review monthly. Production (§24.2) loads from a ConfigMap;
v0 reads this constant.

Unknown models cost $0 (graceful — we'd rather under-report than crash
when a new model id appears).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.agent.types import StageCall


# USD per token (per 1M token rates ÷ 1e6)
MODEL_UNIT_COSTS_USD: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o-mini-2024-07-18": {"in": 0.150 / 1e6, "out": 0.600 / 1e6},
    "gpt-4o-2024-08-06": {"in": 2.500 / 1e6, "out": 10.000 / 1e6},
    # Anthropic
    "claude-haiku-4-5": {"in": 0.250 / 1e6, "out": 1.250 / 1e6},
    "claude-sonnet-4-7": {"in": 3.000 / 1e6, "out": 15.000 / 1e6},
    # Mock — always free
    "mock": {"in": 0.0, "out": 0.0},
}


def compute_cost(model_calls: list[StageCall]) -> float:
    """Sum USD cost across all stage calls.

    Unknown models contribute $0 (graceful). Returns the total rounded
    to 6 decimal places (microdollars precision).
    """
    total = 0.0
    for c in model_calls:
        prices = MODEL_UNIT_COSTS_USD.get(c.model)
        if prices is None:
            # Unknown model — skip rather than crash. Operators will see
            # zero in the cost dashboard and investigate.
            continue
        total += c.tokens_in * prices["in"] + c.tokens_out * prices["out"]
    return round(total, 6)
