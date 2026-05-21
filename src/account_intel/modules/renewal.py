"""JAZ-114 Renewal Radar module.

Surfaces 90/60/30 day renewal alerts based on contract close dates on
the account's deals. Looks for the next deal with a `close_date` in the
future tagged with renewal-like names, plus combines current support
sentiment for a renewal_risk score.

When no renewal deal is found, the module reports na/low and surfaces
'no upcoming renewal in pipeline'.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DealSignal, TicketSignal


_RENEWAL_HINTS = ("renew", "renewal", "extension", "term renewal")


def compute(s: Session, company_id: str):
    from . import ModuleResult

    deals = s.scalars(select(DealSignal).where(DealSignal.company_id == company_id)).all()
    tickets = s.scalars(select(TicketSignal).where(TicketSignal.company_id == company_id)).all()

    now = datetime.now(timezone.utc)
    candidates = []
    for d in deals:
        # Use hs_closed_at as the close date proxy (HubSpot deal close).
        cd_raw = d.hs_closed_at
        if cd_raw is None:
            continue
        cd = cd_raw.replace(tzinfo=timezone.utc) if cd_raw.tzinfo is None else cd_raw
        if cd <= now:
            continue
        name = (d.name or "").lower()
        if any(h in name for h in _RENEWAL_HINTS):
            candidates.append((cd, d))

    candidates.sort(key=lambda x: x[0])
    if not candidates:
        return ModuleResult(
            module_id="renewal_radar",
            label="Renewal Radar",
            score=None,
            severity="na",
            headline="No upcoming renewal in pipeline",
            drivers=[{"name": "no_renewal_deal",
                      "detail": "Deal names contain none of: " + ", ".join(_RENEWAL_HINTS)}],
            metrics={"deals_total": len(deals)},
        )

    next_close, next_deal = candidates[0]
    days_to = (next_close - now).days

    if days_to <= 30:
        window, base = "30-day", 70
    elif days_to <= 60:
        window, base = "60-day", 50
    elif days_to <= 90:
        window, base = "90-day", 30
    else:
        window, base = ">90-day", 10

    # Boost if support is hot
    open_t = [t for t in tickets if t.is_open]
    aged = [t for t in open_t if (t.age_days or 0) > 30]
    bump = min(15, 3 * len(aged))
    score = min(base + bump, 100)

    sev = "high" if score >= 65 else ("medium" if score >= 35 else "low")
    head = (
        f"🛎️ Renewal in {days_to}d ({window} window) · "
        f"{next_deal.name or next_deal.id}"
    )

    drivers = [
        {"name": "renewal_deal",
         "deal_id": next_deal.id,
         "deal_name": next_deal.name,
         "amount": next_deal.amount,
         "close_date": next_close.isoformat(),
         "days_to_close": days_to},
    ]
    if aged:
        drivers.append({"name": "aged_open_tickets_at_renewal", "count": len(aged)})

    return ModuleResult(
        module_id="renewal_radar",
        label="Renewal Radar",
        score=score,
        severity=sev,
        headline=head,
        drivers=drivers,
        metrics={
            "days_to_renewal": days_to,
            "window": window,
            "renewal_amount": next_deal.amount,
            "renewal_deals_total": len(candidates),
            "support_hot_at_renewal": len(aged),
        },
    )
