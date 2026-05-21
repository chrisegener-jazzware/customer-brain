"""Pre-call brief generator (sales mode).

Output: 1-page markdown brief with sections:
  * TL;DR
  * Recent activity (last 30 days)
  * Open deals + value
  * Open tickets / risks
  * Talking points
  * Suggested questions
  * Recommended next step
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..rollup.service import build_signals_payload
from ._claude import call, render_payload


PROMPT = """You generate a SHORT pre-call sales brief for an account manager.

OUTPUT FORMAT (markdown, ≤350 words total):
# {Account Name} — Pre-call Brief

## TL;DR
1-2 sentence headline: where this relationship sits right now.

## Recent activity (last 30 days)
3-5 bullets of what's happened. Cite specific tickets/deals/emails inline like [ticket:ID] or [deal:ID].

## Open deals
- For each open deal: name, amount, stage, days_in_stage. Note if stalled.

## Open tickets / risks
- List open support tickets with priority + age. Flag HIGH/URGENT.

## Talking points
3-5 things to bring up on the call. Mix relationship, business, and risk topics. Be specific.

## Suggested questions
3 open-ended questions to ask the customer.

## Recommended next step
1 concrete action the AM should take after this call.

RULES:
- Use ONLY facts from the signals payload. Do not invent.
- If a section has no data, write "—".
- Cite specific records inline.
- Keep it tight — the AM reads this in 60 seconds before a call.
"""


@dataclass
class PrecallBrief:
    markdown: str
    model: str
    is_fallback: bool


def generate_precall_brief(s: Session, company_id: str) -> PrecallBrief:
    payload = build_signals_payload(s, company_id)
    user_msg = f"Signals payload:\n```json\n{render_payload(payload)}\n```"
    resp = call(PROMPT, user_msg, max_tokens=2000,
                fallback=_heuristic_brief(payload, company_id))
    return PrecallBrief(markdown=resp.text, model=resp.model, is_fallback=resp.is_fallback)


def _heuristic_brief(payload: dict[str, Any], company_id: str) -> str:
    """No-AI fallback. Pure data summary."""
    company = payload.get("company") or {}
    name = company.get("name") or company_id
    tickets = payload.get("tickets") or []
    deals = payload.get("deals") or []
    open_t = [t for t in tickets if t.get("is_open")]
    open_d = [d for d in deals if not (d.get("is_won") or d.get("is_lost"))]
    aged = [t for t in open_t if (t.get("age_days") or 0) > 30]

    lines = [
        f"# {name} — Pre-call Brief (heuristic)",
        "",
        "## TL;DR",
        f"_AI unavailable — showing data summary._ "
        f"{len(open_t)} open ticket(s), {len(open_d)} open deal(s).",
        "",
        "## Open deals",
    ]
    if open_d:
        for d in open_d[:8]:
            lines.append(
                f"- **{d.get('name','?')}** — ${d.get('amount') or 0:,.0f} — "
                f"{d.get('stage','?')} ({d.get('days_in_stage','?')}d in stage)"
            )
    else:
        lines.append("—")
    lines += ["", "## Open tickets"]
    if open_t:
        for t in open_t[:8]:
            lines.append(
                f"- {t.get('subject','?')} — priority {t.get('priority','-')} "
                f"— age {t.get('age_days','?')}d"
            )
    else:
        lines.append("—")
    lines += ["", "## Talking points",
              f"- Address {len(aged)} aged ticket(s)" if aged else "- Confirm priorities for next quarter",
              "- Review open deal pipeline + next step",
              "- Ask about expansion / sister properties",
              "",
              "## Recommended next step",
              "Set ANTHROPIC_API_KEY for richer AI-generated brief."]
    return "\n".join(lines)
