"""Stalled-deal explainer.

For a given deal id, gather context (recent activities mentioning it,
ticket history on the account, current stage history) and ask Claude
to explain why it's likely stalled + suggest one concrete next step.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ActivitySignal, DealSignal, TicketSignal
from ._claude import call, render_payload


PROMPT = """You analyze why a sales deal is stalled and recommend the next concrete step.

OUTPUT FORMAT (markdown, ≤200 words):
## Why this deal is stalled
2-3 sentence explanation grounded in the data. Cite specific activities/tickets inline like [activity:ID] or [ticket:ID].

## Most likely blockers
- 2-4 bullets with concrete blockers from the data, NOT generic sales advice.

## Recommended next step
Single concrete action (email, call, internal escalation, etc.) with a 1-line draft of what to do/say.

RULES:
- Use ONLY facts in the deal context. Do not invent customer objections.
- If the data is too thin to explain, say so plainly.
- Be specific; "follow up" is not acceptable.
"""


@dataclass
class StalledExplanation:
    deal_id: str
    deal_name: str | None
    markdown: str
    model: str
    is_fallback: bool


def explain_stalled_deal(s: Session, company_id: str, deal_id: str) -> StalledExplanation:
    deal = s.get(DealSignal, deal_id)
    if deal is None or deal.company_id != company_id:
        raise ValueError(f"deal {deal_id} not found on company {company_id}")

    # Context: deal itself + last 20 activities + open tickets.
    recent_acts = s.scalars(
        select(ActivitySignal)
        .where(ActivitySignal.company_id == company_id)
        .order_by(ActivitySignal.ts.desc())
        .limit(20)
    ).all()
    open_tickets = s.scalars(
        select(TicketSignal).where(
            TicketSignal.company_id == company_id,
            TicketSignal.is_open == True,  # noqa: E712
        )
    ).all()

    payload = {
        "deal": {
            "id": deal.id,
            "name": deal.name,
            "amount": deal.amount,
            "stage": deal.stage,
            "days_in_stage": deal.days_in_stage,
            "stalled": deal.stalled,
            "last_activity": deal.last_activity.isoformat() if deal.last_activity else None,
            "hs_created_at": deal.hs_created_at.isoformat() if deal.hs_created_at else None,
            "stage_history": getattr(deal, "stage_history", None),
        },
        "recent_activities": [
            {
                "id": a.id,
                "kind": a.kind,
                "subject": a.subject,
                "direction": a.direction,
                "ts": a.ts.isoformat() if a.ts else None,
            }
            for a in recent_acts
        ],
        "open_tickets": [
            {"id": t.id, "subject": t.subject, "priority": t.priority, "age_days": t.age_days}
            for t in open_tickets
        ],
    }

    user_msg = f"Deal context:\n```json\n{render_payload(payload)}\n```"
    resp = call(PROMPT, user_msg, max_tokens=900,
                fallback=_heuristic_explanation(payload))
    return StalledExplanation(
        deal_id=deal_id,
        deal_name=deal.name,
        markdown=resp.text,
        model=resp.model,
        is_fallback=resp.is_fallback,
    )


def _heuristic_explanation(payload: dict) -> str:
    d = payload.get("deal") or {}
    acts = payload.get("recent_activities") or []
    out_acts = [a for a in acts if a.get("direction") == "OUTGOING"]
    in_acts = [a for a in acts if a.get("direction") == "INCOMING"]
    days = d.get("days_in_stage")
    lines = [
        "## Why this deal is stalled (heuristic)",
        f"Deal `{d.get('name','?')}` has been in stage `{d.get('stage','?')}` "
        f"for {days}d. Recent outbound: {len(out_acts)}, inbound: {len(in_acts)}.",
        "",
        "## Most likely blockers",
    ]
    if not in_acts and out_acts:
        lines.append("- Customer hasn't responded to recent outbound (silence pattern)")
    if not acts:
        lines.append("- No recent activity at all — relationship is cold")
    if d.get("stalled"):
        lines.append("- HubSpot flagged stalled (no activity 30d+)")
    if payload.get("open_tickets"):
        lines.append(f"- {len(payload['open_tickets'])} open support ticket(s) may be competing for attention")
    lines += [
        "",
        "## Recommended next step",
        "Set ANTHROPIC_API_KEY for AI-generated specific recommendation.",
    ]
    return "\n".join(lines)
