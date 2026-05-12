"""Digest renderer.

Groups HealthScore objects by account_manager and renders a per-AM markdown
digest, worst-first ranked. Output is plain markdown so it works for email,
HubSpot notes, Teams, or stdout.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from .scoring import HealthScore

_FLAG_EMOJI = {"red": "🔴", "yellow": "🟡", "green": "🟢"}


def group_by_am(scores: Iterable[HealthScore]) -> dict[str, list[HealthScore]]:
    out: dict[str, list[HealthScore]] = defaultdict(list)
    for s in scores:
        out[s.account_manager or "unassigned@jazzware.com"].append(s)
    for k in out:
        out[k].sort(key=lambda s: s.score)  # worst first
    return dict(out)


def render_am_digest(
    am_email: str, scores: list[HealthScore], generated_at: datetime | None = None
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    date_str = generated_at.strftime("%Y-%m-%d")
    red = [s for s in scores if s.flag == "red"]
    yellow = [s for s in scores if s.flag == "yellow"]
    green = [s for s in scores if s.flag == "green"]

    lines: list[str] = []
    lines.append(f"# Customer Health Digest — {date_str}")
    lines.append("")
    lines.append(f"**Account Manager:** {am_email}  ")
    lines.append(
        f"**Portfolio:** {len(scores)} customers "
        f"({len(red)} red · {len(yellow)} yellow · {len(green)} green)"
    )
    lines.append("")
    if not scores:
        lines.append("_No customers with activity in window._")
        return "\n".join(lines)

    lines.append("## Ranked by risk (worst first)")
    lines.append("")
    lines.append("| Flag | Score | Customer | Open | High-pri (30d) | Integ err |")
    lines.append("|---|---:|---|---:|---:|---:|")
    for s in scores:
        t = s.signals.get("tickets", {})
        i = s.signals.get("integrations", {})
        lines.append(
            f"| {_FLAG_EMOJI.get(s.flag, '⚪')} {s.flag} "
            f"| **{s.score}** | {s.customer_name} "
            f"| {t.get('open', 0)} | {t.get('high_priority', 0)} "
            f"| {i.get('error_rate_7d', 0):.0%} |"
        )
    lines.append("")

    for bucket_name, bucket in (("Red", red), ("Yellow", yellow), ("Green", green)):
        if not bucket:
            continue
        lines.append(f"## {_FLAG_EMOJI.get(bucket_name.lower(), '')} {bucket_name}")
        lines.append("")
        for s in bucket:
            t = s.signals.get("tickets", {})
            i = s.signals.get("integrations", {})
            failing = ", ".join(i.get("failing_integrations") or []) or "none"
            lines.append(f"### {s.customer_name} — {s.score}/100")
            lines.append("")
            lines.append(s.narrative)
            lines.append("")
            lines.append(
                f"- Tickets (30d): {t.get('total', 0)} total · "
                f"{t.get('open', 0)} open · "
                f"{t.get('high_priority', 0)} high-priority · "
                f"avg TTR {t.get('avg_time_to_close_hours') or 'n/a'}h"
            )
            lines.append(
                f"- Integrations: {i.get('integration_count', 0)} configured · "
                f"error rate {i.get('error_rate_7d', 0):.1%} · "
                f"failing: {failing}"
            )
            lines.append(
                "- Components: "
                + " · ".join(f"{k}={v}" for k, v in s.components.items())
            )
            lines.append("")

    lines.append("---")
    lines.append(
        f"_Generated {generated_at.isoformat()} · "
        "Mock pipeline (JAZ-95). Real AM mapping pending JAZ-92, "
        "real integration health pending JAZ-91._"
    )
    return "\n".join(lines)


def render_all(
    scores: Iterable[HealthScore], generated_at: datetime | None = None
) -> dict[str, str]:
    """Return {am_email: markdown_digest}."""
    grouped = group_by_am(scores)
    return {am: render_am_digest(am, lst, generated_at) for am, lst in grouped.items()}
