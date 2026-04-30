"""Stage-aware mock LLM client for offline/CI use.

Per `final_1.md` §35.10 + §41.10 (D0-3 fix — explicit stage param, not
title-string detection).

Returns valid Pydantic-shaped JSON for each stage. For the drafter,
attempts to match against a small set of canned (question, dept) pairs
loaded from ``eval/datasets/golden_v1.jsonl`` if present; otherwise
falls back to a sensible default (count over allowed_employees).

The mock client is what powers ``--mock`` mode: every reviewer demo runs
through it without burning tokens. v0 take-home submission can be
demoed end-to-end with no API key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_question(q: str) -> str:
    """Lower-case, strip punctuation + extra whitespace for canned-key lookup."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", q.lower())).strip()


def _hash_key(question: str, dept: str | None) -> str:
    return hashlib.sha256(
        f"{(dept or '').lower()}|{_normalize_question(question)}".encode()
    ).hexdigest()[:32]


def _extract_question(prompt: str) -> str:
    """Find the user question inside a rendered prompt template.

    The drafter prompt renders the question under a ``# QUESTION`` header.
    Falls back to the last non-empty line.
    """
    m = re.search(r"#\s*QUESTION\s*\n+(.+?)(?:\n\n|\Z)", prompt, flags=re.S)
    if m:
        return m.group(1).strip()
    # Last non-empty line fallback
    for line in reversed(prompt.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _extract_dept(prompt: str) -> str | None:
    """Find the active department in a rendered prompt template."""
    m = re.search(r"data_scope_department[:\s]+([A-Za-z]+)", prompt, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bdepartment[:\s]+(Sales|Marketing|Engineering)\b", prompt)
    return m.group(1).strip() if m else None


# ─── Canned drafter SQL by question pattern ──────────────────────────────────
# A small in-module catalog so /mock works without requiring eval datasets
# to be present. Eval-dataset-loaded entries override these.
_CANNED_DRAFTS: dict[str, dict[str, Any]] = {
    "average salary": {
        "sql": "SELECT AVG(SalaryAmount) AS avg_salary FROM allowed_employees LIMIT 100",
        "rationale": "Average base salary across the active department.",
        "confidence": 0.95,
        "assumptions": [],
    },
    "software engineers": {
        "sql": (
            "SELECT Name, Role FROM allowed_employees "
            "WHERE Role LIKE '%Software Engineer%' LIMIT 100"
        ),
        "rationale": "Roles containing 'Software Engineer' (semantic LIKE).",
        "confidence": 0.85,
        "assumptions": ["Interpreted 'software engineers' as a LIKE match — includes Senior."],
    },
    "aws certification": {
        "sql": (
            "SELECT e.Name, c.CertificationName FROM allowed_employees e "
            "JOIN allowed_certifications c USING (EmployeeId) "
            "WHERE c.CertificationName LIKE '%AWS%' LIMIT 100"
        ),
        "rationale": "Employees holding AWS-named certifications.",
        "confidence": 0.95,
        "assumptions": [],
    },
    "highest remaining benefits balance": {
        "sql": (
            "SELECT e.Name, b.BenefitsPackage, b.RemainingBalance "
            "FROM allowed_employees e JOIN allowed_benefits b USING (EmployeeId) "
            "ORDER BY b.RemainingBalance DESC LIMIT 1"
        ),
        "rationale": "Top employee by remaining benefits balance in the active department.",
        "confidence": 0.95,
        "assumptions": [],
    },
    "started after 2023": {
        "sql": (
            "SELECT e.Name, e.EmploymentStartDate, c.CertificationName "
            "FROM allowed_employees e LEFT JOIN allowed_certifications c USING (EmployeeId) "
            "WHERE e.EmploymentStartDate > '2023-01-01' LIMIT 100"
        ),
        "rationale": "Employees hired after 2023-01-01 with their certifications (LEFT JOIN — keeps cert-less employees).",
        "confidence": 0.9,
        "assumptions": [],
    },
    "list all certifications": {
        "sql": "SELECT DISTINCT CertificationName FROM allowed_certifications LIMIT 100",
        "rationale": "Distinct certification names held by employees in the active department.",
        "confidence": 0.95,
        "assumptions": [],
    },
    "top 3 by remaining benefits": {
        "sql": (
            "SELECT e.Name, b.RemainingBalance FROM allowed_employees e "
            "JOIN allowed_benefits b USING (EmployeeId) "
            "ORDER BY b.RemainingBalance DESC LIMIT 3"
        ),
        "rationale": "Top 3 by remaining benefits balance.",
        "confidence": 0.95,
        "assumptions": [],
    },
}


def _match_canned_draft(question: str) -> dict[str, Any] | None:
    """Substring-match the question against the canned catalog."""
    q = _normalize_question(question)
    for key, draft in _CANNED_DRAFTS.items():
        if all(tok in q for tok in key.split()):
            return draft
    return None


class MockLLMClient:
    """Stage-aware mock. Returns shape-correct JSON for each agent stage."""

    model: str = "mock"

    def __init__(self, golden_path: Path | None = None) -> None:
        self._canned: dict[str, dict[str, Any]] = {}
        if golden_path and golden_path.exists():
            self._load_golden(golden_path)

    def _load_golden(self, path: Path) -> None:
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = _hash_key(rec["question"], rec.get("dept"))
                # Heuristic: pick out a likely SQL from the record
                sql = (
                    rec.get("expected_sql")
                    or rec.get("sql")
                    or rec.get("expected_sql_or_pattern", "")
                )
                if sql:
                    self._canned[key] = {
                        "sql": sql,
                        "rationale": "[mock — replay of golden record]",
                        "confidence": 0.9,
                        "assumptions": [],
                    }
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("mock golden load failed: %s", e)

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],  # noqa: ARG002 - stage param drives behavior
        *,
        stage: str,
        max_output_tokens: int | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        t0 = time.monotonic()
        result = self._dispatch(stage, prompt)

        # Telemetry — inflated rough estimates so /tokens isn't always 0
        tokens_in = max(1, len(prompt) // 4)
        tokens_out = max(1, len(json.dumps(result)) // 4)
        result["_telemetry"] = {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached_tokens": 0,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "openai_processing_ms": None,
            "llm_request_id": None,
            "model": "mock",
            "rendered_prompt": prompt,
        }
        return result

    def complete_streaming(
        self,
        prompt: str,  # noqa: ARG002
        *,
        stage: str,  # noqa: ARG002
        max_output_tokens: int | None = None,  # noqa: ARG002
    ) -> Iterator[str]:
        """Mock interpreter — yields a short canned narrative chunk-by-chunk."""
        narrative = (
            "Here are the results for the active department. "
            "Run /sql to see the generated query and /trace for full audit."
        )
        for word in narrative.split():
            yield word + " "

    # ─── Stage dispatch ──────────────────────────────────────────────────
    def _dispatch(self, stage: str, prompt: str) -> dict[str, Any]:
        if stage == "intent":
            return self._intent_response(prompt)
        if stage == "linker":
            return self._linker_response(prompt)
        if stage == "drafter":
            return self._drafter_response(prompt)
        if stage == "critic":
            return self._critic_response(prompt)
        if stage == "interpreter":
            return self._interpreter_response(prompt)
        # Unknown stage — fail loudly
        raise ValueError(f"MockLLMClient: unknown stage {stage!r}")

    def _intent_response(self, prompt: str) -> dict[str, Any]:
        question = _extract_question(prompt).lower()
        # Detect adversarial-flavored intent for demo purposes
        adversarial_markers = (
            "ignore previous",
            "drop the",
            "list all departments",
            "show me employees from sales",  # only matches if scope ≠ Sales
            "rowid",
        )
        if any(m in question for m in adversarial_markers):
            return {
                "kind": "cross_dept_attempt",
                "confidence": 0.9,
                "reasoning": "[mock] question pattern matches an adversarial template",
                "clarification_needed": None,
                "follow_up_resolves_to": None,
            }
        if "what is the structure" in question or "schema of" in question:
            return {
                "kind": "schema_question",
                "confidence": 0.9,
                "reasoning": "[mock] schema-fishing pattern",
                "clarification_needed": None,
                "follow_up_resolves_to": None,
            }
        if "highest paid" in question:
            return {
                "kind": "ambiguous",
                "confidence": 0.7,
                "reasoning": "[mock] 'highest paid' is ambiguous: salary or salary+bonus?",
                "clarification_needed": "Base salary alone, or total comp including bonus?",
                "follow_up_resolves_to": None,
            }
        return {
            "kind": "data_query",
            "confidence": 1.0,
            "reasoning": "[mock] standard data query",
            "clarification_needed": None,
            "follow_up_resolves_to": None,
        }

    def _linker_response(self, prompt: str) -> dict[str, Any]:
        question = _extract_question(prompt).lower()
        tables = ["allowed_employees"]
        cols = [
            {"table": "allowed_employees", "name": "Name"},
            {"table": "allowed_employees", "name": "Role"},
        ]
        if "cert" in question or "aws" in question or "kubernetes" in question:
            tables.append("allowed_certifications")
            cols.append({"table": "allowed_certifications", "name": "CertificationName"})
        if "benefit" in question or "balance" in question:
            tables.append("allowed_benefits")
            cols.append({"table": "allowed_benefits", "name": "RemainingBalance"})
            cols.append({"table": "allowed_benefits", "name": "BenefitsPackage"})
        return {
            "tables": tables,
            "columns": cols,
            "joins": [],
            "filters_implied": [],
        }

    def _drafter_response(self, prompt: str) -> dict[str, Any]:
        question = _extract_question(prompt)
        dept = _extract_dept(prompt)
        # Try canned-by-hash first
        key = _hash_key(question, dept)
        if key in self._canned:
            try:
                from nl2sql.observability.metrics import MOCK_HITS

                MOCK_HITS.inc()
            except ImportError:
                pass
            return dict(self._canned[key])
        # Substring-match the canned catalog
        match = _match_canned_draft(question)
        if match:
            try:
                from nl2sql.observability.metrics import MOCK_HITS

                MOCK_HITS.inc()
            except ImportError:
                pass
            return dict(match)
        # Fall through: a safe default that always works.
        try:
            from nl2sql.observability.metrics import MOCK_MISSES

            MOCK_MISSES.inc()
        except ImportError:
            pass
        return {
            "sql": "SELECT COUNT(*) AS n FROM allowed_employees LIMIT 100",
            "rationale": "[mock fallthrough] safe count over allowed_employees",
            "confidence": 0.5,
            "assumptions": [
                "Mock-mode default — question didn't match any canned template; returning a safe count."
            ],
        }

    def _critic_response(self, prompt: str) -> dict[str, Any]:  # noqa: ARG002
        return {
            "verdict": "ship",
            "issues": [],
            "suggested_sql": None,
            "explain_plan_summary": "[mock — no execution plan analyzed]",
        }

    def _interpreter_response(self, prompt: str) -> dict[str, Any]:  # noqa: ARG002
        return {
            "narrative": "[mock interpretation] See the SQL and rows above.",
            "caveats": [],
        }
