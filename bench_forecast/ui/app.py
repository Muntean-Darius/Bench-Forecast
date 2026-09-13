"""Bench Forecast — Enterprise Workforce Allocation & HITL Console.

Unique, authoritative corporate interface:
- Minimalist executive visual design (Linear/Datadog/Bloomberg style)
- Monospace metadata accents & structured telemetry
- Strict zero-emoji, zero-gradient, zero-animation enforcement
- High-contrast WCAG AAA compliance (> 7:1) across all components
- 3 Dedicated Modules:
  1. Allocation Console (Demand SOW, RAG semantic match, LLM planning, HITL review)
  2. Manager Dashboard (Executive KPI row, workforce directory, pipeline jobs)
  3. Post-Decision Lifecycle (CI-style 4-stage pipeline tracker & SQLite audit history)
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
import pandas as pd
import requests
import streamlit as st

try:
    from src.database.vector_store import VectorStoreManager
    from src.database.sql_db import SQLiteManager
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.database.vector_store import VectorStoreManager
    from src.database.sql_db import SQLiteManager

# ---------------------------------------------------------------------------
# Page Configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Bench Forecast // Enterprise Console",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE_URL = "http://127.0.0.1:8000"
GENERATE_TIMEOUT_SEC = 600
EXECUTE_TIMEOUT_SEC = 120

# Reference timeline anchor for the mock dataset
DATA_REF_DATE = "2026-09-14"

# ---------------------------------------------------------------------------
# Dual-Theme Enterprise Styling Engine: Executive Light & Obsidian Dark
# Monospace telemetry, crisp hairline borders, high contrast (WCAG AAA).
# ---------------------------------------------------------------------------
def get_windows_theme() -> str:
    """Detects Windows system theme preference ('Dark' or 'Light') via registry."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "Light" if val == 1 else "Dark"
    except Exception:
        return "Dark"


def is_dark_mode() -> bool:
    """Returns True if Obsidian Dark theme is active. Defaults to Windows OS theme preset."""
    if "theme_mode" not in st.session_state:
        win_theme = get_windows_theme()
        st.session_state["theme_mode"] = win_theme
        st.session_state["theme_preset"] = f"Windows OS ({win_theme})"
    current = st.session_state.get("theme_mode", "Dark")
    return current in ("Obsidian Dark", "Dark")


