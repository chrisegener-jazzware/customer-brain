"""HubSpot feeder (JAZ-107 + expansion).

Pulls company + tickets + deals + contacts + engagements + quotes + deal stage history.
Maps pipeline/stage labels, computes signals, writes via SQLAlchemy.
Gracefully degrades on 403 (missing scopes — currently quotes + sales-email read).

Usage:
    feeder = HubSpotFeeder()
    feeder.refresh_company("320895019724")            # on-demand
    feeder.refresh_active(days=90)                     # nightly cron
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from ..db import (
    ActivitySignal,
    Company,
    ContactSignal,
    DealSignal,
    QuoteSignal,
    SessionLocal,
    TicketSignal,
)

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


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class HubSpotRateLimitError(Exception):
    pass


class HubSpotMissingScopeError(Exception):
    """403 from HubSpot — handled gracefully (no retries)."""


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
        if r.status_code == 403:
            raise HubSpotMissingScopeError(r.text)
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
        if r.status_code == 403:
            raise HubSpotMissingScopeError(r.text)
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
        try:
            r = self.get(f"/crm/v3/objects/companies/{cid}/associations/{to}")
            return [a["id"] for a in r.get("results", [])]
        except HubSpotMissingScopeError as e:
            log.info("associations companies→%s denied (403): %s", to, str(e)[:160])
            return []

    def deal_associations(self, did: str, to: str) -> list[str]:
        try:
            r = self.get(f"/crm/v3/objects/deals/{did}/associations/{to}")
            return [a["id"] for a in r.get("results", [])]
        except HubSpotMissingScopeError as e:
            log.info("associations deals→%s denied (403): %s", to, str(e)[:160])
            return []

    def ticket(self, tid: str) -> dict:
        props = (
            "subject,content,hs_pipeline_stage,hs_ticket_priority,hs_ticket_category,"
            "createdate,closed_date,hs_lastmodifieddate,hs_resolution,source_type,"
            "hs_num_associated_conversations,hs_first_response_time_minutes,hubspot_owner_id"
        )
        return self.get(f"/crm/v3/objects/tickets/{tid}", params={"properties": props})

    def deal(self, did: str, with_stage_history: bool = True) -> dict:
        props = (
            "dealname,amount,dealstage,pipeline,closedate,createdate,"
            "hubspot_owner_id,hs_deal_stage_probability,hs_lastmodifieddate"
        )
        params: dict[str, Any] = {"properties": props}
        if with_stage_history:
            params["propertiesWithHistory"] = "dealstage"
        return self.get(f"/crm/v3/objects/deals/{did}", params=params)

    def contact(self, cid: str) -> dict | None:
        props = (
            "firstname,lastname,email,jobtitle,phone,"
            "createdate,lastmodifieddate,notes_last_contacted,notes_last_updated,"
            "hs_last_sales_activity_date,hs_last_sales_activity_timestamp"
        )
        try:
            return self.get(f"/crm/v3/objects/contacts/{cid}", params={"properties": props})
        except HubSpotMissingScopeError as e:
            log.info("contact read denied (403): %s", str(e)[:160])
            return None

    def engagement(self, kind: str, eid: str) -> dict | None:
        """kind ∈ {calls, emails, meetings, notes}. Emails currently 403 on prod."""
        prop_map = {
            "calls": "hs_call_title,hs_call_body,hs_call_direction,hs_timestamp,"
            "hubspot_owner_id,hs_call_duration",
            "emails": "hs_email_subject,hs_email_text,hs_email_direction,hs_timestamp,"
            "hubspot_owner_id,hs_email_from_email,hs_email_to_email",
            "meetings": "hs_meeting_title,hs_meeting_body,hs_timestamp,hubspot_owner_id,"
            "hs_meeting_start_time,hs_meeting_end_time",
            "notes": "hs_note_body,hs_timestamp,hubspot_owner_id",
        }
        props = prop_map.get(kind, "")
        try:
            return self.get(f"/crm/v3/objects/{kind}/{eid}", params={"properties": props})
        except HubSpotMissingScopeError as e:
            log.info("engagement %s read denied (403): %s", kind, str(e)[:160])
            return None

    def quote(self, qid: str) -> dict | None:
        props = (
            "hs_title,hs_status,hs_quote_amount,hs_createdate,hs_quote_number,"
            "hs_expiration_date,hs_proposal_template_status,hs_quote_link"
        )
        try:
            return self.get(f"/crm/v3/objects/quotes/{qid}", params={"properties": props})
        except HubSpotMissingScopeError as e:
            log.info("quote read denied (403): %s", str(e)[:160])
            return None

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


# --- Property-extraction helpers -----------------------------------------------

_PROP_BRAND_HINTS = (
    "marina bay sands", "marina-bay-sands", "mgm", "four seasons", "fairmont",
    "ritz-carlton", "ritz carlton", "mandarin oriental", "pan pacific",
    "hilton", "marriott", "shangri-la", "shangri la", "hyatt", "intercontinental",
    "raffles", "westin", "sheraton", "sofitel", "anantara", "regent", "park hyatt",
    "grand hyatt", "novotel", "accor", "andaz", "edition", "rosewood", "capella",
    "banyan tree", "sands", "kempinski", "peninsula",
)

_PROP_STOPWORDS = {
    "the", "&", "and", "of", "for", "to", "in", "at", "on", "—", "-", "–",
    "spare", "devices", "software", "deployment", "deployments", "renewal",
    "renewals", "license", "licenses", "licence", "licences", "upgrade",
    "upgrades", "support", "subscription", "subscriptions", "implementation",
    "expansion", "addition", "additions", "hardware", "addons",
    "extension", "extensions", "annual", "monthly", "quarterly", "yearly",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "q1", "q2", "q3", "q4", "phase", "order", "orders", "po", "file",
    "deploy", "setup", "install", "installs", "setups",
}

# Regex for SKU-like junk tokens (e.g. R5JET-621769)
_SKU_LIKE = re.compile(r"^[A-Z][A-Z0-9]*[-_][A-Z0-9-]+|^[A-Z]+\d+[A-Z]*\S*$")


def extract_properties_from_deal_names(deal_names: list[str]) -> list[dict]:
    """Heuristic: pull recurring property names out of deal titles.

    Tries to find hotel-brand fragments (e.g. "Four Seasons Kyoto", "Pan Pacific")
    and recurring tokens. Returns a list of:
        {"name": "...", "deal_count": N, "deal_ids_sample": [...]}.

    Pure / no network → easy to test.
    """
    matches: dict[str, list[str]] = {}

    for raw in deal_names:
        if not raw:
            continue
        original = raw
        low = raw.lower()

        # 1) Known hospitality brand hints (longest match wins)
        sorted_hints = sorted(_PROP_BRAND_HINTS, key=len, reverse=True)
        hit = None
        for hint in sorted_hints:
            idx = low.find(hint)
            if idx >= 0:
                end = idx + len(hint)
                base = original[idx:end]
                # Walk forward only over likely place tokens (title-case, non-stopword,
                # non-year, non-SKU). Stops on lowercase, stopwords, or numbers.
                fragment_after = original[end:].lstrip(" -—·|/")
                tokens_after = fragment_after.split()
                extra: list[str] = []
                for tk in tokens_after[:4]:
                    if tk.lower() in _PROP_STOPWORDS:
                        break
                    if re.fullmatch(r"\d{2,4}", tk):
                        break
                    if _SKU_LIKE.match(tk):
                        break
                    if tk[:1].isalpha() and not tk[:1].isupper():
                        break
                    extra.append(tk)
                tokens = base.split() + extra
                # Trim trailing stopwords / years just in case
                while tokens and (
                    tokens[-1].lower() in _PROP_STOPWORDS
                    or re.fullmatch(r"\d{2,4}", tokens[-1])
                ):
                    tokens.pop()
                if tokens:
                    hit = " ".join(tokens).strip().title()
                break

        # 2) If no brand-hint, take the segment after the first " - " separator
        if not hit:
            parts = re.split(r"\s+[-–—]\s+", original, maxsplit=2)
            if len(parts) >= 2:
                seg = parts[1]
                tokens = seg.split()
                # take up to 4 tokens excluding stopwords/years
                kept: list[str] = []
                for tk in tokens:
                    if tk.lower() in _PROP_STOPWORDS:
                        break
                    if re.fullmatch(r"\d{2,4}", tk):
                        break
                    kept.append(tk)
                    if len(kept) >= 5:
                        break
                if kept:
                    hit = " ".join(kept).title()

        if hit and len(hit) >= 3:
            matches.setdefault(hit, []).append(original)

    out = [
        {"name": name, "deal_count": len(names), "deal_names_sample": names[:3]}
        for name, names in matches.items()
    ]
    out.sort(key=lambda x: -x["deal_count"])
    return out


# --- Feeder ---------------------------------------------------------------------


@dataclass
class RefreshResult:
    company_id: str
    name: str | None
    tickets: int
    deals: int
    contacts: int
    activities: int
    quotes: int
    stalled_deals: int
    open_tickets: int


class HubSpotFeeder:
    """Pull HubSpot signals into Postgres (expanded)."""

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
            contact_ids = self.client.company_associations(company_id, "contacts")

            n_open_t = 0
            for tid in ticket_ids[:300]:
                t = self.client.ticket(tid)
                is_open = self._upsert_ticket(s, company.id, t)
                if is_open:
                    n_open_t += 1

            n_stalled = 0
            deal_to_quote_ids: list[tuple[str, str]] = []
            for did in deal_ids[:300]:
                d = self.client.deal(did)
                stalled = self._upsert_deal(s, company.id, d)
                if stalled:
                    n_stalled += 1
                # Quote associations (gracefully skipped on 403)
                qids = self.client.deal_associations(did, "quotes")
                for qid in qids[:50]:
                    deal_to_quote_ids.append((did, qid))

            # Contacts
            n_contacts = 0
            for ctid in contact_ids[:200]:
                c_obj = self.client.contact(ctid)
                if c_obj:
                    self._upsert_contact(s, company.id, c_obj)
                    n_contacts += 1

            # Activities (calls + meetings + notes; emails 403 in current scope)
            n_activities = 0
            for kind in ("calls", "meetings", "notes", "emails"):
                eids = self.client.company_associations(company_id, kind)
                for eid in eids[:200]:
                    eng = self.client.engagement(kind, eid)
                    if eng:
                        self._upsert_activity(s, company.id, kind, eng)
                        n_activities += 1

            # Quotes (may all return None if scope denied)
            n_quotes = 0
            seen_quotes: set[str] = set()
            for did, qid in deal_to_quote_ids:
                if qid in seen_quotes:
                    continue
                seen_quotes.add(qid)
                q = self.client.quote(qid)
                if q:
                    self._upsert_quote(s, company.id, did, q)
                    n_quotes += 1

            s.flush()
            company.last_refreshed = datetime.now(UTC)
            company.risk_score = self._compute_risk_score(s, company.id)
            self._compute_metrics(s, company)
            s.commit()

            return RefreshResult(
                company_id=company.id,
                name=company.name,
                tickets=len(ticket_ids),
                deals=len(deal_ids),
                contacts=n_contacts,
                activities=n_activities,
                quotes=n_quotes,
                stalled_deals=n_stalled,
                open_tickets=n_open_t,
            )

    def refresh_active(self, days: int | None = None) -> list[RefreshResult]:
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
        ts.hubspot_owner_id = tp.get("hubspot_owner_id")
        ts.hs_created_at = _parse_dt(tp.get("createdate"))
        ts.hs_closed_at = _parse_dt(tp.get("closed_date"))
        ts.hs_last_modified = _parse_dt(tp.get("hs_lastmodifieddate"))
        ts.is_open = ts.hs_closed_at is None
        ts.reply_count = _to_int(tp.get("hs_num_associated_conversations"))
        ts.first_response_minutes = _to_float(tp.get("hs_first_response_time_minutes"))
        now = datetime.now(UTC)
        if ts.hs_created_at:
            ref = ts.hs_closed_at or now
            ts.age_days = (now - ts.hs_created_at).total_seconds() / 86400
            if ts.hs_closed_at:
                ts.resolution_days = (ref - ts.hs_created_at).total_seconds() / 86400

        # Simple subject-prefix cluster id (first 5 normalized words). Stable for repeat detection.
        if ts.subject:
            norm = re.sub(r"^(re|fwd?):\s*", "", ts.subject.lower()).strip()
            norm = re.sub(r"[^a-z0-9 ]+", " ", norm)
            prefix = " ".join(norm.split()[:5])
            ts.cluster_id = hashlib.md5(prefix.encode()).hexdigest()[:16] if prefix else None
        return ts.is_open

    def _upsert_deal(self, s: Session, company_id: str, d: dict) -> bool:
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
        ds.hubspot_owner_id = dp.get("hubspot_owner_id")

        # Stage history: HubSpot returns newest→oldest in propertiesWithHistory.dealstage.
        history = d.get("propertiesWithHistory", {}).get("dealstage") or []
        if history:
            # Reverse to chronological order, compute days_at_stage between transitions.
            chrono = list(reversed(history))
            stage_history: list[dict] = []
            for i, entry in enumerate(chrono):
                entered_at = _parse_dt(entry.get("timestamp"))
                stage_id = entry.get("value")
                stage_meta = self.deal_stages.get(stage_id or "", {})
                if i < len(chrono) - 1:
                    exit_at = _parse_dt(chrono[i + 1].get("timestamp"))
                else:
                    exit_at = ds.hs_closed_at or _utcnow()
                days_at_stage = None
                if entered_at and exit_at:
                    delta = _as_utc(exit_at) - _as_utc(entered_at)
                    days_at_stage = max(round(delta.total_seconds() / 86400, 2), 0.0)
                stage_history.append(
                    {
                        "stage_id": stage_id,
                        "stage_label": stage_meta.get("label"),
                        "entered_at": entered_at.isoformat() if entered_at else None,
                        "days_at_stage": days_at_stage,
                    }
                )
            ds.stage_history_json = stage_history
            # If we have stage history, override days_in_stage with last-entered
            if stage_history:
                last_entered = _parse_dt(stage_history[-1].get("entered_at"))
                if last_entered:
                    ds.days_in_stage = (
                        _utcnow() - _as_utc(last_entered)
                    ).total_seconds() / 86400

        # stalled = open AND last_activity > 30d
        ds.stalled = False
        if ds.is_open and ds.last_activity:
            days = (datetime.now(UTC) - _as_utc(ds.last_activity)).days
            if ds.days_in_stage is None:
                ds.days_in_stage = float(days)
            ds.stalled = days > 30
        return ds.stalled

    def _upsert_contact(self, s: Session, company_id: str, c_obj: dict) -> None:
        cp = c_obj.get("properties", {}) or {}
        cid = c_obj["id"]
        row = s.get(ContactSignal, cid)
        if row is None:
            row = ContactSignal(id=cid, company_id=company_id)
            s.add(row)
        row.company_id = company_id
        row.first_name = cp.get("firstname")
        row.last_name = cp.get("lastname")
        row.email = cp.get("email")
        row.phone = cp.get("phone")
        row.job_title = cp.get("jobtitle")
        row.last_contacted_at = _parse_dt(cp.get("notes_last_contacted"))
        row.last_activity_at = _parse_dt(
            cp.get("hs_last_sales_activity_date")
            or cp.get("notes_last_updated")
            or cp.get("lastmodifieddate")
        )
        row.hs_created_at = _parse_dt(cp.get("createdate"))
        if row.last_activity_at:
            row.days_since_activity = (
                _utcnow() - _as_utc(row.last_activity_at)
            ).total_seconds() / 86400

    def _upsert_activity(self, s: Session, company_id: str, kind: str, e: dict) -> None:
        ep = e.get("properties", {}) or {}
        eid = e["id"]
        row = s.get(ActivitySignal, eid)
        if row is None:
            row = ActivitySignal(id=eid, company_id=company_id, kind=kind)
            s.add(row)
        row.company_id = company_id
        row.kind = kind.rstrip("s")  # "calls" → "call"
        row.owner_id = ep.get("hubspot_owner_id")
        if kind == "calls":
            row.subject = (ep.get("hs_call_title") or "")[:500] or None
            row.content_preview = (ep.get("hs_call_body") or "")[:2000] or None
            row.direction = ep.get("hs_call_direction")
            row.ts = _parse_dt(ep.get("hs_timestamp"))
        elif kind == "emails":
            row.subject = (ep.get("hs_email_subject") or "")[:500] or None
            row.content_preview = (ep.get("hs_email_text") or "")[:2000] or None
            row.direction = ep.get("hs_email_direction")
            row.ts = _parse_dt(ep.get("hs_timestamp"))
        elif kind == "meetings":
            row.subject = (ep.get("hs_meeting_title") or "")[:500] or None
            row.content_preview = (ep.get("hs_meeting_body") or "")[:2000] or None
            row.ts = _parse_dt(ep.get("hs_timestamp") or ep.get("hs_meeting_start_time"))
        elif kind == "notes":
            body = ep.get("hs_note_body") or ""
            # strip basic HTML
            txt = re.sub(r"<[^>]+>", " ", body)
            row.subject = (txt[:120] + "…") if len(txt) > 120 else (txt or None)
            row.content_preview = txt[:2000] or None
            row.ts = _parse_dt(ep.get("hs_timestamp"))

    def _upsert_quote(self, s: Session, company_id: str, deal_id: str, q: dict) -> None:
        qp = q.get("properties", {}) or {}
        qid = q["id"]
        row = s.get(QuoteSignal, qid)
        if row is None:
            row = QuoteSignal(id=qid, company_id=company_id)
            s.add(row)
        row.company_id = company_id
        row.deal_id = deal_id
        row.title = (qp.get("hs_title") or qp.get("hs_quote_number") or "")[:500] or None
        row.amount = _to_float(qp.get("hs_quote_amount"))
        row.status = qp.get("hs_status")
        row.hs_created_at = _parse_dt(qp.get("hs_createdate"))
        # heuristic: HubSpot doesn't expose revision_count directly → set None for now
        if row.signed_at and row.hs_created_at:
            row.days_to_sign = (
                _as_utc(row.signed_at) - _as_utc(row.hs_created_at)
            ).total_seconds() / 86400

    # --- risk ----------------------------------------------------------------

    @staticmethod
    def _compute_risk_score(s: Session, company_id: str) -> float:
        score = 0.0
        open_tickets = s.scalars(
            select(TicketSignal).where(
                TicketSignal.company_id == company_id, TicketSignal.is_open.is_(True)
            )
        ).all()
        score += min(len(open_tickets) * 5, 30)
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

    # --- per-company metrics --------------------------------------------------

    @staticmethod
    def _compute_metrics(s: Session, company: Company) -> None:
        now = _utcnow()
        d90 = now - timedelta(days=90)
        d30 = now - timedelta(days=30)

        deals = s.scalars(
            select(DealSignal).where(DealSignal.company_id == company.id)
        ).all()
        tickets = s.scalars(
            select(TicketSignal).where(TicketSignal.company_id == company.id)
        ).all()
        activities = s.scalars(
            select(ActivitySignal).where(ActivitySignal.company_id == company.id)
        ).all()

        open_deals = [d for d in deals if d.is_open]
        won_deals = [d for d in deals if d.is_won]
        lost_deals = [d for d in deals if d.is_lost]
        won_90d = [
            d for d in won_deals if d.hs_closed_at and _as_utc(d.hs_closed_at) >= d90
        ]
        lost_90d = [
            d for d in lost_deals if d.hs_closed_at and _as_utc(d.hs_closed_at) >= d90
        ]

        company.open_pipeline_amount = sum((d.amount or 0) for d in open_deals)
        company.won_amount_90d = sum((d.amount or 0) for d in won_90d)
        company.lost_amount_90d = sum((d.amount or 0) for d in lost_90d)
        if won_90d or lost_90d:
            company.win_rate_90d = (
                len(won_90d) / max(len(won_90d) + len(lost_90d), 1)
            )
        else:
            company.win_rate_90d = None

        # avg cycle days for won (createdate → closedate)
        cycle_days = []
        for d in won_deals:
            if d.hs_created_at and d.hs_closed_at:
                cd = (
                    _as_utc(d.hs_closed_at) - _as_utc(d.hs_created_at)
                ).total_seconds() / 86400
                if cd > 0:
                    cycle_days.append(cd)
        company.avg_cycle_days_won = (
            sum(cycle_days) / len(cycle_days) if cycle_days else None
        )

        # stuck deals: same stage > 60d (heuristic — was: avg×2 but we lack a per-stage avg)
        company.stuck_deals_count = sum(
            1 for d in open_deals if (d.days_in_stage or 0) > 60
        )

        # support load 30d (created in last 30d)
        company.support_load_30d = sum(
            1 for t in tickets if t.hs_created_at and _as_utc(t.hs_created_at) >= d30
        )

        # first response avg (hours) on tickets where data exists
        frs = [t.first_response_minutes for t in tickets if t.first_response_minutes]
        company.first_response_avg_hours = (
            (sum(frs) / len(frs)) / 60 if frs else None
        )

        # repeat issue count: clusters with >=2 tickets in last 30d
        from collections import Counter

        cl = Counter(
            t.cluster_id
            for t in tickets
            if t.cluster_id and t.hs_created_at and _as_utc(t.hs_created_at) >= d30
        )
        company.repeat_issue_count = sum(1 for _, n in cl.items() if n >= 2)

        # last_human_activity_at: latest activity ts OR ticket/deal last modified
        candidates: list[datetime] = []
        for a in activities:
            if a.ts:
                candidates.append(_as_utc(a.ts))
        for t in tickets:
            if t.hs_last_modified:
                candidates.append(_as_utc(t.hs_last_modified))
        for d in deals:
            if d.last_activity:
                candidates.append(_as_utc(d.last_activity))
        if candidates:
            company.last_human_activity_at = max(candidates)
            company.days_since_last_activity = (
                now - company.last_human_activity_at
            ).total_seconds() / 86400
        else:
            company.last_human_activity_at = None
            company.days_since_last_activity = None


def iter_company_ids(seed: Iterable[str]) -> Iterable[str]:
    for c in seed:
        c = str(c).strip()
        if c:
            yield c
