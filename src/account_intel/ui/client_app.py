"""Client-facing Streamlit UI (JAZ-125). Port 8503.

Same Postgres signal store, filtered to a "what would a client see" view.
HIDES: sales data, AI risk flags, internal next-best-actions, win rate.
SHOWS: their ticket history + status, integration health (mocked),
       usage trends (mocked), open service requests, account team, QVR preview.

Demo-only "logged in as: [Company]" selector at top — no real auth.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from account_intel.ui._common import api_get, fmt_days, fmt_iso

st.set_page_config(
    page_title="Jazzware Customer Portal",
    page_icon="🏨",
    layout="wide",
)

# --- brand header -------------------------------------------------------------

st.markdown(
    """
    <div style="background:linear-gradient(90deg,#1a3a6e,#2e5cb8);
                color:white;padding:18px 24px;border-radius:6px;margin-bottom:18px;">
      <div style="font-size:0.9em;opacity:0.85;">JAZZWARE</div>
      <div style="font-size:1.7em;font-weight:600;">Customer Portal</div>
      <div style="font-size:0.9em;opacity:0.85;margin-top:4px;">
        Your hospitality middleware status, service requests, and value reporting.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- pre-seeded demo customers -----------------------------------------------

@st.cache_data(ttl=60)
def _list_demo_customers() -> list[dict]:
    # Use any company present in DB as a demo identity option
    try:
        return api_get("/companies/search", q="", limit=50)  # empty q returns nothing, see fallback
    except Exception:
        return []


# Empty-string search doesn't match; fall back to broad fragments.
@st.cache_data(ttl=60)
def _broad_customer_list() -> list[dict]:
    seen: dict[str, dict] = {}
    for frag in ["a", "e", "i", "o", "m", "h"]:
        try:
            for h in api_get("/companies/search", q=frag, limit=50):
                seen[h["id"]] = h
        except Exception:
            pass
    return sorted(seen.values(), key=lambda x: (x.get("name") or ""))


customers = _broad_customer_list()
if not customers:
    st.warning(
        "No customers in the signal store yet. Run `make seed` (or `python -m account_intel.scripts.seed_demo`) "
        "to pre-load demo accounts."
    )
    st.stop()

names = [c.get("name") or c["id"] for c in customers]
idx = st.selectbox(
    "🔐 Logged in as (demo — no real auth)",
    range(len(customers)),
    format_func=lambda i: names[i],
)
cust = customers[idx]
cid = cust["id"]

try:
    view = api_get(f"/account/{cid}")
except Exception as e:  # noqa: BLE001
    st.error(f"Could not load your account: {e}")
    st.stop()

c = view["company"]
st.markdown(f"### Welcome back, **{c['name'] or 'Customer'}** 👋")
st.caption(
    f"{c.get('industry') or 'Hospitality'} · {c.get('city') or ''} {c.get('country') or ''}"
)

# --- visible KPI row (client-safe) -------------------------------------------

tickets = view["tickets"]
open_t = [t for t in tickets if t["is_open"]]
closed_t = [t for t in tickets if not t["is_open"]]
resolved_30 = [
    t for t in closed_t if t.get("resolution_days") is not None and t["resolution_days"] <= 30
]

k1, k2, k3, k4 = st.columns(4)
k1.metric("Open service requests", len(open_t))
k2.metric("Resolved (all time)", len(closed_t))
if closed_t:
    avg_res = sum((t["resolution_days"] or 0) for t in closed_t) / max(len(closed_t), 1)
    k3.metric("Avg resolution time", f"{avg_res:.1f}d")
else:
    k3.metric("Avg resolution time", "—")
k4.metric("Active integrations", max(len(view["integrations"]), 3))  # min 3 for demo realism

st.divider()

# --- tabs ---------------------------------------------------------------------

tab_req, tab_health, tab_usage, tab_team, tab_qvr = st.tabs(
    [
        "📋 Service requests",
        "🔌 Integration health",
        "📈 Usage trends",
        "👥 Your account team",
        "📊 Quarterly Value Report",
    ]
)

with tab_req:
    st.subheader("Open service requests")
    if not open_t:
        st.success("No open service requests — everything is humming. 🎉")
    else:
        rows = [
            {
                "Subject": t["subject"] or "(no subject)",
                "Status": t["stage"] or "In progress",
                "Priority": t["priority"] or "Normal",
                "Opened": fmt_days(t["age_days"]) + " ago",
            }
            for t in open_t
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Recently resolved")
    if not closed_t:
        st.caption("No closed requests yet.")
    else:
        rows = [
            {
                "Subject": t["subject"] or "(no subject)",
                "Resolved in": fmt_days(t["resolution_days"]),
            }
            for t in closed_t[:10]
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.caption("Need to open a new request? Email support@jazzware.com or call your CSM.")

with tab_health:
    st.subheader("Integration health (last 30 days)")
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
        # Demo mock — Phase 2 feeder will replace this
        rows = [
            {
                "Integration": "Opera PMS",
                "Status": "Healthy",
                "Uptime": "99.97%",
                "Last sync": "2 min ago",
                "Errors (24h)": 0,
            },
            {
                "Integration": "Avaya PBX (TeleManager)",
                "Status": "Healthy",
                "Uptime": "99.99%",
                "Last sync": "1 min ago",
                "Errors (24h)": 0,
            },
            {
                "Integration": "Salesforce Service Cloud",
                "Status": "Degraded",
                "Uptime": "98.40%",
                "Last sync": "14 min ago",
                "Errors (24h)": 3,
            },
        ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption("Health is computed across all integrations Jazzware operates for your properties.")

with tab_usage:
    st.subheader("Monthly usage trends")
    st.caption("Demo data — Phase 2 feeder will replace with real metrics from your integrations.")
    today = datetime.utcnow().date()
    months = pd.date_range(end=today, periods=6, freq="MS")
    df = pd.DataFrame(
        {
            "Month": months,
            "PMS sync events": [184_000, 192_500, 201_800, 198_200, 215_400, 223_100],
            "PBX call records": [412_000, 408_900, 421_500, 433_200, 447_800, 459_300],
            "Guest-experience touchpoints": [38_400, 41_200, 44_800, 47_100, 49_900, 53_400],
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
        "Demo placeholders. Production view will resolve from your assigned HubSpot owner records."
    )

with tab_qvr:
    st.subheader(f"Quarterly Value Report — Q{(datetime.utcnow().month - 1)//3 + 1} {datetime.utcnow().year}")
    st.markdown(
        f"""
        **{c['name'] or 'Customer'}** is operating across **{max(len(view['integrations']), 3)} integrations** with Jazzware middleware.

        **Highlights this quarter**
        - **{len(closed_t)} service requests resolved** with avg. resolution
          { (sum((t['resolution_days'] or 0) for t in closed_t) / max(len(closed_t), 1)):.1f } days.
        - **99.8% rolling uptime** across PMS and PBX integrations.
        - **+8% YoY** in guest-experience touchpoints delivered through Jazzware.
        - Zero critical incidents in the last 90 days.

        **What's next**
        - Phase 2 integration health feed goes live next quarter — you'll see live PMS/PBX status here.
        - Self-service ticket creation lands in this portal in Q{((datetime.utcnow().month - 1)//3 + 2)}.

        _A signed PDF version of this report is delivered by your CSM each quarter._
        """
    )

st.divider()
st.caption(
    "© Jazzware. This portal is a demo. Internal staff data, sales pipeline, and AI risk "
    "assessments are intentionally hidden from this view."
)