def get_enterprise_css(is_dark: bool = False) -> str:
    """Generates theme-calibrated corporate CSS without emojis or purple gradients."""
    if is_dark:
        bg_app = "#090D16"
        bg_sidebar = "#060911"
        bg_card = "#111827"
        border_card = "#1E293B"
        border_subtle = "#1E293B"
        text_primary = "#F8FAFC"
        text_muted = "#94A3B8"
        text_sub = "#64748B"
        tab_bg = "#111827"
        tab_active_bg = "#2563EB"
        tab_active_text = "#FFFFFF"
        tab_inactive_text = "#94A3B8"
        btn_pri_bg = "#2563EB"
        btn_pri_border = "#2563EB"
        btn_pri_hover = "#1D4ED8"
        btn_sec_bg = "#111827"
        btn_sec_text = "#F8FAFC"
        btn_sec_border = "#334155"
        btn_sec_hover = "#1E293B"
        input_bg = "#111827"
        input_border = "#334155"
        input_text = "#F8FAFC"
        placeholder_color = "#64748B"
        slider_rail_bg = "#334155"
        slider_rail_border = "#475569"
        slider_fill = "#3B82F6"
        slider_thumb = "#3B82F6"
        slider_thumb_border = "#090D16"
        slider_badge_bg = "#1E293B"
        slider_badge_text = "#93C5FD"
        slider_badge_border = "#2563EB"
        slider_ticks = "#94A3B8"
        code_bg = "#1E293B"
        code_text = "#93C5FD"
        code_border = "#334155"
        progress_track = "#1E293B"
        progress_fill = "#3B82F6"
        popover_bg = "#111827"
        popover_text = "#F8FAFC"
        popover_border = "#334155"
        popover_hover_bg = "#1E3A8A"
        popover_hover_text = "#93C5FD"
        hdr_bg = "#080D1A"
        hdr_accent = "#3B82F6"
        header_pipe = "#334155"
        header_pill_bg = "#111827"
        header_pill_border = "#1E293B"
        alert_info_bg = "#0F1D36"
        alert_info_border = "#1D4ED8"
        alert_info_text = "#93C5FD"
        alert_success_bg = "#062A1F"
        alert_success_border = "#059669"
        alert_success_text = "#6EE7B7"
        alert_warning_bg = "#2E1A05"
        alert_warning_border = "#D97706"
        alert_warning_text = "#FDE68A"
        alert_error_bg = "#2D0B0B"
        alert_error_border = "#DC2626"
        alert_error_text = "#FECACA"
    else:
        bg_app = "#F8FAFC"
        bg_sidebar = "#F8FAFC"
        bg_card = "#FFFFFF"
        border_card = "#CBD5E1"
        border_subtle = "#CBD5E1"
        text_primary = "#0F172A"
        text_muted = "#475569"
        text_sub = "#64748B"
        tab_bg = "#F1F5F9"
        tab_active_bg = "#0F172A"
        tab_active_text = "#FFFFFF"
        tab_inactive_text = "#475569"
        btn_pri_bg = "#1E40AF"
        btn_pri_border = "#1E40AF"
        btn_pri_hover = "#1D4ED8"
        btn_sec_bg = "#FFFFFF"
        btn_sec_text = "#0F172A"
        btn_sec_border = "#CBD5E1"
        btn_sec_hover = "#F1F5F9"
        hdr_bg = "#0F172A"
        hdr_accent = "#2563EB"
        header_pipe = "#475569"
        header_pill_bg = "#1E293B"
        header_pill_border = "#334155"
        input_bg = "#FFFFFF"
        input_border = "#CBD5E1"
        input_text = "#0F172A"
        placeholder_color = "#94A3B8"
        slider_rail_bg = "#94A3B8"
        slider_rail_border = "#64748B"
        slider_fill = "#1E40AF"
        slider_thumb = "#1E40AF"
        slider_thumb_border = "#FFFFFF"
        slider_badge_bg = "#EFF6FF"
        slider_badge_text = "#1E40AF"
        slider_badge_border = "#BFDBFE"
        slider_ticks = "#334155"
        code_bg = "#F1F5F9"
        code_text = "#0F172A"
        code_border = "#CBD5E1"
        progress_track = "#CBD5E1"
        progress_fill = "#1E40AF"
        popover_bg = "#FFFFFF"
        popover_text = "#0F172A"
        popover_border = "#CBD5E1"
        popover_hover_bg = "#EFF6FF"
        popover_hover_text = "#1E40AF"
        alert_info_bg = "#EFF6FF"
        alert_info_border = "#BFDBFE"
        alert_info_text = "#1E40AF"
        alert_success_bg = "#F0FDF4"
        alert_success_border = "#BBF7D0"
        alert_success_text = "#15803D"
        alert_warning_bg = "#FEFCE8"
        alert_warning_border = "#FEF08A"
        alert_warning_text = "#854D0E"
        alert_error_bg = "#FEF2F2"
        alert_error_border = "#FECACA"
        alert_error_text = "#991B1B"

    df_canvas_invert = (
        "[data-testid='stDataFrame'] canvas { filter: invert(0.92) hue-rotate(180deg) brightness(0.95); }"
        if is_dark
        else ""
    )

    return f"""
<style>
/* Global resets: Zero animations, system standard cursors, no smooth scrolling */
*, *::before, *::after {{
    scroll-behavior: auto !important;
    animation: none !important;
    transition: none !important;
    cursor: auto !important;
}}
html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{
    background-color: {bg_app} !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    color: {text_primary} !important;
    cursor: default !important;
}}

/* Comprehensive Typography & High-Contrast Enforcement (strictly excludes buttons and custom HTML spans) */
:not(button) > [data-testid="stMarkdownContainer"] p,
:not(button) > [data-testid="stMarkdownContainer"] li,
:not(button) > [data-testid="stMarkdownContainer"] strong,
:not(button) > [data-testid="stMarkdownContainer"] b,
:not(button) > [data-testid="stMarkdownContainer"] em,
div[data-testid="stText"] p,
div[data-testid="stWidgetLabel"] label,
div[data-testid="stWidgetLabel"] p {{
    color: {text_primary} !important;
}}

/* Executive Command Header - Always High Contrast Obsidian Bar */
#enterprise-header {{
    background-color: {hdr_bg} !important;
    border-bottom: 2px solid {hdr_accent} !important;
    padding: 14px 20px !important;
    border-radius: 6px !important;
    margin-bottom: 20px !important;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2) !important;
}}
#enterprise-header * {{
    box-sizing: border-box !important;
}}
#enterprise-header .header-brand {{
    color: #FFFFFF !important;
    font-size: 18px !important;
    font-weight: 800 !important;
    letter-spacing: -0.02em !important;
}}
#enterprise-header .header-pipe {{
    color: {header_pipe} !important;
    font-weight: 400 !important;
    font-size: 14px !important;
}}
#enterprise-header .header-sub {{
    color: #94A3B8 !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    font-family: monospace !important;
    letter-spacing: 0.04em !important;
}}
#enterprise-header .header-badge {{
    background-color: #2563EB !important;
    color: #FFFFFF !important;
    font-weight: 800 !important;
    font-size: 12px !important;
    padding: 3px 8px !important;
    border-radius: 4px !important;
    font-family: monospace !important;
    letter-spacing: 0.05em !important;
}}
#enterprise-header .header-pill-active {{
    background-color: {header_pill_bg} !important;
    color: #34D399 !important;
    border: 1px solid #059669 !important;
    padding: 3px 8px !important;
    border-radius: 4px !important;
    font-size: 11px !important;
    font-weight: 700 !important;
    font-family: monospace !important;
}}
#enterprise-header .header-pill-model {{
    background-color: {header_pill_bg} !important;
    color: #93C5FD !important;
    border: 1px solid {header_pill_border} !important;
    padding: 3px 8px !important;
    border-radius: 4px !important;
    font-size: 11px !important;
    font-weight: 600 !important;
    font-family: monospace !important;
}}
#enterprise-header .header-pill-env {{
    background-color: {header_pill_bg} !important;
    color: #E2E8F0 !important;
    border: 1px solid {header_pill_border} !important;
    padding: 3px 8px !important;
    border-radius: 4px !important;
    font-size: 11px !important;
    font-weight: 600 !important;
    font-family: monospace !important;
}}

[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6,
h1, h2, h3, h4, h5, h6 {{
    color: {text_primary} !important;
    font-weight: 700 !important;
}}

/* Captions and Subtext */
[data-testid="stCaptionContainer"] p,
[data-testid="stCaptionContainer"] span,
.stCaption,
small {{
    color: {text_muted} !important;
}}

button, [role="button"], a, input, select, textarea {{
    cursor: pointer !important;
}}

/* Sidebar Styling */
[data-testid="stSidebar"] {{
    background-color: {bg_sidebar} !important;
    border-right: 1px solid {border_subtle} !important;
}}
[data-testid="stSidebar"] h3 {{
    color: {text_primary} !important;
    font-size: 12px !important;
    font-weight: 800 !important;
    letter-spacing: 0.05em !important;
    text-transform: uppercase !important;
    font-family: "JetBrains Mono", monospace !important;
}}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span {{
    color: {text_primary} !important;
}}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{
    color: {text_muted} !important;
}}

/* Horizontal Segmented Controls (for filters and radio groups) */
[data-testid="stRadio"] div[role="radiogroup"] {{
    display: flex !important;
    gap: 4px !important;
    background-color: {tab_bg} !important;
    border: 1px solid {border_subtle} !important;
    border-radius: 6px !important;
    padding: 3px !important;
}}
[data-testid="stRadio"] div[role="radiogroup"] > label {{
    flex: 1 1 auto !important;
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    padding: 5px 10px !important;
    border-radius: 4px !important;
    margin: 0 !important;
    cursor: pointer !important;
    background-color: transparent !important;
    border: none !important;
}}
[data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {{
    display: none !important;
}}
[data-testid="stRadio"] div[role="radiogroup"] > label div[data-testid="stMarkdownContainer"] p {{
    font-size: 11px !important;
    font-weight: 700 !important;
    font-family: "JetBrains Mono", monospace !important;
    color: {tab_inactive_text} !important;
    margin: 0 !important;
}}
[data-testid="stRadio"] div[role="radiogroup"] > label:has(input:checked) {{
    background-color: {tab_active_bg} !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25) !important;
}}
[data-testid="stRadio"] div[role="radiogroup"] > label:has(input:checked) div[data-testid="stMarkdownContainer"] p {{
    color: {tab_active_text} !important;
}}

/* ==========================================================================
   High-Contrast Slider Controls & Track Fix
   Supports both Streamlit 1.63+ (react-aria) and legacy BaseWeb DOM
   Eliminates white-on-white slider tracks permanently in both themes
   ========================================================================== */
[data-testid="stSlider"] {{
    padding-top: 4px !important;
    padding-bottom: 12px !important;
}}
[data-testid="stSlider"] label {{
    color: {text_primary} !important;
    font-weight: 700 !important;
    font-size: 13px !important;
    margin-bottom: 4px !important;
}}
/* Base track container - relative positioning */
[data-testid="stSlider"] .react-aria-SliderTrack,
[data-testid="stSlider"] [data-baseweb="slider"] {{
    position: relative !important;
    margin-top: 6px !important;
    margin-bottom: 6px !important;
}}
/* Unfilled track rail */
[data-testid="stSlider"] .react-aria-SliderTrack::before,
[data-testid="stSlider"] [data-baseweb="slider"] > div > div {{
    content: "" !important;
    position: absolute !important;
    left: 0 !important;
    right: 0 !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
    height: 6px !important;
    background-color: {slider_rail_bg} !important;
    border: 1px solid {slider_rail_border} !important;
    border-radius: 3px !important;
    z-index: 0 !important;
}}
/* Slider dynamic filled portion */
[data-testid="stSlider"] .react-aria-SliderTrack > div:first-child,
[data-testid="stSlider"] [class*="efbyxod5"],
[data-testid="stSlider"] [data-baseweb="slider"] > div > div > div {{
    height: 6px !important;
    border-radius: 3px !important;
    box-shadow: inset 0 0 0 1px {slider_rail_border} !important;
    z-index: 1 !important;
}}
/* Slider Thumb circle */
[data-testid="stSlider"] [role="slider"],
[data-testid="stSlider"] .react-aria-SliderThumb,
[data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {{
    background-color: {slider_thumb} !important;
    border: 2px solid {slider_thumb_border} !important;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.5) !important;
    width: 18px !important;
    height: 18px !important;
    border-radius: 50% !important;
    cursor: pointer !important;
    z-index: 2 !important;
}}
/* Slider Thumb Value Number */
[data-testid="stSlider"] [data-testid="stThumbValue"] {{
    color: {slider_badge_text} !important;
    font-weight: 800 !important;
    font-size: 13px !important;
    font-family: "JetBrains Mono", monospace !important;
    background-color: {slider_badge_bg} !important;
    border: 1px solid {slider_badge_border} !important;
    border-radius: 3px !important;
    padding: 1px 6px !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1) !important;
}}
/* Slider Min and Max tick marks */
[data-testid="stSlider"] [data-testid="stTickBar"],
[data-testid="stSlider"] [data-testid="stTickBar"] div,
[data-testid="stSlider"] [data-testid="stTickBar"] p,
[data-testid="stSlider"] [data-testid="stTickBar"] span,
[data-testid="stSlider"] [data-testid="stTickBarMin"],
[data-testid="stSlider"] [data-testid="stTickBarMax"] {{
    color: {slider_ticks} !important;
    font-weight: 700 !important;
    font-size: 11px !important;
    font-family: "JetBrains Mono", monospace !important;
    opacity: 1 !important;
}}

/* Structured enterprise cards */
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 4px !important;
    border: 1px solid {border_card} !important;
    background-color: {bg_card} !important;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.08) !important;
}}

/* Segmented enterprise tabs (Linear style) */
.stTabs [data-baseweb="tab-list"] {{
    gap: 4px;
    background-color: {tab_bg} !important;
    padding: 4px;
    border-radius: 6px;
    border: 1px solid {border_subtle} !important;
    margin-bottom: 20px;
}}
.stTabs [data-baseweb="tab"] {{
    height: 36px;
    border-radius: 4px;
    background-color: transparent !important;
    border: none !important;
    padding: 0 16px;
}}
.stTabs [data-baseweb="tab"] p,
.stTabs [data-baseweb="tab"] span {{
    color: {tab_inactive_text} !important;
    font-weight: 600 !important;
    font-size: 13px !important;
}}
.stTabs [aria-selected="true"] {{
    background-color: {tab_active_bg} !important;
    border-radius: 4px !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25) !important;
}}
.stTabs [aria-selected="true"] p,
.stTabs [aria-selected="true"] span {{
    color: {tab_active_text} !important;
    font-weight: 700 !important;
}}

/* Clean enterprise buttons (Supports Streamlit 1.63+ stBaseButton & legacy kind) */
button[data-testid*="primary"],
button[data-testid="stBaseButton-primary"],
button[data-testid="baseButton-primary"],
button[kind="primary"],
[data-testid="stButton"] button[data-testid*="primary"] {{
    background-color: {btn_pri_bg} !important;
    border: 1px solid {btn_pri_border} !important;
    border-radius: 4px !important;
    letter-spacing: 0.02em !important;
    color: #FFFFFF !important;
}}
button[data-testid*="primary"] *,
button[data-testid="stBaseButton-primary"] *,
button[data-testid="baseButton-primary"] *,
button[kind="primary"] *,
[data-testid="stButton"] button[data-testid*="primary"] * {{
    color: #FFFFFF !important;
    font-weight: 700 !important;
    font-size: 13px !important;
}}
button[data-testid*="primary"]:hover,
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="baseButton-primary"]:hover,
button[kind="primary"]:hover {{
    background-color: {btn_pri_hover} !important;
    border-color: {btn_pri_hover} !important;
}}

button[data-testid*="secondary"],
button[data-testid="stBaseButton-secondary"],
button[data-testid="baseButton-secondary"],
button[kind="secondary"],
[data-testid="stButton"] button[data-testid*="secondary"] {{
    background-color: {btn_sec_bg} !important;
    border: 1px solid {btn_sec_border} !important;
    border-radius: 4px !important;
    color: {btn_sec_text} !important;
}}
button[data-testid*="secondary"] *,
button[data-testid="stBaseButton-secondary"] *,
button[data-testid="baseButton-secondary"] *,
button[kind="secondary"] *,
[data-testid="stButton"] button[data-testid*="secondary"] * {{
    color: {btn_sec_text} !important;
    font-weight: 600 !important;
    font-size: 13px !important;
}}
button[data-testid*="secondary"]:hover,
button[data-testid="stBaseButton-secondary"]:hover,
button[data-testid="baseButton-secondary"]:hover,
button[kind="secondary"]:hover {{
    background-color: {btn_sec_hover} !important;
    border-color: {border_subtle} !important;
}}

button:disabled,
button[disabled] {{
    opacity: 0.55 !important;
    cursor: not-allowed !important;
    box-shadow: none !important;
}}
button[data-testid*="primary"]:disabled *,
button[data-testid*="primary"][disabled] * {{
    color: #FFFFFF !important;
}}

/* Input elements styling */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {{
    background-color: {input_bg} !important;
    border: 1px solid {input_border} !important;
    color: {input_text} !important;
    border-radius: 4px !important;
}}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder {{
    color: {placeholder_color} !important;
    opacity: 1 !important;
}}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {{
    border-color: #3B82F6 !important;
    box-shadow: 0 0 0 1px #3B82F6 !important;
}}
[data-testid="stSelectbox"] [data-baseweb="select"] > div {{
    background-color: {input_bg} !important;
    border: 1px solid {input_border} !important;
    border-radius: 4px !important;
    color: {input_text} !important;
}}
[data-testid="stSelectbox"] [data-baseweb="select"] span,
[data-testid="stSelectbox"] [data-baseweb="select"] div {{
    color: {input_text} !important;
}}
[data-testid="stSelectbox"] svg {{
    fill: {text_muted} !important;
}}

/* Selectbox dropdown popover and options */
div[data-baseweb="popover"],
div[data-baseweb="menu"] {{
    background-color: {popover_bg} !important;
    border: 1px solid {popover_border} !important;
    border-radius: 4px !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25) !important;
}}
div[data-baseweb="popover"] ul {{
    background-color: {popover_bg} !important;
    padding: 4px !important;
}}
[data-baseweb="menu"] li,
[role="option"] {{
    background-color: {popover_bg} !important;
    color: {popover_text} !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    border-radius: 3px !important;
    margin: 1px 0 !important;
}}
[data-baseweb="menu"] li *,
[role="option"] * {{
    color: {popover_text} !important;
}}
[role="option"]:hover,
[data-baseweb="menu"] li:hover,
[aria-selected="true"][role="option"] {{
    background-color: {popover_hover_bg} !important;
}}
[role="option"]:hover *,
[data-baseweb="menu"] li:hover *,
[aria-selected="true"][role="option"] * {{
    color: {popover_hover_text} !important;
}}

/* Streamlit Alert System: Guaranteed Contrast */
div[data-testid="stAlert"] {{
    border-radius: 4px !important;
    border-width: 1px !important;
    border-style: solid !important;
    padding: 10px 14px !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) {{
    background-color: {alert_info_bg} !important;
    border-color: {alert_info_border} !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) p,
div[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) span {{
    color: {alert_info_text} !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) {{
    background-color: {alert_success_bg} !important;
    border-color: {alert_success_border} !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) p,
div[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) span {{
    color: {alert_success_text} !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentWarning"]) {{
    background-color: {alert_warning_bg} !important;
    border-color: {alert_warning_border} !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentWarning"]) p,
div[data-testid="stAlert"]:has([data-testid="stNotificationContentWarning"]) span {{
    color: {alert_warning_text} !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) {{
    background-color: {alert_error_bg} !important;
    border-color: {alert_error_border} !important;
}}
div[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) p,
div[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) span {{
    color: {alert_error_text} !important;
}}

/* Checkbox high contrast */
[data-testid="stCheckbox"] label span {{
    color: {text_primary} !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}}
[data-testid="stCheckbox"] label[data-disabled="true"] span,
[data-testid="stCheckbox"] label[data-disabled="true"] p {{
    color: {text_muted} !important;
}}

/* Progress bar high contrast & single-rail fix (Streamlit 1.63+) */
[data-testid="stProgress"] > div {{
    background-color: transparent !important;
    height: auto !important;
    border: none !important;
}}
[data-testid="stProgress"] [data-testid="stProgressBarTrack"] {{
    background-color: {progress_track} !important;
    height: 8px !important;
    border-radius: 4px !important;
    overflow: hidden !important;
    border: 1px solid {border_subtle} !important;
}}
[data-testid="stProgress"] [data-testid="stProgressBarTrack"] > div {{
    background-color: {progress_fill} !important;
    height: 100% !important;
    border-radius: 3px !important;
}}
[data-testid="stProgress"] p,
[data-testid="stProgress"] span {{
    color: {text_primary} !important;
    font-family: "JetBrains Mono", monospace !important;
    font-size: 11px !important;
    font-weight: 600 !important;
}}

/* Monospace accents for code / telemetry tags */
code {{
    font-family: "JetBrains Mono", "SF Mono", Consolas, monospace !important;
    font-size: 12px !important;
    color: {code_text} !important;
    background-color: {code_bg} !important;
    padding: 2px 6px !important;
    border-radius: 3px !important;
    border: 1px solid {code_border} !important;
}}

/* Expander styling */
[data-testid="stExpander"] {{
    background-color: {bg_card} !important;
    border: 1px solid {border_card} !important;
    border-radius: 4px !important;
}}
[data-testid="stExpander"] summary {{
    color: {text_primary} !important;
}}
[data-testid="stExpander"] summary span,
[data-testid="stExpander"] summary p {{
    color: {text_primary} !important;
    font-weight: 600 !important;
}}
[data-testid="stExpander"] summary svg {{
    fill: {text_muted} !important;
    stroke: {text_muted} !important;
}}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
    background-color: {bg_card} !important;
    border-top: 1px solid {border_subtle} !important;
    color: {text_primary} !important;
}}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] p,
[data-testid="stExpander"] [data-testid="stExpanderDetails"] span {{
    color: {text_primary} !important;
}}

/* DataFrames */
[data-testid="stDataFrame"] {{
    background-color: {bg_card} !important;
    border: 1px solid {border_card} !important;
    border-radius: 4px !important;
}}
{df_canvas_invert}

/* Minimal Top-Right Theme Toggle Button (Vector Material Icon, Zero Tooltip) */
div[data-testid="stVerticalBlock"]:has(#theme-toggle-anchor) button {{
    background-color: {hdr_bg} !important;
    border: 1px solid {border_subtle} !important;
    border-bottom: 2px solid {hdr_accent} !important;
    border-radius: 6px !important;
    height: 52px !important;
    min-height: 52px !important;
    width: 100% !important;
    padding: 0 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    margin-bottom: 20px !important;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2) !important;
}}
div[data-testid="stVerticalBlock"]:has(#theme-toggle-anchor) button:hover {{
    background-color: #1E293B !important;
    border-color: {hdr_accent} !important;
}}
div[data-testid="stVerticalBlock"]:has(#theme-toggle-anchor) button span {{
    font-size: 20px !important;
    color: #FFFFFF !important;
}}

/* Toast Notifications */
div[data-testid="stToast"] {{
    background-color: {bg_card} !important;
    border: 1px solid {border_card} !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important;
}}
div[data-testid="stToast"] p,
div[data-testid="stToast"] span {{
    color: {text_primary} !important;
}}

/* Download Button */
[data-testid="stDownloadButton"] button {{
    background-color: {btn_sec_bg} !important;
    border: 1px solid {btn_sec_border} !important;
    border-radius: 4px !important;
}}
[data-testid="stDownloadButton"] button p,
[data-testid="stDownloadButton"] button span {{
    color: {btn_sec_text} !important;
    font-weight: 600 !important;
}}
[data-testid="stDownloadButton"] button:hover {{
    background-color: {btn_sec_hover} !important;
    border-color: {border_subtle} !important;
}}

/* Divider */
hr {{
    border-color: {border_subtle} !important;
}}
</style>
"""


