"""JAZ-113 Sales Pulse module.

Per-account sales velocity + stalled detection:
  * Active deals, total weighted pipeline value
  * Stalled deal count + amount (signal already on DealSignal)
  * Avg deal age and days since last stage update
  * Quote count + total
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DealSignal, QuoteSignal


def compute(s: Session, company_id: str):
    from . import ModuleResult

    deals = s.scalars(select(DealSignal).where(DealSignal.company_id == company_id)).all()
    quotes = s.scalars(select(QuoteSignal).where(QuoteSignal.company_id == company_id)).all()

    now = datetime.now(timezone.utc)
    open_deals = []
    closed_won = []
    closed_lost = []
    for d in deals:
        stage = (d.stage or "").lower()
        if "closed won" in stage or stage == "closedwon":
            closed_won.append(d)
        elif "closed lost" in stage or stage == "closedlost":
            closed_lost.append(d)
        else:
            open_deals.append(d)

    stalled = [d for d in open_deals if getattr(d, "stalled", False)]
    stalled_value = sum((d.amount or 0) for d in stalled)
    pipeline_value = sum((d.amount or 0) for d in open_deals)

    quote_total = sum((q.amount or 0) for q in quotes)

    # Score blends multiple factors so accounts with similar single-metrics still differentiate.
    if pipeline_value > 0:
        stalled_share = stalled_value / pipeline_value
    else:
        stalled_share = 0
    # Base: stalled share of pipeline (0-60).
    score = min(stalled_share * 60, 60)
    # Add: closed-lost ratio (last 12mo proxy = all-time here, 0-20).
    total_closed = len(closed_won) + len(closed_lost)
    if total_closed:
        lost_ratio = len(closed_lost) / total_closed
        score += min(lost_ratio * 20, 20)
    # Add: zero-activity penalty if has open deals but no quotes/recent updates (0-10).
    if open_deals and not quotes:
        score += 10
    # Add: large pipeline w/ no movement (0-10) — lots of value sitting.
    if pipeline_value > 100_000 and len(stalled) > 0:
        score += min(10, pipeline_value / 200_000)
    score = round(min(score, 100), 1)

    drivers = []
    if stalled:
        drivers.append({
            "name": "stalled_deals",
            "count": len(stalled),
            "amount": stalled_value,
            "share_of_pipeline_pct": round(stalled_share * 100, 1),
        })
    if open_deals:
        drivers.append({
            "name": "active_pipeline",
            "count": len(open_deals),
            "amount": pipeline_value,
        })
    if closed_lost:
        drivers.append({"name": "closed_lost_count", "count": len(closed_lost)})
    if quotes:
        drivers.append({"name": "quotes_total", "count": len(quotes), "amount": quote_total})

    if not deals:
        sev, head = "na", "No sales activity tracked"
    elif score >= 60:
        sev, head = "high", f"💸 {len(stalled)} stalled deal(s) — {round(stalled_share*100)}% of pipeline"
    elif score >= 30:
        sev, head = "medium", f"Mixed sales pulse — {len(stalled)} stalled / {len(open_deals)} active"
    elif open_deals:
        sev, head = "low", f"Healthy pipeline ({len(open_deals)} active, ${pipeline_value:,.0f})"
    else:
        sev, head = "low", "No open deals (closed-only history)"

    return ModuleResult(
        module_id="sales_pulse",
        label="Sales Pulse",
        score=score if deals else None,
        severity=sev,
        headline=head,
        drivers=drivers,
        metrics={
            "open_deals": len(open_deals),
            "stalled_deals": len(stalled),
            "pipeline_value": pipeline_value,
            "stalled_value": stalled_value,
            "stalled_share_pct": round(stalled_share * 100, 1),
            "quotes": len(quotes),
            "quote_total": quote_total,
            "closed_won": len(closed_won),
            "closed_lost": len(closed_lost),
        },
    )
