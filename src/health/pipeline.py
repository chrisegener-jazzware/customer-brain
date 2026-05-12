"""End-to-end pipeline: discover companies -> score -> render digests.

Real HubSpot calls for company + ticket discovery (paginated). Integration
health is stubbed (JAZ-91). AM mapping is stubbed (JAZ-92). Claude is real if
ANTHROPIC_API_KEY is set, otherwise we fall back to a templated narrative.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from .config import get_settings
from .roster import assign_am
from .scoring import HealthScore, score_customer
from .sources.hubspot_companies import Company, HubSpotCompanySource
from .sources.hubspot_tickets import HubSpotTicketSource
from .sources.integration_health import integration_health

log = logging.getLogger(__name__)


def run_pipeline(
    *,
    customer_id: str | None = None,
    lookback_days: int = 90,
    use_claude: bool = True,
    limit: int | None = None,
) -> list[HealthScore]:
    """Discover active customers (or use a single id), score each, return list."""
    settings = get_settings()
    company_source = HubSpotCompanySource(lookback_days=lookback_days)
    ticket_source = HubSpotTicketSource()

    if customer_id:
        try:
            c = company_source.client.crm.companies.basic_api.get_by_id(
                company_id=customer_id, properties=["name", "domain", "hubspot_owner_id"]
            )
            p = c.properties or {}
            companies = [
                Company(
                    id=customer_id,
                    name=p.get("name") or f"Company {customer_id}",
                    domain=p.get("domain"),
                    owner_id=p.get("hubspot_owner_id"),
                )
            ]
        except Exception as e:  # noqa: BLE001
            log.warning("could not hydrate company %s: %s — using bare record",
                        customer_id, e)
            companies = [Company(id=customer_id, name=f"Company {customer_id}")]
    else:
        companies = company_source.list_active_companies()

    if limit:
        companies = companies[:limit]

    log.info("Scoring %d companies (claude=%s)", len(companies), use_claude)

    scores: list[HealthScore] = []
    for c in companies:
        company_dict = c.as_dict()
        try:
            ticket_signals = ticket_source.fetch(c.id)
        except Exception as e:  # noqa: BLE001
            log.warning("ticket fetch failed for %s: %s", c.id, e)
            continue
        integ = integration_health(company_dict)
        am = assign_am(company_dict)
        score = score_customer(
            company=company_dict,
            tickets=ticket_signals,
            integ=integ,
            account_manager=am,
            use_claude=use_claude,
        )
        scores.append(score)
        log.info(
            "scored %s (%s) -> %d/%s",
            c.name,
            c.id,
            score.score,
            score.flag,
        )
    return scores


def scores_to_jsonable(scores: list[HealthScore]) -> list[dict[str, Any]]:
    return [asdict(s) for s in scores]
