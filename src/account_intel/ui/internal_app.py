"""Internal Streamlit UI (JAZ-109). Port 8502.

Directory + search + unified per-account view with sidebar nav.
"""
from __future__ import annotations

import streamlit as st

from account_intel.ui._common import (
    RISK_COLOR,
    RISK_EMOJI,
    api_get,
    api_post,
    fmt_days,
    fmt_iso,
    fmt_money,
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
      .block-container { padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1400px; }
      h1, h2, h3, h4 { color: #0b1d3a; }
      [data-testid="stMetric"] {
        background: #ffffff; padding: 12px 16px; border-radius: 8px;
        border: 1px solid #e5e7eb;
      }
      [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0b1d3a 0%, #14315b 100%);
      }
      [data-testid="stSidebar"] * { color: #f5f7fb !important; }
      [data-testid="stSidebar"] input { color: #0b1d3a !important; }
      .acct-card {
        padding: 10px 12px; margin-bottom: 6px; border-radius: 6px;
        background: rgba(255,255,255,0.06); border-left: 4px solid #888;
        cursor: pointer;
      }
      .acct-card:hover { background: rgba(255,255,255,0.14); }
      .acct-name { font-weight: 600; font-size: 0.95em; }
      .acct-meta { font-size: 0.8em; opacity: 0.85; }
      .pill {
        display: inline-block; padding: 2px 8px; border-radius: 999px;
        font-size: 0.75em; font-weight: 600; margin-right: 4px;
      }
      .pill-red { background:#fee; color:#b91c1c; }
      .pill-yellow { background:#fef9c3; color:#854d0e; }
      .pill-green { background:#dcfce7; color:#166534; }
      .pill-gray { background:#f1f5f9; color:#475569; }
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


# ----- sidebar: directory + search --------------------------------------------
with st.sidebar:
    st.markdown("### 🔧 Account Intel")
    st.caption("Internal view")

    q = st.text_input("🔍 Search by name or domain", placeholder="McLaren, mandarin...", key="q")

    risk_filter = st.radio(
        "Risk filter",
        options=["All", "🔴 Red (70+)", "🟡 Yellow (40-69)", "🟢 Green (<40)"],
        index=0,
        horizontal=False,
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

    # Directory list
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
    st.caption("Unified per-customer view — support · sales · integrations · AI roll-up")
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

try:
    view = api_get(f"/account/{cid}")
except Exception as e:  # noqa: BLE001
    st.error(f"Failed to load account: {e}")
    st.stop()

c = view["company"]

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

# --- AI assessment banner -----------------------------------------------------
a = view.get("assessment")
if a:
    color = RISK_COLOR.get(a["risk_flag"], "#888")
    emoji = RISK_EMOJI.get(a["risk_flag"], "⚪")
    st.markdown(
        f"""
        <div style="border-left:6px solid {color}; padding:16px 20px;
                    background:#fafbfc; border-radius:6px; margin:16px 0;
                    border:1px solid #e5e7eb;">
          <div style="font-size:1.1em; margin-bottom:6px;">
            <b>{emoji} {a['risk_flag'].upper()}</b>
            · risk {a.get('risk_score') or 0:.0f}/100
            · <span style="color:#666; font-size:0.85em;">model: <code>{a.get('model') or '?'}</code></span>
          </div>
          <div style="line-height:1.6; color:#1f2937;">{a['narrative']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if a.get("next_best_actions"):
        st.markdown("##### ⚡ Next best actions")
        for nba in a["next_best_actions"]:
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

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("🎫 Open tickets", len(open_t), f"{len(tickets)} total")
k2.metric("💰 Open pipeline", fmt_money(sum((d["amount"] or 0) for d in open_d)),
          f"{len(open_d)} deals")
k3.metric("🏆 Won", fmt_money(sum((d["amount"] or 0) for d in won_d)), f"{len(won_d)} deals")
k4.metric("⛔ Stalled", len(stalled_d),
          f"${sum((d['amount'] or 0) for d in stalled_d):,.0f}" if stalled_d else "—")
k5.metric("Win rate", f"{wr:.0f}%", f"{len(lost_d)} lost")

# --- tabs ---------------------------------------------------------------------
tab_support, tab_sales, tab_integ, tab_raw = st.tabs(
    [f"🎫 Support ({len(tickets)})", f"💰 Sales ({len(deals)})", "🔌 Integrations", "📦 Raw"]
)

with tab_support:
    if not tickets:
        st.info("No tickets.")
    else:
        st.markdown(f"### Open · {len(open_t)}")
        if not open_t:
            st.caption("No open tickets.")
        for t in open_t[:50]:
            with st.container():
                c1, c2 = st.columns([6, 1])
                with c1:
                    st.markdown(
                        f"**{t['subject'] or '(no subject)'}**  \n"
                        f"<span style='color:#666;font-size:0.85em'>"
                        f"priority {t['priority'] or '—'} · "
                        f"{fmt_days(t['age_days'])} old · stage `{t['stage'] or '—'}`"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                with c2:
                    st.markdown(f"[HubSpot ↗]({t['hubspot_url']})")
        closed_t = [t for t in tickets if not t["is_open"]]
        if closed_t:
            st.markdown(f"### Closed · {len(closed_t)}")
            for t in closed_t[:25]:
                st.markdown(
                    f"- {t['subject'] or '(no subject)'} "
                    f"· resolved in {fmt_days(t['resolution_days'])} "
                    f"· [HubSpot ↗]({t['hubspot_url']})"
                )

with tab_sales:
    if not deals:
        st.info("No deals.")
    else:
        if stalled_d:
            st.warning(
                f"⚠️  **{len(stalled_d)} stalled deal(s)** · "
                f"${sum((d['amount'] or 0) for d in stalled_d):,.0f} at risk"
            )
        st.markdown(f"### Open · {len(open_d)}")
        for d in open_d[:50]:
            tag = " 🛑 **STALLED**" if d["stalled"] else ""
            st.markdown(
                f"- **{d['name'] or '(unnamed)'}** · {fmt_money(d['amount'])} "
                f"· `{d['pipeline'] or ''} → {d['stage'] or '—'}` "
                f"· {fmt_days(d['days_in_stage'])} in stage{tag} "
                f"· [HubSpot ↗]({d['hubspot_url']})"
            )

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

with tab_raw:
    st.json(view)

st.divider()
st.caption(f"Last refreshed: {fmt_iso(c.get('last_refreshed'))}")
