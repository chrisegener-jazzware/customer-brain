"""Nightly incremental HubSpot refresh.

Strategy
--------
For every company already in the DB whose `last_refreshed` is older than
`--max-age` hours, pull the latest HubSpot signals (tickets / deals / contacts
/ activities / quotes / integration signals) via the existing feeder.

The on-demand UI "Refresh" button keeps fresh accounts current; this cron
catches the long-tail of accounts nobody clicked into.

Run with:
    DATABASE_URL=sqlite:///.../customer-brain.db \\
    HUBSPOT_TOKEN=*** \\
    python -m account_intel.scripts.nightly_refresh \\
        --max-age 24 --limit 500

`--limit` caps the per-run workload so a backlog doesn't blow rate limits;
the next run picks up the remainder. With ~250k HubSpot API calls/day
available and ~10 calls per company, 500 per night is safe.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from ..config import settings
from ..db import Company, SessionLocal
from ..feeders import HubSpotFeeder

log = logging.getLogger("account_intel.nightly_refresh")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _stale_company_ids(max_age_hours: int, limit: int) -> list[str]:
    """Return company ids whose last_refreshed is older than the cutoff.

    Companies with NULL last_refreshed (never refreshed) sort first.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    with SessionLocal() as s:
        rows = s.execute(
            select(Company.id, Company.last_refreshed)
            .where(
                (Company.last_refreshed.is_(None))
                | (Company.last_refreshed < cutoff)
            )
            .order_by(Company.last_refreshed.asc().nullsfirst())
            .limit(limit)
        ).all()
    return [r[0] for r in rows]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--max-age",
        type=int,
        default=24,
        help="Refresh companies whose last_refreshed is older than this many hours.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum companies to refresh in this run (rate-limit guard).",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep between companies (be nicer to HubSpot if needed).",
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    _setup_logging(args.verbose)

    if not settings.hubspot_token or settings.hubspot_token.startswith("***"):
        log.error("HUBSPOT_TOKEN not set — aborting")
        return 2

    ids = _stale_company_ids(args.max_age, args.limit)
    if not ids:
        log.info("No stale companies to refresh.")
        return 0

    log.info("Refreshing %d stale companies (max_age=%dh, limit=%d)",
             len(ids), args.max_age, args.limit)

    feeder = HubSpotFeeder()
    ok = 0
    fail = 0
    for i, cid in enumerate(ids, 1):
        try:
            feeder.refresh_company(cid)
            ok += 1
            if i % 25 == 0 or i == len(ids):
                log.info("  progress %d/%d (ok=%d fail=%d)", i, len(ids), ok, fail)
        except Exception as e:  # noqa: BLE001
            fail += 1
            log.warning("refresh failed for %s: %s", cid, e)
        if args.sleep:
            time.sleep(args.sleep)

    log.info("Done. ok=%d fail=%d", ok, fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
