"""HubSpot tickets source: last-N-day support signals per company."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hubspot import HubSpot
from hubspot.crm.tickets import ApiException

from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass
class TicketSignals:
    customer_id: str
    window_days: int
    total: int = 0
    open_count: int = 0
    closed_count: int = 0
    escalated_count: int = 0
    avg_time_to_close_hours: float | None = None
    top_categories: list[tuple[str, int]] = field(default_factory=list)
    last_ticket_at: datetime | None = None

    @property
    def escalation_rate(self) -> float:
        return (self.escalated_count / self.total) if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "open": self.open_count,
            "closed": self.closed_count,
            "escalated": self.escalated_count,
            "escalation_rate": round(self.escalation_rate, 3),
            "avg_time_to_close_hours": self.avg_time_to_close_hours,
            "top_categories": self.top_categories,
            "last_ticket_at": self.last_ticket_at.isoformat()
            if self.last_ticket_at
            else None,
            "window_days": self.window_days,
        }


# HubSpot ticket properties we care about
TICKET_PROPS = [
    "subject",
    "hs_pipeline_stage",
    "hs_ticket_priority",
    "hs_ticket_category",
    "createdate",
    "closed_date",
    "hubspot_owner_id",
]

# Stage IDs that indicate "open" - in production, fetch from HubSpot pipeline API
OPEN_STAGES = {"1", "2", "3"}  # New, Waiting on contact, Waiting on us
ESCALATED_PRIORITIES = {"HIGH", "URGENT"}


class HubSpotTicketSource:
    def __init__(self, client: HubSpot | None = None) -> None:
        s = get_settings()
        self.client = client or HubSpot(access_token=s.hubspot_token)
        self.lookback_days = s.ticket_lookback_days

    def fetch(self, customer_id: str) -> TicketSignals:
        """Fetch ticket signals for a single company id."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        signals = TicketSignals(customer_id=customer_id, window_days=self.lookback_days)

        try:
            # Get tickets associated with this company (v4 associations API)
            assoc = self.client.crm.associations.v4.basic_api.get_page(
                object_type="companies",
                object_id=customer_id,
                to_object_type="tickets",
                limit=500,
            )
            ticket_ids = [str(r.to_object_id) for r in (assoc.results or [])]
        except ApiException as e:
            log.warning("HubSpot association lookup failed for %s: %s", customer_id, e)
            return signals
        except Exception as e:  # noqa: BLE001
            log.warning("HubSpot association lookup error for %s: %s", customer_id, e)
            return signals

        if not ticket_ids:
            return signals

        categories: Counter[str] = Counter()
        close_durations_h: list[float] = []

        for tid in ticket_ids:
            try:
                t = self.client.crm.tickets.basic_api.get_by_id(
                    ticket_id=tid, properties=TICKET_PROPS
                )
            except ApiException as e:
                log.debug("ticket fetch failed %s: %s", tid, e)
                continue

            props = t.properties or {}
            created_raw = props.get("createdate")
            if not created_raw:
                continue
            created = _parse_dt(created_raw)
            if created < cutoff:
                continue

            signals.total += 1
            if signals.last_ticket_at is None or created > signals.last_ticket_at:
                signals.last_ticket_at = created

            stage = props.get("hs_pipeline_stage", "")
            if stage in OPEN_STAGES:
                signals.open_count += 1
            else:
                signals.closed_count += 1
                closed_raw = props.get("closed_date")
                if closed_raw:
                    closed = _parse_dt(closed_raw)
                    close_durations_h.append((closed - created).total_seconds() / 3600.0)

            priority = (props.get("hs_ticket_priority") or "").upper()
            if priority in ESCALATED_PRIORITIES:
                signals.escalated_count += 1

            cat = props.get("hs_ticket_category") or "uncategorized"
            categories[cat] += 1

        if close_durations_h:
            signals.avg_time_to_close_hours = round(
                sum(close_durations_h) / len(close_durations_h), 2
            )
        signals.top_categories = categories.most_common(5)
        return signals


def _parse_dt(raw: str) -> datetime:
    # HubSpot returns ISO8601 with Z or offset; epoch ms also possible
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1000.0, tz=timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
