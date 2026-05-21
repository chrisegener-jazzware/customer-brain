"""Customer health scoring.

Takes raw signals from HubSpot tickets + integration_health stub, computes
component scores, rolls them into a 0-100 health score with red/yellow/green
band, and uses Claude to author a 1-paragraph narrative.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import get_settings
from .sources.hubspot_tickets import TicketSignals
from .sources.integration_health import IntegrationHealth

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class HealthScore:
    customer_id: str
    customer_name: str
    score: int                       # 0..100 (higher = healthier)
    flag: str                        # "green" | "yellow" | "red"
    narrative: str
    components: dict[str, float] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    account_manager: str | None = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Ticket aggregation helpers (works with both real TicketSignals + dict input)
# --------------------------------------------------------------------------- #
def summarise_tickets(
    tickets: list[dict[str, Any]] | TicketSignals,
    recent_window_days: int = 30,
) -> dict[str, Any]:
    """Produce a flat summary the scorer can consume.

    Accepts either a pre-aggregated TicketSignals or a list of raw ticket dicts
    (used by tests with fixtures). When given a list we compute open / recent /
    high-priority / avg-time-to-close locally.
    """
    if isinstance(tickets, TicketSignals):
        return {
            "total": tickets.total,
            "open": tickets.open_count,
            "recent_30d": tickets.total,  # already filtered to window
            "high_priority": tickets.escalated_count,
            "avg_time_to_close_hours": tickets.avg_time_to_close_hours,
            "last_ticket_at": (
                tickets.last_ticket_at.isoformat() if tickets.last_ticket_at else None
            ),
            "escalation_rate": tickets.escalation_rate,
        }

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=recent_window_days)
    open_stages = {"new", "open", "waiting_on_us", "waiting_on_contact", "1", "2", "3"}
    high_pri = {"HIGH", "URGENT", "P0", "P1"}

    total = len(tickets)
    open_count = 0
    recent_count = 0
    high_priority = 0
    close_durations: list[float] = []
    last_ticket_at: datetime | None = None

    for t in tickets:
        created = _coerce_dt(t.get("createdate") or t.get("created_at"))
        if not created:
            continue
        if last_ticket_at is None or created > last_ticket_at:
            last_ticket_at = created
        if created >= recent_cutoff:
            recent_count += 1
        stage = str(t.get("stage") or t.get("hs_pipeline_stage") or "").lower()
        if stage in open_stages:
            open_count += 1
        else:
            closed = _coerce_dt(t.get("closed_date") or t.get("closed_at"))
            if closed:
                close_durations.append((closed - created).total_seconds() / 3600.0)
        pri = str(t.get("priority") or t.get("hs_ticket_priority") or "").upper()
        if pri in high_pri:
            high_priority += 1

    return {
        "total": total,
        "open": open_count,
        "recent_30d": recent_count,
        "high_priority": high_priority,
        "avg_time_to_close_hours": (
            round(sum(close_durations) / len(close_durations), 2)
            if close_durations
            else None
        ),
        "last_ticket_at": last_ticket_at.isoformat() if last_ticket_at else None,
        "escalation_rate": round(high_priority / total, 3) if total else 0.0,
    }


def _coerce_dt(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw)
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Component scoring (each 0..100, higher = healthier)
# --------------------------------------------------------------------------- #
def _ticket_volume_score(summary: dict[str, Any]) -> float:
    recent = summary.get("recent_30d", 0) or 0
    # 0 tickets -> 100; 1-2 -> 85; 3-5 -> 70; 6-10 -> 50; 11-20 -> 25; >20 -> 5
    if recent <= 0:
        return 100.0
    if recent <= 2:
        return 85.0
    if recent <= 5:
        return 70.0
    if recent <= 10:
        return 50.0
    if recent <= 20:
        return 25.0
    return 5.0


def _escalation_score(summary: dict[str, Any]) -> float:
    high = summary.get("high_priority", 0) or 0
    rate = summary.get("escalation_rate", 0.0) or 0.0
    if high == 0:
        return 100.0
    base = max(0.0, 100.0 - (rate * 120.0))
    # extra penalty per absolute high-pri ticket
    return max(0.0, base - (high * 4.0))


def _integration_score(ih: IntegrationHealth) -> float:
    score = 100.0 - (ih.error_rate_7d * 120.0)
    score -= len(ih.failing_integrations) * 8.0
    if ih.last_successful_sync_hours_ago > 48:
        score -= 15.0
    elif ih.last_successful_sync_hours_ago > 24:
        score -= 7.0
    return max(0.0, min(100.0, score))


def _open_load_score(summary: dict[str, Any]) -> float:
    o = summary.get("open", 0) or 0
    if o == 0:
        return 100.0
    if o <= 2:
        return 80.0
    if o <= 5:
        return 60.0
    if o <= 10:
        return 35.0
    return 10.0


def _ttr_score(summary: dict[str, Any]) -> float:
    ttr = summary.get("avg_time_to_close_hours")
    if ttr is None:
        return 75.0  # neutral when nothing closed yet
    if ttr <= 8:
        return 100.0
    if ttr <= 24:
        return 85.0
    if ttr <= 72:
        return 65.0
    if ttr <= 168:
        return 45.0
    return 20.0


def compute_components(
    ticket_summary: dict[str, Any], integ: IntegrationHealth
) -> dict[str, float]:
    return {
        "ticket_volume": round(_ticket_volume_score(ticket_summary), 1),
        "escalation": round(_escalation_score(ticket_summary), 1),
        "open_load": round(_open_load_score(ticket_summary), 1),
        "time_to_close": round(_ttr_score(ticket_summary), 1),
        "integration_health": round(_integration_score(integ), 1),
    }


COMPONENT_WEIGHTS = {
    "ticket_volume": 0.20,
    "escalation": 0.25,
    "open_load": 0.15,
    "time_to_close": 0.15,
    "integration_health": 0.25,
}


def roll_up(components: dict[str, float]) -> int:
    total = 0.0
    weight_sum = 0.0
    for k, w in COMPONENT_WEIGHTS.items():
        if k in components:
            total += components[k] * w
            weight_sum += w
    if weight_sum == 0:
        return 50
    return int(round(total / weight_sum))


def flag_for(score: int) -> str:
    if score < 50:
        return "red"
    if score < 75:
        return "yellow"
    return "green"


# --------------------------------------------------------------------------- #
# Claude narrative
# --------------------------------------------------------------------------- #
NARRATIVE_PROMPT = """You are a Jazzware customer success analyst. Write ONE paragraph (3-5 sentences, plain prose, no bullets, no headings) summarizing this customer's current health for their Account Manager. Lead with the most material risk or strength, mention the specific signals driving the score, and end with a recommended next step.

