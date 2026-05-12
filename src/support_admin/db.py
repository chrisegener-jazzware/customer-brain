"""SQLite store for embedding cache and dedup/annotation state.

Schema:
    embeddings(text_hash TEXT PK, model TEXT, dim INTEGER, vector BLOB, created_at TEXT)
    tickets(hs_id TEXT PK, subject TEXT, content TEXT, error_code TEXT,
            company_id TEXT, created_at TEXT, raw_json TEXT)
    annotations(hs_id TEXT PK, jw_repeat_of TEXT, jw_dedup_score REAL,
                jw_vip_flag INTEGER, jw_link_kind TEXT, updated_at TEXT)
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    text_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    hs_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    company_id TEXT,
    created_at TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_company_created
    ON tickets(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_error_code
    ON tickets(error_code) WHERE error_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS annotations (
    hs_id TEXT PRIMARY KEY,
    jw_repeat_of TEXT,
    jw_dedup_score REAL,
    jw_vip_flag INTEGER NOT NULL DEFAULT 0,
    jw_link_kind TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---- embedding cache ----------------------------------------------------


def get_cached_embedding(
    conn: sqlite3.Connection, text_hash: str, model: str
) -> np.ndarray | None:
    row = conn.execute(
        "SELECT dim, vector FROM embeddings WHERE text_hash = ? AND model = ?",
        (text_hash, model),
    ).fetchone()
    if row is None:
        return None
    arr = np.frombuffer(row["vector"], dtype=np.float32)
    if arr.size != row["dim"]:
        return None
    return arr.copy()


def put_cached_embedding(
    conn: sqlite3.Connection, text_hash: str, model: str, vector: np.ndarray
) -> None:
    arr = np.asarray(vector, dtype=np.float32)
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (text_hash, model, dim, vector, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (text_hash, model, int(arr.size), arr.tobytes(), _now_iso()),
    )


# ---- ticket store -------------------------------------------------------


def upsert_ticket(
    conn: sqlite3.Connection,
    *,
    hs_id: str,
    subject: str,
    content: str,
    error_code: str | None,
    company_id: str | None,
    created_at: str | None,
    raw: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO tickets (hs_id, subject, content, error_code, company_id, created_at, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(hs_id) DO UPDATE SET "
        "    subject=excluded.subject, content=excluded.content, "
        "    error_code=excluded.error_code, company_id=excluded.company_id, "
        "    created_at=excluded.created_at, raw_json=excluded.raw_json",
        (
            hs_id,
            subject or "",
            content or "",
            error_code,
            company_id,
            created_at,
            json.dumps(raw) if raw is not None else None,
        ),
    )


def fetch_company_tickets_since(
    conn: sqlite3.Connection,
    company_id: str,
    since_iso: str,
    *,
    exclude_hs_id: str | None = None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT hs_id, subject, content, error_code, company_id, created_at "
        "FROM tickets WHERE company_id = ? AND created_at >= ?"
    )
    params: list = [company_id, since_iso]
    if exclude_hs_id is not None:
        sql += " AND hs_id != ?"
        params.append(exclude_hs_id)
    sql += " ORDER BY created_at DESC"
    return list(conn.execute(sql, params).fetchall())


def fetch_tickets_by_error_code(
    conn: sqlite3.Connection,
    error_code: str,
    *,
    exclude_hs_id: str | None = None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT hs_id, subject, content, error_code, company_id, created_at "
        "FROM tickets WHERE error_code = ?"
    )
    params: list = [error_code]
    if exclude_hs_id is not None:
        sql += " AND hs_id != ?"
        params.append(exclude_hs_id)
    sql += " ORDER BY created_at DESC"
    return list(conn.execute(sql, params).fetchall())


# ---- annotation store ---------------------------------------------------


def upsert_annotation(
    conn: sqlite3.Connection,
    *,
    hs_id: str,
    jw_repeat_of: str | None = None,
    jw_dedup_score: float | None = None,
    jw_vip_flag: bool | None = None,
    jw_link_kind: str | None = None,
) -> None:
    existing = conn.execute(
        "SELECT jw_repeat_of, jw_dedup_score, jw_vip_flag, jw_link_kind "
        "FROM annotations WHERE hs_id = ?",
        (hs_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO annotations (hs_id, jw_repeat_of, jw_dedup_score, jw_vip_flag, "
            "jw_link_kind, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                hs_id,
                jw_repeat_of,
                jw_dedup_score,
                int(bool(jw_vip_flag)) if jw_vip_flag is not None else 0,
                jw_link_kind,
                _now_iso(),
            ),
        )
        return
    new_repeat = jw_repeat_of if jw_repeat_of is not None else existing["jw_repeat_of"]
    new_score = jw_dedup_score if jw_dedup_score is not None else existing["jw_dedup_score"]
    new_vip = (
        int(bool(jw_vip_flag)) if jw_vip_flag is not None else existing["jw_vip_flag"]
    )
    new_link_kind = jw_link_kind if jw_link_kind is not None else existing["jw_link_kind"]
    conn.execute(
        "UPDATE annotations SET jw_repeat_of = ?, jw_dedup_score = ?, jw_vip_flag = ?, "
        "jw_link_kind = ?, updated_at = ? WHERE hs_id = ?",
        (new_repeat, new_score, new_vip, new_link_kind, _now_iso(), hs_id),
    )


def get_annotation(conn: sqlite3.Connection, hs_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM annotations WHERE hs_id = ?", (hs_id,)
    ).fetchone()


def all_annotations(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute("SELECT * FROM annotations ORDER BY updated_at DESC").fetchall()
