"""FastAPI endpoint smoke tests using an in-memory DB."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from account_intel.api.app import app
from account_intel.db import get_session
from account_intel.db.models import Company, TicketSignal


@pytest.fixture
def client(session_factory):
    def _override():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    with session_factory() as s:
        s.add(
            Company(
                id="320895019724",
                name="McLaren Technologies APAC",
                domain="mclarentechnologies.com",
                last_refreshed=datetime.now(UTC),
            )
        )
        s.add(
            TicketSignal(
                id="t1",
                company_id="320895019724",
                subject="PMS sync",
                priority="HIGH",
                is_open=True,
                age_days=10,
            )
        )
        s.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_search_companies(client):
    r = client.get("/companies/search", params={"q": "mclaren"})
    assert r.status_code == 200
    hits = r.json()
    assert len(hits) == 1
    assert hits[0]["id"] == "320895019724"


def test_account_view(client):
    r = client.get("/account/320895019724")
    assert r.status_code == 200
    data = r.json()
    assert data["company"]["name"] == "McLaren Technologies APAC"
    assert len(data["tickets"]) == 1
    assert data["tickets"][0]["hubspot_url"].startswith("https://app.hubspot.com/")
    # heuristic fallback should run (no Claude key in tests)
    assert data["assessment"] is not None
    assert data["assessment"]["risk_flag"] in {"red", "yellow", "green"}


def test_account_404(client):
    r = client.get("/account/doesnotexist")
    assert r.status_code == 404


def test_triage_book(session_factory):
    """JAZ-256 — ranks accounts by composite risk + ticket + silence + revenue."""
    from account_intel.api.app import app as fresh_app
    from account_intel.db import get_session as _get_session

    def _override():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    fresh_app.dependency_overrides[_get_session] = _override
    with session_factory() as s:
        # high-risk: red flag + aged ticket
        s.add(Company(
            id="hi", name="HighRisk Co", risk_score=85.0,
            stuck_deals_count=2, days_since_last_activity=20.0,
            annual_revenue=500_000, last_refreshed=datetime.now(UTC),
        ))
        s.add(TicketSignal(
            id="t_hi", company_id="hi", subject="Down", is_open=True, age_days=25,
        ))
        # low-risk: clean
        s.add(Company(
            id="lo", name="Quiet Co", risk_score=10.0,
            stuck_deals_count=0, days_since_last_activity=2.0,
            annual_revenue=50_000, last_refreshed=datetime.now(UTC),
        ))
        # zero-signal: should be filtered
        s.add(Company(id="zz", name="Empty Co", last_refreshed=datetime.now(UTC)))
        s.commit()

    try:
        c = TestClient(fresh_app)
        r = c.get("/triage/book", params={"limit": 10})
        assert r.status_code == 200
        hits = r.json()
        assert len(hits) >= 1
        # high-risk must outrank low-risk
        ids = [h["company_id"] for h in hits]
        assert "hi" in ids
        if "lo" in ids:
            assert ids.index("hi") < ids.index("lo")
        top = next(h for h in hits if h["company_id"] == "hi")
        assert top["triage_score"] > 0
        assert top["aged_tickets"] == 1
        assert top["top_reasons"]
        assert top["hubspot_url"].startswith("https://app.hubspot.com/")
    finally:
        fresh_app.dependency_overrides.clear()
