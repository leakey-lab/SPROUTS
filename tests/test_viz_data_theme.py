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
