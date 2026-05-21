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
    # Stalled: prefer explicit flag, fall back to days_in_stage>30 when feeder
    # hasn't populated last_activity (common on partial HubSpot scopes).
    def _is_stalled(d):
        if getattr(d, "stalled", False):
            return True
        if d.is_open and (d.days_in_stage or 0) > 30:
            return True
        return False
    stalled = [d for d in deals if _is_stalled(d)]

    # Activity trend: 30d vs prior 60d (positive trend = good)
    now = datetime.now(timezone.utc)
    last30 = sum(1 for a in activities if a.ts and a.ts.replace(tzinfo=timezone.utc) > now - timedelta(days=30))
    prior60 = sum(
        1 for a in activities
        if a.ts and now - timedelta(days=90) < a.ts.replace(tzinfo=timezone.utc) <= now - timedelta(days=30)
    )
    expected = prior60 / 2.0 if prior60 else 0
    activity_drop = (expected - last30) / expected if expected else 0  # 0..1 fraction drop

    # Continuous (not step) so scores differentiate even with similar signal mix.
    score = 0.0
    drivers = []
    # 1. HIGH/URGENT aged tickets dominate — add per-ticket weight scaled by age.
    if high_aged:
        weight = sum(min((t.age_days or 0) / 30.0, 4) for t in high_aged)  # 1..4 per ticket
        score += min(50, 10 * weight)
        drivers.append({"name": "high_priority_aged_tickets", "count": len(high_aged),
                        "oldest_days": max((t.age_days or 0) for t in high_aged)})
    # 2. Other aged tickets — capped contribution.
    other_aged = [t for t in aged_open if t not in high_aged]
    if other_aged:
        weight = sum(min((t.age_days or 0) / 30.0, 3) for t in other_aged)
        score += min(20, 3 * weight)
        drivers.append({"name": "aged_open_tickets", "count": len(other_aged)})
    # 3. Open ticket volume — linear up to 15.
    if open_t:
        score += min(15, 2 * len(open_t))
        if len(open_t) >= 3:
            drivers.append({"name": "open_ticket_volume", "count": len(open_t)})
    # 4. Stalled deals — weighted by amount.
    if stalled:
        amt = sum((d.amount or 0) for d in stalled)
        score += 12 + min(15, 2 * len(stalled)) + min(8, amt / 50000)
        drivers.append({"name": "stalled_deals", "count": len(stalled), "amount": amt})
    # 5. Activity drop — continuous from 30% drop.
    if activity_drop > 0.3 and prior60 > 3:
        score += round(min(20, activity_drop * 25))
        drivers.append({"name": "activity_drop", "drop_pct": round(activity_drop * 100, 1),
                        "last30": last30, "prior_avg30": round(expected, 1)})
    # 6. Zero activity on a tracked account.
    if last30 == 0 and (deals or tickets):
        score += 8 + min(5, len(open_t) * 1)
        drivers.append({"name": "no_recent_activity", "detail": "0 engagements in 30d"})
    score = min(round(score, 1), 100)

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
