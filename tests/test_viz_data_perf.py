"""Opt-in performance budget for the faceted depth profile (PV-08).

Before the one-pass assembly, 6 tubes x 24 dates (144 subplots) took 2.7-4.3 s
because every per-subplot ``add_shape`` re-validated the whole shapes tuple
(O(subplots²)); it now builds in ~0.4 s on the audit machine. Wall-clock tests
are noisy on shared CI runners, so this one only runs when
``SPROUTS_PERF_TESTS=1`` is set.
"""

import csv
import datetime as dt
import os
import time

import pytest

from app.config import LENGTH_CSV_HEADERS
from app.data_processing.data_processor import DataProcessor
from app.visualization.dash_data_cache import DataCache
from app.visualization.dash_visualizations import DashVisualizations

pytestmark = pytest.mark.skipif(
    os.environ.get("SPROUTS_PERF_TESTS") != "1",
    reason="wall-clock budget; set SPROUTS_PERF_TESTS=1 to run",
)

BUDGET_S = 1.5


class _Charts(DashVisualizations):
    def __init__(self, processor):
        self.data_processor = processor
        self.data_cache = DataCache(processor)
        self.available_images = {}


def _write_campaign(path, tubes=8, dates=24, positions=60):
    start = dt.date(2024, 6, 1)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(LENGTH_CSV_HEADERS)
        for tube in range(1, tubes + 1):
            for d in range(dates):
                day = (start + dt.timedelta(days=5 * d)).strftime("%Y.%m.%d")
                for pos in range(6, 6 + positions):
                    value = round(((tube * 7 + d * 3 + pos) % 23) / 2.0, 2)
                    name = f"CAM_T{tube:03d}_L{pos:03d}_{day}_120000_001"
                    writer.writerow([name, tube, pos, day, "12:00:00", value, ""])
    return str(path)


def test_faceted_6_tubes_by_24_dates_builds_within_budget(tmp_path):
    charts = _Charts(DataProcessor(_write_campaign(tmp_path / "l.csv")))

    start = time.perf_counter()
    fig = charts.create_faceted_depth_profile([1, 2, 3, 4, 5, 6])
    elapsed = time.perf_counter() - start

    assert len(fig.layout.shapes) == 6 * 24
    assert elapsed < BUDGET_S, f"faceted 6x24 took {elapsed:.2f} s (budget {BUDGET_S} s)"
