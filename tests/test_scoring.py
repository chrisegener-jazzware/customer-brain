"""Scorer tests with mocked HubSpot fixtures + mocked Anthropic response."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# allow running without install
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from health import scoring  # noqa: E402
from health.sources.integration_health import (  # noqa: E402
    IntegrationHealth,
    integration_health,
)

from .fixtures import (  # noqa: E402
    COMPANY_GREEN,
    COMPANY_RED,
    COMPANY_YELLOW,
    FakeAnthropicClient,
    GREEN_TICKETS,
    RED_TICKETS,
    YELLOW_TICKETS,
)


@pytest.fixture
def healthy_integ() -> IntegrationHealth:
    return IntegrationHealth(
        customer_id="100",
        integration_count=4,
        error_rate_7d=0.02,
        failing_integrations=[],
        last_successful_sync_hours_ago=1.0,
    )


@pytest.fixture
def degraded_integ() -> IntegrationHealth:
    return IntegrationHealth(
        customer_id="200",
        integration_count=5,
        error_rate_7d=0.25,
        failing_integrations=["opera-pms"],
        last_successful_sync_hours_ago=30.0,
    )


@pytest.fixture
def broken_integ() -> IntegrationHealth:
    return IntegrationHealth(
        customer_id="300",
        integration_count=6,
        error_rate_7d=0.65,
        failing_integrations=["opera-pms", "twilio-sms"],
        last_successful_sync_hours_ago=72.0,
    )


def test_summarise_tickets_counts_open_recent_and_highpri():
    s = scoring.summarise_tickets(RED_TICKETS)
    assert s["total"] == len(RED_TICKETS)
    assert s["open"] >= 17                # 12 high open + 5 urgent open
    assert s["high_priority"] >= 17       # all HIGH/URGENT
    assert s["avg_time_to_close_hours"] is not None


def test_green_customer_scores_green(healthy_integ):
    score = scoring.score_customer(
        COMPANY_GREEN, GREEN_TICKETS, healthy_integ,
        account_manager="alex@jazzware.com", use_claude=False,
    )
    assert score.flag == "green"
    assert score.score >= 75
    assert "Acme Resort" in score.narrative
    assert score.account_manager == "alex@jazzware.com"


def test_yellow_customer_scores_in_band(degraded_integ):
    score = scoring.score_customer(
        COMPANY_YELLOW, YELLOW_TICKETS, degraded_integ, use_claude=False,
    )
    assert score.flag in {"yellow", "red"}  # boundary tolerant
    assert 30 <= score.score < 80


def test_red_customer_scores_red(broken_integ):
    score = scoring.score_customer(
        COMPANY_RED, RED_TICKETS, broken_integ, use_claude=False,
    )
    assert score.flag == "red"
    assert score.score < 50


def test_claude_narrative_uses_mocked_client(monkeypatch, healthy_integ):
    fake = FakeAnthropicClient(canned="Acme Resort is GREEN with low risk. Stay the course.")

    class _FakeAnthropicModule:
        Anthropic = lambda *a, **kw: fake  # noqa: E731

    import importlib
    import sys as _sys
    _sys.modules["anthropic"] = _FakeAnthropicModule  # type: ignore[assignment]
    importlib.invalidate_caches()

    # ensure config returns a non-empty key so we take the claude branch
    from health.config import reload_settings
    reload_settings(ANTHROPIC_API_KEY="test-key")

    score = scoring.score_customer(
        COMPANY_GREEN, GREEN_TICKETS, healthy_integ, use_claude=True,
    )
    assert "Acme Resort is GREEN" in score.narrative
    assert fake.calls, "expected Claude to be invoked"
    assert fake.calls[0]["model"]
    # cleanup
    _sys.modules.pop("anthropic", None)
    reload_settings()


def test_integration_health_stub_is_deterministic():
    a = integration_health({"id": "abc"})
    b = integration_health({"id": "abc"})
    c = integration_health({"id": "xyz"})
    assert a.as_dict() == b.as_dict()
    assert a.as_dict() != c.as_dict()


def test_roll_up_weighted():
    components = {
        "ticket_volume": 100, "escalation": 100, "open_load": 100,
        "time_to_close": 100, "integration_health": 0,
    }
    score = scoring.roll_up(components)
    # integration weight is 0.25 -> max ~75
    assert 70 <= score <= 80
