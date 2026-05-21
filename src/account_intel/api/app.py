"""FastAPI service: GET /account/{company_id} + expanded endpoints."""
from __future__ import annotations

import logging
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ..db import (
    ActivitySignal,
    AIAssessment,
    Company,
    ContactSignal,
    DealSignal,
    IntegrationSignal,
    QuoteSignal,
    TicketSignal,
    get_session,
)
from ..feeders import HubSpotFeeder, extract_properties_from_deal_names
from ..rollup import RollupService

log = logging.getLogger(__name__)

app = FastAPI(title="Jazzware Account Intel", version="0.2.0")


# --- DTOs ---------------------------------------------------------------------


class CompanySearchHit(BaseModel):
    id: str
    name: str | None
    domain: str | None
    risk_score: float | None
    last_refreshed: str | None


class TicketDTO(BaseModel):
    id: str
    subject: str | None
    stage: str | None
    priority: str | None
    is_open: bool
    age_days: float | None
    resolution_days: float | None
    reply_count: int | None = None
    first_response_minutes: float | None = None
    hubspot_url: str


class DealDTO(BaseModel):
    id: str
    name: str | None
    amount: float | None
    pipeline: str | None
    stage: str | None
    is_open: bool
    is_won: bool
    stalled: bool
    days_in_stage: float | None
    stage_history: list[dict] | None = None
    hubspot_url: str


class IntegrationDTO(BaseModel):
    name: str
    uptime_pct_30d: float | None
    last_sync: str | None
    error_count_24h: int | None
    status: str | None


class AssessmentDTO(BaseModel):
    risk_flag: str
    risk_score: float | None
    narrative: str
    next_best_actions: list[dict]
    generated_at: str
    model: str | None
    summaries: dict | None = None


class ContactDTO(BaseModel):
    id: str
    name: str | None
    email: str | None
    job_title: str | None
    phone: str | None
    last_activity_at: str | None
    days_since_activity: float | None


class ActivityDTO(BaseModel):
    id: str
    kind: str
    subject: str | None
    direction: str | None
    ts: str | None
    content_preview: str | None = None


class QuoteDTO(BaseModel):
    id: str
    deal_id: str | None
    title: str | None
    amount: float | None
    status: str | None
    created: str | None
    days_to_sign: float | None


class MetricsDTO(BaseModel):
    open_pipeline_amount: float | None
    won_amount_90d: float | None
    lost_amount_90d: float | None
    avg_cycle_days_won: float | None
    win_rate_90d: float | None
    stuck_deals_count: int | None
    support_load_30d: int | None
    first_response_avg_hours: float | None
    repeat_issue_count: int | None
    last_human_activity_at: str | None
    days_since_last_activity: float | None


class HotSignalDTO(BaseModel):
    kind: str  # stalled_deal | repeat_issue | quiet_contact | old_quote | aged_ticket | integration_red
    severity: str  # high | medium | low
    label: str
    detail: str | None = None
    object_id: str | None = None
    hubspot_url: str | None = None


class PropertyDTO(BaseModel):
    name: str
    deal_count: int
    deal_names_sample: list[str]


class AccountView(BaseModel):
    company: dict
    tickets: list[TicketDTO]
    deals: list[DealDTO]
    integrations: list[IntegrationDTO]
    assessment: AssessmentDTO | None


# --- helpers ------------------------------------------------------------------


def _ticket_url(tid: str) -> str:
    return f"https://app.hubspot.com/contacts/_/record/0-5/{tid}"


def _deal_url(did: str) -> str:
    return f"https://app.hubspot.com/contacts/_/record/0-3/{did}"


def _contact_url(cid: str) -> str:
    return f"https://app.hubspot.com/contacts/_/record/0-1/{cid}"


def _quote_url(qid: str) -> str:
    return f"https://app.hubspot.com/contacts/_/record/0-14/{qid}"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- routes -------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "account-intel", "version": app.version}


