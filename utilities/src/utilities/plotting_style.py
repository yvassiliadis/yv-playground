"""
utilities.plotting_style

A Scope3-style plotting style module for consistent matplotlib/seaborn plots.

Usage:
    import utilities.plotting_style as pstyle
    pstyle.apply_style()

    fig, ax = plt.subplots()
    ...
    pstyle.set_title(ax, "Title", "Subtitle")
    pstyle.style_plot(ax, df=df, legend_column="series", format_thousands_axis="y")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.colors import Colormap, LinearSegmentedColormap
import re
from cycler import cycler

try:
    import seaborn as sns  # optional but recommended
except Exception:  # pragma: no cover
    sns = None  # type: ignore

try:
    # Optional dependency
    from pyfonts import load_google_font, set_default_font  # type: ignore
except Exception:  # pragma: no cover
    load_google_font = None  # type: ignore
    set_default_font = None  # type: ignore


# -------- Palette (picked for readability; includes teal/yellow/purple-ish) --------
OKABE_ITO = [
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # green/teal
    "#CC79A7",  # purple
    "#56B4E9",  # sky
    "#F0E442",  # yellow
    "#D55E00",  # vermillion
]

# If you want a slightly "kiwi-ish" feel (still plot-safe):
KIWIISH = [
    "#7CFF6B",  # kiwi green (accent)
    "#00B3A4",  # teal
    "#F0E442",  # yellow
    "#B45CFF",  # purple
    "#56B4E9",  # sky
    "#FF8A00",  # warm orange
    "#0072B2",  # blue
]

def _sorted_hex(colors: Sequence[str]) -> list[str]:
    # sort by hex string (case-insensitive) for deterministic cmap construction
    return sorted(colors, key=lambda c: c.lower())

OKABE_ITO_CMAP = LinearSegmentedColormap.from_list("okabe_ito_cont", _sorted_hex(OKABE_ITO), N=256)
KIWIISH_CMAP = LinearSegmentedColormap.from_list("kiwiish_cont", _sorted_hex(KIWIISH), N=256)

_ACTIVE_CMAP = None

TEXT_COLOR = "#333333"
TITLE_COLOR = "#000000"
SUBTITLE_COLOR = "#8A8A8A"

def _title_case(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    return s.title()

@dataclass(frozen=True)
class PlotFonts:
    base: object | None = None
    bold: object | None = None

def _title_case(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    return s.title()

def apply_style(
    *,
    use_seaborn: bool = True,
    palette: Sequence[str] = OKABE_ITO,
    install_font: bool = True,
    font_name: str = "Funnel Sans",
    cmap: Colormap | None = None,
) -> PlotFonts:
    """
    Apply global plotting defaults (Scope3-style). Call once per notebook/session.

    - Sets seaborn theme/palette (optional)
    - Sets matplotlib rcParams (grid/spines/typography)
    - Sets discrete color cycle from `palette`
    - Sets default continuous colormap inferred from `palette` unless `cmap` provided
    - Optionally sets Funnel Sans as default via pyfonts

    Returns PlotFonts so you can explicitly use bold for titles if desired.
    """
    # --- Optional seaborn theme first (so our rcParams override it) ---
    if use_seaborn and sns is not None:
        sns.set_theme(style="white", context="notebook")
        sns.set_palette(list(palette))

    # --- Core rcParams ---
    mpl.rcParams.update(
        {
            # Figure
            "figure.figsize": (12, 8),
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "figure.autolayout": False,

            # Subplots spacing
            "figure.subplot.hspace": 0.4,
            "figure.subplot.wspace": 0.3,
            "figure.subplot.top": 0.80,  # room for title/subtitle/legend

            # Text
            "text.color": TEXT_COLOR,
            "font.family": "sans-serif",
            "font.size": 11,

            # Axes labels
            "axes.labelsize": 12,
            "axes.labelpad": 5,
            "axes.labelcolor": TEXT_COLOR,

            # Title defaults
            "axes.titlelocation": "left",
            "axes.titlesize": 14,
            "axes.titleweight": "bold",
            "axes.titlecolor": TITLE_COLOR,

            # Axes look
            "axes.edgecolor": "none",
            "axes.linewidth": 0.0,

            # Ticks
            "xtick.color": TEXT_COLOR,
            "ytick.color": TEXT_COLOR,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.major.size": 0,
            "ytick.major.size": 0,
            "xtick.minor.size": 0,
            "ytick.minor.size": 0,

            # Grid (major only)
            "axes.grid": True,
            "axes.grid.which": "major",
            "axes.grid.axis": "both",
            "grid.color": "#999999",
            "grid.alpha": 0.3,
            "grid.linewidth": 0.5,
            "grid.linestyle": ":",

            # Spines OFF (global)
            "axes.spines.left": False,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.spines.bottom": False,

            # Legend global bits
            "legend.frameon": False,
            "legend.fontsize": 12,

            # Lines
            "lines.linewidth": 2.0,
            "lines.solid_capstyle": "round",

            # Formatting niceties
            "axes.formatter.useoffset": False,
        }
    )

    # --- Discrete cycle from palette ---
    mpl.rcParams["axes.prop_cycle"] = cycler(color=list(palette))

    # --- Choose default continuous cmap based on palette (unless explicitly provided) ---
    if cmap is None:
        if list(palette) == list(KIWIISH):
            cmap = KIWIISH_CMAP
        elif list(palette) == list(OKABE_ITO):
            cmap = OKABE_ITO_CMAP
        else:
            cmap = LinearSegmentedColormap.from_list("custom_cont", list(palette), N=256)

    # --- Register + set global default cmap ---
    try:
        mpl.colormaps.register(cmap, force=True)  # mpl>=3.5-ish
    except Exception:
        # older fallback
        try:
            mpl.cm.register_cmap(name=cmap.name, cmap=cmap)
        except Exception:
            pass

    mpl.rcParams["image.cmap"] = cmap.name
    
    global _ACTIVE_CMAP
    _ACTIVE_CMAP = cmap

    # --- Fonts (optional) ---
    if (not install_font) or load_google_font is None or set_default_font is None:
        return PlotFonts()

    try:
        font = load_google_font(font_name)
        font_bold = load_google_font(font_name, weight=800)
        set_default_font(font)  # make it the global default
        return PlotFonts(base=font, bold=font_bold)
    except Exception:
        return PlotFonts()

# def set_title(
#     ax: plt.Axes,
#     title: str,
#     subtitle: Optional[str] = None,
#     *,
#     title_pad: int = 44,
#     subtitle_y: float = 1.03,
#     subtitle_fontsize: int = 11,
#     subtitle_color: str = SUBTITLE_COLOR,
#     subtitle_weight: str = "regular",
#     title_case: bool = True,
# ) -> None:
#     """
#     Title aligned per rcParam axes.titlelocation (default left),
#     subtitle directly underneath, both above the plotting area.
#     """
#     if title_case:
#         title = _title_case(title) if title else title
#         subtitle = _title_case(subtitle) if subtitle else subtitle

