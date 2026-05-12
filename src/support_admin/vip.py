"""VIP routing — load VIP company list from CSV and apply ticket annotations.

CSV format (header required)::

    hubspot_company_id,name,tier
    C-100,Acme Hotels,platinum
    C-205,Beachfront Resort,gold

If the CSV does not exist, the module silently skips VIP processing.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import get_settings
from .hubspot_client import HSTicket, HubSpotClient, PROP_VIP_FLAG

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VIPEntry:
    company_id: str
    name: str = ""
    tier: str = ""


@dataclass
class VIPResult:
    is_vip: bool
    entry: VIPEntry | None = None
    note: str | None = None
    priority: str | None = None  # "HIGH" when VIP


@lru_cache(maxsize=8)
def _load_csv(path_str: str) -> dict[str, VIPEntry]:
    path = Path(path_str)
    if not path.exists():
        log.info("VIP list CSV not found at %s; skipping", path)
        return {}
    out: dict[str, VIPEntry] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("hubspot_company_id") or "").strip()
            if not cid:
                continue
            out[cid] = VIPEntry(
                company_id=cid,
                name=(row.get("name") or "").strip(),
                tier=(row.get("tier") or "").strip(),
            )
    log.info("Loaded %d VIP companies from %s", len(out), path)
    return out


def load_vip_list(path: Path | None = None) -> dict[str, VIPEntry]:
    settings = get_settings()
    target = path or settings.vip_list_path
    if target is None:
        return {}
    return _load_csv(str(target))


def reset_vip_cache() -> None:
    _load_csv.cache_clear()


def evaluate(ticket: HSTicket, *, vip_list: dict[str, VIPEntry] | None = None) -> VIPResult:
    table = vip_list if vip_list is not None else load_vip_list()
    if not table or not ticket.company_id:
        return VIPResult(is_vip=False)
    entry = table.get(ticket.company_id)
    if entry is None:
        return VIPResult(is_vip=False)
    note = f"VIP customer ({entry.tier or 'unspecified tier'}): {entry.name or entry.company_id}. " \
           f"Routing priority HIGH per VIP policy."
    return VIPResult(is_vip=True, entry=entry, note=note, priority="HIGH")


def apply(
    ticket: HSTicket,
    client: HubSpotClient,
    *,
    vip_list: dict[str, VIPEntry] | None = None,
    add_note: bool = True,
    set_priority: bool = True,
) -> VIPResult:
    """Apply VIP annotations on the HubSpot ticket via ``client``."""
    result = evaluate(ticket, vip_list=vip_list)
    if not result.is_vip:
        return result
    properties: dict[str, str] = {PROP_VIP_FLAG: "true"}
    if set_priority and result.priority:
        properties["hs_ticket_priority"] = result.priority
    client.update_ticket_properties(ticket.id, properties)
    if add_note and result.note:
        client.add_note(ticket.id, result.note)
    return result
