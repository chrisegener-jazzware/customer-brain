"""Repeat-issue / dedup detection tests (JAZ-67 rules)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from support_admin.dedup import detect_repeat
from support_admin.hubspot_client import HSTicket


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _ticket(**kwargs) -> HSTicket:
    base = dict(
        id=kwargs.pop("id"),
        subject=kwargs.pop("subject", ""),
        content=kwargs.pop("content", ""),
        error_code=kwargs.pop("error_code", None),
        company_id=kwargs.pop("company_id", None),
        created_at=kwargs.pop("created_at", _iso(0)),
        properties=kwargs.pop("properties", {}),
    )
    return HSTicket(**base)


def test_first_ticket_is_not_a_repeat():
    t = _ticket(id="A1", subject="Wake-up calls not syncing", company_id="C-50")
    result = detect_repeat(t)
    assert result.is_repeat is False
    assert result.repeat_of is None


def test_hard_link_on_error_code_exact_match():
    first = _ticket(
        id="B1",
        subject="Different surface message",
        error_code="PMS-LINK-503",
        company_id="C-100",
        created_at=_iso(3),
    )
    detect_repeat(first)
    second = _ticket(
        id="B2",
        subject="Totally unrelated wording for the second ticket",
        error_code="PMS-LINK-503",
        company_id="C-999",  # different company; hard link still wins
        created_at=_iso(0),
    )
    result = detect_repeat(second)
    assert result.is_repeat is True
    assert result.link_kind == "hard"
    assert result.repeat_of == "B1"
    assert result.score == 1.0


def test_soft_link_same_company_within_window():
    first = _ticket(
        id="S1",
        subject="PMS keeps disconnecting from PBX",
        company_id="C-200",
        created_at=_iso(5),
    )
    detect_repeat(first)
    second = _ticket(
        id="S2",
        subject="PMS keeps disconnecting from PBX",  # near-identical → high cosine
        company_id="C-200",
        created_at=_iso(0),
    )
    result = detect_repeat(second)
    assert result.is_repeat is True
    assert result.link_kind == "soft"
    assert result.repeat_of == "S1"
    assert result.score >= 0.85


def test_no_link_when_outside_window():
    first = _ticket(
        id="W1",
        subject="PMS keeps disconnecting from PBX",
        company_id="C-300",
        created_at=_iso(120),  # well outside 30d window
    )
    detect_repeat(first)
    second = _ticket(
        id="W2",
        subject="PMS keeps disconnecting from PBX",
        company_id="C-300",
        created_at=_iso(0),
    )
    result = detect_repeat(second)
    assert result.is_repeat is False
    assert "no recent tickets" in result.reason


def test_no_link_when_different_company():
    first = _ticket(
        id="X1",
        subject="PMS keeps disconnecting from PBX",
        company_id="C-400",
        created_at=_iso(2),
    )
    detect_repeat(first)
    second = _ticket(
        id="X2",
        subject="PMS keeps disconnecting from PBX",
        company_id="C-401",  # different company, no error_code → no link
        created_at=_iso(0),
    )
    result = detect_repeat(second)
    assert result.is_repeat is False


def test_unrelated_subjects_below_threshold():
    first = _ticket(
        id="U1",
        subject="PMS keeps disconnecting from PBX",
        company_id="C-500",
        created_at=_iso(2),
    )
    detect_repeat(first)
    second = _ticket(
        id="U2",
        subject="Minibar charges missing on folio",
        company_id="C-500",
        created_at=_iso(0),
    )
    result = detect_repeat(second)
    assert result.is_repeat is False
