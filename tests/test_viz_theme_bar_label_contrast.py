"""Bar value labels keep the text colour plotly.js 3 gave them (plotly.js 4 bundle).

plotly.js 4 picks the colour of a label inside a bar by WCAG contrast instead of
tinycolor's brightness, so labels on some mid-blue date colours would turn from dark grey
to white. ``theme.bar_labels`` sets ``insidetextfont.color`` on exactly those bars, so every
other figure stays as it was, JSON included.
"""

from __future__ import annotations

import pytest

from app.visualization import theme


def _rgb(colour):
    return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))


def _plotly3_pick(colour):  # tinycolor isDark: brightness < 128
    r, g, b = _rgb(colour)
    return "#fff" if (r * 299 + g * 587 + b * 114) / 1000 < 128 else "#444"


def _luminance(colour):
    def linear(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(v) for v in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _plotly4_pick(colour):  # Color.isDark of plotly.js 4.1.1 (culori wcagContrast)
    def ratio(a, b):
        hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    return "#fff" if ratio(colour, "#ffffff") > ratio(colour, "#444444") else "#444"


@pytest.mark.parametrize("dates", range(1, 61))
def test_every_date_colour_keeps_its_plotly3_label_colour(dates):
    for colour in theme.date_colors(dates):
        labels = theme.bar_labels(["1.00"], 1, colour)
        shown = labels.get("insidetextfont", {}).get("color") or _plotly4_pick(colour)
        assert shown == _plotly3_pick(colour), colour


def test_only_the_bars_whose_pick_changed_get_a_text_colour():
    assert theme.bar_labels(["1.00"], 1, "#4f97d1")["insidetextfont"] == {"color": "#444"}
    for colour in ("#127abe", "#3e8dcb", "#5ea1d8", "#dbeefe", "#5fd6a0"):
        assert "insidetextfont" not in theme.bar_labels(["1.00"], 1, colour), colour
    assert theme.bar_labels(["1.00"], 1) == {"text": ["1.00"], "textposition": "auto"}
    assert theme.bar_labels(["1.00"], theme.MAX_LABELLED_BARS + 1, "#4f97d1") == {
        "textposition": "none"
    }


def test_the_owners_eleven_dates_need_no_text_colour():
    # The owner's trial has 11 dates: its figures stay as they were, JSON included.
    assert all(theme.inside_label_colour(c) is None for c in theme.date_colors(11))
