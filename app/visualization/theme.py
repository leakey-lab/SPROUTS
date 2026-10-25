"""
SPROUTS visualization theme — a single source of truth for the Plotly look.

Drop-in module. Importing it registers two Plotly templates:

    "sprouts"        — dark, matches the SPROUTS desktop shell (default)
    "sprouts_light"  — light variant (same geometry, light surfaces)

and exposes small helpers so chart code stops re-declaring 40-line layout
dicts in every method.

Usage
-----
    from app.visualization import theme           # registers templates on import
    theme.use("sprouts")                           # make it the global default

    fig = go.Figure(...)
    theme.style(fig, title="Root Length by Tube")  # apply title + sane margins

    # build hover text without emoji / fragile inline <span> colours:
    hovertemplate = theme.hover("Tube %{x}", [
        ("Date",   "%{customdata[0]}"),
        ("Length", "%{y:.2f} mm"),
    ])

Design tokens mirror the SPROUTS CSS custom properties (styles.css :root) so the
embedded dashboard and the desktop chrome read as one product.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
from plotly.colors import sample_colorscale

from app.design import tokens as _tokens

# ---------------------------------------------------------------------------
# Design tokens — the shared, Qt-free app.design.tokens (AR-17), so the charts,
# the desktop shell and styles.css :root read as one product.
# ---------------------------------------------------------------------------

# Dark surfaces (deep, desaturated)
BG_0 = _tokens.BG_0  # deepest — app behind / paper
BG_1 = _tokens.BG_1  # chrome
BG_2 = _tokens.BG_2  # panels / plot area
BG_3 = _tokens.BG_3  # raised cards / inputs

BORDER = _tokens.BORDER
BORDER_STRONG = _tokens.BORDER_STRONG

TEXT = _tokens.TEXT
TEXT_MUTED = _tokens.TEXT_MUTED
TEXT_FAINT = _tokens.TEXT_FAINT  # AA on every surface (was #686c82, 3.0:1 on BG_2)

# Brand accents. The dashboards keep the leaf-green primary while the desktop
# shell moved to purple (tokens.ACCENT); switching is an open design decision.
ACCENT = "#5fd6a0"  # SPROUTS leaf green (primary)
SEL = _tokens.SEL  # purple — reserved for selection / highlight
WARN = _tokens.WARN
INFO = _tokens.INFO
DANGER = _tokens.DANGER
SKEL = _tokens.SKEL  # warm — skeleton

# Fonts (loaded via assets/, falls back to system)
FONT_SANS = "IBM Plex Sans, system-ui, -apple-system, Segoe UI, sans-serif"
FONT_MONO = "IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, monospace"

# Categorical palette for multi-series (tubes / dates). Ten hues, all chosen to
# stay legible on a dark plot ground and to stay distinct from each other.
# Replaces px.colors.qualitative.Plotly (the default rainbow).
COLORWAY = [
    "#5fd6a0",  # green   (accent)
    "#79c0e8",  # blue
    "#b794f6",  # purple
    "#e8b25e",  # amber
    "#f0a868",  # orange
    "#ec6a78",  # red
    "#4fd1c5",  # teal
    "#f6a5c0",  # pink
    "#c3e88d",  # lime
    "#9aa5ff",  # periwinkle
]

# One blue ramp for measurement dates: oldest darkest, newest lightest, one hue, every stop
# at least 3:1 on BG_2 and clear of the mask green, skeleton orange and selection purple.
DATE_RAMP = (
    "#127abe",
    "#2c84c4",
    "#3e8dcb",
    "#4f97d1",
    "#5ea1d8",
    "#6cabde",
    "#7bb4e5",
    "#89beeb",
    "#97c8f2",
    "#a6d2f8",
    "#b4dcff",
    "#c8e5ff",
    "#dbeefe",
)

# Sequential scale for ordered data (depth profiles). Plasma is perceptually
# uniform — keep it as the ordered default.
SEQUENTIAL = "Plasma"

# Plasma's dark half (deep indigo -> purple) falls to 1.0-2.9:1 against BG_2, below
# the 3:1 WCAG 1.4.11 minimum for data marks. Marks drawn on the dark plot ground
# therefore sample only this part of the scale (>= 3.3:1, red -> yellow).
SEQUENTIAL_ON_DARK = (0.5, 1.0)
# Ten discrete swatches (the faceted depth bars used ten Plasma swatches before).
_SEQUENTIAL_SWATCHES = sample_colorscale(
    SEQUENTIAL,
    [
        SEQUENTIAL_ON_DARK[0] + (SEQUENTIAL_ON_DARK[1] - SEQUENTIAL_ON_DARK[0]) * i / 9
        for i in range(10)
    ],
)

# Bar value labels are drawn only when at most this many bars share the x axis
# (the tubes of a stacked chart; positions x dates of a grouped profile). With
# more, the labels shrink to unreadable specks and each costs Plotly a text
# measurement: a 160-tube stacked chart rendered in 795 ms with labels and in
# 314 ms without (QtWebEngine, median of 5). The hover shows the values anyway.
MAX_LABELLED_BARS = 30

# Right margin of a chart: room for the legend beside the plot, or a thin edge when
# the legend is hidden (the Growth Over Time view hides it for many tubes).
MARGIN_RIGHT_LEGEND = 180
MARGIN_RIGHT_NO_LEGEND = 24

# Light-variant surface overrides (geometry is shared with the dark template).
_LIGHT = {
    "paper": "#ffffff",
    "plot": "#ffffff",
    "text": "#1f2430",
    "muted": "#5b6172",
    "grid": "#e7e9f0",
    "zero": "#c7cad6",
    "line": "#aeb2c2",
    "hover_bg": "#ffffff",
    "hover_border": "#c7cad6",
    "legend_bg": "rgba(255,255,255,0.92)",
}


# ---------------------------------------------------------------------------
# Template construction
# ---------------------------------------------------------------------------


def _axis(grid: str, zero: str, line: str, tick: str) -> dict:
    return dict(
        showgrid=True,
        gridcolor=grid,
        gridwidth=1,
        zeroline=True,
        zerolinecolor=zero,
        zerolinewidth=1,
        showline=True,
        linecolor=line,
        linewidth=1,
        ticks="outside",
        ticklen=5,
        tickcolor=line,
        tickfont=dict(size=13, color=tick),
        title=dict(font=dict(size=15, color=tick)),
        automargin=True,
    )


def _build(dark: bool) -> go.layout.Template:
    if dark:
        paper, plot = BG_0, BG_2
        text, muted = TEXT, TEXT_MUTED
        grid, zero, line = "rgba(255,255,255,0.06)", BORDER_STRONG, BORDER
        hover_bg, hover_border = BG_3, BORDER_STRONG
        legend_bg = "rgba(33,35,46,0.92)"
    else:
        paper, plot = _LIGHT["paper"], _LIGHT["plot"]
        text, muted = _LIGHT["text"], _LIGHT["muted"]
        grid, zero, line = _LIGHT["grid"], _LIGHT["zero"], _LIGHT["line"]
        hover_bg, hover_border = _LIGHT["hover_bg"], _LIGHT["hover_border"]
        legend_bg = _LIGHT["legend_bg"]

    template = go.layout.Template()

    template.layout = go.Layout(
        font=dict(family=FONT_SANS, size=14, color=text),
        paper_bgcolor=paper,
        plot_bgcolor=plot,
        colorway=COLORWAY,
        title=dict(
            font=dict(family=FONT_SANS, size=20, color=text),
            x=0.5,
            xanchor="center",
            y=0.97,
            yanchor="top",
        ),
        margin=dict(l=64, r=MARGIN_RIGHT_LEGEND, t=64, b=48),
        xaxis=_axis(grid, zero, line, muted),
        yaxis=_axis(grid, zero, line, muted),
        legend=dict(
            font=dict(size=13, color=muted),
            bgcolor=legend_bg,
            bordercolor=line,
            borderwidth=1,
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=1.02,
        ),
        hovermode="closest",
        hoverlabel=dict(
            bgcolor=hover_bg,
            bordercolor=hover_border,
            font=dict(family=FONT_SANS, size=13, color=text),
            align="left",
        ),
        colorscale=dict(sequential=SEQUENTIAL),
        bargap=0.2,
        bargroupgap=0.08,
        # subtle transition so callback-driven figure swaps animate
        transition=dict(duration=250, easing="cubic-in-out"),
    )

    # Per-trace defaults
    template.data.bar = [go.Bar(marker=dict(line=dict(width=0)))]
    template.data.scatter = [
        go.Scatter(line=dict(width=2), marker=dict(size=7, line=dict(width=0)))
    ]

    return template


# Register on import so a bare `import theme` is enough.
pio.templates["sprouts"] = _build(dark=True)
pio.templates["sprouts_light"] = _build(dark=False)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def use(name: str = "sprouts") -> None:
    """Make a SPROUTS template the global Plotly default.

    Call once at app startup (after `import theme`). Every `go.Figure()` created
    afterwards inherits the theme, so individual charts only set title + data.
    """
    pio.templates.default = name


def style(
    fig: go.Figure,
    *,
    title: str | None = None,
    height: int | None = None,
    template: str = "sprouts",
    **layout_kwargs,
) -> go.Figure:
    """Apply the theme to a figure in one call.

    Use for figures where you want to be explicit instead of relying on the
    global default (e.g. figures built before `use()` runs, or subplots from
    make_subplots which do not inherit the default template cleanly).
    """
    fig.update_layout(template=template, **layout_kwargs)
    if title is not None:
        fig.update_layout(title_text=title)
    if height is not None:
        fig.update_layout(height=height)
    return fig


def hover(title: str, rows: list[tuple[str, str]], *, with_extra: bool = True) -> str:
    """Build a clean, consistent hovertemplate — no emoji, no inline colours.

    Colour, font and border come from the template's `hoverlabel`, so the hover
    box matches every other chart automatically.

        hover("Tube %{x}", [("Date", "%{customdata[0]}"), ("Length", "%{y:.2f} mm")])

    Args:
        title: bold first line (may contain Plotly format refs like %{x}).
        rows:  (label, value) pairs; value may contain %{...} format refs.
        with_extra: append "<extra></extra>" to suppress the secondary box.
    """
    lines = [f"<b>{title}</b>"]
    lines += [f"{label}\u2002<b>{value}</b>" for label, value in rows]
    out = "<br>".join(lines)
    return out + "<extra></extra>" if with_extra else out


def color(index: int) -> str:
    """Categorical colour by series index (wraps the colorway)."""
    return COLORWAY[index % len(COLORWAY)]


def date_colors(n: int) -> list[str]:
    """``n`` colours on :data:`DATE_RAMP`, oldest date darkest, newest lightest.

    Up to 13 dates pick evenly spaced stops (``n == 13`` is the whole ramp); more are
    interpolated linearly in sRGB between the 13 stops. A date's colour is its place among
    ALL dates of the trial, so it is the same in every view and for every tube.
    """
    if n <= 0:
        return []
    last = len(DATE_RAMP) - 1
    if n <= len(DATE_RAMP):
        return [DATE_RAMP[round(i * last / (n - 1))] if n > 1 else DATE_RAMP[0] for i in range(n)]
    stops = [tuple(int(stop[i : i + 2], 16) for i in (1, 3, 5)) for stop in DATE_RAMP]
    colours = []
    for i in range(n):
        position = i * last / (n - 1)
        low = min(int(position), last - 1)
        fraction = position - low
        rgb = (
            round(a + (b - a) * fraction) for a, b in zip(stops[low], stops[low + 1], strict=True)
        )
        colours.append("#" + "".join(f"{channel:02x}" for channel in rgb))
    return colours


def margin_right(show_legend: bool) -> int:
    """The chart's right margin: room for the legend only when it is shown."""
    return MARGIN_RIGHT_LEGEND if show_legend else MARGIN_RIGHT_NO_LEGEND


