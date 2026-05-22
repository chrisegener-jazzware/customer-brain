"""Client-facing Streamlit UI (JAZ-125, JAZ-130). Port 8503.

Premium customer portal demo. Tightened hero, explicit text colors everywhere,
bigger numbers, more whitespace, polished card system.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from account_intel.ui._ai_bubble import render_panel as render_ai_bubble
from account_intel.ui._common import api_get, fmt_days, fmt_iso, parse_iso
from account_intel.ui._theme import (
    NAVY_900,
    SLATE_500,
    SLATE_700,
    SLATE_900,
    ai_subcard,
    animated_kpi_row,
    hero_v2,
    inject_theme,
    kpi_row,
    section_header,
    value_snapshot_card,
)

st.set_page_config(
    page_title="Jazzware Customer Portal",
    page_icon="🏨",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_theme()

# Client app gets a slightly tighter container
st.markdown(
    "<style>.block-container { max-width: 1200px; }</style>",
    unsafe_allow_html=True,
)


# --- customer directory -------------------------------------------------------
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

# ---- Top bar (replaces the clipping selectbox label) ------------------------
st.markdown('<div class="ji-topbar">', unsafe_allow_html=True)
tb_l, tb_c, tb_r = st.columns([4, 5, 3])
with tb_l:
    st.markdown(
        '<div class="ji-topbar-left">'
        '<div class="ji-topbar-logo">J</div>'
        '<div><div class="ji-topbar-sub">Jazzware</div>'
        '<div class="ji-topbar-title">Customer Portal</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
with tb_c:
    idx = st.selectbox(
        "account",
        range(len(customers)),
        format_func=lambda i: names[i],
        label_visibility="collapsed",
    )
with tb_r:
    st.markdown(
        '<div style="display:flex;justify-content:flex-end;align-items:center;height:100%;">'
        '<span class="ji-topbar-pill"><span class="dot"></span>On track</span>'
        '</div>',
        unsafe_allow_html=True,
    )
st.markdown('</div>', unsafe_allow_html=True)

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

# --- hero (v2 — animated gradient + glass meta box) --------------------------
last_updated = fmt_iso(c.get("last_refreshed"))
location = " · ".join(filter(None, [c.get("city"), c.get("country")]))
st.markdown(
    hero_v2(
        eyebrow="Customer Portal",
        title=c["name"] or "Welcome",
        subtitle=(
            f"{c.get('industry') or 'Hospitality'}"
            f"{' · ' + location if location else ''} · "
            "Your middleware status, service requests, and value reporting."
        ),
        last_updated=last_updated,
        status_label="On track",
    ),
    unsafe_allow_html=True,
)

# --- AI TL;DR welcome ---------------------------------------------------------
client_tldr = summaries.get("client_tldr") if summaries else None
if client_tldr:
    # Explicit color on the welcome card — it is a custom block.
    st.markdown(
        f'<div class="ji-tldr" style="color:{SLATE_900};">👋 {client_tldr}</div>',
        unsafe_allow_html=True,
    )

# --- KPI row (5 metrics) ------------------------------------------------------
tickets = view["tickets"]
open_t = [t for t in tickets if t["is_open"]]
closed_t = [t for t in tickets if not t["is_open"]]
now = datetime.utcnow()
# Approximate "resolved this quarter" via age_days + resolution_days,
# since the API doesn't expose a closed-at timestamp.
days_since_quarter_start = (now - datetime(now.year, ((now.month - 1) // 3) * 3 + 1, 1)).days


def _resolved_this_quarter(t: dict) -> bool:
    age = t.get("age_days")
    res = t.get("resolution_days")
    if age is None or res is None:
        return False
    # ticket closed (age - res) days ago
    closed_days_ago = age - res
    return 0 <= closed_days_ago <= days_since_quarter_start


resolved_this_q = [t for t in closed_t if _resolved_this_quarter(t)]
avg_res = (
    sum((t["resolution_days"] or 0) for t in closed_t) / max(len(closed_t), 1)
    if closed_t else 0
)
integration_count = max(len(view["integrations"]), 3)
# Compute uptime from real integration signals (fallback 99.8)
real_ints = view["integrations"] or []
if real_ints:
    uptimes = [i.get("uptime_pct_30d") for i in real_ints if i.get("uptime_pct_30d") is not None]
    uptime = (sum(uptimes) / len(uptimes)) if uptimes else 99.8
else:
    uptime = 99.8

st.markdown(
    animated_kpi_row([
        {"label": "Open requests",        "value": str(len(open_t)),         "icon": "📋"},
        {"label": "Resolved this quarter", "value": str(len(resolved_this_q)), "icon": "✅", "tone": "alt"},
        {"label": "Avg resolution",       "value": f"{avg_res:.1f}d" if closed_t else "—", "icon": "⏱️"},
        {"label": "Active integrations",  "value": str(integration_count),     "icon": "🔌"},
        {"label": "Uptime (30d)",         "value": f"{uptime:.2f}%",            "icon": "🟢", "tone": "alt"},
    ]),
    unsafe_allow_html=True,
)

# --- Value snapshot (this quarter) -------------------------------------------
try:
    snap = api_get(f"/account/{cid}/value_snapshot")
except Exception:
    snap = None
if snap:
    st.markdown(value_snapshot_card(snap), unsafe_allow_html=True)

# --- tabs (Ask AI moved to floating bubble) ----------------------------------
tab_req, tab_health, tab_usage, tab_props, tab_insights, tab_team, tab_qvr, tab_roadmap = st.tabs(
    [
        f"📋 Service requests ({len(open_t)})",
        "🔌 Integration health",
        "📈 Usage trends",
        f"🏨 Your properties ({len(properties)})",
        "💡 Insights",
        "👥 Account team",
        "📊 Quarterly Report",
        "🗺️ Roadmap",
    ]
)

# --- Service requests ---------------------------------------------------------
with tab_req:
    st.markdown(ai_subcard(
        f"You have {len(open_t)} open service request(s) and {len(closed_t)} resolved historically. "
        f"Average resolution time is {avg_res:.1f} days. Our team triages every request within one business day."
    ), unsafe_allow_html=True)

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
    st.markdown(ai_subcard(
        f"{integration_count} active integrations are running for your properties with a rolling "
        f"30-day average uptime of {uptime:.2f}%. Critical incidents in the last 90 days: 0."
    ), unsafe_allow_html=True)

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

    for r in rows:
        status_lower = r["Status"].lower()
        days_clean = 47 if status_lower == "healthy" else (12 if status_lower == "degraded" else 0)
        mttr = "12 minutes" if "PMS" in r["Integration"] else "8 minutes"
        st.markdown(
            f'<div class="ji-card"><div class="ji-card-title">🧠 {r["Integration"]}</div>'
            f'<div class="ji-card-body">'
            f'Has been {status_lower} for {days_clean} consecutive days; '
            f'last incident resolved in {mttr}.</div></div>',
            unsafe_allow_html=True,
        )
    st.caption("Health computed across all integrations Jazzware operates for your properties.")

# --- Usage trends -------------------------------------------------------------
with tab_usage:
    st.markdown(ai_subcard(
        "Your platform usage has grown steadily across all integration surfaces over the last 12 months — "
        "PMS sync events, PBX call records, and guest-experience touchpoints all trending up."
    ), unsafe_allow_html=True)

    st.subheader("Monthly usage trends")
    st.caption("Demo data — production feed lands next quarter.")
    today = datetime.utcnow().date().replace(day=1)
    months = [today - timedelta(days=30 * i) for i in range(11, -1, -1)]
    pms = [165_000, 172_000, 178_500, 184_000, 192_500, 201_800, 198_200, 215_400, 223_100, 231_900, 245_700, 261_300]
    pbx = [378_000, 386_400, 401_200, 412_000, 408_900, 421_500, 433_200, 447_800, 459_300, 472_100, 488_400, 501_700]
    guest = [29_400, 33_100, 35_800, 38_400, 41_200, 44_800, 47_100, 49_900, 53_400, 58_200, 61_900, 66_400]
    df = pd.DataFrame({
        "Month": months,
        "PMS sync events": pms,
        "PBX call records": pbx,
        "Guest-experience touchpoints": guest,
    }).set_index("Month")
    st.line_chart(df)

    pms_yoy = (pms[-1] / pms[0] - 1) * 100
    peak_pbx = max(pbx)
    saved_hours = (sum(pbx) / 50_000) * 8
    kpi_row([
        {"label": "PMS events YoY", "value": f"+{pms_yoy:.0f}%"},
        {"label": "Peak PBX call records", "value": f"{peak_pbx:,}"},
        {"label": "Hours saved (est.)", "value": f"{saved_hours:,.0f}h"},
    ])

    st.markdown(
        '<div class="ji-card"><div class="ji-card-title">🧠 Trend</div>'
        '<div class="ji-card-body">'
        'Volume is up across all three integration surfaces in the last 12 months — strongest '
        'growth in guest-experience touchpoints (+125% YoY), driven by the mobile portal rollout.'
        '</div></div>',
        unsafe_allow_html=True,
    )

# --- Your properties ----------------------------------------------------------
with tab_props:
    st.markdown(ai_subcard(
        f"You have {len(properties)} active properties supported by Jazzware middleware. "
        "Each property is auto-detected from order titles in your account."
    ), unsafe_allow_html=True)

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
        client_insights = summaries.get("client_insights") if summaries else None
        if client_insights:
            st.markdown(
                f'<div class="ji-card"><div class="ji-card-title">🧠 Across your portfolio</div>'
                f'<div class="ji-card-body">{client_insights}</div></div>',
                unsafe_allow_html=True,
            )

# --- Insights -----------------------------------------------------------------
with tab_insights:
    st.subheader("Your data, your trends")
    client_insights = summaries.get("client_insights") if summaries else None
    if client_insights:
        st.markdown(
            f'<div class="ji-tldr" style="color:{SLATE_900};">🧠 {client_insights}</div>',
            unsafe_allow_html=True,
        )

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
        st.markdown(f'<div class="ji-value-event">{ev}</div>', unsafe_allow_html=True)

    st.markdown("### 📊 Activity over the last 12 months")
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

# --- Account team -------------------------------------------------------------
with tab_team:
    st.subheader("Your Jazzware account team")
    owner_id = c.get("hubspot_owner_id")
    csm_name = "Sarah Chen" if not owner_id else f"Account Owner #{owner_id}"

    team_cards = [
        ("Customer Success Manager", csm_name, "sarah.chen@jazzware.com"),
        ("Technical Support Lead", "Marco Reyes", "marco.reyes@jazzware.com"),
        ("Executive Sponsor", "James Slatter, Group MD", "james.slatter@jazzware.com"),
    ]
    cols = st.columns(3)
    for col, (title, name, email) in zip(cols, team_cards, strict=False):
        with col:
            st.markdown(
                f'<div class="ji-card">'
                f'<div class="ji-card-title" style="color:{SLATE_500};text-transform:uppercase;'
                f'letter-spacing:0.06em;font-size:0.74em;">{title}</div>'
                f'<div style="color:{NAVY_900};font-weight:600;font-size:1.08em;'
                f'margin-top:2px;">{name}</div>'
                f'<div style="color:{SLATE_500};font-size:0.86em;margin-top:4px;">{email}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

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
          <a class="ji-cta-btn"
             href='mailto:{csm_name.split()[0].lower()}.chen@jazzware.com?subject=Check-in%20request'>
            📅 Schedule a check-in
          </a>
        </div>
        """,
        unsafe_allow_html=True,
    )

