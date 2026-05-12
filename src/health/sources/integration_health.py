"""Integration health stub.

REAL DATA SOURCE: TBD via Linear JAZ-91 (log-watch-agent / Datadog / NewRelic /
HubSpot custom property). For now we return mock random-but-deterministic
signals so the rest of the pipeline can be exercised end-to-end.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass
class IntegrationHealth:
    customer_id: str
    integration_count: int
    error_rate_7d: float          # 0.0 - 1.0
    failing_integrations: list[str]
    last_successful_sync_hours_ago: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "integration_count": self.integration_count,
            "error_rate_7d": round(self.error_rate_7d, 3),
            "failing_integrations": self.failing_integrations,
            "last_successful_sync_hours_ago": round(
                self.last_successful_sync_hours_ago, 1
            ),
        }


_INTEGRATION_CATALOG = [
    "opera-pms",
    "infor-hms",
    "stayntouch",
    "mews",
    "twilio-sms",
    "cisco-cucm",
    "avaya-cm",
    "salesforce-sync",
    "hubspot-sync",
    "ringcentral",
]


def integration_health(company: dict[str, Any]) -> IntegrationHealth:
    """Return deterministic mock health based on company id hash.

    Real source TBD (Linear JAZ-91). Deterministic so tests/demos stay stable.
    """
    cid = str(company.get("id") or company.get("customer_id") or "0")
    h = hashlib.sha256(cid.encode()).digest()

    integration_count = 2 + (h[0] % 6)            # 2..7
    error_rate = (h[1] % 100) / 100.0             # 0.00..0.99
    # heavier weight to lower error rates so most are healthy
    error_rate = error_rate ** 2
    n_failing = 0
    if error_rate > 0.5:
        n_failing = 2
    elif error_rate > 0.15:
        n_failing = 1
    failing = [
        _INTEGRATION_CATALOG[(h[2 + i] % len(_INTEGRATION_CATALOG))]
        for i in range(n_failing)
    ]
    last_sync = (h[5] % 240) / 1.0                # 0..239 hours
    if error_rate < 0.05:
        last_sync = min(last_sync, 4.0)

    return IntegrationHealth(
        customer_id=cid,
        integration_count=integration_count,
        error_rate_7d=error_rate,
        failing_integrations=failing,
        last_successful_sync_hours_ago=last_sync,
    )
