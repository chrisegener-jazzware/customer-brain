"""Digest renderer tests."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from health.digest import group_by_am, render_all, render_am_digest  # noqa: E402
from health.scoring import HealthScore  # noqa: E402


def _mk(name: str, score: int, flag: str, am: str) -> HealthScore:
    return HealthScore(
        customer_id=name.lower(),
        customer_name=name,
        score=score,
        flag=flag,
        narrative=f"{name} is {flag}.",
        components={"ticket_volume": 50, "escalation": 50, "open_load": 50,
                    "time_to_close": 50, "integration_health": 50},
        signals={
            "tickets": {"total": 3, "open": 1, "high_priority": 2,
                        "avg_time_to_close_hours": 12.0, "recent_30d": 3,
                        "escalation_rate": 0.67},
            "integrations": {"integration_count": 4, "error_rate_7d": 0.1,
                             "failing_integrations": []},
        },
        account_manager=am,
    )


def test_group_by_am_worst_first():
    scores = [
        _mk("A", 90, "green", "alex@x"),
        _mk("B", 30, "red", "alex@x"),
        _mk("C", 60, "yellow", "alex@x"),
        _mk("D", 80, "green", "priya@x"),
    ]
    grouped = group_by_am(scores)
    assert list(grouped.keys()) == ["alex@x", "priya@x"] or set(grouped.keys()) == {
        "alex@x", "priya@x"
    }
    assert [s.customer_name for s in grouped["alex@x"]] == ["B", "C", "A"]


def test_render_am_digest_contains_sections():
    scores = [_mk("Acme", 30, "red", "alex@x"), _mk("Beta", 80, "green", "alex@x")]
    md = render_am_digest("alex@x", sorted(scores, key=lambda s: s.score),
                          generated_at=datetime(2026, 5, 11, tzinfo=timezone.utc))
    assert "Customer Health Digest" in md
    assert "alex@x" in md
    assert "Acme" in md and "Beta" in md
    assert "🔴" in md  # red emoji rendered
    assert "Ranked by risk" in md


def test_render_all_one_file_per_am():
    scores = [_mk("A", 40, "red", "a@x"), _mk("B", 90, "green", "b@x")]
    out = render_all(scores, generated_at=datetime(2026, 5, 11, tzinfo=timezone.utc))
    assert set(out.keys()) == {"a@x", "b@x"}
    assert "Customer Health Digest" in out["a@x"]
