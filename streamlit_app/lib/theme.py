"""Apply the Neuro Gaming Lab palette to the Streamlit app.

Mirrors ``docs/neuro-theme.css`` so the dashboard feels like part of the same
hub. Streamlit reads ``.streamlit/config.toml`` for theme defaults; this
module layers a small CSS pass on top for components that ``config.toml``
does not control (badges, code blocks, tables).
"""

from __future__ import annotations

import streamlit as st

NEURO_BG = "#0a0a0f"
NEURO_SURFACE = "#12121a"
NEURO_TEXT = "#e8e6e3"
NEURO_MUTED = "#888888"
NEURO_ACCENT = "#7c3aed"

_CSS = f"""
<style>
:root {{
  --neuro-bg: {NEURO_BG};
  --neuro-surface: {NEURO_SURFACE};
  --neuro-text: {NEURO_TEXT};
  --neuro-muted: {NEURO_MUTED};
  --neuro-accent: {NEURO_ACCENT};
}}
.stApp {{ background-color: var(--neuro-bg); color: var(--neuro-text); }}
section[data-testid="stSidebar"] {{ background-color: var(--neuro-surface); }}
code, pre, kbd, samp {{ background-color: var(--neuro-surface) !important; color: var(--neuro-text) !important; }}
.neuro-pill {{
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--neuro-surface);
  color: var(--neuro-accent);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12px;
  margin-right: 6px;
}}
.neuro-banner {{
  border-left: 3px solid var(--neuro-accent);
  background: var(--neuro-surface);
  padding: 10px 14px;
  border-radius: 4px;
  color: var(--neuro-text);
}}
</style>
"""


def apply_theme(page_title: str, icon: str = "*") -> None:
    st.set_page_config(page_title=f"Neuro ML · {page_title}", page_icon=icon, layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)


def banner(text: str) -> None:
    st.markdown(f'<div class="neuro-banner">{text}</div>', unsafe_allow_html=True)


def pill(text: str) -> str:
    return f'<span class="neuro-pill">{text}</span>'
