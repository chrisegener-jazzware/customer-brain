"""Centralized design system for the Streamlit apps.

ONE comprehensive style block, imported once per app. Every reusable card sets
BOTH `background` AND `color` explicitly — no inheritance gaps, no white-on-white.

Tokens are CSS custom properties on :root, so any inline component HTML can use
`color:var(--slate-900)` and stay consistent.

This module also exports reusable card helpers that return HTML strings; callers
emit them with `st.markdown(..., unsafe_allow_html=True)`.
"""
from __future__ import annotations

from collections.abc import Iterable

import streamlit as st

# --------------------------------------------------------------------------- #
# Token reference (kept in Python for easy reuse in card helpers)             #
# --------------------------------------------------------------------------- #

NAVY_900 = "#0b1d3a"
NAVY_700 = "#14315b"
BLUE_600 = "#2563eb"
BLUE_50 = "#eff6ff"
SLATE_900 = "#0f172a"
SLATE_700 = "#334155"
SLATE_500 = "#64748b"
SLATE_300 = "#cbd5e1"
SLATE_100 = "#f1f5f9"
SLATE_50 = "#f8fafc"
WHITE = "#ffffff"

RED = "#dc2626"
AMBER = "#d97706"
EMERALD = "#059669"

PILL_STYLES = {
    "red":   ("#fee2e2", "#991b1b"),
    "amber": ("#fef3c7", "#92400e"),
    "green": ("#d1fae5", "#065f46"),
    "blue":  ("#dbeafe", "#1e40af"),
    "gray":  ("#f1f5f9", "#475569"),
}

# --------------------------------------------------------------------------- #
# Master CSS — every visible card sets both background AND color.             #
# --------------------------------------------------------------------------- #

