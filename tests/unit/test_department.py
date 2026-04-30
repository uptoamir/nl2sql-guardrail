"""Department picker tests — including the chi-square fairness test.

Per `final_1.md` §41.11. Uses ``random.Random(42)`` for the chi-square
trial so the test is deterministic (no spurious failures).
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from nl2sql.config import ALLOWED_DEPARTMENTS
from nl2sql.guardrails.department import (
    DepartmentSelectionError,
    pick_department,
)


def test_seed_reproducibility() -> None:
    a = pick_department(seed=42).department
    b = pick_department(seed=42).department
    assert a == b


def test_override_takes_precedence() -> None:
    out = pick_department(override="Sales", seed=42)
    assert out.department == "Sales"
    assert out.source == "override"


def test_override_invalid_raises() -> None:
    with pytest.raises(DepartmentSelectionError):
        pick_department(override="HR")


def test_random_with_seed_zero_is_seeded_not_systemrandom() -> None:
    """seed=0 must be treated as a seed (not falsy → fall back to SystemRandom)."""
    a = pick_department(seed=0).department
    b = pick_department(seed=0).department
    assert a == b


def test_chi_square_uniform_distribution() -> None:
    """30k trials with deterministic seed should be uniform.

    Uses scipy.stats.chisquare. seed=42 makes this deterministic — never
    a spurious CI failure.
    """
    pytest.importorskip("scipy")
    from scipy.stats import chisquare  # type: ignore[import-not-found]

    rng = random.Random(42)
    counts: Counter[str] = Counter()
    for _ in range(30_000):
        out = pick_department(rng=rng)
        counts[out.department] += 1

    expected = [10_000, 10_000, 10_000]
    observed = [counts[d] for d in ALLOWED_DEPARTMENTS]
    chi2, p = chisquare(observed, expected)
    # With seed=42 + 30k trials, distribution should be very close to uniform.
    # Set bar very low (p > 0.001) — only fails on a real distribution skew.
    assert p > 0.001, f"distribution skewed: counts={counts} p={p}"


def test_banner_random() -> None:
    out = pick_department(seed=1)
    assert "Department selected" in out.banner
    assert out.department in out.banner


def test_banner_override() -> None:
    out = pick_department(override="Sales")
    assert "override" in out.banner.lower()
    assert "Sales" in out.banner