@app.get("/companies/search", response_model=list[CompanySearchHit])
def search_companies(
    q: str = Query(..., min_length=1, description="Company name fragment"),
    limit: int = 20,
    s: Session = Depends(get_session),
) -> list[CompanySearchHit]:
    pattern = f"%{q.lower()}%"
    rows = s.scalars(
        select(Company)
        .where(or_(func.lower(Company.name).like(pattern), func.lower(Company.domain).like(pattern)))
        .order_by(Company.name)
        .limit(limit)
    ).all()
    return [
        CompanySearchHit(
            id=r.id,
            name=r.name,
            domain=r.domain,
            risk_score=r.risk_score,
            last_refreshed=r.last_refreshed.isoformat() if r.last_refreshed else None,
        )
        for r in rows
    ]


@app.get("/companies/list", response_model=list[CompanySearchHit])
def list_companies(
    limit: int = 500,
    s: Session = Depends(get_session),
) -> list[CompanySearchHit]:
    rows = s.scalars(
        select(Company).order_by(desc(Company.risk_score), Company.name).limit(limit)
    ).all()
    return [
        CompanySearchHit(
            id=r.id,
            name=r.name,
            domain=r.domain,
            risk_score=r.risk_score,
            last_refreshed=r.last_refreshed.isoformat() if r.last_refreshed else None,
        )
        for r in rows
    ]


def _shared_session_factory(s: Session):
    @contextmanager
    def _factory():
        yield s

    return _factory


