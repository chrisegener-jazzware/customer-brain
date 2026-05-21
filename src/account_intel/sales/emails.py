"""Follow-up email drafter.

Generates a copy/paste email draft for a given account, optionally
scoped to a specific deal or contact. Returns subject + body.

We don't auto-send — output goes to clipboard / copy button in UI.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ActivitySignal, Company, ContactSignal, DealSignal
from ._claude import call, render_payload


PROMPT = """You draft a SHORT follow-up email from an AM to a hotel customer contact.

OUTPUT FORMAT (strict JSON, no markdown fences):
{
  "subject": "...",
  "body": "Hi {first_name},\\n\\n... 3-5 short paragraphs ... \\n\\nBest,\\n{am_name}"
}

EMAIL STYLE:
- Professional, warm, hospitality industry tone.
- ≤150 words in body.
- Reference 1-2 specific recent items from the signals (a ticket, a deal, a recent meeting).
- Clear call-to-action in the last paragraph.
- Use {first_name} as the recipient placeholder if no contact name available.
- Use {am_name} as the sender placeholder.

RULES:
- Return strict JSON only.
- Do not invent facts. If unsure, keep claims general.
- No emojis, no marketing buzzwords.
"""


@dataclass
class EmailDraft:
    subject: str
    body: str
    model: str
    is_fallback: bool
    suggested_to_email: str | None = None
    suggested_to_name: str | None = None


def draft_followup_email(
    s: Session,
    company_id: str,
    deal_id: str | None = None,
    contact_id: str | None = None,
) -> EmailDraft:
    company = s.get(Company, company_id)
    deal = s.get(DealSignal, deal_id) if deal_id else None
    contact = s.get(ContactSignal, contact_id) if contact_id else None

    # If no contact pinned, pick the most-recently-active contact.
    if contact is None:
        contact = s.scalars(
            select(ContactSignal)
            .where(ContactSignal.company_id == company_id)
            .order_by(ContactSignal.last_activity_at.desc().nullslast())
            .limit(1)
        ).first()

    recent_acts = s.scalars(
        select(ActivitySignal)
        .where(ActivitySignal.company_id == company_id)
        .order_by(ActivitySignal.ts.desc())
        .limit(10)
    ).all()

    payload = {
        "company": {"id": company_id, "name": company.name if company else None},
        "deal": {
            "id": deal.id, "name": deal.name, "stage": deal.stage, "amount": deal.amount,
            "days_in_stage": deal.days_in_stage,
        } if deal else None,
        "contact": {
            "id": contact.id,
            "name": _full_name(contact),
            "email": contact.email,
            "job_title": contact.job_title,
        } if contact else None,
        "recent_activities": [
            {"kind": a.kind, "subject": a.subject, "direction": a.direction,
             "ts": a.ts.isoformat() if a.ts else None}
            for a in recent_acts
        ],
    }

    user_msg = f"Context:\n```json\n{render_payload(payload)}\n```"
    resp = call(PROMPT, user_msg, max_tokens=800,
                fallback=_heuristic_email(payload))

    # Parse JSON response (strip optional fences just in case)
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    import json
    try:
        data = json.loads(text)
        subject = data.get("subject", "Follow-up").strip()
        body = data.get("body", "").strip()
    except Exception:
        # If Claude returned non-JSON, treat whole thing as body.
        subject = "Follow-up"
        body = text

    return EmailDraft(
        subject=subject,
        body=body,
        model=resp.model,
        is_fallback=resp.is_fallback,
        suggested_to_email=contact.email if contact else None,
        suggested_to_name=_full_name(contact) if contact else None,
    )


def _full_name(contact) -> str | None:
    if contact is None:
        return None
    parts = [contact.first_name, contact.last_name]
    name = " ".join(p for p in parts if p).strip()
    return name or None


def _heuristic_email(payload: dict) -> str:
    import json
    co = (payload.get("company") or {}).get("name", "your team")
    contact = payload.get("contact") or {}
    deal = payload.get("deal") or {}
    first = (contact.get("name") or "{first_name}").split(" ")[0]

    if deal:
        body = (
            f"Hi {first},\n\n"
            f"Wanted to check in on **{deal.get('name', 'the project')}**. "
            f"It's been sitting in `{deal.get('stage','?')}` and I want to "
            f"make sure we're not missing anything on our side.\n\n"
            f"Could we set up 20 minutes this week to walk through outstanding "
            f"questions and the next milestone?\n\n"
            f"Best,\n{{am_name}}"
        )
        subject = f"Quick check-in — {deal.get('name', co)}"
    else:
        body = (
            f"Hi {first},\n\n"
            f"Checking in on {co}. Want to make sure we're aligned on priorities "
            f"and that any open items on our side are moving.\n\n"
            f"Happy to jump on a quick call if useful.\n\n"
            f"Best,\n{{am_name}}"
        )
        subject = f"Checking in — {co}"
    return json.dumps({"subject": subject, "body": body})
