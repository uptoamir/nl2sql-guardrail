"""Per-user bookmark storage at ``~/.nl2sql/bookmarks.json``.

Per `final_1.md` §39.5 + §41.8. Bookmarks are operator memory, NOT
tenant data — keyed per OS user, not per workspace. The ``workspace_id``
inside each entry exists so cross-workspace replays warn the operator.

The path can be overridden via ``NL2SQL_BOOKMARKS_DIR`` — needed in
read-only container deployments where ``$HOME`` is not writable. The
launcher's docker-compose sets this to the writable cache volume.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast


def _bookmarks_dir() -> Path:
    override = os.environ.get("NL2SQL_BOOKMARKS_DIR")
    return Path(override) if override else Path.home() / ".nl2sql"


BOOKMARKS_PATH = _bookmarks_dir() / "bookmarks.json"


def _load_raw() -> dict[str, Any]:
    if not BOOKMARKS_PATH.exists():
        return {"version": 1, "bookmarks": {}}
    try:
        return cast("dict[str, Any]", json.loads(BOOKMARKS_PATH.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return {"version": 1, "bookmarks": {}}


def _save_raw(data: dict[str, Any]) -> None:
    BOOKMARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_bookmark(name: str, question: str, workspace_id: str) -> None:
    data = _load_raw()
    data["bookmarks"][name] = {
        "question": question,
        "workspace_id": workspace_id,
        "created_at": datetime.now(UTC).isoformat(),
        "last_run_at": None,
        "run_count": 0,
    }
    _save_raw(data)


def load_bookmarks(workspace_id: str | None = None) -> dict[str, dict[str, Any]]:
    data = _load_raw()
    bms: dict[str, dict[str, Any]] = data.get("bookmarks", {})
    if workspace_id is None:
        return bms
    return {k: v for k, v in bms.items() if v.get("workspace_id") == workspace_id}


def forget_bookmark(name: str) -> None:
    data = _load_raw()
    data.get("bookmarks", {}).pop(name, None)
    _save_raw(data)


def touch_bookmark(name: str) -> None:
    """Increment run_count + update last_run_at."""
    data = _load_raw()
    bm = data.get("bookmarks", {}).get(name)
    if bm:
        bm["run_count"] = bm.get("run_count", 0) + 1
        bm["last_run_at"] = datetime.now(UTC).isoformat()
        _save_raw(data)
