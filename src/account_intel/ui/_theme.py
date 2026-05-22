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

/* ============================================================== */
/*  v2 client-portal polish — hero, top bar, KPIs, value snapshot   */
/* ============================================================== */

/* ---- Top bar (replaces the clipping "Logged in as" dropdown) ---- */
.ji-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: var(--white);
  border: 1px solid var(--slate-300);
  border-radius: 14px;
  padding: 10px 16px;
  margin-bottom: 14px;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
.ji-topbar-left   { display:flex; align-items:center; gap:10px; min-width:0; }
.ji-topbar-logo   {
  width: 36px; height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--navy-900), var(--blue-600));
  color: var(--white);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 1.05em;
  box-shadow: 0 4px 12px rgba(37,99,235,0.25);
}
.ji-topbar-title  { font-weight: 700; color: var(--navy-900); font-size: 0.95em; line-height: 1.1; }
.ji-topbar-sub    { font-size: 0.74em; color: var(--slate-500); letter-spacing: 0.06em; text-transform: uppercase; }
.ji-topbar-right  { display:flex; align-items:center; gap:10px; flex-shrink:0; }
.ji-topbar-divider{ width:1px; background:var(--slate-300); align-self:stretch; }

/* Inline selectbox in the top bar — drop the giant label, slim down */
.ji-topbar [data-testid="stSelectbox"] label { display: none !important; }
.ji-topbar [data-testid="stSelectbox"] {
  margin: 0 !important;
  min-width: 240px;
}
.ji-topbar [data-baseweb="select"] > div {
  background: var(--slate-50);
  border-color: var(--slate-300);
  border-radius: 10px !important;
  min-height: 38px;
}
.ji-topbar [data-baseweb="select"] > div:hover {
  border-color: var(--blue-600);
  background: var(--white);
}

.ji-topbar-pill {
  display: inline-flex; align-items: center; gap: 6px;
  background: #d1fae5; color: #065f46;
  font-size: 0.74em; font-weight: 600;
  padding: 5px 12px; border-radius: 999px;
  white-space: nowrap;
}
.ji-topbar-pill .dot {
  width:6px; height:6px; border-radius:50%;
  background: var(--emerald);
  box-shadow: 0 0 0 0 rgba(16,185,129,0.45);
  animation: ji-pulse 2.2s infinite;
}
@keyframes ji-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(16,185,129,0.45); }
  70%  { box-shadow: 0 0 0 8px rgba(16,185,129,0); }
  100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); }
}

