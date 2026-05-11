"""Unit tests for the expansion: property extraction + new endpoints +
summaries heuristic fallback."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from account_intel.api.app import app
from account_intel.db import get_session
from account_intel.db.models import (
    ActivitySignal,
    Company,
    ContactSignal,
    DealSignal,
    QuoteSignal,
    TicketSignal,
)
from account_intel.feeders import extract_properties_from_deal_names
from account_intel.rollup.service import RollupService

# --- property extraction ------------------------------------------------------


def test_property_extraction_known_brand():
    names = [
        "McLaren Technologies Asia Pacific Pte Ltd - Four Seasons Kyoto Japan Spare Devices May 2026",
        "McLaren Technologies - Marina Bay Sands Annual Renewal",
        "Pan Pacific - R5JET-621769 - File Deployment",
        "McLaren Technologies - Marina Bay Sands Q2 Upgrade",
    ]
    props = extract_properties_from_deal_names(names)
    by_name = {p["name"]: p for p in props}
    assert any("Marina Bay Sands" in n for n in by_name)
    # marina bay sands should appear ≥2 times
    mbs = next(p for n, p in by_name.items() if "Marina Bay Sands" in n)
    assert mbs["deal_count"] >= 2


def test_property_extraction_handles_no_brand():
    names = ["Random deal name without anything special"]
    props = extract_properties_from_deal_names(names)
    # may extract nothing or a generic fragment; just must not crash
    assert isinstance(props, list)


def test_property_extraction_empty():
    assert extract_properties_from_deal_names([]) == []


# --- endpoint smoke ------------------------------------------------------------


@pytest.fixture
def client(session_factory):
    def _override():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    now = datetime.now(UTC)
    with session_factory() as s:
        s.add(
            Company(
                id="320995239625",
                name="McLaren International Pty. Ltd",
                domain="mclarenint.com",
                last_refreshed=now,
                open_pipeline_amount=185000,
                support_load_30d=4,
            )
        )
        s.add(
            TicketSignal(
                id="t1",
                company_id="320995239625",
                subject="PMS sync failing",
                priority="HIGH",
                is_open=True,
                age_days=20,
                hs_created_at=now - timedelta(days=20),
                cluster_id="abc123",
            )
        )
        s.add(
            TicketSignal(
                id="t2",
                company_id="320995239625",
                subject="PMS sync failing again",
                priority="HIGH",
                is_open=True,
                age_days=20,
                hs_created_at=now - timedelta(days=20),
                cluster_id="abc123",
            )
        )
        s.add(
            TicketSignal(
                id="t3",
                company_id="320995239625",
                subject="Other unrelated",
                priority="HIGH",
                is_open=True,
                age_days=45,
                hs_created_at=now - timedelta(days=45),
                cluster_id="xyz999",
            )
        )
        s.add(
            DealSignal(
                id="d1",
                company_id="320995239625",
                name="McLaren - MGM Macau spare devices",
                amount=85000,
                stage="Decision Maker Bought-In",
                is_open=True,
                stalled=True,
                days_in_stage=50,
            )
        )
        s.add(
            ContactSignal(
                id="ct1",
                company_id="320995239625",
                first_name="Jason",
                last_name="Lee",
                email="jason.lee@mclarenint.com",
                job_title="Senior Software Specialist",
                last_activity_at=now - timedelta(days=70),
                days_since_activity=70,
            )
        )
        s.add(
            ActivitySignal(
                id="a1",
                company_id="320995239625",
                kind="note",
                subject="Internal note",
                ts=now - timedelta(days=3),
            )
        )
        s.add(
            QuoteSignal(
                id="q1",
                company_id="320995239625",
                deal_id="d1",
                title="MBS Renewal Quote",
                amount=120000,
                status="PENDING_SIGNATURE",
                hs_created_at=now - timedelta(days=30),
            )
        )
        s.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_contacts_endpoint(client):
    r = client.get("/account/320995239625/contacts")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"].startswith("Jason")


def test_activities_endpoint(client):
    r = client.get("/account/320995239625/activities?days=30")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "note"


def test_quotes_endpoint(client):
    r = client.get("/account/320995239625/quotes")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["amount"] == 120000


def test_metrics_endpoint(client):
    r = client.get("/account/320995239625/metrics")
    assert r.status_code == 200
    m = r.json()
    assert m["open_pipeline_amount"] == 185000
    assert m["support_load_30d"] == 4


def test_hot_signals_endpoint(client):
    r = client.get("/account/320995239625/hot_signals")
    assert r.status_code == 200
    rows = r.json()
    # We expect: 1 stalled_deal, 1 aged_ticket (45d), 1 repeat_issue (cluster abc123 ×2),
    # 1 quiet_contact (70d), 1 old_quote (30d)
    kinds = {r_["kind"] for r_ in rows}
    assert "stalled_deal" in kinds
    assert "aged_ticket" in kinds
    assert "repeat_issue" in kinds
    assert "quiet_contact" in kinds
    assert "old_quote" in kinds


def test_properties_endpoint(client):
    r = client.get("/account/320995239625/properties")
    assert r.status_code == 200
    # Single deal name → may or may not match a known brand; just smoke
    assert isinstance(r.json(), list)


def test_account_view_includes_summaries(client):
    """Ensures the assessment payload has summaries_json with multi-zoom keys."""
    r = client.get("/account/320995239625")
    assert r.status_code == 200
    a = r.json()["assessment"]
    assert a is not None
    s = a.get("summaries") or {}
    # heuristic fallback produces all expected keys
    for k in (
        "tldr",
        "support_summary",
        "sales_summary",
        "relationship_summary",
        "risk_drivers",
        "opportunities",
        "client_tldr",
        "client_insights",
    ):
        assert k in s, f"missing key {k}"


def test_summaries_refresh_endpoint(client):
    r = client.post("/account/320995239625/refresh_summaries")
    assert r.status_code == 200
    out = r.json()
    assert "summaries" in out
    assert out["summaries"]["tldr"]


# --- heuristic summaries are deterministic without Claude key -----------------


def test_heuristic_summaries_stable_shape():
    payload = {
        "company": {"name": "Test Corp"},
        "tickets": [
            {"is_open": True, "age_days": 40, "priority": "HIGH"},
            {"is_open": False, "age_days": 90},
        ],
        "deals": [
            {"is_open": True, "stalled": True, "amount": 50000, "stage": "X"},
        ],
        "activities": [{"kind": "note", "ts": "2026-05-08T00:00:00Z"}],
        "contacts": [{"name": "Jane", "title": "Mgr"}],
        "metrics": {
            "open_pipeline_amount": 50000,
            "won_amount_90d": 30000,
            "repeat_issue_count": 1,
            "days_since_last_activity": 50,
        },
    }
    out = RollupService._heuristic_summaries(payload)
    assert out["tldr"].startswith("Test Corp")
    assert "ticket" in out["support_summary"].lower()
    assert "deal" in out["sales_summary"].lower()
    assert isinstance(out["risk_drivers"], list)
    assert isinstance(out["opportunities"], list)
    assert "client_tldr" in out
    assert "client_insights" in out
