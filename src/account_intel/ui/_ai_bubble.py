"""Ask AI side-drawer for the client portal.

Reliable, simple implementation:
  - Toggle button rendered in the top bar (via render_toggle).
  - When open, a right-side slide-in drawer renders at the END of the
    page. Position: fixed via inline style, no fragile sibling selectors.
  - Uses st.text_input + Send button (NOT st.chat_input, which is
    reserved for bottom-of-page and conflicts with our drawer).
  - Uses the client-safe `/ask_client` endpoint.
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


def _key(cid: str, suffix: str) -> str:
    return f"ai_bubble::{cid}::{suffix}"


def render_toggle(cid: str) -> None:
    """Top-bar button that opens/closes the drawer."""
    open_key = _key(cid, "open")
    if open_key not in st.session_state:
        st.session_state[open_key] = False
    label = "✕  Close AI" if st.session_state[open_key] else "💬  Ask AI"
    if st.button(label, key=f"{open_key}::toggle", type="primary", use_container_width=True):
        st.session_state[open_key] = not st.session_state[open_key]
        st.rerun()


def render_panel(cid: str) -> None:
    """Render the right-side drawer when open. Call once at the END of the page."""
    open_key = _key(cid, "open")
    msgs_key = _key(cid, "msgs")
    pending_key = _key(cid, "pending")

    if msgs_key not in st.session_state:
        st.session_state[msgs_key] = []
    if not st.session_state.get(open_key):
        return

    # ── Process any pending question that was submitted on the previous run ──
    # st.text_input on Enter only persists value; we use the Send button OR
    # a form so the submit is a discrete event. Using st.form below for
    # reliable enter-to-submit behavior.
    if st.session_state.get(pending_key):
        q = st.session_state[pending_key]
        st.session_state[pending_key] = ""
        _send(cid, q)

    # ── Drawer wrapper — fixed right-side slide-in ───────────────────────────
    # We mark the wrapper div with a unique class so the CSS below can pin it.
    drawer_class = f"ji-ai-drawer-{cid.replace('-', '_').replace(':', '_')}"
    st.markdown(
        f"""
        <style>
        /* Pin the next Streamlit element-container (the one immediately after
           this style block) to the right side of the viewport, sliding in. */
        div[data-testid="stVerticalBlock"] > div.{drawer_class} ~ div {{ /* noop fallback */ }}

        /* Use a sentinel + JS-free approach: we instead inline-style the
           wrapper via a markdown div with the pinned class. Streamlit will
           render this as a real DOM node; nested Streamlit elements appear
           naturally inside. */
        .ji-drawer-host {{
          position: fixed;
          top: 0;
          right: 0;
          height: 100vh;
          width: 440px;
          max-width: 92vw;
          background: #ffffff;
          border-left: 1px solid #cbd5e1;
          box-shadow: -30px 0 60px -20px rgba(11,29,58,0.35);
          z-index: 9999;
          overflow-y: auto;
          animation: ji-slide-in .28s cubic-bezier(.2,.7,.3,1) both;
          padding: 0 18px 18px;
        }}
        @keyframes ji-slide-in {{
          from {{ transform: translateX(100%); opacity: 0.4; }}
          to   {{ transform: translateX(0);    opacity: 1;   }}
        }}
        /* Backdrop dim (subtle, doesn't block clicks behind) */
        .ji-drawer-backdrop {{
          position: fixed; inset: 0;
          background: rgba(11,29,58,0.10);
          z-index: 9998;
          pointer-events: none;
          animation: ji-fade-up .25s ease both;
        }}
        </style>
        <div class="ji-drawer-backdrop"></div>
        """,
        unsafe_allow_html=True,
    )

    # The drawer body. Because Streamlit doesn't let us nest its widgets
    # inside an arbitrary HTML wrapper, we use a CSS trick: render a marker
    # div that styles its *parent* `[data-testid="stVerticalBlock"]` as the
    # drawer. The selector below uses :has() (supported in modern browsers).
    st.markdown(
        """
        <style>
        /* Style the vertical block that contains the drawer marker as the
           fixed-position drawer. :has() is supported in Chrome/Safari/Edge
           and recent Firefox. */
        div[data-testid="stVerticalBlock"]:has(> div > div[data-ai-drawer-marker="true"]) {
          position: fixed;
          top: 0;
          right: 0;
          height: 100vh;
          width: 440px;
          max-width: 92vw;
          background: #ffffff;
          border-left: 1px solid #cbd5e1;
          box-shadow: -30px 0 60px -20px rgba(11,29,58,0.35);
          z-index: 9999;
          overflow-y: auto;
          animation: ji-slide-in .28s cubic-bezier(.2,.7,.3,1) both;
          padding: 0 18px 18px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Drawer content lives inside a Streamlit container so widgets work.
    drawer = st.container()
    with drawer:
        # Marker div that the :has() selector above keys off of.
        st.markdown(
            '<div data-ai-drawer-marker="true"></div>',
            unsafe_allow_html=True,
        )

        # Header — hero card with avatar, gradient, status
        st.markdown(
            '<div style="position:relative;background:linear-gradient(135deg,#0b1d3a 0%,#1e3a72 50%,#2563eb 100%);'
            'color:#fff;padding:22px 22px 26px;border-radius:0 0 18px 18px;'
            'margin:0 -18px 18px;overflow:hidden;">'
            '<div style="position:absolute;top:-30px;right:-30px;width:140px;height:140px;'
            'border-radius:50%;background:radial-gradient(circle,rgba(255,255,255,0.18),transparent 70%);"></div>'
            '<div style="display:flex;align-items:center;gap:12px;">'
            '<div style="width:42px;height:42px;border-radius:12px;background:rgba(255,255,255,0.18);'
            'backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;'
            'font-size:1.4em;flex-shrink:0;border:1px solid rgba(255,255,255,0.25);">🤖</div>'
            '<div style="min-width:0;">'
            '<div style="font-weight:700;font-size:1.05em;display:flex;align-items:center;gap:8px;">'
            'AI Service Concierge'
            '<span style="width:8px;height:8px;border-radius:50%;background:#34d399;'
            'box-shadow:0 0 10px #34d399;animation:ji-pulse 2.2s infinite"></span>'
            '</div>'
            '<div style="font-size:0.78em;opacity:0.9;margin-top:2px;">Online · Grounded on your service data</div>'
            '</div>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Sample prompts (only when no history) — styled as suggestion chips
        if not st.session_state[msgs_key]:
            st.markdown(
                '<div style="font-size:0.7em;font-weight:700;letter-spacing:0.14em;'
                'text-transform:uppercase;color:#64748b;margin:4px 0 10px;">Try asking</div>',
                unsafe_allow_html=True,
            )
            for i, p in enumerate(SAMPLE_PROMPTS):
                if st.button(f"  {p}", key=f"{msgs_key}::sample::{i}", use_container_width=True):
                    _send(cid, p)
                    st.rerun()
            st.write("")
            # Subtle separator + helper text
            st.markdown(
                '<div style="text-align:center;font-size:0.72em;color:#94a3b8;'
                'padding:8px 0;border-top:1px dashed #e2e8f0;margin-top:12px;">'
                '✨ Or type your own question below</div>',
                unsafe_allow_html=True,
            )

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

        if st.session_state.get(_key(cid, "busy")):
            with st.chat_message("assistant"):
                st.markdown("_Thinking…_")

        # Composer — styled form with rounded input + colored Send
        st.markdown(
            '<style>'
            '.ji-composer { background:#f8fafc; border:1px solid #e2e8f0; border-radius:14px; '
            'padding:10px 12px; margin-top:8px; }'
            '.ji-composer [data-testid="stTextInput"] input { '
            'background:#fff !important; border:1px solid #cbd5e1 !important; '
            'border-radius:10px !important; padding:10px 12px !important; }'
            '.ji-composer [data-testid="stTextInput"] input:focus { '
            'border-color:#2563eb !important; box-shadow:0 0 0 3px rgba(37,99,235,0.15) !important; }'
            '.ji-composer .stButton button[kind="primary"] { '
            'background:linear-gradient(135deg,#2563eb,#0b1d3a) !important; '
            'border:0 !important; font-weight:600 !important; }'
            '.ji-composer .stButton button[kind="primary"]:hover { '
            'transform:translateY(-1px); box-shadow:0 8px 18px -6px rgba(37,99,235,0.45) !important; }'
            '</style>'
            '<div class="ji-composer">',
            unsafe_allow_html=True,
        )
        with st.form(key=f"{msgs_key}::form", clear_on_submit=True):
            q = st.text_input(
                "Ask anything…",
                key=f"{msgs_key}::input",
                placeholder="Ask anything about your service…",
                label_visibility="collapsed",
            )
            c1, c2 = st.columns([2, 1])
            with c1:
                submitted = st.form_submit_button("↗ Send", type="primary", use_container_width=True)
            with c2:
                clear = st.form_submit_button("Clear", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
        if submitted and q.strip():
            st.session_state[pending_key] = q.strip()
            st.rerun()
        if clear:
            st.session_state[msgs_key] = []
            st.rerun()

        # Escalate — footer action
        st.write("")
        if st.button("🙋‍♂️  Talk to a person", key=f"{msgs_key}::escalate", use_container_width=True):
            st.session_state[msgs_key].append({
                "role": "assistant",
                "content": "I've flagged your account team — they'll reach out shortly. "
                           "In the meantime, you can email **support@jazzware.com** or call your CSM.",
            })
            st.rerun()


def _send(cid: str, question: str) -> None:
    msgs_key = _key(cid, "msgs")
    busy_key = _key(cid, "busy")
    st.session_state[msgs_key].append({"role": "user", "content": question})
    st.session_state[busy_key] = True
    try:
        resp = api_post(f"/account/{cid}/ask_client", json={"question": question})
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