#     ax.set_title(title, pad=title_pad)

#     if not subtitle:
#         return

#     loc = mpl.rcParams.get("axes.titlelocation", "center")
#     if loc == "left":
#         x, ha = 0.0, "left"
#     elif loc == "right":
#         x, ha = 1.0, "right"
#     else:
#         x, ha = 0.5, "center"

#     ax.text(
#         x,
#         subtitle_y,
#         subtitle,
#         transform=ax.transAxes,
#         ha=ha,
#         va="bottom",
#         fontsize=subtitle_fontsize,
#         fontweight=subtitle_weight,
#         color=subtitle_color,
#         clip_on=False,
#     )

def set_title(
    ax: plt.Axes,
    title: str,
    subtitle: Optional[str] = None,
    *,
    title_y: float = 1.12,
    subtitle_gap: float = 0.04,   # <-- what you asked for
    subtitle_fontsize: int = 11,
    subtitle_color: str = SUBTITLE_COLOR,
    subtitle_weight: str = "regular",
    title_case: bool = True,
) -> None:
    if title_case and title:
        title = _title_case(title)
        if subtitle:
            subtitle = _title_case(subtitle)

    # remove any existing matplotlib title
    ax.set_title("")

    loc = mpl.rcParams.get("axes.titlelocation", "center")
    if loc == "left":
        x, ha = 0.0, "left"
    elif loc == "right":
        x, ha = 1.0, "right"
    else:
        x, ha = 0.5, "center"

    # Title
    ax.text(
        x, title_y, title,
        transform=ax.transAxes,
        ha=ha, va="bottom",
        fontsize=mpl.rcParams.get("axes.titlesize", 14),
        fontweight=mpl.rcParams.get("axes.titleweight", "bold"),
        color=mpl.rcParams.get("axes.titlecolor", TITLE_COLOR),
        clip_on=False,
    )

    # Subtitle
    if subtitle:
        ax.text(
            x, title_y - subtitle_gap, subtitle,
            transform=ax.transAxes,
            ha=ha, va="bottom",
            fontsize=subtitle_fontsize,
            fontweight=subtitle_weight,
            color=subtitle_color,
            clip_on=False,
        )


