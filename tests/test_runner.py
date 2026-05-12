"""End-to-end runner tests against the mock HubSpot client."""

from __future__ import annotations

from support_admin import runner, vip
from support_admin.hubspot_client import HubSpotClient


def test_run_batch_writes_repeat_and_vip_annotations(monkeypatch, fixture_tickets, vip_csv):
    monkeypatch.setenv("VIP_LIST_PATH", str(vip_csv))
    vip.reset_vip_cache()
    from support_admin import config as config_mod

    config_mod.reset_settings_cache()

    client = HubSpotClient(token="", fixtures=fixture_tickets)
    outcomes = runner.process_batch(client.list_tickets(), client, dry_run=False)

    by_id = {o.ticket_id: o for o in outcomes}
    # T-1 first → not a repeat
    assert by_id["T-1"].dedup.is_repeat is False
    # T-2: same error_code as T-1 → hard link
    assert by_id["T-2"].dedup.is_repeat is True
    assert by_id["T-2"].dedup.link_kind == "hard"
    assert by_id["T-2"].dedup.repeat_of == "T-1"
    # T-1 / T-2 are on C-100 (platinum VIP)
    assert by_id["T-1"].vip.is_vip is True
    assert by_id["T-2"].vip.is_vip is True
    # T-4 is not VIP and not a repeat
    assert by_id["T-4"].vip.is_vip is False
    assert by_id["T-4"].dedup.is_repeat is False
    # Writes happened on T-1 (VIP), T-2 (VIP + repeat), T-3 (VIP)
    update_targets = {w["ticket_id"] for w in client.writes if w["op"] == "update"}
    assert {"T-1", "T-2", "T-3"} <= update_targets


def test_dry_run_does_not_write(fixture_tickets, vip_csv, monkeypatch):
    monkeypatch.setenv("VIP_LIST_PATH", str(vip_csv))
    vip.reset_vip_cache()
    from support_admin import config as config_mod

    config_mod.reset_settings_cache()

    client = HubSpotClient(token="", fixtures=fixture_tickets)
    runner.process_batch(client.list_tickets(), client, dry_run=True)
    assert client.writes == []
