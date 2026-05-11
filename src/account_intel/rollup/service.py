"""Claude roll-up service (JAZ-108, expanded).

Reads all signals for a company → calls Claude → writes ai_assessment row.
Now also computes multi-zoom summaries (TL;DR, support/sales/relationship,
risk drivers, opportunities, client-safe TL;DR & insights) stored in
`ai_assessment.summaries_json`.

Cached by signals_hash for ROLLUP_CACHE_TTL_SECONDS (default 6h).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import (
    ActivitySignal,
    AIAssessment,
    Company,
    ContactSignal,
    DealSignal,
    IntegrationSignal,
    QuoteSignal,
    SessionLocal,
    TicketSignal,
)

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
SUMMARIES_PROMPT_PATH = Path(__file__).parent / "prompts" / "summaries.md"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def build_signals_payload(s: Session, company_id: str) -> dict[str, Any]:
    """Assemble JSON payload the model sees. Pure function — easy to test."""
    c = s.get(Company, company_id)
    if c is None:
        raise ValueError(f"Unknown company {company_id}")

    tickets = s.scalars(
        select(TicketSignal).where(TicketSignal.company_id == company_id)
    ).all()
    deals = s.scalars(
        select(DealSignal).where(DealSignal.company_id == company_id)
    ).all()
    integrations = s.scalars(
        select(IntegrationSignal).where(IntegrationSignal.company_id == company_id)
    ).all()

    return {
        "company": {
            "id": c.id,
            "name": c.name,
            "domain": c.domain,
            "industry": c.industry,
            "country": c.country,
            "city": c.city,
            "lifecycle_stage": c.lifecycle_stage,
            "annual_revenue": c.annual_revenue,
            "employees": c.employees,
        },
        "tickets": [
            {
                "id": t.id,
                "subject": t.subject,
                "stage": t.pipeline_stage,
                "priority": t.priority,
                "is_open": t.is_open,
                "age_days": round(t.age_days, 1) if t.age_days else None,
                "resolution_days": round(t.resolution_days, 1) if t.resolution_days else None,
                "created": _iso(t.hs_created_at),
                "closed": _iso(t.hs_closed_at),
            }
            for t in tickets
        ],
        "deals": [
            {
                "id": d.id,
                "name": d.name,
                "amount": d.amount,
                "pipeline": d.pipeline,
                "stage": d.stage,
                "is_open": d.is_open,
                "is_won": d.is_won,
                "stalled": d.stalled,
                "days_in_stage": d.days_in_stage,
                "last_activity": _iso(d.last_activity),
            }
            for d in deals
        ],
        "integrations": [
            {
                "name": i.integration_name,
                "uptime_pct_30d": i.uptime_pct_30d,
                "last_sync": _iso(i.last_sync),
                "error_count_24h": i.error_count_24h,
                "status": i.status,
            }
            for i in integrations
        ],
    }


def build_summaries_payload(s: Session, company_id: str) -> dict[str, Any]:
    """Richer payload for multi-zoom summaries (contacts, activities, quotes, metrics)."""
    c = s.get(Company, company_id)
    if c is None:
        raise ValueError(f"Unknown company {company_id}")

    base = build_signals_payload(s, company_id)

    contacts = s.scalars(
        select(ContactSignal).where(ContactSignal.company_id == company_id)
    ).all()
    activities = s.scalars(
        select(ActivitySignal)
        .where(ActivitySignal.company_id == company_id)
        .order_by(desc(ActivitySignal.ts))
        .limit(50)
    ).all()
    quotes = s.scalars(
        select(QuoteSignal).where(QuoteSignal.company_id == company_id)
    ).all()

    base["contacts"] = [
        {
            "id": ct.id,
            "name": " ".join(filter(None, [ct.first_name, ct.last_name])).strip() or ct.email,
            "title": ct.job_title,
            "email": ct.email,
            "last_activity_at": _iso(ct.last_activity_at),
            "days_since_activity": (
                round(ct.days_since_activity, 1) if ct.days_since_activity else None
            ),
        }
        for ct in contacts
    ]
    base["activities"] = [
        {
            "id": a.id,
            "kind": a.kind,
            "subject": a.subject,
            "direction": a.direction,
            "ts": _iso(a.ts),
        }
        for a in activities
    ]
    base["quotes"] = [
        {
            "id": q.id,
            "title": q.title,
            "amount": q.amount,
            "status": q.status,
            "created": _iso(q.hs_created_at),
            "days_to_sign": q.days_to_sign,
        }
        for q in quotes
    ]
    base["metrics"] = {
        "open_pipeline_amount": c.open_pipeline_amount,
        "won_amount_90d": c.won_amount_90d,
        "lost_amount_90d": c.lost_amount_90d,
        "avg_cycle_days_won": c.avg_cycle_days_won,
        "win_rate_90d": c.win_rate_90d,
        "stuck_deals_count": c.stuck_deals_count,
        "support_load_30d": c.support_load_30d,
        "first_response_avg_hours": c.first_response_avg_hours,
        "repeat_issue_count": c.repeat_issue_count,
        "last_human_activity_at": _iso(c.last_human_activity_at),
        "days_since_last_activity": c.days_since_last_activity,
    }
    return base


def hash_signals(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _pick_model(payload: dict) -> str:
    if len(payload.get("tickets", [])) > 50 or len(payload.get("deals", [])) > 20:
        return settings.anthropic_model_large
    return settings.anthropic_model


class RollupService:
    def __init__(self, session_factory=SessionLocal, anthropic_client=None):
        self.session_factory = session_factory
        self._client = anthropic_client  # injected for tests
        self._prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""
        self._summ_prompt = (
            SUMMARIES_PROMPT_PATH.read_text(encoding="utf-8")
            if SUMMARIES_PROMPT_PATH.exists()
            else ""
        )

    # --- public --------------------------------------------------------------

    def get_or_create(self, company_id: str, force: bool = False) -> AIAssessment:
        with self.session_factory() as s:
            payload = build_signals_payload(s, company_id)
            summ_payload = build_summaries_payload(s, company_id)
            sig_hash = hash_signals(summ_payload)
            ttl = timedelta(seconds=settings.rollup_cache_ttl_seconds)
            cutoff = datetime.now(UTC) - ttl
            if not force:
                cached = s.scalars(
                    select(AIAssessment)
                    .where(AIAssessment.company_id == company_id)
                    .order_by(desc(AIAssessment.generated_at))
                    .limit(1)
                ).first()
                if (
                    cached
                    and cached.signals_hash == sig_hash
                    and cached.generated_at.replace(tzinfo=UTC) > cutoff
                ):
                    return cached
            result = self._call_claude(payload)
            summaries = self._call_claude_summaries(summ_payload)
            row = AIAssessment(
                company_id=company_id,
                risk_flag=result["risk_flag"],
                risk_score=result.get("risk_score"),
                narrative=result["narrative"],
                next_best_actions=result.get("next_best_actions", []),
                signals_hash=sig_hash,
                model=result["_model"],
                summaries_json=summaries,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def recompute_summaries(self, company_id: str) -> AIAssessment:
        """Recompute the multi-zoom summaries (cheaper than full rollup)."""
        return self.get_or_create(company_id, force=True)

    # --- claude --------------------------------------------------------------

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not settings.anthropic_api_key or settings.anthropic_api_key.startswith("sk-ant-REPLACE"):
            log.info("ANTHROPIC_API_KEY not set — using heuristic fallback")
            return None
        try:
            import anthropic  # type: ignore

            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            return self._client
        except Exception as e:  # noqa: BLE001
            log.warning("anthropic client unavailable: %s", e)
            return None

    def _call_claude(self, payload: dict) -> dict:
        model = _pick_model(payload)
        client = self._ensure_client()
        if client is None:
            return {**self._heuristic_fallback(payload), "_model": "heuristic-fallback"}

        try:
            user_msg = "Signals for assessment:\n```json\n" + json.dumps(payload, indent=2) + "\n```"
            resp = client.messages.create(
                model=model,
                max_tokens=1500,
                system=self._prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = resp.content[0].text  # type: ignore[attr-defined]
            data = json.loads(_strip_fences(text))
            data["_model"] = model
            return data
        except Exception as e:  # noqa: BLE001
            log.exception("Claude roll-up failed, falling back: %s", e)
            return {**self._heuristic_fallback(payload), "_model": "heuristic-fallback"}

    def _call_claude_summaries(self, payload: dict) -> dict:
        """Multi-zoom summaries. Falls back to heuristic dict on error."""
        model = _pick_model(payload)
        client = self._ensure_client()
        if client is None:
            return self._heuristic_summaries(payload)
        try:
            user_msg = (
                "Signals for multi-zoom summaries:\n```json\n"
                + json.dumps(payload, indent=2)
                + "\n```"
            )
            resp = client.messages.create(
                model=model,
                max_tokens=1800,
                system=self._summ_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = resp.content[0].text  # type: ignore[attr-defined]
            data = json.loads(_strip_fences(text))
            return data
        except Exception as e:  # noqa: BLE001
            log.exception("Claude summaries failed, using heuristic: %s", e)
            return self._heuristic_summaries(payload)

    # --- heuristic fallbacks -------------------------------------------------

    @staticmethod
    def _heuristic_fallback(payload: dict) -> dict:
        tickets = payload.get("tickets", [])
        deals = payload.get("deals", [])
        open_t = [t for t in tickets if t.get("is_open")]
        old_open = [t for t in open_t if (t.get("age_days") or 0) > 30]
        stalled = [d for d in deals if d.get("stalled")]
        crit_open = [
            t
            for t in open_t
            if (t.get("priority") or "").upper() in {"HIGH", "URGENT"} and (t.get("age_days") or 0) > 14
        ]

        if (len(open_t) >= 3 and old_open and stalled) or crit_open:
            flag, score = "red", 75
        elif old_open or stalled or len(open_t) >= 5:
            flag, score = "yellow", 50
        else:
            flag, score = "green", 15

        bits = []
        if open_t:
            bits.append(f"{len(open_t)} open ticket(s)")
        if old_open:
            bits.append(f"{len(old_open)} aged >30d")
        if stalled:
            total = sum((d.get("amount") or 0) for d in stalled)
            bits.append(f"{len(stalled)} stalled deal(s) worth ${total:,.0f}")
        narrative = (
            f"Heuristic fallback (Claude unavailable). Signals: {', '.join(bits) or 'no notable issues'}."
        )

        actions: list[dict] = []
        if crit_open:
            actions.append(
                {"who": "Support", "action": "Escalate aged HIGH-priority tickets", "rationale": "SLA breach risk"}
            )
        if stalled:
            actions.append(
                {"who": "Sales", "action": "Re-engage stalled deal", "rationale": ">30d no activity"}
            )
        if not actions:
            actions.append({"who": "CSM", "action": "Routine check-in", "rationale": "No flags"})

        return {
            "risk_flag": flag,
            "risk_score": score,
            "narrative": narrative,
            "next_best_actions": actions[:3],
        }

    @staticmethod
    def _heuristic_summaries(payload: dict) -> dict:
        c = payload.get("company", {}) or {}
        tickets = payload.get("tickets", []) or []
        deals = payload.get("deals", []) or []
        activities = payload.get("activities", []) or []
        contacts = payload.get("contacts", []) or []
        metrics = payload.get("metrics", {}) or {}

        open_t = [t for t in tickets if t.get("is_open")]
        old_open = [t for t in open_t if (t.get("age_days") or 0) > 30]
        open_d = [d for d in deals if d.get("is_open")]
        stalled = [d for d in deals if d.get("stalled")]
        last_act = activities[0] if activities else None

        name = c.get("name") or "this account"
        tldr_bits = []
        if old_open:
            tldr_bits.append(f"{len(old_open)} aged ticket(s) need attention")
        if stalled:
            tldr_bits.append(f"{len(stalled)} stalled deal(s)")
        if not tldr_bits and open_t:
            tldr_bits.append(f"{len(open_t)} open ticket(s)")
        tldr = (
            f"{name}: " + "; ".join(tldr_bits)
            if tldr_bits
            else f"{name}: green — no immediate flags."
        )

        support_summary = (
            f"{len(open_t)} open ticket(s) (of {len(tickets)} total). "
            f"{len(old_open)} aged >30 days."
            if tickets
            else "No support tickets on record."
        )
        sales_summary = (
            f"{len(open_d)} open deal(s) totaling ${(metrics.get('open_pipeline_amount') or 0):,.0f}; "
            f"{len(stalled)} stalled."
            if deals
            else "No deals on record."
        )
        rel = (
            f"{len(contacts)} contact(s) on file. "
            + (f"Last activity: {last_act.get('kind')} on {last_act.get('ts','?')[:10]}." if last_act else "No recent activities recorded.")
        )

        risk_drivers: list[str] = []
        if old_open:
            risk_drivers.append(f"{len(old_open)} ticket(s) >30 days old")
        if stalled:
            risk_drivers.append(f"{len(stalled)} stalled deal(s)")
        if metrics.get("repeat_issue_count"):
            risk_drivers.append(
                f"{metrics['repeat_issue_count']} repeat-issue cluster(s) in last 30 days"
            )
        if (metrics.get("days_since_last_activity") or 0) > 21:
            risk_drivers.append(
                f"No human activity in {metrics['days_since_last_activity']:.0f} days"
            )
        if not risk_drivers:
            risk_drivers.append("No notable risk drivers")

        opportunities: list[str] = []
        if metrics.get("won_amount_90d"):
            opportunities.append(f"Won ${metrics['won_amount_90d']:,.0f} in last 90d — replicate playbook")
        if open_d:
            opportunities.append(f"${(metrics.get('open_pipeline_amount') or 0):,.0f} live pipeline to close")
        if not opportunities:
            opportunities.append("No standout expansion signals")

        client_tldr = f"Welcome back to your {name} portal — here is your latest service snapshot."
        client_insights = (
            f"You have {len(open_t)} active service request(s). "
            f"We've resolved {len([t for t in tickets if not t.get('is_open')])} requests to date."
        )

        return {
            "tldr": tldr,
            "support_summary": support_summary,
            "sales_summary": sales_summary,
            "relationship_summary": rel,
            "risk_drivers": risk_drivers[:5],
            "opportunities": opportunities[:5],
            "client_tldr": client_tldr,
            "client_insights": client_insights,
        }


def _strip_fences(text: str) -> str:
    """Tolerate Claude returning ```json … ``` instead of bare JSON."""
    t = text.strip()
    if t.startswith("```"):
        # remove first line
        first_nl = t.find("\n")
        if first_nl >= 0:
            t = t[first_nl + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
