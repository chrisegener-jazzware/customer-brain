"""HubSpot deals source: MRR/ARR, renewal proximity, expansion signals."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hubspot import HubSpot
from hubspot.crm.deals import ApiException

from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass
class DealSignals:
    customer_id: str
    active_deals: int = 0
    total_arr: float = 0.0
    total_mrr: float = 0.0
    next_renewal_at: datetime | None = None
    days_to_renewal: int | None = None
    deals: list[dict[str, Any]] = field(default_factory=list)

    @property
    def renewal_proximity_band(self) -> str:
        if self.days_to_renewal is None:
            return "unknown"
        if self.days_to_renewal < 30:
            return "imminent"
        if self.days_to_renewal < 90:
            return "near"
        if self.days_to_renewal < 180:
            return "mid"
        return "far"

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_deals": self.active_deals,
            "total_arr": round(self.total_arr, 2),
            "total_mrr": round(self.total_mrr, 2),
            "next_renewal_at": self.next_renewal_at.isoformat()
            if self.next_renewal_at
            else None,
            "days_to_renewal": self.days_to_renewal,
            "renewal_proximity_band": self.renewal_proximity_band,
            "deals": self.deals,
        }


DEAL_PROPS = [
    "dealname",
    "dealstage",
    "amount",
    "closedate",
    "renewal_date",
    "hs_arr",
    "hs_mrr",
    "pipeline",
]

# Stages that count as "won and active" subscription
ACTIVE_STAGES = {"closedwon", "renewal", "expansion"}


class HubSpotDealSource:
    def __init__(self, client: HubSpot | None = None) -> None:
        s = get_settings()
        self.client = client or HubSpot(access_token=s.hubspot_token)

    def fetch(self, customer_id: str) -> DealSignals:
        signals = DealSignals(customer_id=customer_id)
        try:
            assoc = self.client.crm.companies.associations_api.get_all(
                company_id=customer_id, to_object_type="deals"
            )
            deal_ids = [r.id for r in (assoc.results or [])]
        except ApiException as e:
            log.warning("HubSpot deal assoc lookup failed for %s: %s", customer_id, e)
            return signals

        now = datetime.now(timezone.utc)
        for did in deal_ids:
            try:
                d = self.client.crm.deals.basic_api.get_by_id(
                    deal_id=did, properties=DEAL_PROPS
                )
            except ApiException:
                continue
            props = d.properties or {}
            stage = (props.get("dealstage") or "").lower()
            if stage not in ACTIVE_STAGES:
                continue

            arr = _to_float(props.get("hs_arr"))
            mrr = _to_float(props.get("hs_mrr"))
            renewal_raw = props.get("renewal_date") or props.get("closedate")
            renewal = _parse_dt(renewal_raw) if renewal_raw else None

            signals.active_deals += 1
            signals.total_arr += arr
            signals.total_mrr += mrr
            signals.deals.append(
                {
                    "id": did,
                    "name": props.get("dealname"),
                    "stage": stage,
                    "arr": arr,
                    "mrr": mrr,
                    "renewal_at": renewal.isoformat() if renewal else None,
                }
            )
            if renewal and (
                signals.next_renewal_at is None or renewal < signals.next_renewal_at
            ):
                signals.next_renewal_at = renewal

        if signals.next_renewal_at:
            signals.days_to_renewal = max(
                0, (signals.next_renewal_at - now).days
            )
        return signals


def _to_float(v: Any) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(raw: str) -> datetime:
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1000.0, tz=timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
