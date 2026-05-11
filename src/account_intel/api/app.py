"""FastAPI service: GET /account/{company_id} and related endpoints."""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ..db import (
    Company,
    DealSignal,
    IntegrationSignal,
    TicketSignal,
    get_session,
)
from ..feeders import HubSpotFeeder
from ..rollup import RollupService

log = logging.getLogger(__name__)

app = FastAPI(title="Jazzware Account Intel", version="0.1.0")


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


# --- routes -------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "account-intel"}


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
    """List all seeded companies — used by the internal UI directory view."""
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


@app.get("/account/{company_id}", response_model=AccountView)
def get_account(
    company_id: str,
    refresh: bool = Query(False, description="Force HubSpot refresh before reading"),
    s: Session = Depends(get_session),
) -> AccountView:
    if refresh:
        try:
            HubSpotFeeder().refresh_company(company_id)
            # rollup will be re-evaluated below
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

    # rollup (cached unless force=refresh). Use the request's bound session
    # via the service's session_factory hook so test DB overrides propagate.
    from contextlib import contextmanager

    @contextmanager
    def _shared_session():
        # The outer `s` already manages its own lifecycle (via get_session
        # dependency). Yield it without closing.
        yield s

    try:
        assessment_row = RollupService(session_factory=_shared_session).get_or_create(
            company_id, force=refresh
        )
        assessment = AssessmentDTO(
            risk_flag=assessment_row.risk_flag,
            risk_score=assessment_row.risk_score,
            narrative=assessment_row.narrative,
            next_best_actions=assessment_row.next_best_actions or [],
            generated_at=assessment_row.generated_at.isoformat(),
            model=assessment_row.model,
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
    """On-demand refresh trigger (used by 'Refresh now' button in UI)."""
    try:
        result = HubSpotFeeder().refresh_company(company_id)
        return {
            "company_id": result.company_id,
            "name": result.name,
            "tickets": result.tickets,
            "deals": result.deals,
            "open_tickets": result.open_tickets,
            "stalled_deals": result.stalled_deals,
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"refresh failed: {e}") from e
