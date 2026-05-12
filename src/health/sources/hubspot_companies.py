"""HubSpot companies source: list companies with ticket activity in window.

Uses the search API to find companies with associated tickets created in the
last N days. Paginated. Real API calls (no mocking).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from hubspot import HubSpot
from hubspot.crm.tickets import (
    ApiException,
    PublicObjectSearchRequest,
    Filter,
    FilterGroup,
)

from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass
class Company:
    id: str
    name: str
    domain: str | None = None
    owner_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            "owner_id": self.owner_id,
        }


COMPANY_PROPS = ["name", "domain", "hubspot_owner_id"]


class HubSpotCompanySource:
    """Discover companies with ticket activity in the recent window."""

    def __init__(
        self,
        client: HubSpot | None = None,
        lookback_days: int = 90,
        page_size: int = 100,
    ) -> None:
        s = get_settings()
        self.client = client or HubSpot(access_token=s.hubspot_token)
        self.lookback_days = lookback_days
        self.page_size = page_size

    def list_active_companies(self) -> list[Company]:
        """Return all companies that have at least one ticket created in window.

        Strategy: search tickets created in window, collect distinct associated
        company ids, then hydrate company records.
        """
        cutoff_ms = int(
            (
                datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
            ).timestamp()
            * 1000
        )
        company_ids: set[str] = set()

        for ticket in self._iter_recent_tickets(cutoff_ms):
            for cid in _ticket_company_ids(ticket):
                company_ids.add(cid)

        log.info(
            "Found %d distinct companies with tickets in last %d days",
            len(company_ids),
            self.lookback_days,
        )
        return self._hydrate_companies(sorted(company_ids))

    # ---- internals ----
    def _iter_recent_tickets(self, cutoff_ms: int) -> Iterator[Any]:
        """Search recent tickets, then resolve each to its associated companies."""
        after: str | None = None
        while True:
            req = PublicObjectSearchRequest(
                filter_groups=[
                    FilterGroup(
                        filters=[
                            Filter(
                                property_name="createdate",
                                operator="GTE",
                                value=str(cutoff_ms),
                            )
                        ]
                    )
                ],
                properties=["createdate"],
                limit=self.page_size,
                after=after,
            )
            try:
                page = self.client.crm.tickets.search_api.do_search(
                    public_object_search_request=req
                )
            except ApiException as e:
                log.error("HubSpot ticket search failed: %s", e)
                return
            for t in page.results or []:
                # Hydrate associations per ticket (search API doesn't return them)
                try:
                    assoc = self.client.crm.associations.v4.basic_api.get_page(
                        object_type="tickets",
                        object_id=t.id,
                        to_object_type="companies",
                        limit=500,
                    )
                    company_ids = [
                        str(r.to_object_id) for r in (assoc.results or [])
                    ]
                except Exception as e:  # noqa: BLE001
                    log.debug("ticket->company assoc failed %s: %s", t.id, e)
                    company_ids = []
                # Attach as a simple dict so _ticket_company_ids can read it
                setattr(
                    t,
                    "associations",
                    {
                        "companies": {
                            "results": [{"id": cid} for cid in company_ids]
                        }
                    },
                )
                yield t
            paging = getattr(page, "paging", None)
            nxt = getattr(getattr(paging, "next", None), "after", None) if paging else None
            if not nxt:
                return
            after = nxt

    def _hydrate_companies(self, company_ids: list[str]) -> list[Company]:
        out: list[Company] = []
        for cid in company_ids:
            try:
                c = self.client.crm.companies.basic_api.get_by_id(
                    company_id=cid, properties=COMPANY_PROPS
                )
            except Exception as e:  # noqa: BLE001
                log.debug("company hydrate failed %s: %s", cid, e)
                continue
            p = c.properties or {}
            out.append(
                Company(
                    id=cid,
                    name=p.get("name") or f"Company {cid}",
                    domain=p.get("domain"),
                    owner_id=p.get("hubspot_owner_id"),
                )
            )
        return out


def _ticket_company_ids(ticket: Any) -> list[str]:
    """Extract associated company ids from a ticket search result."""
    assoc = getattr(ticket, "associations", None) or {}
    # SDK returns either a dict or an object with .companies
    companies = None
    if isinstance(assoc, dict):
        companies = assoc.get("companies")
    else:
        companies = getattr(assoc, "companies", None)
    if not companies:
        return []
    results = getattr(companies, "results", None) or (
        companies.get("results") if isinstance(companies, dict) else None
    )
    if not results:
        return []
    return [getattr(r, "id", None) or r.get("id") for r in results if r]
