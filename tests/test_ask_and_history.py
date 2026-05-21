"""Tests for JAZ-185 Ask AI + JAZ-187 risk history + JAZ-186 NBA update."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from account_intel.api.app import app
from account_intel.db import get_session
from account_intel.db.models import AIAssessment, Company, TicketSignal


@pytest.fixture
def client(session_factory):
    def _override():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    # seed company + a few historical assessments
    with session_factory() as s:
        s.add(Company(id="cid-test", name="Test Hotel", domain="test.example"))
        s.add(TicketSignal(
            id="t-1", company_id="cid-test", subject="VoIP outage",
            is_open=True, age_days=12, priority="HIGH",
        ))
        # 3 historical assessments — green → yellow → red trajectory
        base = datetime.utcnow() - timedelta(days=30)
        for i, (flag, score) in enumerate([("green", 15), ("yellow", 50), ("red", 75)]):
            s.add(AIAssessment(
                company_id="cid-test", risk_flag=flag, risk_score=score,
                narrative=f"narrative {i}", next_best_actions=[
                    {"action": "Investigate VoIP outage", "priority": "high"},
                    {"action": "Schedule QBR", "priority": "medium"},
                ],
                generated_at=base + timedelta(days=i*10),
                model="test-model",
            ))
        s.commit()

    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- JAZ-185 Ask AI ----

def test_ask_requires_question(client):
    r = client.post("/account/cid-test/ask", json={"question": "  "})
    assert r.status_code == 400


def test_ask_404_for_unknown_company(client):
    r = client.post("/account/nope/ask", json={"question": "hello"})
    assert r.status_code == 404


def test_ask_returns_heuristic_when_no_claude(client):
    # No ANTHROPIC_API_KEY set in test env → heuristic path
    r = client.post("/account/cid-test/ask", json={"question": "any open tickets?"})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "heuristic-fallback"
    assert "ticket" in body["answer"].lower()
    assert isinstance(body["citations"], list)


# ---- JAZ-187 Risk history ----

def test_risk_history_returns_chronological(client):
    r = client.get("/account/cid-test/risk_history")
    assert r.status_code == 200
    points = r.json()
    assert len(points) == 3
    # Should be oldest → newest after the reversed() in app.py
    assert [p["risk_flag"] for p in points] == ["green", "yellow", "red"]
    assert [p["risk_score"] for p in points] == [15, 50, 75]


def test_risk_history_404_for_unknown(client):
    r = client.get("/account/nope/risk_history")
    assert r.status_code == 404


def test_risk_history_respects_limit(client):
    r = client.get("/account/cid-test/risk_history?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2


# ---- JAZ-186 NBA update ----

def test_nba_mark_done(client, session_factory):
    r = client.post("/account/cid-test/nba/update",
                    json={"action_index": 0, "status": "done"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    # Verify persisted directly on the latest assessment row.
    from sqlalchemy import select, desc
    with session_factory() as s:
        row = s.scalars(
            select(AIAssessment)
            .where(AIAssessment.company_id == "cid-test")
            .order_by(desc(AIAssessment.generated_at))
            .limit(1)
        ).first()
        assert row.next_best_actions[0]["status"] == "done"
        assert "updated_at" in row.next_best_actions[0]
        # Other action untouched
        assert row.next_best_actions[1].get("status") != "done"


def test_nba_rejects_invalid_status(client):
    r = client.post("/account/cid-test/nba/update",
                    json={"action_index": 0, "status": "garbage"})
    assert r.status_code == 400


def test_nba_rejects_out_of_range_index(client):
    r = client.post("/account/cid-test/nba/update",
                    json={"action_index": 99, "status": "done"})
    assert r.status_code == 400
