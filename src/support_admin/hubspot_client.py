"""HubSpot CRM v3 tickets API wrapper.

Reads `HUBSPOT_PRIVATE_APP_TOKEN`. When the token is unset the client runs in
mock mode and returns fixture tickets — useful for local dev and tests.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.hubapi.com"

# Custom property names we write back to HubSpot tickets.
PROP_REPEAT_OF = "jw_repeat_of"
PROP_DEDUP_SCORE = "jw_dedup_score"
PROP_VIP_FLAG = "jw_vip_flag"
PROP_LINK_KIND = "jw_link_kind"

# Properties we read.
TICKET_PROPERTIES = [
    "subject",
    "content",
    "hs_pipeline_stage",
    "createdate",
    "hs_lastmodifieddate",
    "hubspot_company_id",
    "error_code",
    PROP_REPEAT_OF,
    PROP_DEDUP_SCORE,
    PROP_VIP_FLAG,
    PROP_LINK_KIND,
]


@dataclass
class HSTicket:
    """Lightweight, normalized ticket projection used across the package."""

    id: str
    subject: str = ""
    content: str = ""
    error_code: str | None = None
    company_id: str | None = None
    created_at: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, obj: dict[str, Any]) -> "HSTicket":
        props = obj.get("properties") or {}
        return cls(
            id=str(obj.get("id", "")),
            subject=props.get("subject") or "",
            content=props.get("content") or "",
            error_code=props.get("error_code"),
            company_id=props.get("hubspot_company_id"),
            created_at=props.get("createdate"),
            properties=dict(props),
        )


# ---- fixture / mock mode ------------------------------------------------


def _default_fixture_path() -> Path:
    here = Path(__file__).resolve().parent
    return here / "fixtures" / "tickets.json"


def _build_default_fixtures() -> list[dict[str, Any]]:
    """Used when no fixture file is on disk — sane defaults for tests/dev."""
    now = datetime.now(timezone.utc)

    def iso(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).isoformat()

    return [
        {
            "id": "1001",
            "properties": {
                "subject": "PMS keeps disconnecting from PBX",
                "content": "The Tiger PMS link drops every few hours, calls fail.",
                "hubspot_company_id": "C-100",
                "createdate": iso(2),
                "error_code": "PMS-LINK-503",
            },
        },
        {
            "id": "1002",
            "properties": {
                "subject": "PMS disconnecting from PBX again",
                "content": "Same disconnect issue, link keeps dropping.",
                "hubspot_company_id": "C-100",
                "createdate": iso(1),
                "error_code": "PMS-LINK-503",
            },
        },
        {
            "id": "1003",
            "properties": {
                "subject": "Wake-up call schedule not syncing",
                "content": "Wake-up calls aren't pushed to rooms.",
                "hubspot_company_id": "C-200",
                "createdate": iso(1),
                "error_code": "WAKEUP-SYNC",
            },
        },
        {
            "id": "1004",
            "properties": {
                "subject": "Brand new minibar billing question",
                "content": "Charges aren't appearing on guest folio for minibar.",
                "hubspot_company_id": "C-300",
                "createdate": iso(0),
            },
        },
        {
            "id": "1005",
            "properties": {
                "subject": "PMS keeps disconnecting from PBX",
                "content": "Old ticket, outside the 30 day window.",
                "hubspot_company_id": "C-100",
                "createdate": iso(120),
                "error_code": "PMS-LINK-503",
            },
        },
    ]


def load_fixtures(path: Path | None = None) -> list[dict[str, Any]]:
    candidate = path or _default_fixture_path()
    if candidate.exists():
        try:
            return json.loads(candidate.read_text())
        except Exception:  # pragma: no cover
            log.exception("Failed to read fixture file %s; falling back to defaults", candidate)
    return _build_default_fixtures()


# ---- client -------------------------------------------------------------


class HubSpotClient:
    """Thin wrapper over the HubSpot CRM v3 tickets endpoints.

    When ``token`` is empty/None, the client runs in **mock mode** — reads come
    from fixtures, writes are recorded in-memory and never hit the network.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        fixtures: list[dict[str, Any]] | None = None,
        fixture_path: Path | None = None,
        force_mock: bool | None = None,
    ) -> None:
        self.token = (token if token is not None else os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")) or ""
        # Force mock when DRY_RUN is set even if a token is present — lets us
        # exercise the full pipeline without hitting HubSpot (also useful when
        # the token is missing scopes).
        if force_mock is None:
            force_mock = os.environ.get("SUPPORT_ADMIN_DRY_RUN", "").lower() in ("1", "true", "yes")
        self.mock_mode = (not self.token) or force_mock
        self._owned_client: httpx.Client | None = None
        if self.mock_mode:
            self._client = None
            self._fixtures = list(fixtures) if fixtures is not None else load_fixtures(fixture_path)
            self.writes: list[dict[str, Any]] = []  # spy for tests
            if self.token and force_mock:
                log.info("HubSpot client forced into mock mode (DRY_RUN=true)")
        else:
            if client is not None:
                self._client = client
            else:
                self._owned_client = httpx.Client(
                    base_url=API_BASE,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                    timeout=30.0,
                )
                self._client = self._owned_client
            self._fixtures = []
            self.writes = []

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def __enter__(self) -> "HubSpotClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- reads ----------------------------------------------------------

    def list_tickets(
        self,
        *,
        limit: int = 100,
        after: str | None = None,
        properties: Iterable[str] = TICKET_PROPERTIES,
    ) -> list[HSTicket]:
        if self.mock_mode:
            return [HSTicket.from_api(t) for t in self._fixtures]
        params: dict[str, Any] = {"limit": limit, "properties": ",".join(properties)}
        if after:
            params["after"] = after
        resp = self._client.get("/crm/v3/objects/tickets", params=params)
        resp.raise_for_status()
        body = resp.json()
        return [HSTicket.from_api(o) for o in body.get("results", [])]

    def get_ticket(self, ticket_id: str, *, properties: Iterable[str] = TICKET_PROPERTIES) -> HSTicket | None:
        if self.mock_mode:
            for fix in self._fixtures:
                if str(fix.get("id")) == str(ticket_id):
                    return HSTicket.from_api(fix)
            return None
        resp = self._client.get(
            f"/crm/v3/objects/tickets/{ticket_id}",
            params={"properties": ",".join(properties)},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return HSTicket.from_api(resp.json())

    def search_tickets_modified_since(self, since: datetime, *, limit: int = 100) -> list[HSTicket]:
        """Search for tickets modified after the given UTC timestamp."""
        if self.mock_mode:
            return [HSTicket.from_api(t) for t in self._fixtures]
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "hs_lastmodifieddate",
                            "operator": "GTE",
                            "value": int(since.timestamp() * 1000),
                        }
                    ]
                }
            ],
            "properties": list(TICKET_PROPERTIES),
            "limit": limit,
            "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
        }
        resp = self._client.post("/crm/v3/objects/tickets/search", json=body)
        resp.raise_for_status()
        return [HSTicket.from_api(o) for o in resp.json().get("results", [])]

    # ---- writes ---------------------------------------------------------

    def update_ticket_properties(self, ticket_id: str, properties: dict[str, Any]) -> None:
        # Coerce bool -> str for HubSpot single-line text properties
        coerced = {k: _coerce(v) for k, v in properties.items()}
        if self.mock_mode:
            self.writes.append({"op": "update", "ticket_id": str(ticket_id), "properties": coerced})
            log.debug("[mock] update %s -> %s", ticket_id, coerced)
            # Update local fixture so subsequent reads reflect the write
            for fix in self._fixtures:
                if str(fix.get("id")) == str(ticket_id):
                    fix.setdefault("properties", {}).update(coerced)
            return
        resp = self._client.patch(
            f"/crm/v3/objects/tickets/{ticket_id}",
            json={"properties": coerced},
        )
        resp.raise_for_status()

    def add_note(self, ticket_id: str, body: str) -> str | None:
        if self.mock_mode:
            note_id = f"note-{len(self.writes) + 1}"
            self.writes.append({"op": "note", "ticket_id": str(ticket_id), "body": body, "note_id": note_id})
            return note_id
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        payload = {
            "properties": {"hs_note_body": body, "hs_timestamp": ts_ms},
            "associations": [
                {
                    "to": {"id": str(ticket_id)},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 228,  # note -> ticket
                        }
                    ],
                }
            ],
        }
        resp = self._client.post("/crm/v3/objects/notes", json=payload)
        resp.raise_for_status()
        return resp.json().get("id")


def _coerce(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value