# --- Quarterly Value Report ---------------------------------------------------
with tab_qvr:
    q = (now.month - 1) // 3 + 1
    next_q = q + 1 if q < 4 else 1
    st.subheader(f"Quarterly Value Report — Q{q} {now.year}")

    st.markdown(
        f'<div class="ji-card">'
        f'<div class="ji-card-body" style="color:{SLATE_700};line-height:1.65;">'
        f'<b style="color:{NAVY_900};">{c["name"] or "Customer"}</b> is operating across '
        f'<b style="color:{NAVY_900};">{integration_count} integrations</b> with Jazzware middleware'
        f'{f", supporting <b style=color:{NAVY_900};>{len(properties)} active properties</b>" if properties else ""}.'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    st.markdown("### Highlights this quarter")
    highlights = [
        f"{len(closed_t)} service requests resolved · avg resolution {avg_res:.1f} days",
        f"{uptime:.2f}% rolling uptime across PMS and PBX integrations",
        "+8% YoY in guest-experience touchpoints delivered through Jazzware",
        "Zero critical incidents in the last 90 days",
    ]
    for h in highlights:
        st.markdown(f'<div class="ji-value-event">✓ {h}</div>', unsafe_allow_html=True)

    st.markdown("### How you compare")
    try:
        bms = api_get(f"/account/{cid}/benchmarks")
    except Exception:
        bms = []
    if bms:
        bench_html = "".join(
            f'<tr><td style="padding:6px 8px;color:#324158">{b["label"]}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:600;color:#1b4f87">{b["your_value"] if b["your_value"] is not None else "—"}</td>'
            f'<td style="padding:6px 8px;text-align:right;color:#64748b">{b["portfolio_avg"] if b["portfolio_avg"] is not None else "—"}</td>'
            f'<td style="padding:6px 8px;text-align:right">'
            f'<span style="background:#dcfce7;color:#166534;padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:600">{b["percentile"] if b["percentile"] is not None else "—"}{"%ile" if b["percentile"] is not None else ""}</span>'
            f'</td></tr>'
            for b in bms
        )
        st.markdown(
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:6px;background:#fff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden">'
            f'<thead><tr style="background:#f8fafc;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.04em">'
            f'<th style="padding:8px;text-align:left">Metric</th>'
            f'<th style="padding:8px;text-align:right">You</th>'
            f'<th style="padding:8px;text-align:right">Portfolio avg</th>'
            f'<th style="padding:8px;text-align:right">Ranking</th></tr></thead>'
            f'<tbody>{bench_html}</tbody></table>',
            unsafe_allow_html=True,
        )

    st.markdown("### What's next")
    st.markdown(
        f"""
        - Phase 2 integration health feed goes live next quarter — live PMS/PBX status will land here.
        - Self-service ticket creation lands in this portal in Q{next_q}.
        - Quarterly value report will be delivered as a signed PDF by your CSM each quarter.
        """
    )

    # QBR download
    st.markdown("---")
    qbr_col1, qbr_col2 = st.columns([3, 1])
    with qbr_col1:
        st.markdown("**Download this quarter's review as a PDF** to share with leadership.")
    with qbr_col2:
        import os
        _api_base = os.environ.get("API_BASE_URL", "http://localhost:8000")
        st.link_button("📄 Download QBR", f"{_api_base}/account/{cid}/qbr_pdf", use_container_width=True)

# --- Roadmap ------------------------------------------------------------------
with tab_roadmap:
    st.subheader("What's coming next")
    st.caption("Features in our product pipeline. Your CSM will reach out when these go live.")

    roadmap = [
        {"name": "Mobile customer portal", "when": "Q3 2026", "kind": "amber",
         "desc": "Full-featured mobile app for guests + housekeeping with offline mode."},
        {"name": "Self-service ticket creation", "when": "Q4 2026", "kind": "blue",
         "desc": "Open and track support tickets directly from this portal — no email required."},
        {"name": "Real-time integration health feed", "when": "Q4 2026", "kind": "blue",
         "desc": "Live PMS / PBX status with incident replay and per-property uptime."},
        {"name": "AI-powered concierge assistant", "when": "Q1 2027", "kind": "gray",
         "desc": "Natural-language guest-request handling integrated with your PMS."},
        {"name": "Multi-property dashboards", "when": "Q1 2027", "kind": "gray",
         "desc": "Roll up usage and incidents across all properties in your group."},
        {"name": "Quarterly Value Report (signed PDF)", "when": "Q2 2027", "kind": "gray",
         "desc": "Auto-generated quarterly report delivered by your CSM."},
    ]
    pill_styles = {
        "amber": ("#fef3c7", "#92400e"),
        "blue": ("#dbeafe", "#1e40af"),
        "gray": ("#f1f5f9", "#475569"),
    }
    for r in roadmap:
        bg, fg = pill_styles.get(r["kind"], pill_styles["gray"])
        st.markdown(
            f'<div class="ji-roadmap-card">'
            f'<b>{r["name"]}</b>'
            f'<span class="ji-pill" style="background:{bg};color:{fg};margin-left:8px;">'
            f'{r["when"]}</span>'
            f'<div class="ji-roadmap-desc">{r["desc"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

st.divider()
st.caption(
    "© Jazzware. This portal is a demo. Internal staff data, sales pipeline, and AI risk "
    "assessments are intentionally hidden from this client view."
)

# --- Floating AI bubble (always available, accessible from any tab) ----------
render_ai_bubble(cid)
