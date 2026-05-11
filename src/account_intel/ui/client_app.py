"""Client-facing Streamlit UI (JAZ-125). Port 8503.

Branded customer portal demo. Same Postgres signal store as internal view,
filtered to a "what would a client see" surface.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from account_intel.ui._common import api_get, fmt_days, fmt_iso

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
        background: #ffffff; padding: 12px 16px; border-radius: 8px;
        border: 1px solid #e5e7eb;
      }
      .hero {
        background: linear-gradient(135deg, #0b1d3a 0%, #1e3a72 50%, #2e5cb8 100%);
        color: white; padding: 24px 32px; border-radius: 10px;
        margin-bottom: 24px; box-shadow: 0 4px 12px rgba(11,29,58,0.15);
      }
      .hero-eyebrow { font-size: 0.8em; opacity: 0.85; letter-spacing: 0.12em; text-transform: uppercase; }
      .hero-title { font-size: 2.0em; font-weight: 600; margin-top: 4px; }
      .hero-sub { font-size: 0.95em; opacity: 0.9; margin-top: 8px; }
      .status-badge {
        display: inline-block; padding: 4px 12px; border-radius: 999px;
        font-size: 0.85em; font-weight: 600; background: #dcfce7; color: #166534;
      }
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
        # Fallback for older API without /companies/list
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

try:
    view = api_get(f"/account/{cid}")
except Exception as e:  # noqa: BLE001
    st.error(f"Could not load your account: {e}")
    st.stop()

c = view["company"]

# --- hero ---------------------------------------------------------------------
st.markdown(
    f"""
    <div class="hero">
      <div class="hero-eyebrow">JAZZWARE · CUSTOMER PORTAL</div>
      <div class="hero-title">{c['name'] or 'Welcome'}</div>
      <div class="hero-sub">
        {c.get('industry') or 'Hospitality'} ·
        {c.get('city') or ''} {c.get('country') or ''} ·
        Your middleware status, service requests, and value reporting.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- visible KPI row (client-safe) -------------------------------------------

tickets = view["tickets"]
open_t = [t for t in tickets if t["is_open"]]
closed_t = [t for t in tickets if not t["is_open"]]

avg_res = (
    sum((t["resolution_days"] or 0) for t in closed_t) / max(len(closed_t), 1)
    if closed_t else 0
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("📋 Open service requests", len(open_t))
k2.metric("✅ Resolved (all time)", len(closed_t))
k3.metric("⏱ Avg resolution time", f"{avg_res:.1f}d" if closed_t else "—")
k4.metric("🔌 Active integrations", max(len(view["integrations"]), 3))

st.write("")

# --- tabs ---------------------------------------------------------------------

tab_req, tab_health, tab_usage, tab_team, tab_qvr = st.tabs(
    [
        f"📋 Service requests ({len(open_t)})",
        "🔌 Integration health",
        "📈 Usage trends",
        "👥 Your account team",
        "📊 Quarterly Value Report",
    ]
)

with tab_req:
    st.subheader(f"Open service requests · {len(open_t)}")
    if not open_t:
        st.success("✨ No open service requests — everything is humming.")
    else:
        rows = [
            {
                "Subject": (t["subject"] or "(no subject)"),
                "Status": (t["stage"] or "In progress"),
                "Priority": (t["priority"] or "Normal"),
                "Opened": (fmt_days(t["age_days"]) or "—") + " ago",
            }
            for t in open_t
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader(f"Recently resolved · {len(closed_t)}")
    if not closed_t:
        st.caption("No closed requests yet.")
    else:
        rows = [
            {
                "Subject": t["subject"] or "(no subject)",
                "Resolved in": fmt_days(t["resolution_days"]) or "—",
            }
            for t in closed_t[:10]
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.info(
        "📨 Need to open a new request? Email **support@jazzware.com** or call your CSM."
    )

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
    st.caption("Health computed across all integrations Jazzware operates for your properties.")

with tab_usage:
    st.subheader("Monthly usage trends")
    st.caption("Demo data — production feed lands next quarter.")
    # Build months explicitly so length always matches the value arrays.
    today = datetime.utcnow().date().replace(day=1)
    months = [today - timedelta(days=30 * i) for i in range(5, -1, -1)]
    pms = [184_000, 192_500, 201_800, 198_200, 215_400, 223_100]
    pbx = [412_000, 408_900, 421_500, 433_200, 447_800, 459_300]
    guest = [38_400, 41_200, 44_800, 47_100, 49_900, 53_400]
    assert len(months) == len(pms) == len(pbx) == len(guest)
    df = pd.DataFrame(
        {
            "Month": months,
            "PMS sync events": pms,
            "PBX call records": pbx,
            "Guest-experience touchpoints": guest,
        }
    ).set_index("Month")
    st.line_chart(df)

with tab_team:
    st.subheader("Your Jazzware account team")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Customer Success Manager**")
        st.write("Sarah Chen")
        st.caption("sarah.chen@jazzware.com")
    with c2:
        st.markdown("**Technical Support Lead**")
        st.write("Marco Reyes")
        st.caption("marco.reyes@jazzware.com")
    with c3:
        st.markdown("**Executive Sponsor**")
        st.write("James Slatter, Group MD")
        st.caption("james.slatter@jazzware.com")
    st.caption(
        "Demo placeholders — production view will resolve from your assigned HubSpot owner records."
    )

with tab_qvr:
    now = datetime.utcnow()
    q = (now.month - 1) // 3 + 1
    next_q = q + 1 if q < 4 else 1
    st.subheader(f"Quarterly Value Report — Q{q} {now.year}")
    st.markdown(
        f"""
        **{c['name'] or 'Customer'}** is operating across **{max(len(view['integrations']), 3)} integrations**
        with Jazzware middleware.

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
