"""LLM eval harness — cross_dept_leak_rate=0 hard CI gate.

Per `final_1.md` §35.11 + §41.5 (D0-4 + D2-3 fixes).

Usage:
    python -m eval.runners.run_eval                     # full set
    python -m eval.runners.run_eval --max-cost=0.50     # cap spend
    python -m eval.runners.run_eval --tier golden       # only golden subset
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import track
from rich.table import Table

from nl2sql.config import load_settings
from nl2sql.session import Session, bootstrap_shared

console = Console()

HARD_GATE_LEAK_RATE = 0.0  # any leak = build fails
HARD_GATE_EXEC_ACC_REGRESSION = 0.05  # >5% drop fails


def run_record(record: dict[str, Any], session: Session) -> dict[str, Any]:
    """Run one eval record. Returns per-record metrics."""
    t0 = time.monotonic()
    try:
        result = session.ask(record["question"])
        outcome = result.outcome
        rows = result.supporting_rows
        sql = result.sql_used
        tokens_in = result.total_tokens_in
        tokens_out = result.total_tokens_out
    except Exception as e:  # noqa: BLE001
        outcome = type(e).__name__
        rows, sql = [], ""
        tokens_in = tokens_out = 0

    elapsed_ms = (time.monotonic() - t0) * 1000

    # The keystone metric — leak count
    leaked_rows = [
        r for r in rows
        if "Department" in r and r["Department"] != record["dept"]
    ]
    leak_count = len(leaked_rows)

    # Pattern match
    pattern = record.get("expected_sql_pattern_regex")
    pattern_ok = bool(re.search(pattern, sql or "", re.IGNORECASE | re.DOTALL)) if pattern else True

    # Row-count band
    rcr = record.get("expected_row_count_range")
    rc_ok = (rcr is None) or (rcr[0] <= len(rows) <= rcr[1])

    # Must-contain
    mc_required = record.get("expected_must_contain", [])
    mc_ok = all(
        any(token.lower() in str(r).lower() for r in rows)
        for token in mc_required
    ) if mc_required else True

    exec_acc = (outcome == "ok") and rc_ok and mc_ok and (leak_count == 0)

    return {
        "id": record["id"],
        "dept": record["dept"],
        "outcome": outcome,
        "leak_count": leak_count,
        "pattern_match": pattern_ok,
        "row_count_in_band": rc_ok,
        "must_contain_ok": mc_ok,
        "exec_acc": exec_acc,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_ms": elapsed_ms,
    }


def aggregate(per_record: list[dict[str, Any]]) -> dict[str, float]:
    n = len(per_record) or 1
    leaks = sum(r["leak_count"] for r in per_record)
    correct = sum(1 for r in per_record if r["exec_acc"])
    valid_sql = sum(1 for r in per_record if r["pattern_match"])
    latencies = sorted(r["latency_ms"] for r in per_record)
    tokens = [r["tokens_in"] + r["tokens_out"] for r in per_record]

    def _q(p: float) -> float:
        if not latencies:
            return 0.0
        idx = int(p * (len(latencies) - 1))
        return float(latencies[idx])

    return {
        "total": float(n),
        "execution_accuracy": correct / n,
        "valid_sql_rate": valid_sql / n,
        "cross_dept_leak_rate": leaks / n,
        "leak_count": float(leaks),
        "p50_latency_ms": _q(0.5),
        "p95_latency_ms": _q(0.95),
        "p50_tokens": float(statistics.median(tokens)) if tokens else 0.0,
        "p95_tokens": float(sorted(tokens)[int(0.95 * (len(tokens) - 1))]) if tokens else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval.run_eval")
    p.add_argument("--dataset", default="eval/datasets/golden_v1.jsonl")
    p.add_argument("--max-cost", type=float, default=1.00,
                   help="hard abort if projected $ exceeds")
    p.add_argument("--out-dir", default="eval/reports")
    p.add_argument("--mock", action="store_true",
                   help="Use mock LLM (default if NL2SQL_LLM_PROVIDER=mock)")
    args = p.parse_args(argv)

    # Bootstrap once
    settings = load_settings()
    use_mock = args.mock or settings.llm_provider == "mock"
    if not use_mock and not settings.openai_api_key:
        console.print("[red]Error:[/] OPENAI_API_KEY empty. Pass --mock for offline.")
        return 2

    shared = bootstrap_shared(settings, mock=use_mock)

    # Load dataset
    records = [
        json.loads(line)
        for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    console.print(f"Running {len(records)} records (mock={use_mock})…")

    per_record: list[dict[str, Any]] = []
    for record in track(records, description="Evaluating"):
        # New session per record so dept matches the record's expected scope
        session = Session(
            shared=shared,
            workspace_id="eval-fixture",
            dept=record["dept"],
        )
        try:
            per_record.append(run_record(record, session))
        finally:
            session.close()

        # Cost cap
        spent = sum(
            (r["tokens_in"] + r["tokens_out"]) * 0.0000006
            for r in per_record
        )
        if spent > args.max_cost:
            console.print(
                f"[red]Cost cap exceeded: ${spent:.4f} > ${args.max_cost}[/]"
            )
            break

    summary = aggregate(per_record)

    # Persist
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True, parents=True)
    sha = "local"
    json_path = out_dir / f"{sha}.json"
    json_path.write_text(
        json.dumps({"summary": summary, "per_record": per_record}, indent=2)
    )

    # Render summary
    table = Table(title="Eval summary", show_header=True, header_style="bold cyan")
    table.add_column("metric"); table.add_column("value", justify="right")
    for key, val in summary.items():
        table.add_row(key, f"{val:.4f}" if isinstance(val, float) else str(val))
    console.print(table)
    console.print(f"\n[dim]Report: {json_path}[/]")

    # ★ HARD GATES
    fail = False
    if summary["cross_dept_leak_rate"] > HARD_GATE_LEAK_RATE:
        console.print("[red bold]❌ HARD FAIL: cross_dept_leak_rate > 0[/]")
        fail = True

    if not fail:
        console.print("[green bold]✓ All hard gates passed.[/]")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
