"""PV-01: deep positions (L121-L127) must reach every length chart.

``DataProcessor`` used to downcast ``Position`` to int8, and the interval arithmetic
``((max_pos - 1) // 10 + 1) * 10`` then overflowed (124 -> 130 wraps to -126). Every
tube/date whose deepest image sat at L121-L127 silently vanished from Growth Lines and
showed "No Data" / a zero field average in the faceted view. These tests pin the fixed
behaviour on a hand-built CSV: tube 1 has a shallow date (L101-L110) and a deep date
(L115-L127), with ``Length (mm) = position - 100`` so every expected value is simple.
"""

import csv

import pandas as pd
import pytest

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.visualization.dash_data_cache import DataCache
from app.visualization.dash_visualizations import DashVisualizations

SHALLOW = pd.Timestamp("2024-07-18")
DEEP = pd.Timestamp("2024-08-07")


class _Charts(DashVisualizations):
    """Minimal host for the DashVisualizations mixin (the attributes DashApp provides)."""

    def __init__(self, processor):
        self.data_processor = processor
        self.data_cache = DataCache(processor)
        self.available_images = {}


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LENGTH_CSV_HEADERS)
        writer.writeheader()
        for tube, position, date in rows:
            writer.writerow(
                {
                    "Image": f"CAM_T{tube:03d}_L{position:03d}_{date}_120000_001",
                    "Tube": tube,
                    "Position": position,
                    "Date": date,
                    "Time": "12:00:00",
                    "Length (mm)": float(position - 100),
                    "Error": "",
                }
            )


@pytest.fixture
def deep_csv(tmp_path):
    path = tmp_path / "root_lengths.csv"
    rows = [(1, p, "2024.07.18") for p in range(101, 111)]
    rows += [(1, p, "2024.08.07") for p in range(115, 128)]
    _write_csv(path, rows)
    return str(path)


def test_interval_table_keeps_positions_121_to_127(deep_csv):
    cache = DataCache(DataProcessor(deep_csv))

    table = cache.get_interval_data(1, DEEP, 10)

    assert sorted(table) == [120, 130]
    # L115-L120 -> lengths 15..20; L121-L127 -> lengths 21..27
    assert table[120]["count"] == 6
    assert table[120]["avg"] == pytest.approx(17.5)
    assert table[130]["count"] == 7
    assert table[130]["avg"] == pytest.approx(24.0)
    assert table[130]["min"] == pytest.approx(21.0)
    assert table[130]["max"] == pytest.approx(27.0)
    assert (table[130]["interval_start"], table[130]["interval_end"]) == (121, 130)
    assert sum(stats["count"] for stats in table.values()) == 13


def test_growth_lines_draw_the_deep_date(deep_csv):
    fig = _Charts(DataProcessor(deep_csv)).show_growth_lines(1)

    traces = {trace.name: trace for trace in fig.data}
    assert set(traces) == {"2024-07-18", "2024-08-07"}
    assert list(traces["2024-08-07"].y) == [120, 130]
    assert list(traces["2024-08-07"].x) == pytest.approx([17.5, 24.0])


def test_faceted_profile_shows_deep_cell_and_nonzero_field_average(deep_csv):
    fig = _Charts(DataProcessor(deep_csv)).create_faceted_depth_profile([1])

    assert not [t for t in fig.data if getattr(t, "mode", None) == "text"], "no 'No Data' cells"
    field = [t for t in fig.data if t.name == "Field Avg"]
    tube = [t for t in fig.data if t.name == "Tube"]
    assert len(field) == len(tube) == 2  # one pair of bars per date row
    deep_field = next(t for t in field if list(t.y) == [120, 130])
    # field average of a single tube equals that tube's interval means, drawn leftwards
    assert list(deep_field.x) == pytest.approx([-17.5, -24.0])