def value_labels(values) -> list[str]:
    """Two-decimal labels; ``""`` for a missing (NaN) value."""
    return ["" if value != value else f"{value:.2f}" for value in values]


def bar_labels(text, bars: int) -> dict:
    """``go.Bar`` label arguments: ``text`` shown inside the bars only when readable.

    Args:
        text: the per-bar labels.
        bars: bars side by side on the x axis (see :data:`MAX_LABELLED_BARS`).
    """
    if bars > MAX_LABELLED_BARS:
        return {"textposition": "none"}
    return {"text": text, "textposition": "auto"}


def sequential_color(fraction: float) -> str:
    """Ordered colour for ``fraction`` in [0, 1] (e.g. relative depth), legible on dark.

    Picks one of ten swatches from the dark-safe part of :data:`SEQUENTIAL`
    (see :data:`SEQUENTIAL_ON_DARK`); values outside [0, 1] are clamped.
    """
    clamped = min(max(fraction, 0.0), 1.0)
    return _SEQUENTIAL_SWATCHES[int(clamped * (len(_SEQUENTIAL_SWATCHES) - 1))]


def rgba(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` + alpha -> ``rgba(r,g,b,a)`` for translucent fills built from tokens."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def message_figure(text: str, *, color: str = TEXT_MUTED, size: int = 20) -> go.Figure:
    """An empty figure that only shows ``text`` centred (empty states, input hints).

    Use ``color=DANGER`` for errors; the default muted tone suits hints.
    """
    fig = go.Figure()
    fig.add_annotation(
        text=text,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=size, color=color),
    )
    # Text only: the empty axes (-1..6 by -1..4) and plot area read as a chart of nothing.
    fig.update_layout(
        xaxis={"visible": False}, yaxis={"visible": False}, plot_bgcolor="rgba(0,0,0,0)"
    )
    return fig
