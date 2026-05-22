"""Floating "Ask AI" launcher + popup chat panel for the client portal.

UX:
  - A pill ("Ask anything about your service") + circle button sit pinned
    bottom-right of the viewport.
  - Clicking the button toggles a popup chat anchored to the same corner.
  - Chat state lives in st.session_state keyed by company_id so switching
    between accounts resets the thread.
  - Uses the client-safe `/ask_client` endpoint.

This is a Streamlit-friendly approximation of a true floating widget; the
launcher rail at the bottom is pure HTML so it never re-renders, while
the panel inside uses Streamlit primitives (chat_message, chat_input,
buttons) so the messages, citations, and submit logic work natively.
"""
from __future__ import annotations

import streamlit as st

from account_intel.ui._common import api_post


SAMPLE_PROMPTS = [
    "Why was my last ticket escalated?",
    "How is our uptime trending?",
    "What's on the roadmap for us?",
    "Summarize my open requests",
]


def _state_key(company_id: str, suffix: str) -> str:
    return f"ai_bubble::{company_id}::{suffix}"


def render_launcher(open_key: str) -> None:
    """Render the always-on bottom-right launcher pill + button."""
    is_open = st.session_state.get(open_key, False)
    # The launcher itself is HTML-only (so it sticks to the viewport via
    # position:fixed); the *toggle* is a tiny invisible Streamlit button
    # we overlay via :empty/:has trickery? Simpler: use a normal Streamlit
    # button rendered just below, but visually hidden, and let the user
    # click the colored fab. To keep it reliable across Streamlit versions
    # we instead place a small toggle button at the very bottom of the
    # page using st.button so clicks are wired through Streamlit's normal
    # event loop.
    if not is_open:
        st.markdown(
            '<div class="ji-ai-launcher">'
            '<div class="preview">💬 Ask your service AI</div>'
            '</div>',
            unsafe_allow_html=True,
        )


def render_panel(company_id: str) -> None:
    """Render the popup panel + toggle button. Single entry point per page."""
    open_key = _state_key(company_id, "open")
    msgs_key = _state_key(company_id, "msgs")
    busy_key = _state_key(company_id, "busy")

    if msgs_key not in st.session_state:
        st.session_state[msgs_key] = []
    if open_key not in st.session_state:
        st.session_state[open_key] = False

    # Toggle button — styled by .ji-ai-launcher .btn via container class hack:
    # we wrap it in a styled div so the visual treatment matches the design.
    btn_label = "✕" if st.session_state[open_key] else "💬"
    st.markdown('<div class="ji-ai-fab-anchor"></div>', unsafe_allow_html=True)
    st.markdown(
        """
        <style>
        /* Pin the Streamlit button that immediately follows the anchor to the
           bottom-right of the viewport, styled as the fab. */
        .ji-ai-fab-anchor + div .stButton > button {
          position: fixed; right: 24px; bottom: 24px; z-index: 9999;
          width: 60px; height: 60px; border-radius: 50%;
          background: linear-gradient(135deg, var(--blue-600), var(--navy-900));
          color: #fff; font-size: 1.4em; line-height: 1;
          border: 0; padding: 0;
          box-shadow: 0 14px 30px -8px rgba(37,99,235,0.55), 0 4px 8px rgba(0,0,0,0.06);
          transition: transform .15s ease;
        }
        .ji-ai-fab-anchor + div .stButton > button:hover { transform: scale(1.06); }
        .ji-ai-fab-anchor + div .stButton > button:focus { outline: 2px solid #2563eb; outline-offset: 2px; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    if st.button(btn_label, key=open_key + "::btn", help="Ask your service AI"):
        st.session_state[open_key] = not st.session_state[open_key]
        st.rerun()

    if not st.session_state[open_key]:
        # Show the resting "preview pill" rail
        render_launcher(open_key)
        return

    # Panel container — wrap Streamlit elements inside the styled box.
    st.markdown('<div class="ji-ai-panel-anchor"></div>', unsafe_allow_html=True)
    st.markdown(
        """
        <style>
        /* Style the next Streamlit container to look like the popup panel. */
        .ji-ai-panel-anchor + div {
          position: fixed; right: 24px; bottom: 96px; z-index: 9998;
          width: 380px; max-width: calc(100vw - 32px); max-height: 70vh;
          background: var(--white); border: 1px solid var(--slate-300);
          border-radius: 18px; overflow: hidden;
          box-shadow: 0 30px 60px -20px rgba(11,29,58,0.45), 0 4px 12px rgba(0,0,0,0.08);
          animation: ji-pop-up .25s cubic-bezier(.2,.7,.3,1) both;
        }
        .ji-ai-panel-anchor + div > div:first-child { /* body padding */
          padding: 0 14px 12px;
        }
        .ji-ai-panel-anchor + div [data-testid="stChatMessageContent"] p { margin: 0.2em 0; }
        .ji-ai-panel-anchor + div [data-testid="stChatInput"] { background: var(--slate-50); }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container():
        st.markdown(
            '<div style="background:linear-gradient(135deg,#0b1d3a,#2563eb);'
            'color:#fff;padding:14px 18px;margin:-1rem -14px 8px;">'
            '<div style="font-weight:700;font-size:1.0em;display:flex;align-items:center;gap:8px;">'
            '<span style="width:8px;height:8px;border-radius:50%;background:#34d399;'
            'box-shadow:0 0 8px #34d399"></span>'
            'AI Service Concierge'
            '</div>'
            '<div style="font-size:0.78em;opacity:0.85;margin-top:2px;">'
            'Grounded on your tickets & integrations · escalate to staff anytime'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Sample prompts
        if not st.session_state[msgs_key]:
            cols = st.columns(2)
            for i, prompt in enumerate(SAMPLE_PROMPTS):
                with cols[i % 2]:
                    if st.button(prompt, key=f"{msgs_key}::sample::{i}", use_container_width=True):
                        _send(company_id, prompt)
                        st.rerun()

        # Message history
        for m in st.session_state[msgs_key]:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])
                if m.get("citations"):
                    pills = " ".join(
                        f'<span style="background:#eff6ff;color:#1e40af;'
                        f'padding:2px 8px;border-radius:9999px;font-size:11px;'
                        f'margin-right:4px;display:inline-block;margin-top:4px;">{c}</span>'
                        for c in m["citations"]
                    )
                    st.markdown(
                        f'<div style="margin-top:6px;font-size:11px;color:#64748b;">'
                        f'<span style="color:#059669;font-weight:600;">●</span> Grounded: {pills}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        if st.session_state.get(busy_key):
            with st.chat_message("assistant"):
                st.markdown("_Thinking…_")

        q = st.chat_input("Ask anything about your service…", key=f"{msgs_key}::input")
        if q:
            _send(company_id, q)
            st.rerun()


def _send(company_id: str, question: str) -> None:
    msgs_key = _state_key(company_id, "msgs")
    busy_key = _state_key(company_id, "busy")
    st.session_state[msgs_key].append({"role": "user", "content": question})
    st.session_state[busy_key] = True
    try:
        resp = api_post(f"/account/{company_id}/ask_client", json={"question": question})
        st.session_state[msgs_key].append({
            "role": "assistant",
            "content": resp.get("answer", "—"),
            "citations": resp.get("citations", []),
            "model": resp.get("model", ""),
        })
    except Exception as e:  # noqa: BLE001
        st.session_state[msgs_key].append({
            "role": "assistant",
            "content": f"I had trouble reaching the AI: {e}",
        })
    finally:
        st.session_state[busy_key] = False
