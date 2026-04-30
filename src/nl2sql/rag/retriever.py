"""Hybrid BM25 retriever for few-shot drafter prompts.

Per `final_1.md` §6.1 + §41.5. v0 is BM25-only — no dense retrieval, no
chromadb. The retriever's API (``retrieve(question, k) -> list[Example]``
+ ``is_warm(sql, min_successes)``) is stable across v0/v1/v2.

Token-match-only with a tiny seed corpus is genuinely useful — the few
spec example questions retrieve the matching gold SQL deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class Example:
    id: str
    question: str
    sql: str
    tags: list[str]


def load_seed_examples() -> list[Example]:
    """Load `seed_examples.yaml` from package resources."""
    try:
        text = (
            resources.files("nl2sql.rag").joinpath("seed_examples.yaml").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        here = Path(__file__).parent / "seed_examples.yaml"
        if not here.exists():
            return []
        text = here.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or []
    return [
        Example(
            id=r["id"],
            question=r["question"],
            sql=r["sql"].strip(),
            tags=list(r.get("tags", [])),
        )
        for r in raw
    ]


def _tokenize(text: str) -> list[str]:
    """Lower-case + split on non-alphanumeric — same tokenizer for indexing + queries."""
    import re

    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


class HybridExampleRetriever:
    """v0 retriever — BM25 only.

    For a tiny corpus (< 50 examples) BM25 is equivalent in recall to
    dense retrieval and runs in <1ms. v1 adds bge-small + RRF.
    """

    def __init__(self, examples: list[Example]) -> None:
        self.examples = examples
        self._tokenized = [_tokenize(ex.question) for ex in examples]
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:  # pragma: no cover - rank-bm25 in core deps
            self._bm25 = None
            return
        if not self._tokenized:
            self._bm25 = None
            return
        self._bm25 = BM25Okapi(self._tokenized)

    def retrieve(self, question: str, k: int = 3) -> list[Example]:
        """Return top-k examples by BM25 score."""
        if not self.examples:
            return []
        if self._bm25 is None:
            # Fallback: simple shared-token count
            q_toks = set(_tokenize(question))
            scores = [len(set(toks) & q_toks) for toks in self._tokenized]
        else:
            scores = list(self._bm25.get_scores(_tokenize(question)))
        # Sort by score desc, break ties by example order
        ranked = sorted(
            range(len(self.examples)),
            key=lambda i: (-scores[i], i),
        )
        return [self.examples[i] for i in ranked[:k]]

    def is_warm(self, sql: str, *, min_successes: int = 5) -> bool:  # noqa: ARG002
        """v0: warm pool empty → always returns False → critic always runs.

        v1 (active-learning loop §6.4) maintains success-counts and this
        method gates the critic-skip optimization.
        """
        return False
