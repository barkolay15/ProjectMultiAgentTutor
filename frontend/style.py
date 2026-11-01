"""Небольшая подстройка внешнего вида. Цвета не задаются: работают и светлая, и тёмная тема.

Тему переключает сам Streamlit: меню ⋮ справа вверху → Settings → Theme
(Light, Dark или Use system setting).
"""
import streamlit as st

_CSS = """
<style>
.block-container { padding-top: 2.2rem; max-width: 1200px; }
footer { visibility: hidden; }
[data-testid="stAppDeployButton"], .stDeployButton { display: none; }
[data-testid="stChatMessage"] { border-radius: 14px; padding: 0.8rem 1rem; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }
h1 { letter-spacing: -0.01em; }
</style>
"""


def apply_style() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def is_dark_theme() -> bool:
    """Текущая тема Streamlit; у старых версий (до 1.46) считаем тему тёмной."""
    try:
        return st.context.theme.type != "light"
    except Exception:
        return True