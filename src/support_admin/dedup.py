"""Repeat-issue / dedup detection — implements the JAZ-67 decision.

Rules:
- **Hard link**: same ``error_code`` field exact match → ``link_kind="hard"``.
- **Soft link**: subject cosine similarity ≥ 0.85 with another ticket from the
  same ``hubspot_company_id`` within the last 30 days → ``link_kind="soft"``.

The first-matching candidate (highest score) wins. Hard links always beat soft.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .config import get_settings
from .embeddings import Embedder, cosine_sim, get_embedder
from .hubspot_client import HSTicket

log = logging.getLogger(__name__)


@dataclass
class DedupResult:
    is_repeat: bool
    repeat_of: str | None = None  # hs_id of the original ticket
    score: float = 0.0
    link_kind: str | None = None  # "hard" | "soft" | None
    reason: str = ""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def detect_repeat(
    ticket: HSTicket,
    *,
    db_path: Path | None = None,
    embedder: Embedder | None = None,
    sim_threshold: float | None = None,
    window_days: int | None = None,
    persist_ticket: bool = True,
) -> DedupResult:
    """Return a :class:`DedupResult` for ``ticket``.

    Side effects: by default the ticket is upserted into the SQLite store so
    future tickets can compare against it. Pass ``persist_ticket=False`` to
    skip persistence (useful for dry-run analysis).
    """

    settings = get_settings()
    db_path = Path(db_path) if db_path is not None else settings.db_path
    embedder = embedder or get_embedder()
    sim_threshold = sim_threshold if sim_threshold is not None else settings.repeat_sim_threshold
    window_days = window_days if window_days is not None else settings.repeat_window_days

    # Phase 1: persist + read candidates (short DB transaction; release before
    # we hand control to the embedder, which opens its own connection).
    hard_match: str | None = None
    candidate_rows: list = []

    with db.connect(db_path) as conn:
        if persist_ticket:
            db.upsert_ticket(
                conn,
                hs_id=ticket.id,
                subject=ticket.subject,
                content=ticket.content,
                error_code=ticket.error_code,
                company_id=ticket.company_id,
                created_at=ticket.created_at,
                raw=ticket.properties,
            )
        if ticket.error_code:
            rows = db.fetch_tickets_by_error_code(
                conn, ticket.error_code, exclude_hs_id=ticket.id
            )
            if rows:
                hard_match = rows[0]["hs_id"]
        if hard_match is None and ticket.company_id:
            ticket_dt = _parse_dt(ticket.created_at) or datetime.now(timezone.utc)
            since_iso = (ticket_dt - timedelta(days=window_days)).isoformat()
            candidate_rows = [
                {"hs_id": r["hs_id"], "subject": r["subject"]}
                for r in db.fetch_company_tickets_since(
                    conn, ticket.company_id, since_iso, exclude_hs_id=ticket.id
                )
            ]

    # 1. Hard link wins.
    if hard_match is not None:
        return DedupResult(
            is_repeat=True,
            repeat_of=hard_match,
            score=1.0,
            link_kind="hard",
            reason=f"error_code exact match: {ticket.error_code}",
        )

    # 2. Soft link: same company within window, subject cosine sim ≥ threshold.
    if not ticket.company_id:
        return DedupResult(is_repeat=False, reason="no company_id")
    if not candidate_rows:
        return DedupResult(is_repeat=False, reason="no recent tickets for company")

    candidate_subjects = [r["subject"] or "" for r in candidate_rows]
    all_subjects = [ticket.subject or ""] + candidate_subjects
    vectors = embedder.embed_many(all_subjects)
    new_vec = vectors[0]
    cand_vecs = vectors[1:]

    best_idx = -1
    best_score = -1.0
    for i, cv in enumerate(cand_vecs):
        score = cosine_sim(new_vec, cv)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx >= 0 and best_score >= sim_threshold:
        original = candidate_rows[best_idx]
        return DedupResult(
            is_repeat=True,
            repeat_of=original["hs_id"],
            score=float(best_score),
            link_kind="soft",
            reason=(
                f"subject cosine={best_score:.3f} ≥ {sim_threshold:.2f} "
                f"within {window_days}d, same company {ticket.company_id}"
            ),
        )

    return DedupResult(
        is_repeat=False,
        score=float(max(best_score, 0.0)),
        reason=f"best cosine={max(best_score, 0.0):.3f} < {sim_threshold:.2f}",
    )
