"""Streamlit UI — chat surface for the NL2SQL agent.

Per `final_1.md` §35.3 + §39.6 + §41.20. Per-browser session state (NOT
@st.cache_resource on Session, per B0-3); shared resources cached
separately. Same Session/Pipeline backend as the CLI — only the I/O
layer differs.
"""

from __future__ import annotations

import json
import os
import re
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
    page_icon="🛡️",
    layout="centered",
    initial_sidebar_state="expanded",
)


# ─── Custom CSS — refined typography, layout, micro-components ──────────────
st.markdown(
    """
    <style>
      /* ── Page chrome ───────────────────────────────────────────────── */
      .block-container {
          max-width: 800px;
          padding-top: 1.5rem;
          padding-bottom: 7rem;
      }
      [data-testid="stHeader"] { background: transparent; }
      #MainMenu, footer { visibility: hidden; }

      /* Tabular numerals everywhere */
      body, .block-container, [data-testid="stSidebar"] {
          font-feature-settings: "tnum" 1, "ss01" 1;
      }

      /* ── Sidebar polish ────────────────────────────────────────────── */
      [data-testid="stSidebar"] {
          background: #0a0d12;
          border-right: 1px solid rgba(255,255,255,0.05);
      }
      [data-testid="stSidebar"] .block-container { padding-top: 1.25rem; }

      .nl2sql-brand {
          display: flex;
          align-items: center;
          gap: 0.55rem;
          font-weight: 600;
          font-size: 0.95rem;
          letter-spacing: -0.01em;
          margin-bottom: 0.85rem;
      }
      .nl2sql-brand .logo {
          width: 22px; height: 22px;
          border-radius: 6px;
          background: linear-gradient(135deg, #10b981 0%, #047857 100%);
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 12px;
      }

      .nl2sql-section {
          font-size: 0.66rem;
          color: #6b7280;
          text-transform: uppercase;
          letter-spacing: 0.09em;
          font-weight: 600;
          margin: 1.1rem 0 0.5rem 0;
      }

      /* Sidebar metric cards (stacked) */
      .nl2sql-metric-row { display: flex; flex-direction: column; gap: 0.35rem; }
      .nl2sql-metric {
          display: flex; justify-content: space-between; align-items: baseline;
          padding: 0.35rem 0.6rem;
          background: rgba(255,255,255,0.025);
          border-radius: 6px;
          border: 1px solid rgba(255,255,255,0.04);
      }
      .nl2sql-metric .label {
          font-size: 0.72rem; color: #9ca3af;
      }
      .nl2sql-metric .value {
          font-size: 0.85rem; font-weight: 600; color: #e5e7eb;
          font-variant-numeric: tabular-nums;
      }

      /* Scope pill */
      .nl2sql-scope-pill {
          display: inline-flex; align-items: center; gap: 0.35rem;
          padding: 3px 10px;
          border-radius: 999px;
          background: rgba(16, 185, 129, 0.12);
          color: #34d399;
          font-size: 0.74rem;
          font-weight: 600;
          letter-spacing: 0.01em;
          border: 1px solid rgba(16, 185, 129, 0.25);
      }
      .nl2sql-scope-pill::before {
          content: ""; display: inline-block;
          width: 6px; height: 6px; border-radius: 50%;
          background: #10b981;
      }
      .nl2sql-mode-pill {
          display: inline-block;
          padding: 3px 8px;
          border-radius: 999px;
          background: rgba(255,255,255,0.04);
          color: #9ca3af;
          font-size: 0.68rem;
          font-weight: 500;
          margin-left: 0.4rem;
      }

      /* Sidebar buttons cleaner */
      [data-testid="stSidebar"] div.stButton > button {
          background: rgba(255,255,255,0.025);
          border: 1px solid rgba(255,255,255,0.06);
          color: #d1d5db;
          font-size: 0.82rem;
          font-weight: 500;
          padding: 0.45rem 0.7rem;
          text-align: left;
          line-height: 1.3;
          white-space: normal;
          height: auto;
          transition: all 0.12s ease;
      }
      [data-testid="stSidebar"] div.stButton > button:hover {
          background: rgba(16, 185, 129, 0.08);
          border-color: rgba(16, 185, 129, 0.25);
          color: #f3f4f6;
      }

      /* ── Hero (empty state) ────────────────────────────────────────── */
      .nl2sql-hero {
          text-align: center;
          padding: 3.5rem 0 1.75rem 0;
      }
      .nl2sql-hero h1 {
          font-size: 2.1rem;
          font-weight: 700;
          letter-spacing: -0.02em;
          margin-bottom: 0.4rem;
          background: linear-gradient(135deg, #f3f4f6 0%, #9ca3af 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
      }
      .nl2sql-hero .subtitle {
          color: #9ca3af;
          font-size: 0.95rem;
          margin: 0.5rem 0 0.25rem 0;
      }
      .nl2sql-hero .scope-callout {
          margin-top: 1rem;
      }

      /* Hero prompt cards */
      .main div.stButton > button {
          background: rgba(255,255,255,0.025);
          border: 1px solid rgba(255,255,255,0.07);
          color: #e5e7eb;
          font-weight: 500;
          font-size: 0.88rem;
          padding: 0.85rem 1rem;
          text-align: left;
          line-height: 1.35;
          height: auto;
          white-space: normal;
          border-radius: 10px;
          transition: all 0.15s ease;
      }
      .main div.stButton > button:hover {
          background: rgba(16, 185, 129, 0.06);
          border-color: rgba(16, 185, 129, 0.3);
          transform: translateY(-1px);
      }

      /* ── Top status bar (during chat) ──────────────────────────────── */
      .nl2sql-topbar {
          display: flex; justify-content: flex-end; align-items: center;
          gap: 0.5rem;
          padding: 0.25rem 0 0.75rem 0;
          border-bottom: 1px solid rgba(255,255,255,0.04);
          margin-bottom: 1.25rem;
      }

      /* ── Chat messages ─────────────────────────────────────────────── */
      [data-testid="stChatMessage"] {
          background: transparent !important;
          padding: 0.6rem 0;
      }
      [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
          margin: 0;
          line-height: 1.55;
      }
      /* User messages are typically one short line — center the avatar
         vertically against the text so it doesn't appear to float. */
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
          align-items: center !important;
      }
      /* Assistant messages have a meta strip + trace + table + expanders
         below the narrative, so keep the avatar top-aligned (default). */
      [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
          align-items: flex-start !important;
      }

      /* ── Meta chip strip (confidence / rows / ms / tokens) ─────────── */
      .nl2sql-meta-strip {
          display: flex; flex-wrap: wrap; gap: 0.4rem;
          margin: 0.5rem 0 0.85rem 0;
          font-size: 0.74rem;
      }
      .nl2sql-chip {
          display: inline-flex; align-items: center; gap: 0.3rem;
          padding: 3px 9px;
          border-radius: 6px;
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.05);
          color: #d1d5db;
          font-variant-numeric: tabular-nums;
      }
      .nl2sql-chip .label { color: #9ca3af; }
      .nl2sql-chip.ok { color: #34d399; border-color: rgba(16,185,129,0.25); background: rgba(16,185,129,0.08); }
      .nl2sql-chip.warn { color: #fbbf24; border-color: rgba(251,191,36,0.25); background: rgba(251,191,36,0.08); }
      .nl2sql-chip.bad { color: #f87171; border-color: rgba(248,113,113,0.25); background: rgba(248,113,113,0.08); }

      /* ── 8-layer trace at-a-glance ─────────────────────────────────── */
      .nl2sql-trace-row {
          display: flex; align-items: center; gap: 0.35rem;
          margin: 0.4rem 0 0.85rem 0;
          padding: 0.45rem 0.6rem;
          background: rgba(255,255,255,0.02);
          border-radius: 8px;
          border: 1px solid rgba(255,255,255,0.04);
          font-size: 0.72rem;
      }
      .nl2sql-trace-row .label {
          color: #9ca3af; font-weight: 500;
          margin-right: 0.5rem;
          letter-spacing: 0.01em;
      }
      .nl2sql-layer-dot {
          width: 22px; height: 22px;
          border-radius: 5px;
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 0.62rem; font-weight: 700;
          font-variant-numeric: tabular-nums;
          letter-spacing: -0.02em;
          cursor: help;
      }
      .nl2sql-layer-dot.passed {
          background: rgba(16,185,129,0.12);
          color: #34d399;
          border: 1px solid rgba(16,185,129,0.3);
      }
      .nl2sql-layer-dot.failed {
          background: rgba(248,113,113,0.15);
          color: #f87171;
          border: 1px solid rgba(248,113,113,0.4);
      }
      .nl2sql-trace-row .summary { margin-left: auto; color: #6b7280; }

      /* ── Expanders ─────────────────────────────────────────────────── */
      [data-testid="stExpander"] {
          border: 1px solid rgba(255,255,255,0.06) !important;
          border-radius: 8px !important;
          background: rgba(255,255,255,0.015);
      }
      [data-testid="stExpander"] summary {
          font-size: 0.82rem;
          font-weight: 500;
      }

      /* ── Dataframe styling ─────────────────────────────────────────── */
      [data-testid="stDataFrame"] {
          border-radius: 8px;
          overflow: hidden;
          border: 1px solid rgba(255,255,255,0.05);
      }

      /* ── Chat input — softer focus state ───────────────────────────── */
      [data-testid="stChatInput"] {
          border-radius: 12px !important;
          border: 1px solid rgba(255,255,255,0.08) !important;
          background: rgba(255,255,255,0.02) !important;
          transition: border-color 0.15s ease;
      }
      [data-testid="stChatInput"]:focus-within {
          border-color: rgba(16,185,129,0.5) !important;
          box-shadow: 0 0 0 3px rgba(16,185,129,0.08) !important;
      }

      /* ── Action row (CSV/JSON/Bookmark) — quieter buttons ──────────── */
      .nl2sql-actions div.stButton > button,
      .nl2sql-actions div[data-testid="stDownloadButton"] > button {
          background: transparent !important;
          border: 1px solid rgba(255,255,255,0.08) !important;
          color: #9ca3af !important;
          font-size: 0.78rem !important;
          font-weight: 500 !important;
          padding: 0.35rem 0.7rem !important;
          height: auto !important;
      }
      .nl2sql-actions div.stButton > button:hover,
      .nl2sql-actions div[data-testid="stDownloadButton"] > button:hover {
          background: rgba(255,255,255,0.04) !important;
          border-color: rgba(255,255,255,0.15) !important;
          color: #e5e7eb !important;
      }

      /* ── Refusal block — warning style softened ────────────────────── */
      [data-testid="stAlert"][kind="warning"] {
          background: rgba(251,191,36,0.06) !important;
          border-left: 3px solid rgba(251,191,36,0.5) !important;
          border-radius: 6px !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Sample prompts shown in the empty state (cards) ────────────────────────
HERO_SUGGESTIONS = [
    ("📊", "What is the average salary?"),
    ("🏆", "Top 3 by remaining benefits balance"),
    ("📅", "Who started after 2023 and their certifications"),
    ("💎", "Who has the highest remaining benefits balance?"),
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


# Columns whose values should render as USD currency in result tables.
_CURRENCY_COL_RE = re.compile(
    r"(salary|amount|balance|bonus|cost|revenue|price|usd)",
    re.IGNORECASE,
)


def _format_rows_for_display(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pretty-print currency-like floats (e.g. SalaryAmount -> $84,535.10).

    Keeps non-money floats as-is. Strings/ints untouched.
    """
    if not rows:
        return rows
    cols = list(rows[0].keys())
    money_cols = {c for c in cols if _CURRENCY_COL_RE.search(c)}
    if not money_cols:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        new = dict(row)
        for c in money_cols:
            v = new.get(c)
            if isinstance(v, int | float):
                new[c] = f"${v:,.2f}"
        out.append(new)
    return out


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


def _render_meta_strip(final: FinalAnswer) -> None:
    """Confidence + counts + cost as distinct chips (replaces single caption)."""
    confidence_class = {
        "Confident": "ok",
        "Best guess": "warn",
        "Not sure": "bad",
    }[final.confidence_label]
    confidence_dot = {
        "Confident": "●",
        "Best guess": "●",
        "Not sure": "●",
    }[final.confidence_label]
    total_tokens = final.total_tokens_in + final.total_tokens_out
    chips = [
        f'<span class="nl2sql-chip {confidence_class}">{confidence_dot} {final.confidence_label}</span>',
        f'<span class="nl2sql-chip"><span class="label">rows</span> {final.row_count:,}</span>',
        f'<span class="nl2sql-chip"><span class="label">exec</span> {final.exec_time_ms} ms</span>',
        f'<span class="nl2sql-chip"><span class="label">tokens</span> {total_tokens:,}</span>',
    ]
    st.markdown(
        f'<div class="nl2sql-meta-strip">{"".join(chips)}</div>',
        unsafe_allow_html=True,
    )


def _render_trace_at_a_glance(final: FinalAnswer) -> None:
    """Always-visible 8-layer trace strip — defense-in-depth as a glance."""
    if not final.guardrail_layers:
        return
    dots = []
    passed_count = 0
    for layer in final.guardrail_layers:
        klass = "passed" if layer.passed else "failed"
        if layer.passed:
            passed_count += 1
        # Tooltip: layer name + verdict + timing
        verdict = "passed" if layer.passed else (layer.reason or "rejected")
        title = f"{layer.layer_id} {layer.name} — {verdict} · {layer.elapsed_ms:.1f} ms"
        dots.append(
            f'<span class="nl2sql-layer-dot {klass}" title="{title}">{layer.layer_id}</span>'
        )
    total = len(final.guardrail_layers)
    summary = f"{passed_count}/{total} passed"
    st.markdown(
        f'<div class="nl2sql-trace-row">'
        f'<span class="label">guardrail</span>'
        f"{''.join(dots)}"
        f'<span class="summary">{summary}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_assistant_details(final_dump: dict[str, Any]) -> None:
    """Per-message details: meta chips, trace, rows, SQL, exports, bookmark."""
    final = FinalAnswer.model_validate(final_dump)

    _render_meta_strip(final)
    _render_trace_at_a_glance(final)

    if final.supporting_rows:
        st.dataframe(
            _format_rows_for_display(final.supporting_rows),
            use_container_width=True,
            hide_index=True,
        )

    if final.sql_used:
        with st.expander("Generated SQL"):
            st.code(final.sql_used, language="sql")

    with st.expander("Guardrail trace · detail"):
        for layer in final.guardrail_layers:
            icon = "✓" if layer.passed else "✗"
            color = "#34d399" if layer.passed else "#f87171"
            verdict = "passed" if layer.passed else f"REJECTED — {layer.reason or '(no reason)'}"
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:0.5rem;'
                f'padding:0.25rem 0;font-size:0.85rem;">'
                f'<span style="color:{color};font-weight:700;width:14px;">{icon}</span>'
                f'<span style="color:#9ca3af;font-weight:600;width:30px;">{layer.layer_id}</span>'
                f'<span style="color:#e5e7eb;flex:1;">{layer.name}</span>'
                f'<span style="color:#6b7280;font-size:0.78rem;">{verdict}</span>'
                f'<span style="color:#6b7280;font-variant-numeric:tabular-nums;'
                f'font-size:0.78rem;width:55px;text-align:right;">{layer.elapsed_ms:.1f} ms</span>'
                f"</div>",
                unsafe_allow_html=True,
            )

    # Action row — quieter, grouped
    st.markdown('<div class="nl2sql-actions">', unsafe_allow_html=True)
    a, b, c = st.columns([1, 1, 2])
    a.download_button(
        "↓ CSV",
        _to_csv(final.supporting_rows),
        file_name=f"nl2sql-{final.run_id[:8]}.csv",
        mime="text/csv",
        key=f"csv_{final.run_id}",
        use_container_width=True,
        disabled=not final.supporting_rows,
    )
    b.download_button(
        "↓ JSON",
        json.dumps(final.audit_record, indent=2, default=str).encode(),
        file_name=f"nl2sql-{final.run_id[:8]}.json",
        mime="application/json",
        key=f"json_{final.run_id}",
        use_container_width=True,
    )
    save_name = c.text_input(
        "Save as bookmark",
        key=f"save_input_{final.run_id}",
        placeholder="★ save as bookmark…",
        label_visibility="collapsed",
    )
    st.markdown("</div>", unsafe_allow_html=True)
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


# ─── Sidebar: brand, scope, metrics, history, controls ─────────────────────
with st.sidebar:
    # Brand
    st.markdown(
        '<div class="nl2sql-brand"><span class="logo">🛡️</span><span>NL2SQL Guardrail</span></div>',
        unsafe_allow_html=True,
    )

    # Scope + mode pills
    mode = "mock" if os.environ.get("NL2SQL_LLM_PROVIDER") == "mock" else "real-LLM"
    st.markdown(
        f'<span class="nl2sql-scope-pill">{session.dept}</span>'
        f'<span class="nl2sql-mode-pill">{mode}</span>',
        unsafe_allow_html=True,
    )

    # Metrics
    cost = session.session_cost
    st.markdown('<div class="nl2sql-section">session</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="nl2sql-metric-row">'
        f'<div class="nl2sql-metric"><span class="label">turns</span>'
        f'<span class="value">{cost["turns"]:,}</span></div>'
        f'<div class="nl2sql-metric"><span class="label">tokens</span>'
        f'<span class="value">{cost["tokens"]:,}</span></div>'
        f'<div class="nl2sql-metric"><span class="label">cost</span>'
        f'<span class="value">${cost["usd"]:.4f}</span></div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # History
    history_msgs = [m for m in st.session_state["messages"] if m["role"] == "user"]
    if history_msgs:
        st.markdown('<div class="nl2sql-section">history</div>', unsafe_allow_html=True)
        for i, m in enumerate(reversed(history_msgs[-10:])):
            preview = m["content"][:42] + ("…" if len(m["content"]) > 42 else "")
            if st.button(preview, key=f"hist_{i}_{hash(m['content'])}", use_container_width=True):
                st.session_state["pending_question"] = m["content"]
                st.rerun()

    # Bookmarks (real, persisted to ~/.nl2sql/bookmarks.json)
    bms = load_bookmarks(workspace_id=session.workspace_id)
    if bms:
        st.markdown('<div class="nl2sql-section">bookmarks</div>', unsafe_allow_html=True)
        for name, bm in bms.items():
            bc1, bc2 = st.columns([5, 1])
            if bc1.button(
                f"★ {name}", key=f"bm_{name}", help=bm["question"], use_container_width=True
            ):
                touch_bookmark(name)
                st.session_state["pending_question"] = bm["question"]
                st.rerun()
            if bc2.button("✕", key=f"bm_x_{name}", help="Remove"):
                forget_bookmark(name)
                st.rerun()

    # Controls
    st.markdown('<div class="nl2sql-section">controls</div>', unsafe_allow_html=True)
    if st.button("＋ New chat", use_container_width=True):
        session.clear()
        st.session_state["messages"] = []
        st.rerun()
    if st.button("⤺ Re-roll department", use_container_width=True, help="Random new scope"):
        for k in ("session", "messages", "dept_source"):
            st.session_state.pop(k, None)
        st.rerun()


# ─── Top status bar (only during active chat) ──────────────────────────────
if st.session_state["messages"] or st.session_state["pending_question"]:
    st.markdown(
        f'<div class="nl2sql-topbar"><span class="nl2sql-scope-pill">{session.dept}</span></div>',
        unsafe_allow_html=True,
    )


# ─── Empty state: hero + 4 prompt cards ────────────────────────────────────
if not st.session_state["messages"] and not st.session_state["pending_question"]:
    st.markdown(
        f"""
        <div class="nl2sql-hero">
          <h1>NL2SQL Guardrail</h1>
          <p class="subtitle">Ask anything about <strong>{session.dept}</strong>.
          Cross-department rows are blocked at 8 independent layers.</p>
          <div class="scope-callout">
            <span class="nl2sql-scope-pill">{session.dept}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    for i, (icon, q) in enumerate(HERO_SUGGESTIONS):
        if cols[i % 2].button(f"{icon}  {q}", key=f"hero_{i}", use_container_width=True):
            st.session_state["pending_question"] = q
            st.rerun()


# ─── Chat history render ────────────────────────────────────────────────────
for msg in st.session_state["messages"]:
    avatar = "🧑" if msg["role"] == "user" else "🛡️"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        if msg.get("final_dump"):
            _render_assistant_details(msg["final_dump"])


# ─── Input ──────────────────────────────────────────────────────────────────
question = st.chat_input(f"Ask anything about {session.dept}…")
if st.session_state["pending_question"]:
    question = st.session_state["pending_question"]
    st.session_state["pending_question"] = None


# ─── Handle a new turn ──────────────────────────────────────────────────────
if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🛡️"):
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
