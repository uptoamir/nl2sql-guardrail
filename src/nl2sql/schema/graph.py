"""Schema graph — v0 minimal token-match implementation.

Per `final_1.md` §5 + §41.5. The load-bearing API is
:meth:`SchemaGraph.relevant_tables_for_question` — its return type
(``list[str]``) is stable across v0/v1/v2 (§31). Underneath, v0 uses
plain token matching; v1 will swap in bge-small dense retrieval; v2
goes to GraphRAG. Same call site, different impl.

For the 3-table fixture, token-match is equivalent in recall to dense
retrieval (we only have ~13 columns total — every reasonable question
matches at least one).
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from nl2sql.db.introspect import introspect_schema, value_domain

_TABLE_VIEW_MAP = {
    "Employee": "allowed_employees",
    "Certification": "allowed_certifications",
    "Benefits": "allowed_benefits",
}


class SchemaGraph:
    """Minimal v0 schema container.

    Holds:
      - introspected schema (tables + columns + FKs)
      - column business descriptions (loaded from descriptions.yaml)
      - low-cardinality value domains (e.g., Department enum)
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self._conn = conn
        self.schema: dict[str, Any] = {}
        self.descriptions: dict[str, str] = descriptions or {}
        self.value_domains: dict[str, list[Any]] = {}

    @classmethod
    def load_descriptions(cls) -> dict[str, str]:
        """Load `descriptions.yaml` from the package, with a filesystem fallback."""
        try:
            text = (
                resources.files("nl2sql.schema")
                .joinpath("descriptions.yaml")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError, AttributeError):
            # Fallback for editable installs / tests.
            here = Path(__file__).parent / "descriptions.yaml"
            if here.exists():
                text = here.read_text(encoding="utf-8")
            else:
                return {}
        return yaml.safe_load(text) or {}

    def build(self) -> None:
        """Introspect schema + extract value domains for low-cardinality columns."""
        self.schema = introspect_schema(self._conn)
        # Value domains for known low-card columns
        self.value_domains["Employee.Department"] = value_domain(
            self._conn, "Employee", "Department"
        )
        self.value_domains["Benefits.BenefitsPackage"] = value_domain(
            self._conn, "Benefits", "BenefitsPackage"
        )

    # ─── The load-bearing API (stable across v0/v1/v2) ──────────────────
    def relevant_tables_for_question(self, question: str) -> list[str]:
        """Return allowed_* views relevant to ``question`` (token-match in v0).

        Returns at least ``["allowed_employees"]`` so the prompt always
        has somewhere to anchor.
        """
        q = question.lower()
        tables: set[str] = {"allowed_employees"}  # always relevant
        if any(
            tok in q
            for tok in (
                "cert",
                "certification",
                "aws",
                "azure",
                "kubernetes",
                "scrum",
                "google",
                "comptia",
                "pmp",
            )
        ):
            tables.add("allowed_certifications")
        if any(tok in q for tok in ("benefit", "balance", "bronze", "silver", "gold", "platinum")):
            tables.add("allowed_benefits")
        return sorted(tables)

    def render_for_prompt(self, tables: list[str] | None = None) -> str:
        """Render the schema as Markdown describing ONLY the allowed_* views.

        Critical: base table names (Employee, Certification, Benefits) are
        NEVER mentioned. The LLM's view of the world is the allowed_* views
        only. This is Layer 1 of the guardrail.
        """
        if tables is None:
            tables = ["allowed_employees", "allowed_certifications", "allowed_benefits"]
        lines: list[str] = []
        for view in tables:
            base = next((b for b, v in _TABLE_VIEW_MAP.items() if v == view), None)
            if not base or base not in self.schema.get("tables", {}):
                continue
            lines.append(f"### `{view}` (department-scoped)")
            for col in self.schema["tables"][base]["columns"]:
                desc_key = f"{view}.{col['name']}"
                desc = self.descriptions.get(desc_key, "")
                nullable = "" if col["notnull"] else " (nullable)"
                pk = " PK" if col["pk"] else ""
                lines.append(f"  - **{col['name']}** {col['type']}{nullable}{pk}")
                if desc:
                    lines.append(f"    {desc}")
            # Value domains where applicable
            for full_col, vals in self.value_domains.items():
                col_name = full_col.split(".", 1)[1]
                if full_col.startswith(base + ".") and vals:
                    quoted = ", ".join(f"`{v}`" for v in vals)
                    lines.append(f"  - {col_name} value domain: {quoted}")
            lines.append("")
        return "\n".join(lines)
