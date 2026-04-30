"""Runtime configuration loaded from environment variables.

We lean on ``pydantic-settings`` so every value is type-validated at startup.
The class is the single source of truth for tunable knobs; CLI flags can
override individual fields by passing ``Settings(...)`` keyword args.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Department = Literal["Sales", "Marketing", "Engineering"]
ALLOWED_DEPARTMENTS: tuple[Department, ...] = ("Sales", "Marketing", "Engineering")


class Settings(BaseSettings):
    """Strongly-typed runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="NL2SQL_",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── LLM ─────────────────────────────────────────────────────────────────
    openai_api_key: str = Field(
        default="",
        validation_alias="OPENAI_API_KEY",
        description="OpenAI API key. Required unless --mock is used.",
    )
    anthropic_api_key: str = Field(
        default="",
        validation_alias="ANTHROPIC_API_KEY",
        description="Anthropic API key. Optional fallback.",
    )
    llm_provider: Literal["openai", "anthropic", "mock"] = Field(default="openai")
    llm_model: str = Field(
        default="gpt-4o-mini-2024-07-18",
        description=(
            "LLM model id. Pinned to an exact version (P1-18 model-drift "
            "detection). Override per env or via --model flag."
        ),
    )
    expected_model: str = Field(
        default="",
        description=(
            "If set, asserted against response.model on every call. "
            "Defaults to llm_model when empty."
        ),
    )
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    input_max_tokens: int = Field(
        default=8000,
        ge=100,
        le=128_000,
        description="Hard pre-call budget enforced by tiktoken (P1-3).",
    )
    output_max_tokens: int = Field(default=1500, ge=64, le=16_000)

    # ─── Agent behaviour ────────────────────────────────────────────────────
    agent_max_repair_attempts: int = Field(default=3, ge=0, le=10)
    result_row_limit: int = Field(default=100, ge=1, le=10_000)
    query_timeout_ms: int = Field(default=5000, ge=100, le=60_000)

    # ─── Database ────────────────────────────────────────────────────────────
    db_path: Path = Field(default=Path("employees.db"))

    # ─── Department picker ──────────────────────────────────────────────────
    department_override: str = Field(
        default="",
        description=(
            "If set to one of Sales/Marketing/Engineering, skip the random pick. "
            "Demos / tests only — every override is logged."
        ),
    )
    random_seed: int | None = Field(default=None)

    # ─── Logging ─────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    audit_log_path: Path = Field(default=Path("logs/audit.jsonl"))

    @field_validator("department_override")
    @classmethod
    def _validate_dept_override(cls, v: str) -> str:
        if v == "":
            return v
        if v not in ALLOWED_DEPARTMENTS:
            raise ValueError(
                f"department_override must be empty or one of {ALLOWED_DEPARTMENTS}, got {v!r}"
            )
        return v

    @field_validator("db_path")
    @classmethod
    def _normalise_db_path(cls, v: Path) -> Path:
        # Resolve relative paths against the current working directory so error
        # messages are unambiguous; existence is checked at connection time so
        # tests can construct Settings without the file being present.
        return v.expanduser()


def load_settings(**overrides: object) -> Settings:
    """Build a Settings instance, applying any caller-supplied overrides."""
    return Settings(**overrides)  # type: ignore[arg-type]
