"""Internal Streamlit UI (JAZ-109, expanded). Port 8502.

Directory + search + unified per-account view with multi-zoom AI summaries,
contacts, activity timeline, quotes, metrics, hot signals, and sparklines.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from account_intel.ui._common import (
    RISK_COLOR,
    RISK_EMOJI,
    api_get,
    api_post,
    fmt_days,
    fmt_iso,
    fmt_money,
    parse_iso,
)

st.set_page_config(
    page_title="Jazzware Account Intel",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----- styling ----------------------------------------------------------------
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1500px; }
      h1, h2, h3, h4 { color: #0b1d3a; }
      h1 { margin-bottom: 0.1rem; }
      [data-testid="stMetric"] {
        background: #ffffff; padding: 10px 14px; border-radius: 8px;
        border: 1px solid #e5e7eb;
      }
      [data-testid="stMetric"] label { font-size: 0.8em; }
      [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0b1d3a 0%, #14315b 100%);
      }
      [data-testid="stSidebar"] * { color: #f5f7fb !important; }
      [data-testid="stSidebar"] input { color: #0b1d3a !important; }
      .acct-card {
        padding: 8px 10px; margin-bottom: 4px; border-radius: 6px;
        background: rgba(255,255,255,0.06); border-left: 4px solid #888;
      }
      .acct-card:hover { background: rgba(255,255,255,0.14); }
      .acct-name { font-weight: 600; font-size: 0.92em; }
      .acct-meta { font-size: 0.75em; opacity: 0.85; }
      .pill {
        display: inline-block; padding: 2px 8px; border-radius: 999px;
        font-size: 0.75em; font-weight: 600; margin-right: 4px;
      }
      .pill-red { background:#fee; color:#b91c1c; }
      .pill-yellow { background:#fef9c3; color:#854d0e; }
      .pill-green { background:#dcfce7; color:#166534; }
      .pill-gray { background:#f1f5f9; color:#475569; }
      .tldr {
        background: #eff6ff; border-left: 5px solid #2563eb;
        padding: 14px 18px; border-radius: 6px; margin: 8px 0 14px 0;
        font-size: 1.05em; line-height: 1.5;
      }
      .ai-sub {
        background: #f8fafc; border-left: 3px solid #94a3b8;
        padding: 10px 14px; border-radius: 6px; margin-bottom: 12px;
        font-size: 0.92em; color: #334155; line-height: 1.5;
      }
      .hot-row {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
        padding: 8px 12px; margin-bottom: 6px;
      }
      .hot-high { border-left: 4px solid #dc2626; }
      .hot-medium { border-left: 4px solid #f59e0b; }
      .hot-low { border-left: 4px solid #94a3b8; }
      .small { font-size: 0.82em; color: #64748b; }
    </style>
    """,
    unsafe_allow_html=True,
)


def risk_pill(score: float | None) -> str:
    if score is None:
        return '<span class="pill pill-gray">— no score</span>'
    if score >= 70:
        return f'<span class="pill pill-red">🔴 {score:.0f}</span>'
    if score >= 40:
        return f'<span class="pill pill-yellow">🟡 {score:.0f}</span>'
    return f'<span class="pill pill-green">🟢 {score:.0f}</span>'


def risk_border_color(score: float | None) -> str:
    if score is None:
        return "#888"
    if score >= 70:
        return "#d62728"
    if score >= 40:
        return "#ff9f1c"
    return "#2ca02c"


def ai_sub(text: str | None) -> None:
    if not text:
        return
    st.markdown(f'<div class="ai-sub">🧠 {text}</div>', unsafe_allow_html=True)


