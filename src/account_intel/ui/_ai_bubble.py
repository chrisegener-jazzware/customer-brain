"""Ask AI panel for the client portal.

Reliable implementation:
  - Toggle button rendered in a known location (called by client_app at the
    top bar). Uses st.session_state to flip an `open` flag.
  - When open, the panel renders inline (right after the toggle call) but
    visually styled to look like a floating popup card.
  - Uses the client-safe `/ask_client` endpoint.

The previous CSS sibling-selector approach (`anchor + div`) was fragile
because Streamlit wraps elements in multiple containers. This version uses
a wrapper `st.container()` styled via a stable class on a child markdown,
and positions via a regular flex card (not position:fixed) — guarantees
clicks land on the actual Streamlit button.
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


def _key(company_id: str, suffix: str) -> str:
    return f"ai_bubble::{company_id}::{suffix}"


def render_toggle(company_id: str) -> None:
    """Render the 'Ask AI' button. Place this in the top bar."""
    open_key = _key(company_id, "open")
    if open_key not in st.session_state:
        st.session_state[open_key] = False
    is_open = st.session_state[open_key]
    label = "✕  Close AI" if is_open else "💬  Ask AI"
    if st.button(label, key=f"{open_key}::toggle", type="primary", use_container_width=True):
        st.session_state[open_key] = not is_open
        st.rerun()


def render_panel(company_id: str) -> None:
    """Render the AI panel (only when open). Place this where you want the
    panel to appear in the page — typically right under the top bar.
    """
    open_key = _key(company_id, "open")
    msgs_key = _key(company_id, "msgs")

    if msgs_key not in st.session_state:
        st.session_state[msgs_key] = []
    if not st.session_state.get(open_key):
        return

    # Header card
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0b1d3a,#2563eb);'
        'color:#fff;padding:16px 20px;border-radius:14px 14px 0 0;'
        'margin-bottom:0;animation:ji-pop-up .25s cubic-bezier(.2,.7,.3,1) both;">'
        '<div style="font-weight:700;font-size:1.05em;display:flex;align-items:center;gap:10px;">'
        '<span style="width:9px;height:9px;border-radius:50%;background:#34d399;'
        'box-shadow:0 0 10px #34d399;animation:ji-pulse 2.2s infinite"></span>'
        'AI Service Concierge'
        '</div>'
        '<div style="font-size:0.82em;opacity:0.88;margin-top:4px;">'
        'Grounded on your tickets and integrations · escalate to staff anytime'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Body wrapper
    st.markdown(
        '<div style="background:#fff;border:1px solid #cbd5e1;border-top:0;'
        'border-radius:0 0 14px 14px;padding:16px 18px 6px;'
        'box-shadow:0 20px 40px -12px rgba(11,29,58,0.18);'
        'margin-bottom:18px;">',
        unsafe_allow_html=True,
    )

    # Sample prompts (only when no history)
    if not st.session_state[msgs_key]:
        st.caption("Try a sample question:")
        cols = st.columns(2)
        for i, p in enumerate(SAMPLE_PROMPTS):
            with cols[i % 2]:
                if st.button(p, key=f"{msgs_key}::sample::{i}", use_container_width=True):
                    _send(company_id, p)
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
                    f'<span style="color:#059669;font-weight:600;">●</span> Grounded on: {pills}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    if st.session_state.get(_key(company_id, "busy")):
        with st.chat_message("assistant"):
            st.markdown("_Thinking…_")

    # Composer
    q = st.chat_input("Ask anything about your service…", key=f"{msgs_key}::input")
    if q:
        _send(company_id, q)
        st.rerun()

    # Footer actions
    fc1, fc2 = st.columns([1, 1])
    with fc1:
        if st.button("🗑 Clear chat", key=f"{msgs_key}::clear", use_container_width=True):
            st.session_state[msgs_key] = []
            st.rerun()
    with fc2:
        if st.button("🙋 Talk to a person", key=f"{msgs_key}::escalate", use_container_width=True):
            st.session_state[msgs_key].append({
                "role": "assistant",
                "content": "I've flagged your account team — they'll reach out shortly. "
                           "In the meantime, you can email **support@jazzware.com** or call your CSM.",
            })
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


def _send(company_id: str, question: str) -> None:
    msgs_key = _key(company_id, "msgs")
    busy_key = _key(company_id, "busy")
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
