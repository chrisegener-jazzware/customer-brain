"""Per-ticket AI artifacts: summarize, draft response, triage.

Each function takes (Session, company_id, ticket_id) → typed dataclass.
Heuristic fallbacks when Claude isn't available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ActivitySignal, Company, TicketSignal
from ..sales._claude import call, render_payload


@dataclass
class TicketSummary:
    ticket_id: str
    summary: str        # markdown
    model: str
    is_fallback: bool


@dataclass
class TicketResponseDraft:
    ticket_id: str
    response_text: str  # plain text reply
    tone: str           # 'standard' | 'empathetic' | 'escalation'
    model: str
    is_fallback: bool


@dataclass
class TicketTriage:
    ticket_id: str
    should_escalate: bool
    severity: str       # 'low' | 'medium' | 'high' | 'critical'
    reasoning: str      # markdown
    suggested_owner_role: str
    model: str
    is_fallback: bool


def _gather_ticket_context(s: Session, company_id: str, ticket_id: str) -> dict[str, Any]:
    ticket = s.get(TicketSignal, ticket_id)
    if ticket is None or ticket.company_id != company_id:
        raise ValueError(f"ticket {ticket_id} not found on company {company_id}")
    company = s.get(Company, company_id)
    # Other open tickets on this account.
    other = s.scalars(
        select(TicketSignal).where(
            TicketSignal.company_id == company_id,
            TicketSignal.id != ticket_id,
        ).limit(20)
    ).all()
    # Recent related activities.
    acts = s.scalars(
        select(ActivitySignal).where(
            ActivitySignal.company_id == company_id,
        ).order_by(ActivitySignal.ts.desc()).limit(10)
    ).all()
    return {
        "ticket": {
            "id": ticket.id,
            "subject": ticket.subject,
            "priority": ticket.priority,
            "age_days": ticket.age_days,
            "is_open": ticket.is_open,
            "pipeline_stage": getattr(ticket, "pipeline_stage", None),
            "content": getattr(ticket, "content_preview", None),
            "hs_created_at": ticket.hs_created_at.isoformat() if ticket.hs_created_at else None,
        },
        "company": {"id": company_id, "name": company.name if company else None},
        "other_tickets": [
            {"id": t.id, "subject": t.subject, "priority": t.priority,
             "age_days": t.age_days, "is_open": t.is_open}
            for t in other
        ],
        "recent_activities": [
            {"kind": a.kind, "subject": a.subject, "direction": a.direction,
             "ts": a.ts.isoformat() if a.ts else None}
            for a in acts
        ],
    }


# ---- summarize ----

SUMMARIZE_PROMPT = """Summarize this support ticket in EXACTLY 3 bullets for a support AM about to respond.

Bullet 1: What the customer is asking / reporting (1 sentence).
Bullet 2: Relevant account context (other open tickets, recent activity).
Bullet 3: Suggested first action (1 verb-led sentence: "Reply with...", "Escalate to...", "Check...").

OUTPUT FORMAT (markdown):
- **What:** ...
- **Context:** ...
- **First action:** ...

RULES:
- Ground every bullet in the data. Don't invent.
- ≤40 words per bullet."""


def summarize_ticket(s: Session, company_id: str, ticket_id: str) -> TicketSummary:
    ctx = _gather_ticket_context(s, company_id, ticket_id)
    user_msg = f"Ticket context:\n```json\n{render_payload(ctx)}\n```"
    resp = call(SUMMARIZE_PROMPT, user_msg, max_tokens=400,
                fallback=_summary_fallback(ctx))
    return TicketSummary(
        ticket_id=ticket_id, summary=resp.text,
        model=resp.model, is_fallback=resp.is_fallback,
    )


def _summary_fallback(ctx: dict) -> str:
    t = ctx["ticket"]
    other_open = sum(1 for x in ctx["other_tickets"] if x["is_open"])
    return (
        f"- **What:** {t.get('subject') or 'No subject'}\n"
        f"- **Context:** Priority {t.get('priority','-')}, age {t.get('age_days','?')}d. "
        f"{other_open} other open ticket(s) on this account.\n"
        f"- **First action:** Review ticket thread in HubSpot and respond. "
        f"(Set ANTHROPIC_API_KEY for AI-generated specific action.)"
    )


# ---- draft response ----

DRAFT_RESPONSE_PROMPT = """Draft a professional support response to a hotel customer.

