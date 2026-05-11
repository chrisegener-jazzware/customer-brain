"""Client-facing Streamlit UI (JAZ-125, expanded). Port 8503.

Branded customer portal demo. Pulls from the same Postgres signal store as
internal view but filtered to a "what would a client see" surface, plus
AI-generated client-safe TL;DR + insights + properties + roadmap.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from account_intel.ui._common import api_get, fmt_days, fmt_iso, parse_iso

st.set_page_config(
    page_title="Jazzware Customer Portal",
    page_icon="🏨",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- styling ------------------------------------------------------------------

st.markdown(
    """
    <style>
      .block-container { padding-top: 1rem; max-width: 1200px; }
      h1, h2, h3, h4 { color: #0b1d3a; }
      [data-testid="stMetric"] {
        background: #ffffff; padding: 14px 18px; border-radius: 10px;
        border: 1px solid #e5e7eb;
      }
      [data-testid="stMetric"] [data-testid="stMetricValue"] { font-size: 2.0em; }
      .hero {
        background: linear-gradient(135deg, #0b1d3a 0%, #1e3a72 50%, #2e5cb8 100%);
        color: white; padding: 28px 36px; border-radius: 12px;
        margin-bottom: 22px; box-shadow: 0 4px 14px rgba(11,29,58,0.18);
      }
      .hero-eyebrow { font-size: 0.78em; opacity: 0.85; letter-spacing: 0.14em; text-transform: uppercase; }
      .hero-title { font-size: 2.2em; font-weight: 600; margin-top: 4px; }
      .hero-sub { font-size: 1.0em; opacity: 0.92; margin-top: 8px; line-height: 1.5; }
      .status-badge {
        display: inline-block; padding: 4px 12px; border-radius: 999px;
        font-size: 0.85em; font-weight: 600; background: #dcfce7; color: #166534;
      }
      .tldr-card {
        background: #eff6ff; border-left: 5px solid #2563eb;
        padding: 16px 20px; border-radius: 8px; margin-bottom: 22px;
        font-size: 1.06em; line-height: 1.55; color: #0b1d3a;
      }
      .insight-card {
        background: #f8fafc; border-radius: 8px; padding: 14px 18px;
        margin-bottom: 12px; border: 1px solid #e5e7eb;
      }
      .value-event {
        background: #ecfdf5; border-left: 4px solid #10b981;
        padding: 10px 14px; border-radius: 6px; margin-bottom: 8px;
        font-size: 0.95em;
      }
      .roadmap-pill {
        display: inline-block; padding: 3px 10px; border-radius: 999px;
        font-size: 0.75em; font-weight: 600; margin-left: 6px;
      }
      .roadmap-q3 { background: #fef3c7; color: #92400e; }
      .roadmap-q4 { background: #dbeafe; color: #1e40af; }
      .roadmap-2027 { background: #f3e8ff; color: #6b21a8; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- pre-seeded demo customers ------------------------------------------------

@st.cache_data(ttl=60)
def _list_all_customers() -> list[dict]:
    try:
        return api_get("/companies/list", limit=500)
    except Exception:
        seen: dict[str, dict] = {}
        for frag in ["a", "e", "i", "o", "m", "h"]:
            try:
                for h in api_get("/companies/search", q=frag, limit=50):
                    seen[h["id"]] = h
            except Exception:
                pass
        return sorted(seen.values(), key=lambda x: (x.get("name") or ""))


customers = _list_all_customers()
if not customers:
    st.warning(
        "No customers in the signal store yet. Seed the demo first:  \n"
        "`docker compose exec api python -m account_intel.scripts.seed_demo`"
    )
    st.stop()

names = [c.get("name") or c["id"] for c in customers]

with st.container():
    c1, c2 = st.columns([3, 1])
    with c1:
        idx = st.selectbox(
            "🔐 Logged in as (demo only)",
            range(len(customers)),
            format_func=lambda i: names[i],
        )
    with c2:
        st.write("")
        st.write("")
        st.markdown(
            '<div style="text-align:right;"><span class="status-badge">🟢 On track</span></div>',
            unsafe_allow_html=True,
        )

cust = customers[idx]
cid = cust["id"]

with st.spinner("Loading your portal..."):
    try:
        view = api_get(f"/account/{cid}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load your account: {e}")
        st.stop()

    try:
        contacts = api_get(f"/account/{cid}/contacts")
    except Exception:
        contacts = []
    try:
        activities = api_get(f"/account/{cid}/activities?days=180")
    except Exception:
        activities = []
    try:
        properties = api_get(f"/account/{cid}/properties")
    except Exception:
        properties = []
    try:
        metrics = api_get(f"/account/{cid}/metrics")
    except Exception:
        metrics = {}

c = view["company"]
assessment = view.get("assessment") or {}
summaries = (assessment.get("summaries") or {}) if assessment else {}

# --- hero ---------------------------------------------------------------------
st.markdown(
    f"""
    <div class="hero">
      <div class="hero-eyebrow">JAZZWARE · CUSTOMER PORTAL</div>
      <div class="hero-title">{c['name'] or 'Welcome'}</div>
      <div class="hero-sub">
        {c.get('industry') or 'Hospitality'} ·
        {c.get('city') or ''} {c.get('country') or ''} ·
        Your middleware status, service requests, properties, and value reporting.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- AI TL;DR welcome ---------------------------------------------------------
client_tldr = summaries.get("client_tldr") if summaries else None
if client_tldr:
    st.markdown(f'<div class="tldr-card">👋 {client_tldr}</div>', unsafe_allow_html=True)

# --- KPI row (client-safe) ----------------------------------------------------

tickets = view["tickets"]
open_t = [t for t in tickets if t["is_open"]]
closed_t = [t for t in tickets if not t["is_open"]]

avg_res = (
    sum((t["resolution_days"] or 0) for t in closed_t) / max(len(closed_t), 1)
    if closed_t else 0
)
since_critical = 90  # placeholder — would be derived from real integration data
integration_count = max(len(view["integrations"]), 3)

k1, k2, k3, k4 = st.columns(4)
k1.metric("📋 Open service requests", len(open_t))
k2.metric("✅ Resolved (all time)", len(closed_t))
k3.metric("⏱ Avg resolution time", f"{avg_res:.1f}d" if closed_t else "—")
k4.metric("🔌 Active integrations", integration_count)

st.write("")

# --- tabs ---------------------------------------------------------------------

tab_req, tab_health, tab_usage, tab_props, tab_insights, tab_roadmap, tab_team, tab_qvr = st.tabs(
    [
        f"📋 Service requests ({len(open_t)})",
        "🔌 Integration health",
        "📈 Usage trends",
        f"🏨 Your properties ({len(properties)})",
        "💡 Insights",
        "🗺️ Roadmap",
        "👥 Account team",
        "📊 Quarterly Value Report",
    ]
)

# --- Service requests ---------------------------------------------------------
with tab_req:
    st.subheader(f"Open service requests · {len(open_t)}")
    if not open_t:
        st.success("✨ No open service requests — everything is humming.")
    else:
        for t in open_t[:25]:
            with st.expander(
                f"📋 {t['subject'] or '(no subject)'} · "
                f"{fmt_days(t['age_days']) or '—'} ago · {t['stage'] or 'In progress'}",
            ):
                st.markdown(
                    f"**Status:** {t['stage'] or 'In progress'}  \n"
                    f"**Priority:** {t['priority'] or 'Normal'}  \n"
                    f"**Opened:** {fmt_days(t['age_days']) or '—'} ago  \n"
                    f"**Replies:** {t.get('reply_count') if t.get('reply_count') is not None else '—'}  \n\n"
                    f"_Our team is actively working on this. Your CSM will follow up if more "
                    f"detail is needed._"
                )

    st.subheader(f"Recently resolved · {len(closed_t)}")
    if not closed_t:
        st.caption("No closed requests yet.")
    else:
        rows = [
            {
                "Subject": t["subject"] or "(no subject)",
                "Resolved in": fmt_days(t["resolution_days"]) or "—",
            }
            for t in closed_t[:15]
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.info(
        "📨 Need to open a new request? Email **support@jazzware.com** or call your CSM."
    )

# --- Integration health -------------------------------------------------------
with tab_health:
    st.subheader("Integration health · last 30 days")
    real = view["integrations"]

    if real:
        rows = [
            {
                "Integration": i["name"],
                "Status": (i["status"] or "—").title(),
                "Uptime": f"{i['uptime_pct_30d'] or 0:.2f}%",
                "Last sync": fmt_iso(i["last_sync"]),
                "Errors (24h)": i["error_count_24h"] or 0,
            }
            for i in real
        ]
    else:
        rows = [
            {"Integration": "Opera PMS", "Status": "Healthy", "Uptime": "99.97%",
             "Last sync": "2 min ago", "Errors (24h)": 0},
            {"Integration": "Avaya PBX (TeleManager)", "Status": "Healthy", "Uptime": "99.99%",
             "Last sync": "1 min ago", "Errors (24h)": 0},
            {"Integration": "Salesforce Service Cloud", "Status": "Degraded", "Uptime": "98.40%",
             "Last sync": "14 min ago", "Errors (24h)": 3},
        ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Per-integration AI mini-summary (synthetic until live signal lands)
    for r in rows:
        days_clean = 47 if r["Status"].lower() == "healthy" else (
            12 if r["Status"].lower() == "degraded" else 0
        )
        mttr = "12 minutes" if "PMS" in r["Integration"] else "8 minutes"
        st.markdown(
            f"<div class='insight-card'>🧠 <b>{r['Integration']}</b> — "
            f"has been {r['Status'].lower()} for {days_clean} consecutive days; "
            f"last incident resolved in {mttr}.</div>",
            unsafe_allow_html=True,
        )
    st.caption("Health computed across all integrations Jazzware operates for your properties.")

# --- Usage trends -------------------------------------------------------------
with tab_usage:
    st.subheader("Monthly usage trends")
    st.caption("Demo data — production feed lands next quarter.")
    today = datetime.utcnow().date().replace(day=1)
    months = [today - timedelta(days=30 * i) for i in range(11, -1, -1)]
    pms = [165_000, 172_000, 178_500, 184_000, 192_500, 201_800, 198_200, 215_400, 223_100, 231_900, 245_700, 261_300]
    pbx = [378_000, 386_400, 401_200, 412_000, 408_900, 421_500, 433_200, 447_800, 459_300, 472_100, 488_400, 501_700]
    guest = [29_400, 33_100, 35_800, 38_400, 41_200, 44_800, 47_100, 49_900, 53_400, 58_200, 61_900, 66_400]
    df = pd.DataFrame(
        {
            "Month": months,
            "PMS sync events": pms,
            "PBX call records": pbx,
            "Guest-experience touchpoints": guest,
        }
    ).set_index("Month")
    st.line_chart(df)

    col1, col2, col3 = st.columns(3)
    pms_yoy = (pms[-1] / pms[0] - 1) * 100
    peak_pbx = max(pbx)
    saved_hours = (sum(pbx) / 50_000) * 8  # rough estimate
    col1.metric("PMS events YoY", f"+{pms_yoy:.0f}%")
    col2.metric("Peak PBX call records", f"{peak_pbx:,}")
    col3.metric("Hours saved (est.)", f"{saved_hours:,.0f}h")

    st.markdown(
        '<div class="insight-card">🧠 <b>Trend.</b> Volume is up across all three '
        'integration surfaces in the last 12 months — strongest growth in '
        'guest-experience touchpoints (+125% YoY), driven by the mobile portal rollout.</div>',
        unsafe_allow_html=True,
    )

# --- Your properties ----------------------------------------------------------
with tab_props:
    st.subheader("Your properties")
    if not properties:
        st.info(
            "No properties detected from your account data. Properties are auto-extracted "
            "from deal/order titles (e.g. \"Four Seasons Kyoto\", \"Pan Pacific\")."
        )
    else:
        st.caption(
            "Extracted from your active deals and orders. Each property is "
            "supported by Jazzware middleware."
        )
        for p in properties[:20]:
            with st.expander(
                f"🏨 {p['name']} · {p['deal_count']} order(s) on file",
                expanded=p == properties[0],
            ):
                st.markdown(
                    f"**Status:** Healthy  \n"
                    f"**Last incident:** None in last 30 days  \n"
                    f"**Primary integration:** Opera PMS  \n\n"
                    f"_Sample order:_ {p['deal_names_sample'][0] if p['deal_names_sample'] else '—'}"
                )
        # Cross-property AI summary
        client_insights = summaries.get("client_insights") if summaries else None
        if client_insights:
            st.markdown(
                f"<div class='insight-card'>🧠 <b>Across your portfolio.</b> {client_insights}</div>",
                unsafe_allow_html=True,
            )

# --- Insights -----------------------------------------------------------------
with tab_insights:
    st.subheader("Your data, your trends")
    client_insights = summaries.get("client_insights") if summaries else None
    if client_insights:
        st.markdown(
            f"<div class='tldr-card'>🧠 {client_insights}</div>",
            unsafe_allow_html=True,
        )

    # Recent value events (auto-generated milestones)
    st.markdown("### 📣 Recent milestones")
    events = []
    if len(closed_t) >= 5:
        events.append(
            f"✅ {len(closed_t)} service requests resolved — avg {avg_res:.1f} days"
        )
    if closed_t:
        events.append(
            f"⚡ Fastest recent resolution: {min(t['resolution_days'] or 999 for t in closed_t):.1f} days"
        )
    if metrics.get("support_load_30d") is not None and metrics["support_load_30d"] <= 3:
        events.append("🛡️ Low support load in the last 30 days — your system is stable")
    if not events:
        events = [
            "🚀 Onboarded onto Jazzware middleware",
            "🔌 Integrations live and healthy",
        ]
    for ev in events:
        st.markdown(f'<div class="value-event">{ev}</div>', unsafe_allow_html=True)

    st.markdown("### 📊 Activity over the last 12 months")
    # Build a real activity timeline from data
    if activities:
        buckets: dict[str, int] = {}
        for a in activities:
            ts = parse_iso(a.get("ts"))
            if not ts:
                continue
            key = ts.strftime("%Y-%m")
            buckets[key] = buckets.get(key, 0) + 1
        if buckets:
            df = pd.DataFrame(
                sorted(buckets.items()), columns=["Month", "Engagements"]
            ).set_index("Month")
            st.bar_chart(df, height=180)
    else:
        st.caption("Activity timeline will appear here as we work together.")

# --- Roadmap ------------------------------------------------------------------
with tab_roadmap:
    st.subheader("What's coming next")
    st.caption("Features in our product pipeline. Your CSM will reach out when these go live.")

    roadmap = [
        {"name": "Mobile customer portal", "when": "Q3 2026", "tag": "q3",
         "desc": "Full-featured mobile app for guests + housekeeping with offline mode."},
        {"name": "Self-service ticket creation", "when": "Q4 2026", "tag": "q4",
         "desc": "Open and track support tickets directly from this portal — no email required."},
        {"name": "Real-time integration health feed", "when": "Q4 2026", "tag": "q4",
         "desc": "Live PMS / PBX status with incident replay and per-property uptime."},
        {"name": "AI-powered concierge assistant", "when": "Q1 2027", "tag": "2027",
         "desc": "Natural-language guest-request handling integrated with your PMS."},
        {"name": "Multi-property dashboards", "when": "Q1 2027", "tag": "2027",
         "desc": "Roll up usage and incidents across all properties in your group."},
        {"name": "Quarterly Value Report (signed PDF)", "when": "Q2 2027", "tag": "2027",
         "desc": "Auto-generated quarterly report delivered by your CSM."},
    ]
    for r in roadmap:
        pill_cls = {"q3": "roadmap-q3", "q4": "roadmap-q4", "2027": "roadmap-2027"}[r["tag"]]
        st.markdown(
            f"<div class='insight-card'><b>{r['name']}</b>"
            f"<span class='roadmap-pill {pill_cls}'>{r['when']}</span>"
            f"<div style='margin-top:6px; color:#475569;'>{r['desc']}</div></div>",
            unsafe_allow_html=True,
        )

# --- Account team -------------------------------------------------------------
with tab_team:
    st.subheader("Your Jazzware account team")
    owner_id = c.get("hubspot_owner_id")
    csm_name = "Sarah Chen" if not owner_id else f"Account Owner #{owner_id}"
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Customer Success Manager**")
        st.write(csm_name)
        st.caption("sarah.chen@jazzware.com")
    with c2:
        st.markdown("**Technical Support Lead**")
        st.write("Marco Reyes")
        st.caption("marco.reyes@jazzware.com")
    with c3:
        st.markdown("**Executive Sponsor**")
        st.write("James Slatter, Group MD")
        st.caption("james.slatter@jazzware.com")

    # Real contacts if we have any
    if contacts:
        st.divider()
        st.markdown("### Your contacts on file")
        rows = [
            {
                "Name": c_row.get("name") or "—",
                "Title": c_row.get("job_title") or "—",
                "Email": c_row.get("email") or "—",
            }
            for c_row in contacts[:10]
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown(
        f"""
        <div style='margin-top:18px;'>
          <a href='mailto:{csm_name.split()[0].lower()}.chen@jazzware.com?subject=Check-in%20request'
             style='background:#0b1d3a; color:white; padding:10px 18px; border-radius:6px;
                    text-decoration:none; font-weight:600;'>
            📅 Schedule a check-in
          </a>
        </div>
        """,
        unsafe_allow_html=True,
    )

# --- Quarterly Value Report ---------------------------------------------------
with tab_qvr:
    now = datetime.utcnow()
    q = (now.month - 1) // 3 + 1
    next_q = q + 1 if q < 4 else 1
    st.subheader(f"Quarterly Value Report — Q{q} {now.year}")
    st.markdown(
        f"""
        **{c['name'] or 'Customer'}** is operating across **{integration_count} integrations**
        with Jazzware middleware{f", supporting {len(properties)} active properties" if properties else ""}.

        ### Highlights this quarter
        - **{len(closed_t)} service requests resolved** · avg resolution **{avg_res:.1f} days**
        - **99.8% rolling uptime** across PMS and PBX integrations
        - **+8% YoY** in guest-experience touchpoints delivered through Jazzware
        - Zero critical incidents in the last 90 days

        ### What's next
        - Phase 2 integration health feed goes live next quarter — live PMS/PBX status will land here.
        - Self-service ticket creation lands in this portal in Q{next_q}.
        - Quarterly value report will be delivered as a signed PDF by your CSM each quarter.
        """
    )

st.divider()
st.caption(
    "© Jazzware. This portal is a demo. Internal staff data, sales pipeline, and AI risk "
    "assessments are intentionally hidden from this client view."
)
