"""Internal Streamlit UI (JAZ-109). Port 8502.

Search any HubSpot company → unified view (McLaren report layout).
Shows all internal signals + AI assessment + next-best-actions.
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

st.set_page_config(page_title="Jazzware Account Intel — Internal", page_icon="🔧", layout="wide")

st.title("🔧 Jazzware Account Intel — Internal")
st.caption("Unified per-customer view: support + sales + integrations + AI roll-up")

# --- search -------------------------------------------------------------------

col_search, col_btn = st.columns([5, 1])
with col_search:
    q = st.text_input("Search company by name or domain", placeholder="McLaren, mandarin oriental, ...")
with col_btn:
    st.write("")
    st.write("")
    do_search = st.button("Search", use_container_width=True)

selected_id: str | None = st.session_state.get("selected_id")

if q:
    try:
        hits = api_get("/companies/search", q=q, limit=20)
    except Exception as e:  # noqa: BLE001
        st.error(f"Search failed: {e}")
        hits = []
    if hits:
        labels = [
            f"{h['name'] or '?'} — {h['domain'] or ''}  (risk {h['risk_score'] or 0:.0f})"
            for h in hits
        ]
        idx = st.selectbox("Matching companies", range(len(hits)), format_func=lambda i: labels[i])
        if st.button("Open account view", type="primary"):
            st.session_state["selected_id"] = hits[idx]["id"]
            st.rerun()

# --- account view -------------------------------------------------------------

if selected_id:
    cid = selected_id
    col_h, col_refresh = st.columns([5, 1])

    with col_refresh:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh now", help="Trigger on-demand HubSpot pull"):
            with st.spinner("Pulling from HubSpot..."):
                try:
                    api_post(f"/account/{cid}/refresh")
                    st.success("Refreshed.")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Refresh failed: {e}")

    try:
        view = api_get(f"/account/{cid}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to load account: {e}")
        st.stop()

    c = view["company"]
    with col_h:
        st.markdown(f"## {c['name'] or c['id']}")
        st.caption(
            f"{c.get('industry') or '—'} · {c.get('country') or '—'} · "
            f"lifecycle: {c.get('lifecycle_stage') or '—'} · "
            f"[open in HubSpot]({c['hubspot_url']})"
        )

    # --- AI assessment banner -------------------------------------------------
    a = view.get("assessment")
    if a:
        color = RISK_COLOR.get(a["risk_flag"], "#888")
        emoji = RISK_EMOJI.get(a["risk_flag"], "⚪")
        st.markdown(
            f"""
            <div style="border-left:6px solid {color}; padding:12px 16px;
                        background:#f9f9f9; border-radius:4px; margin:12px 0;">
              <div style="font-size:1.2em;"><b>{emoji} {a['risk_flag'].upper()}</b>
                  · risk score {a.get('risk_score') or 0:.0f}/100
                  · model: <code>{a.get('model') or '?'}</code></div>
              <div style="margin-top:8px;">{a['narrative']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if a.get("next_best_actions"):
            st.markdown("**Next best actions**")
            for nba in a["next_best_actions"]:
                st.markdown(f"- **{nba.get('who','?')}** — {nba.get('action','?')}  \n  _{nba.get('rationale','')}_")

    # --- KPI row --------------------------------------------------------------
    tickets = view["tickets"]
    deals = view["deals"]
    open_t = [t for t in tickets if t["is_open"]]
    open_d = [d for d in deals if d["is_open"]]
    won_d = [d for d in deals if d["is_won"]]
    stalled_d = [d for d in deals if d["stalled"]]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Tickets (total)", len(tickets), f"{len(open_t)} open")
    k2.metric("Deals (total)", len(deals), f"{len(open_d)} open")
    k3.metric("Open deal value", fmt_money(sum((d["amount"] or 0) for d in open_d)))
    k4.metric("Stalled deals", len(stalled_d))
    k5.metric("Won deals", len(won_d), fmt_money(sum((d["amount"] or 0) for d in won_d)))

    # --- tabs -----------------------------------------------------------------
    tab_support, tab_sales, tab_integ, tab_raw = st.tabs(
        ["🎫 Support", "💰 Sales", "🔌 Integrations", "📦 Raw JSON"]
    )

    with tab_support:
        if not tickets:
            st.info("No tickets.")
        else:
            st.markdown(f"### Open ({len(open_t)})")
            for t in open_t[:25]:
                st.markdown(
                    f"- **{t['subject'] or '(no subject)'}** "
                    f"· pri {t['priority'] or '—'} "
                    f"· {fmt_days(t['age_days'])} old "
                    f"· stage `{t['stage'] or '—'}` "
                    f"· [HubSpot ↗]({t['hubspot_url']})"
                )
            st.markdown(f"### Closed ({len(tickets) - len(open_t)})")
            for t in [t for t in tickets if not t["is_open"]][:25]:
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
                    f"⚠️  {len(stalled_d)} stalled deal(s) — "
                    f"${sum((d['amount'] or 0) for d in stalled_d):,.0f} at risk"
                )
            st.markdown(f"### Open ({len(open_d)})")
            for d in open_d[:50]:
                tag = " 🛑 stalled" if d["stalled"] else ""
                st.markdown(
                    f"- **{d['name'] or '(unnamed)'}** {fmt_money(d['amount'])} "
                    f"· {d['pipeline'] or ''} → {d['stage'] or '—'} "
                    f"· {fmt_days(d['days_in_stage'])} in stage{tag} "
                    f"· [HubSpot ↗]({d['hubspot_url']})"
                )
            won = [d for d in deals if d["is_won"]]
            lost = [d for d in deals if not d["is_open"] and not d["is_won"]]
            wr = len(won) / (len(won) + len(lost)) * 100 if (won or lost) else 0
            st.metric("Win rate", f"{wr:.0f}%")

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

    st.caption(f"Last refreshed: {fmt_iso(c.get('last_refreshed'))}")

else:
    st.info("Search a company above to open the unified account view.")
