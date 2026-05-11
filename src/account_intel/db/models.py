"""Postgres schema (JAZ-106).

Tables:
* company — mirror of HubSpot company + computed (risk_score, last_refreshed)
* ticket_signal — per-ticket facts
* deal_signal — per-deal facts
* integration_signal — per-integration health (schema only, feeder is Phase 2)
* ai_assessment — Claude roll-up output
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

    tickets: Mapped[list[TicketSignal]] = relationship(back_populates="company", cascade="all, delete-orphan")
    deals: Mapped[list[DealSignal]] = relationship(back_populates="company", cascade="all, delete-orphan")
    integrations: Mapped[list[IntegrationSignal]] = relationship(
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
    cluster_id: Mapped[str | None] = mapped_column(String(64), index=True)  # from support-admin dedup
    age_days: Mapped[float | None] = mapped_column(Float)
    resolution_days: Mapped[float | None] = mapped_column(Float)
    hs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hs_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hs_last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_open: Mapped[bool] = mapped_column(default=True)

    company: Mapped[Company] = relationship(back_populates="tickets")

    __table_args__ = (Index("ix_ticket_company_open", "company_id", "is_open"),)


class DealSignal(Base):
    __tablename__ = "deal_signal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # HubSpot deal id
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
    stalled: Mapped[bool] = mapped_column(default=False)  # >30d no activity & still open
    hs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hs_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="deals")

    __table_args__ = (Index("ix_deal_company_open", "company_id", "is_open"),)


class IntegrationSignal(Base):
    """Per-integration health. Schema only — feeder is Phase 2."""

    __tablename__ = "integration_signal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    integration_name: Mapped[str] = mapped_column(String(120))  # e.g. "Opera PMS", "Avaya PBX"
    uptime_pct_30d: Mapped[float | None] = mapped_column(Float)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_count_24h: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(30))  # green / yellow / red
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="integrations")


class AIAssessment(Base):
    __tablename__ = "ai_assessment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    risk_flag: Mapped[str] = mapped_column(String(10))  # red / yellow / green
    risk_score: Mapped[float | None] = mapped_column(Float)
    narrative: Mapped[str] = mapped_column(Text)
    next_best_actions: Mapped[list] = mapped_column(JSON, default=list)
    signals_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # invalidation key
    model: Mapped[str | None] = mapped_column(String(60))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="assessments")
