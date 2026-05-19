"""Lightweight Claude usage logger (vendored from token-counter Layer 1).

Wraps an `anthropic.Anthropic` (sync) client so every `messages.create`
call appends a row to `$TOKEN_COUNTER_DB` (SQLite). No Admin API key
needed — uses the SDK's `response.usage` object plus a local price book.

Single source of truth lives in:
    https://github.com/chrisegener-jazzware/token-counter/blob/main/instrumentation/__init__.py

Update both when prices change. Keep this file <120 lines.
"""
from __future__ import annotations

import functools
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone

# Anthropic pricing per 1M tokens (USD). Reviewed 2026-05-19.
PRICING = {
    "claude-opus-4-7":   {"in": 15.00, "out": 75.00, "cw": 18.75, "cr": 1.50},
    "claude-opus-4":     {"in": 15.00, "out": 75.00, "cw": 18.75, "cr": 1.50},
    "claude-3-opus":     {"in": 15.00, "out": 75.00, "cw": 18.75, "cr": 1.50},
    "claude-sonnet-4-5": {"in":  3.00, "out": 15.00, "cw":  3.75, "cr": 0.30},
    "claude-sonnet-4":   {"in":  3.00, "out": 15.00, "cw":  3.75, "cr": 0.30},
    "claude-3-7-sonnet": {"in":  3.00, "out": 15.00, "cw":  3.75, "cr": 0.30},
    "claude-3-5-sonnet": {"in":  3.00, "out": 15.00, "cw":  3.75, "cr": 0.30},
    "claude-haiku-4-5":  {"in":  1.00, "out":  5.00, "cw":  1.25, "cr": 0.10},
    "claude-3-5-haiku":  {"in":  0.80, "out":  4.00, "cw":  1.00, "cr": 0.08},
}


def _price(model: str) -> dict:
    if model in PRICING: return PRICING[model]
    for k, v in PRICING.items():
        if model.startswith(k): return v
    m = model.lower()
    if "opus" in m:   return PRICING["claude-opus-4-7"]
    if "sonnet" in m: return PRICING["claude-sonnet-4-5"]
    if "haiku" in m:  return PRICING["claude-haiku-4-5"]
    return PRICING["claude-opus-4-7"]


def _cost(model: str, inp: int, out: int, cw: int, cr: int) -> float:
    p = _price(model)
    return round((inp*p["in"] + out*p["out"] + cw*p["cw"] + cr*p["cr"]) / 1_000_000, 6)


def _db_path() -> str:
    return os.environ.get("TOKEN_COUNTER_DB", "/var/lib/jazzware/token_counter.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, bucket_date TEXT, project TEXT, host TEXT, model TEXT,
    request_id TEXT,
    input_tokens INTEGER, output_tokens INTEGER,
    cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER,
    cost_usd REAL, latency_ms INTEGER, stop_reason TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_call_event_bucket ON call_event(bucket_date);
CREATE INDEX IF NOT EXISTS ix_call_event_project ON call_event(project);
"""


def _ensure_schema() -> None:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with sqlite3.connect(path) as c:
        c.executescript(_SCHEMA)


def _record(project: str, model: str, response, error: str | None, latency_ms: int) -> None:
    try:
        usage = getattr(response, "usage", None) if response is not None else None
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cw  = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr  = getattr(usage, "cache_read_input_tokens", 0) or 0
        stop = getattr(response, "stop_reason", None) if response is not None else None
        rid  = getattr(response, "id", None) if response is not None else None
        now = datetime.now(timezone.utc)
        _ensure_schema()
        with sqlite3.connect(_db_path()) as c:
            c.execute(
                "INSERT INTO call_event(ts,bucket_date,project,host,model,request_id,"
                "input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,"
                "cost_usd,latency_ms,stop_reason,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.isoformat(), now.date().isoformat(), project, socket.gethostname(),
                 model, rid, inp, out, cw, cr,
                 _cost(model, inp, out, cw, cr), latency_ms, stop, error),
            )
    except Exception:                                   # noqa: BLE001 — never break the caller
        pass


def track(client, project: str | None = None):
    """Wrap an `anthropic.Anthropic` so every messages.create logs to SQLite."""
    project = project or os.environ.get("JAZZ_PROJECT") or os.path.basename(os.getcwd())
    original = client.messages.create

    @functools.wraps(original)
    def wrapped(**kw):
        t0 = time.time()
        err = None
        resp = None
        try:
            resp = original(**kw)
            return resp
        except Exception as e:                          # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            _record(project, kw.get("model", "unknown"), resp, err, int((time.time()-t0)*1000))

    client.messages.create = wrapped                    # type: ignore[method-assign]
    return client
