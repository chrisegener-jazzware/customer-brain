"""Internal Streamlit UI (JAZ-109, JAZ-130). Port 8502.

Polished workflow:
  • Sidebar directory with sort toggle + filter + selected-state styling
  • Top bar: company name + risk pill + Refresh (no giant hero)
  • TL;DR strip (single blue accent line)
  • KPI row (6 metrics)
  • 2-column layout: Hot signals | AI assessment + drivers/opps
  • Tab strip with per-tab 2-3 sentence AI summary and sortable tables
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from account_intel.ui._common import (
    api_get,
    api_post,
    fmt_days,
    fmt_iso,
    fmt_money,
    parse_iso,
)
from account_intel.ui._theme import (
    AMBER,
    EMERALD,
    NAVY_900,
    RED,
    SLATE_500,
    ai_subcard,
    hot_row,
    inject_theme,
    kpi_row,
    risk_banner,
    risk_pill,
    severity_dot,
    tldr_card,
)

st.set_page_config(
    page_title="Jazzware Account Intel",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_theme()


def _risk_color(score: float | None) -> str:
    if score is None:
        return SLATE_500
    if score >= 70:
        return RED
    if score >= 40:
        return AMBER
    return EMERALD


def _ai_sub(text: str | None) -> None:
    if not text:
        return
    st.markdown(ai_subcard(text), unsafe_allow_html=True)


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

    sort_by = st.radio(
        "Sort by",
        options=["Risk score", "Name", "Last activity"],
        index=0,
        horizontal=True,
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

    if sort_by == "Risk score":
        hits.sort(key=lambda x: -(x.get("risk_score") or 0))
    elif sort_by == "Name":
        hits.sort(key=lambda x: (x.get("name") or "").lower())
    elif sort_by == "Last activity":
        hits.sort(key=lambda x: (x.get("last_refreshed") or ""), reverse=True)

    st.caption(f"**{len(hits)}** accounts")
    st.divider()

    for h in hits[:100]:
        score = h.get("risk_score") or 0
        color = _risk_color(h.get("risk_score"))
        is_selected = st.session_state.get("selected_id") == h["id"]
        sel_cls = " selected" if is_selected else ""
        st.markdown(
            f"""<div class="ji-acct-card{sel_cls}" style="border-left-color:{color};">
                  <div class="ji-acct-name">{h['name'] or '(unnamed)'}</div>
                  <div class="ji-acct-meta">{h.get('domain') or '—'} · risk {score:.0f}</div>
                </div>""",
            unsafe_allow_html=True,
        )
        if st.button("Open ›", key=f"o_{h['id']}", use_container_width=True):
            st.session_state["selected_id"] = h["id"]
            st.rerun()

# ----- main pane --------------------------------------------------------------
selected_id = st.session_state.get("selected_id")

if not selected_id:
    st.markdown("# 🔧 Jazzware Account Intel")
    st.caption(
        "Unified per-customer view — support · sales · integrations · contacts · activity · AI roll-up"
    )

    # --- Triage my book hero (JAZ-256) ----------------------------------------
    st.markdown(
        """
        <div style="
          margin:18px 0 10px 0;padding:22px 26px;border-radius:16px;
          background:linear-gradient(135deg,#0b1d3a 0%,#1e3a8a 55%,#3b82f6 100%);
          color:#fff;box-shadow:0 8px 24px rgba(15,23,42,0.25);
        ">
          <div style="display:flex;align-items:center;gap:14px;">
            <div style="font-size:1.9em;">🎯</div>
            <div style="flex:1;">
              <div style="font-size:1.35em;font-weight:700;letter-spacing:-0.01em;">Triage my book</div>
              <div style="font-size:0.92em;opacity:0.85;margin-top:2px;">
                AI ranks every account by risk, ticket pressure, silence, and stuck pipeline.
                One click. Your Monday morning in two seconds.
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    btn_col, slot_col = st.columns([1, 4])
    with btn_col:
        if st.button("🎯 Triage now", type="primary", use_container_width=True, key="triage_run"):
            st.session_state["triage_ran"] = True
    with slot_col:
        st.caption("Top 10 accounts needing attention right now.")

    if st.session_state.get("triage_ran"):
        with st.spinner("Triaging your book…"):
            try:
                triage = api_get("/triage/book", limit=10)
            except Exception as e:  # noqa: BLE001
                st.error(f"Triage failed: {e}")
                triage = []

        if not triage:
            st.info("No accounts surfaced — your book is clean. 🎉")
        else:
            st.markdown("### Ranked")
            for rank, t in enumerate(triage, start=1):
                color = _risk_color(t.get("risk_score"))
                # build a compact stats line
                stats = []
                if t.get("aged_tickets"):
                    stats.append(f"🎫 {t['aged_tickets']} aged")
                elif t.get("open_tickets"):
                    stats.append(f"🎫 {t['open_tickets']} open")
                if t.get("stuck_deals"):
                    stats.append(f"💼 {t['stuck_deals']} stuck")
                if t.get("days_since_last_activity") is not None:
                    stats.append(f"🕒 {int(t['days_since_last_activity'])}d silent")
                if t.get("open_pipeline_amount"):
                    stats.append(f"💰 {fmt_money(t['open_pipeline_amount'])} open")
                stats_line = "  ·  ".join(stats) if stats else "—"

                reasons_html = "".join(
                    f"<li style='margin:2px 0;'>{r}</li>" for r in (t.get("top_reasons") or [])
                )
                suggested = t.get("suggested_action")
                suggested_html = (
                    f"<div style='margin-top:8px;padding:8px 10px;background:#f1f5f9;border-radius:8px;"
                    f"font-size:0.88em;color:#0b1d3a;'><b>Suggested:</b> {suggested}</div>"
                    if suggested else ""
                )

                card_col, btn_col2 = st.columns([6, 1])
                with card_col:
                    st.markdown(
                        f"""
                        <div style="
                          border:1px solid #e2e8f0;border-left:5px solid {color};
                          border-radius:12px;padding:14px 18px;margin-bottom:10px;
                          background:#fff;box-shadow:0 1px 3px rgba(15,23,42,0.04);
                        ">
                          <div style="display:flex;align-items:center;gap:10px;">
                            <div style="font-size:0.78em;color:#64748b;font-weight:600;">#{rank}</div>
                            <div style="font-size:1.08em;font-weight:700;color:#0b1d3a;">
                              {t.get('company_name') or t['company_id']}
                            </div>
                            {risk_pill(t.get('risk_score'))}
                            <div style="margin-left:auto;font-size:0.78em;color:#64748b;">
                              triage score <b style="color:#0b1d3a;">{t.get('triage_score'):.0f}</b>
                            </div>
                          </div>
                          <div style="color:#475569;font-size:0.85em;margin-top:4px;">
                            {t.get('domain') or '—'}  ·  {stats_line}
                          </div>
                          <ul style="margin:8px 0 0 18px;padding:0;color:#334155;font-size:0.9em;">
                            {reasons_html}
                          </ul>
                          {suggested_html}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with btn_col2:
                    st.write("")
                    if st.button("Open ›", key=f"tri_{t['company_id']}", use_container_width=True):
                        st.session_state["selected_id"] = t["company_id"]
                        st.rerun()

    st.divider()
    st.info("👈 Or pick an account from the directory on the left.")
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

# Fetch extras early so KPI/hot-signals work
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

# JAZ-186 helper — mark a next-best-action done/dismissed via API.
def _update_nba(cid: str, idx: int, status: str) -> None:
    try:
        api_post(f"/account/{cid}/nba/update", json={"action_index": idx, "status": status})
        st.cache_data.clear()
    except Exception as exc:  # noqa: BLE001
        st.error(f"NBA update failed: {exc}")

# Alias for ease of reading in the NBA expander.
company_id = cid
metrics = extras["metrics"] or {}
contacts = extras["contacts"] or []
activities = extras["activities"] or []
hot_signals = extras["hot"] or []
quotes = extras["quotes"] or []
properties = extras["properties"] or []

# --- TOP BAR: name + risk pill + refresh --------------------------------------
top_left, top_right = st.columns([5, 1])
with top_left:
    risk_score = assessment.get("risk_score") if assessment else None
    industry = c.get("industry") or "—"
    country = c.get("country") or "—"
    lifecycle = c.get("lifecycle_stage") or "—"
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:2px;">
          <h1 style="margin:0;color:{NAVY_900};">{c['name'] or c['id']}</h1>
          {risk_pill(risk_score)}
        </div>
        <div style="color:#64748b;font-size:0.88em;margin-bottom:10px;">
          {industry} · {country} · lifecycle: <b style="color:#0b1d3a;">{lifecycle}</b> ·
          <a href="{c['hubspot_url']}" target="_blank">Open in HubSpot ↗</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
with top_right:
    st.write("")
    if st.button("🔄 Refresh", use_container_width=True, type="primary",
                 help="Pull fresh data from HubSpot"):
        with st.spinner("Pulling..."):
            try:
                api_post(f"/account/{cid}/refresh")
                st.cache_data.clear()
                st.success("Refreshed.")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Refresh failed: {e}")

# --- TL;DR strip --------------------------------------------------------------
tldr = summaries.get("tldr") if summaries else None
if tldr:
    st.markdown(tldr_card(tldr), unsafe_allow_html=True)

# --- KPI row (single line, 6 metrics) -----------------------------------------
tickets = view["tickets"]
deals = view["deals"]
open_t = [t for t in tickets if t["is_open"]]
open_d = [d for d in deals if d["is_open"]]
won_d = [d for d in deals if d["is_won"]]
stalled_d = [d for d in deals if d["stalled"]]
lost_d = [d for d in deals if not d["is_open"] and not d["is_won"]]
wr = len(won_d) / (len(won_d) + len(lost_d)) * 100 if (won_d or lost_d) else 0
stalled_amt = sum((d['amount'] or 0) for d in stalled_d) if stalled_d else 0
days_since_act = metrics.get("days_since_last_activity")
repeat_issues = metrics.get("repeat_issue_count") or 0

kpi_row([
    {"label": "Open tickets", "value": str(len(open_t)), "delta": f"of {len(tickets)} total"},
    {"label": "Open pipeline", "value": fmt_money(metrics.get("open_pipeline_amount")),
     "delta": f"{len(open_d)} deals"},
    {"label": "Stalled $", "value": fmt_money(stalled_amt) if stalled_amt else "—",
     "delta": f"{len(stalled_d)} deals" if stalled_d else "0 stuck"},
    {"label": "Win rate", "value": f"{wr:.0f}%", "delta": f"{len(won_d)}W / {len(lost_d)}L"},
    {"label": "Days since activity",
     "value": f"{days_since_act:.0f}d" if days_since_act is not None else "—",
     "delta": "—" if days_since_act is None else ("recent" if days_since_act < 14 else "quiet")},
    {"label": "Repeat issues", "value": str(repeat_issues),
     "delta": "clusters detected" if repeat_issues else "none"},
])

st.write("")

# --- 2-column: Hot signals | AI assessment + drivers/opps ---------------------
left_col, right_col = st.columns([1, 1.2])

with left_col:
    st.markdown("#### 🔥 Hot signals")
    if not hot_signals:
        st.markdown(
            '<div class="ji-card" style="background:#ecfdf5;border-color:#a7f3d0;color:#065f46;">'
            '🟢 <b>All clear.</b> No concerns auto-surfaced.</div>',
            unsafe_allow_html=True,
        )
    else:
        for h in hot_signals[:5]:
            sev = h.get("severity", "low")
            st.markdown(
                f'<div class="ji-hot-row ji-hot-{sev}">'
                f'{severity_dot(sev)}<b>{h["label"]}</b>'
                f' <span class="ji-hot-detail">— {h.get("detail") or ""}</span></div>',
                unsafe_allow_html=True,
            )
        if len(hot_signals) > 5:
            st.caption(f"+ {len(hot_signals) - 5} more in the Hot signals tab")

with right_col:
    st.markdown("#### 🧠 AI assessment")
    if assessment:
        st.markdown(
            risk_banner(
                assessment.get("risk_flag"),
                assessment.get("risk_score"),
                assessment.get("narrative") or "",
                assessment.get("model"),
            ),
            unsafe_allow_html=True,
        )
        drivers = summaries.get("risk_drivers") or []
        opps = summaries.get("opportunities") or []
        nbas = assessment.get("next_best_actions") or []
        with st.expander(f"🔻 Risk drivers ({len(drivers)})", expanded=False):
            if drivers:
                for x in drivers:
                    st.markdown(f"- {x}")
            else:
                st.caption("—")
        with st.expander(f"🚀 Opportunities ({len(opps)})", expanded=False):
            if opps:
                for x in opps:
                    st.markdown(f"- {x}")
            else:
                st.caption("—")
        # JAZ-186 — NBAs with 1-click triage (mark done / dismiss).
        active_nbas = [(idx, n) for idx, n in enumerate(nbas)
                       if (n.get("status") not in {"done", "dismissed"})]
        with st.expander(f"⚡ Next best actions ({len(active_nbas)} active / {len(nbas)} total)", expanded=True):
            if not active_nbas:
                st.caption("— all clear")
            for idx, nba in active_nbas:
                cols = st.columns([6, 1, 1])
                cols[0].markdown(
                    f"**{nba.get('who','?')}** — {nba.get('action','?')}  \n"
                    f"*{nba.get('rationale','')}*"
                )
                if cols[1].button("✅ Done", key=f"nba_done_{idx}"):
                    _update_nba(company_id, idx, "done")
                    st.rerun()
                if cols[2].button("✖ Skip", key=f"nba_skip_{idx}"):
                    _update_nba(company_id, idx, "dismissed")
                    st.rerun()
            done = [n for n in nbas if n.get("status") == "done"]
            if done:
                with st.expander(f"✓ Completed ({len(done)})", expanded=False):
                    for n in done:
                        st.markdown(f"- ~~{n.get('action','?')}~~")
    else:
        st.info("No AI assessment yet. Hit Refresh to compute one.")

st.write("")


# --- JAZ-187: Risk trajectory chart ------------------------------------------
try:
    risk_hist = api_get(f"/account/{cid}/risk_history", limit=30) or []
except Exception:  # noqa: BLE001
    risk_hist = []
if risk_hist and len(risk_hist) >= 2:
    with st.expander(f"📈 Risk trajectory ({len(risk_hist)} assessments)", expanded=False):
        try:
            df = pd.DataFrame(risk_hist)
            df["generated_at"] = pd.to_datetime(df["generated_at"])
            df = df.set_index("generated_at")
            st.line_chart(df[["risk_score"]], height=200)
            latest = risk_hist[-1]
            first = risk_hist[0]
            delta = (latest.get("risk_score") or 0) - (first.get("risk_score") or 0)
            arrow = "↗️ rising" if delta > 5 else ("↘️ easing" if delta < -5 else "➡️ flat")
            st.caption(
                f"{arrow} — {first['risk_flag']} ({first.get('risk_score')}) → "
                f"{latest['risk_flag']} ({latest.get('risk_score')}) over {len(risk_hist)} assessments"
            )
        except Exception as exc:  # noqa: BLE001
            st.caption(f"chart error: {exc}")

# --- tabs ---------------------------------------------------------------------
tab_support, tab_sales, tab_quotes, tab_contacts, tab_activity, tab_metrics, tab_hot, tab_modules, tab_salesmode, tab_integ, tab_ask, tab_raw = st.tabs(
    [
        f"🎫 Support ({len(tickets)})",
        f"💰 Sales ({len(deals)})",
        f"📑 Quotes ({len(quotes)})",
        f"🧑‍💼 Contacts ({len(contacts)})",
        f"📅 Activity ({len(activities)})",
        "📊 Metrics",
        f"🔥 Hot signals ({len(hot_signals)})",
        "🧠 Modules",
        "💼 Sales Tools",
        "🔌 Integrations",
        "💬 Ask AI",
        "📦 Raw",
    ]
)

# --- Support ------------------------------------------------------------------
with tab_support:
    _ai_sub(summaries.get("support_summary"))
    if not tickets:
        st.info("No tickets.")
    else:
        # Sparkline of tickets by week-ago bucket
        bucket: Counter = Counter()
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

        rows = []
        for t in tickets:
            rows.append({
                "Subject": t["subject"] or "(no subject)",
                "Status": "Open" if t["is_open"] else "Closed",
                "Stage": t["stage"] or "—",
                "Priority": t["priority"] or "—",
                "Age": fmt_days(t["age_days"]),
                "Resolution": fmt_days(t["resolution_days"]) if not t["is_open"] else "—",
                "Replies": t.get("reply_count") if t.get("reply_count") is not None else "—",
                "HubSpot": t["hubspot_url"],
            })
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
    _ai_sub(summaries.get("sales_summary"))
    if not deals:
        st.info("No deals.")
    else:
        if stalled_d:
            st.warning(
                f"⚠️  **{len(stalled_d)} stalled deal(s)** · "
                f"{fmt_money(stalled_amt)} at risk"
            )

        stage_amt: dict[str, float] = defaultdict(float)
        for d in open_d:
            stage_amt[d.get("stage") or "—"] += d.get("amount") or 0
        if stage_amt:
            st.caption("💰 Open pipeline by stage")
            st.bar_chart(pd.Series(stage_amt), height=180)

        rows = []
        for d in deals:
            rows.append({
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
            })
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

        deals_with_history = [d for d in deals if d.get("stage_history")]
        if deals_with_history:
            with st.expander(f"📜 Stage history ({len(deals_with_history)} deals)"):
                for d in deals_with_history:
                    st.markdown(f"**{d['name']}**")
                    hist_df = pd.DataFrame(d["stage_history"] or [])
                    if not hist_df.empty:
                        st.dataframe(hist_df, use_container_width=True, hide_index=True)

# --- Quotes -------------------------------------------------------------------
with tab_quotes:
    if not quotes:
        st.info(
            "No quotes pulled for this account. "
            "*(HubSpot quotes scope is not granted on this PAT — feeder returns empty list. "
            "Grant `crm.objects.quotes.read` to populate.)*"
        )
    else:
        rows = [{
            "Title": q["title"] or "—",
            "Amount": q["amount"] or 0,
            "Status": q["status"] or "—",
            "Created": (q["created"] or "—")[:10],
            "Days to sign": q.get("days_to_sign") or "—",
            "Deal id": q.get("deal_id") or "—",
        } for q in quotes]
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            column_config={"Amount": st.column_config.NumberColumn(format="$%.0f")},
        )

# --- Contacts -----------------------------------------------------------------
with tab_contacts:
    _ai_sub(summaries.get("relationship_summary"))
    if not contacts:
        st.info("No contacts associated with this company in HubSpot.")
    else:
        rows = []
        for c_row in contacts:
            d = c_row.get("days_since_activity") or 0
            if d > 60:
                note = f"⚠️ Quiet — no activity in {d:.0f} days"
            elif d > 21:
                note = f"Slowing — {d:.0f}d since last activity"
            else:
                note = "✅ Active"
            rows.append({
                "Name": c_row.get("name") or "(unknown)",
                "Title": c_row.get("job_title") or "—",
                "Email": c_row.get("email") or "—",
                "Phone": c_row.get("phone") or "—",
                "Last activity": (c_row.get("last_activity_at") or "—")[:10],
                "AI note": note,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# --- Activity -----------------------------------------------------------------
with tab_activity:
    _ai_sub(summaries.get("relationship_summary"))
    if not activities:
        st.info("No engagements found (HubSpot may not expose all engagement types in current scope).")
    else:
        filter_choice = st.radio("Window", ["24h", "7d", "30d", "90d"], index=2, horizontal=True)
        days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}[filter_choice]
        cutoff = datetime.now(UTC) - timedelta(days=days)

        def _in_window(a):
            ts = parse_iso(a.get("ts"))
            if ts is None:
                return True
            # Guard against naive datetimes from upstream parsers.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return ts >= cutoff

        filtered = [a for a in activities if _in_window(a)]
        st.caption(f"{len(filtered)} engagement(s) in last {filter_choice}")

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
                f"<span class='ji-small'>{a['kind']} · {a.get('direction') or ''} · {(a.get('ts') or '—')[:16]}</span>",
                unsafe_allow_html=True,
            )
            if a.get("content_preview"):
                with st.expander("View preview"):
                    st.write(a["content_preview"])

# --- Metrics ------------------------------------------------------------------
with tab_metrics:
    st.subheader("Computed metrics")
    g1, g2, g3 = st.columns(3)
    with g1:
        st.metric("Open pipeline", fmt_money(metrics.get("open_pipeline_amount")))
        st.metric("Won (90d)", fmt_money(metrics.get("won_amount_90d")))
        st.metric("Lost (90d)", fmt_money(metrics.get("lost_amount_90d")))
    with g2:
        wr_m = metrics.get("win_rate_90d")
        st.metric("Win rate (90d)", f"{(wr_m or 0) * 100:.0f}%" if wr_m is not None else "—")
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

    if properties:
        st.markdown("### 🏨 Properties / sister entities")
        st.caption("Extracted from deal names — useful for reseller channels (e.g. McLaren).")
        prows = [
            {"Property": p["name"], "Deals": p["deal_count"],
             "Sample deal": p["deal_names_sample"][0] if p["deal_names_sample"] else ""}
            for p in properties
        ]
        st.dataframe(pd.DataFrame(prows), use_container_width=True, hide_index=True)

# --- Hot signals --------------------------------------------------------------
with tab_hot:
    if not hot_signals:
        st.success("🟢 No hot signals — all clear.")
    else:
        for sev in ("high", "medium", "low"):
            group = [h for h in hot_signals if h["severity"] == sev]
            if not group:
                continue
            st.markdown(f"#### {sev.title()} ({len(group)})")
            for h in group:
                st.markdown(
                    hot_row(sev, h["label"], h.get("detail") or "", h.get("hubspot_url")),
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

# --- JAZ-113/114/115: Modules tab --------------------------------------------
with tab_modules:
    try:
        modules = api_get(f"/account/{cid}/modules") or []
    except Exception as exc:  # noqa: BLE001
        modules = []
        st.error(f"modules load failed: {exc}")
    if not modules:
        st.info("No module data yet.")
    # Card-grid layout: one column per module, colored border + big score.
    sev_color = {"high": "#d62728", "medium": "#ff9f1c", "low": "#2ca02c", "na": "#94a3b8"}
    sev_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢", "na": "⚪"}
    if modules:
        cols = st.columns(len(modules))
        for i, m in enumerate(modules):
            with cols[i]:
                color = sev_color.get(m["severity"], "#94a3b8")
                emoji = sev_emoji.get(m["severity"], "⚪")
                score_str = f"{m['score']:.0f}" if m.get("score") is not None else "—"
                top_driver = (m.get("drivers") or [{}])[0].get("name", "—") if m.get("drivers") else "—"
                st.markdown(
                    f"<div style='border:1px solid {color}; border-left:6px solid {color}; "
                    f"border-radius:8px; padding:14px 16px; margin-bottom:8px;'>"
                    f"<div style='color:{color}; font-size:12px; font-weight:600; letter-spacing:0.04em;'>"
                    f"{emoji} {m['label'].upper()}</div>"
                    f"<div style='font-size:42px; font-weight:700; line-height:1.1; margin:4px 0;'>{score_str}</div>"
                    f"<div style='color:#475569; font-size:13px; margin-bottom:4px;'>{m['headline']}</div>"
                    f"<div style='color:#94a3b8; font-size:11px;'>top signal: <code>{top_driver}</code></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        # Drill-down expanders below the card row.
        for m in modules:
            with st.expander(f"→ {m['label']} — details", expanded=False):
                if m.get("drivers"):
                    st.markdown("**Signals:**")
                    for d in m["drivers"]:
                        parts = [f"`{d.get('name','?')}`"]
                        for k, v in d.items():
                            if k == "name":
                                continue
                            parts.append(f"{k}={v}")
                        st.markdown("- " + " · ".join(parts))
                if m.get("metrics"):
                    st.caption("Metrics")
                    st.json(m["metrics"])

# --- SALES MODE: pre-call brief + stalled deal explainer + email drafter -----
with tab_salesmode:
    st.markdown(
        "✨ **AI Sales Tools** — generate AM-ready outputs from this account's "
        "signals. Each artifact takes 5–10 seconds to compute."
    )
    sales_cols = st.columns(3)

    # 1. Pre-call brief ------------------------------------------------------
    brief_key = f"sales_brief_{cid}"
    if sales_cols[0].button("📄 Generate pre-call brief", key=f"brief_btn_{cid}", use_container_width=True):
        with st.spinner("Drafting brief…"):
            try:
                st.session_state[brief_key] = api_get(f"/account/{cid}/sales/precall_brief")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Brief failed: {exc}")

    # 2. Email drafter -------------------------------------------------------
    email_key = f"sales_email_{cid}"
    if sales_cols[1].button("✉️ Draft follow-up email", key=f"email_btn_{cid}", use_container_width=True):
        with st.spinner("Drafting email…"):
            try:
                st.session_state[email_key] = api_post(f"/account/{cid}/sales/draft_email", json={})
            except Exception as exc:  # noqa: BLE001
                st.error(f"Draft failed: {exc}")

    # 3. Stalled deal explainer ---------------------------------------------
    stalled_deals = [d for d in deals if d.get("stalled") and not (d.get("is_won") or d.get("is_lost"))]
    explain_key = f"sales_explain_{cid}"
    if stalled_deals:
        sel_label = sales_cols[2].selectbox(
            "🔍 Explain stalled deal",
            options=[d["id"] for d in stalled_deals],
            format_func=lambda did: next((d["name"] or did for d in stalled_deals if d["id"] == did), did),
            key=f"explain_sel_{cid}",
        )
        if sales_cols[2].button("Analyze", key=f"explain_btn_{cid}", use_container_width=True):
            with st.spinner("Analyzing deal…"):
                try:
                    st.session_state[explain_key] = api_get(f"/account/{cid}/sales/explain_deal/{sel_label}")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Explain failed: {exc}")
    else:
        sales_cols[2].caption("No stalled deals to analyze ✅")

    st.divider()

    # Render outputs
    brief = st.session_state.get(brief_key)
    if brief:
        with st.container(border=True):
            st.subheader("📄 Pre-call brief")
            if brief.get("is_fallback"):
                st.warning("AI unavailable — showing data summary fallback.")
            st.markdown(brief["markdown"])
            st.caption(f"Generated by `{brief.get('model','?')}`")
            st.download_button(
                "⬇️ Download as markdown",
                data=brief["markdown"],
                file_name=f"precall_{cid}.md",
                mime="text/markdown",
                key=f"dl_brief_{cid}",
            )

    email = st.session_state.get(email_key)
    if email:
        with st.container(border=True):
            st.subheader("✉️ Follow-up email draft")
            if email.get("is_fallback"):
                st.warning("AI unavailable — using template fallback.")
            to_str = email.get("suggested_to_email") or "—"
            if email.get("suggested_to_name"):
                to_str = f"{email['suggested_to_name']} <{email.get('suggested_to_email') or 'no email'}>"
            st.caption(f"**To:** {to_str}")
            st.text_input("Subject", value=email["subject"], key=f"email_sub_{cid}")
            st.text_area("Body", value=email["body"], height=260, key=f"email_body_{cid}")
            st.caption(f"Drafted by `{email.get('model','?')}` · review, edit, then copy to your email client")

    explain = st.session_state.get(explain_key)
    if explain:
        with st.container(border=True):
            st.subheader(f"🔍 Stalled deal analysis — {explain.get('deal_name', explain['deal_id'])}")
            if explain.get("is_fallback"):
                st.warning("AI unavailable — showing heuristic.")
            st.markdown(explain["markdown"])
            st.caption(f"Generated by `{explain.get('model','?')}`")

# --- JAZ-185: Ask AI ----------------------------------------------------------
with tab_ask:
    st.markdown(
        "Ask anything about this account. Claude is constrained to the signals "
        "on this page — tickets, deals, contacts, activities, quotes. Cites "
        "specific records and refuses to guess."
    )
    ask_key = f"ask_history_{cid}"
    pending_key = f"ask_pending_{cid}"
    if ask_key not in st.session_state:
        st.session_state[ask_key] = []

    # Suggestion chips — setting pending triggers the same answer flow as chat_input.
    suggestions = [
        "What are the biggest risks right now?",
        "Are there any stalled deals?",
        "Who should I reach out to first?",
        "Summarize the last 30 days of activity.",
    ]
    if not st.session_state[ask_key]:
        sug_cols = st.columns(len(suggestions))
        for i, sug in enumerate(suggestions):
            if sug_cols[i].button(sug, key=f"sug_{cid}_{i}", use_container_width=True):
                st.session_state[pending_key] = sug
                st.rerun()

    # Render conversation history with native chat bubbles.
    for turn in st.session_state[ask_key]:
        with st.chat_message("user"):
            st.markdown(turn["q"])
        with st.chat_message("assistant"):
            st.markdown(turn["a"])
            if turn.get("citations"):
                st.caption("Cited: " + " · ".join(turn["citations"]))

    # Resolve the next question (chat_input OR pending suggestion).
    user_q = st.chat_input("Ask about this account…", key=f"chat_input_{cid}")
    if not user_q and st.session_state.get(pending_key):
        user_q = st.session_state.pop(pending_key)

    if user_q and user_q.strip():
        with st.chat_message("user"):
            st.markdown(user_q)
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    result = api_post(f"/account/{cid}/ask", json={"question": user_q})
                    st.session_state[ask_key].append({
                        "q": user_q,
                        "a": result.get("answer", ""),
                        "citations": result.get("citations", []),
                        "model": result.get("model", ""),
                    })
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Ask failed: {exc}")

    if st.session_state[ask_key]:
        if st.button("Clear conversation", key=f"clear_ask_{cid}"):
            st.session_state[ask_key] = []
            st.rerun()

# --- Raw ----------------------------------------------------------------------
with tab_raw:
    st.json({"view": view, "extras": extras})

st.divider()
st.caption(
    f"Last refreshed: {fmt_iso(c.get('last_refreshed'))} · "
    f"model: {assessment.get('model') if assessment else '—'}"
)
