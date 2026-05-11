"""Unit tests for the HubSpot feeder — mocks the HTTP client."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from account_intel.db.models import Company, DealSignal, TicketSignal
from account_intel.feeders import HubSpotFeeder


def _now_iso(days_ago: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _client_stub() -> MagicMock:
    c = MagicMock()
    c.company.return_value = {
        "id": "320895019724",
        "properties": {
            "name": "McLaren Technologies Asia Pacific",
            "domain": "mclarentechnologies.com",
            "industry": "Hospitality",
            "country": "Singapore",
            "city": "Singapore",
            "lifecyclestage": "customer",
            "createdate": _now_iso(900),
            "hubspot_owner_id": "owner-1",
            "annualrevenue": "1500000",
            "numberofemployees": "75",
        },
    }
    c.company_associations.side_effect = lambda cid, to: {
        "tickets": ["t1", "t2"],
        "deals": ["d1", "d2"],
        "contacts": [],
        "calls": [],
        "emails": [],
        "meetings": [],
        "notes": [],
    }.get(to, [])
    c.deal_associations.side_effect = lambda did, to: []
    c.contact.return_value = None
    c.engagement.return_value = None
    c.quote.return_value = None
    c.ticket.side_effect = lambda tid: {
        "t1": {
            "id": "t1",
            "properties": {
                "subject": "PMS sync failing",
                "content": "Opera ↔ Jazzware sync stopped",
                "hs_pipeline_stage": "ts_open",
                "hs_ticket_priority": "HIGH",
                "hs_ticket_category": "integration",
                "createdate": _now_iso(45),
                "closed_date": None,
                "hs_lastmodifieddate": _now_iso(10),
                "source_type": "EMAIL",
            },
        },
        "t2": {
            "id": "t2",
            "properties": {
                "subject": "Question about reporting",
                "content": "Question",
                "hs_pipeline_stage": "ts_closed",
                "hs_ticket_priority": "LOW",
                "hs_ticket_category": "question",
                "createdate": _now_iso(60),
                "closed_date": _now_iso(55),
                "hs_lastmodifieddate": _now_iso(55),
                "source_type": "EMAIL",
            },
        },
    }[tid]
    c.deal.side_effect = lambda did, with_stage_history=True: {
        "d1": {
            "id": "d1",
            "properties": {
                "dealname": "McLaren expansion 2026",
                "amount": "85000",
                "dealstage": "stage_decision",
                "createdate": _now_iso(120),
                "closedate": None,
                "hs_lastmodifieddate": _now_iso(47),  # stalled
                "hs_deal_stage_probability": "0.6",
            },
        },
        "d2": {
            "id": "d2",
            "properties": {
                "dealname": "Initial PMS deal",
                "amount": "30000",
                "dealstage": "stage_won",
                "createdate": _now_iso(800),
                "closedate": _now_iso(700),
                "hs_lastmodifieddate": _now_iso(700),
                "hs_deal_stage_probability": "1.0",
            },
        },
    }[did]
    c.deal_stage_map.return_value = {
        "stage_decision": {"label": "Decision Maker Bought-In", "pipeline": "Sales", "won": False, "closed": False, "probability": 0.6},
        "stage_won": {"label": "Closed Won", "pipeline": "Sales", "won": True, "closed": True, "probability": 1.0},
    }
    c.ticket_stage_map.return_value = {"ts_open": "In Progress", "ts_closed": "Resolved"}
    return c


def test_refresh_company_writes_signals(session_factory):
    feeder = HubSpotFeeder(client=_client_stub(), session_factory=session_factory)
    result = feeder.refresh_company("320895019724")

    assert result.tickets == 2
    assert result.deals == 2
    assert result.open_tickets == 1
    assert result.stalled_deals == 1

    with session_factory() as s:
        c = s.get(Company, "320895019724")
        assert c is not None
        assert c.name == "McLaren Technologies Asia Pacific"
        assert c.risk_score is not None and c.risk_score > 0

        tickets = s.query(TicketSignal).filter_by(company_id=c.id).all()
        assert len(tickets) == 2
        open_t = [t for t in tickets if t.is_open]
        assert len(open_t) == 1
        assert open_t[0].priority == "HIGH"

        deals = s.query(DealSignal).filter_by(company_id=c.id).all()
        stalled = [d for d in deals if d.stalled]
        won = [d for d in deals if d.is_won]
        assert len(stalled) == 1
        assert len(won) == 1


def test_refresh_is_idempotent(session_factory):
    """Two calls produce the same rows, not duplicates."""
    feeder = HubSpotFeeder(client=_client_stub(), session_factory=session_factory)
    feeder.refresh_company("320895019724")
    feeder.refresh_company("320895019724")
    with session_factory() as s:
        assert s.query(TicketSignal).count() == 2
        assert s.query(DealSignal).count() == 2
        assert s.query(Company).count() == 1
