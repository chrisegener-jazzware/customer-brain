"""Account Manager roster.

MOCK mapping of HubSpot company_id -> AM email. Replaceable later via Linear
JAZ-92 (pull real owner from HubSpot owner_id -> owner email lookup, or maintain
in a Google Sheet / Airtable).

`assign_am` resolves a company to an AM email, falling back to a default bucket.
"""
from __future__ import annotations

import hashlib
from typing import Any

# Explicit overrides — fill in real mappings when known.
MOCK_AM_ROSTER: dict[str, str] = {
    # "12345": "alex.morgan@jazzware.com",
    # "67890": "priya.shah@jazzware.com",
}

# AM pool used for deterministic fallback assignment by company id hash.
MOCK_AM_POOL: list[str] = [
    "alex.morgan@jazzware.com",
    "priya.shah@jazzware.com",
    "jordan.lee@jazzware.com",
    "sam.rivera@jazzware.com",
    "casey.brooks@jazzware.com",
]

DEFAULT_AM = "unassigned@jazzware.com"


def assign_am(company: dict[str, Any]) -> str:
    """Resolve an AM email for a company.

    Order: explicit override -> deterministic pool assignment -> default.
    Real source TBD per Linear JAZ-92.
    """
    cid = str(company.get("id") or company.get("customer_id") or "")
    if not cid:
        return DEFAULT_AM
    if cid in MOCK_AM_ROSTER:
        return MOCK_AM_ROSTER[cid]
    if not MOCK_AM_POOL:
        return DEFAULT_AM
    h = hashlib.sha256(cid.encode()).digest()
    return MOCK_AM_POOL[h[0] % len(MOCK_AM_POOL)]
