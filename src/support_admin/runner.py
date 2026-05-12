"""Main poll loop — fetch new tickets, run rules, write annotations back to HubSpot.

Annotations written via custom properties (``jw_repeat_of``, ``jw_dedup_score``,
``jw_vip_flag``, ``jw_link_kind``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import db
from .config import get_settings
from .contract_status import lookup as lookup_contract
from .dedup import DedupResult, detect_repeat
from .embeddings import Embedder, get_embedder
from .hubspot_client import (
    HSTicket,
    HubSpotClient,
    PROP_DEDUP_SCORE,
    PROP_LINK_KIND,
    PROP_REPEAT_OF,
    PROP_VIP_FLAG,
)
from .vip import VIPResult, apply as apply_vip, evaluate as evaluate_vip, load_vip_list

log = logging.getLogger(__name__)


@dataclass
class TicketOutcome:
    ticket_id: str
    company_id: str | None
    dedup: DedupResult
    vip: VIPResult
    contract_status: str
    written: bool

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "company_id": self.company_id,
            "dedup": asdict(self.dedup),
            "vip_is_vip": self.vip.is_vip,
            "vip_tier": self.vip.entry.tier if self.vip.entry else None,
            "contract_status": self.contract_status,
            "written": self.written,
        }


def process_ticket(
    ticket: HSTicket,
    client: HubSpotClient,
    *,
    embedder: Embedder | None = None,
    vip_list: dict | None = None,
    db_path: Path | None = None,
    dry_run: bool = False,
) -> TicketOutcome:
    """Run all rules against a single ticket and (unless dry_run) write back."""
    settings = get_settings()
    db_path = Path(db_path) if db_path is not None else settings.db_path
    embedder = embedder or get_embedder()

    dedup = detect_repeat(ticket, db_path=db_path, embedder=embedder)
    vip_result = evaluate_vip(ticket, vip_list=vip_list)
    contract = lookup_contract(ticket)

    properties: dict[str, str] = {}
    if dedup.is_repeat and dedup.repeat_of:
        properties[PROP_REPEAT_OF] = str(dedup.repeat_of)
        properties[PROP_DEDUP_SCORE] = f"{dedup.score:.4f}"
        properties[PROP_LINK_KIND] = dedup.link_kind or ""
    if vip_result.is_vip:
        properties[PROP_VIP_FLAG] = "true"
        if vip_result.priority:
            properties["hs_ticket_priority"] = vip_result.priority

    written = False
    if not dry_run and properties:
        client.update_ticket_properties(ticket.id, properties)
        if vip_result.is_vip and vip_result.note:
            client.add_note(ticket.id, vip_result.note)
        if dedup.is_repeat and dedup.repeat_of:
            client.add_note(
                ticket.id,
                f"[support-admin] {dedup.link_kind} repeat link → ticket "
                f"{dedup.repeat_of} (score={dedup.score:.3f}). {dedup.reason}",
            )
        written = True

    # Persist annotations locally (always; cheap and useful for backfill).
    with db.connect(db_path) as conn:
        db.upsert_annotation(
            conn,
            hs_id=ticket.id,
            jw_repeat_of=dedup.repeat_of,
            jw_dedup_score=dedup.score if dedup.is_repeat else None,
            jw_vip_flag=vip_result.is_vip,
            jw_link_kind=dedup.link_kind,
        )

    return TicketOutcome(
        ticket_id=ticket.id,
        company_id=ticket.company_id,
        dedup=dedup,
        vip=vip_result,
        contract_status=contract.status,
        written=written,
    )


def process_batch(
    tickets: Iterable[HSTicket],
    client: HubSpotClient,
    *,
    dry_run: bool = False,
    db_path: Path | None = None,
) -> list[TicketOutcome]:
    embedder = get_embedder()
    vip_list = load_vip_list()
    outcomes: list[TicketOutcome] = []
    for t in tickets:
        try:
            outcomes.append(
                process_ticket(
                    t, client, embedder=embedder, vip_list=vip_list,
                    db_path=db_path, dry_run=dry_run,
                )
            )
        except Exception:
            log.exception("Failed processing ticket %s", t.id)
    return outcomes


def run_once(*, dry_run: bool = False, limit: int = 100) -> list[TicketOutcome]:
    settings = get_settings()
    with HubSpotClient() as client:
        tickets = client.list_tickets(limit=limit)
        log.info("Fetched %d tickets (mock_mode=%s)", len(tickets), client.mock_mode)
        return process_batch(tickets, client, dry_run=dry_run, db_path=settings.db_path)


def run_loop(*, dry_run: bool = False, interval_seconds: int | None = None) -> None:
    settings = get_settings()
    interval = interval_seconds if interval_seconds is not None else settings.poll_interval_seconds
    log.info("Starting runner loop; interval=%ds dry_run=%s", interval, dry_run)
    last_seen = datetime.now(timezone.utc)
    with HubSpotClient() as client:
        while True:
            try:
                if client.mock_mode:
                    tickets = client.list_tickets()
                else:
                    tickets = client.search_tickets_modified_since(last_seen)
                if tickets:
                    log.info("Processing %d tickets", len(tickets))
                    process_batch(tickets, client, dry_run=dry_run, db_path=settings.db_path)
                last_seen = datetime.now(timezone.utc)
            except Exception:
                log.exception("Runner iteration failed")
            time.sleep(interval)


def backfill(*, dry_run: bool = False, limit: int = 1000) -> list[TicketOutcome]:
    """Pull recent tickets and run rules; useful for historical bootstrap."""
    return run_once(dry_run=dry_run, limit=limit)
