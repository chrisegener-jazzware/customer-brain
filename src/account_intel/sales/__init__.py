"""Sales mode — AM-facing AI artifacts.

Three artifacts:
  * generate_precall_brief(company_id) → markdown brief
  * explain_stalled_deal(company_id, deal_id) → explanation + next step
  * draft_followup_email(company_id, deal_id?, contact_id?) → email draft

All three reuse the rollup signals payload + Claude client.
"""
from __future__ import annotations

from .briefs import generate_precall_brief
from .deals import explain_stalled_deal
from .emails import draft_followup_email

__all__ = [
    "generate_precall_brief",
    "explain_stalled_deal",
    "draft_followup_email",
]