/* ---- Hero v2 — animated gradient + subtle parallax ----- */
.ji-hero-v2 {
  position: relative;
  overflow: hidden;
  isolation: isolate;
  background: linear-gradient(135deg, #0b1d3a 0%, #14315b 35%, #2563eb 100%);
  color: var(--white);
  padding: 26px 32px 28px;
  border-radius: 18px;
  margin-bottom: 18px;
  box-shadow: 0 10px 30px -8px rgba(11,29,58,0.45), 0 2px 6px rgba(0,0,0,0.06);
  animation: ji-fade-up .55s cubic-bezier(.2,.7,.3,1) both;
}
.ji-hero-v2::before,
.ji-hero-v2::after {
  content: "";
  position: absolute; inset: 0;
  z-index: -1;
  pointer-events: none;
}
.ji-hero-v2::before {
  background:
    radial-gradient(ellipse at 80% 0%, rgba(255,255,255,0.18), transparent 55%),
    radial-gradient(ellipse at 0% 100%, rgba(37,99,235,0.4), transparent 55%);
}
.ji-hero-v2::after {
  background: repeating-linear-gradient(115deg, rgba(255,255,255,0.04) 0 2px, transparent 2px 8px);
  opacity: 0.5;
  animation: ji-shimmer 22s linear infinite;
}
@keyframes ji-shimmer { from { transform: translateX(0); } to { transform: translateX(-200px); } }
@keyframes ji-fade-up { from { opacity: 0; transform: translateY(10px); } to { opacity:1; transform: translateY(0); } }

.ji-hero-v2 .eyebrow {
  display: inline-flex; align-items: center; gap: 8px;
  color: rgba(255,255,255,0.85);
  font-size: 0.7em;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 600;
}
.ji-hero-v2 .eyebrow .e-dot { width:6px; height:6px; border-radius:50%; background:#34d399; box-shadow:0 0 8px #34d399; }
.ji-hero-v2 .title   { font-size: 2.1em; font-weight: 700; margin-top: 10px; letter-spacing: -0.01em; }
.ji-hero-v2 .sub     { color: rgba(255,255,255,0.92); font-size: 1.0em; margin-top: 8px; line-height: 1.55; max-width: 640px; }
.ji-hero-v2 .meta-box {
  text-align: right;
  background: rgba(255,255,255,0.10);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 12px;
  padding: 10px 14px;
  min-width: 180px;
}
.ji-hero-v2 .meta-box .lbl { font-size: 0.66em; letter-spacing: 0.16em; text-transform: uppercase; color: rgba(255,255,255,0.7); }
.ji-hero-v2 .meta-box .val { font-weight: 600; color: #fff; margin-top: 2px; font-size: 0.92em; }
.ji-hero-v2 .meta-box .status { margin-top: 8px; }
.ji-hero-v2 .meta-box .status .pill {
  display:inline-flex; align-items:center; gap:6px;
  background: rgba(52,211,153,0.18);
  color:#a7f3d0; border:1px solid rgba(52,211,153,0.4);
  font-size:0.72em; font-weight:600; padding:4px 10px; border-radius:999px;
}
.ji-hero-v2 .meta-box .status .pill .d { width:6px; height:6px; border-radius:50%; background:#34d399; box-shadow:0 0 8px #34d399; }

/* ---- Animated KPI cards (replaces st.metric row) ---- */
.ji-kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
  margin: 4px 0 18px;
}
@media (max-width: 1100px) { .ji-kpi-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 700px)  { .ji-kpi-grid { grid-template-columns: repeat(2, 1fr); } }

.ji-kpi {
  position: relative;
  background: var(--white);
  border: 1px solid var(--slate-300);
  border-radius: 14px;
  padding: 14px 16px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
  animation: ji-fade-up .55s cubic-bezier(.2,.7,.3,1) both;
}
.ji-kpi:hover { transform: translateY(-2px); box-shadow: 0 10px 24px -10px rgba(11,29,58,0.18); border-color: var(--blue-600); }
.ji-kpi::after {
  content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px;
  background: linear-gradient(90deg, var(--blue-600), #34d399);
  opacity: 0.85;
}
.ji-kpi .l   { font-size: 0.7em; font-weight: 600; color: var(--slate-500); text-transform: uppercase; letter-spacing: 0.06em; }
.ji-kpi .v   { font-size: 1.85em; font-weight: 700; color: var(--navy-900); margin-top: 4px; line-height: 1.1; letter-spacing: -0.01em; }
.ji-kpi .d   { font-size: 0.78em; color: var(--emerald); margin-top: 4px; font-weight: 600; }
.ji-kpi .d.neg { color: var(--red); }
.ji-kpi .icon{ position: absolute; right: 12px; top: 14px; font-size: 1.4em; opacity: 0.18; }
.ji-kpi.alt::after  { background: linear-gradient(90deg, #f59e0b, var(--blue-600)); }
.ji-kpi.warn::after { background: linear-gradient(90deg, var(--red), var(--amber)); }

/* ---- Value snapshot v2 ---- */
.ji-value-card {
  position: relative;
  background: linear-gradient(135deg, #f8fafc 0%, #eff6ff 60%, #ecfdf5 120%);
  border: 1px solid var(--slate-300);
  border-radius: 16px;
  padding: 18px 22px;
  margin: 4px 0 22px;
  overflow: hidden;
  box-shadow: 0 6px 18px -8px rgba(11,29,58,0.15);
  animation: ji-fade-up .55s .05s cubic-bezier(.2,.7,.3,1) both;
}
.ji-value-card::before {
  content: ""; position: absolute; right: -40px; top: -40px;
  width: 180px; height: 180px; border-radius: 50%;
  background: radial-gradient(circle, rgba(37,99,235,0.10), transparent 70%);
}
.ji-value-card .vc-hd {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 14px;
}
.ji-value-card .vc-eyebrow {
  font-size: 0.7em; font-weight: 700; color: var(--blue-600);
  text-transform: uppercase; letter-spacing: 0.14em;
  display: inline-flex; align-items: center; gap: 6px;
}
.ji-value-card .vc-period { font-size: 0.78em; color: var(--slate-500); font-weight: 600; }
.ji-value-card .vc-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px;
}
@media (max-width: 720px) { .ji-value-card .vc-grid { grid-template-columns: repeat(2, 1fr); } }
.ji-value-card .vc-stat {
  background: rgba(255,255,255,0.65);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  border: 1px solid rgba(203,213,225,0.6);
  border-radius: 12px;
  padding: 12px;
}
.ji-value-card .vc-stat .lab { font-size: 0.7em; color: var(--slate-500); text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
.ji-value-card .vc-stat .num { font-size: 1.7em; color: var(--navy-900); font-weight: 700; margin-top: 4px; line-height: 1.05; }
.ji-value-card .vc-stat .icon{ float: right; opacity: 0.4; font-size: 1.1em; }
.ji-value-card .vc-nba   {
  background: var(--white);
  border: 1px solid var(--slate-300);
  border-radius: 12px;
  padding: 12px 14px;
}
.ji-value-card .vc-nba .nba-hd { font-size: 0.78em; font-weight: 700; color: var(--navy-900); margin-bottom: 6px; }
.ji-value-card .vc-nba ul { margin: 0; padding-left: 18px; }
.ji-value-card .vc-nba li { color: var(--slate-700); margin: 3px 0; font-size: 0.9em; line-height: 1.5; }

/* ---- Floating AI bubble + popup chat panel ---- */
.ji-ai-launcher {
  position: fixed;
  right: 24px; bottom: 24px;
  z-index: 9999;
  display: flex; align-items: center; gap: 10px;
  pointer-events: none;
}
.ji-ai-launcher .preview {
  background: var(--white);
  border: 1px solid var(--slate-300);
  border-radius: 999px;
  padding: 8px 14px;
  font-size: 0.84em;
  font-weight: 500;
  color: var(--slate-700);
  box-shadow: 0 10px 20px -8px rgba(11,29,58,0.25);
  pointer-events: auto;
  animation: ji-fade-up .6s .8s both;
}
.ji-ai-launcher .btn {
  pointer-events: auto;
  width: 60px; height: 60px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--blue-600), var(--navy-900));
  color: var(--white);
  display: flex; align-items: center; justify-content: center;
  font-size: 1.6em;
  box-shadow: 0 14px 30px -8px rgba(37,99,235,0.55), 0 4px 8px rgba(0,0,0,0.06);
  cursor: pointer;
  border: 0;
  transition: transform .15s ease, box-shadow .15s ease;
  animation: ji-bob 4.5s ease-in-out infinite;
}
.ji-ai-launcher .btn:hover { transform: scale(1.06); }
@keyframes ji-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }

.ji-ai-panel {
  position: fixed;
  right: 24px; bottom: 96px;
  width: 380px; max-width: calc(100vw - 32px);
  max-height: 70vh;
  background: var(--white);
  border: 1px solid var(--slate-300);
  border-radius: 18px;
  z-index: 9998;
  box-shadow: 0 30px 60px -20px rgba(11,29,58,0.45), 0 4px 12px rgba(0,0,0,0.08);
  overflow: hidden;
  display: flex; flex-direction: column;
  animation: ji-pop-up .25s cubic-bezier(.2,.7,.3,1) both;
}
@keyframes ji-pop-up { from { opacity: 0; transform: translateY(12px) scale(0.98); } to { opacity: 1; transform: translateY(0) scale(1); } }
.ji-ai-panel .hd {
  background: linear-gradient(135deg, var(--navy-900), var(--blue-600));
  color: #fff;
  padding: 14px 18px;
}
.ji-ai-panel .hd .title { font-weight: 700; font-size: 1.0em; display:flex; align-items:center; gap:8px; }
.ji-ai-panel .hd .sub   { font-size: 0.78em; opacity: 0.85; margin-top: 2px; }
.ji-ai-panel .hd .dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #34d399;
  box-shadow: 0 0 8px #34d399;
  animation: ji-pulse 2.2s infinite;
}

/* Section header used between cards */
.ji-section-hd {
  font-size: 0.72em;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--slate-500);
  margin: 18px 0 8px;
  display: flex; align-items: center; gap: 8px;
}
.ji-section-hd::after {
  content:""; flex:1; height:1px; background: var(--slate-300);
}

/* Smooth tab transitions */
[data-baseweb="tab-panel"] { animation: ji-fade-up .35s ease both; }
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


# --------------------------------------------------------------------------- #
# v2 components (client portal polish)                                        #
# --------------------------------------------------------------------------- #


def hero_v2(
    *,
    eyebrow: str,
    title: str,
    subtitle: str,
    last_updated: str | None = None,
    status_label: str = "On track",
) -> str:
    """Returns hero HTML. Uses .ji-hero-v2 styles (animated gradient + parallax)."""
    meta = ""
    if last_updated:
        meta = (
            f'<div class="meta-box">'
            f'<div class="lbl">Last updated</div>'
            f'<div class="val">{last_updated}</div>'
            f'<div class="status"><span class="pill"><span class="d"></span>{status_label}</span></div>'
            f'</div>'
        )
    return (
        f'<div class="ji-hero-v2">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap;">'
        f'<div style="min-width:0;flex:1;">'
        f'<div class="eyebrow"><span class="e-dot"></span>{eyebrow}</div>'
        f'<div class="title">{title}</div>'
        f'<div class="sub">{subtitle}</div>'
        f'</div>'
        f'{meta}'
        f'</div></div>'
    )


def animated_kpi_row(items: Iterable[dict]) -> str:
    """Render KPIs as animated gradient cards. Each item may include:
        label, value, delta, icon, tone ("default"|"alt"|"warn").
    Returns one HTML string \u2014 emit with st.markdown(..., unsafe_allow_html=True).
    """
    EMDASH = "\u2014"
    out = ['<div class="ji-kpi-grid">']
    for it in items:
        tone = it.get("tone", "default")
        cls = "ji-kpi" + ("" if tone == "default" else f" {tone}")
        delta = it.get("delta")
        delta_html = ""
        if delta:
            neg = str(delta).strip().startswith("-")
            neg_cls = " neg" if neg else ""
            delta_html = f'<div class="d{neg_cls}">{delta}</div>'
        icon = it.get("icon", "")
        icon_html = f'<div class="icon">{icon}</div>' if icon else ""
        label = it.get("label", "")
        value = it.get("value", EMDASH)
        out.append(
            f'<div class="{cls}">'
            f'{icon_html}'
            f'<div class="l">{label}</div>'
            f'<div class="v">{value}</div>'
            f'{delta_html}'
            f'</div>'
        )
    out.append("</div>")
    return "".join(out)


def value_snapshot_card(snap: dict) -> str:
    """Render the JAZ-265 value snapshot as a single styled card.

    snap is the dict returned by /account/{id}/value_snapshot.
    """
    integ = f'{snap.get("integrations_healthy", 0)}/{snap.get("integrations_total", 0)}'
    avg = snap.get("avg_resolution_days")
    avg_str = f"{avg:.1f}d" if isinstance(avg, (int, float)) else "\u2014"
    stats = [
        ("Tickets resolved",     snap.get("tickets_resolved", 0), "\u2705"),
        ("Avg resolution",       avg_str,                          "\u23F1\uFE0F"),
        ("Hours saved (est)",    snap.get("hours_saved_estimate", 0), "\u26A1"),
        ("Integrations healthy", integ,                            "\U0001F50C"),
    ]
    nba_items = snap.get("nba_client") or []
    nba_html = (
        "".join(f"<li>{x}</li>" for x in nba_items)
        if nba_items else
        "<li>No recommended actions this quarter \u2014 you're on track.</li>"
    )
    stats_html = "".join(
        f'<div class="vc-stat"><span class="icon">{ic}</span>'
        f'<div class="lab">{lab}</div>'
        f'<div class="num">{val}</div></div>'
        for lab, val, ic in stats
    )
    return (
        f'<div class="ji-value-card">'
        f'<div class="vc-hd">'
        f'<div class="vc-eyebrow">\U0001F381 Value snapshot</div>'
        f'<div class="vc-period">{snap.get("period_label", "")}</div>'
        f'</div>'
        f'<div class="vc-grid">{stats_html}</div>'
        f'<div class="vc-nba">'
        f'<div class="nba-hd">\u2728 What\u2019s next for you</div>'
        f'<ul>{nba_html}</ul>'
        f'</div>'
        f'</div>'
    )


def section_header(text: str) -> str:
    return f'<div class="ji-section-hd">{text}</div>'