def style_plot(
    ax: plt.Axes,
    *,
    df=None,
    legend_column: Optional[str] = None,
    format_thousands_axis: Optional[str] = None,  # "x"|"y"|"both"|None
    x_rotation: int = 30,
    y_rotation: int = 10,
    legend: bool = True,
    legend_y: float = 1.01,
    legend_max_cols: int = 6,
    legend_bbox: Tuple[float, float, float, float] = (0, 1.01, 1, 0.2),
    apply_cmap: bool = True,
) -> None:
    """
    One-stop post-plot styling (like Scope3's style_plot).
    Call once per axes after plotting.
    """
    # Tick rotation (rcParams keys don't exist for rotation in some mpl versions)
    ax.tick_params(axis="x", rotation=x_rotation)
    ax.tick_params(axis="y", rotation=y_rotation)

    # Thousands separators
    if format_thousands_axis:
        formatter = mticker.FuncFormatter(lambda x, p: format(int(x), ","))
        if format_thousands_axis in ("y", "both"):
            ax.yaxis.set_major_formatter(formatter)
        if format_thousands_axis in ("x", "both"):
            ax.xaxis.set_major_formatter(formatter)

    # Legend: horizontal, below subtitle (and above plot)
    if legend:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            if df is not None and legend_column is not None:
                try:
                    n_items = int(df[legend_column].nunique())
                except Exception:
                    n_items = len(labels)
            else:
                n_items = len(labels)

            ncol = max(1, min(n_items, legend_max_cols))

            ax.legend(
                handles, labels,
                loc="lower left",
                bbox_to_anchor=(0.0, legend_y),  # point anchor, not full-width box
                ncol=ncol,
                frameon=False,
                borderaxespad=0.0,
                columnspacing=1.0,
                handletextpad=0.6,
                handlelength=1.6,
                labelspacing=0.6,
            )


    if ax.get_xlabel():
        ax.set_xlabel(_title_case(ax.get_xlabel()))
    if ax.get_ylabel():
        ax.set_ylabel(_title_case(ax.get_ylabel()))

    if apply_cmap and _ACTIVE_CMAP is not None:
        for im in getattr(ax, "images", []):
            im.set_cmap(_ACTIVE_CMAP)
        for coll in getattr(ax, "collections", []):
            if hasattr(coll, "set_cmap"):
                coll.set_cmap(_ACTIVE_CMAP)
