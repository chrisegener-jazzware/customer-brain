"""JAZ-68: Batch dedup + repeat-issue reporting against real HubSpot tickets.

Pulls recent tickets (default last 90 days, up to 500), persists a local JSON
snapshot, and produces two ranked reports:

1. **Duplicate clusters** — tickets that look like the same underlying issue.
   Combines:
     * Exact rule: same normalized subject + same company within 7 days.
     * Semantic rule: Claude clusters near-duplicates by subject + category.
2. **Top repeat-issue customers** — companies where the same root cause
   recurs >2x within 30 days (root cause derived from ticket category /
   sub-category, or Claude theme label when category is missing).

CLI: ``support-admin report --days 90 --limit 500``
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

log = logging.getLogger(__name__)

HUBSPOT_API = "https://api.hubapi.com"

# Properties we pull for the report. Aligned with the real HubSpot Tickets
# schema (probed against the production portal — Jazzware uses Salesforce-
# style custom fields suffixed `__c`).
REPORT_PROPERTIES = [
    "subject",
    "content",
    "createdate",
    "hs_lastmodifieddate",
    "hs_pipeline",
    "hs_pipeline_stage",
    "hs_ticket_priority",
    "hs_ticket_category",
    "hs_primary_company_id",
    "hs_primary_company_name",
    "account_name__c",
    "case_category__c",
    "case_sub_category__c",
    "casenumber",
]


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass
class TicketRecord:
    id: str
    subject: str
    content: str
    created_at: str
    company_id: str | None
    company_name: str | None
    category: str | None
    sub_category: str | None
    priority: str | None
    casenumber: str | None
    pipeline_stage: str | None

    @classmethod
    def from_api(cls, obj: dict[str, Any]) -> "TicketRecord":
        p = obj.get("properties") or {}
        company = p.get("hs_primary_company_name") or p.get("account_name__c")
        return cls(
            id=str(obj.get("id", "")),
            subject=(p.get("subject") or "").strip(),
            content=(p.get("content") or "").strip(),
            created_at=p.get("createdate") or "",
            company_id=p.get("hs_primary_company_id"),
            company_name=company,
            category=p.get("case_category__c") or p.get("hs_ticket_category"),
            sub_category=p.get("case_sub_category__c"),
            priority=p.get("hs_ticket_priority"),
            casenumber=p.get("casenumber"),
            pipeline_stage=p.get("hs_pipeline_stage"),
        )


def fetch_tickets(
    *,
    token: str,
    days: int = 90,
    limit: int = 500,
    page_size: int = 100,
) -> list[TicketRecord]:
    """Page through HubSpot ticket search API and collect up to ``limit`` tickets
    created within the last ``days`` days.
    """
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    out: list[TicketRecord] = []
    after: str | None = None

    with httpx.Client(
        base_url=HUBSPOT_API,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    ) as client:
        while len(out) < limit:
            body: dict[str, Any] = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "createdate",
                                "operator": "GTE",
                                "value": str(since_ms),
                            }
                        ]
                    }
                ],
                "properties": REPORT_PROPERTIES,
                "limit": min(page_size, limit - len(out)),
                "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
            }
            if after:
                body["after"] = after
            resp = client.post("/crm/v3/objects/tickets/search", json=body)
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results", [])
            if not results:
                break
            out.extend(TicketRecord.from_api(r) for r in results)
            paging = payload.get("paging", {}).get("next")
            if not paging:
                break
            after = paging.get("after")
            if not after:
                break
    log.info("Pulled %d tickets from HubSpot (window=%dd, cap=%d)", len(out), days, limit)
    return out


def save_snapshot(tickets: list[TicketRecord], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "count": len(tickets),
        "tickets": [asdict(t) for t in tickets],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    log.info("Wrote snapshot: %s (%d tickets)", path, len(tickets))
    return path


def load_snapshot(path: Path) -> list[TicketRecord]:
    raw = json.loads(path.read_text())
    return [TicketRecord(**t) for t in raw.get("tickets", [])]


# ---------------------------------------------------------------------------
# Subject normalization
# ---------------------------------------------------------------------------

# Patterns we strip when normalizing subjects — reply prefixes, salesforce
# refs, casenumbers, ticket numbers etc.
_PREFIX_RE = re.compile(
    r"^(?:\s*(?:re|fw|fwd|aw|sv|tr|action required from you)\s*:\s*)+",
    re.IGNORECASE,
)
# Salesforce thread refs look like '[ ref:!00D3i0uc3E.!500UV0n... ]' but the
# closing bracket is often missing from email subjects (truncation).
_SFREF_RE = re.compile(r"\[?\s*ref:[!\w.:]+\.?\.{0,3}\]?", re.IGNORECASE)
_CASE_RE = re.compile(r"\b(?:case|ticket|ref|#)\s*#?\s*\d{3,}\b", re.IGNORECASE)
_NUM_RE = re.compile(r"\b\d{4,}\b")
_WS_RE = re.compile(r"\s+")


def normalize_subject(subject: str) -> str:
    s = subject or ""
    # repeatedly strip leading reply prefixes (RE: RE: FW: ...)
    while True:
        new = _PREFIX_RE.sub("", s)
        if new == s:
            break
        s = new
    s = _SFREF_RE.sub("", s)
    s = _CASE_RE.sub("", s)
    s = _NUM_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip(" -–|:")
    return s.lower()


# ---------------------------------------------------------------------------
# Cluster types
# ---------------------------------------------------------------------------


@dataclass
class Cluster:
    cluster_id: str
    kind: str  # "exact" | "semantic"
    label: str  # human label / canonical subject
    ticket_ids: list[str] = field(default_factory=list)
    company_ids: list[str] = field(default_factory=list)
    company_names: list[str] = field(default_factory=list)
    score: float = 1.0  # min similarity for semantic; 1.0 for exact
    sample_subjects: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.ticket_ids)


@dataclass
class RepeatCustomer:
    company_id: str | None
    company_name: str
    theme: str  # root cause label
    count: int
    ticket_ids: list[str]
    first_seen: str
    last_seen: str


# ---------------------------------------------------------------------------
# Exact-match dedup
# ---------------------------------------------------------------------------


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_exact_duplicates(
    tickets: list[TicketRecord],
    *,
    window_days: int = 7,
) -> list[Cluster]:
    """Cluster tickets sharing (normalized subject, company) within window_days.

    Returns clusters of size ≥ 2.
    """
    by_key: dict[tuple[str, str], list[TicketRecord]] = defaultdict(list)
    for t in tickets:
        norm = normalize_subject(t.subject)
        company = t.company_id or t.company_name or ""
        if not norm or not company:
            continue
        by_key[(norm, company)].append(t)

    clusters: list[Cluster] = []
    window = timedelta(days=window_days)
    for (norm, company), group in by_key.items():
        if len(group) < 2:
            continue
        # Sort by created_at and split into sub-clusters where consecutive
        # tickets fall within window.
        group_sorted = sorted(group, key=lambda x: x.created_at or "")
        current: list[TicketRecord] = [group_sorted[0]]
        for prev, nxt in zip(group_sorted, group_sorted[1:]):
            pd = _parse_dt(prev.created_at)
            nd = _parse_dt(nxt.created_at)
            if pd and nd and (nd - pd) <= window:
                current.append(nxt)
            else:
                if len(current) >= 2:
                    clusters.append(_make_cluster("exact", norm, current))
                current = [nxt]
        if len(current) >= 2:
            clusters.append(_make_cluster("exact", norm, current))
    clusters.sort(key=lambda c: c.size, reverse=True)
    return clusters


def _make_cluster(kind: str, label: str, group: list[TicketRecord]) -> Cluster:
    cid = f"{kind}:{abs(hash((label, tuple(t.id for t in group)))) % 10_000_000:07d}"
    return Cluster(
        cluster_id=cid,
        kind=kind,
        label=label or "(no subject)",
        ticket_ids=[t.id for t in group],
        company_ids=sorted({t.company_id for t in group if t.company_id}),
        company_names=sorted({t.company_name for t in group if t.company_name}),
        sample_subjects=[t.subject for t in group[:3]],
        score=1.0 if kind == "exact" else 0.0,
    )


# ---------------------------------------------------------------------------
# Claude-based semantic clustering
# ---------------------------------------------------------------------------


SEMANTIC_PROMPT = """You are a support-ticket triage assistant.

