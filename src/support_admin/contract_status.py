"""Contract status lookup — STUB.

TODO(JAZ-XXX): Wire up to HubSpot custom field once the field name is
confirmed by RevOps. Candidate names floated so far:

    - ``contract_status``        (company-level)
    - ``jw_contract_state``       (proposed Jazzware namespace)
    - ``support_entitlement``     (Aspire-wide)

Until that's locked in we return ``"active"`` so downstream code paths can be
wired without blocking on the field decision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .hubspot_client import HSTicket

log = logging.getLogger(__name__)

ContractStatusValue = str  # "active" | "expired" | "lapsed" | "trial" | "unknown"

PLACEHOLDER_FIELD = "contract_status"  # TODO: replace with confirmed property name


@dataclass
class ContractStatus:
    company_id: str | None
    status: ContractStatusValue
    source: str  # "stub" until field is wired


def lookup(ticket: HSTicket) -> ContractStatus:
    """STUB: returns ``"active"`` until the HubSpot field name is confirmed.

    When the real field name is known, replace this with a call to
    ``HubSpotClient.get_company_property(ticket.company_id, PLACEHOLDER_FIELD)``.
    """
    log.debug(
        "contract_status.lookup is a stub (ticket=%s company=%s); returning 'active'",
        ticket.id,
        ticket.company_id,
    )
    return ContractStatus(company_id=ticket.company_id, status="active", source="stub")