Customer: {name} (id {cid})
Health score: {score}/100 ({flag})
Component scores: {components}
Ticket signals (30d): {tickets}
Integration health (7d): {integrations}

Return only the paragraph, no preamble."""


def _build_narrative(
    name: str,
    cid: str,
    score: int,
    flag: str,
    components: dict[str, float],
    tickets: dict[str, Any],
    integrations: dict[str, Any],
) -> str:
    s = get_settings()
    api_key = s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    prompt = NARRATIVE_PROMPT.format(
        name=name,
        cid=cid,
        score=score,
        flag=flag.upper(),
        components=json.dumps(components),
        tickets=json.dumps(tickets, default=str),
        integrations=json.dumps(integrations, default=str),
    )
    if not api_key:
        return _fallback_narrative(name, score, flag, components, tickets, integrations)
    try:
        from anthropic import Anthropic
        from _token_tracker import track as _tt_track

        client = _tt_track(Anthropic(api_key=api_key), project="customer-brain")
        resp = client.messages.create(
            model=s.claude_model,
            max_tokens=s.claude_summary_max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        return text or _fallback_narrative(
            name, score, flag, components, tickets, integrations
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Claude narrative failed for %s: %s — using fallback", cid, e)
        return _fallback_narrative(name, score, flag, components, tickets, integrations)


def _fallback_narrative(
    name: str,
    score: int,
    flag: str,
    components: dict[str, float],
    tickets: dict[str, Any],
    integrations: dict[str, Any],
) -> str:
    weakest = min(components.items(), key=lambda kv: kv[1]) if components else ("n/a", 0)
    return (
        f"{name} is {flag.upper()} at {score}/100. "
        f"Driver: {weakest[0]} component scoring {weakest[1]:.0f}. "
        f"Last 30d: {tickets.get('total', 0)} tickets "
        f"({tickets.get('open', 0)} open, {tickets.get('high_priority', 0)} high-priority); "
        f"integrations error rate {integrations.get('error_rate_7d', 0):.0%}, "
        f"{len(integrations.get('failing_integrations', []) or [])} failing. "
        "Recommend AM check-in this week."
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def score_customer(
    company: dict[str, Any],
    tickets: list[dict[str, Any]] | TicketSignals,
    integ: IntegrationHealth,
    account_manager: str | None = None,
    *,
    use_claude: bool = True,
) -> HealthScore:
    summary = summarise_tickets(tickets)
    components = compute_components(summary, integ)
    score = roll_up(components)
    flag = flag_for(score)
    cid = str(company.get("id") or company.get("customer_id"))
    name = str(company.get("name") or f"Company {cid}")
    if use_claude:
        narrative = _build_narrative(
            name, cid, score, flag, components, summary, integ.as_dict()
        )
    else:
        narrative = _fallback_narrative(
            name, score, flag, components, summary, integ.as_dict()
        )
    return HealthScore(
        customer_id=cid,
        customer_name=name,
        score=score,
        flag=flag,
        narrative=narrative,
        components=components,
        signals={"tickets": summary, "integrations": integ.as_dict()},
        account_manager=account_manager,
    )
