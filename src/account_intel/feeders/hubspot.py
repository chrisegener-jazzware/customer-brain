"""HubSpot feeder (JAZ-107).

Ported from /tmp/account_intel_full.py PoC. Pulls company + tickets + deals,
maps pipeline/stage labels, computes signals, writes via SQLAlchemy.

Usage:
    feeder = HubSpotFeeder()
    feeder.refresh_company("320895019724")            # on-demand
    feeder.refresh_active(days=90)                     # nightly cron
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from ..db import Company, DealSignal, SessionLocal, TicketSignal

log = logging.getLogger(__name__)

HS_BASE = "https://api.hubapi.com"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class HubSpotRateLimitError(Exception):
    pass


class HubSpotClient:
    """Thin HubSpot v3 wrapper. Retries on 429/5xx with exponential backoff."""

    def __init__(self, token: str | None = None, timeout: float = 30.0):
        self.token = token or settings.hubspot_token
        self._client = httpx.Client(
            base_url=HS_BASE,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception_type((HubSpotRateLimitError, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def get(self, path: str, params: dict | None = None) -> dict:
        r = self._client.get(path, params=params)
        if r.status_code == 429:
            raise HubSpotRateLimitError(r.text)
        if r.status_code >= 500:
            raise HubSpotRateLimitError(f"server {r.status_code}: {r.text}")
        r.raise_for_status()
        return r.json()

    @retry(
        retry=retry_if_exception_type((HubSpotRateLimitError, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def post(self, path: str, body: dict) -> dict:
        r = self._client.post(path, json=body)
        if r.status_code == 429:
            raise HubSpotRateLimitError(r.text)
        if r.status_code >= 500:
            raise HubSpotRateLimitError(f"server {r.status_code}: {r.text}")
        r.raise_for_status()
        return r.json()

    # --- pipeline maps -------------------------------------------------------

    def deal_stage_map(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for p in self.get("/crm/v3/pipelines/deals").get("results", []):
            for s in p.get("stages", []):
                md = s.get("metadata", {}) or {}
                out[s["id"]] = {
                    "label": s["label"],
                    "pipeline": p["label"],
                    "won": md.get("isClosed") == "true" and md.get("probability") == "1.0",
                    "closed": md.get("isClosed") == "true",
                    "probability": _to_float(md.get("probability")),
                }
        return out

    def ticket_stage_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in self.get("/crm/v3/pipelines/tickets").get("results", []):
            for s in p.get("stages", []):
                out[s["id"]] = s["label"]
        return out

    # --- objects -------------------------------------------------------------

    def company(self, cid: str) -> dict:
        props = (
            "name,domain,industry,country,city,lifecyclestage,createdate,"
            "hubspot_owner_id,annualrevenue,numberofemployees"
        )
        return self.get(f"/crm/v3/objects/companies/{cid}", params={"properties": props})

    def company_associations(self, cid: str, to: str) -> list[str]:
        r = self.get(f"/crm/v3/objects/companies/{cid}/associations/{to}")
        return [a["id"] for a in r.get("results", [])]

    def ticket(self, tid: str) -> dict:
        props = (
            "subject,content,hs_pipeline_stage,hs_ticket_priority,hs_ticket_category,"
            "createdate,closed_date,hs_lastmodifieddate,hs_resolution,source_type"
        )
        return self.get(f"/crm/v3/objects/tickets/{tid}", params={"properties": props})

    def deal(self, did: str) -> dict:
        props = (
            "dealname,amount,dealstage,pipeline,closedate,createdate,"
            "hubspot_owner_id,hs_deal_stage_probability,hs_lastmodifieddate"
        )
        return self.get(f"/crm/v3/objects/deals/{did}", params={"properties": props})

    def search_companies_with_activity(self, days: int = 90, limit: int = 100) -> list[dict]:
        """Companies whose lastmodifieddate is within the window. Paginated."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict] = []
        after: str | None = None
        while True:
            body = {
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": cutoff}
                        ]
                    }
                ],
                "properties": ["name", "domain"],
                "limit": min(limit, 100),
            }
            if after:
                body["after"] = after
            r = self.post("/crm/v3/objects/companies/search", body)
            out.extend(r.get("results", []))
            after = (r.get("paging") or {}).get("next", {}).get("after")
            if not after:
                break
        return out


