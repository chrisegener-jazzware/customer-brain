"""Seed the DB with the demo companies named in DEMO_COMPANY_IDS.

Pulls real HubSpot data via the feeder. Idempotent — safe to re-run.

Usage:
    python -m account_intel.scripts.seed_demo
"""
from __future__ import annotations

import logging
import sys

from ..config import settings
from ..feeders import HubSpotFeeder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed_demo")


def main() -> int:
    ids = settings.demo_company_id_list
    if not ids:
        log.error("DEMO_COMPANY_IDS is empty in .env")
        return 1
    if not settings.hubspot_token or settings.hubspot_token.startswith("pat-na2-REPLACE"):
        log.error("HUBSPOT_TOKEN not configured")
        return 2

    feeder = HubSpotFeeder()
    for cid in ids:
        log.info("seeding company %s ...", cid)
        try:
            r = feeder.refresh_company(cid)
            log.info(
                "  ok: %s — tickets=%d (open=%d) deals=%d (stalled=%d)",
                r.name,
                r.tickets,
                r.open_tickets,
                r.deals,
                r.stalled_deals,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("  FAILED %s: %s", cid, e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
