"""VIP CSV loading + apply tests."""

from __future__ import annotations

from pathlib import Path

from support_admin import vip
from support_admin.hubspot_client import HSTicket, HubSpotClient


def test_missing_csv_silently_skips():
    vip.reset_vip_cache()
    table = vip.load_vip_list(Path("/tmp/does/not/exist.csv"))
    assert table == {}


def test_loads_csv_and_flags_vip(vip_csv, fixture_tickets):
    vip.reset_vip_cache()
    table = vip.load_vip_list(vip_csv)
    assert "C-100" in table
    assert table["C-100"].tier == "platinum"

    client = HubSpotClient(token="", fixtures=fixture_tickets)
    vip_ticket = client.list_tickets()[0]  # C-100
    result = vip.apply(vip_ticket, client, vip_list=table)

    assert result.is_vip is True
    assert result.priority == "HIGH"
    # Update + note written
    ops = [w["op"] for w in client.writes]
    assert "update" in ops
    assert "note" in ops
    update = next(w for w in client.writes if w["op"] == "update")
    assert update["properties"]["jw_vip_flag"] == "true"
    assert update["properties"]["hs_ticket_priority"] == "HIGH"


def test_non_vip_company_makes_no_writes(vip_csv, fixture_tickets):
    vip.reset_vip_cache()
    table = vip.load_vip_list(vip_csv)
    client = HubSpotClient(token="", fixtures=fixture_tickets)
    # T-4 is on C-300 — not in CSV
    non_vip = next(t for t in client.list_tickets() if t.id == "T-4")
    result = vip.apply(non_vip, client, vip_list=table)
    assert result.is_vip is False
    assert client.writes == []