THEME_CSS = """
<style>
:root {
  --navy-900: #0b1d3a;
  --navy-700: #14315b;
  --blue-600: #2563eb;
  --blue-50:  #eff6ff;
  --slate-900:#0f172a;
  --slate-700:#334155;
  --slate-500:#64748b;
  --slate-300:#cbd5e1;
  --slate-100:#f1f5f9;
  --slate-50: #f8fafc;
  --white:    #ffffff;
  --red:      #dc2626;
  --amber:    #d97706;
  --emerald:  #059669;
}

/* ---- Page chrome ---- */
.block-container {
  padding-top: 1.1rem;
  padding-bottom: 3rem;
  max-width: 1500px;
}
h1, h2, h3, h4, h5, h6 { color: var(--navy-900); }
h1 { margin-bottom: 0.2rem; }
a { color: var(--blue-600); }

/* ---- st.metric polish ---- */
[data-testid="stMetric"] {
  background: var(--white);
  padding: 12px 16px;
  border-radius: 10px;
  border: 1px solid var(--slate-300);
  box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
[data-testid="stMetric"] label,
[data-testid="stMetric"] [data-testid="stMetricLabel"] {
  color: var(--slate-500) !important;
  font-size: 0.78em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
  color: var(--navy-900) !important;
  font-weight: 700;
}
[data-testid="stMetric"] [data-testid="stMetricDelta"] {
  color: var(--slate-700) !important;
  font-size: 0.78em;
}

/* ---- Tabs ---- */
[data-baseweb="tab-list"] { gap: 4px; }
[data-baseweb="tab"] {
  background: var(--white) !important;
  color: var(--slate-700) !important;
  border-radius: 8px 8px 0 0;
  padding: 8px 14px !important;
}
[data-baseweb="tab"][aria-selected="true"] {
  background: var(--blue-50) !important;
  color: var(--navy-900) !important;
  font-weight: 600;
}

/* ---- Sidebar (scoped, no global * override) ---- */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, var(--navy-900) 0%, var(--navy-700) 100%);
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] .stRadio > label,
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label > div {
  color: #f5f7fb !important;
}
/* Inputs inside the dark sidebar keep dark text on white */
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea,
[data-testid="stSidebar"] select {
  color: var(--navy-900) !important;
  background: var(--white) !important;
}
/* Buttons inside the sidebar */
[data-testid="stSidebar"] .stButton > button {
  background: rgba(255,255,255,0.10);
  color: #f5f7fb !important;
  border: 1px solid rgba(255,255,255,0.20);
}
[data-testid="stSidebar"] .stButton > button:hover {
  background: rgba(255,255,255,0.20);
  border-color: rgba(255,255,255,0.40);
}
/* Primary action button (main area) */
.stButton > button[kind="primary"] {
  background: var(--blue-600);
  color: var(--white);
  border: 1px solid var(--blue-600);
}

/* ---- Reusable card classes (all set background AND color) ---- */
.ji-card {
  background: var(--white);
  color: var(--slate-900);
  border: 1px solid var(--slate-300);
  border-radius: 10px;
  padding: 14px 18px;
  margin-bottom: 12px;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
.ji-card .ji-card-title {
  color: var(--navy-900);
  font-weight: 600;
  font-size: 1.0em;
  margin-bottom: 6px;
}
.ji-card .ji-card-body {
  color: var(--slate-700);
  line-height: 1.55;
  font-size: 0.95em;
}
.ji-card .ji-card-footnote {
  color: var(--slate-500);
  font-size: 0.8em;
  margin-top: 8px;
}

.ji-tldr {
  background: var(--blue-50);
  color: var(--navy-900);
  border-left: 5px solid var(--blue-600);
  border-radius: 8px;
  padding: 14px 18px;
  margin: 6px 0 16px 0;
  font-size: 1.04em;
  line-height: 1.55;
}
.ji-tldr b { color: var(--navy-900); }

.ji-risk-banner {
  background: var(--white);
  color: var(--slate-900);
  border: 1px solid var(--slate-300);
  border-left: 6px solid var(--slate-500);
  border-radius: 10px;
  padding: 12px 18px;
  margin: 4px 0 14px 0;
}
.ji-risk-banner .ji-risk-header {
  color: var(--navy-900);
  font-weight: 600;
  font-size: 1.0em;
  margin-bottom: 4px;
}
.ji-risk-banner .ji-risk-meta {
  color: var(--slate-500);
  font-size: 0.82em;
}
.ji-risk-banner .ji-risk-body {
  color: var(--slate-700);
  line-height: 1.55;
  margin-top: 6px;
}

.ji-ai-sub {
  background: var(--slate-50);
  color: var(--slate-700);
  border-left: 3px solid var(--slate-300);
  border-radius: 6px;
  padding: 10px 14px;
  margin: 0 0 14px 0;
  font-size: 0.92em;
  line-height: 1.55;
}

.ji-pill {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.78em;
  font-weight: 600;
  margin-right: 4px;
  white-space: nowrap;
}

/* ---- Hot signals ---- */
.ji-hot-row {
  background: var(--white);
  color: var(--slate-900);
  border: 1px solid var(--slate-300);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 8px;
  font-size: 0.92em;
}
.ji-hot-row b { color: var(--navy-900); }
.ji-hot-row .ji-hot-detail { color: var(--slate-500); font-size: 0.86em; }
.ji-hot-high   { border-left: 4px solid var(--red); }
.ji-hot-medium { border-left: 4px solid var(--amber); }
.ji-hot-low    { border-left: 4px solid var(--slate-500); }

/* ---- Directory account rows (sidebar) ---- */
.ji-acct-card {
  padding: 8px 10px;
  margin-bottom: 4px;
  border-radius: 8px;
  background: rgba(255,255,255,0.06);
  color: #f5f7fb;
  border-left: 4px solid var(--slate-500);
}
.ji-acct-card.selected {
  background: rgba(37,99,235,0.30);
  border-left-color: var(--blue-600);
}
.ji-acct-card .ji-acct-name { color: #f5f7fb; font-weight: 600; font-size: 0.92em; }
.ji-acct-card .ji-acct-meta { color: #cbd5e1; font-size: 0.76em; opacity: 0.95; }

/* ---- Hero panel (client app) ---- */
.ji-hero {
  background: linear-gradient(135deg, var(--navy-900) 0%, #1e3a72 50%, #2e5cb8 100%);
  color: var(--white);
  padding: 22px 30px;
  border-radius: 12px;
  margin-bottom: 18px;
  box-shadow: 0 4px 14px rgba(11,29,58,0.18);
}
.ji-hero .ji-hero-eyebrow {
  color: rgba(255,255,255,0.82);
  font-size: 0.74em;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.ji-hero .ji-hero-title { color: var(--white); font-size: 2.0em; font-weight: 600; margin-top: 4px; }
.ji-hero .ji-hero-sub   { color: rgba(255,255,255,0.92); font-size: 0.98em; margin-top: 6px; line-height: 1.5; }
.ji-hero .ji-hero-meta  { color: rgba(255,255,255,0.78); font-size: 0.82em; margin-top: 8px; }

.ji-status-badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 0.82em;
  font-weight: 600;
  background: #d1fae5;
  color: #065f46;
}

.ji-value-event {
  background: #ecfdf5;
  color: #064e3b;
  border-left: 4px solid var(--emerald);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 8px;
  font-size: 0.95em;
}

.ji-roadmap-card {
  background: var(--white);
  color: var(--slate-900);
  border: 1px solid var(--slate-300);
  border-radius: 10px;
  padding: 12px 16px;
  margin-bottom: 10px;
}
.ji-roadmap-card b { color: var(--navy-900); }
.ji-roadmap-card .ji-roadmap-desc { color: var(--slate-700); margin-top: 6px; font-size: 0.92em; line-height: 1.5; }

.ji-cta-btn {
  display: inline-block;
  background: var(--navy-900);
  color: var(--white) !important;
  padding: 10px 18px;
  border-radius: 8px;
  text-decoration: none;
  font-weight: 600;
}
.ji-cta-btn:hover { background: var(--blue-600); }

/* ---- Small caption ---- */
.ji-small { color: var(--slate-500); font-size: 0.82em; }
</style>
"""


