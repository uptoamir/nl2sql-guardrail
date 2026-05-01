"""Streamlit UI — ChatGPT-style chat surface for the NL2SQL agent.

Per `final_1.md` §35.3 + §39.6 + §41.20. Per-browser session state (NOT
@st.cache_resource on Session, per B0-3); shared resources cached
separately. Same Session/Pipeline backend as the CLI — only the I/O
layer differs.

Visual design: centered narrow column (~720 px), hero empty-state with
prompt cards, collapsed sidebar holding conversation history + scope +
cost. No clutter on the main pane during active chat.
"""

from __future__ import annotations

import json
import os
from typing import Any

import streamlit as st

from nl2sql.agent.types import FinalAnswer
from nl2sql.bookmarks import (
    forget_bookmark,
    load_bookmarks,
    save_bookmark,
    touch_bookmark,
)
from nl2sql.config import load_settings
from nl2sql.guardrails.department import pick_department
from nl2sql.session import Session, SharedResources, bootstrap_shared

st.set_page_config(
    page_title="NL2SQL Guardrail",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
)


# ─── Custom CSS — ChatGPT-style typography, spacing, centered column ────────
st.markdown(
    """
    <style>
      .block-container {
          max-width: 760px;
          padding-top: 1rem;
          padding-bottom: 6rem;
      }
      [data-testid="stChatMessage"] {
          background: transparent !important;
          padding-top: 0.5rem;
          padding-bottom: 0.5rem;
      }
      [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
          margin: 0.25rem 0;
      }
      .nl2sql-hero {
          text-align: center;
          padding: 4rem 0 1.5rem 0;
      }
      .nl2sql-hero h1 {
          font-size: 2rem;
          font-weight: 600;
          margin-bottom: 0.25rem;
      }
      .nl2sql-hero p {
          color: #888;
          font-size: 1rem;
          margin: 0.25rem 0 1.5rem 0;
      }
      .nl2sql-scope-pill {
          display: inline-block;
          padding: 2px 10px;
          border-radius: 999px;
          background: rgba(72, 187, 120, 0.15);
          color: #48bb78;
          font-size: 0.78rem;
          font-weight: 500;
          letter-spacing: 0.02em;
      }
      .nl2sql-meta {
          color: #777;
          font-size: 0.78rem;
          margin-top: 0.25rem;
      }
      div.stButton > button {
          text-align: left;
          font-weight: 400;
          color: #ddd;
          border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.03);
          padding: 0.6rem 0.85rem;
          height: auto;
          white-space: normal;
      }
      div.stButton > button:hover {
          background: rgba(255,255,255,0.07);
          border-color: rgba(255,255,255,0.2);
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Sample prompts shown in the empty state (cards) ────────────────────────
HERO_SUGGESTIONS = [
    "What is the average salary?",
    "Top 3 by remaining benefits balance",
    "Who started after 2023 and their certifications",
    "Who has the highest remaining benefits balance?",
]


# ─── Shared resources cache (correct B0-3 pattern) ──────────────────────────
@st.cache_resource(show_spinner="Booting the agent…")
def get_shared_resources() -> SharedResources:
    """Schema graph, retriever, LLM client. Expensive to init; immutable
    across browser sessions."""
    use_mock = os.environ.get("NL2SQL_LLM_PROVIDER") == "mock"
    settings = load_settings()
    return bootstrap_shared(settings, mock=use_mock)


def get_session() -> Session:
    """Per-browser-session Session. Each tab gets its own scope."""
    if "session" not in st.session_state:
        shared = get_shared_resources()
        choice = pick_department(
            override=shared.settings.department_override,
            seed=shared.settings.random_seed,
        )
        st.session_state.session = Session(
            shared=shared,
            workspace_id="local-fixture",
            dept=choice.department,
            user_id="streamlit-fixture",
        )
        st.session_state.dept_source = choice.source
    return st.session_state.session


def _to_csv(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    import csv
    import io

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _render_assistant_details(final_dump: dict[str, Any]) -> None:
    """Per-message details: SQL, rows, 8-layer trace, exports, save bookmark.

    Defined at module top because Streamlit reruns the script top-to-bottom
    on every interaction; the chat-history loop calls this for any stored
    message that has a final_dump payload.
    """
    final = FinalAnswer.model_validate(final_dump)

    # Confidence + timing chip line
    color = {"Confident": "🟢", "Best guess": "🟡", "Not sure": "🔴"}[final.confidence_label]
    st.caption(
        f"{color} {final.confidence_label}  ·  "
        f"{final.row_count} rows  ·  {final.exec_time_ms} ms  ·  "
        f"{final.total_tokens_in + final.total_tokens_out} tokens"
    )

    if final.supporting_rows:
        st.dataframe(final.supporting_rows, use_container_width=True, hide_index=True)

    if final.sql_used:
        with st.expander("SQL"):
            st.code(final.sql_used, language="sql")

    with st.expander("Guardrail trace · 8 layers"):
        for layer in final.guardrail_layers:
            icon = "✅" if layer.passed else "🔴"
            verdict = "passed" if layer.passed else f"REJECTED — {layer.reason or '(no reason)'}"
            st.markdown(
                f"{icon} **{layer.layer_id}** {layer.name}  —  "
                f"*{verdict}*  · {layer.elapsed_ms:.1f} ms"
            )

    a, b, c = st.columns([1, 1, 2])
    a.download_button(
        "CSV",
        _to_csv(final.supporting_rows),
        file_name=f"nl2sql-{final.run_id[:8]}.csv",
        mime="text/csv",
        key=f"csv_{final.run_id}",
        use_container_width=True,
    )
    b.download_button(
        "JSON",
        json.dumps(final.audit_record, indent=2, default=str).encode(),
        file_name=f"nl2sql-{final.run_id[:8]}.json",
        mime="application/json",
        key=f"json_{final.run_id}",
        use_container_width=True,
    )
    save_name = c.text_input(
        "Save as bookmark",
        key=f"save_input_{final.run_id}",
        placeholder="bookmark name…",
        label_visibility="collapsed",
    )
    if save_name:
        # Walk the message list to find the assistant turn whose final_dump
        # matches this run_id, then take the user question that came right
        # before it (messages[i-1]).
        msgs = st.session_state.get("messages", [])
        question_text = final.sql_used  # fallback
        for i, m in enumerate(msgs):
            if m.get("final_dump", {}).get("run_id") == final.run_id and i > 0:
                prev = msgs[i - 1]
                if prev.get("role") == "user":
                    question_text = prev["content"]
                break
        ws_id = (
            st.session_state.session.workspace_id
            if "session" in st.session_state
            else "local-fixture"
        )
        save_bookmark(save_name, question_text, ws_id)
        st.toast(f"Saved as '{save_name}'", icon="⭐")


# ─── Init session + state ───────────────────────────────────────────────────
session = get_session()
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "pending_question" not in st.session_state:
    st.session_state["pending_question"] = None


# ─── Sidebar: scope, cost, history, controls ────────────────────────────────
with st.sidebar:
    st.markdown("### 📊 NL2SQL Guardrail")
    mode = "mock" if os.environ.get("NL2SQL_LLM_PROVIDER") == "mock" else "real-LLM"
    st.markdown(
        f'<span class="nl2sql-scope-pill">scope · {session.dept}</span> '
        f'<span class="nl2sql-meta">{mode}</span>',
        unsafe_allow_html=True,
    )

    st.divider()

    cost = session.session_cost
    st.caption(
        f"**{cost['turns']}** turns · **{cost['tokens']:,}** tokens · **${cost['usd']:.4f}**"
    )

    st.divider()

    # Conversation history (clickable to scroll-to or re-ask)
    st.caption("HISTORY")
    history_msgs = [m for m in st.session_state["messages"] if m["role"] == "user"]
    if not history_msgs:
        st.caption("_no turns yet_")
    else:
        for i, m in enumerate(reversed(history_msgs[-10:])):
            preview = m["content"][:42] + ("…" if len(m["content"]) > 42 else "")
            if st.button(preview, key=f"hist_{i}_{hash(m['content'])}", use_container_width=True):
                st.session_state["pending_question"] = m["content"]
                st.rerun()

    st.divider()

    # Bookmarks (real, persisted to ~/.nl2sql/bookmarks.json)
    bms = load_bookmarks(workspace_id=session.workspace_id)
    if bms:
        st.caption("BOOKMARKS")
        for name, bm in bms.items():
            bc1, bc2 = st.columns([4, 1])
            if bc1.button(name, key=f"bm_{name}", help=bm["question"], use_container_width=True):
                touch_bookmark(name)
                st.session_state["pending_question"] = bm["question"]
                st.rerun()
            if bc2.button("✕", key=f"bm_x_{name}"):
                forget_bookmark(name)
                st.rerun()
        st.divider()

    if st.button("New chat", use_container_width=True):
        session.clear()
        st.session_state["messages"] = []
        st.rerun()
    if st.button("Re-roll department", use_container_width=True, help="Random new scope"):
        for k in ("session", "messages", "dept_source"):
            st.session_state.pop(k, None)
        st.rerun()


# ─── Top of main column: small scope chip once chat starts ──────────────────
if st.session_state["messages"] or st.session_state["pending_question"]:
    st.markdown(
        f'<div style="text-align:right; margin-top:-0.5rem; margin-bottom:1rem;">'
        f'<span class="nl2sql-scope-pill">scope · {session.dept}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


# ─── Empty state: ChatGPT-style hero + 4 prompt cards ───────────────────────
# Only show hero when there's truly nothing to chat about — once a question is
# in flight (pending_question) or in history, hide it so the chat takes over.
if not st.session_state["messages"] and not st.session_state["pending_question"]:
    st.markdown(
        f"""
        <div class="nl2sql-hero">
          <h1>NL2SQL Guardrail</h1>
          <p>Ask about the <strong>{session.dept}</strong> department.
          Cross-department rows are guarded at 8 layers.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    for i, q in enumerate(HERO_SUGGESTIONS):
        if cols[i % 2].button(q, key=f"hero_{i}", use_container_width=True):
            st.session_state["pending_question"] = q
            st.rerun()


# ─── Chat history render ────────────────────────────────────────────────────
for msg in st.session_state["messages"]:
    avatar = "🧑" if msg["role"] == "user" else "🤖"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        if msg.get("final_dump"):
            _render_assistant_details(msg["final_dump"])


# ─── Input ──────────────────────────────────────────────────────────────────
question = st.chat_input(f"Message NL2SQL Guardrail · {session.dept}")
if st.session_state["pending_question"]:
    question = st.session_state["pending_question"]
    st.session_state["pending_question"] = None


# ─── Handle a new turn ──────────────────────────────────────────────────────
if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🤖"):
        status_box = st.empty()
        narrative_box = st.empty()
        narrative_text = ""
        sql_seen = ""
        rows_payload: dict[str, Any] | None = None
        final_answer: FinalAnswer | None = None

        try:
            for chunk in session.ask_streaming(question):
                if chunk.kind == "status":
                    status_box.caption(f"› {chunk.text}")
                elif chunk.kind == "sql":
                    sql_seen = chunk.text or ""
                elif chunk.kind == "rows":
                    rows_payload = chunk.payload  # type: ignore[assignment]
                elif chunk.kind == "narrative_token":
                    narrative_text += chunk.text or ""
                    narrative_box.markdown(narrative_text + "▍")
                elif chunk.kind == "refusal":
                    # Render warning, but DON'T break — the pipeline always
                    # yields a `done` chunk right after, and we need that to
                    # complete the turn (cost accounting, audit write).
                    narrative_box.warning(chunk.text or "")
                    narrative_text = chunk.text or ""
                elif chunk.kind == "error":
                    status_box.error(chunk.text or "")
                    break
                elif chunk.kind == "done":
                    final_answer = chunk.final
                    status_box.empty()
                    narrative_box.markdown(narrative_text)  # drop cursor
        except Exception as e:  # noqa: BLE001
            status_box.error(f"{type(e).__name__}: {e}")

        if final_answer:
            _render_assistant_details(final_answer.model_dump())
            st.session_state["messages"].append(
                {
                    "role": "assistant",
                    "content": narrative_text or "(see results above)",
                    "final_dump": final_answer.model_dump(),
                }
            )
        else:
            st.session_state["messages"].append(
                {
                    "role": "assistant",
                    "content": narrative_text or "(no answer)",
                }
            )

    # Force a rerun so the sidebar's turns/tokens/cost meter reflects this
    # turn immediately. Without this, the sidebar shows the count from BEFORE
    # this turn until the user's next interaction triggers a natural rerun.
    st.rerun()
