import asyncio
import base64
import html as _html
import os
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from google import genai
from openai import AsyncOpenAI

from src import advisor_log
from src import config as exclusions
from src.advisor import ask_committee
from src.performance import portfolio_vs_benchmarks
from src.runner import load_all_runs, load_latest_run, run_committee

load_dotenv()
exclusions.load()

st.set_page_config(page_title="Investment Committee", layout="wide")

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"], .stMarkdown p, .stText {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
}

/* ── Holding cards ── */
.holding-card {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 6px;
    transition: border-color 0.15s, background 0.15s;
}
.holding-card:hover { border-color: #3b82f6; background: #243044; }
.holding-card.core     { border-left: 3px solid #3b82f6; }
.holding-card.moonshot { border-left: 3px solid #8b5cf6; }
.holding-left  { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; overflow: hidden; }
.holding-right { display: flex; align-items: center; gap: 8px; flex-wrap: nowrap; justify-content: flex-end; flex-shrink: 0; }
.ticker-sym { font-family: 'JetBrains Mono', monospace; font-size: 1.05rem; font-weight: 600; color: #f1f5f9; letter-spacing: 0.04em; }
.co-name    { font-size: 0.78rem; color: #64748b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ── st.html wrapper: propagate width into card flex children ── */
[data-testid="stHtml"] { width: 100%; min-width: 0; }
.price-val  { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; color: #94a3b8; }
.upside-pos { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; font-weight: 600; color: #10b981; }
.upside-neg { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; font-weight: 600; color: #ef4444; }
.upside-block { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.upside-sub { font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; }
.upside-sub.up { color: rgba(16,185,129,0.65); }
.upside-sub.dn { color: rgba(239,68,68,0.65); }
.weight-val {
    font-family: 'JetBrains Mono', monospace; font-size: 0.95rem; font-weight: 700;
    color: #3b82f6; background: rgba(59,130,246,0.12);
    padding: 3px 10px; border-radius: 5px; min-width: 48px; text-align: right;
}

/* ── Member nomination logos ── */
.mem-badges {
    display: inline-flex; gap: 4px; align-items: center;
    border: 1px solid #334155; border-radius: 6px;
    padding: 3px 6px; background: rgba(255,255,255,0.03);
}
.mem-logo {
    width: 20px; height: 20px; border-radius: 50%;
    border: 1.5px solid; object-fit: cover; display: inline-block; vertical-align: middle;
}
.mem-claude { border-color: rgba(245,158,11,0.7); }
.mem-gpt    { border-color: rgba(16,185,129,0.7); filter: invert(1); }
.mem-gemini { border-color: rgba(59,130,246,0.7); }

/* ── Conviction badge (member picks) ── */
.conv-badge { font-size: 0.65rem; font-weight: 700; letter-spacing: 0.06em; padding: 2px 7px; border-radius: 4px; text-transform: uppercase; }
.conv-core  { color: #3b82f6; background: rgba(59,130,246,0.10); border: 1px solid rgba(59,130,246,0.3); }
.conv-moon  { color: #8b5cf6; background: rgba(139,92,246,0.10); border: 1px solid rgba(139,92,246,0.3); }

/* ── Recommendation badges (advisor panel and research log only) ── */
.rec-badge {
    display: inline-block; font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem; font-weight: 600; letter-spacing: 0.06em;
    padding: 4px 12px; border-radius: 20px;
}
.rec-buy       { color: #10b981; background: rgba(16,185,129,0.12);  border: 1px solid rgba(16,185,129,0.4);  }
.rec-watch     { color: #f59e0b; background: rgba(245,158,11,0.12);  border: 1px solid rgba(245,158,11,0.4);  }
.rec-pass      { color: #ef4444; background: rgba(239,68,68,0.12);   border: 1px solid rgba(239,68,68,0.4);   }
.rec-portfolio { color: #3b82f6; background: rgba(59,130,246,0.12);  border: 1px solid rgba(59,130,246,0.4);  }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid #334155;
    padding: 0 2px;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-weight: 500;
    font-size: 0.82rem;
    color: #64748b;
    background: transparent;
    border-radius: 6px 6px 0 0;
    padding: 9px 20px;
    border: 1px solid transparent;
    border-bottom: none;
    margin: 0 1px -1px;
    transition: color 0.15s, background 0.15s, border-color 0.15s;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #94a3b8 !important;
    background: #1a2333 !important;
}
.stTabs [aria-selected="true"] {
    color: #f1f5f9 !important;
    background: #1e293b !important;
    border-color: #334155 !important;
    border-bottom-color: #1e293b !important;
}

/* ── Metric cards ── */
[data-testid="metric-container"] {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px 20px;
}

/* ── Expanders ── */
[data-testid="stExpander"] {
    background: #1a2333; border: 1px solid #2d3d52 !important; border-radius: 8px !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] { background: #0d1424 !important; }
[data-testid="stSidebar"] [data-testid="stTextInput"] input {
    background: rgba(255,255,255,0.07) !important;
    border: 1px solid #334155 !important;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input:focus {
    border-color: #3b82f6 !important;
    box-shadow: 0 0 0 2px rgba(59,130,246,0.15) !important;
}

/* ── Editorial member picks ── */
.pick-col-header { display: flex; align-items: center; gap: 10px; padding-bottom: 12px; }
.pick-col-logo   { width: 28px; height: 28px; }
.pick-col-title  { font-family: 'Plus Jakarta Sans', sans-serif; font-size: 1.1rem; font-weight: 700; letter-spacing: 0.01em; }
.pick-col-rule   { height: 2px; border-radius: 1px; margin: 12px 0 18px; opacity: 0.5; }
.pick-entry      { border-bottom: 1px solid #1a2740; padding: 14px 0; }
.pick-entry:last-of-type { border-bottom: none; }
.pick-header     { list-style: none; cursor: pointer; display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.pick-header::-webkit-details-marker { display: none; }
.pick-header::marker { display: none; }
.pick-meta       { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.pick-ticker     { font-family: 'JetBrains Mono', monospace; font-size: 1.05rem; font-weight: 700; letter-spacing: 0.04em; }
.pick-company    { font-size: 0.73rem; color: #64748b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pick-data       { display: flex; align-items: center; gap: 6px; margin-top: 5px; flex-wrap: wrap; }
.pick-toggle     { font-size: 0.75rem; color: #475569; flex-shrink: 0; padding-top: 3px; transition: color 0.15s; }
.pick-toggle::before { content: '+'; font-family: 'JetBrains Mono', monospace; font-weight: 600; }
details.pick-entry[open] .pick-toggle { color: #94a3b8; }
details.pick-entry[open] .pick-toggle::before { content: '−'; }
.pick-body       { padding: 10px 0 2px; }
.pick-rationale-text { color: #94a3b8; font-size: 0.82rem; line-height: 1.65; margin: 0 0 6px; }
.pick-edge       { color: #64748b; font-size: 0.78rem; margin-top: 6px; }
.pick-edge strong { color: #94a3b8; }
.pick-moon-label { font-size: 0.65rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: #8b5cf6; display: block; margin: 20px 0 2px; padding-top: 16px; border-top: 1px solid #2d3d52; }

/* ── Advisor recommendation row ── */
.rec-row   { display: flex; align-items: center; gap: 12px; margin: 8px 0; }
.rec-label { font-weight: 600; color: #94a3b8; font-size: 0.9rem; }

/* ── Moonshot section header ── */
.moonshot-header {
    color: #a78bfa; font-weight: 700; font-size: 1rem;
    border-left: 3px solid #8b5cf6; padding-left: 10px;
    margin: 20px 0 12px; display: block;
}

/* ── Expandable holding cards ── */
.holding-details { margin-bottom: 6px; }
.holding-details > summary { list-style: none; cursor: pointer; }
.holding-details > summary::-webkit-details-marker { display: none; }
.holding-details > summary::marker { display: none; }
.holding-details[open] > summary.holding-card {
    border-radius: 10px 10px 0 0;
    border-bottom-color: #2d3d52;
    margin-bottom: 0;
}
.rationale-panel {
    background: #1a2333; border: 1px solid #2d3d52; border-top: none;
    border-radius: 0 0 10px 10px; padding: 14px 18px;
}
.rationale-text {
    color: #cbd5e1; font-size: 0.85rem; line-height: 1.65; margin: 0 0 8px;
}
.rationale-meta {
    color: #64748b; font-size: 0.78rem; margin-top: 10px;
    padding-top: 10px; border-top: 1px solid #2d3d52;
}
.rationale-edge {
    color: #94a3b8; font-size: 0.82rem; margin-top: 8px;
}
.rationale-edge strong { color: #cbd5e1; }
.rationale-nominated { display: flex; align-items: center; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid #2d3d52; }
.nominated-label { font-size: 0.75rem; color: #64748b; }

/* ── Portfolio quick nav ── */
.portfolio-nav { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; font-size: 0.85rem; }
.nav-count     { color: #64748b; }
.nav-sep       { color: #334155; }
.nav-link      { color: #a78bfa; text-decoration: none; font-weight: 500; }
.nav-link:hover { color: #c4b5fd; text-decoration: underline; }
</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)

_MEMBER_LOGO_CLASS = {"claude": "mem-claude", "gpt": "mem-gpt", "gemini": "mem-gemini"}


def _logo_data_uri(member: str) -> str:
    path = Path(__file__).parent / "static" / f"{member}.png"
    if not path.exists():
        return ""
    return f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}"


_MEMBER_LOGO_SRC = {m: _logo_data_uri(m) for m in ("claude", "gpt", "gemini")}


def _member_badges_html(nominated_by: list[str]) -> str:
    imgs = [
        f'<img src="{_MEMBER_LOGO_SRC[m.lower()]}"'
        f' class="mem-logo {_MEMBER_LOGO_CLASS.get(m.lower(), "")}"'
        f' title="{m}" />'
        for m in nominated_by
        if _MEMBER_LOGO_SRC.get(m.lower())
    ]
    return f'<span class="mem-badges">{"".join(imgs)}</span>'


def _text_to_html(text: str) -> str:
    paras = [p.strip() for p in _html.escape(text).split("\n\n") if p.strip()]
    return "".join(
        f'<p class="rationale-text">{p.replace(chr(10), "<br>")}</p>' for p in paras
    )


def _filter_series(series_dict: dict, opt: str) -> pd.Series:
    s = pd.Series(series_dict)
    s.index = pd.to_datetime(s.index)
    if s.index.tz is not None:
        s.index = s.index.tz_convert(None)
    now = pd.Timestamp.now()
    cutoff = {
        "1W": now - pd.Timedelta(weeks=1),
        "1M": now - pd.Timedelta(days=30),
        "3M": now - pd.Timedelta(days=90),
        "YTD": pd.Timestamp(now.year, 1, 1),
        "1Y": None,
    }[opt]
    if cutoff is not None:
        s = s[s.index >= cutoff]
    if not s.empty:
        s = (s - s.iloc[0]) * 100
    return s


def get_clients() -> tuple[anthropic.AsyncAnthropic, AsyncOpenAI, genai.Client]:
    return (
        anthropic.AsyncAnthropic(),
        AsyncOpenAI(),
        genai.Client(api_key=os.environ["GOOGLE_API_KEY"]),
    )


@st.cache_data(ttl=3600)
def fetch_performance(tickers: list[str], weights: list[float]) -> dict:
    return portfolio_vs_benchmarks(tickers, weights)


def days_since(dt: datetime) -> int:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days


def render_holding(h) -> None:
    card_class = "moonshot" if h.conviction == "moonshot" else "core"
    price_html = (
        f'<span class="price-val">${h.current_price:,.2f}</span>'
        if h.current_price is not None
        else ""
    )
    upside_html = ""
    if h.mean_upside_pct is not None:
        mean_cls = "upside-pos" if h.mean_upside_pct >= 0 else "upside-neg"
        mean_part = f'<span class="{mean_cls}">{h.mean_upside_pct:+.1f}%</span>'
        med_part = ""
        if h.median_upside_pct is not None:
            sub_cls = "up" if h.median_upside_pct >= 0 else "dn"
            med_part = f'<span class="upside-sub {sub_cls}">{h.median_upside_pct:+.1f}% med</span>'
        upside_html = f'<div class="upside-block">{mean_part}{med_part}</div>'
    is_moonshot = h.conviction == "moonshot"
    badges = _member_badges_html(h.nominated_by)
    header_badges = "" if is_moonshot else badges
    panel_nominated = (
        f'<div class="rationale-nominated">'
        f'<span class="nominated-label">Nominated by</span>{badges}'
        f"</div>"
        if is_moonshot
        else ""
    )

    st.html(
        f'<details class="holding-details">'
        f'<summary class="holding-card {card_class}">'
        f'<div class="holding-left">'
        f'<span class="ticker-sym">{h.ticker}</span>'
        f'<span class="co-name">{_html.escape(h.company_name)}</span>'
        f"</div>"
        f'<div class="holding-right">'
        f"{price_html}{upside_html}"
        f'<span class="weight-val">{h.weight}%</span>'
        f"{header_badges}"
        f"</div>"
        f"</summary>"
        f'<div class="rationale-panel">'
        f"{_text_to_html(h.rationale)}"
        f"{panel_nominated}"
        f"</div>"
        f"</details>"
    )


_MEMBER_COLOR = {"claude": "#f59e0b", "gpt": "#10b981", "gemini": "#3b82f6"}


def _pick_entry_html(p, color: str) -> str:
    conv_cls = "conv-moon" if p.conviction == "moonshot" else "conv-core"
    conv_label = "Moon" if p.conviction == "moonshot" else "Core"
    price_html = (
        f'<span class="price-val">${p.current_price:,.2f}</span>'
        if p.current_price
        else ""
    )
    upside_html = ""
    if p.mean_upside_pct is not None:
        mean_cls = "upside-pos" if p.mean_upside_pct >= 0 else "upside-neg"
        mean_part = f'<span class="{mean_cls}">{p.mean_upside_pct:+.1f}%</span>'
        med_part = ""
        if p.median_upside_pct is not None:
            sub_cls = "up" if p.median_upside_pct >= 0 else "dn"
            med_part = f'<span class="upside-sub {sub_cls}">{p.median_upside_pct:+.1f}% med</span>'
        upside_html = f'<div class="upside-block">{mean_part}{med_part}</div>'
    edge_html = (
        f'<p class="pick-edge"><strong>Edge:</strong> {_html.escape(p.variant_perception)}</p>'
        if p.variant_perception
        else ""
    )
    paras = [q.strip() for q in _html.escape(p.rationale).split("\n\n") if q.strip()]
    rationale_html = "".join(
        f'<p class="pick-rationale-text">{q.replace(chr(10), "<br>")}</p>'
        for q in paras
    )
    return (
        f'<details class="pick-entry">'
        f'<summary class="pick-header">'
        f'<div class="pick-meta">'
        f'<span class="pick-ticker" style="color:{color}">{p.ticker}</span>'
        f'<span class="pick-company">{_html.escape(p.company_name)}</span>'
        f'<div class="pick-data">'
        f'<span class="conv-badge {conv_cls}">{conv_label}</span>'
        f"{price_html}{upside_html}"
        f"</div>"
        f"</div>"
        f'<span class="pick-toggle"></span>'
        f"</summary>"
        f'<div class="pick-body">{rationale_html}{edge_html}</div>'
        f"</details>"
    )


def _member_col_html(logo_src: str, name: str, color: str, logo_cls: str, picks) -> str:
    core = [p for p in picks if p.conviction == "core"]
    moonshots = [p for p in picks if p.conviction == "moonshot"]
    entries = "".join(_pick_entry_html(p, color) for p in core)
    if moonshots:
        entries += '<span class="pick-moon-label">🌙 Moonshots</span>'
        entries += "".join(_pick_entry_html(p, color) for p in moonshots)
    return (
        f'<div class="pick-col-header">'
        f'<img src="{logo_src}" class="pick-col-logo mem-logo {logo_cls}" />'
        f'<span class="pick-col-title" style="color:{color}">{name}</span>'
        f"</div>"
        f'<div class="pick-col-rule" style="background:{color}"></div>'
        f"{entries}"
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.title("Investment Committee")

latest_run = load_latest_run()

if latest_run:
    age = days_since(latest_run.timestamp)
    if age == 0:
        st.sidebar.success("Last run: today")
    elif age <= 7:
        st.sidebar.info(f"Last run: {age} day{'s' if age != 1 else ''} ago")
    elif age <= 30:
        st.sidebar.warning(
            f"Last run: {age} days ago — market conditions may have shifted"
        )
    else:
        st.sidebar.error(
            f"Last run: {age} days ago — consider re-running the committee"
        )
else:
    st.sidebar.warning("No runs yet")

run_clicked = st.sidebar.button(
    "Run Committee", type="primary", use_container_width=True
)

st.sidebar.divider()
st.sidebar.markdown("**Ask the Committee**")
ticker_input = st.sidebar.text_input("Ticker", placeholder="e.g. TSLA").upper().strip()
ask_clicked = st.sidebar.button("Get Opinion", use_container_width=True)

# ── Run committee ─────────────────────────────────────────────────────────────

if run_clicked:
    with st.spinner(
        "Committee deliberating... Claude is researching via web search (~60-90 seconds)"
    ):
        anthropic_client, openai_client, gemini_client = get_clients()
        try:
            latest_run = asyncio.run(
                run_committee(anthropic_client, openai_client, gemini_client)
            )
        except ValueError as e:
            st.error(f"Committee run failed: {e}. Please try again.")
            st.stop()
    st.success("Committee run complete!")
    st.rerun()

# ── Main content ──────────────────────────────────────────────────────────────

if not latest_run:
    st.title("Investment Committee")
    st.info(
        "No portfolio yet. Click **Run Committee** in the sidebar to generate picks."
    )
    st.stop()

all_runs = load_all_runs()

tab_portfolio, tab_performance, tab_breakdown, tab_changes, tab_log, tab_settings = (
    st.tabs(
        [
            "Portfolio",
            "Performance",
            "Member Breakdown",
            "What Changed",
            "Research Log",
            "Settings",
        ]
    )
)

# ── Portfolio tab ─────────────────────────────────────────────────────────────

with tab_portfolio:
    st.header("Current Portfolio")
    st.caption(f"From run on {latest_run.timestamp.strftime('%B %d, %Y at %H:%M UTC')}")

    core = [h for h in latest_run.portfolio if h.conviction == "core"]
    moonshots = [h for h in latest_run.portfolio if h.conviction == "moonshot"]

    st.html(
        f'<div class="portfolio-nav">'
        f'<span class="nav-count">{len(core)} core picks</span>'
        f'<span class="nav-sep">·</span>'
        f'<a href="#moonshots" class="nav-link">🌙 {len(moonshots)} moonshots ↓</a>'
        f"</div>"
    )

    st.subheader("Core Picks")
    for h in core:
        render_holding(h)

    st.html('<span id="moonshots" class="moonshot-header">🌙 Moonshots</span>')
    moon_cols = st.columns(len(moonshots)) if moonshots else [st]
    for col, h in zip(moon_cols, moonshots):
        with col:
            render_holding(h)

# ── Performance tab ───────────────────────────────────────────────────────────

with tab_performance:
    st.header("Performance")

    tickers = [h.ticker for h in latest_run.portfolio]
    weights = [h.weight for h in latest_run.portfolio]

    with st.spinner("Fetching price data..."):
        try:
            perf = fetch_performance(tickers, weights)
        except Exception as e:
            st.error(f"Could not fetch performance data: {e}")
            perf = {}

    if perf:
        range_opt = (
            st.pills(
                "Range",
                ["1W", "1M", "3M", "YTD", "1Y"],
                default="1Y",
                label_visibility="collapsed",
            )
            or "1Y"
        )

        def _range_return(raw: dict) -> float:
            s = _filter_series(raw, range_opt)
            return float(s.iloc[-1]) if not s.empty else 0.0

        p_ret = (
            _range_return(perf["portfolio"]["series"]) if "portfolio" in perf else 0.0
        )
        b_ret = (
            _range_return(perf["benchmark"]["series"]) if "benchmark" in perf else 0.0
        )
        s_ret = _range_return(perf["spy"]["series"]) if "spy" in perf else 0.0

        cols = st.columns(3)
        with cols[0]:
            st.metric("Portfolio", f"{p_ret:+.1f}%")
        with cols[1]:
            st.metric(
                "IGM+NVDA Blend",
                f"{b_ret:+.1f}%",
                delta=f"{b_ret - p_ret:+.1f}% vs portfolio",
            )
        with cols[2]:
            st.metric(
                "SPY",
                f"{s_ret:+.1f}%",
                delta=f"{s_ret - p_ret:+.1f}% vs portfolio",
            )

        st.divider()

        series_data: dict[str, dict] = {}
        if "portfolio" in perf:
            series_data["Portfolio"] = perf["portfolio"]["series"]
        if "benchmark" in perf:
            series_data["IGM+NVDA"] = perf["benchmark"]["series"]
        if "spy" in perf:
            series_data["SPY"] = perf["spy"]["series"]

        if series_data:
            line_styles: dict[str, dict] = {
                "Portfolio": dict(color="#06b6d4", width=2.5),
                "IGM+NVDA": dict(color="#f59e0b", width=2, dash="dash"),
                "SPY": dict(color="#64748b", width=1.5, dash="dot"),
            }
            fig = go.Figure()
            for name, raw_series in series_data.items():
                s = _filter_series(raw_series, range_opt)
                if s.empty:
                    continue
                fig.add_trace(
                    go.Scatter(
                        x=s.index,
                        y=s.round(2).values,
                        name=name,
                        mode="lines",
                        line=line_styles.get(name, dict(width=2)),
                        hovertemplate="%{y:+.2f}%<extra></extra>",
                    )
                )
            fig.update_layout(
                plot_bgcolor="#0f172a",
                paper_bgcolor="#1e293b",
                font=dict(
                    family="Plus Jakarta Sans, sans-serif", color="#94a3b8", size=12
                ),
                xaxis=dict(
                    gridcolor="#1e293b",
                    zeroline=False,
                    tickfont=dict(family="JetBrains Mono, monospace", color="#64748b"),
                ),
                yaxis=dict(
                    gridcolor="#334155",
                    zeroline=False,
                    tickformat=".1f",
                    ticksuffix="%",
                    tickfont=dict(family="JetBrains Mono, monospace", color="#64748b"),
                ),
                legend=dict(
                    bgcolor="rgba(0,0,0,0)",
                    bordercolor="#334155",
                    borderwidth=1,
                    font=dict(color="#94a3b8"),
                ),
                margin=dict(l=0, r=0, t=16, b=0),
                hovermode="x unified",
            )
            st.plotly_chart(fig, use_container_width=True)

        _range_label = {
            "1W": "1 week",
            "1M": "1 month",
            "3M": "3 months",
            "YTD": "year to date",
            "1Y": "1 year",
        }
        st.caption(
            f"Performance shown for {_range_label.get(range_opt, range_opt)}. "
            "Past performance does not predict future results."
        )

# ── Member breakdown tab ──────────────────────────────────────────────────────

with tab_breakdown:
    st.header("Member Picks")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.html(
            _member_col_html(
                _MEMBER_LOGO_SRC["claude"],
                "Claude",
                "#f59e0b",
                "mem-claude",
                latest_run.claude_picks,
            )
        )
        if latest_run.claude_sources:
            with st.expander(f"Sources consulted ({len(latest_run.claude_sources)})"):
                for src in latest_run.claude_sources:
                    st.markdown(f"- [{src.title}]({src.url})")

    with col2:
        st.html(
            _member_col_html(
                _MEMBER_LOGO_SRC["gpt"],
                "GPT",
                "#10b981",
                "mem-gpt",
                latest_run.gpt_picks,
            )
        )

    with col3:
        st.html(
            _member_col_html(
                _MEMBER_LOGO_SRC["gemini"],
                "Gemini",
                "#3b82f6",
                "mem-gemini",
                latest_run.gemini_picks,
            )
        )

# ── What Changed tab ─────────────────────────────────────────────────────────

with tab_changes:
    st.header("Run History")

    run_summary = [
        {
            "Date": r.timestamp.strftime("%b %d, %Y %H:%M UTC"),
            "Core": len([h for h in r.portfolio if h.conviction == "core"]),
            "Moonshots": len([h for h in r.portfolio if h.conviction == "moonshot"]),
            "Consensus": len([h for h in r.portfolio if len(h.nominated_by) > 1]),
        }
        for r in all_runs
    ]
    st.dataframe(pd.DataFrame(run_summary), use_container_width=True, hide_index=True)

    if len(all_runs) < 2:
        st.info("Run the committee again to enable run comparison.")
    else:
        st.divider()
        st.subheader("Compare Runs")

        run_labels = [r.timestamp.strftime("%b %d, %Y %H:%M UTC") for r in all_runs]

        col_a, col_b = st.columns(2)
        with col_a:
            current_idx = st.selectbox(
                "Current run",
                range(len(all_runs)),
                format_func=lambda i: run_labels[i],
                index=0,
            )
        with col_b:
            compare_idx = st.selectbox(
                "Compare against",
                range(len(all_runs)),
                format_func=lambda i: run_labels[i],
                index=1,
            )

        if current_idx == compare_idx:
            st.warning("Select two different runs to compare.")
        else:
            run_a = all_runs[current_idx]
            run_b = all_runs[compare_idx]

            current = {h.ticker: h for h in run_a.portfolio}
            previous = {h.ticker: h for h in run_b.portfolio}

            added = [current[t] for t in current if t not in previous]
            removed = [previous[t] for t in previous if t not in current]
            held = [(previous[t], current[t]) for t in current if t in previous]
            weight_changes = [
                (prev, curr)
                for prev, curr in held
                if abs(curr.weight - prev.weight) >= 0.5
            ]

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Added", len(added))
            with col2:
                st.metric("Removed", len(removed))
            with col3:
                st.metric("Unchanged", len(held) - len(weight_changes))

            st.divider()

            if added:
                st.subheader("New Positions")
                for h in sorted(added, key=lambda x: -x.weight):
                    members = ", ".join(h.nominated_by)
                    consensus = " 🤝" if len(h.nominated_by) > 1 else ""
                    st.markdown(
                        f"**{h.ticker}** — {h.company_name}  |  {h.weight}%{consensus}  |  *{members}*"
                    )

            if removed:
                st.subheader("Exited Positions")
                for h in sorted(removed, key=lambda x: -x.weight):
                    members = ", ".join(h.nominated_by)
                    st.markdown(
                        f"**{h.ticker}** — {h.company_name}  |  was {h.weight}%  |  *{members}*"
                    )

            if weight_changes:
                st.subheader("Significant Weight Changes")
                for prev, curr in sorted(
                    weight_changes, key=lambda x: -abs(x[1].weight - x[0].weight)
                ):
                    delta = curr.weight - prev.weight
                    arrow = "▲" if delta > 0 else "▼"
                    st.markdown(
                        f"**{curr.ticker}** — {curr.company_name}  |  "
                        f"{prev.weight}% → {curr.weight}%  {arrow} {abs(delta):.1f}pp"
                    )

            if not added and not removed and not weight_changes:
                st.success("Portfolio is unchanged between selected runs.")

# ── Research Log tab ──────────────────────────────────────────────────────────

with tab_log:
    st.header("Research Log")

    log_entries = advisor_log.load()

    if not log_entries:
        st.info("No one-off checks yet. Use **Ask the Committee** in the sidebar.")
    else:
        summary_rows = [
            {
                "Date": e["timestamp"][:10],
                "Ticker": e["ticker"],
                "Company": e["company_name"],
                "Recommendation": e["recommendation"].upper(),
                "Allocation %": e.get("suggested_allocation_pct") or "—",
                "Fits Philosophy": "Yes" if e.get("fits_philosophy") else "No",
            }
            for e in reversed(log_entries)
        ]

        def _style_rec(val: str) -> str:
            c = {"BUY": "#10b981", "WATCH": "#f59e0b", "PASS": "#ef4444"}.get(
                val, "#94a3b8"
            )
            return f"color: {c}; font-weight: 600; font-family: 'JetBrains Mono', monospace"

        styled_df = pd.DataFrame(summary_rows).style.map(
            _style_rec, subset=["Recommendation"]
        )
        st.dataframe(styled_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Details")
        for e in reversed(log_entries):
            rec_color = {"buy": "green", "watch": "orange", "pass": "red"}.get(
                e["recommendation"], "gray"
            )
            alloc = (
                f"  |  Suggested allocation: **{e['suggested_allocation_pct']}%**"
                if e.get("suggested_allocation_pct")
                else ""
            )
            label = (
                f"**{e['ticker']}** — {e['company_name']}"
                f"  |  :{rec_color}[{e['recommendation'].upper()}]{alloc}"
                f"  |  {e['timestamp'][:10]}"
            )
            with st.expander(label):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown("**Claude**")
                    st.write(e.get("claude_take") or "—")
                with col2:
                    st.markdown("**GPT**")
                    st.write(e.get("gpt_take") or "—")
                with col3:
                    st.markdown("**Gemini**")
                    st.write(e.get("gemini_take") or "—")

# ── Settings tab ─────────────────────────────────────────────────────────────

with tab_settings:
    st.header("Settings")

    col_tickers, col_sectors = st.columns(2)

    with col_tickers:
        st.subheader("Excluded Tickers")
        for _ticker in sorted(exclusions.EXCLUDED_TICKERS):
            _c1, _c2 = st.columns([5, 1])
            _c1.text(_ticker)
            if _c2.button("✕", key=f"rm_ticker_{_ticker}"):
                exclusions.EXCLUDED_TICKERS.discard(_ticker)
                exclusions.save()
                st.rerun()
        with st.form("add_excluded_ticker", clear_on_submit=True):
            _new_ticker = (
                st.text_input("", placeholder="Add ticker, e.g. AAPL").upper().strip()
            )
            if st.form_submit_button("Add ticker") and _new_ticker:
                exclusions.EXCLUDED_TICKERS.add(_new_ticker)
                exclusions.save()
                st.rerun()

    with col_sectors:
        st.subheader("Excluded Sectors")
        for _sector in sorted(exclusions.EXCLUDED_SECTORS):
            _c1, _c2 = st.columns([5, 1])
            _c1.text(_sector)
            if _c2.button("✕", key=f"rm_sector_{_sector}"):
                exclusions.EXCLUDED_SECTORS.discard(_sector)
                exclusions.save()
                st.rerun()
        with st.form("add_excluded_sector", clear_on_submit=True):
            _new_sector = st.text_input(
                "", placeholder="Add sector, e.g. Utilities"
            ).strip()
            if st.form_submit_button("Add sector") and _new_sector:
                exclusions.EXCLUDED_SECTORS.add(_new_sector)
                exclusions.save()
                st.rerun()

# ── Advisor panel ─────────────────────────────────────────────────────────────

if ask_clicked and ticker_input:
    st.divider()
    st.header(f"Committee Opinion: {ticker_input}")

    current_portfolio = latest_run.portfolio if latest_run else []

    with st.spinner(f"Asking the committee about {ticker_input}..."):
        anthropic_client, openai_client, gemini_client = get_clients()
        try:
            advice = asyncio.run(
                ask_committee(
                    ticker_input,
                    anthropic_client,
                    openai_client,
                    gemini_client,
                    current_portfolio,
                )
            )
        except Exception as e:
            st.error(f"Could not get committee opinion: {e}")
            st.stop()

    advisor_log.append(advice)

    badge_class = {
        "buy": "rec-buy",
        "watch": "rec-watch",
        "pass": "rec-pass",
        "already in portfolio": "rec-portfolio",
    }.get(advice.recommendation, "rec-pass")

    st.markdown(f"### {advice.company_name} ({advice.ticker})")
    st.markdown(
        f'<div class="rec-row">'
        f'<span class="rec-label">Recommendation</span>'
        f'<span class="rec-badge {badge_class}">{advice.recommendation.upper()}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
    if advice.suggested_allocation_pct is not None:
        st.markdown(
            f"**Suggested allocation: {advice.suggested_allocation_pct}%** (avg of committee)"
        )
    st.markdown(
        f"Fits investment philosophy: {'Yes' if advice.fits_philosophy else 'No'}"
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**Claude's take**")
        st.write(advice.claude_take)
    with col2:
        st.markdown("**GPT's take**")
        st.write(advice.gpt_take)
    with col3:
        st.markdown("**Gemini's take**")
        st.write(advice.gemini_take)
