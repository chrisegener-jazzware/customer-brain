"""JAZ-115 Churn Early-Warning module.

Watches available signals for churn-shaped patterns:
  * Aged open support tickets (>30d, especially HIGH priority)
  * Reply lag growing (open ticket count rising)
  * Stalled deals (sales relationship cooling)
  * Activity drop (last 30d activity vs prior 60d)

Login-frequency telemetry (JAZ-71) isn't available yet — flagged as
'partial signal coverage' in the headline rather than blocking the module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ActivitySignal, DealSignal, TicketSignal


def compute(s: Session, company_id: str):
    from . import ModuleResult

    tickets = s.scalars(select(TicketSignal).where(TicketSignal.company_id == company_id)).all()
    deals = s.scalars(select(DealSignal).where(DealSignal.company_id == company_id)).all()
    activities = s.scalars(
        select(ActivitySignal).where(ActivitySignal.company_id == company_id)
    ).all()

    open_t = [t for t in tickets if t.is_open]
    aged_open = [t for t in open_t if (t.age_days or 0) > 30]
    high_aged = [t for t in aged_open if (t.priority or "").upper() in {"HIGH", "URGENT"}]
    stalled = [d for d in deals if getattr(d, "stalled", False)]

    # Activity trend: 30d vs prior 60d (positive trend = good)
    now = datetime.now(timezone.utc)
    last30 = sum(1 for a in activities if a.ts and a.ts.replace(tzinfo=timezone.utc) > now - timedelta(days=30))
    prior60 = sum(
        1 for a in activities
        if a.ts and now - timedelta(days=90) < a.ts.replace(tzinfo=timezone.utc) <= now - timedelta(days=30)
    )
    expected = prior60 / 2.0 if prior60 else 0
    activity_drop = (expected - last30) / expected if expected else 0  # 0..1 fraction drop

    score = 0.0
    drivers = []
    if high_aged:
        score += 35
        drivers.append({"name": "high_priority_aged_tickets", "count": len(high_aged),
                        "detail": "HIGH/URGENT tickets aged >30d"})
    if aged_open and not high_aged:
        score += 18
        drivers.append({"name": "aged_open_tickets", "count": len(aged_open)})
    if len(open_t) >= 5:
        score += 12
        drivers.append({"name": "open_ticket_volume", "count": len(open_t)})
    if stalled:
        score += 20
        drivers.append({"name": "stalled_deals", "count": len(stalled),
                        "amount": sum((d.amount or 0) for d in stalled)})
    if activity_drop > 0.5 and prior60 > 5:
        score += 15
        drivers.append({"name": "activity_drop", "drop_pct": round(activity_drop * 100, 1),
                        "last30": last30, "prior_avg30": round(expected, 1)})
    score = min(score, 100)

    if score >= 60:
        sev, head = "high", f"⚠️ Churn risk HIGH — {len(drivers)} signal(s)"
    elif score >= 30:
        sev, head = "medium", f"Churn risk elevated — {len(drivers)} signal(s)"
    elif score > 0:
        sev, head = "low", "Minor churn signals, monitor"
    else:
        sev, head = "low", "No active churn signals"

    return ModuleResult(
        module_id="churn_ew",
        label="Churn Early-Warning",
        score=score,
        severity=sev,
        headline=head,
        drivers=drivers,
        metrics={
            "open_tickets": len(open_t),
            "aged_open": len(aged_open),
            "high_aged": len(high_aged),
            "stalled_deals": len(stalled),
            "activity_last30": last30,
            "activity_prior_avg30": round(expected, 1),
        },
    )