# --- Feeder ---------------------------------------------------------------------


@dataclass
class RefreshResult:
    company_id: str
    name: str | None
    tickets: int
    deals: int
    stalled_deals: int
    open_tickets: int


class HubSpotFeeder:
    """Pull HubSpot signals into Postgres."""

    def __init__(self, client: HubSpotClient | None = None, session_factory=SessionLocal):
        self.client = client or HubSpotClient()
        self.session_factory = session_factory
        self._deal_stages: dict[str, dict] | None = None
        self._ticket_stages: dict[str, str] | None = None

    # --- stage map cache (per-feeder lifetime) -------------------------------

    @property
    def deal_stages(self) -> dict[str, dict]:
        if self._deal_stages is None:
            self._deal_stages = self.client.deal_stage_map()
        return self._deal_stages

    @property
    def ticket_stages(self) -> dict[str, str]:
        if self._ticket_stages is None:
            self._ticket_stages = self.client.ticket_stage_map()
        return self._ticket_stages

    # --- public --------------------------------------------------------------

    def is_fresh(self, company_id: str, ttl_seconds: int | None = None) -> bool:
        ttl = ttl_seconds if ttl_seconds is not None else settings.feeder_fresh_ttl_seconds
        with self.session_factory() as s:
            c = s.get(Company, company_id)
            if not c or not c.last_refreshed:
                return False
            age = datetime.now(UTC) - c.last_refreshed.replace(tzinfo=UTC)
            return age.total_seconds() < ttl

    def refresh_company(self, company_id: str) -> RefreshResult:
        co = self.client.company(company_id)
        with self.session_factory() as s:
            company = self._upsert_company(s, co)
            ticket_ids = self.client.company_associations(company_id, "tickets")
            deal_ids = self.client.company_associations(company_id, "deals")

            n_open_t = 0
            for tid in ticket_ids[:200]:
                t = self.client.ticket(tid)
                is_open = self._upsert_ticket(s, company.id, t)
                if is_open:
                    n_open_t += 1

            n_stalled = 0
            for did in deal_ids[:300]:
                d = self.client.deal(did)
                stalled = self._upsert_deal(s, company.id, d)
                if stalled:
                    n_stalled += 1

            s.flush()
            company.last_refreshed = datetime.now(UTC)
            company.risk_score = self._compute_risk_score(s, company.id)
            s.commit()

            return RefreshResult(
                company_id=company.id,
                name=company.name,
                tickets=len(ticket_ids),
                deals=len(deal_ids),
                stalled_deals=n_stalled,
                open_tickets=n_open_t,
            )

    def refresh_active(self, days: int | None = None) -> list[RefreshResult]:
        """Nightly cron entrypoint: refresh every company touched in last N days."""
        days = days if days is not None else settings.feeder_activity_window_days
        results: list[RefreshResult] = []
        for c in self.client.search_companies_with_activity(days=days):
            try:
                results.append(self.refresh_company(c["id"]))
            except Exception as e:  # noqa: BLE001
                log.exception("refresh_company failed for %s: %s", c.get("id"), e)
        return results

    # --- upserts -------------------------------------------------------------

    def _upsert_company(self, s: Session, co: dict) -> Company:
        cp = co.get("properties", {}) or {}
        cid = co["id"]
        company = s.get(Company, cid)
        if company is None:
            company = Company(id=cid)
            s.add(company)
        company.name = cp.get("name")
        company.domain = cp.get("domain")
        company.industry = cp.get("industry")
        company.country = cp.get("country")
        company.city = cp.get("city")
        company.lifecycle_stage = cp.get("lifecyclestage")
        company.hubspot_owner_id = cp.get("hubspot_owner_id")
        company.annual_revenue = _to_float(cp.get("annualrevenue"))
        try:
            company.employees = int(cp.get("numberofemployees")) if cp.get("numberofemployees") else None
        except (TypeError, ValueError):
            company.employees = None
        company.hs_created_at = _parse_dt(cp.get("createdate"))
        return company

    def _upsert_ticket(self, s: Session, company_id: str, t: dict) -> bool:
        """Returns True if open."""
        tp = t.get("properties", {}) or {}
        tid = t["id"]
        ts = s.get(TicketSignal, tid)
        if ts is None:
            ts = TicketSignal(id=tid, company_id=company_id)
            s.add(ts)
        ts.company_id = company_id
        ts.subject = (tp.get("subject") or "")[:500] or None
        content = tp.get("content") or ""
        ts.content_excerpt = content[:2000] if content else None
        ts.pipeline_stage = self.ticket_stages.get(tp.get("hs_pipeline_stage", ""))
        ts.priority = tp.get("hs_ticket_priority")
        ts.category = tp.get("hs_ticket_category")
        ts.source_type = tp.get("source_type")
        ts.hs_created_at = _parse_dt(tp.get("createdate"))
        ts.hs_closed_at = _parse_dt(tp.get("closed_date"))
        ts.hs_last_modified = _parse_dt(tp.get("hs_lastmodifieddate"))
        ts.is_open = ts.hs_closed_at is None
        now = datetime.now(UTC)
        if ts.hs_created_at:
            ref = ts.hs_closed_at or now
            ts.age_days = (now - ts.hs_created_at).total_seconds() / 86400
            if ts.hs_closed_at:
                ts.resolution_days = (ref - ts.hs_created_at).total_seconds() / 86400
        return ts.is_open

    def _upsert_deal(self, s: Session, company_id: str, d: dict) -> bool:
        """Returns True if stalled (open and >30d no activity)."""
        dp = d.get("properties", {}) or {}
        did = d["id"]
        ds = s.get(DealSignal, did)
        if ds is None:
            ds = DealSignal(id=did, company_id=company_id)
            s.add(ds)
        ds.company_id = company_id
        ds.name = (dp.get("dealname") or "")[:500] or None
        ds.amount = _to_float(dp.get("amount"))
        ds.stage_id = dp.get("dealstage")
        sm = self.deal_stages.get(dp.get("dealstage", ""))
        if sm:
            ds.pipeline = sm["pipeline"]
            ds.stage = sm["label"]
            ds.is_won = sm["won"]
            ds.is_lost = sm["closed"] and not sm["won"]
            ds.is_open = not sm["closed"]
        ds.probability = _to_float(dp.get("hs_deal_stage_probability"))
        ds.hs_created_at = _parse_dt(dp.get("createdate"))
        ds.hs_closed_at = _parse_dt(dp.get("closedate"))
        ds.last_activity = _parse_dt(dp.get("hs_lastmodifieddate"))

        # stalled = open AND last_activity > 30d
        ds.stalled = False
        if ds.is_open and ds.last_activity:
            days = (datetime.now(UTC) - ds.last_activity).days
            ds.days_in_stage = float(days)
            ds.stalled = days > 30
        return ds.stalled

    # --- risk ----------------------------------------------------------------

    @staticmethod
    def _compute_risk_score(s: Session, company_id: str) -> float:
        """Simple heuristic; Claude roll-up provides nuance + narrative."""
        score = 0.0
        open_tickets = s.scalars(
            select(TicketSignal).where(
                TicketSignal.company_id == company_id, TicketSignal.is_open.is_(True)
            )
        ).all()
        score += min(len(open_tickets) * 5, 30)
        # Aging open tickets
        for t in open_tickets:
            if t.age_days and t.age_days > 30:
                score += 5
        stalled = s.scalars(
            select(DealSignal).where(
                DealSignal.company_id == company_id, DealSignal.stalled.is_(True)
            )
        ).all()
        score += min(len(stalled) * 8, 40)
        return min(score, 100.0)


def iter_company_ids(seed: Iterable[str]) -> Iterable[str]:
    """Yield company ids (helper for scripts/tests)."""
    for c in seed:
        c = str(c).strip()
        if c:
            yield c