You will receive a JSON list of HubSpot support tickets, each with:
- `id`, `subject`, `category`, `sub_category`, `company_name`

Group tickets that describe the **same underlying root cause / issue type**,
even if wording differs. Examples:
- "Phone not ringing" + "Extension 204 won't ring inbound" → same root cause.
- Two tickets about the same outage at the same hotel are the same.
- Different products (PMS vs PBX) should NOT be grouped.

Rules:
- A cluster must have ≥ 2 tickets. Singletons MUST be omitted.
- Provide a short human-readable `theme` (≤ 8 words) per cluster.
- Provide a `confidence` (0..1) — how sure you are these are the same issue.
- Only include clusters where confidence ≥ 0.6.

Return STRICT JSON with this shape (no markdown, no commentary):
{
  "clusters": [
    {
      "theme": "string",
      "confidence": 0.0,
      "ticket_ids": ["id1", "id2", ...]
    }
  ]
}
"""


def cluster_with_claude(
    tickets: list[TicketRecord],
    *,
    api_key: str,
    model: str = "claude-sonnet-4-5",
    batch_size: int = 80,
    min_confidence: float = 0.6,
) -> list[Cluster]:
    """Use Claude to find semantic clusters of near-duplicate tickets.

    We feed batches of tickets to Claude. Cross-batch clusters are then merged
    via a union-find pass on the union of returned cluster sets.
    """
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError("anthropic package not installed; pip install anthropic") from e

    client = anthropic.Anthropic(api_key=api_key)
    by_id = {t.id: t for t in tickets}
    all_raw_clusters: list[tuple[str, float, list[str]]] = []

    # Strip to minimal payload for Claude.
    def _to_payload(t: TicketRecord) -> dict[str, Any]:
        return {
            "id": t.id,
            "subject": t.subject[:300],
            "category": t.category,
            "sub_category": t.sub_category,
            "company_name": t.company_name,
        }

    for batch_start in range(0, len(tickets), batch_size):
        batch = tickets[batch_start : batch_start + batch_size]
        payload = json.dumps([_to_payload(t) for t in batch], ensure_ascii=False)
        log.info(
            "Claude semantic clustering: batch %d-%d (%d tickets)",
            batch_start,
            batch_start + len(batch),
            len(batch),
        )
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SEMANTIC_PROMPT,
                messages=[{"role": "user", "content": payload}],
            )
        except Exception as e:
            log.warning("Claude call failed for batch %d: %s", batch_start, e)
            continue
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        parsed = _extract_json(text)
        if not parsed:
            log.warning("Could not parse Claude JSON for batch %d", batch_start)
            continue
        for cl in parsed.get("clusters", []):
            ids = [str(i) for i in cl.get("ticket_ids", []) if str(i) in by_id]
            conf = float(cl.get("confidence", 0.0))
            theme = str(cl.get("theme", "")).strip() or "(unlabeled)"
            if len(ids) >= 2 and conf >= min_confidence:
                all_raw_clusters.append((theme, conf, ids))

    # Union-find merge across batches.
    parent: dict[str, str] = {tid: tid for tid in by_id}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    theme_for_root: dict[str, tuple[str, float]] = {}
    for theme, conf, ids in all_raw_clusters:
        head = ids[0]
        for tid in ids[1:]:
            union(head, tid)
        root = find(head)
        prev = theme_for_root.get(root)
        if prev is None or conf > prev[1]:
            theme_for_root[root] = (theme, conf)

    groups: dict[str, list[str]] = defaultdict(list)
    seen = {tid for _, _, ids in all_raw_clusters for tid in ids}
    for tid in seen:
        groups[find(tid)].append(tid)

    clusters: list[Cluster] = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        theme, conf = theme_for_root.get(root, ("(unlabeled)", 0.0))
        group = [by_id[m] for m in members]
        c = _make_cluster("semantic", theme, group)
        c.score = conf
        clusters.append(c)
    clusters.sort(key=lambda c: (c.size, c.score), reverse=True)
    return clusters


def _extract_json(text: str) -> dict[str, Any] | None:
    """Tolerant JSON extractor — handles ```json fences and stray prose."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fence
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find the first { ... } block.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Repeat-issue customer detection
# ---------------------------------------------------------------------------


def find_repeat_customers(
    tickets: list[TicketRecord],
    *,
    window_days: int = 30,
    min_count: int = 3,  # ">2x" → strictly more than 2 → 3+
    semantic_clusters: Iterable[Cluster] | None = None,
) -> list[RepeatCustomer]:
    """Find (company, theme) pairs hit ≥ min_count times within window_days.

    Theme derivation order:
        1. Category / sub-category (if both present).
        2. Category alone.
        3. Semantic cluster theme (if ticket is part of one).
        4. Normalized subject.
    """
    sem_theme: dict[str, str] = {}
    if semantic_clusters:
        for cl in semantic_clusters:
            for tid in cl.ticket_ids:
                sem_theme[tid] = cl.label

    def _theme(t: TicketRecord) -> str:
        if t.category and t.sub_category:
            return f"{t.category} / {t.sub_category}"
        if t.category:
            return t.category
        if t.id in sem_theme:
            return sem_theme[t.id]
        norm = normalize_subject(t.subject)
        return norm or "(uncategorized)"

    by_key: dict[tuple[str, str, str], list[TicketRecord]] = defaultdict(list)
    for t in tickets:
        company = t.company_name or "(unknown)"
        cid = t.company_id or ""
        theme = _theme(t)
        by_key[(cid, company, theme)].append(t)

    window = timedelta(days=window_days)
    out: list[RepeatCustomer] = []
    for (cid, company, theme), group in by_key.items():
        if len(group) < min_count:
            continue
        group.sort(key=lambda x: x.created_at or "")
        # Sliding window: is there any window of size min_count fitting in
        # window_days? Simplest: check if first->last span ≤ window OR scan
        # consecutive windows of size min_count.
        ts = [_parse_dt(t.created_at) for t in group]
        ts_clean = [d for d in ts if d]
        hit = False
        for i in range(len(ts_clean) - min_count + 1):
            if (ts_clean[i + min_count - 1] - ts_clean[i]) <= window:
                hit = True
                break
        if not hit:
            continue
        out.append(
            RepeatCustomer(
                company_id=cid or None,
                company_name=company,
                theme=theme,
                count=len(group),
                ticket_ids=[t.id for t in group],
                first_seen=group[0].created_at or "",
                last_seen=group[-1].created_at or "",
            )
        )
    out.sort(key=lambda r: r.count, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_report(
    *,
    tickets: list[TicketRecord],
    exact_clusters: list[Cluster],
    semantic_clusters: list[Cluster],
    repeat_customers: list[RepeatCustomer],
    top_n: int = 10,
) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("Jazzware Support Admin — Dedup + Repeat-Issue Report (JAZ-68)")
    lines.append("=" * 78)
    lines.append(f"Tickets analyzed: {len(tickets)}")
    lines.append(f"Exact-match duplicate clusters: {len(exact_clusters)}")
    lines.append(f"Semantic (Claude) clusters: {len(semantic_clusters)}")
    lines.append(f"Repeat-issue customers: {len(repeat_customers)}")
    lines.append("")

    def _render_cluster(c: Cluster, idx: int) -> list[str]:
        rows = [
            f"  [{idx}] kind={c.kind} size={c.size} score={c.score:.2f}",
            f"      label: {c.label[:120]}",
        ]
        if c.company_names:
            rows.append(f"      companies: {', '.join(c.company_names[:3])}")
        for s in c.sample_subjects[:3]:
            rows.append(f"        · {s[:110]}")
        rows.append(f"      ticket_ids: {', '.join(c.ticket_ids[:8])}")
        return rows

    lines.append("-" * 78)
    lines.append(f"TOP {top_n} EXACT-MATCH DUPLICATE CLUSTERS")
    lines.append("-" * 78)
    if not exact_clusters:
        lines.append("  (none found)")
    for i, c in enumerate(exact_clusters[:top_n], 1):
        lines.extend(_render_cluster(c, i))
    lines.append("")

    lines.append("-" * 78)
    lines.append(f"TOP {top_n} SEMANTIC (CLAUDE) CLUSTERS")
    lines.append("-" * 78)
    if not semantic_clusters:
        lines.append("  (none found)")
    for i, c in enumerate(semantic_clusters[:top_n], 1):
        lines.extend(_render_cluster(c, i))
    lines.append("")

    lines.append("-" * 78)
    lines.append(f"TOP {top_n} REPEAT-ISSUE CUSTOMERS (>2 hits / 30d)")
    lines.append("-" * 78)
    if not repeat_customers:
        lines.append("  (none found)")
    for i, r in enumerate(repeat_customers[:top_n], 1):
        lines.append(
            f"  [{i}] {r.company_name}  ×{r.count}  theme: {r.theme}"
        )
        lines.append(
            f"      first={r.first_seen[:10]}  last={r.last_seen[:10]}  "
            f"tickets={', '.join(r.ticket_ids[:6])}"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class ReportResult:
    snapshot_path: Path
    ticket_count: int
    exact_clusters: list[Cluster]
    semantic_clusters: list[Cluster]
    repeat_customers: list[RepeatCustomer]
    text: str


def run_report(
    *,
    days: int = 90,
    limit: int = 500,
    snapshot_path: Path | None = None,
    use_snapshot: bool = False,
    skip_semantic: bool = False,
    semantic_batch_size: int = 80,
    hubspot_token: str | None = None,
    anthropic_key: str | None = None,
    anthropic_model: str = "claude-sonnet-4-5",
    top_n: int = 10,
) -> ReportResult:
    snapshot_path = Path(snapshot_path) if snapshot_path else Path("data/tickets_snapshot.json")

    if use_snapshot and snapshot_path.exists():
        tickets = load_snapshot(snapshot_path)
        log.info("Loaded %d tickets from snapshot %s", len(tickets), snapshot_path)
    else:
        token = hubspot_token or os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
        if not token:
            raise RuntimeError("HUBSPOT_PRIVATE_APP_TOKEN not set and no snapshot to reuse")
        tickets = fetch_tickets(token=token, days=days, limit=limit)
        save_snapshot(tickets, snapshot_path)

    exact = find_exact_duplicates(tickets, window_days=7)

    semantic: list[Cluster] = []
    if not skip_semantic:
        key = anthropic_key or os.environ.get("ANTHROPIC_API_KEY")
        if key:
            try:
                semantic = cluster_with_claude(
                    tickets,
                    api_key=key,
                    model=anthropic_model,
                    batch_size=semantic_batch_size,
                )
            except Exception as e:
                log.warning("Semantic clustering failed, continuing without it: %s", e)
        else:
            log.warning("ANTHROPIC_API_KEY not set — skipping semantic clustering")

    repeats = find_repeat_customers(tickets, semantic_clusters=semantic)

    text = render_report(
        tickets=tickets,
        exact_clusters=exact,
        semantic_clusters=semantic,
        repeat_customers=repeats,
        top_n=top_n,
    )
    return ReportResult(
        snapshot_path=snapshot_path,
        ticket_count=len(tickets),
        exact_clusters=exact,
        semantic_clusters=semantic,
        repeat_customers=repeats,
        text=text,
    )
