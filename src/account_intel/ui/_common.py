"""Shared UI helpers: API client + formatting."""
from __future__ import annotations

import os
from datetime import datetime

import httpx

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
TIMEOUT = 30.0


def api_get(path: str, **params) -> dict | list:
    r = httpx.get(f"{API_BASE}{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_post(path: str, json: dict | None = None, **params) -> dict:
    r = httpx.post(
        f"{API_BASE}{path}",
        params=params,
        json=json,
        timeout=TIMEOUT * 2,
    )
    r.raise_for_status()
    return r.json()


def fmt_money(v: float | None) -> str:
    return f"${v:,.0f}" if v else "—"


def fmt_days(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.0f}d"


def fmt_iso(s: str | None) -> str:
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


RISK_COLOR = {"red": "#d62728", "yellow": "#ff9f1c", "green": "#2ca02c"}
RISK_EMOJI = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
