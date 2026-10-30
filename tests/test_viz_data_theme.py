"""PV-14: every length chart reads on the dark SPROUTS plot ground.

Walks the serialised figure of every DashVisualizations builder and checks that
no light-theme colour literal survives and that each data mark (trace line or
marker) reaches the WCAG 1.4.11 non-text contrast of 3:1 against the plot
background; trace text needs 4.5:1 (WCAG 1.4.3).
"""

import pytest

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.visualization import theme
from app.visualization.dash_data_cache import DataCache
from app.visualization.dash_visualizations import DashVisualizations
from tests.test_viz_data_cache import write_metric_csv

LIGHT_THEME_LITERALS = {"black", "white", "gray", "grey", "lightgray", "lightgrey"}
LIGHT_THEME_LITERALS |= {"red", "green", "blue"}
COLOUR_KEYS = {"color", "fillcolor", "gridcolor", "zerolinecolor", "linecolor", "bordercolor"}


class _Charts(DashVisualizations):
    def __init__(self, processor):
        self.data_processor = processor
        self.data_cache = DataCache(processor)
        self.available_images = {}


BUILDERS = {
    "stacked": lambda c: c.create_stacked_bar_chart(),
    "time": lambda c: c.show_growth_over_time([1, 2], True, True, True),
    "lines": lambda c: c.show_growth_lines(1),
    "faceted": lambda c: c.create_faceted_depth_profile([1, 2]),
    "faceted-message": lambda c: c.create_faceted_depth_profile([]),
}


@pytest.fixture(scope="module")
def charts(tmp_path_factory):
    path = write_metric_csv(tmp_path_factory.mktemp("theme") / "l.csv", LENGTH_CSV_HEADERS)
    return _Charts(DataProcessor(path))


def _parse_colour(value: str):
    """'#rrggbb' / 'rgb(r, g, b)' / 'rgba(r, g, b, a)' -> ((r, g, b), alpha)."""
    value = value.strip()
    if value.startswith("#") and len(value) == 7:
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5)), 1.0
    inside = value[value.index("(") + 1 : value.index(")")].split(",")
    channels = tuple(float(x) for x in inside[:3])
    return channels, float(inside[3]) if len(inside) == 4 else 1.0


def _luminance(rgb) -> float:
    def linear(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
    lf = _luminance(_parse_colour(foreground)[0])
    lb = _luminance(_parse_colour(background)[0])
    return (max(lf, lb) + 0.05) / (min(lf, lb) + 0.05)


def _walk_colours(node, path=""):
    """Yield (path, colour) for every colour-valued key, expanding colour arrays."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "template":
                continue  # the template is theme.py itself
            child = f"{path}.{key}"
            if key in COLOUR_KEYS and isinstance(value, str):
                yield child, value
            elif key in COLOUR_KEYS and isinstance(value, list):
                yield from ((f"{child}[{i}]", v) for i, v in enumerate(value))
            else:
                yield from _walk_colours(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_colours(value, f"{path}[{i}]")


def test_contrast_helper_matches_known_pairs():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("rgb(236, 238, 245)", "#21232e") == pytest.approx(13.47, abs=0.01)
    with pytest.raises(ValueError):
        contrast_ratio("black", "#21232e")  # named colours are not parsed (and not allowed)


@pytest.mark.parametrize("name", BUILDERS)
def test_no_light_theme_colour_literals(charts, name):
    fig = BUILDERS[name](charts).to_plotly_json()

    literals = [
        (path, colour)
        for path, colour in _walk_colours(fig)
        if colour.strip().lower() in LIGHT_THEME_LITERALS
    ]
    assert literals == []


@pytest.mark.parametrize("name", BUILDERS)
def test_trace_marks_reach_graphical_contrast(charts, name):
    fig = BUILDERS[name](charts).to_plotly_json()

    weak = []
    for i, trace in enumerate(fig["data"]):
        for part, minimum in (("line", 3.0), ("marker", 3.0), ("textfont", 4.5)):
            for path, colour in _walk_colours({part: trace.get(part, {})}, f"data[{i}]"):
                if path.endswith("line.color") and part == "marker":
                    continue  # bar outlines are separators, not data marks
                try:
                    if _parse_colour(colour)[1] == 0:
                        continue  # fully transparent (e.g. the edges of a variance band)
                    ratio = contrast_ratio(colour, theme.BG_2)
                except ValueError:
                    weak.append((path, colour, "not a hex/rgb colour"))
                    continue
                if ratio < minimum:
                    weak.append((path, colour, round(ratio, 2)))
    assert weak == []


@pytest.mark.parametrize("name", ["TEXT", "TEXT_MUTED", "TEXT_FAINT"])
def test_text_tokens_are_readable_on_every_dashboard_surface(name):
    # WCAG 1.4.3 AA for normal text; the tokens come from app.design.tokens (AR-17)
    for surface in (theme.BG_0, theme.BG_1, theme.BG_2, theme.BG_3):
        assert contrast_ratio(getattr(theme, name), surface) >= 4.5, (name, surface)


@pytest.mark.parametrize("color", [theme.TEXT_MUTED, theme.DANGER])
def test_a_message_figure_is_text_only(color):
    # R-VIZ: hints and errors ("Select 1 to 6 tubes...") were drawn over an empty
    # chart, axes -1..6 by -1..4 and a plot area, which read as a chart of nothing.
    fig = theme.message_figure("Select 1 to 6 tubes", color=color)

    assert fig.data == ()
    assert fig.layout.xaxis.visible is False and fig.layout.yaxis.visible is False
    assert fig.layout.plot_bgcolor == "rgba(0,0,0,0)"
    (note,) = fig.layout.annotations
    assert (note.text, note.x, note.y, note.font.color) == ("Select 1 to 6 tubes", 0.5, 0.5, color)


# ---- O27: one blue ramp for dates -------------------------------------------------


def _oklab(hex_colour):
    """sRGB '#rrggbb' -> OKLab (Ottosson): linearise, LMS matrix, cube root, Lab matrix."""
    r, g, b = (
        (c / 255.0 / 12.92 if c / 255.0 <= 0.04045 else ((c / 255.0 + 0.055) / 1.055) ** 2.4)
        for c in _parse_colour(hex_colour)[0]
    )
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _delta(a, b):
    """OKLab distance x 100."""
    return 100 * sum((x - y) ** 2 for x, y in zip(_oklab(a), _oklab(b), strict=True)) ** 0.5


def test_date_ramp_is_the_decided_list_and_n13_returns_it_whole():
    assert theme.DATE_RAMP == (
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
    assert theme.date_colors(13) == list(theme.DATE_RAMP)
    assert theme.date_colors(1) == ["#127abe"] and theme.date_colors(2) == ["#127abe", "#dbeefe"]
    assert theme.date_colors(0) == []
    assert theme.date_colors(-3) == []


@pytest.mark.parametrize("n", range(1, 25))
def test_date_colors_are_ordered_readable_and_clear_of_the_layer_accents(n):
    colours = theme.date_colors(n)
    assert len(colours) == n and all(len(c) == 7 and c.startswith("#") for c in colours)
    lightness = [_oklab(c)[0] for c in colours]
    assert lightness == sorted(lightness)  # oldest darkest, newest lightest
    assert all(contrast_ratio(c, theme.BG_2) >= 3.0 for c in colours)
    for accent in (theme.ACCENT, theme.SKEL, theme.SEL):  # mask green, skeleton, selection
        assert min(_delta(c, accent) for c in colours) >= 10, accent
