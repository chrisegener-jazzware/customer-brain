"""Test fixtures: mocked HubSpot tickets + companies + Anthropic response."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

NOW = datetime.now(timezone.utc)


def ticket(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "1",
        "subject": "VOIP outage",
        "stage": "closed",
        "priority": "MEDIUM",
        "createdate": (NOW - timedelta(days=5)).isoformat(),
        "closed_date": (NOW - timedelta(days=4, hours=12)).isoformat(),
    }
    base.update(overrides)
    return base


GREEN_TICKETS: list[dict[str, Any]] = [
    ticket(id="g1", priority="LOW",
           createdate=(NOW - timedelta(days=20)).isoformat(),
           closed_date=(NOW - timedelta(days=19)).isoformat()),
]

YELLOW_TICKETS: list[dict[str, Any]] = [
    ticket(id=f"y{i}", priority="MEDIUM",
           createdate=(NOW - timedelta(days=i + 1)).isoformat(),
           closed_date=(NOW - timedelta(days=i)).isoformat())
    for i in range(6)
] + [
    ticket(id="y-open", stage="open", priority="HIGH",
           createdate=(NOW - timedelta(days=2)).isoformat(),
           closed_date=None),
]

RED_TICKETS: list[dict[str, Any]] = (
    [
        ticket(id=f"r{i}", priority="HIGH", stage="open",
               createdate=(NOW - timedelta(days=i % 20 + 1)).isoformat(),
               closed_date=None)
        for i in range(12)
    ]
    + [
        ticket(id=f"ru{i}", priority="URGENT", stage="open",
               createdate=(NOW - timedelta(days=i % 10 + 1)).isoformat(),
               closed_date=None)
        for i in range(5)
    ]
    + [
        ticket(id=f"rc{i}", priority="HIGH",
               createdate=(NOW - timedelta(days=15)).isoformat(),
               closed_date=(NOW - timedelta(days=5)).isoformat())  # 10d TTR
        for i in range(4)
    ]
)


COMPANY_GREEN = {"id": "100", "name": "Acme Resort"}
COMPANY_YELLOW = {"id": "200", "name": "Beachside Inn"}
COMPANY_RED = {"id": "300", "name": "Skyline Towers"}


class FakeAnthropicMessage:
    def __init__(self, text: str) -> None:
        self.content = [type("Block", (), {"type": "text", "text": text})()]


class FakeAnthropicClient:
    """Drop-in replacement that records prompts and returns canned text."""

    def __init__(self, canned: str = "Mocked Claude narrative.") -> None:
        self.canned = canned
        self.calls: list[dict[str, Any]] = []

        class _Messages:
            def __init__(self, outer: "FakeAnthropicClient") -> None:
                self._outer = outer

            def create(self, **kwargs: Any) -> FakeAnthropicMessage:
                self._outer.calls.append(kwargs)
                return FakeAnthropicMessage(self._outer.canned)

        self.messages = _Messages(self)
