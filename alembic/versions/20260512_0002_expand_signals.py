"""Expand signals: new tables (contact_signal, activity_signal, quote_signal) and
computed-metric columns on company + stage_history_json on deal_signal
+ reply_count / first_response_minutes / hubspot_owner_id on ticket_signal
+ summaries_json on ai_assessment.

All additive — existing rows continue to work.

Revision ID: 20260512_0002
Revises: 20260511_0001
Create Date: 2026-05-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260512_0002"
down_revision: str | None = "20260511_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- company: new computed metric columns -------------------------------
    with op.batch_alter_table("company") as b:
        b.add_column(sa.Column("open_pipeline_amount", sa.Float))
        b.add_column(sa.Column("won_amount_90d", sa.Float))
        b.add_column(sa.Column("lost_amount_90d", sa.Float))
        b.add_column(sa.Column("avg_cycle_days_won", sa.Float))
        b.add_column(sa.Column("win_rate_90d", sa.Float))
        b.add_column(sa.Column("stuck_deals_count", sa.Integer))
        b.add_column(sa.Column("support_load_30d", sa.Integer))
        b.add_column(sa.Column("first_response_avg_hours", sa.Float))
        b.add_column(sa.Column("repeat_issue_count", sa.Integer))
        b.add_column(sa.Column("last_human_activity_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("days_since_last_activity", sa.Float))

    # --- ticket_signal: reply_count, first_response_minutes, owner ----------
    with op.batch_alter_table("ticket_signal") as b:
        b.add_column(sa.Column("reply_count", sa.Integer))
        b.add_column(sa.Column("first_response_minutes", sa.Float))
        b.add_column(sa.Column("hubspot_owner_id", sa.String(32)))

    # --- deal_signal: stage_history_json + owner ----------------------------
    with op.batch_alter_table("deal_signal") as b:
        b.add_column(sa.Column("stage_history_json", sa.JSON))
        b.add_column(sa.Column("hubspot_owner_id", sa.String(32)))

    # --- ai_assessment: summaries_json --------------------------------------
    with op.batch_alter_table("ai_assessment") as b:
        b.add_column(sa.Column("summaries_json", sa.JSON))

    # --- contact_signal -----------------------------------------------------
    op.create_table(
        "contact_signal",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(32),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("first_name", sa.String(120)),
        sa.Column("last_name", sa.String(120)),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(60)),
        sa.Column("job_title", sa.String(255)),
        sa.Column("last_activity_at", sa.DateTime(timezone=True)),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True)),
        sa.Column("hs_created_at", sa.DateTime(timezone=True)),
        sa.Column("days_since_activity", sa.Float),
    )

    # --- activity_signal ----------------------------------------------------
    op.create_table(
        "activity_signal",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(32),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(500)),
        sa.Column("content_preview", sa.Text),
        sa.Column("direction", sa.String(20)),
        sa.Column("ts", sa.DateTime(timezone=True), index=True),
        sa.Column("owner_id", sa.String(32)),
    )
    op.create_index("ix_activity_company_ts", "activity_signal", ["company_id", "ts"])

    # --- quote_signal -------------------------------------------------------
    op.create_table(
        "quote_signal",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(32),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("deal_id", sa.String(32), index=True),
        sa.Column("title", sa.String(500)),
        sa.Column("amount", sa.Float),
        sa.Column("status", sa.String(60)),
        sa.Column("revision_count", sa.Integer),
        sa.Column("days_to_sign", sa.Float),
        sa.Column("hs_created_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("signed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("quote_signal")
    op.drop_index("ix_activity_company_ts", "activity_signal")
    op.drop_table("activity_signal")
    op.drop_table("contact_signal")

    with op.batch_alter_table("ai_assessment") as b:
        b.drop_column("summaries_json")
    with op.batch_alter_table("deal_signal") as b:
        b.drop_column("hubspot_owner_id")
        b.drop_column("stage_history_json")
    with op.batch_alter_table("ticket_signal") as b:
        b.drop_column("hubspot_owner_id")
        b.drop_column("first_response_minutes")
        b.drop_column("reply_count")
    with op.batch_alter_table("company") as b:
        for col in [
            "days_since_last_activity",
            "last_human_activity_at",
            "repeat_issue_count",
            "first_response_avg_hours",
            "support_load_30d",
            "stuck_deals_count",
            "win_rate_90d",
            "avg_cycle_days_won",
            "lost_amount_90d",
            "won_amount_90d",
            "open_pipeline_amount",
        ]:
            b.drop_column(col)
