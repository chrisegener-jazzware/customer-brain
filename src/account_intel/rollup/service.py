"""Claude roll-up service (JAZ-108).

Reads all signals for a company → calls Claude → writes ai_assessment row.
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
from ..db import AIAssessment, Company, DealSignal, IntegrationSignal, SessionLocal, TicketSignal

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"


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


def hash_signals(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _pick_model(payload: dict) -> str:
    """Escalate to opus for complex/large accounts (>50 tickets or >20 deals)."""
    if len(payload.get("tickets", [])) > 50 or len(payload.get("deals", [])) > 20:
        return settings.anthropic_model_large
    return settings.anthropic_model


class RollupService:
    def __init__(self, session_factory=SessionLocal, anthropic_client=None):
        self.session_factory = session_factory
        self._client = anthropic_client  # injected for tests
        self._prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""

    # --- public --------------------------------------------------------------

    def get_or_create(self, company_id: str, force: bool = False) -> AIAssessment:
        with self.session_factory() as s:
            payload = build_signals_payload(s, company_id)
            sig_hash = hash_signals(payload)
            ttl = timedelta(seconds=settings.rollup_cache_ttl_seconds)
            cutoff = datetime.now(UTC) - ttl
            if not force:
                cached = s.scalars(
                    select(AIAssessment)
                    .where(AIAssessment.company_id == company_id)
                    .order_by(desc(AIAssessment.generated_at))
                    .limit(1)
                ).first()
                if cached and cached.signals_hash == sig_hash and cached.generated_at.replace(
                    tzinfo=UTC
                ) > cutoff:
                    return cached
            result = self._call_claude(payload)
            row = AIAssessment(
                company_id=company_id,
                risk_flag=result["risk_flag"],
                risk_score=result.get("risk_score"),
                narrative=result["narrative"],
                next_best_actions=result.get("next_best_actions", []),
                signals_hash=sig_hash,
                model=result["_model"],
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    # --- claude --------------------------------------------------------------

    def _call_claude(self, payload: dict) -> dict:
        model = _pick_model(payload)
        if self._client is None:
            if not settings.anthropic_api_key or settings.anthropic_api_key.startswith("sk-ant-REPLACE"):
                log.info("ANTHROPIC_API_KEY not set — using heuristic fallback")
                return {**self._heuristic_fallback(payload), "_model": "heuristic-fallback"}
            try:
                import anthropic  # type: ignore

                self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            except Exception as e:  # noqa: BLE001
                log.warning("anthropic client unavailable, returning stub: %s", e)
                return {**self._heuristic_fallback(payload), "_model": "heuristic-fallback"}

        try:
            user_msg = "Signals for assessment:\n```json\n" + json.dumps(payload, indent=2) + "\n```"
            resp = self._client.messages.create(
                model=model,
                max_tokens=1500,
                system=self._prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = resp.content[0].text  # type: ignore[attr-defined]
            data = json.loads(text)
            data["_model"] = model
            return data
        except Exception as e:  # noqa: BLE001
            log.exception("Claude roll-up failed, falling back: %s", e)
            return {**self._heuristic_fallback(payload), "_model": "heuristic-fallback"}

    @staticmethod
    def _heuristic_fallback(payload: dict) -> dict:
        """Used when Claude is unavailable. Implements the same rubric, conservatively."""
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
            actions.append(
                {"who": "CSM", "action": "Routine check-in", "rationale": "No flags"}
            )

        return {
            "risk_flag": flag,
            "risk_score": score,
            "narrative": narrative,
            "next_best_actions": actions[:3],
        }