def inject_theme() -> None:
    """Call once at the top of each Streamlit app."""
    st.markdown(THEME_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Reusable card helpers (return HTML strings).                                #
# --------------------------------------------------------------------------- #


def pill(label: str, kind: str = "gray") -> str:
    bg, fg = PILL_STYLES.get(kind, PILL_STYLES["gray"])
    return (
        f'<span class="ji-pill" style="background:{bg};color:{fg};">'
        f'{label}</span>'
    )


def risk_pill(score: float | None) -> str:
    if score is None:
        return pill("— no score", "gray")
    if score >= 70:
        return pill(f"🔴 {score:.0f}", "red")
    if score >= 40:
        return pill(f"🟡 {score:.0f}", "amber")
    return pill(f"🟢 {score:.0f}", "green")


def card(title: str, body: str, accent_color: str | None = None, footnote: str | None = None) -> str:
    border = f"border-left:5px solid {accent_color};" if accent_color else ""
    fn = (
        f'<div class="ji-card-footnote">{footnote}</div>' if footnote else ""
    )
    return (
        f'<div class="ji-card" style="{border}">'
        f'<div class="ji-card-title">{title}</div>'
        f'<div class="ji-card-body">{body}</div>'
        f'{fn}'
        f'</div>'
    )


def tldr_card(text: str) -> str:
    return f'<div class="ji-tldr">📌 <b>TL;DR.</b> {text}</div>'


def ai_subcard(text: str) -> str:
    return f'<div class="ji-ai-sub">🧠 {text}</div>'


def risk_banner(flag: str | None, score: float | None, narrative: str, model: str | None) -> str:
    color = {"red": RED, "yellow": AMBER, "amber": AMBER, "green": EMERALD}.get(
        (flag or "").lower(), SLATE_500
    )
    emoji = {"red": "🔴", "yellow": "🟡", "amber": "🟡", "green": "🟢"}.get(
        (flag or "").lower(), "⚪"
    )
    flag_text = (flag or "unknown").upper()
    score_text = f"{score:.0f}/100" if score is not None else "—"
    return (
        f'<div class="ji-risk-banner" style="border-left-color:{color};">'
        f'<div class="ji-risk-header">{emoji} {flag_text} · risk {score_text} '
        f'<span class="ji-risk-meta">· model: <code>{model or "?"}</code></span></div>'
        f'<div class="ji-risk-body">{narrative}</div>'
        f'</div>'
    )


def hot_row(severity: str, label: str, detail: str = "", hubspot_url: str | None = None) -> str:
    sev_cls = {
        "high": "ji-hot-high",
        "medium": "ji-hot-medium",
        "low": "ji-hot-low",
    }.get(severity, "ji-hot-low")
    link = (
        f' · <a href="{hubspot_url}" target="_blank">HubSpot ↗</a>'
        if hubspot_url else ""
    )
    return (
        f'<div class="ji-hot-row {sev_cls}"><b>{label}</b> '
        f'<span class="ji-hot-detail">— {detail}{link}</span></div>'
    )


def kpi_row(items: Iterable[dict]) -> None:
    """Render a row of metrics using st.metric (which we've already styled).

    items: iterable of {label, value, delta?, help?}.
    """
    items = list(items)
    if not items:
        return
    cols = st.columns(len(items))
    for col, it in zip(cols, items, strict=False):
        col.metric(
            it.get("label", ""),
            it.get("value", "—"),
            it.get("delta"),
            help=it.get("help"),
        )


def severity_dot(sev: str) -> str:
    color = {"high": RED, "medium": AMBER, "low": SLATE_500}.get(sev, SLATE_500)
    return (
        f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
        f'background:{color};margin-right:8px;vertical-align:middle;"></span>'
    )
