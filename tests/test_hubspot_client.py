"""HubSpot client mock-mode tests."""

from __future__ import annotations

from support_admin.hubspot_client import HSTicket, HubSpotClient, PROP_VIP_FLAG


def test_mock_mode_returns_fixtures(fixture_tickets):
    client = HubSpotClient(token="", fixtures=fixture_tickets)
    assert client.mock_mode is True
    tickets = client.list_tickets()
    assert len(tickets) == len(fixture_tickets)
    assert all(isinstance(t, HSTicket) for t in tickets)
    assert tickets[0].subject == "PMS keeps disconnecting from PBX"
    assert tickets[0].company_id == "C-100"
    assert tickets[0].error_code == "PMS-LINK-503"


def test_mock_get_ticket_by_id(fixture_tickets):
    client = HubSpotClient(token="", fixtures=fixture_tickets)
    found = client.get_ticket("T-3")
    assert found is not None
    assert found.subject.startswith("Wake-up")
    assert client.get_ticket("does-not-exist") is None


def test_mock_writes_are_recorded(fixture_tickets):
    client = HubSpotClient(token="", fixtures=fixture_tickets)
    client.update_ticket_properties("T-1", {PROP_VIP_FLAG: True, "hs_ticket_priority": "HIGH"})
    note_id = client.add_note("T-1", "Hello note")
    assert note_id is not None
    ops = [w["op"] for w in client.writes]
    assert ops == ["update", "note"]
    # Bool coerced to "true"
    update_props = client.writes[0]["properties"]
    assert update_props[PROP_VIP_FLAG] == "true"
    # Mock fixture is mutated so subsequent reads reflect the write
    refreshed = client.get_ticket("T-1")
    assert refreshed.properties[PROP_VIP_FLAG] == "true"
