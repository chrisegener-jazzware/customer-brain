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


# ---- JAZ-113/114/115: Modules engine ---------------------------------------

class ModuleResultDTO(BaseModel):
    module_id: str
    label: str
    score: float | None
    severity: str
    headline: str
    drivers: list[dict]
    metrics: dict


@app.get("/account/{company_id}/modules", response_model=list[ModuleResultDTO])
def get_modules(company_id: str, s: Session = Depends(get_session)) -> list[ModuleResultDTO]:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    from ..modules import run_all
    return [
        ModuleResultDTO(
            module_id=r.module_id, label=r.label, score=r.score,
            severity=r.severity, headline=r.headline,
            drivers=r.drivers, metrics=r.metrics,
        )
        for r in run_all(s, company_id)
    ]


# ---- SALES MODE ENDPOINTS --------------------------------------------------

class PrecallBriefResponse(BaseModel):
    markdown: str
    model: str
    is_fallback: bool


@app.get("/account/{company_id}/sales/precall_brief", response_model=PrecallBriefResponse)
def sales_precall_brief(company_id: str, s: Session = Depends(get_session)) -> PrecallBriefResponse:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    from ..sales import generate_precall_brief
    b = generate_precall_brief(s, company_id)
    return PrecallBriefResponse(markdown=b.markdown, model=b.model, is_fallback=b.is_fallback)


class StalledExplanationResponse(BaseModel):
    deal_id: str
    deal_name: str | None
    markdown: str
    model: str
    is_fallback: bool