@app.get("/account/{company_id}", response_model=AccountView)
def get_account(
    company_id: str,
    refresh: bool = Query(False, description="Force HubSpot refresh before reading"),
    s: Session = Depends(get_session),
) -> AccountView:
    if refresh:
        try:
            HubSpotFeeder().refresh_company(company_id)
        except Exception as e:  # noqa: BLE001
            log.exception("refresh failed: %s", e)
            raise HTTPException(502, f"HubSpot refresh failed: {e}") from e

    c = s.get(Company, company_id)
    if c is None:
        raise HTTPException(404, f"company {company_id} not in local store; pass refresh=true to fetch")

    tickets = s.scalars(
        select(TicketSignal)
        .where(TicketSignal.company_id == company_id)
        .order_by(desc(TicketSignal.hs_created_at))
    ).all()
    deals = s.scalars(
        select(DealSignal)
        .where(DealSignal.company_id == company_id)
        .order_by(desc(DealSignal.hs_created_at))
    ).all()
    integrations = s.scalars(
        select(IntegrationSignal).where(IntegrationSignal.company_id == company_id)
    ).all()

    try:
        assessment_row = RollupService(
            session_factory=_shared_session_factory(s)
        ).get_or_create(company_id, force=refresh)
        assessment = AssessmentDTO(
            risk_flag=assessment_row.risk_flag,
            risk_score=assessment_row.risk_score,
            narrative=assessment_row.narrative,
            next_best_actions=assessment_row.next_best_actions or [],
            generated_at=assessment_row.generated_at.isoformat(),
            model=assessment_row.model,
            summaries=assessment_row.summaries_json,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("rollup failed: %s", e)
        assessment = None

    return AccountView(
        company={
            "id": c.id,
            "name": c.name,
            "domain": c.domain,
            "industry": c.industry,
            "country": c.country,
            "city": c.city,
            "lifecycle_stage": c.lifecycle_stage,
            "annual_revenue": c.annual_revenue,
            "employees": c.employees,
            "risk_score": c.risk_score,
            "last_refreshed": c.last_refreshed.isoformat() if c.last_refreshed else None,
            "hubspot_owner_id": c.hubspot_owner_id,
            "hubspot_url": f"https://app.hubspot.com/contacts/_/record/0-2/{c.id}",
        },
        tickets=[
            TicketDTO(
                id=t.id,
                subject=t.subject,
                stage=t.pipeline_stage,
                priority=t.priority,
                is_open=t.is_open,
                age_days=t.age_days,
                resolution_days=t.resolution_days,
                reply_count=t.reply_count,
                first_response_minutes=t.first_response_minutes,
                hubspot_url=_ticket_url(t.id),
            )
            for t in tickets
        ],
        deals=[
            DealDTO(
                id=d.id,
                name=d.name,
                amount=d.amount,
                pipeline=d.pipeline,
                stage=d.stage,
                is_open=d.is_open,
                is_won=d.is_won,
                stalled=d.stalled,
                days_in_stage=d.days_in_stage,
                stage_history=d.stage_history_json,
                hubspot_url=_deal_url(d.id),
            )
            for d in deals
        ],
        integrations=[
            IntegrationDTO(
                name=i.integration_name,
                uptime_pct_30d=i.uptime_pct_30d,
                last_sync=i.last_sync.isoformat() if i.last_sync else None,
                error_count_24h=i.error_count_24h,
                status=i.status,
            )
            for i in integrations
        ],
        assessment=assessment,
    )


@app.post("/account/{company_id}/refresh")
def refresh_account(company_id: str) -> dict:
    try:
        result = HubSpotFeeder().refresh_company(company_id)
        return {
            "company_id": result.company_id,
            "name": result.name,
            "tickets": result.tickets,
            "deals": result.deals,
            "contacts": result.contacts,
            "activities": result.activities,
            "quotes": result.quotes,
            "open_tickets": result.open_tickets,
            "stalled_deals": result.stalled_deals,
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"refresh failed: {e}") from e


# --- NEW expanded endpoints -------------------------------------------------


@app.get("/account/{company_id}/contacts", response_model=list[ContactDTO])
def get_contacts(company_id: str, s: Session = Depends(get_session)) -> list[ContactDTO]:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    rows = s.scalars(
        select(ContactSignal)
        .where(ContactSignal.company_id == company_id)
        .order_by(desc(ContactSignal.last_activity_at))
    ).all()
    out = []
    for c in rows:
        full_name = " ".join(filter(None, [c.first_name, c.last_name])).strip() or c.email
        out.append(
            ContactDTO(
                id=c.id,
                name=full_name,
                email=c.email,
                job_title=c.job_title,
                phone=c.phone,
                last_activity_at=_iso(c.last_activity_at),
                days_since_activity=(
                    round(c.days_since_activity, 1) if c.days_since_activity else None
                ),
            )
        )
    return out


@app.get("/account/{company_id}/activities", response_model=list[ActivityDTO])
def get_activities(
    company_id: str,
    days: int = Query(90, ge=1, le=365),
    s: Session = Depends(get_session),
) -> list[ActivityDTO]:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = s.scalars(
        select(ActivitySignal)
        .where(
            ActivitySignal.company_id == company_id,
            or_(ActivitySignal.ts.is_(None), ActivitySignal.ts >= cutoff),
        )
        .order_by(desc(ActivitySignal.ts))
    ).all()
    return [
        ActivityDTO(
            id=a.id,
            kind=a.kind,
            subject=a.subject,
            direction=a.direction,
            ts=_iso(a.ts),
            content_preview=(a.content_preview or "")[:400] or None,
        )
        for a in rows
    ]


@app.get("/account/{company_id}/quotes", response_model=list[QuoteDTO])
def get_quotes(company_id: str, s: Session = Depends(get_session)) -> list[QuoteDTO]:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    rows = s.scalars(
        select(QuoteSignal)
        .where(QuoteSignal.company_id == company_id)
        .order_by(desc(QuoteSignal.hs_created_at))
    ).all()
    return [
        QuoteDTO(
            id=q.id,
            deal_id=q.deal_id,
            title=q.title,
            amount=q.amount,
            status=q.status,
            created=_iso(q.hs_created_at),
            days_to_sign=q.days_to_sign,
        )
        for q in rows
    ]


@app.get("/account/{company_id}/metrics", response_model=MetricsDTO)
def get_metrics(company_id: str, s: Session = Depends(get_session)) -> MetricsDTO:
    c = s.get(Company, company_id)
    if c is None:
        raise HTTPException(404, f"unknown company {company_id}")
    return MetricsDTO(
        open_pipeline_amount=c.open_pipeline_amount,
        won_amount_90d=c.won_amount_90d,
        lost_amount_90d=c.lost_amount_90d,
        avg_cycle_days_won=c.avg_cycle_days_won,
        win_rate_90d=c.win_rate_90d,
        stuck_deals_count=c.stuck_deals_count,
        support_load_30d=c.support_load_30d,
        first_response_avg_hours=c.first_response_avg_hours,
        repeat_issue_count=c.repeat_issue_count,
        last_human_activity_at=_iso(c.last_human_activity_at),
        days_since_last_activity=c.days_since_last_activity,
    )


@app.get("/account/{company_id}/hot_signals", response_model=list[HotSignalDTO])
def get_hot_signals(company_id: str, s: Session = Depends(get_session)) -> list[HotSignalDTO]:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")

    now = datetime.now(UTC)
    out: list[HotSignalDTO] = []

    # Stalled deals
    stalled = s.scalars(
        select(DealSignal).where(
            DealSignal.company_id == company_id, DealSignal.stalled.is_(True)
        )
    ).all()
    for d in stalled:
        out.append(
            HotSignalDTO(
                kind="stalled_deal",
                severity="high" if (d.amount or 0) >= 50000 else "medium",
                label=f"Stalled deal: {d.name or d.id}",
                detail=f"${(d.amount or 0):,.0f} · {d.days_in_stage or 0:.0f}d in {d.stage or 'stage'}",
                object_id=d.id,
                hubspot_url=_deal_url(d.id),
            )
        )

    # Aged open tickets (>14d)
    tickets = s.scalars(
        select(TicketSignal).where(
            TicketSignal.company_id == company_id, TicketSignal.is_open.is_(True)
        )
    ).all()
    for t in tickets:
        if t.age_days and t.age_days > 14:
            sev = "high" if t.age_days > 30 else "medium"
            if (t.priority or "").upper() in {"HIGH", "URGENT"}:
                sev = "high"
            out.append(
                HotSignalDTO(
                    kind="aged_ticket",
                    severity=sev,
                    label=f"Aged ticket: {t.subject or t.id}",
                    detail=f"{t.age_days:.0f}d old · priority {t.priority or '—'}",
                    object_id=t.id,
                    hubspot_url=_ticket_url(t.id),
                )
            )

    # Repeat issue clusters (last 30d, naive subject prefix)
    cutoff = now - timedelta(days=30)
    recent = s.scalars(
        select(TicketSignal).where(
            TicketSignal.company_id == company_id,
            TicketSignal.hs_created_at >= cutoff,
            TicketSignal.cluster_id.is_not(None),
        )
    ).all()
    cl = Counter(t.cluster_id for t in recent)
    for cluster_id, n in cl.items():
        if n >= 2:
            members = [t for t in recent if t.cluster_id == cluster_id]
            sample = members[0].subject if members else cluster_id
            out.append(
                HotSignalDTO(
                    kind="repeat_issue",
                    severity="medium" if n < 4 else "high",
                    label=f"Repeat issue ×{n}: {sample}",
                    detail=f"{n} similar tickets in last 30 days",
                    object_id=cluster_id,
                )
            )

    # Contacts gone quiet (>45d)
    contacts = s.scalars(
        select(ContactSignal).where(ContactSignal.company_id == company_id)
    ).all()
    quiet = [
        c
        for c in contacts
        if c.days_since_activity and c.days_since_activity > 45
    ]
    for c in quiet[:5]:
        full = " ".join(filter(None, [c.first_name, c.last_name])).strip() or c.email or c.id
        out.append(
            HotSignalDTO(
                kind="quiet_contact",
                severity="low",
                label=f"Quiet contact: {full}",
                detail=f"No activity for {c.days_since_activity:.0f} days · {c.job_title or '—'}",
                object_id=c.id,
                hubspot_url=_contact_url(c.id),
            )
        )

    # Old quotes (>21d, not signed)
    quotes = s.scalars(
        select(QuoteSignal).where(QuoteSignal.company_id == company_id)
    ).all()
    for q in quotes:
        if q.signed_at:
            continue
        if q.hs_created_at and (now - _as_utc(q.hs_created_at)).days > 21:
            out.append(
                HotSignalDTO(
                    kind="old_quote",
                    severity="medium",
                    label=f"Old quote: {q.title or q.id}",
                    detail=f"{(now - _as_utc(q.hs_created_at)).days}d since created · status {q.status or '—'}",
                    object_id=q.id,
                    hubspot_url=_quote_url(q.id),
                )
            )

    # Integration red
    integ = s.scalars(
        select(IntegrationSignal).where(IntegrationSignal.company_id == company_id)
    ).all()
    for i in integ:
        if (i.status or "").lower() == "red":
            out.append(
                HotSignalDTO(
                    kind="integration_red",
                    severity="high",
                    label=f"Integration RED: {i.integration_name}",
                    detail=f"uptime {i.uptime_pct_30d or 0:.1f}% · errors24h {i.error_count_24h or 0}",
                    object_id=str(i.id),
                )
            )

    # Order by severity then kind
    sev_order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda x: (sev_order.get(x.severity, 9), x.kind))
    return out


