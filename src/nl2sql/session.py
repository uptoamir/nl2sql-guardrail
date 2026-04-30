"""Session — identity + history + delegated Pipeline runner.

Per `final_1.md` §10.3 + §36.10 (canonical Session signature) + §41.2
(bootstrap_shared) + §40.2 (session_cost property).

A Session represents one user's working context: scope (workspace, dept,
user), conversation history (last 3 turns), and a Pipeline that runs
each turn against shared resources.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from nl2sql.agent.pipeline import Pipeline
from nl2sql.agent.types import DEPT, FinalAnswer, StreamChunk, Turn
from nl2sql.config import Settings, load_settings
from nl2sql.db.connection import open_session
from nl2sql.guardrails.errors import AgentError
from nl2sql.llm.cost import compute_cost
from nl2sql.llm.factory import create_llm_client
from nl2sql.observability.audit import emit_audit
from nl2sql.schema.graph import SchemaGraph

if TYPE_CHECKING:  # pragma: no cover
    from nl2sql.db.introspect import open_introspect_conn  # noqa: F401
    from nl2sql.llm.base import LLMClient
    from nl2sql.rag.retriever import HybridExampleRetriever

logger = logging.getLogger(__name__)


# ─── SharedResources — cached across browser sessions / processes ───────────
@dataclass
class SharedResources:
    """Genuinely shared, expensive-to-init objects.

    Streamlit's ``@st.cache_resource`` wraps a single call to
    :func:`bootstrap_shared`. CLI calls it once per process. Eval calls
    it once per run.
    """

    settings: Settings
    schema_graph: SchemaGraph
    retriever: HybridExampleRetriever
    llm_client: LLMClient
    session_conn: sqlite3.Connection | None = None  # set per-session in Session.__init__

    def cache_stats(self) -> dict[str, dict[str, Any]]:
        """Per-cache hit/miss counts. v0: stub for /cache slash command."""
        return {
            "semantic": {
                "status": "not_initialized",
                "hits": 0,
                "misses": 0,
                "note": "v0 ships without chromadb; enable via --profile obs",
            },
            "schema": {"hits": 1, "misses": 0, "note": "loaded once at startup"},
            "prompt": {"status": "managed by provider"},
        }

    def invalidate_semantic_cache(self, *, workspace_id: str, dept: str) -> int:  # noqa: ARG002
        return 0  # v0 stub


def bootstrap_shared(
    settings: Settings | None = None,
    *,
    mock: bool = False,
) -> SharedResources:
    """Build SharedResources once.

    Used by:
      - Streamlit ``@st.cache_resource`` wrapper
      - CLI ``main()``
      - ``eval/runners/run_eval.py`` ``main()``

    Single canonical entry point per §41.2.
    """
    settings = settings or load_settings()

    # SchemaGraph needs a read-only introspect connection.
    from nl2sql.db.introspect import open_introspect_conn

    introspect_conn = open_introspect_conn(settings.db_path)
    schema_graph = SchemaGraph(
        conn=introspect_conn,
        descriptions=SchemaGraph.load_descriptions(),
    )
    schema_graph.build()

    # Retriever — load seed examples
    from nl2sql.rag.retriever import HybridExampleRetriever, load_seed_examples

    retriever = HybridExampleRetriever(load_seed_examples())

    # LLM client (mock or real)
    llm_client = create_llm_client(settings, mock=mock)

    return SharedResources(
        settings=settings,
        schema_graph=schema_graph,
        retriever=retriever,
        llm_client=llm_client,
    )


# ─── Session ────────────────────────────────────────────────────────────────
class Session:
    """One user session: scope + history + pipeline.

    Canonical signature per §41.2 + §36.10. v0 fixture path uses
    ``Session(shared=, workspace_id=, dept=)``; production uses
    ``Session(shared=, jwt=)``.
    """

    def __init__(
        self,
        *,
        shared: SharedResources,
        workspace_id: str | None = None,
        dept: DEPT | None = None,
        user_id: str | None = None,
        jwt: str | None = None,
    ) -> None:
        if jwt is not None:
            # v1 production path — JWT validation
            raise NotImplementedError("JWT path is v1; v0 uses workspace_id+dept fixture path.")
        if workspace_id is None or dept is None:
            raise ValueError("must supply jwt OR (workspace_id + dept)")

        self.shared = shared
        self.workspace_id = workspace_id
        self.dept: DEPT = dept
        self.user_id = user_id or "fixture-user"
        self.run_id = str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())
        self.history: deque[Turn] = deque(maxlen=3)

        # Open the per-session DB connection (RO + TEMP VIEWs + authorizer)
        self.shared.session_conn = open_session(
            shared.settings.db_path,
            dept=dept,
            workspace_id=workspace_id,
        )

        # Per-session token accumulator (for /cost and UI cost meter)
        self._tokens_in_total = 0
        self._tokens_out_total = 0
        self._cost_usd_total = 0.0

        # Pipeline
        self.pipeline = Pipeline(shared=shared, workspace_id=workspace_id, dept=dept)

        # Bind contextvars so every log line carries scope identity
        structlog.contextvars.bind_contextvars(
            workspace_id=workspace_id,
            session_id=self.session_id,
            dept=dept,
            user_id=self.user_id,
        )

    # ─── The two public ask methods ────────────────────────────────────
    def ask_streaming(self, question: str) -> Iterator[StreamChunk]:
        """Yield StreamChunks; append a Turn + emit audit on 'done'."""
        self.run_id = str(uuid.uuid4())  # new run per ask
        for chunk in self.pipeline.run_streaming(
            question,
            list(self.history),
            run_id=self.run_id,
            session_id=self.session_id,
        ):
            yield chunk
            if chunk.kind == "done" and chunk.final is not None:
                self._on_turn_complete(chunk.final, question)

    def ask(self, question: str) -> FinalAnswer:
        """Blocking convenience for CLI / FastAPI / non-streaming consumers."""
        last: StreamChunk | None = None
        for chunk in self.ask_streaming(question):
            last = chunk
            if chunk.kind == "error":
                raise AgentError(outcome="error", detail=chunk.text)
        if last is not None and last.kind == "done" and last.final is not None:
            return last.final
        raise AgentError(outcome="error", detail="stream ended without done chunk")

    # ─── Cost accumulation (for /cost) ──────────────────────────────────
    def _on_turn_complete(self, final: FinalAnswer, question: str) -> None:
        self._tokens_in_total += final.total_tokens_in
        self._tokens_out_total += final.total_tokens_out
        self._cost_usd_total += compute_cost(final.model_calls)

        self.history.append(self._turn_from_final(final, question))
        emit_audit("turn", **final.audit_record)

    def _turn_from_final(self, final: FinalAnswer, question: str) -> Turn:
        rows_summary = f"{final.row_count} rows" if final.row_count else "0 rows"
        return Turn(
            timestamp=datetime.now(UTC),
            question=question,
            sql_used=final.sql_used or None,
            row_count=final.row_count,
            brief_result_summary=rows_summary,
            intent_kind=final.audit_record.get("intent_kind", "unknown"),
            outcome=final.outcome,
            audit_record=final.audit_record,
        )

    @property
    def session_cost(self) -> dict[str, Any]:
        """Running session cost — used by /cost slash command + UI cost meter."""
        return {
            "tokens_in": self._tokens_in_total,
            "tokens_out": self._tokens_out_total,
            "tokens": self._tokens_in_total + self._tokens_out_total,
            "usd": round(self._cost_usd_total, 6),
            "turns": len(self.history),
        }

    def clear(self) -> None:
        """Clear conversation history AND running cost totals.

        Used by the UI's "New chat" button and the CLI's /clear command.
        Without this, ``session_cost.turns`` would go to 0 while the
        token totals stay non-zero (a confusing UX inconsistency).
        """
        self.history.clear()
        self._tokens_in_total = 0
        self._tokens_out_total = 0
        self._cost_usd_total = 0.0

    def close(self) -> None:
        """Flush any buffered audit records and close the DB connection."""
        if self.shared.session_conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self.shared.session_conn.close()
            self.shared.session_conn = None
