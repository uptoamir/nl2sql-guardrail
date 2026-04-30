"""Shared pytest fixtures."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

# Force mock provider for the entire test suite — no API keys consumed.
os.environ.setdefault("NL2SQL_LLM_PROVIDER", "mock")


@pytest.fixture
def fixture_db_path(tmp_path: Path) -> Path:
    """A copy of the canonical employees.db in a tmp dir.

    Tests that mutate the DB or open multiple connections can use this
    without polluting the source fixture.
    """
    src = Path(__file__).parent.parent / "employees.db"
    dst = tmp_path / "employees.db"
    shutil.copy(src, dst)
    return dst


@pytest.fixture
def session_conn(fixture_db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open_session-style connection (RO + TEMP VIEWs + authorizer)."""
    from nl2sql.db.connection import open_session

    conn = open_session(fixture_db_path, dept="Engineering", workspace_id="local-fixture")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def shared_resources(fixture_db_path: Path):
    """Bootstrapped SharedResources in mock mode."""
    from nl2sql.config import Settings
    from nl2sql.session import bootstrap_shared

    settings = Settings(db_path=fixture_db_path, llm_provider="mock")
    return bootstrap_shared(settings, mock=True)


@pytest.fixture
def session(shared_resources):
    """A Session in Engineering scope, mock mode."""
    from nl2sql.session import Session

    s = Session(
        shared=shared_resources,
        workspace_id="local-fixture",
        dept="Engineering",
    )
    yield s
    s.close()
