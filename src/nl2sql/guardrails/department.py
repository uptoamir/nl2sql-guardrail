"""Startup-time department picker.

The picker is a pure function so it's trivially testable. It returns the
chosen department + a one-line provenance string (random / override) that the
CLI surfaces back to the user — important because an override fundamentally
changes the agent's blast radius and must never be silent.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from nl2sql.config import ALLOWED_DEPARTMENTS, Department


class DepartmentSelectionError(ValueError):
    """Raised when an override is supplied but isn't an allowed department."""


@dataclass(frozen=True, slots=True)
class DepartmentChoice:
    department: Department
    source: str  # "random" | "override"
    seed: int | None = None

    @property
    def banner(self) -> str:
        if self.source == "override":
            return f"Department selected: {self.department}  (override — NOT random)"
        if self.seed is not None:
            return f"Department selected: {self.department}  (random, seed={self.seed})"
        return f"Department selected: {self.department}  (random)"


def pick_department(
    *,
    override: str = "",
    seed: int | None = None,
    rng: random.Random | None = None,
) -> DepartmentChoice:
    """Pick the active department.

    - If ``override`` is a non-empty string, it must be an allowed department.
    - Otherwise pick uniformly at random from the three allowed departments.
    - ``seed`` makes the random draw reproducible (used by tests / demos).
    """
    if override:
        if override not in ALLOWED_DEPARTMENTS:
            raise DepartmentSelectionError(
                f"Department override {override!r} is not in {ALLOWED_DEPARTMENTS}"
            )
        return DepartmentChoice(department=override, source="override")

    rng = rng or (random.Random(seed) if seed is not None else random.SystemRandom())
    chosen = rng.choice(ALLOWED_DEPARTMENTS)
    return DepartmentChoice(department=chosen, source="random", seed=seed)