@app.get("/account/{company_id}/properties", response_model=list[PropertyDTO])
def get_properties(company_id: str, s: Session = Depends(get_session)) -> list[PropertyDTO]:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    deals = s.scalars(
        select(DealSignal).where(DealSignal.company_id == company_id)
    ).all()
    names = [d.name for d in deals if d.name]
    props = extract_properties_from_deal_names(names)
    return [
        PropertyDTO(
            name=p["name"],
            deal_count=p["deal_count"],
            deal_names_sample=p["deal_names_sample"],
        )
        for p in props
    ]


@app.post("/account/{company_id}/refresh_summaries")
def refresh_summaries(company_id: str, s: Session = Depends(get_session)) -> dict:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    try:
        row = RollupService(
            session_factory=_shared_session_factory(s)
        ).recompute_summaries(company_id)
        return {
            "company_id": company_id,
            "model": row.model,
            "summaries": row.summaries_json,
            "generated_at": row.generated_at.isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"summaries refresh failed: {e}") from e


# ---- JAZ-185: Ask AI per account (grounded Q&A) ----------------------------

class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    model: str
    citations: list[str]


@app.post("/account/{company_id}/ask", response_model=AskResponse)
def ask_account(company_id: str, body: AskRequest, s: Session = Depends(get_session)) -> AskResponse:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    if not body.question.strip():
        raise HTTPException(400, "empty question")
    result = RollupService(
        session_factory=_shared_session_factory(s)
    ).ask(company_id, body.question)
    return AskResponse(**result)


