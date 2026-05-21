"""Sales mode homepage. Port 8504.

Single-page dashboard for sales AMs: top opportunities by open pipeline,
quick-glance stalled count, recency of last activity. Click an account
to deep-link into the internal account page where Sales Tools tab lives.
"""
from __future__ import annotations

import streamlit as st

from account_intel.ui._common import API_BASE, api_get, fmt_money

st.set_page_config(page_title="Sales — Customer Brain", page_icon="💼", layout="wide")
st.title("💼 Sales — Top Opportunities")
st.caption("Accounts ranked by open pipeline value · click into one for AI sales tools.")

limit = st.slider("Accounts to show", min_value=10, max_value=100, value=25, step=5)
try:
    hits = api_get(f"/sales/pipeline?limit={limit}") or []
except Exception as exc:  # noqa: BLE001
    st.error(f"Pipeline load failed: {exc}")
    hits = []

if not hits:
    st.info("No open pipeline data.")
    st.stop()

# Header row
hcols = st.columns([3, 1, 2, 1, 2])
hcols[0].markdown("**Account**")
hcols[1].markdown("**Open deals**")
hcols[2].markdown("**Open value**")
hcols[3].markdown("**Stalled**")
hcols[4].markdown("**Days since last activity**")
st.divider()

for h in hits:
    cols = st.columns([3, 1, 2, 1, 2])
    name = h.get("company_name") or h["company_id"]
    cols[0].markdown(f"**{name}**  \n`{h['company_id']}`")
    cols[1].markdown(str(h["open_deals"]))
    cols[2].markdown(fmt_money(h["open_deal_value"]))
    stalled = h["stalled_deals"]
    if stalled > 0:
        cols[3].markdown(f"🔴 {stalled}")
    else:
        cols[3].markdown(f"🟢 0")
    days = h.get("days_since_last_activity")
    if days is None:
        cols[4].markdown("—")
    elif days > 14:
        cols[4].markdown(f"🟡 {days}d")
    elif days > 30:
        cols[4].markdown(f"🔴 {days}d")
    else:
        cols[4].markdown(f"🟢 {days}d")

st.divider()
st.caption(
    f"Data via `{API_BASE}/sales/pipeline`. "
    "For deep-dive analysis, open the internal account view (port 8502) and use the 💼 Sales Tools tab."
)