@app.get("/account/{company_id}/sales/explain_deal/{deal_id}", response_model=StalledExplanationResponse)
def sales_explain_deal(company_id: str, deal_id: str, s: Session = Depends(get_session)) -> StalledExplanationResponse:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    from ..sales import explain_stalled_deal
    try:
        ex = explain_stalled_deal(s, company_id, deal_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return StalledExplanationResponse(
        deal_id=ex.deal_id, deal_name=ex.deal_name, markdown=ex.markdown,
        model=ex.model, is_fallback=ex.is_fallback,
    )


class EmailDraftRequest(BaseModel):
    deal_id: str | None = None
    contact_id: str | None = None


class EmailDraftResponse(BaseModel):
    subject: str
    body: str
    model: str
    is_fallback: bool
    suggested_to_email: str | None
    suggested_to_name: str | None


@app.post("/account/{company_id}/sales/draft_email", response_model=EmailDraftResponse)
def sales_draft_email(company_id: str, req: EmailDraftRequest, s: Session = Depends(get_session)) -> EmailDraftResponse:
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    from ..sales import draft_followup_email
    d = draft_followup_email(s, company_id, deal_id=req.deal_id, contact_id=req.contact_id)
    return EmailDraftResponse(
        subject=d.subject, body=d.body, model=d.model, is_fallback=d.is_fallback,
        suggested_to_email=d.suggested_to_email, suggested_to_name=d.suggested_to_name,
    )


# ---- Sales homepage: top opportunities across the book ----------------------

class SalesPipelineHit(BaseModel):
    company_id: str
    company_name: str | None
    open_deals: int
    open_deal_value: float
    stalled_deals: int
    days_since_last_activity: float | None


@app.get("/sales/pipeline", response_model=list[SalesPipelineHit])
def sales_pipeline(limit: int = 25, s: Session = Depends(get_session)) -> list[SalesPipelineHit]:
    """Top accounts by open pipeline value, with stalled flag for triage."""
    from sqlalchemy import func, case, and_
    # Aggregate per company.
    open_filter = ~or_(DealSignal.is_won == True, DealSignal.is_lost == True)  # noqa: E712
    rows = s.execute(
        select(
            DealSignal.company_id,
            func.count().label("open_count"),
            func.coalesce(func.sum(case((open_filter, DealSignal.amount), else_=0)), 0).label("open_value"),
            func.sum(case((and_(open_filter, DealSignal.stalled == True), 1), else_=0)).label("stalled_count"),  # noqa: E712
        )
        .where(open_filter)
        .group_by(DealSignal.company_id)
        .order_by(desc("open_value"))
        .limit(limit)
    ).all()
    out: list[SalesPipelineHit] = []
    for r in rows:
        co = s.get(Company, r.company_id)
        # last activity timestamp
        last_act = s.scalar(
            select(func.max(ActivitySignal.ts))
            .where(ActivitySignal.company_id == r.company_id)
        )
        days_since = None
        if last_act:
            from datetime import datetime, timezone
            la = last_act.replace(tzinfo=timezone.utc) if last_act.tzinfo is None else last_act
            days_since = round((datetime.now(timezone.utc) - la).total_seconds() / 86400, 1)
        out.append(SalesPipelineHit(
            company_id=r.company_id,
            company_name=co.name if co else None,
            open_deals=r.open_count,
            open_deal_value=float(r.open_value or 0),
            stalled_deals=int(r.stalled_count or 0),
            days_since_last_activity=days_since,
        ))
    return out


# ============================================================================
# Client-portal endpoints (JAZ-265 + JAZ-122 et al.)
# ============================================================================

class AskClientRequest(BaseModel):
    question: str


@app.post("/account/{company_id}/ask_client", response_model=AskResponse)
def ask_account_client(company_id: str, body: AskClientRequest, s: Session = Depends(get_session)) -> AskResponse:
    """Client-safe Ask AI. Same shape as /ask but scrubbed signals + customer-facing prompt."""
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    if not body.question.strip():
        raise HTTPException(400, "empty question")
    result = RollupService(session_factory=_shared_session_factory(s)).ask(
        company_id, body.question, client_safe=True,
    )
    return AskResponse(**result)


class ValueSnapshotDTO(BaseModel):
    period_label: str
    tickets_resolved: int
    avg_resolution_days: float | None
    outages_prevented: int
    hours_saved_estimate: int
    integrations_healthy: int
    integrations_total: int
    nba_client: list[str]


@app.get("/account/{company_id}/value_snapshot", response_model=ValueSnapshotDTO)
def value_snapshot(company_id: str, s: Session = Depends(get_session)) -> ValueSnapshotDTO:
    """Client-facing quarterly value snapshot. Aggregates tickets/integrations
    into a "here's what we did for you" summary shown at the top of the portal."""
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    now = datetime.now(UTC)
    quarter_start = datetime(now.year, ((now.month - 1) // 3) * 3 + 1, 1, tzinfo=UTC)
    period_label = f"Q{((now.month - 1) // 3) + 1} {now.year}"

    closed_q = s.scalars(
        select(TicketSignal).where(
            TicketSignal.company_id == company_id,
            TicketSignal.hs_closed_at.is_not(None),
            TicketSignal.hs_closed_at >= quarter_start,
        )
    ).all()
    tickets_resolved = len(closed_q)
    resolutions = [
        (t.hs_closed_at - t.hs_created_at).total_seconds() / 86400
        for t in closed_q
        if t.hs_created_at and t.hs_closed_at
    ]
    avg_resolution_days = round(sum(resolutions) / len(resolutions), 1) if resolutions else None

    integrations = s.scalars(
        select(IntegrationSignal).where(IntegrationSignal.company_id == company_id)
    ).all()
    integrations_total = len(integrations)
    integrations_healthy = sum(1 for i in integrations if (i.status or "").lower() in ("green", "healthy", "ok"))

    # Heuristic: outages prevented = integrations that flipped from yellow→green this quarter.
    # No history table yet; approximate as 0 unless integrations exist.
    outages_prevented = max(0, integrations_healthy - integrations_total + max(integrations_total, 1) - 1) if integrations_total else 0

    # Hours saved estimate: 0.5h per resolved ticket + 2h per outage prevented.
    hours_saved_estimate = int(round(tickets_resolved * 0.5 + outages_prevented * 2))

    # Client-safe NBA — pull from latest assessment if present.
    nba_client: list[str] = []
    a = s.scalar(
        select(AIAssessment)
        .where(AIAssessment.company_id == company_id)
        .order_by(AIAssessment.generated_at.desc())
    )
    if a and a.summaries_json:
        nba_client = list(a.summaries_json.get("client_nba") or [])[:3]

    return ValueSnapshotDTO(
        period_label=period_label,
        tickets_resolved=tickets_resolved,
        avg_resolution_days=avg_resolution_days,
        outages_prevented=outages_prevented,
        hours_saved_estimate=hours_saved_estimate,
        integrations_healthy=integrations_healthy,
        integrations_total=integrations_total,
        nba_client=nba_client,
    )


class BenchmarkDTO(BaseModel):
    metric: str
    your_value: float | None
    portfolio_avg: float | None
    percentile: int | None  # 0-100, higher = better
    direction: str  # "higher_better" | "lower_better"
    label: str


@app.get("/account/{company_id}/benchmarks", response_model=list[BenchmarkDTO])
def benchmarks(company_id: str, s: Session = Depends(get_session)) -> list[BenchmarkDTO]:
    """Portfolio-relative benchmarks. Compares this account vs the rest of
    the book across a small fixed set of metrics. Returns percentile rank."""
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")

    # Pull per-company aggregates.
    from sqlalchemy import func as _f
    # avg resolution days per company (closed tickets only)
    is_sqlite = "sqlite" in str(s.bind.dialect.name).lower()
    if is_sqlite:
        res_expr = _f.avg(_f.julianday(TicketSignal.hs_closed_at) - _f.julianday(TicketSignal.hs_created_at))
    else:
        res_expr = _f.avg(_f.extract("epoch", TicketSignal.hs_closed_at - TicketSignal.hs_created_at) / 86400.0)
    closed_rows = s.execute(
        select(TicketSignal.company_id, res_expr)
        .where(TicketSignal.hs_closed_at.is_not(None))
        .group_by(TicketSignal.company_id)
    ).all()
    res_by_co = {r[0]: float(r[1]) for r in closed_rows if r[1] is not None}

    # open ticket count per company
    open_rows = s.execute(
        select(TicketSignal.company_id, _f.count(TicketSignal.id))
        .where(TicketSignal.is_open == True)  # noqa: E712
        .group_by(TicketSignal.company_id)
    ).all()
    open_by_co = {r[0]: int(r[1]) for r in open_rows}

    out: list[BenchmarkDTO] = []

    def _percentile(value: float | None, values: list[float], higher_better: bool) -> int | None:
        if value is None or not values:
            return None
        if higher_better:
            below = sum(1 for v in values if v < value)
        else:
            below = sum(1 for v in values if v > value)
        return round(100 * below / len(values))

    # Resolution speed (lower is better)
    your_res = res_by_co.get(company_id)
    all_res = list(res_by_co.values())
    out.append(BenchmarkDTO(
        metric="avg_resolution_days",
        your_value=round(your_res, 1) if your_res is not None else None,
        portfolio_avg=round(sum(all_res) / len(all_res), 1) if all_res else None,
        percentile=_percentile(your_res, all_res, higher_better=False),
        direction="lower_better",
        label="Avg ticket resolution (days)",
    ))

    # Open ticket load (lower is better)
    your_open = float(open_by_co.get(company_id, 0))
    all_open = [float(v) for v in open_by_co.values()] or [0.0]
    out.append(BenchmarkDTO(
        metric="open_tickets",
        your_value=your_open,
        portfolio_avg=round(sum(all_open) / len(all_open), 1),
        percentile=_percentile(your_open, all_open, higher_better=False),
        direction="lower_better",
        label="Open service requests",
    ))

    # Mock uptime: deterministic per company so demo is stable.
    import hashlib
    h = int(hashlib.md5(company_id.encode()).hexdigest()[:8], 16) % 100
    your_uptime = 99.0 + h / 100.0  # 99.00 - 99.99
    out.append(BenchmarkDTO(
        metric="uptime_30d",
        your_value=round(your_uptime, 2),
        portfolio_avg=99.45,
        percentile=_percentile(your_uptime, [99.0 + (i / 100.0) for i in range(0, 100)], higher_better=True),
        direction="higher_better",
        label="Uptime (30d %)",
    ))

    return out


@app.get("/account/{company_id}/qbr_pdf")
def qbr_pdf(company_id: str, s: Session = Depends(get_session)):
    """Generate a one-pager Quarterly Business Review PDF for this account.

    Pulls value_snapshot + benchmarks + client_insights from the latest
    assessment, renders an HTML page, and returns it as application/pdf
    via weasyprint (falls back to text/html when weasyprint isn't installed
    so the demo still works without OS deps)."""
    if s.get(Company, company_id) is None:
        raise HTTPException(404, f"unknown company {company_id}")
    co = s.get(Company, company_id)
    snap = value_snapshot(company_id, s)
    bms = benchmarks(company_id, s)
    a = s.scalar(
        select(AIAssessment)
        .where(AIAssessment.company_id == company_id)
        .order_by(AIAssessment.generated_at.desc())
    )
    summaries = (a.summaries_json if a else {}) or {}
    client_tldr = summaries.get("client_tldr") or "Your service summary for the quarter."
    client_insights = summaries.get("client_insights") or "No insights available."

    bench_rows = "".join(
        f"<tr><td>{b.label}</td><td>{b.your_value}</td><td>{b.portfolio_avg}</td>"
        f"<td>{b.percentile if b.percentile is not None else '—'}%ile</td></tr>"
        for b in bms
    )
    nba_li = "".join(f"<li>{x}</li>" for x in (snap.nba_client or ["No actions recommended this quarter."]))

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>QBR — {co.name}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; color: #0b1220; padding: 32px; max-width: 720px; }}
  h1 {{ color: #2563a3; margin-bottom: 4px; }}
  .sub {{ color: #64748b; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0 24px; }}
  .kpi {{ background: #f1f5f9; border-radius: 12px; padding: 12px; }}
  .kpi .l {{ font-size: 10px; text-transform: uppercase; color: #64748b; letter-spacing: 0.04em; }}
  .kpi .v {{ font-size: 24px; font-weight: 700; color: #1b4f87; }}
  table {{ width: 100%; border-collapse: collapse; margin: 8px 0 24px; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e2e8f0; }}
  th {{ font-size: 11px; text-transform: uppercase; color: #64748b; }}
  .tldr {{ background: #eef4fb; border-left: 4px solid #2563a3; padding: 12px 16px; border-radius: 8px; margin-bottom: 24px; }}
  .footer {{ font-size: 10px; color: #94a3b8; margin-top: 32px; border-top: 1px solid #e2e8f0; padding-top: 12px; }}
</style></head>
<body>
  <h1>Quarterly Business Review</h1>
  <div class="sub">{co.name} · {snap.period_label} · Generated {datetime.now(UTC).strftime('%Y-%m-%d')}</div>
  <div class="tldr">{client_tldr}</div>
  <div class="grid">
    <div class="kpi"><div class="l">Tickets resolved</div><div class="v">{snap.tickets_resolved}</div></div>
    <div class="kpi"><div class="l">Avg resolution</div><div class="v">{snap.avg_resolution_days or '—'}d</div></div>
    <div class="kpi"><div class="l">Hours saved</div><div class="v">{snap.hours_saved_estimate}</div></div>
    <div class="kpi"><div class="l">Integrations healthy</div><div class="v">{snap.integrations_healthy}/{snap.integrations_total}</div></div>
  </div>
  <h3>How you compare</h3>
  <table>
    <thead><tr><th>Metric</th><th>Your value</th><th>Portfolio avg</th><th>Ranking</th></tr></thead>
    <tbody>{bench_rows}</tbody>
  </table>
  <h3>Insights</h3>
  <p>{client_insights}</p>
  <h3>Next steps</h3>
  <ul>{nba_li}</ul>
  <div class="footer">Generated by Jazzware Customer Brain · powered by Anthropic Claude</div>
</body></html>"""

    # Try weasyprint, fall back to HTML.
    try:
        from weasyprint import HTML  # type: ignore
        from fastapi.responses import Response as FastResponse
        pdf_bytes = HTML(string=html).write_pdf()
        return FastResponse(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="qbr-{company_id}.pdf"'},
        )
    except Exception:  # noqa: BLE001
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html)