OUTPUT FORMAT (plain text, ≤140 words):
- Open with brief acknowledgment.
- Body: address the issue specifically using the ticket subject + context. If the data is thin, ask 1-2 clarifying questions instead of guessing.
- Close with clear next step (e.g., "I'll have an update by EOD").
- Sign off as "{am_name}".

TONE: warm, professional, hospitality-industry savvy. No corporate jargon.

RULES:
- Use ONLY facts from the data. Don't promise specifics you can't verify (no specific times, prices, ticket IDs unless they are in the data).
- If the ticket is HIGH/URGENT, acknowledge urgency."""


def draft_ticket_response(s: Session, company_id: str, ticket_id: str) -> TicketResponseDraft:
    ctx = _gather_ticket_context(s, company_id, ticket_id)
    user_msg = f"Ticket context:\n```json\n{render_payload(ctx)}\n```"
    resp = call(DRAFT_RESPONSE_PROMPT, user_msg, max_tokens=600,
                fallback=_response_fallback(ctx))
    # Tone inference from ticket priority.
    pri = (ctx["ticket"].get("priority") or "").upper()
    tone = "escalation" if pri in {"URGENT"} else ("empathetic" if pri == "HIGH" else "standard")
    return TicketResponseDraft(
        ticket_id=ticket_id, response_text=resp.text, tone=tone,
        model=resp.model, is_fallback=resp.is_fallback,
    )


def _response_fallback(ctx: dict) -> str:
    t = ctx["ticket"]
    return (
        f"Hi,\n\n"
        f"Thanks for flagging this — I'm looking into \"{t.get('subject','your request')}\" "
        f"right now and will have an update by end of day.\n\n"
        f"In the meantime, could you confirm the affected property/extension so we can "
        f"prioritize correctly?\n\n"
        f"Best,\n{{am_name}}"
    )


# ---- triage ----

TRIAGE_PROMPT = """Decide if this support ticket needs escalation and explain why.

OUTPUT FORMAT (strict JSON, no markdown fences):
{
  "should_escalate": true | false,
  "severity": "low" | "medium" | "high" | "critical",
  "reasoning": "2-3 sentence markdown explanation, citing specific data",
  "suggested_owner_role": "L1" | "L2" | "engineering" | "account_manager" | "exec"
}

RULES:
- "should_escalate": true if severity is high or critical, or if customer is at risk (multiple aged HIGH tickets, etc.).
- Use ONLY facts from the data. Don't invent customer threats.
- Be conservative: don't escalate trivial issues."""


def triage_ticket(s: Session, company_id: str, ticket_id: str) -> TicketTriage:
    ctx = _gather_ticket_context(s, company_id, ticket_id)
    user_msg = f"Ticket context:\n```json\n{render_payload(ctx)}\n```"
    resp = call(TRIAGE_PROMPT, user_msg, max_tokens=400,
                fallback=_triage_fallback(ctx))
    # Parse JSON.
    import json
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip().rstrip("`").strip()
    try:
        data = json.loads(text)
    except Exception:
        data = {"should_escalate": False, "severity": "medium",
                "reasoning": text or "(could not parse AI response)",
                "suggested_owner_role": "L1"}
    return TicketTriage(
        ticket_id=ticket_id,
        should_escalate=bool(data.get("should_escalate", False)),
        severity=str(data.get("severity", "medium")),
        reasoning=str(data.get("reasoning", "")),
        suggested_owner_role=str(data.get("suggested_owner_role", "L1")),
        model=resp.model,
        is_fallback=resp.is_fallback,
    )


def _triage_fallback(ctx: dict) -> str:
    import json
    t = ctx["ticket"]
    pri = (t.get("priority") or "").upper()
    age = t.get("age_days") or 0
    if pri == "URGENT" or (pri == "HIGH" and age > 14):
        out = {"should_escalate": True, "severity": "critical" if pri == "URGENT" else "high",
               "reasoning": f"{pri} priority ticket aged {age}d.", "suggested_owner_role": "L2"}
    elif pri == "HIGH" or age > 30:
        out = {"should_escalate": False, "severity": "high",
               "reasoning": f"{pri or 'normal'} priority, aged {age}d.", "suggested_owner_role": "L1"}
    else:
        out = {"should_escalate": False, "severity": "medium",
               "reasoning": "Routine ticket.", "suggested_owner_role": "L1"}
    return json.dumps(out)
