"""PII redaction — HMAC-SHA256 with per-run ephemeral salt.

Per `final_1.md` §14.1 (P1-12). Hash long-term audit-log fields with a
salt that lives only in process memory and is never persisted, so a
stolen log file can't be correlated to identity.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

# Cache loaded policies so we don't re-read the YAML for every redact call.
_POLICIES: dict[str, Any] | None = None


def load_policies() -> dict[str, Any]:
    """Load `policies.yaml` from the package."""
    global _POLICIES  # noqa: PLW0603 - module-level cache, set once on first load
    if _POLICIES is not None:
        return _POLICIES
    try:
        text = (
            resources.files("nl2sql.governance")
            .joinpath("policies.yaml")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        here = Path(__file__).parent / "policies.yaml"
        text = here.read_text(encoding="utf-8")
    _POLICIES = yaml.safe_load(text) or {}
    return _POLICIES


def hash_pii(value: str, salt: bytes) -> str:
    """HMAC-SHA256 hex-truncated to 16 chars. For long-term storage only."""
    return hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


class RedactingProcessor:
    """structlog processor that hashes PII fields BEFORE the JSON renderer.

    Salt is per-run, ephemeral (only in process memory). Salt is NOT
    logged, NOT persisted.
    """

    def __init__(self, run_id: str | None = None) -> None:  # noqa: ARG002 - kept for API
        self.salt = secrets.token_bytes(32)
        policies = load_policies()
        self._pii_keys: set[str] = set(policies.get("keys_to_redact", []))

    def __call__(
        self, _logger: Any, _method_name: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        """Hash any matching PII keys in the event_dict in-place."""
        for k in list(event_dict):
            if k in self._pii_keys and isinstance(event_dict[k], str):
                event_dict[k] = hash_pii(event_dict[k], self.salt)
            elif k == "supporting_rows" and isinstance(event_dict[k], list):
                event_dict[k] = [self._redact_row(r) for r in event_dict[k]]
        return event_dict

    def _redact_row(self, row: Any) -> Any:
        if not isinstance(row, dict):
            return row
        return {
            k: hash_pii(v, self.salt) if (k in self._pii_keys and isinstance(v, str)) else v
            for k, v in row.items()
        }