# ----- sidebar: directory + search --------------------------------------------
with st.sidebar:
    st.markdown("### 🔧 Account Intel")
    st.caption("Internal view")

    q = st.text_input("🔍 Search by name or domain", placeholder="McLaren, mandarin...", key="q")

    risk_filter = st.radio(
        "Risk filter",
        options=["All", "🔴 Red (70+)", "🟡 Yellow (40-69)", "🟢 Green (<40)"],
        index=0,
    )

    try:
        if q:
            hits = api_get("/companies/search", q=q, limit=200)
        else:
            hits = api_get("/companies/list", limit=500)
    except Exception as e:  # noqa: BLE001
        st.error(f"API unavailable: {e}")
        hits = []

    def in_filter(h):
        s = h.get("risk_score") or 0
        if risk_filter.startswith("🔴"):
            return s >= 70
        if risk_filter.startswith("🟡"):
            return 40 <= s < 70
        if risk_filter.startswith("🟢"):
            return s < 40
        return True

    hits = [h for h in hits if in_filter(h)]

    st.caption(f"**{len(hits)}** accounts")
    st.divider()

    for h in hits[:100]:
        score = h.get("risk_score") or 0
        color = risk_border_color(h.get("risk_score"))
        is_selected = st.session_state.get("selected_id") == h["id"]
        bg = "rgba(255,255,255,0.20)" if is_selected else "rgba(255,255,255,0.06)"
        st.markdown(
            f"""<div class="acct-card" style="border-left-color:{color}; background:{bg};">
                  <div class="acct-name">{h['name'] or '(unnamed)'}</div>
                  <div class="acct-meta">{h.get('domain') or '—'} · risk {score:.0f}</div>
                </div>""",
            unsafe_allow_html=True,
        )
        if st.button("Open ›", key=f"o_{h['id']}", use_container_width=True):
            st.session_state["selected_id"] = h["id"]
            st.rerun()

# ----- main pane --------------------------------------------------------------
selected_id = st.session_state.get("selected_id")

if not selected_id:
    st.title("🔧 Jazzware Account Intel")
    st.caption("Unified per-customer view — support · sales · integrations · contacts · activity · AI roll-up")
    st.divider()
    st.info("👈 Pick an account from the directory on the left, or use the search bar above it.")

    if hits:
        st.markdown("### Top risk accounts")
        cols = st.columns(3)
        for i, h in enumerate(sorted(hits, key=lambda x: -(x.get("risk_score") or 0))[:6]):
            with cols[i % 3]:
                color = risk_border_color(h.get("risk_score"))
                st.markdown(
                    f"""<div style="border-left:5px solid {color}; padding:12px 16px;
                                background:#fff; border-radius:6px; margin-bottom:12px;
                                border:1px solid #e5e7eb;">
                          <div style="font-weight:600;">{h['name'] or '(unnamed)'}</div>
                          <div style="font-size:0.85em; color:#666; margin-top:4px;">
                            {h.get('domain') or '—'}
                          </div>
                          <div style="margin-top:8px;">{risk_pill(h.get('risk_score'))}</div>
                        </div>""",
                    unsafe_allow_html=True,
                )
                if st.button("Open", key=f"top_{h['id']}", use_container_width=True):
                    st.session_state["selected_id"] = h["id"]
                    st.rerun()
    st.stop()

# --- account view -------------------------------------------------------------
cid = selected_id

with st.spinner("Loading account..."):
    try:
        view = api_get(f"/account/{cid}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to load account: {e}")
        st.stop()

c = view["company"]
assessment = view.get("assessment") or {}
summaries = (assessment.get("summaries") or {}) if assessment else {}

col_h, col_refresh = st.columns([6, 1])
with col_h:
    st.markdown(f"# {c['name'] or c['id']}")
    st.caption(
        f"{c.get('industry') or '—'} · {c.get('country') or '—'} · "
        f"lifecycle: **{c.get('lifecycle_stage') or '—'}** · "
        f"[Open in HubSpot ↗]({c['hubspot_url']})"
    )
