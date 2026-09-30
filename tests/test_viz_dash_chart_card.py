"""viz-dash: the chart card keeps the chart's width when the faceted grid scrolls (R-VIZ).

The faceted depth profile is taller than the window and scrolls inside the chart
card. The card's scrollbar appeared only once the grid had been drawn, narrowed the
chart by its width and so triggered a full redraw of the 60-80 subplot grid: every
faceted chart was drawn twice (measured 1805 -> 1343 ms from the tube pick to the
last redraw, 46,970-row trial, median of 6). The gutter is reserved instead.
"""

from __future__ import annotations

import re

from app.visualization.dash_shell import ASSETS_DIR


def _rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no {selector} rule"
    return match.group(1)


def test_the_chart_card_reserves_its_scrollbar_gutter():
    with open(f"{ASSETS_DIR}/style.css", encoding="utf-8") as fh:
        body = _rule(fh.read(), ".viz-chart-card")
    declarations = {
        name.strip(): value.strip()
        for name, _, value in (
            d.partition(":") for d in re.sub(r"/\*.*?\*/", "", body, flags=re.S).split(";")
        )
        if name.strip()
    }
    assert declarations.get("overflow-y") == "auto"
    assert declarations.get("scrollbar-gutter") == "stable"
    # the gutter shows the card: the charts' paper colour keeps it invisible
    assert declarations.get("background") == "var(--bg-0)"
