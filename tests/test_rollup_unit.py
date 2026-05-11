"""Unit tests for the Claude roll-up service.

Tests the heuristic fallback (which Claude unavailability triggers),
the signal-hash caching path, and payload assembly.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from account_intel.db.models import Company, DealSignal, TicketSignal
from account_intel.rollup.service import RollupService, build_signals_payload, hash_signals


def _seed_mclaren(session_factory) -> str:
    with session_factory() as s:
        c = Company(
            id="320895019724",
            name="McLaren Technologies APAC",
            domain="mclarentechnologies.com",
            industry="Hospitality",
            country="Singapore",
        )
        s.add(c)
        now = datetime.now(UTC)
        # 1 critical aged open ticket → should flip red
        s.add(
            TicketSignal(
                id="t1",
                company_id=c.id,
                subject="PMS sync failing",
                priority="HIGH",
                is_open=True,
                hs_created_at=now - timedelta(days=42),
                age_days=42,
            )
        )
        # 2 routine open tickets
        for tid, age in [("t2", 5), ("t3", 10)]:
            s.add(
                TicketSignal(
                    id=tid,
                    company_id=c.id,
                    subject=f"Misc {tid}",
                    priority="LOW",
                    is_open=True,
                    hs_created_at=now - timedelta(days=age),
                    age_days=age,
                )
            )
        # stalled deal
        s.add(
            DealSignal(
                id="d1",
                company_id=c.id,
                name="Expansion",
                amount=85000.0,
                stage="Decision Maker Bought-In",
                pipeline="Sales",
                is_open=True,
                stalled=True,
                days_in_stage=47,
                last_activity=now - timedelta(days=47),
            )
        )
        s.commit()
    return "320895019724"


def test_build_signals_payload(session_factory):
    cid = _seed_mclaren(session_factory)
    with session_factory() as s:
        payload = build_signals_payload(s, cid)
    assert payload["company"]["name"] == "McLaren Technologies APAC"
    assert len(payload["tickets"]) == 3
    assert len(payload["deals"]) == 1
    assert payload["deals"][0]["stalled"] is True


def test_hash_stable():
    p = {"a": 1, "b": [1, 2, 3]}
    h1 = hash_signals(p)
    h2 = hash_signals({"b": [1, 2, 3], "a": 1})
    assert h1 == h2 and len(h1) == 64


def test_heuristic_fallback_flags_red():
    """McLaren pattern: aged HIGH ticket + stalled deal → red."""
    payload = {
        "company": {"name": "McLaren"},
        "tickets": [
            {"id": "t1", "is_open": True, "age_days": 42, "priority": "HIGH"},
            {"id": "t2", "is_open": True, "age_days": 5, "priority": "LOW"},
            {"id": "t3", "is_open": True, "age_days": 10, "priority": "LOW"},
        ],
        "deals": [{"id": "d1", "stalled": True, "amount": 85000, "is_open": True}],
        "integrations": [],
    }
    result = RollupService._heuristic_fallback(payload)
    assert result["risk_flag"] == "red"
    assert result["risk_score"] >= 70
    assert any("HIGH" in a["action"] or "Escalate" in a["action"] for a in result["next_best_actions"])


def test_heuristic_fallback_green_when_healthy():
    payload = {
        "company": {"name": "HappyHotel"},
        "tickets": [{"id": "t1", "is_open": False, "priority": "LOW", "age_days": 3}],
        "deals": [{"id": "d1", "stalled": False, "amount": 10000, "is_open": True, "is_won": False}],
        "integrations": [],
    }
    result = RollupService._heuristic_fallback(payload)
    assert result["risk_flag"] == "green"


def test_get_or_create_caches_by_hash(session_factory):
    """Second call with unchanged signals returns the cached row."""
    cid = _seed_mclaren(session_factory)
    svc = RollupService(session_factory=session_factory, anthropic_client=None)
    a1 = svc.get_or_create(cid)
    a2 = svc.get_or_create(cid)
    assert a1.id == a2.id  # cache hit, not a new row