with col_refresh:
    st.write("")
    if st.button("🔄 Refresh", use_container_width=True, help="Pull fresh data from HubSpot"):
        with st.spinner("Pulling..."):
            try:
                api_post(f"/account/{cid}/refresh")
                st.success("Refreshed.")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Refresh failed: {e}")

# --- TL;DR strip --------------------------------------------------------------
tldr = summaries.get("tldr") if summaries else None
if tldr:
    st.markdown(f'<div class="tldr">📌 <b>TL;DR.</b> {tldr}</div>', unsafe_allow_html=True)

# --- AI assessment banner -----------------------------------------------------
if assessment:
    color = RISK_COLOR.get(assessment["risk_flag"], "#888")
    emoji = RISK_EMOJI.get(assessment["risk_flag"], "⚪")
    st.markdown(
        f"""
        <div style="border-left:6px solid {color}; padding:14px 18px;
                    background:#fafbfc; border-radius:6px; margin:8px 0 14px 0;
                    border:1px solid #e5e7eb;">
          <div style="font-size:1.05em; margin-bottom:4px;">
            <b>{emoji} {assessment['risk_flag'].upper()}</b>
            · risk {assessment.get('risk_score') or 0:.0f}/100
            · <span style="color:#666; font-size:0.85em;">model:
                <code>{assessment.get('model') or '?'}</code></span>
          </div>
          <div style="line-height:1.55; color:#1f2937;">{assessment['narrative']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    drivers = summaries.get("risk_drivers") or []
    opps = summaries.get("opportunities") or []
    col_d, col_o, col_nba = st.columns(3)
    with col_d:
        with st.expander("🔻 Risk drivers", expanded=bool(drivers)):
            if drivers:
                for x in drivers:
                    st.markdown(f"- {x}")
            else:
                st.caption("—")
    with col_o:
        with st.expander("🚀 Opportunities", expanded=bool(opps)):
            if opps:
                for x in opps:
                    st.markdown(f"- {x}")
            else:
                st.caption("—")
    with col_nba:
        with st.expander("⚡ Next best actions", expanded=bool(assessment.get("next_best_actions"))):
            for nba in assessment.get("next_best_actions") or []:
                st.markdown(
                    f"- **{nba.get('who','?')}** — {nba.get('action','?')}  \n"
                    f"  *{nba.get('rationale','')}*"
                )
else:
    st.info("No AI assessment yet. Hit Refresh to compute one.")

# --- KPI row ------------------------------------------------------------------
tickets = view["tickets"]
deals = view["deals"]
open_t = [t for t in tickets if t["is_open"]]
open_d = [d for d in deals if d["is_open"]]
won_d = [d for d in deals if d["is_won"]]
stalled_d = [d for d in deals if d["stalled"]]
lost_d = [d for d in deals if not d["is_open"] and not d["is_won"]]
wr = len(won_d) / (len(won_d) + len(lost_d)) * 100 if (won_d or lost_d) else 0

# Fetch metrics + contacts + activities + hot signals (with spinners)
@st.cache_data(ttl=300, show_spinner=False)
def _load_extras(cid: str):
    out = {}
    for key, path in [
        ("metrics", f"/account/{cid}/metrics"),
        ("contacts", f"/account/{cid}/contacts"),
        ("activities", f"/account/{cid}/activities?days=90"),
        ("hot", f"/account/{cid}/hot_signals"),
        ("quotes", f"/account/{cid}/quotes"),
        ("properties", f"/account/{cid}/properties"),
    ]:
        try:
            out[key] = api_get(path)
        except Exception:  # noqa: BLE001
            out[key] = [] if key != "metrics" else {}
    return out


extras = _load_extras(cid)
metrics = extras["metrics"] or {}
contacts = extras["contacts"] or []
activities = extras["activities"] or []
hot_signals = extras["hot"] or []
quotes = extras["quotes"] or []
properties = extras["properties"] or []

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("🎫 Open tickets", len(open_t), f"of {len(tickets)} total")
k2.metric("💰 Open pipeline", fmt_money(metrics.get("open_pipeline_amount")), f"{len(open_d)} deals")
k3.metric("🏆 Won 90d", fmt_money(metrics.get("won_amount_90d")), f"{len(won_d)} total")
k4.metric("⛔ Stalled", len(stalled_d),
          f"${sum((d['amount'] or 0) for d in stalled_d):,.0f}" if stalled_d else "—")
k5.metric("Win rate (all)", f"{wr:.0f}%", f"{len(lost_d)} lost")
k6.metric("🧑‍💼 Contacts", len(contacts), f"{metrics.get('support_load_30d') or 0} tkts 30d")

# --- tabs ---------------------------------------------------------------------
tab_support, tab_sales, tab_contacts, tab_activity, tab_quotes, tab_metrics, tab_hot, tab_integ, tab_raw = st.tabs(
    [
        f"🎫 Support ({len(tickets)})",
        f"💰 Sales ({len(deals)})",
        f"🧑‍💼 Contacts ({len(contacts)})",
        f"📅 Activity ({len(activities)})",
        f"📑 Quotes ({len(quotes)})",
        "📊 Metrics",
        f"🔥 Hot signals ({len(hot_signals)})",
        "🔌 Integrations",
        "📦 Raw",
    ]
)

# --- Support ------------------------------------------------------------------
with tab_support:
    ai_sub(summaries.get("support_summary"))
    if not tickets:
        st.info("No tickets.")
    else:
        # Sparkline: tickets opened by week, last 12 weeks
        weeks = defaultdict(int)
        for t in tickets:
            ts = parse_iso(t.get("hubspot_url"))  # placeholder; we don't have a created field in DTO
        # Use age_days to bucket
        now = datetime.now(UTC)
        bucket = Counter()
        for t in tickets:
            age = t.get("age_days")
            if age is None:
                continue
            wk = int(age // 7)
            if wk <= 12:
                bucket[wk] += 1
        if bucket:
            spark = pd.DataFrame(
                [{"week_ago": k, "tickets": bucket.get(k, 0)} for k in range(12, -1, -1)]
            ).set_index("week_ago")
            st.caption("🎫 Tickets opened (by weeks-ago bucket, last 12w)")
            st.bar_chart(spark, height=140)

        # Tickets table
        rows = []
        for t in tickets:
            rows.append(
                {
                    "Subject": t["subject"] or "(no subject)",
                    "Status": "Open" if t["is_open"] else "Closed",
                    "Stage": t["stage"] or "—",
                    "Priority": t["priority"] or "—",
                    "Age": fmt_days(t["age_days"]),
                    "Resolution": fmt_days(t["resolution_days"]) if not t["is_open"] else "—",
                    "Replies": t.get("reply_count") if t.get("reply_count") is not None else "—",
                    "HubSpot": t["hubspot_url"],
                }
            )
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "HubSpot": st.column_config.LinkColumn(display_text="↗"),
            },
        )

# --- Sales --------------------------------------------------------------------
with tab_sales:
    ai_sub(summaries.get("sales_summary"))
    if not deals:
        st.info("No deals.")
    else:
        if stalled_d:
            st.warning(
                f"⚠️  **{len(stalled_d)} stalled deal(s)** · "
                f"${sum((d['amount'] or 0) for d in stalled_d):,.0f} at risk"
            )

        # Sparkline-ish: deals by stage
        stage_amt: dict[str, float] = defaultdict(float)
        for d in open_d:
            stage_amt[d.get("stage") or "—"] += d.get("amount") or 0
        if stage_amt:
            st.caption("💰 Open pipeline by stage")
            st.bar_chart(pd.Series(stage_amt), height=180)

        rows = []
        for d in deals:
            rows.append(
                {
                    "Deal": d["name"] or "(unnamed)",
                    "Amount": d["amount"] or 0,
                    "Pipeline": d["pipeline"] or "—",
                    "Stage": d["stage"] or "—",
                    "Days in stage": d["days_in_stage"] or 0,
                    "Status": (
                        "🛑 Stalled" if d["stalled"] else (
                            "🏆 Won" if d["is_won"] else (
                                "❌ Lost" if not d["is_open"] else "Open"
                            )
                        )
                    ),
                    "HubSpot": d["hubspot_url"],
                }
            )
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Amount": st.column_config.NumberColumn(format="$%.0f"),
                "HubSpot": st.column_config.LinkColumn(display_text="↗"),
            },
        )

        # Per-deal stage history
        deals_with_history = [d for d in deals if d.get("stage_history")]
        if deals_with_history:
            with st.expander(f"📜 Stage history ({len(deals_with_history)} deals)"):
                for d in deals_with_history:
                    st.markdown(f"**{d['name']}**")
                    hist_df = pd.DataFrame(d["stage_history"] or [])
                    if not hist_df.empty:
                        st.dataframe(hist_df, use_container_width=True, hide_index=True)

# --- Contacts -----------------------------------------------------------------
with tab_contacts:
    ai_sub(summaries.get("relationship_summary"))
    if not contacts:
        st.info("No contacts associated with this company in HubSpot.")
    else:
        rows = []
        for c_row in contacts:
            note = ""
            d = c_row.get("days_since_activity") or 0
            if d > 60:
                note = f"⚠️ Quiet — no activity in {d:.0f} days"
            elif d > 21:
                note = f"Slowing — {d:.0f}d since last activity"
            else:
                note = "✅ Active"
            rows.append(
                {
                    "Name": c_row.get("name") or "(unknown)",
                    "Title": c_row.get("job_title") or "—",
                    "Email": c_row.get("email") or "—",
                    "Phone": c_row.get("phone") or "—",
                    "Last activity": (c_row.get("last_activity_at") or "—")[:10],
                    "AI note": note,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# --- Activity timeline --------------------------------------------------------
with tab_activity:
    ai_sub(summaries.get("relationship_summary"))
    if not activities:
        st.info("No engagements found (HubSpot may not expose all engagement types in current scope).")
    else:
        filter_choice = st.radio(
            "Window", ["24h", "7d", "30d", "90d"], index=2, horizontal=True
        )
        days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}[filter_choice]
        cutoff = datetime.now(UTC) - timedelta(days=days)

        def _in_window(a):
            ts = parse_iso(a.get("ts"))
            return ts is None or ts >= cutoff

        filtered = [a for a in activities if _in_window(a)]
        st.caption(f"{len(filtered)} engagement(s) in last {filter_choice}")

        # Counter by kind
        kinds_count = Counter(a["kind"] for a in filtered)
        if kinds_count:
            cols = st.columns(len(kinds_count))
            for i, (k, n) in enumerate(kinds_count.items()):
                with cols[i]:
                    st.metric(k.title(), n)

        for a in filtered[:80]:
            kind_emoji = {"call": "📞", "email": "✉️", "meeting": "📅", "note": "📝"}.get(a["kind"], "·")
            st.markdown(
                f"**{kind_emoji} {a['subject'] or '(no subject)'}**  \n"
                f"<span class='small'>{a['kind']} · {a.get('direction') or ''} · {(a.get('ts') or '—')[:16]}</span>",
                unsafe_allow_html=True,
            )
            if a.get("content_preview"):
                with st.expander("View preview"):
                    st.write(a["content_preview"])

# --- Quotes -------------------------------------------------------------------
with tab_quotes:
    if not quotes:
        st.info(
            "No quotes pulled for this account. "
            "*(HubSpot quotes scope is not granted on this PAT — feeder returns empty list. "
            "Grant `crm.objects.quotes.read` to populate.)*"
        )
    else:
        rows = [
            {
                "Title": q["title"] or "—",
                "Amount": q["amount"] or 0,
                "Status": q["status"] or "—",
                "Created": (q["created"] or "—")[:10],
                "Days to sign": q.get("days_to_sign") or "—",
                "Deal id": q.get("deal_id") or "—",
            }
            for q in quotes
        ]
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            column_config={"Amount": st.column_config.NumberColumn(format="$%.0f")},
        )

# --- Metrics ------------------------------------------------------------------
with tab_metrics:
    st.subheader("Computed metrics")
    g1, g2, g3 = st.columns(3)
    with g1:
        st.metric("Open pipeline", fmt_money(metrics.get("open_pipeline_amount")))
        st.metric("Won (90d)", fmt_money(metrics.get("won_amount_90d")))
        st.metric("Lost (90d)", fmt_money(metrics.get("lost_amount_90d")))
    with g2:
        wr = metrics.get("win_rate_90d")
        st.metric("Win rate (90d)", f"{(wr or 0) * 100:.0f}%" if wr is not None else "—")
        cy = metrics.get("avg_cycle_days_won")
        st.metric("Avg cycle (won)", f"{cy:.0f}d" if cy else "—")
        st.metric("Stuck deals (>60d in stage)", metrics.get("stuck_deals_count") or 0)
    with g3:
        st.metric("Support load (30d)", metrics.get("support_load_30d") or 0)
        fr = metrics.get("first_response_avg_hours")
        st.metric("Avg first response", f"{fr:.1f}h" if fr else "—")
        st.metric("Repeat-issue clusters", metrics.get("repeat_issue_count") or 0)

    st.divider()
    da = metrics.get("days_since_last_activity")
    if da is not None:
        st.metric(
            "Last human activity",
            f"{da:.0f} days ago",
            help=metrics.get("last_human_activity_at"),
        )

    # Sister entities (property breakdown)
    if properties:
        st.markdown("### 🏨 Properties / sister entities")
        st.caption("Extracted from deal names — useful for reseller channels (e.g. McLaren).")
        prows = [
            {"Property": p["name"], "Deals": p["deal_count"], "Sample deal": p["deal_names_sample"][0] if p["deal_names_sample"] else ""}
            for p in properties
        ]
        st.dataframe(pd.DataFrame(prows), use_container_width=True, hide_index=True)

# --- Hot signals --------------------------------------------------------------
with tab_hot:
    if not hot_signals:
        st.success("🟢 No hot signals — all clear.")
    else:
        # group by severity
        for sev in ("high", "medium", "low"):
            group = [h for h in hot_signals if h["severity"] == sev]
            if not group:
                continue
            st.markdown(f"#### {sev.title()} ({len(group)})")
            for h in group:
                link = (
                    f' · <a href="{h["hubspot_url"]}" target="_blank">HubSpot ↗</a>'
                    if h.get("hubspot_url")
                    else ""
                )
                st.markdown(
                    f'<div class="hot-row hot-{sev}"><b>{h["label"]}</b> '
                    f'<span class="small">— {h.get("detail") or ""}{link}</span></div>',
                    unsafe_allow_html=True,
                )

# --- Integrations -------------------------------------------------------------
with tab_integ:
    ints = view["integrations"]
    if not ints:
        st.info("No integration signals yet — feeder is Phase 2 (schema only in Phase 1).")
    else:
        for i in ints:
            st.markdown(
                f"- **{i['name']}** — {i['status'] or '—'} "
                f"· uptime 30d {i['uptime_pct_30d'] or 0:.1f}% "
                f"· last sync {fmt_iso(i['last_sync'])} "
                f"· errors 24h {i['error_count_24h'] or 0}"
            )

# --- Raw ----------------------------------------------------------------------
with tab_raw:
    st.json({"view": view, "extras": extras})

st.divider()
st.caption(f"Last refreshed: {fmt_iso(c.get('last_refreshed'))} · model: {assessment.get('model') if assessment else '—'}")
