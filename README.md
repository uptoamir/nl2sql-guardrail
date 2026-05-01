# NL2SQL Guardrail Data Agent

[**▶ Live demo →**](https://nl2sql-guardrail-production.up.railway.app/)
&nbsp;·&nbsp;
[GitHub](https://github.com/uptoamir/nl2sql-guardrail)

Natural-language → SQL agent over `employees.db` with an **8-layer guardrail**
that makes cross-department data leakage **structurally impossible**. Two
launch modes (CLI + browser UI), full feature parity, real defense-in-depth.

## 30 seconds — single command

```bash
git clone https://github.com/uptoamir/nl2sql-guardrail.git && cd nl2sql-guardrail

# Browser UI (offline, no API key)
./nl2sql ui --mock

# Terminal REPL (offline, no API key)
./nl2sql cli --mock

# With your OpenAI key
./nl2sql ui  --openai-key=sk-...
./nl2sql cli --openai-key=sk-...
```

The launcher auto-bootstraps `uv` + the venv on first run (~30s cold,
~5s subsequent).

### Per-OS setup

The launcher is a Bash script and runs natively on macOS and Linux.
On Windows, use **WSL2** (recommended) or **Git Bash**.

<details>
<summary><strong>macOS</strong></summary>

```bash
# 1. Install uv (Python package manager) — auto-installs Python 3.12 if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. (Optional) Install Docker Desktop if you want --runtime=docker
#    https://www.docker.com/products/docker-desktop/

# 3. Clone + run
git clone https://github.com/uptoamir/nl2sql-guardrail.git
cd nl2sql-guardrail
./nl2sql ui --mock
```
</details>

<details>
<summary><strong>Linux (Ubuntu/Debian/Fedora/Arch)</strong></summary>

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. (Optional) Docker Engine + Compose plugin
#    Ubuntu/Debian: https://docs.docker.com/engine/install/ubuntu/
#    Fedora:        https://docs.docker.com/engine/install/fedora/

# 3. Clone + run
git clone https://github.com/uptoamir/nl2sql-guardrail.git
cd nl2sql-guardrail
chmod +x nl2sql
./nl2sql ui --mock
```
</details>

<details>
<summary><strong>Windows — WSL2 (recommended)</strong></summary>

```powershell
# 1. Install WSL2 with Ubuntu (one-time, in PowerShell as admin)
wsl --install -d Ubuntu

# 2. Restart, open the Ubuntu shell. From here, everything is Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. (Optional) Docker Desktop with WSL2 backend:
#    https://docs.docker.com/desktop/setup/install/windows-install/

# 4. Clone + run (inside Ubuntu shell)
git clone https://github.com/uptoamir/nl2sql-guardrail.git
cd nl2sql-guardrail
./nl2sql ui --mock
```

> **Note**: open the UI in your Windows browser at `http://localhost:8501` —
> WSL2 forwards the port automatically.

</details>

<details>
<summary><strong>Windows — Git Bash (no WSL)</strong></summary>

```bash
# 1. Install Git for Windows (includes Git Bash)
#    https://git-scm.com/download/win

# 2. Install uv via PowerShell (one-time)
#    Run in PowerShell:
#    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 3. Open Git Bash, then:
git clone https://github.com/uptoamir/nl2sql-guardrail.git
cd nl2sql-guardrail
./nl2sql ui --mock
```

> **Caveat**: some Bash features in the launcher (e.g., `[[ -t 0 && -t 1 ]]`)
> behave slightly differently in Git Bash than in real Bash. WSL2 is more
> reliable.

</details>

## What you'll see

Both modes start by logging the random department selection per the
spec's mandatory startup guardrail:

```
[INFO] Department selected: Engineering (random)
```

Then accept NL questions. Try the 5 spec examples + 10 more in
`/samples` (CLI) or the sidebar (UI):

1. *Who are the software engineers?*
2. *Which employees have an AWS certification?*  ← the core guardrail test
3. *What is the average salary?*
4. *List employees who started after 2023 and their certifications*
5. *Who has the highest remaining benefits balance?*

Every result shows the **generated SQL** (collapsible) and the
**8-layer guardrail trace** (every defensive layer that fired).

## Architecture (one paragraph)

The agent is a **6-stage pipeline** (Intent → Linker → Drafter → Critic
→ Executor → Interpreter), each stage a typed Pydantic handoff. Outputs
flow through an **8-layer defense-in-depth guardrail**: prompt scoping
→ JSON contract → AST validate → AST rewrite → RO connection → SQLite
`set_authorizer` → result audit → multi-tenant scope. Engine-level
enforcement via `set_authorizer` makes the guardrail bypass-resistant
even if the Python validator is monkey-patched.
Reflexion-style bounded-retry repair loop handles invalid SQL. Every
turn writes a structured audit record. The CLI and Streamlit UI consume
the **same** `Session` + `Pipeline` API — they're truly feature-equivalent.

## Run modes

```bash
# Local Python (default — fastest)
./nl2sql cli --openai-key=sk-...
./nl2sql ui  --openai-key=sk-...

# Docker runtime (no Python locally)
./nl2sql cli --openai-key=sk-... --runtime=docker
./nl2sql ui  --openai-key=sk-... --runtime=docker

# Reproducible / forced dept (for demos)
./nl2sql cli --mock --seed=42 --department=Engineering

# Single-shot (for scripts / CI)
./nl2sql cli --mock --question="average salary?"

# Tests + eval
./nl2sql test
./nl2sql eval --mock      # offline; --openai-key=sk-... for real LLM
```

## Slash commands (CLI)

29 commands. Type `/help` for the list. Highlights:

- `/sql` `/trace` `/layers` — inspect the last turn
- `/explain` — plain-English reasoning chain
- `/samples` `/ask <n>` — curated examples
- `/raw <sql>` — submit SQL directly (still through guardrail!) — the
  live guardrail demo: `/raw SELECT * FROM Employee` is denied at L3
- `/edit` — open last SQL in $EDITOR; re-run through the same guardrail
- `/save <name>` `/run <name>` `/list bookmarks` — operator memory
- `/cost` `/cache` `/scope` `/redact` — visibility into agent state

## Defense-in-depth proof (run it yourself)

```bash
./nl2sql cli --mock
❯ /raw SELECT * FROM Employee
L3 rejected: base_table_ref base_table_Employee  ← sqlglot AST validator catches it

❯ /raw SELECT * FROM allowed_employees
[34 rows in your active department]            ← scoped data only
```

Even if a future bug bypassed L3, the L6 SQLite `set_authorizer` denies
direct base-table reads at the C-engine level: `not authorized`.

## What's actually here

```
src/nl2sql/                   The agent
  guardrails/                  → 4 deterministic layers (validator, rewriter,
                                  authorizer, result_audit)
  agent/                       → Pipeline + 6 stages
  llm/                         → OpenAI + Mock + cost/budget/router
  schema/ rag/                 → Schema graph + BM25 retriever
  observability/ governance/   → Metrics + audit log + PII redaction
  cli/  prompts/               → CLI REPL + 5 stage prompt templates
streamlit_app.py              The UI
tests/                        195 tests passing (unit + property + redteam + integration)
eval/                         30-record golden set + run_eval.py harness
final_1.md                    Full production-engineering spec (~9,300 lines)
audit_doc.md                  Implementation audit log
ARCHITECTURE.md               Architecture decisions + threat model
DEMO.md                       5-minute reviewer walkthrough
SECURITY.md                   Vulnerability disclosure process
```

## What's deferred to v1 (honest)

`final_1.md` describes a full enterprise production service with
multi-tenant scope, multi-region (PIPEDA / GDPR data residency), SLSA
L3 supply chain, OTel observability, eval-driven CI gating, runbooks,
SOC 2 evidence collection. **The take-home submission is a focused
subset**: 8-layer guardrail, dual-launch CLI+UI, 195 tests, 30-record
eval with `cross_dept_leak_rate=0` hard gate. Everything else is
documented as the v1 rollout roadmap.

## AI tooling note

Built end-to-end with [Claude Code](https://www.anthropic.com/claude-code).
Specifically useful for: surfacing edge cases (rowid leak, the orphan
Cert/Benefits IN-vs-EXISTS issue, NULL-bonus aggregation ambiguity, the
TEMP-VIEW-before-authorizer ordering); generating Hypothesis property
strategies; writing the 30-prompt adversarial red-team corpus; iterating
on the v3.0 → v3.11 plan with five rounds of self-audit.

## License

MIT — see `LICENSE`.
