"""Postgres schema (JAZ-106, expanded).

Tables:
* company — mirror of HubSpot company + computed metrics + last_refreshed + risk_score
* ticket_signal — per-ticket facts (+ reply_count, first_response_minutes)
* deal_signal — per-deal facts (+ stage_history_json)
* integration_signal — per-integration health (schema only, feeder is Phase 2)
* contact_signal — per-associated contact (NEW)
* activity_signal — engagement timeline: calls, emails, meetings, notes (NEW)
* quote_signal — quote-level signals (NEW; skipped gracefully when 403)
* ai_assessment — Claude roll-up output (+ summaries_json multi-zoom AI views)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "company"

    # HubSpot company id (string in HS, but always numeric)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)
    domain: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(120))
    lifecycle_stage: Mapped[str | None] = mapped_column(String(60))
    hubspot_owner_id: Mapped[str | None] = mapped_column(String(32))
    annual_revenue: Mapped[float | None] = mapped_column(Float)
    employees: Mapped[int | None] = mapped_column(Integer)
    hs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Computed
    risk_score: Mapped[float | None] = mapped_column(Float)  # 0-100, higher = more risk
    last_refreshed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # --- NEW computed metrics (denormalized for fast UI reads) ---------------
    open_pipeline_amount: Mapped[float | None] = mapped_column(Float)
    won_amount_90d: Mapped[float | None] = mapped_column(Float)
    lost_amount_90d: Mapped[float | None] = mapped_column(Float)
    avg_cycle_days_won: Mapped[float | None] = mapped_column(Float)
    win_rate_90d: Mapped[float | None] = mapped_column(Float)  # 0..1
    stuck_deals_count: Mapped[int | None] = mapped_column(Integer)
    support_load_30d: Mapped[int | None] = mapped_column(Integer)  # tickets opened in last 30d
    first_response_avg_hours: Mapped[float | None] = mapped_column(Float)
    repeat_issue_count: Mapped[int | None] = mapped_column(Integer)
    last_human_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    days_since_last_activity: Mapped[float | None] = mapped_column(Float)

    tickets: Mapped[list[TicketSignal]] = relationship(back_populates="company", cascade="all, delete-orphan")
    deals: Mapped[list[DealSignal]] = relationship(back_populates="company", cascade="all, delete-orphan")
    integrations: Mapped[list[IntegrationSignal]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    contacts: Mapped[list[ContactSignal]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    activities: Mapped[list[ActivitySignal]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    quotes: Mapped[list[QuoteSignal]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    assessments: Mapped[list[AIAssessment]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class TicketSignal(Base):
    __tablename__ = "ticket_signal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # HubSpot ticket id
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str | None] = mapped_column(String(500))
    content_excerpt: Mapped[str | None] = mapped_column(Text)
    pipeline_stage: Mapped[str | None] = mapped_column(String(120))
    priority: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(120))
    source_type: Mapped[str | None] = mapped_column(String(60))
    cluster_id: Mapped[str | None] = mapped_column(String(64), index=True)
    age_days: Mapped[float | None] = mapped_column(Float)
    resolution_days: Mapped[float | None] = mapped_column(Float)
    hs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hs_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hs_last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_open: Mapped[bool] = mapped_column(default=True)

    # NEW
    reply_count: Mapped[int | None] = mapped_column(Integer)
    first_response_minutes: Mapped[float | None] = mapped_column(Float)
    hubspot_owner_id: Mapped[str | None] = mapped_column(String(32))

    company: Mapped[Company] = relationship(back_populates="tickets")

    __table_args__ = (Index("ix_ticket_company_open", "company_id", "is_open"),)


class DealSignal(Base):
    __tablename__ = "deal_signal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(500))
    amount: Mapped[float | None] = mapped_column(Float)
    pipeline: Mapped[str | None] = mapped_column(String(120))
    stage: Mapped[str | None] = mapped_column(String(120))
    stage_id: Mapped[str | None] = mapped_column(String(64))
    probability: Mapped[float | None] = mapped_column(Float)
    days_in_stage: Mapped[float | None] = mapped_column(Float)
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_won: Mapped[bool] = mapped_column(default=False)
    is_lost: Mapped[bool] = mapped_column(default=False)
    is_open: Mapped[bool] = mapped_column(default=True)
    stalled: Mapped[bool] = mapped_column(default=False)
    hs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hs_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # NEW: [{stage_id, stage_label, entered_at, days_at_stage}, ...]
    stage_history_json: Mapped[list | None] = mapped_column(JSON)
    hubspot_owner_id: Mapped[str | None] = mapped_column(String(32))

    company: Mapped[Company] = relationship(back_populates="deals")

    __table_args__ = (Index("ix_deal_company_open", "company_id", "is_open"),)


class IntegrationSignal(Base):
    __tablename__ = "integration_signal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    integration_name: Mapped[str] = mapped_column(String(120))
    uptime_pct_30d: Mapped[float | None] = mapped_column(Float)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_count_24h: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(30))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="integrations")


class ContactSignal(Base):
    """Per-associated contact for a company."""

    __tablename__ = "contact_signal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # HubSpot contact id
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(60))
    job_title: Mapped[str | None] = mapped_column(String(255))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    days_since_activity: Mapped[float | None] = mapped_column(Float)

    company: Mapped[Company] = relationship(back_populates="contacts")


class ActivitySignal(Base):
    """Engagement timeline: calls, emails, meetings, notes."""

    __tablename__ = "activity_signal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # engagement id
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # call|email|meeting|note
    subject: Mapped[str | None] = mapped_column(String(500))
    content_preview: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str | None] = mapped_column(String(20))  # INBOUND|OUTBOUND
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(32))

    company: Mapped[Company] = relationship(back_populates="activities")

    __table_args__ = (Index("ix_activity_company_ts", "company_id", "ts"),)


class QuoteSignal(Base):
    """Quote-level signals — gracefully empty when HubSpot scope is denied."""

    __tablename__ = "quote_signal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # quote id
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    deal_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(60))
    revision_count: Mapped[int | None] = mapped_column(Integer)
    days_to_sign: Mapped[float | None] = mapped_column(Float)
    hs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="quotes")


class AIAssessment(Base):
    __tablename__ = "ai_assessment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    risk_flag: Mapped[str] = mapped_column(String(10))  # red / yellow / green
    risk_score: Mapped[float | None] = mapped_column(Float)
    narrative: Mapped[str] = mapped_column(Text)
    next_best_actions: Mapped[list] = mapped_column(JSON, default=list)
    signals_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(60))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # NEW — multi-zoom summaries:
    # {
    #   "tldr": "...",
    #   "support_summary": "...",
    #   "sales_summary": "...",
    #   "relationship_summary": "...",
    #   "risk_drivers": ["...","..."],
    #   "opportunities": ["...","..."],
    #   "client_tldr": "...",
    #   "client_insights": "..."
    # }
    summaries_json: Mapped[dict | None] = mapped_column(JSON)

    company: Mapped[Company] = relationship(back_populates="assessments")
