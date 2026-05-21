"""Tests for sales mode endpoints (briefs, deal explainer, email drafter, pipeline)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from account_intel.api.app import app
from account_intel.db import get_session
from account_intel.db.models import (
    ActivitySignal, Company, ContactSignal, DealSignal, TicketSignal,
)


@pytest.fixture
def client(session_factory, monkeypatch):
    # Force heuristic fallback path (no Anthropic key).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _override():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    with session_factory() as s:
        s.add(Company(id="sales-co", name="Sales Test Hotel", domain="sales.example"))
        s.add(DealSignal(
            id="d-stalled", company_id="sales-co", name="2027 renewal expansion",
            amount=120000, stage="Quote sent", days_in_stage=45,
            stalled=True, is_won=False, is_lost=False, is_open=True,
        ))
        s.add(DealSignal(
            id="d-active", company_id="sales-co", name="New install",
            amount=15000, stage="Discovery", days_in_stage=10,
            stalled=False, is_won=False, is_lost=False, is_open=True,
        ))
        s.add(TicketSignal(
            id="t-1", company_id="sales-co", subject="VoIP outage",
            is_open=True, age_days=35, priority="HIGH",
        ))
        s.add(ContactSignal(
            id="c-1", company_id="sales-co",
            first_name="Maria", last_name="Lopez",
            email="m.lopez@hyatt.example", job_title="GM",
            last_activity_at=datetime.now(timezone.utc) - timedelta(days=8),
        ))
        s.add(ActivitySignal(
            id="a-1", company_id="sales-co", kind="EMAIL",
            subject="Quote follow-up", direction="OUTGOING",
            ts=datetime.now(timezone.utc) - timedelta(days=20),
        ))
        s.commit()

    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- pre-call brief ----

def test_precall_brief_returns_markdown_with_sections(client):
    r = client.get("/account/sales-co/sales/precall_brief")
    assert r.status_code == 200
    body = r.json()
    assert body["is_fallback"] is True  # No Anthropic key in tests
    assert "Pre-call Brief" in body["markdown"]
    # Heuristic fallback should mention the open deal value somewhere
    assert "120,000" in body["markdown"] or "120000" in body["markdown"] or "$120" in body["markdown"]


def test_precall_brief_404_for_unknown(client):
    r = client.get("/account/nope/sales/precall_brief")
    assert r.status_code == 404


# ---- stalled deal explainer ----

def test_explain_deal_returns_explanation(client):
    r = client.get("/account/sales-co/sales/explain_deal/d-stalled")
    assert r.status_code == 200
    body = r.json()
    assert body["deal_id"] == "d-stalled"
    assert body["deal_name"] == "2027 renewal expansion"
    assert "stalled" in body["markdown"].lower()


def test_explain_deal_404_for_wrong_company(client):
    r = client.get("/account/sales-co/sales/explain_deal/d-nonexistent")
    assert r.status_code == 404


# ---- email drafter ----

def test_draft_email_uses_active_contact_when_unspecified(client):
    r = client.post("/account/sales-co/sales/draft_email", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["suggested_to_email"] == "m.lopez@hyatt.example"
    assert body["suggested_to_name"] == "Maria Lopez"
    assert body["subject"]
    assert body["body"]


def test_draft_email_with_specific_deal(client):
    r = client.post("/account/sales-co/sales/draft_email", json={"deal_id": "d-stalled"})
    assert r.status_code == 200
    body = r.json()
    # Heuristic falls back to deal name in subject
    assert "renewal" in body["subject"].lower() or "follow" in body["subject"].lower()


# ---- sales pipeline ----

def test_sales_pipeline_lists_account_by_open_value(client):
    r = client.get("/sales/pipeline?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    hit = body[0]
    assert hit["company_id"] == "sales-co"
    assert hit["open_deals"] == 2
    assert hit["open_deal_value"] == 135000.0
    assert hit["stalled_deals"] == 1