# ---- JAZ-187: Risk/sentiment trajectory history ----------------------------

class RiskHistoryPoint(BaseModel):
    generated_at: str
    risk_flag: str
    risk_score: float | None
    model: str | None


@app.get("/account/{company_id}/risk_history", response_model=list[RiskHistoryPoint])
def risk_history(company_id: str, limit: int = 30, s: Session = Depends(get_session)) -> list[RiskHistoryPoint]:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    rows = s.scalars(
        select(AIAssessment)
        .where(AIAssessment.company_id == company_id)
        .order_by(AIAssessment.generated_at.desc())
        .limit(limit)
    ).all()
    return [
        RiskHistoryPoint(
            generated_at=r.generated_at.isoformat(),
            risk_flag=r.risk_flag,
            risk_score=r.risk_score,
            model=r.model,
        )
        for r in reversed(rows)
    ]


# ---- JAZ-186: Next-best-action persistence (mark done / dismiss) -----------

class NBAUpdateRequest(BaseModel):
    action_index: int
    status: str  # 'done' | 'dismissed' | 'reopened'


@app.post("/account/{company_id}/nba/update")
def update_nba(company_id: str, body: NBAUpdateRequest, s: Session = Depends(get_session)) -> dict:
    """Mark a next-best-action as done / dismissed. Mutates the latest
    assessment row in place so the UI reflects the change immediately.
    A re-rolled assessment will regenerate fresh actions."""
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    if body.status not in {"done", "dismissed", "reopened"}:
        raise HTTPException(400, "status must be done|dismissed|reopened")
    row = s.scalars(
        select(AIAssessment)
        .where(AIAssessment.company_id == company_id)
        .order_by(AIAssessment.generated_at.desc())
        .limit(1)
    ).first()
    if row is None or not row.next_best_actions:
        raise HTTPException(404, "no assessment / no actions to update")
    actions = list(row.next_best_actions)
    if not (0 <= body.action_index < len(actions)):
        raise HTTPException(400, f"action_index {body.action_index} out of range")
    actions[body.action_index] = {**actions[body.action_index], "status": body.status,
                                  "updated_at": datetime.utcnow().isoformat()}
    row.next_best_actions = actions
    # SQLAlchemy needs a hint when mutating JSON in-place
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "next_best_actions")
    s.commit()
    return {"company_id": company_id, "action_index": body.action_index, "status": body.status}
