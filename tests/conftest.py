"""Shared test scaffolding for the merged customer-brain repo.

Provides:
- session_factory:  in-memory SQLite for account_intel ORM tests
- isolated_env:     per-test temp DB + cleared env for support_admin tests (autouse)
- fixture_tickets:  canned HubSpot ticket payloads for support_admin tests
- vip_csv:          tmp VIP list for support_admin VIP routing tests
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from account_intel.db.models import Base


# ---------------------------------------------------------------------------
# account_intel: SQLAlchemy in-memory session factory
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory():
    """In-memory SQLite shared across threads (StaticPool). Schema fresh per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# support_admin: per-test env isolation + deterministic embeddings
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Each test gets a fresh DB path, no real HubSpot/OpenAI keys, no VIP list."""
    from support_admin import config as config_mod
    from support_admin import embeddings as embeddings_mod
    from support_admin import vip as vip_mod

    db_path = tmp_path / "support_admin.db"
    monkeypatch.delenv("HUBSPOT_PRIVATE_APP_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VIP_LIST_PATH", raising=False)
    monkeypatch.setenv("SUPPORT_ADMIN_DB", str(db_path))
    monkeypatch.setenv("REPEAT_SIM_THRESHOLD", "0.85")
    monkeypatch.setenv("REPEAT_WINDOW_DAYS", "30")

    config_mod.reset_settings_cache()
    embeddings_mod.reset_embedder_cache()
    vip_mod.reset_vip_cache()

    backend = embeddings_mod.HashingBackend(dim=128)
    embeddings_mod._default = embeddings_mod.Embedder(db_path=db_path, backend=backend)

    yield

    config_mod.reset_settings_cache()
    embeddings_mod.reset_embedder_cache()
    vip_mod.reset_vip_cache()


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def fixture_tickets() -> list[dict]:
    return [
        {"id": "T-1", "properties": {
            "subject": "PMS keeps disconnecting from PBX",
            "content": "Tiger PMS link drops every few hours.",
            "hubspot_company_id": "C-100",
            "createdate": _iso(2),
            "error_code": "PMS-LINK-503",
        }},
        {"id": "T-2", "properties": {
            "subject": "PMS disconnecting from PBX again",
            "content": "Same disconnect issue, link drops repeatedly.",
            "hubspot_company_id": "C-100",
            "createdate": _iso(1),
            "error_code": "PMS-LINK-503",
        }},
        {"id": "T-3", "properties": {
            "subject": "Wake-up calls not syncing to rooms",
            "content": "Wake-up call schedule isn't pushed.",
            "hubspot_company_id": "C-200",
            "createdate": _iso(1),
            "error_code": "WAKEUP-SYNC",
        }},
        {"id": "T-4", "properties": {
            "subject": "Minibar charges missing on folio",
            "content": "Brand new question about minibar billing.",
            "hubspot_company_id": "C-300",
            "createdate": _iso(0),
        }},
    ]


@pytest.fixture
def vip_csv(tmp_path) -> Path:
    csv_path = tmp_path / "vip_list.csv"
    csv_path.write_text(
        "hubspot_company_id,name,tier\n"
        "C-100,Acme Hotels,platinum\n"
        "C-200,Beachfront Resort,gold\n"
    )
    return csv_path
