"""The metrics bar flags each measured value against its own input (W4, W3).

LENGTH was measured from the generated skeleton (``skeletons/<stem>_fake.png``), AREA
from the mask (``mask/<stem>.png``). A saved mask (Ctrl+S with nothing unsaved rewrites
it, by design: spec D7) makes AREA outdated, never LENGTH; a regenerated skeleton makes
LENGTH outdated, never AREA. Each value's tooltip names the CSV it was read from, and a
corrected skeleton, which Measure does not read (FR-03), is named in the LENGTH tooltip.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS  # noqa: E402
from app.data_processing.measurement_index import MeasurementIndex  # noqa: E402
from app.inference.metrics import write_metric_csv  # noqa: E402

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_deleted_widgets():
    """Widgets a test deleteLater()s are destroyed before the next test (never at exit)."""
    yield
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


def _row(stem: str, column: str, value) -> dict:
    return {
        "Image": stem,
        "Tube": 1,
        "Position": 44,
        "Date": "2024.07.18",
        "Time": "11:15:30",
        column: value,
    }


def _shift(path, seconds: float) -> None:
    """Move ``path``'s modification time by ``seconds``."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + int(seconds * 1e9)))


def _measured_project(root):
    """A project whose image has a mask, a generated skeleton and both CSVs, the inputs
    older than the CSVs (nothing outdated)."""
    (root / "mask").mkdir(parents=True)
    (root / "skeletons").mkdir()
    mask = root / "mask" / f"{STEM}.png"
    skeleton = root / "skeletons" / f"{STEM}_fake.png"
    mask.write_bytes(b"m")
    skeleton.write_bytes(b"s")
    lengths = str(root / "skeletons" / "root_lengths.csv")
    areas = str(root / "mask" / "root_areas.csv")
    write_metric_csv([_row(STEM, LENGTH_CSV_HEADERS[5], 18.77)], lengths, LENGTH_CSV_HEADERS)
    write_metric_csv([_row(STEM, AREA_CSV_HEADERS[5], 16.41)], areas, AREA_CSV_HEADERS)
    for path in (mask, skeleton):
        _shift(path, -60)
    return mask, skeleton, lengths, areas


# ------------------------------------------------------------------ MeasurementIndex
def test_a_newer_mask_outdates_the_area_only(tmp_path):
    mask, _skeleton, _lengths, _areas = _measured_project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    assert index.outdated(STEM) == {"length": False, "area": False}

    _shift(mask, 120)  # saved after the Measure run

    assert index.outdated(STEM) == {"length": False, "area": True}
    assert index.is_outdated(STEM) is True  # the old any-metric answer stays


def test_a_newer_generated_skeleton_outdates_the_length_only(tmp_path):
    _mask, skeleton, _lengths, _areas = _measured_project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()

    _shift(skeleton, 120)

    assert index.outdated(STEM) == {"length": True, "area": False}


def test_a_metric_without_a_row_is_never_outdated(tmp_path):
    mask, skeleton, _lengths, _areas = _measured_project(tmp_path)
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()
    for path in (mask, skeleton):
        _shift(path, 120)

    assert index.outdated("24EFCAM1_T001_L045_2024.07.18_111612_001_SMM") == {
        "length": False,
        "area": False,
    }


# ------------------------------------------------------------------------ MetricsBar
def test_each_value_has_its_own_flag_and_source(app):
    from app.gui.metrics_bar import MetricsBar

    bar = MetricsBar()
    bar.set_metrics(
        length=18.77,
        area=16.41,
        stale=False,
        source="D:/p/skeletons/root_lengths.csv",
        area_stale=True,
        area_source="D:/p/mask/root_areas.csv",
    )

    assert bar._length_value.property("stale") is False
    assert bar._area_value.property("stale") is True
    assert "root_lengths.csv" in bar._length_cell.toolTip()
    assert "Measure again" not in bar._length_cell.toolTip()
    assert "root_areas.csv" in bar._area_cell.toolTip()
    assert "root_lengths.csv" not in bar._area_cell.toolTip()
    assert "Measure again" in bar._area_cell.toolTip()
    bar.deleteLater()


def test_the_length_tooltip_names_a_correction_that_is_not_measured(app):
    from app.gui.metrics_bar import MetricsBar

    bar = MetricsBar()
    bar.set_metrics(length=18.77, area=16.41, length_note="A corrected skeleton exists")
    assert "A corrected skeleton exists" in bar._length_cell.toolTip()
    assert "corrected" not in bar._area_cell.toolTip()
    bar.set_metrics(length=18.77, area=16.41)
    assert "corrected" not in bar._length_cell.toolTip()
    bar.deleteLater()


def test_one_stale_flag_still_flags_both_values(app):
    """Callers that pass one flag (and one source) keep today's meaning."""
    from app.gui.metrics_bar import MetricsBar

    bar = MetricsBar()
    bar.set_metrics(length=1.0, area=2.0, stale=True, source="D:/p/x.csv")
    assert bar._length_value.property("stale") is True
    assert bar._area_value.property("stale") is True
    assert "x.csv" in bar._area_cell.toolTip()
    bar.deleteLater()


# ------------------------------------------------------------------------ MainWindow
@pytest.fixture
def window(app, tmp_path):
    from app.gui.main_window import MainWindow

    made = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    yield made
    made.close()
    made.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _open(window, folder) -> None:
    import time

    window.open_path(str(folder))
    end = time.monotonic() + 5
    while time.monotonic() < end and not window._image_leaves():
        QApplication.processEvents()
    item = window.library_index.item_for(STEM)
    window.file_list.setCurrentItem(item)
    window.on_image_selected(item)
    end = time.monotonic() + 5
    bar = window.metrics_bar
    # Both CSVs parsed (in the pool, one after the other): both values are on show.
    while time.monotonic() < end and (bar._length_cell.isHidden() or bar._area_cell.isHidden()):
        QApplication.processEvents()


def _write_jpg(path) -> None:
    from PyQt6.QtGui import QColor, QImage

    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(path))


def test_a_saved_mask_flags_area_not_length_in_the_window(window, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    mask, _skeleton, _lengths, _areas = _measured_project(folder)
    _write_jpg(folder / f"{STEM}.jpg")
    _shift(mask, 120)  # Ctrl+S in Correct rewrote the mask after the Measure run

    _open(window, folder)
    bar = window.metrics_bar

    assert bar._length_value.text() == "18.77 mm"
    assert bar._length_value.property("stale") is False
    assert bar._area_value.property("stale") is True
    assert "root_lengths.csv" in bar._length_cell.toolTip()
    assert "root_areas.csv" in bar._area_cell.toolTip()


def test_a_correction_is_named_in_the_length_tooltip(window, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    _measured_project(folder)
    _write_jpg(folder / f"{STEM}.jpg")
    corrections = folder / "skeleton_corrections"
    corrections.mkdir()
    (corrections / f"{STEM}_skeleton.png").write_bytes(b"c")

    _open(window, folder)
    tip = window.metrics_bar._length_cell.toolTip()

    assert "corrected skeleton" in tip.lower() and "not measured" in tip.lower()
    assert window.metrics_bar._length_value.property("stale") is False
