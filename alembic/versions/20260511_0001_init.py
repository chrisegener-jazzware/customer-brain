"""Initial schema — company, ticket_signal, deal_signal, integration_signal, ai_assessment.

Revision ID: 20260511_0001
Revises:
Create Date: 2026-05-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260511_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(255), index=True),
        sa.Column("domain", sa.String(255)),
        sa.Column("industry", sa.String(120)),
        sa.Column("country", sa.String(120)),
        sa.Column("city", sa.String(120)),
        sa.Column("lifecycle_stage", sa.String(60)),
        sa.Column("hubspot_owner_id", sa.String(32)),
        sa.Column("annual_revenue", sa.Float),
        sa.Column("employees", sa.Integer),
        sa.Column("hs_created_at", sa.DateTime(timezone=True)),
        sa.Column("risk_score", sa.Float),
        sa.Column("last_refreshed", sa.DateTime(timezone=True), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ticket_signal",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("company_id", sa.String(32), sa.ForeignKey("company.id", ondelete="CASCADE"), index=True),
        sa.Column("subject", sa.String(500)),
        sa.Column("content_excerpt", sa.Text),
        sa.Column("pipeline_stage", sa.String(120)),
        sa.Column("priority", sa.String(30)),
        sa.Column("category", sa.String(120)),
        sa.Column("source_type", sa.String(60)),
        sa.Column("cluster_id", sa.String(64), index=True),
        sa.Column("age_days", sa.Float),
        sa.Column("resolution_days", sa.Float),
        sa.Column("hs_created_at", sa.DateTime(timezone=True)),
        sa.Column("hs_closed_at", sa.DateTime(timezone=True)),
        sa.Column("hs_last_modified", sa.DateTime(timezone=True)),
        sa.Column("is_open", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_ticket_company_open", "ticket_signal", ["company_id", "is_open"])

    op.create_table(
        "deal_signal",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("company_id", sa.String(32), sa.ForeignKey("company.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(500)),
        sa.Column("amount", sa.Float),
        sa.Column("pipeline", sa.String(120)),
        sa.Column("stage", sa.String(120)),
        sa.Column("stage_id", sa.String(64)),
        sa.Column("probability", sa.Float),
        sa.Column("days_in_stage", sa.Float),
        sa.Column("last_activity", sa.DateTime(timezone=True)),
        sa.Column("is_won", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_lost", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_open", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("stalled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("hs_created_at", sa.DateTime(timezone=True)),
        sa.Column("hs_closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_deal_company_open", "deal_signal", ["company_id", "is_open"])

    op.create_table(
        "integration_signal",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.String(32), sa.ForeignKey("company.id", ondelete="CASCADE"), index=True),
        sa.Column("integration_name", sa.String(120), nullable=False),
        sa.Column("uptime_pct_30d", sa.Float),
        sa.Column("last_sync", sa.DateTime(timezone=True)),
        sa.Column("error_count_24h", sa.Integer),
        sa.Column("status", sa.String(30)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ai_assessment",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.String(32), sa.ForeignKey("company.id", ondelete="CASCADE"), index=True),
        sa.Column("risk_flag", sa.String(10), nullable=False),
        sa.Column("risk_score", sa.Float),
        sa.Column("narrative", sa.Text, nullable=False),
        sa.Column("next_best_actions", sa.JSON, server_default="[]"),
        sa.Column("signals_hash", sa.String(64), index=True),
        sa.Column("model", sa.String(60)),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ai_assessment")
    op.drop_table("integration_signal")
    op.drop_index("ix_deal_company_open", "deal_signal")
    op.drop_table("deal_signal")
    op.drop_index("ix_ticket_company_open", "ticket_signal")
    op.drop_table("ticket_signal")
    op.drop_table("company")
