"""Tests for JAZ-113 Sales Pulse, JAZ-114 Renewal Radar, JAZ-115 Churn EW."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from account_intel.api.app import app
from account_intel.db import get_session
from account_intel.db.models import (
    ActivitySignal, Company, DealSignal, QuoteSignal, TicketSignal,
)
from account_intel.modules import run_all


@pytest.fixture
def client(session_factory):
    def _override():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_high_churn(session_factory):
    with session_factory() as s:
        s.add(Company(id="churn-co", name="Churn Co"))
        # 2 aged HIGH tickets + 1 stalled deal
        s.add(TicketSignal(id="t-h1", company_id="churn-co", subject="VoIP down",
                           is_open=True, age_days=45, priority="HIGH"))
        s.add(TicketSignal(id="t-h2", company_id="churn-co", subject="E911 broken",
                           is_open=True, age_days=60, priority="URGENT"))
        s.add(DealSignal(id="d-1", company_id="churn-co", name="Annual contract",
                         amount=50000, stalled=True, stage="Negotiation"))
        s.commit()


def _seed_renewal_60d(session_factory):
    with session_factory() as s:
        s.add(Company(id="ren-co", name="Renewal Co"))
        close = datetime.now(timezone.utc) + timedelta(days=55)
        s.add(DealSignal(id="d-r1", company_id="ren-co", name="2027 renewal",
                         amount=80000, hs_closed_at=close, stage="Quote sent"))
        s.commit()


def _seed_healthy(session_factory):
    with session_factory() as s:
        s.add(Company(id="happy-co", name="Happy Co"))
        s.add(DealSignal(id="d-h", company_id="happy-co", name="New install",
                         amount=10000, stalled=False, stage="Discovery"))
        s.commit()


# ---- Churn EW ----

def test_churn_high_when_aged_high_priority(session_factory):
    _seed_high_churn(session_factory)
    with session_factory() as s:
        results = run_all(s, "churn-co")
    churn = next(r for r in results if r.module_id == "churn_ew")
    assert churn.severity in {"high", "medium"}
    assert churn.score and churn.score >= 30
    assert any(d["name"] == "high_priority_aged_tickets" for d in churn.drivers)


def test_churn_clean_for_healthy_account(session_factory):
    _seed_healthy(session_factory)
    with session_factory() as s:
        results = run_all(s, "happy-co")
    churn = next(r for r in results if r.module_id == "churn_ew")
    assert churn.severity == "low"
    # Tiny baseline penalty for tracked-but-quiet accounts is acceptable.
    assert (churn.score or 0) < 20


# ---- Renewal Radar ----

def test_renewal_detects_60day_window(session_factory):
    _seed_renewal_60d(session_factory)
    with session_factory() as s:
        results = run_all(s, "ren-co")
    ren = next(r for r in results if r.module_id == "renewal_radar")
    assert ren.severity in {"medium", "high"}
    assert ren.metrics["window"] == "60-day"
    assert 50 <= ren.metrics["days_to_renewal"] <= 60


def test_renewal_na_when_no_renewal_deal(session_factory):
    _seed_healthy(session_factory)
    with session_factory() as s:
        results = run_all(s, "happy-co")
    ren = next(r for r in results if r.module_id == "renewal_radar")
    assert ren.severity == "na"
    assert "No upcoming renewal" in ren.headline


# ---- Sales Pulse ----

def test_sales_pulse_high_when_pipeline_stalled(session_factory):
    _seed_high_churn(session_factory)
    with session_factory() as s:
        results = run_all(s, "churn-co")
    sp = next(r for r in results if r.module_id == "sales_pulse")
    # Stalled deal = 100% of pipeline since only one deal
    assert sp.severity == "high"
    assert sp.metrics["stalled_share_pct"] == 100.0


def test_sales_pulse_na_when_no_deals(session_factory):
    with session_factory() as s:
        s.add(Company(id="bare-co", name="Bare Co"))
        s.commit()
    with session_factory() as s:
        results = run_all(s, "bare-co")
    sp = next(r for r in results if r.module_id == "sales_pulse")
    assert sp.severity == "na"
    assert sp.score is None


# ---- API endpoint ----

def test_modules_api_returns_all_three(client, session_factory):
    _seed_high_churn(session_factory)
    r = client.get("/account/churn-co/modules")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    ids = {m["module_id"] for m in body}
    assert ids == {"churn_ew", "renewal_radar", "sales_pulse"}


def test_modules_api_404(client):
    r = client.get("/account/nope/modules")
    assert r.status_code == 404


def test_scores_differentiate_across_companies(session_factory):
    """Three companies with different signal mixes should produce different
    churn scores (the user-reported bug was 'all scores identical')."""
    with session_factory() as s:
        s.add(Company(id="hot", name="Hot"))
        s.add(Company(id="warm", name="Warm"))
        s.add(Company(id="cold", name="Cold"))
        # Hot: 2 HIGH aged tickets + 1 big stalled deal
        s.add(TicketSignal(id="ht1", company_id="hot", subject="down", is_open=True, age_days=60, priority="HIGH"))
        s.add(TicketSignal(id="ht2", company_id="hot", subject="down2", is_open=True, age_days=45, priority="URGENT"))
        s.add(DealSignal(id="hd1", company_id="hot", name="Big deal", amount=200000, stalled=True, stage="Neg"))
        # Warm: 1 aged ticket normal priority + 1 small stalled deal
        s.add(TicketSignal(id="wt1", company_id="warm", subject="x", is_open=True, age_days=40, priority="MEDIUM"))
        s.add(DealSignal(id="wd1", company_id="warm", name="Mid deal", amount=10000, stalled=True, stage="Quote"))
        # Cold: nothing
        s.commit()

    with session_factory() as s:
        scores = {}
        for cid in ("hot", "warm", "cold"):
            r = run_all(s, cid)
            churn = next(x for x in r if x.module_id == "churn_ew")
            scores[cid] = churn.score or 0
    assert scores["hot"] > scores["warm"] > scores["cold"], scores
    assert (scores["hot"] - scores["cold"]) > 30, scores
