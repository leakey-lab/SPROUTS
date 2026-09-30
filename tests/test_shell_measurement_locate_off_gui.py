"""The metrics bar never walks folders for a legacy CSV on the GUI thread (review finding).

``MeasurementIndex.lookup(load=False)`` / ``needs_load()`` are the GUI thread's calls (every
library selection). Without a ``root_lengths.csv`` in ``skeletons/`` or the project folder,
locating one fell through to the legacy ``test_latest`` search, a walk of the project
folders and their parents: about 60 ms per selection on a 46k-image project (at most every
2 s). The GUI-thread calls now look only where Measure writes today (a few stats); the walk
runs in the worker's :meth:`MeasurementIndex.prewarm`, which the window starts once when a
project opens, so a legacy CSV is still found.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app import project_layout  # noqa: E402
from app.config import LENGTH_CSV_HEADERS  # noqa: E402
from app.data_processing.measurement_index import MeasurementIndex  # noqa: E402
from app.inference.metrics import write_metric_csv  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
LEGACY = ("skeletonizer", "test_latest", "images")


@pytest.fixture
def walks(monkeypatch):
    """Every legacy ``test_latest`` search, as the folder it started from."""
    seen: list[str] = []
    real = project_layout.find_test_latest_dir

    def spy(start, *args, **kwargs):
        seen.append(start)
        return real(start, *args, **kwargs)

    monkeypatch.setattr(project_layout, "find_test_latest_dir", spy)
    return seen


def _legacy_lengths(root) -> str:
    folder = root.joinpath(*LEGACY)
    folder.mkdir(parents=True)
    row = {
        "Image": STEM,
        "Tube": 1,
        "Position": 44,
        "Date": "2024.07.18",
        "Time": "11:15:30",
        LENGTH_CSV_HEADERS[5]: 9.25,
    }
    path = str(folder / "root_lengths.csv")
    write_metric_csv([row], path, LENGTH_CSV_HEADERS)
    return path


def test_the_gui_thread_calls_never_walk_for_a_legacy_csv(tmp_path, walks):
    now = [0.0]
    index = MeasurementIndex(str(tmp_path), clock=lambda: now[0])
    for _ in range(3):
        now[0] += 5.0  # past the relocation throttle every time
        index.lookup(STEM, load=False)
        index.needs_load()
        index.outdated(STEM)
        index.sources()
    assert walks == []


def test_the_worker_prewarm_still_finds_a_legacy_csv(tmp_path, walks):
    _legacy_lengths(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    assert index.lookup(STEM, load=False).length_mm is None  # not found on the GUI thread

    index.prewarm()  # a worker's job

    assert index.lookup(STEM, load=False).length_mm == 9.25
    assert index.sources()["length"].endswith("root_lengths.csv")


def test_a_csv_measure_writes_later_is_found_without_a_walk(tmp_path, walks):
    now = [0.0]
    index = MeasurementIndex(str(tmp_path), clock=lambda: now[0])
    index.prewarm()
    walks.clear()
    folder = tmp_path / "skeletons"
    folder.mkdir()
    row = {"Image": STEM, "Tube": 1, "Position": 44, "Date": "d", "Time": "t"}
    row[LENGTH_CSV_HEADERS[5]] = 3.5
    write_metric_csv([row], str(folder / "root_lengths.csv"), LENGTH_CSV_HEADERS)
    now[0] += 5.0

    assert index.needs_load() is True
    index.prewarm()
    assert index.lookup(STEM, load=False).length_mm == 3.5


# ------------------------------------------------------------------------ the window
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


def test_the_bar_never_waits_for_a_csv_the_pool_is_parsing(app, tmp_path):
    """needs_load() takes the CSV lock, which the pool's parse holds (about 50 ms at 46k
    rows here): while a parse runs, a selection must not ask it (the parse's ready signal
    refreshes the bar)."""
    from app.gui.main_window import MainWindow

    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        index = MeasurementIndex(str(tmp_path))
        asked = []
        index.needs_load = lambda: asked.append(1) or False
        window._measurements = index
        window._measurements_loading = True  # a pool job is parsing

        window._refresh_metrics_bar(STEM)

        assert asked == []
    finally:
        window.close()
        window.deleteLater()
        flush_deleted()


def test_a_legacy_csv_reaches_the_metrics_bar(app, tmp_path):
    from app.gui.main_window import MainWindow

    root = tmp_path / "proj"
    root.mkdir()
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(root / f"{STEM}.jpg"))
    _legacy_lengths(root)
    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        window.open_path(str(root))
        end = time.monotonic() + 5
        while time.monotonic() < end and not window._image_leaves():
            QApplication.processEvents()
        item = window.library_index.item_for(STEM)
        window.file_list.setCurrentItem(item)
        window.on_image_selected(item)
        end = time.monotonic() + 5
        while time.monotonic() < end and window.metrics_bar._length_cell.isHidden():
            QApplication.processEvents()
        assert window.metrics_bar._length_value.text() == "9.25 mm"
    finally:
        window.close()
        window.deleteLater()
        flush_deleted()
