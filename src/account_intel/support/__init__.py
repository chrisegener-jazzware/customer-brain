"""Support mode — AI artifacts for support AMs.

Four artifacts:
  * summarize_ticket(company_id, ticket_id) → 3-line context summary
  * draft_ticket_response(company_id, ticket_id) → response draft
  * triage_ticket(company_id, ticket_id) → escalation flag + reasoning
  * hot_tickets() → cross-book top tickets by risk
"""
from __future__ import annotations

from .ticket_ai import (
    summarize_ticket,
    draft_ticket_response,
    triage_ticket,
)
from .hot import compute_hot_tickets

__all__ = [
    "summarize_ticket",
    "draft_ticket_response",
    "triage_ticket",
    "compute_hot_tickets",
]
