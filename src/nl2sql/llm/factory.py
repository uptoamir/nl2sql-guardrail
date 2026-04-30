"""LLM client factory — picks the provider based on Settings.

Per `final_1.md` §35.10 + §41.2. Single canonical entry point used by
``bootstrap_shared``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from nl2sql.config import Settings
from nl2sql.llm.base import LLMClient

logger = logging.getLogger(__name__)


def create_llm_client(settings: Settings, *, mock: bool = False) -> LLMClient:
    """Build the LLM client matching ``settings.llm_provider``.

    ``mock=True`` overrides the provider selection entirely (used by
    ``--mock`` flag and offline CI runs).
    """
    if mock or settings.llm_provider == "mock":
        from nl2sql.llm.mock_client import MockLLMClient

        # Try to load eval golden records as canned responses; falls back to
        # the in-module catalog if the file is missing.
        golden_path = Path("eval/datasets/golden_v1.jsonl")
        return MockLLMClient(golden_path=golden_path if golden_path.exists() else None)

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is empty. Pass --mock for offline mode, or "
                "set the key in .env / pass --openai-key=sk-... to ./nl2sql."
            )
        from nl2sql.llm.openai_client import OpenAIClient

        return OpenAIClient(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            timeout_s=settings.llm_timeout_s,
            temperature=settings.llm_temperature,
            expected_model=settings.expected_model or settings.llm_model,
            max_retries=settings.llm_max_retries,
        )

    if settings.llm_provider == "anthropic":
        # Anthropic is documented as a fallback; v0 take-home doesn't
        # exercise it. Raise a clear error rather than ship a broken stub.
        raise NotImplementedError(
            "Anthropic provider is documented as a v1 fallback; not implemented in v0. "
            "Use --mock for offline or LLM_PROVIDER=openai."
        )

    raise ValueError(f"unknown llm_provider: {settings.llm_provider!r}")
