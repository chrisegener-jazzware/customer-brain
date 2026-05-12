"""Tests for JAZ-68 dedup + repeat-issue report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from support_admin.ticket_report import (
    Cluster,
    TicketRecord,
    find_exact_duplicates,
    find_repeat_customers,
    normalize_subject,
    render_report,
    _extract_json,
)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _mk(
    tid: str,
    subject: str,
    *,
    company_id: str = "C1",
    company_name: str = "Acme Hotel",
    category: str | None = None,
    sub_category: str | None = None,
    days_ago: int = 0,
) -> TicketRecord:
    return TicketRecord(
        id=tid,
        subject=subject,
        content="",
        created_at=_iso(days_ago),
        company_id=company_id,
        company_name=company_name,
        category=category,
        sub_category=sub_category,
        priority=None,
        casenumber=None,
        pipeline_stage=None,
    )


# ---- subject normalization ------------------------------------------------


def test_normalize_strips_reply_prefixes():
    assert normalize_subject("RE: RE: FW: Issue") == "issue"
    assert normalize_subject("Re: Phone not ringing") == "phone not ringing"
    assert normalize_subject("ACTION REQUIRED FROM YOU: RE: Outage") == "outage"


def test_normalize_strips_sf_refs_and_numbers():
    s = "Voice Services [ ref:!00D3i0uc3E.!500UV0n... - 3373340"
    out = normalize_subject(s)
    assert "ref:" not in out
    assert "3373340" not in out
    assert "voice services" in out


def test_normalize_strips_case_numbers():
    assert normalize_subject("Case #12345 Phone issue") == "phone issue"
    assert normalize_subject("Ticket 99999 - outage") == "outage"


# ---- exact-match clusters -------------------------------------------------


def test_exact_dup_basic_grouping():
    tickets = [
        _mk("1", "Phone not ringing", company_id="A", days_ago=1),
        _mk("2", "RE: Phone not ringing", company_id="A", days_ago=2),
        _mk("3", "Phone not ringing", company_id="B", days_ago=1),  # diff company
        _mk("4", "Different subject entirely", company_id="A", days_ago=1),
    ]
    clusters = find_exact_duplicates(tickets, window_days=7)
    assert len(clusters) == 1
    assert clusters[0].size == 2
    assert set(clusters[0].ticket_ids) == {"1", "2"}
    assert clusters[0].kind == "exact"


def test_exact_dup_respects_window():
    tickets = [
        _mk("1", "Phone not ringing", company_id="A", days_ago=1),
        _mk("2", "Phone not ringing", company_id="A", days_ago=20),  # > 7d
    ]
    clusters = find_exact_duplicates(tickets, window_days=7)
    assert clusters == []


def test_exact_dup_requires_company():
    tickets = [
        _mk("1", "x", company_id="", company_name=None, days_ago=1),
        _mk("2", "x", company_id="", company_name=None, days_ago=2),
    ]
    assert find_exact_duplicates(tickets) == []


# ---- repeat-issue customers -----------------------------------------------


def test_repeat_customers_uses_category():
    tickets = [
        _mk("1", "a", category="PMS", sub_category="Booking", days_ago=2),
        _mk("2", "b", category="PMS", sub_category="Booking", days_ago=10),
        _mk("3", "c", category="PMS", sub_category="Booking", days_ago=20),
        _mk("4", "z", category="PBX", days_ago=2),  # different theme
    ]
    repeats = find_repeat_customers(tickets, window_days=30, min_count=3)
    assert len(repeats) == 1
    assert repeats[0].theme == "PMS / Booking"
    assert repeats[0].count == 3


def test_repeat_customers_strict_gt_2():
    tickets = [
        _mk("1", "a", category="X", days_ago=1),
        _mk("2", "b", category="X", days_ago=2),  # only 2 → not a repeat
    ]
    assert find_repeat_customers(tickets, min_count=3) == []


def test_repeat_customers_window_enforced():
    tickets = [
        _mk("1", "a", category="X", days_ago=1),
        _mk("2", "b", category="X", days_ago=2),
        _mk("3", "c", category="X", days_ago=80),  # outside 30d
    ]
    assert find_repeat_customers(tickets, window_days=30, min_count=3) == []


def test_repeat_customers_falls_back_to_semantic_theme():
    tickets = [
        _mk("1", "Phone broken", days_ago=1),
        _mk("2", "Phone broken", days_ago=2),
        _mk("3", "Phone broken", days_ago=3),
    ]
    sem = [
        Cluster(
            cluster_id="semantic:1",
            kind="semantic",
            label="phone outage",
            ticket_ids=["1", "2", "3"],
        )
    ]
    repeats = find_repeat_customers(tickets, semantic_clusters=sem, min_count=3)
    assert len(repeats) == 1
    assert repeats[0].theme == "phone outage"


# ---- json extractor -------------------------------------------------------


def test_extract_json_handles_fenced_response():
    text = '```json\n{"clusters": [{"theme": "x", "confidence": 0.9, "ticket_ids": ["1", "2"]}]}\n```'
    out = _extract_json(text)
    assert out is not None
    assert out["clusters"][0]["theme"] == "x"


def test_extract_json_handles_prose_wrapper():
    text = 'Here you go:\n{"clusters": []}\nThat\'s it.'
    assert _extract_json(text) == {"clusters": []}


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("no json here") is None
    assert _extract_json("") is None


# ---- rendering ------------------------------------------------------------


def test_render_report_includes_headings():
    out = render_report(
        tickets=[_mk("1", "x")],
        exact_clusters=[],
        semantic_clusters=[],
        repeat_customers=[],
    )
    assert "Tickets analyzed: 1" in out
    assert "EXACT-MATCH DUPLICATE CLUSTERS" in out
    assert "SEMANTIC (CLAUDE) CLUSTERS" in out
    assert "REPEAT-ISSUE CUSTOMERS" in out