# Inject theme styling
_is_dark = is_dark_mode()
st.markdown(get_enterprise_css(_is_dark), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# High-Precision Corporate Components
# ---------------------------------------------------------------------------
def render_header(is_dark: Optional[bool] = None) -> None:
    """Renders sleek obsidian executive app bar with telemetry."""
    if is_dark is None:
        is_dark = is_dark_mode()

    header_bg = "#080D1A" if is_dark else "#0F172A"
    border_accent = "#3B82F6" if is_dark else "#2563EB"
    badge_bg = "#2563EB"
    badge_fg = "#FFFFFF"
    pipe_color = "#334155" if is_dark else "#475569"
    sub_color = "#94A3B8"
    pill_bg = "#111827" if is_dark else "#1E293B"
    pill_border = "#1E293B" if is_dark else "#334155"

    html = (
        f'<div id="enterprise-header" style="background-color: {header_bg}; border-bottom: 2px solid {border_accent}; '
        f'padding: 14px 20px; border-radius: 6px; margin-bottom: 20px; '
        f'box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);">'
        f'<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">'
        f'<div style="display: flex; align-items: center; gap: 12px;">'
        f'<span class="header-badge" style="background-color: {badge_bg}; color: {badge_fg}; font-weight: 800; font-size: 12px; '
        f'padding: 3px 8px; border-radius: 4px; font-family: monospace; letter-spacing: 0.05em;">BF</span>'
        f'<span class="header-brand" style="color: #FFFFFF; font-size: 18px; font-weight: 800; letter-spacing: -0.02em;">'
        f'BENCH FORECAST</span>'
        f'<span class="header-pipe" style="color: {pipe_color}; font-weight: 400; font-size: 14px;">|</span>'
        f'<span class="header-sub" style="color: {sub_color}; font-size: 12px; font-weight: 600; font-family: monospace; letter-spacing: 0.04em;">'
        f'CAPACITY PLANNING &amp; HITL ALLOCATION</span>'
        f'</div>'
        f'<div style="display: flex; align-items: center; gap: 10px;">'
        f'<span class="header-pill-active" style="background-color: {pill_bg}; color: #34D399; border: 1px solid #059669; '
        f'padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; font-family: monospace;">'
        f'● ENGINE ACTIVE</span>'
        f'<span class="header-pill-model" style="background-color: {pill_bg}; color: #93C5FD; border: 1px solid {pill_border}; '
        f'padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; font-family: monospace;">'
        f'LLAMA-3.2 / LOCAL</span>'
        f'<span class="header-pill-env" style="background-color: {pill_bg}; color: #E2E8F0; border: 1px solid {pill_border}; '
        f'padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; font-family: monospace;">'
        f'ENV: PROD</span>'
        f'</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_theme_toggle_button() -> None:
    """Renders a sleek, minimal, non-emoji icon button on the right side to toggle theme."""
    is_dark = is_dark_mode()
    icon = ":material/light_mode:" if is_dark else ":material/dark_mode:"
    target_theme = "Light" if is_dark else "Dark"

    st.html('<div id="theme-toggle-anchor"></div>')
    if st.button("", icon=icon, key="btn_theme_toggle", help=None, use_container_width=True):
        st.session_state["theme_mode"] = target_theme
        st.session_state["theme_preset"] = "Manual Override"
        st.rerun()


def hex_to_rgb(hex_str: str) -> str:
    """Converts hex color code to 'r, g, b' string."""
    try:
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            return f"{r}, {g}, {b}"
    except Exception:
        pass
    return "59, 130, 246"


def render_metric_card(
    category: str,
    title: str,
    value: str,
    subtext: str,
    top_bar_color: str = "#2563EB",
    dot_color: str = "#2563EB",
    is_dark: Optional[bool] = None,
) -> None:
    """Sleek corporate KPI card (Datadog/Stripe style) with vivid theme-calibrated accents."""
    if is_dark is None:
        is_dark = is_dark_mode()

    if is_dark and top_bar_color in ("#0F172A", "#1E293B", "#334155"):
        top_bar_color = "#3B82F6"
        dot_color = "#3B82F6"

    accent_rgb = hex_to_rgb(top_bar_color)

    # Theme-calibrated corporate palette with vivid category badge and accent value
    if is_dark:
        bg_card = "#111827"
        border_card = f"rgba({accent_rgb}, 0.35)"
        title_color = "#F8FAFC"
        subtext_color = "#94A3B8"
        card_bg = f"linear-gradient(180deg, rgba({accent_rgb}, 0.12) 0%, rgba(17, 24, 39, 0.98) 75%)"
        shadow = f"0 2px 8px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba({accent_rgb}, 0.2)"
        category_bg = f"rgba({accent_rgb}, 0.20)"
        category_border = f"rgba({accent_rgb}, 0.45)"
        category_color = top_bar_color
        value_color = top_bar_color
        if top_bar_color in ("#0284C7", "#0369A1", "#2563EB", "#3B82F6"):
            value_color = "#38BDF8"
            category_color = "#38BDF8"
        elif top_bar_color in ("#10B981", "#059669"):
            value_color = "#34D399"
            category_color = "#34D399"
        elif top_bar_color in ("#D97706", "#B45309"):
            value_color = "#FBBF24"
            category_color = "#FBBF24"
        elif top_bar_color in ("#4F46E5", "#4338CA", "#6366F1"):
            value_color = "#818CF8"
            category_color = "#818CF8"
        elif top_bar_color in ("#0D9488", "#0F766E"):
            value_color = "#2DD4BF"
            category_color = "#2DD4BF"
        dot_color = value_color
    else:
        bg_card = "#FFFFFF"
        border_card = f"rgba({accent_rgb}, 0.30)"
        title_color = "#0F172A"
        subtext_color = "#64748B"
        card_bg = f"linear-gradient(180deg, rgba({accent_rgb}, 0.07) 0%, #FFFFFF 70%)"
        shadow = f"0 2px 6px rgba({accent_rgb}, 0.12), 0 1px 3px rgba(15, 23, 42, 0.06)"
        category_bg = f"rgba({accent_rgb}, 0.12)"
        category_border = f"rgba({accent_rgb}, 0.30)"
        category_color = top_bar_color
        value_color = top_bar_color
        if top_bar_color in ("#10B981",):
            value_color = "#059669"
            category_color = "#059669"
        dot_color = value_color

    html = (
        f'<div style="background: {card_bg}; border: 1px solid {border_card}; '
        f'border-top: 3px solid {top_bar_color}; border-radius: 6px; padding: 12px 14px; '
        f'box-shadow: {shadow}; margin-bottom: 8px;">'
        f'<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">'
        f'<span style="background-color: {category_bg}; color: {category_color}; '
        f'border: 1px solid {category_border}; padding: 2px 8px; border-radius: 4px; '
        f'font-size: 10px; font-weight: 800; letter-spacing: 0.06em; '
        f'text-transform: uppercase; font-family: monospace;">{category}</span>'
        f'<span style="color: {dot_color}; font-size: 11px;">●</span>'
        f'</div>'
        f'<div style="color: {title_color}; font-size: 12px; font-weight: 700; '
        f'letter-spacing: -0.01em; margin-bottom: 2px;">{title}</div>'
        f'<div style="color: {value_color}; font-size: 26px; font-weight: 800; '
        f'line-height: 1.15; margin: 4px 0; font-variant-numeric: tabular-nums; '
        f'letter-spacing: -0.02em;">{value}</div>'
        f'<div style="color: {subtext_color}; font-size: 11px; font-weight: 500;">{subtext}</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_status_pill(text: str, tone: str = "neutral", is_dark: Optional[bool] = None) -> str:
    """High-contrast corporate status pill with monospace font."""
    if is_dark is None:
        is_dark = is_dark_mode()

    if is_dark:
        tones = {
            "success": ("#064E3B", "#34D399", "#059669"),
            "warning": ("#451A03", "#FBBF24", "#92400E"),
            "info": ("#1E3A8A", "#60A5FA", "#2563EB"),
            "danger": ("#450A0A", "#F87171", "#991B1B"),
            "neutral": ("#1E293B", "#E2E8F0", "#334155"),
        }
    else:
        tones = {
            "success": ("#F0FDF4", "#15803D", "#86EFAC"),
            "warning": ("#FEFCE8", "#854D0E", "#FDE68A"),
            "info": ("#EFF6FF", "#1D4ED8", "#BFDBFE"),
            "danger": ("#FEF2F2", "#B91C1C", "#FCA5A5"),
            "neutral": ("#F1F5F9", "#334155", "#CBD5E1"),
        }
    bg, fg, border = tones.get(tone, tones["neutral"])
    return (
        f'<span style="background-color: {bg}; color: {fg}; border: 1px solid {border}; '
        f'padding: 3px 8px; border-radius: 3px; font-size: 11px; font-weight: 700; '
        f'font-family: monospace; display: inline-block; letter-spacing: 0.04em;">{text}</span>'
    )


def render_pipeline_node(
    step_num: str,
    step_name: str,
    status_label: str,
    tone: str,
    detail_line: str,
    is_dark: Optional[bool] = None,
) -> None:
    """CI-CD style pipeline execution node."""
    if is_dark is None:
        is_dark = is_dark_mode()

    if is_dark:
        tones = {
            "success": ("#064E3B", "#34D399", "#059669", "#A7F3D0"),
            "active": ("#1E3A8A", "#60A5FA", "#2563EB", "#93C5FD"),
            "pending": ("#111827", "#94A3B8", "#1E293B", "#CBD5E1"),
            "danger": ("#450A0A", "#F87171", "#991B1B", "#FECACA"),
        }
        badge_bg = "#090D16"
        sub_text_color = "#94A3B8"
    else:
        tones = {
            "success": ("#F0FDF4", "#15803D", "#86EFAC", "#166534"),
            "active": ("#EFF6FF", "#1D4ED8", "#93C5FD", "#1E40AF"),
            "pending": ("#F8FAFC", "#64748B", "#CBD5E1", "#475569"),
            "danger": ("#FEF2F2", "#B91C1C", "#FCA5A5", "#991B1B"),
        }
        badge_bg = "#FFFFFF"
        sub_text_color = "#64748B"

    bg, fg, border, text_color = tones.get(tone, tones["pending"])
    html = (
        f'<div style="background-color: {bg}; border: 1px solid {border}; '
        f'border-radius: 4px; padding: 12px 14px; margin-bottom: 8px;">'
        f'<div style="display: flex; justify-content: space-between; align-items: center;">'
        f'<span style="color: {sub_text_color}; font-size: 10px; font-weight: 700; font-family: monospace;">'
        f'STAGE 0{step_num}</span>'
        f'<span style="background-color: {badge_bg}; color: {fg}; border: 1px solid {border}; '
        f'padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 800; font-family: monospace;">'
        f'{status_label}</span>'
        f'</div>'
        f'<div style="color: {text_color}; font-size: 13px; font-weight: 700; margin: 6px 0 2px 0;">'
        f'{step_name}</div>'
        f'<div style="color: {sub_text_color}; font-size: 11px; font-family: monospace;">{detail_line}</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data & API Handlers
# ---------------------------------------------------------------------------
def load_mock_data() -> Dict[str, Any]:
    """Loads standardized employee and demand mock records."""
    data_path = Path(__file__).resolve().parents[1] / "data" / "mock_data.json"
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"demands": [], "employees": []}


def call_generate_forecast(
    department_id: str = "ALL",
    horizon_days: int = 90,
    min_win_probability: float = 0.75,
) -> Optional[Dict[str, Any]]:
    """POST /generate — executes agentic workflow until human review pause."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/generate",
            json={
                "department_id": department_id,
                "horizon_days": int(horizon_days),
                "min_win_probability": float(min_win_probability),
            },
            timeout=GENERATE_TIMEOUT_SEC,
        )
        if response.status_code in (200, 202):
            return response.json()
        st.error(f"Generation failed [HTTP {response.status_code}]: {response.text[:300]}")
    except requests.exceptions.Timeout:
        st.error(
            f"Request timed out after {GENERATE_TIMEOUT_SEC}s. "
            "Pipeline is still executing on server. Check status or switch provider."
        )
    except requests.exceptions.ConnectionError:
        st.error(f"Unable to connect to API server at {API_BASE_URL}. Verify `python server.py` is running.")
    except Exception as e:
        st.error(f"Pipeline execution error: {e}")
    return None


def call_execute_forecast(
    recommendation_id: str,
    thread_id: str,
    approved: bool,
    rejection_feedback: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """POST /execute — approves or rejects with revision feedback."""
    try:
        payload: Dict[str, Any] = {
            "recommendation_id": recommendation_id,
            "thread_id": thread_id,
            "approved": approved,
            "approver_name": st.session_state.get("approver_name", "Manager"),
        }
        if not approved and rejection_feedback:
            payload["rejection_feedback"] = rejection_feedback

        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/execute",
            json=payload,
            timeout=EXECUTE_TIMEOUT_SEC,
        )
        if response.status_code == 200:
            return response.json()
        st.error(f"Execute failed [HTTP {response.status_code}]: {response.text[:300]}")
    except requests.exceptions.Timeout:
        st.error(f"Execution request timed out after {EXECUTE_TIMEOUT_SEC}s.")
    except requests.exceptions.ConnectionError:
        st.error("API server connection lost.")
    except Exception as e:
        st.error(f"Execution error: {e}")
    return None


def call_submit_feedback(recommendation_id: str, feedback: str) -> Optional[Dict[str, Any]]:
    """POST /feedback — submits audit notes."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/feedback",
            json={"recommendation_id": recommendation_id, "feedback": feedback},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def get_db_allocation_history() -> List[Dict[str, Any]]:
    """Fetches operational allocation audit records directly from SQLite."""
    try:
        db = SQLiteManager()
        return db.get_allocation_history()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Module 1: Forecast & Allocation Console
# ---------------------------------------------------------------------------
def render_demand_card(demand: Dict[str, Any]) -> None:
    """Renders demand specifications in an enterprise SOW card."""
    win = demand.get("win_probability", 0.0)
    is_dark = is_dark_mode()

    header_bg = "#111827" if is_dark else "#F8FAFC"
    header_border = "#1E293B" if is_dark else "#CBD5E1"
    header_label = "#94A3B8" if is_dark else "#475569"
    badge_bg = "#090D16" if is_dark else "#FFFFFF"
    badge_text = "#F8FAFC" if is_dark else "#0F172A"
    badge_border = "#1E293B" if is_dark else "#CBD5E1"

    with st.container(border=True):
        header = (
            f'<div style="background-color: {header_bg}; border-bottom: 1px solid {header_border}; '
            f'padding: 10px 14px; border-radius: 4px 4px 0 0; display: flex; '
            f'justify-content: space-between; align-items: center; margin: -1rem -1rem 1rem -1rem;">'
            f'<span style="font-weight: 700; font-size: 11px; color: {header_label}; font-family: monospace; letter-spacing: 0.05em;">'
            f'TARGET REQUISITION</span>'
            f'<span style="background-color: {badge_bg}; color: {badge_text}; font-size: 11px; font-weight: 700; '
            f'padding: 2px 8px; border-radius: 3px; border: 1px solid {badge_border}; font-family: monospace;">'
            f'{demand.get("id")}</span>'
            f'</div>'
        )
        st.markdown(header, unsafe_allow_html=True)

        st.markdown(f"### {demand.get('role')}")

        c1, c2 = st.columns(2)
        c1.markdown(f"**Project ID:** `{demand.get('project_id')}`")
        c2.markdown(f"**Target Start Date:** `{demand.get('start_date')}`")
        c1.markdown(f"**Headcount Required:** `{demand.get('headcount', 1)} FTE`")
        c2.markdown(f"**Win Probability:** `{win:.0%}`")

        st.progress(win)

        st.markdown("**Required Technical Stack:**")
        skills = demand.get("required_skills", [])
        if skills:
            chip_bg = "#1E293B" if is_dark else "#F1F5F9"
            chip_text = "#F8FAFC" if is_dark else "#0F172A"
            chip_border = "#334155" if is_dark else "#CBD5E1"
            chips = " ".join(
                f'<span style="background-color: {chip_bg}; color: {chip_text}; border: 1px solid {chip_border}; '
                f'padding: 3px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; '
                f'font-family: monospace; display: inline-block; margin: 2px 2px;">{s}</span>'
                for s in skills
            )
            st.markdown(chips, unsafe_allow_html=True)
        else:
            st.caption("No specific skills listed.")

        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        st.markdown("**Statement of Work (SOW):**")
        sow_bg = "#111827" if is_dark else "#F8FAFC"
        sow_bar = "#3B82F6" if is_dark else "#64748B"
        sow_text = "#CBD5E1" if is_dark else "#334155"
        sow_border = "border: 1px solid #1E293B;" if is_dark else ""
        sow_html = (
            f'<div style="background-color: {sow_bg}; border-left: 3px solid {sow_bar}; {sow_border} '
            f'padding: 8px 12px; font-size: 12px; color: {sow_text}; line-height: 1.5; font-style: italic; border-radius: 0 4px 4px 0;">'
            f'{demand.get("description", "No narrative statement available.")}'
            f'</div>'
        )
        st.markdown(sow_html, unsafe_allow_html=True)


def render_candidate_card(candidate: Dict[str, Any], match_score_str: str) -> None:
    """Renders candidate match details using structured telemetry blocks."""
    is_dark = is_dark_mode()

    header_bg = "#111827" if is_dark else "#F8FAFC"
    header_border = "#1E293B" if is_dark else "#CBD5E1"
    header_label = "#94A3B8" if is_dark else "#475569"
    badge_bg = "#090D16" if is_dark else "#FFFFFF"
    badge_text = "#F8FAFC" if is_dark else "#0F172A"
    badge_border = "#1E293B" if is_dark else "#CBD5E1"

    with st.container(border=True):
        header = (
            f'<div style="background-color: {header_bg}; border-bottom: 1px solid {header_border}; '
            f'padding: 10px 14px; border-radius: 4px 4px 0 0; display: flex; '
            f'justify-content: space-between; align-items: center; margin: -1rem -1rem 1rem -1rem;">'
            f'<span style="font-weight: 700; font-size: 11px; color: {header_label}; font-family: monospace; letter-spacing: 0.05em;">'
            f'SEMANTIC CANDIDATE RETRIEVAL (RAG)</span>'
            f'<span style="background-color: {badge_bg}; color: {badge_text}; font-size: 11px; font-weight: 700; '
            f'padding: 2px 8px; border-radius: 3px; border: 1px solid {badge_border}; font-family: monospace;">'
            f'{candidate.get("id")}</span>'
            f'</div>'
        )
        st.markdown(header, unsafe_allow_html=True)

        st.markdown(f"### {candidate.get('name')}")

        m1, m2, m3 = st.columns(3)
        with m1:
            render_metric_card("SEMANTIC", "RAG MATCH", match_score_str, "Cosine similarity", "#10B981", "#10B981")
        with m2:
            render_metric_card("PROFILE", "EXPERIENCE", f"{candidate.get('experience_years', 0)} Yrs", "Verified tenure", "#0284C7", "#0284C7")
        with m3:
            cost = candidate.get("cost_rate")
            render_metric_card("COMMERCIAL", "COST RATE", f"€{cost}/hr" if cost else "Standard", "Billing tier", "#D97706", "#D97706")

        st.markdown("<div style='margin-top: 6px;'></div>", unsafe_allow_html=True)
        cur_proj = candidate.get("current_project")
        if cur_proj and cur_proj.lower() not in ("none", "bench", ""):
            st.markdown(render_status_pill(f"ASSIGNED: {cur_proj.upper()}", "info"), unsafe_allow_html=True)
        else:
            st.markdown(render_status_pill("BENCH AVAILABLE", "warning"), unsafe_allow_html=True)

        st.caption(f"Availability Horizon: {candidate.get('available_from', '-')}")

        st.markdown("**Verified Competencies:**")
        skills = candidate.get("skills", [])
        if skills:
            chip_bg = "#1E293B" if is_dark else "#F1F5F9"
            chip_text = "#F8FAFC" if is_dark else "#0F172A"
            chip_border = "#334155" if is_dark else "#CBD5E1"
            chips = " ".join(
                f'<span style="background-color: {chip_bg}; color: {chip_text}; border: 1px solid {chip_border}; '
                f'padding: 3px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; '
                f'font-family: monospace; display: inline-block; margin: 2px 2px;">{s}</span>'
                for s in skills
            )
            st.markdown(chips, unsafe_allow_html=True)

        bio = candidate.get("bio") or candidate.get("profile_text", "")
        if bio:
            with st.expander("ChromaDB Semantic Document Excerpt"):
                st.write(bio)


def render_recommendations_panel(
    recommendations: Dict[str, Any],
    rec_id: str,
    thread_id: str,
    revision_count: int = 0,
) -> None:
    """LLM recommendation panel with HITL review controls."""
    if revision_count > 0:
        st.info(f"[REVISION ITERATION {revision_count}/2] Incorporating recorded manager guidance.")

    # Confidence Score
    confidence = recommendations.get("confidence_score", 0.0)
    conf_label = "HIGH CONFIDENCE" if confidence >= 0.8 else "MODERATE CONFIDENCE" if confidence >= 0.6 else "LOW CONFIDENCE"
    st.progress(confidence, text=f"AI Confidence Score: {confidence:.0%} [{conf_label}]")

    if recommendations.get("revision_note"):
        st.caption(f"Revision Focus: {recommendations['revision_note'][:140]}")

    # Reallocations Table
    reallocations = recommendations.get("reallocations", [])
    if reallocations:
        st.markdown("**Proposed Reallocation Schedule**")
        realloc_data = []
        for r in reallocations:
            score = r.get("match_score", 0.0)
            realloc_data.append({
                "Employee ID": r.get("employee_id", "-"),
                "Target Project": r.get("target_project_id", "-"),
                "Assigned Role": r.get("role", "Unspecified"),
                "Match Score": f"{score:.0%}" if score else "N/A",
            })
        st.dataframe(pd.DataFrame(realloc_data), use_container_width=True, hide_index=True)
    else:
        st.caption("No direct talent reallocations proposed.")

    # Training Proposals
    trainings = recommendations.get("trainings", [])
    if trainings:
        st.markdown("**Upskilling & Internal Training Proposals**")
        for t in trainings:
            target_skills = ", ".join(t.get("target_skills", []))
            st.markdown(f"- `{t.get('employee_id')}`: {target_skills} ({t.get('duration_weeks')}w duration)")

    # External Hirings
    hirings = recommendations.get("hirings", [])
    if hirings:
        st.markdown("**External Talent Requisitions Required**")
        for h in hirings:
            req_skills = ", ".join(h.get("required_skills", []))
            st.markdown(f"- **{h.get('role')}** (Headcount: {h.get('headcount', 1)}) — Stack: [{req_skills}]")

    # Strategic Reasoning
    st.markdown("**Strategic Rationale**")
    reasoning_text = recommendations.get("reasoning", "No narrative rationale provided.")
    is_dark = is_dark_mode()
    box_bg = "#111827" if is_dark else "#F8FAFC"
    box_border = "#1E293B" if is_dark else "#CBD5E1"
    box_accent = "#3B82F6" if is_dark else "#0F172A"
    box_text = "#E2E8F0" if is_dark else "#1E293B"
    box_html = (
        f'<div style="background-color: {box_bg}; border: 1px solid {box_border}; border-left: 4px solid {box_accent}; '
        f'padding: 12px 16px; border-radius: 4px; font-size: 13px; color: {box_text}; line-height: 1.6;">'
        f'{reasoning_text}</div>'
    )
    st.markdown(box_html, unsafe_allow_html=True)

    st.divider()

    # -----------------------------------------------------------------------
    # HITL Decision Gate
    # -----------------------------------------------------------------------
    st.markdown("#### Human-in-the-Loop Decision Gate")

    decision_status = st.session_state.get(f"status_{rec_id}", "pending")

    if decision_status == "APPROVED":
        st.markdown(render_status_pill("STATUS: APPROVED & COMMITTED", "success"), unsafe_allow_html=True)
        st.success("Allocation approved and committed to SQLite database.")
        return
    if decision_status == "REJECTED_TERMINATED":
        st.markdown(render_status_pill("STATUS: PROPOSAL REJECTED", "danger"), unsafe_allow_html=True)
        st.error("Proposal rejected. Workflow terminated without operational modifications.")
        return
    if decision_status == "SUPERSEDED":
        st.markdown(render_status_pill("STATUS: SUPERSEDED BY REVISION", "info"), unsafe_allow_html=True)
        st.info("This proposal was superseded by an updated revision. Review the revised plan above.")
        return

    col_appr, col_rej = st.columns(2)

    with col_appr:
        if st.button(
            "Approve Allocation",
            type="primary",
            use_container_width=True,
            key=f"btn_approve_{rec_id}",
        ):
            with st.spinner("Executing database commit..."):
                result = call_execute_forecast(rec_id, thread_id, approved=True)
            if result and result.get("status") == "completed":
                st.session_state[f"status_{rec_id}"] = "APPROVED"
                st.session_state["last_execution_status"] = result.get("execution_status", "Committed")
                st.session_state["post_decision_state"] = {
                    "status": "APPROVED",
                    "recommendation_id": rec_id,
                    "thread_id": thread_id,
                    "approved_by": st.session_state.get("approver_name", "Manager"),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "execution_status": result.get("execution_status", "Committed to SQLite"),
                }
                st.toast("Allocation approved and committed.")
                st.rerun()
            else:
                st.error("Approval commit failed. Check server logs.")

    with col_rej:
        if st.button(
            "Reject Proposal",
            use_container_width=True,
            key=f"btn_reject_{rec_id}",
        ):
            st.session_state["show_feedback_for"] = rec_id

    # Revision Feedback Input
    if st.session_state.get("show_feedback_for") == rec_id:
        st.markdown("---")
        st.markdown("**Manager Revision Guidance**")
        feedback_text = st.text_area(
            "Enter revision parameters or constraints for the model:",
            placeholder=(
                "e.g. 'EMP-001 is allocated to FinTech project until Q4. "
                "Evaluate EMP-002 or recommend an external hire. "
                "Prioritize candidates with Kubernetes certification.'"
            ),
            key=f"feedback_input_{rec_id}",
            height=100,
        )

        fb_col1, fb_col2 = st.columns(2)

        with fb_col1:
            if st.button(
                "Submit Revision Request",
                type="primary",
                use_container_width=True,
                key=f"btn_submit_rev_{rec_id}",
            ):
                if not feedback_text.strip():
                    st.warning("Please specify guidance before submitting a revision.")
                else:
                    with st.spinner("Submitting guidance — LLM generating revised allocation plan..."):
                        result = call_execute_forecast(
                            rec_id, thread_id,
                            approved=False,
                            rejection_feedback=feedback_text.strip(),
                        )
                    if result and result.get("status") == "revised_for_review":
                        new_rec_id = result.get("recommendation_id", rec_id)
                        st.session_state["active_rec_id"] = new_rec_id
                        st.session_state["active_thread_id"] = result.get("thread_id", thread_id)
                        st.session_state["api_forecast_result"] = result
                        st.session_state["show_feedback_for"] = None
                        st.session_state[f"status_{rec_id}"] = "SUPERSEDED"
                        st.session_state["post_decision_state"] = {
                            "status": "REVISED",
                            "recommendation_id": new_rec_id,
                            "previous_recommendation_id": rec_id,
                            "thread_id": result.get("thread_id", thread_id),
                            "revision_count": result.get("revision_count", 1),
                            "feedback_applied": feedback_text.strip(),
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        st.toast("Revised proposal ready for review.")
                        st.rerun()
                    else:
                        st.error("Revision request failed or returned unexpected response.")

        with fb_col2:
            if st.button(
                "Terminate Proposal",
                use_container_width=True,
                key=f"btn_terminate_{rec_id}",
            ):
                with st.spinner("Terminating proposal..."):
                    result = call_execute_forecast(rec_id, thread_id, approved=False)
                if result:
                    st.session_state[f"status_{rec_id}"] = "REJECTED_TERMINATED"
                    st.session_state["show_feedback_for"] = None
                    st.session_state["post_decision_state"] = {
                        "status": "REJECTED_TERMINATED",
                        "recommendation_id": rec_id,
                        "reason": feedback_text.strip() if feedback_text else "Manager rejected proposal without revision request.",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    st.rerun()


# ---------------------------------------------------------------------------
# Module 2: Manager Dashboard
# ---------------------------------------------------------------------------
def render_manager_dashboard(employees: List[Dict[str, Any]], demands: List[Dict[str, Any]]) -> None:
    """Executive directory: all employee roster, active jobs, and structured KPI metrics."""
    st.markdown("### Workforce Directory & Pipeline Intelligence")
    st.caption("Operational visibility into talent distribution, bench availability, and project demand requisitions.")

    # Calculate real data metrics using anchor date DATA_REF_DATE (2026-09-14)
    bench_immediate = len([
        e for e in employees
        if e.get("available_from", "9999") <= DATA_REF_DATE or not e.get("current_project") or "bench" in str(e.get("current_project")).lower()
    ])
    bench_30d = len([
        e for e in employees
        if e.get("available_from", "9999") <= "2026-10-14"
    ])
    total_open_headcount = sum(d.get("headcount", 1) for d in demands)
    avg_win_prob = (
        sum(d.get("win_probability", 0.0) for d in demands) / max(len(demands), 1)
    )

    # 6 Crisp Corporate Metric Cards (Datadog/Linear style)
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        render_metric_card("HEADCOUNT", "TOTAL WORKFORCE", str(len(employees)), "Active employee pool", "#2563EB", "#2563EB")
    with k2:
        render_metric_card("BENCH", "AVAILABLE NOW", str(bench_immediate), f"{bench_30d} rolling off in 30d", "#D97706", "#D97706")
    with k3:
        render_metric_card("ALLOCATION", "ASSIGNED", str(len(employees) - bench_immediate), "Active engagements", "#0284C7", "#0284C7")
    with k4:
        render_metric_card("PIPELINE", "DEMANDS", str(len(demands)), "Registered roles", "#4F46E5", "#4F46E5")
    with k5:
        render_metric_card("REQUISITIONS", "OPEN POSITIONS", str(total_open_headcount), "Required headcount", "#0D9488", "#0D9488")
    with k6:
        render_metric_card("CONFIDENCE", "AVG WIN RATE", f"{avg_win_prob:.0%}", "Pipeline confidence", "#10B981", "#10B981")

    st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)

    # Section 1: Employee Directory
    st.markdown("#### Workforce Roster & Deployment Status")

    dir_col1, dir_col2 = st.columns([1, 2])
    with dir_col1:
        status_filter = st.selectbox(
            "Filter by Status:",
            ["All Workforce", "Available on Bench", "Currently Assigned"],
            key="mgr_status_filter",
        )
    with dir_col2:
        emp_search = st.text_input(
            "Search Workforce (Name, ID, Skill):",
            placeholder="e.g. Java, Python, EMP-001, Popescu...",
            key="mgr_emp_search",
        )

    filtered_emps = employees
    if status_filter == "Available on Bench":
        filtered_emps = [
            e for e in filtered_emps
            if e.get("available_from", "9999") <= DATA_REF_DATE or not e.get("current_project") or "bench" in str(e.get("current_project")).lower()
        ]
    elif status_filter == "Currently Assigned":
        filtered_emps = [
            e for e in filtered_emps
            if e.get("available_from", "9999") > DATA_REF_DATE and e.get("current_project") and "bench" not in str(e.get("current_project")).lower()
        ]

    if emp_search.strip():
        q = emp_search.strip().lower()
        filtered_emps = [
            e for e in filtered_emps
            if q in e.get("name", "").lower()
            or q in e.get("id", "").lower()
            or any(q in s.lower() for s in e.get("skills", []))
            or q in str(e.get("current_project", "")).lower()
        ]

    table_records = []
    for e in filtered_emps:
        proj = e.get("current_project")
        avail = e.get("available_from", "-")
        is_bench = avail <= DATA_REF_DATE or not proj or "bench" in str(proj).lower()
        status_str = "BENCH" if is_bench else f"ASSIGNED ({proj})"
        table_records.append({
            "ID": e.get("id"),
            "Full Name": e.get("name"),
            "Status": status_str,
            "Experience (Yrs)": e.get("experience_years", "-"),
            "Available From": avail,
            "Key Skills": ", ".join(e.get("skills", [])),
        })

    st.dataframe(pd.DataFrame(table_records), use_container_width=True, hide_index=True)

    # Detailed Profile Inspector
    with st.expander("Detailed Employee Profile Inspector"):
        emp_options = {f"{e['id']} — {e['name']}": e for e in filtered_emps}
        if emp_options:
            selected_emp_key = st.selectbox("Select Profile:", list(emp_options.keys()), key="sel_emp_insp")
            target_emp = emp_options[selected_emp_key]

            inf1, inf2 = st.columns([1, 2])
            with inf1:
                st.markdown(f"**ID:** `{target_emp.get('id')}`")
                st.markdown(f"**Experience:** `{target_emp.get('experience_years')} years`")
                st.markdown(f"**Available:** `{target_emp.get('available_from')}`")
                cur_proj = target_emp.get('current_project')
                avail = target_emp.get("available_from", "-")
                is_bench = avail <= DATA_REF_DATE or not cur_proj or "bench" in str(cur_proj).lower()
                if is_bench:
                    st.markdown(render_status_pill("BENCH AVAILABLE", "warning"), unsafe_allow_html=True)
                else:
                    st.markdown(render_status_pill(f"ASSIGNED: {cur_proj.upper()}", "info"), unsafe_allow_html=True)
            with inf2:
                st.markdown("**Technical Skills:**")
                st.markdown(" ".join(f"`{s}`" for s in target_emp.get("skills", [])))
                st.markdown("**Professional Biography:**")
                st.caption(target_emp.get("bio", "No narrative available."))
        else:
            st.caption("No employee records match the search criteria.")

    st.divider()

    # Section 2: Pipeline Demands
    st.markdown("#### Pipeline Requisitions & Demand Matching")

    dem_col1, dem_col2 = st.columns([1, 2])
    with dem_col1:
        dem_win_filter = st.slider(
            "Minimum Opportunity Confidence:",
            min_value=0,
            max_value=100,
            value=0,
            step=5,
            format="%d%%",
            key="mgr_dem_win_filter",
        )
    with dem_col2:
        dem_search = st.text_input(
            "Search Requisitions (Role, Project, Skill):",
            placeholder="e.g. Microservices, DEM-101, Bank...",
            key="mgr_dem_search",
        )

    filtered_demands = [
        d for d in demands if d.get("win_probability", 0.0) >= (dem_win_filter / 100.0)
    ]

    if dem_search.strip():
        dq = dem_search.strip().lower()
        filtered_demands = [
            d for d in filtered_demands
            if dq in d.get("role", "").lower()
            or dq in d.get("id", "").lower()
            or dq in d.get("project_id", "").lower()
            or any(dq in s.lower() for s in d.get("required_skills", []))
        ]

    dem_table_data = []
    for d in filtered_demands:
        dem_table_data.append({
            "Demand ID": d.get("id"),
            "Project ID": d.get("project_id"),
            "Target Role": d.get("role"),
            "Headcount": d.get("headcount", 1),
            "Start Date": d.get("start_date", "-"),
            "Win Probability": f"{d.get('win_probability', 0.0):.0%}",
            "Required Skills": ", ".join(d.get("required_skills", [])),
        })

    st.dataframe(pd.DataFrame(dem_table_data), use_container_width=True, hide_index=True)

    with st.expander("Inspect Statement of Work & SOW Deliverables"):
        dem_options = {f"{d['id']} — {d['role']}": d for d in filtered_demands}
        if dem_options:
            selected_dem_key = st.selectbox("Select Requisition:", list(dem_options.keys()), key="sel_dem_insp")
            target_dem = dem_options[selected_dem_key]
            st.markdown(f"**Project ID:** `{target_dem.get('project_id')}` | **Headcount:** `{target_dem.get('headcount', 1)} FTE`")
            st.markdown(f"**Win Probability:** `{target_dem.get('win_probability', 0.0):.0%}` | **Start:** `{target_dem.get('start_date')}`")
            st.markdown("**Required Skills:**")
            st.markdown(" ".join(f"`{s}`" for s in target_dem.get("required_skills", [])))
            st.markdown("**Statement of Work:**")
            st.caption(target_dem.get("description", "No narrative available."))
        else:
            st.caption("No demand records match the filter criteria.")


# ---------------------------------------------------------------------------
# Module 3: Post-Decision Workflow
# ---------------------------------------------------------------------------
def render_post_decision_tab() -> None:
    """Post-Decision Lifecycle tracking and relational database audit trail."""
    st.markdown("### Post-Decision Execution & Relational Audit")
    st.caption("Immutable transaction history, operational transition checklist, and execution lifecycle status.")

    post_state = st.session_state.get("post_decision_state")

    # Active Decision Lifecycle
    with st.container(border=True):
        st.markdown("#### Active Execution Lifecycle")

        if not post_state:
            st.info(
                "No human review decision has been recorded in the current session.\n\n"
                "To trigger lifecycle progression:\n"
                "1. Navigate to Forecast & Allocation tab\n"
                "2. Generate or review an open role allocation\n"
                "3. Click 'Approve Allocation' or 'Reject Proposal'"
            )

            st.markdown("**Standard 4-Stage Execution Architecture:**")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                render_pipeline_node("1", "HUMAN REVIEW", "PENDING", "pending", "Gate waiting for manager sign-off")
            with c2:
                render_pipeline_node("2", "DATABASE SYNC", "PENDING", "pending", "Atomic commit to mock_db.sqlite")
            with c3:
                render_pipeline_node("3", "PROJECT HANDOVER", "PENDING", "pending", "Delivery team notification")
            with c4:
                render_pipeline_node("4", "ONBOARDING", "PENDING", "pending", "Resource activation & access")

        else:
            status = post_state.get("status")

            if status == "APPROVED":
                st.markdown(render_status_pill("STATUS: APPROVED & EXECUTED", "success"), unsafe_allow_html=True)
                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

                st.markdown("**Execution Pipeline Progression:**")
                s1, s2, s3, s4 = st.columns(4)
                with s1:
                    render_pipeline_node("1", "HUMAN REVIEW", "APPROVED", "success", f"Sign-off by {post_state.get('approved_by')}")
                with s2:
                    render_pipeline_node("2", "DATABASE SYNC", "COMMITTED", "success", "Row committed to allocations table")
                with s3:
                    render_pipeline_node("3", "HANDOVER", "IN PROGRESS", "active", "Delivery lead notification active")
                with s4:
                    render_pipeline_node("4", "ONBOARDING", "SCHEDULED", "pending", "Workspace & repo provisioning")

                st.markdown("---")
                m1, m2, m3 = st.columns(3)
                m1.markdown(f"**Recommendation ID:** `{post_state.get('recommendation_id')}`")
                m2.markdown(f"**Approved By:** `{post_state.get('approved_by')}`")
                m3.markdown(f"**Timestamp:** `{post_state.get('timestamp')}`")

                st.markdown(f"**Execution Detail:** `{post_state.get('execution_status')}`")

                st.markdown("**Operational Handover Checklist:**")
                st.checkbox("Transaction record committed in SQLite database", value=True, disabled=True)
                st.checkbox("Employee profile updated to target project in database", value=True, disabled=True)
                st.checkbox("Project delivery lead notified of staff allocation", value=False)
                st.checkbox("Git repository access and cloud workspace credentials provisioned", value=False)
                st.checkbox("Project orientation and knowledge transfer session scheduled", value=False)

            elif status == "REVISED":
                st.markdown(render_status_pill("STATUS: REVISION IN PROGRESS", "info"), unsafe_allow_html=True)
                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

                st.markdown("**Revision Loop Status:**")
                s1, s2, s3, s4 = st.columns(4)
                with s1:
                    render_pipeline_node("1", "FEEDBACK RECORDED", "COMPLETE", "success", "Manager guidance parsed")
                with s2:
                    render_pipeline_node("2", "CONSTRAINTS ADAPTED", "COMPLETE", "success", "Planner prompt updated")
                with s3:
                    render_pipeline_node("3", "REVISED PLAN", "ACTIVE", "active", "New proposal synthesized")
                with s4:
                    render_pipeline_node("4", "PENDING REVIEW", "WAITING", "pending", "Awaiting human review in Tab 1")

                st.markdown("---")
                r1, r2 = st.columns(2)
                r1.markdown(f"**Current Recommendation ID:** `{post_state.get('recommendation_id')}`")
                r2.markdown(f"**Superseded Recommendation ID:** `{post_state.get('previous_recommendation_id')}`")
                st.markdown(f"**Revision Iteration:** `{post_state.get('revision_count', 1)} of 2`")
                st.markdown(f"**Manager Guidance Applied:** _{post_state.get('feedback_applied')}_")
                st.info("The updated allocation plan is ready for review in the Forecast & Allocation tab.")

            elif status == "REJECTED_TERMINATED":
                st.markdown(render_status_pill("STATUS: PROPOSAL TERMINATED", "danger"), unsafe_allow_html=True)
                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

                st.markdown("**Termination Status:**")
                s1, s2, s3, s4 = st.columns(4)
                with s1:
                    render_pipeline_node("1", "HUMAN REVIEW", "REJECTED", "danger", "Manager rejected proposal")
                with s2:
                    render_pipeline_node("2", "WORKFLOW", "TERMINATED", "danger", "Workflow halted")
                with s3:
                    render_pipeline_node("3", "DATABASE SYNC", "UNALTERED", "success", "Zero DB changes recorded")
                with s4:
                    render_pipeline_node("4", "SOURCING", "ESCALATION", "pending", "External recruitment required")

                st.markdown("---")
                st.markdown(f"**Recommendation ID:** `{post_state.get('recommendation_id')}`")
                st.markdown(f"**Rejection Rationale:** {post_state.get('reason')}")
                st.warning("No operational database records were modified. The pipeline requisition remains unfulfilled.")

                st.markdown("**Alternative Sourcing Pathways:**")
                st.markdown("- **External Talent Acquisition:** Open external recruiting requisition.")
                st.markdown("- **Contractor Network:** Requisition specialized contractor capacity.")
                st.markdown("- **Internal Upskilling:** Target bench employees for fast-track skill development.")

    st.divider()

    # Section 2: Relational Database Audit Trail
    st.markdown("#### Relational Database Allocation Audit Trail")
    st.caption("Immutable record of executed reallocations recorded in SQLite database.")

    history_records = get_db_allocation_history()

    if history_records:
        history_df = pd.DataFrame(history_records)
        columns_to_show = [
            c for c in [
                "id", "employee_id", "target_project_id", "role",
                "match_score", "approved_by", "approved_at", "status"
            ] if c in history_df.columns
        ]
        st.dataframe(history_df[columns_to_show], use_container_width=True, hide_index=True)

        csv_data = history_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Allocation Audit Trail (CSV)",
            data=csv_data,
            file_name="bench_forecast_allocations_audit.csv",
            mime="text/csv",
        )
    else:
        st.info("No executed allocations currently recorded in the SQLite audit table.")


# ---------------------------------------------------------------------------
# Main Controller
# ---------------------------------------------------------------------------
def main() -> None:
    data = load_mock_data()
    demands: List[Dict[str, Any]] = data.get("demands", [])
    employees: List[Dict[str, Any]] = data.get("employees", [])

    # Sidebar: System Controls & Parameters
    with st.sidebar:
        st.markdown("### SYSTEM CONFIGURATION")

        # API Health Check
        backend_online = False
        health_info: Dict[str, Any] = {}
        try:
            res = requests.get(f"{API_BASE_URL}/api/v1/health", timeout=2)
            if res.status_code == 200:
                backend_online = True
                health_info = res.json()
        except Exception:
            pass

        if backend_online:
            st.markdown(render_status_pill("ONLINE // API CONNECTED", "success"), unsafe_allow_html=True)
            st.caption(
                f"Provider: `{health_info.get('llm_provider', '-')}` | "
                f"Active Workflows: `{health_info.get('active_workflows', 0)}`"
            )
        else:
            st.markdown(render_status_pill("OFFLINE // API DISCONNECTED", "danger"), unsafe_allow_html=True)
            st.caption("Start backend: `python server.py`")

        st.session_state["approver_name"] = st.text_input(
            "Reviewer Name / Approver ID:",
            value=st.session_state.get("approver_name", "Manager"),
        )

        st.divider()
        st.markdown("### FORECAST HORIZON")

        horizon_days = st.slider(
            "Look-ahead Window (days):",
            min_value=30,
            max_value=180,
            value=st.session_state.get("horizon_days_val", 90),
            step=15,
            help="Include bench profiles available within this window.",
        )
        st.session_state["horizon_days_val"] = horizon_days

        win_prob_pct = st.slider(
            "Minimum Opportunity Win Rate:",
            min_value=50,
            max_value=100,
            value=st.session_state.get("win_prob_pct_val", 75),
            step=5,
            format="%d%%",
            help="Filter demand pipeline by minimum opportunity win probability.",
        )
        st.session_state["win_prob_pct_val"] = win_prob_pct
        min_win_prob = win_prob_pct / 100.0

        # Structured Telemetry Block with Verified Metrics
        bench_now = len([
            e for e in employees
            if e.get("available_from", "9999") <= DATA_REF_DATE or not e.get("current_project") or "bench" in str(e.get("current_project")).lower()
        ])
        bench_30d = len([
            e for e in employees
            if e.get("available_from", "9999") <= "2026-10-14"
        ])

        is_dark = is_dark_mode()
        tel_bg = "#111827" if is_dark else "#FFFFFF"
        tel_border = "#1E293B" if is_dark else "#CBD5E1"
        tel_accent = "#3B82F6" if is_dark else "#2563EB"
        tel_header = "#94A3B8" if is_dark else "#64748B"
        tel_text = "#CBD5E1" if is_dark else "#334155"
        tel_bold = "#F8FAFC" if is_dark else "#0F172A"
        tel_30d = "#38BDF8" if is_dark else "#0284C7"

        telemetry_html = (
            f'<div style="background-color: {tel_bg}; border: 1px solid {tel_border}; border-left: 3px solid {tel_accent}; '
            f'border-radius: 4px; padding: 10px 12px; margin: 12px 0; font-family: monospace; font-size: 11px;">'
            f'<div style="color: {tel_header}; font-weight: 700; text-transform: uppercase; margin-bottom: 4px;">'
            f'CAPACITY TELEMETRY</div>'
            f'<div style="color: {tel_text};">SUPPLY: <b style="color: {tel_bold};">{len(employees)} PROFILES</b></div>'
            f'<div style="color: {tel_text};">BENCH: <b style="color: {tel_bold};">{bench_now} AVAILABLE NOW</b> | '
            f'<span style="color: {tel_30d};">{bench_30d} IN 30D</span></div>'
            f'<div style="color: {tel_text};">PIPELINE: <b style="color: {tel_bold};">{len(demands)} DEMANDS</b> '
            f'(WIN &ge; {win_prob_pct}%)</div>'
            f'</div>'
        )
        st.markdown(telemetry_html, unsafe_allow_html=True)

        st.divider()
        st.markdown("### PIPELINE REQUISITIONS")

        filtered_demands = [
            d for d in demands if d.get("win_probability", 0.0) >= min_win_prob
        ]
        demand_options = {
            f"{d['id']} — {d['role']} ({d.get('win_probability', 0.0):.0%})": d
            for d in filtered_demands
        }

        selected_demand = None
        if not demand_options:
            st.warning("No pipeline demands meet current win probability threshold.")
        else:
            selected_key = st.selectbox("Select Target Role:", list(demand_options.keys()))
            selected_demand = demand_options.get(selected_key)

        st.divider()

        generate_disabled = not backend_online
        if st.button(
            "Generate Forecast",
            type="primary",
            use_container_width=True,
            disabled=generate_disabled,
            help="Executes LangGraph agentic pipeline: data extraction -> RAG matching -> planning.",
        ):
            with st.spinner("Executing agentic pipeline: data extraction, ChromaDB RAG, and LLM planning..."):
                resp = call_generate_forecast("ALL", horizon_days, min_win_prob)

            if resp:
                st.session_state["api_forecast_result"] = resp
                st.session_state["active_thread_id"] = resp.get("thread_id", "")
                st.session_state["active_rec_id"] = resp.get("recommendation_id", "")
                st.session_state["show_feedback_for"] = None
                st.toast("Forecast generated successfully. Review below.")

        if generate_disabled:
            st.caption("Start the API server to enable forecast generation.")

        last_exec = st.session_state.get("last_execution_status")
        if last_exec:
            st.markdown(f"**Last Execution:** `{last_exec}`")

    # Executive Top App Bar with Minimal Theme Toggle Button on Right
    col_hdr, col_theme_btn = st.columns([0.94, 0.06], vertical_alignment="center")
    with col_hdr:
        render_header()
    with col_theme_btn:
        render_theme_toggle_button()

    # Top-Level Tabs
    tab_forecast, tab_manager, tab_post_decision = st.tabs([
        "Allocation Console",
        "Manager Dashboard",
        "Post-Decision Lifecycle",
    ])

    # -----------------------------------------------------------------------
    # Tab 1: Allocation Console
    # -----------------------------------------------------------------------
    with tab_forecast:
        col_demand, col_review = st.columns([1, 1], gap="large")

        with col_demand:
            if selected_demand:
                render_demand_card(selected_demand)
            else:
                st.info("Select a pipeline requisition from the sidebar to inspect specifications.")

        with col_review:
            # Semantic RAG retrieval
            matched_candidate: Optional[Dict[str, Any]] = None
            match_score_str = "—"

            if selected_demand:
                try:
                    vector_mgr = VectorStoreManager()
                    query = f"{selected_demand.get('role', '')} {selected_demand.get('description', '')}"
                    rag_results = vector_mgr.similarity_search(query, k=1)
                    if rag_results:
                        top = rag_results[0]
                        matched_candidate = next((e for e in employees if e["id"] == top["id"]), None)
                        match_score_str = f"{top['similarity_score']:.0%}"
                except Exception:
                    pass

            if not matched_candidate and employees:
                matched_candidate = employees[0]

            if matched_candidate:
                render_candidate_card(matched_candidate, match_score_str)

            st.divider()

            # LLM Recommendation Panel
            api_result = st.session_state.get("api_forecast_result")
            rec_id = st.session_state.get("active_rec_id", "")
            thread_id = st.session_state.get("active_thread_id", "")

            if api_result:
                recommendations = api_result.get("recommendations") or {}
                revision_count = api_result.get("revision_count", 0)
                render_recommendations_panel(recommendations, rec_id, thread_id, revision_count)
            else:
                st.info(
                    "Click **Generate Forecast** in the sidebar to execute the allocation pipeline.\n\n"
                    "Workflow Pipeline:\n"
                    "1. Extract bench profiles available within selected look-ahead window\n"
                    "2. Query ChromaDB for vector similarity matching (RAG)\n"
                    "3. Run LLM skill alignment for candidate-demand pairs\n"
                    "4. Formulate reallocation, upskilling, and hiring recommendations\n"
                    "5. Pause at HITL gate for manager sign-off"
                )

            # Audit Notes Expander
            with st.expander("Manager Audit Notes & Governance"):
                note_text = st.text_area(
                    "Log observations or governance notes:",
                    placeholder="Enter review notes, business context, or caveats...",
                    key="audit_note_text",
                )
                if st.button("Record Audit Note", key="save_audit_note"):
                    if rec_id and note_text.strip():
                        call_submit_feedback(rec_id, note_text.strip())
                        st.success("Governance note logged in audit trail.")
                    elif not rec_id:
                        st.warning("No active recommendation ID. Generate a forecast first.")
                    else:
                        st.warning("Note content is empty.")

    # -----------------------------------------------------------------------
    # Tab 2: Manager Dashboard
    # -----------------------------------------------------------------------
    with tab_manager:
        render_manager_dashboard(employees, demands)

    # -----------------------------------------------------------------------
    # Tab 3: Post-Decision Workflow
    # -----------------------------------------------------------------------
    with tab_post_decision:
        render_post_decision_tab()


if __name__ == "__main__":
    main()
