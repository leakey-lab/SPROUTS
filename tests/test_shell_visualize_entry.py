"""Entering Visualize opens the highlighted dashboard, or says what to run first (W5).

Before, the first Visualize visit (and the first after another folder opened) showed the
Library photo with the Length segment highlighted, and nothing opened until the segment was
clicked again. Now entering the stage opens the highlighted dashboard; when its CSV does
not exist yet the stage page names the next step (Measure, or Generate when there is
nothing to measure) with a button to it, instead of a message box. Every stage change is
also reported to ``visualization_manager.stage_changed`` when that exists (it releases the
dashboards some minutes after Visualize is left).

Offscreen; the dashboards themselves are replaced by recorders (building one needs Dash).
"""

from __future__ import annotations

import csv
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app import config  # noqa: E402
from app.gui.panels import Panel  # noqa: E402

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def guards(monkeypatch):
    """No message box (the empty state replaces it) and no delayed temp sweeps."""
    from app.gui import image_manager

    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox, name, lambda *a, n=name, **k: pytest.fail(f"QMessageBox.{n}: {a[2:]}")
        )
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    """A shown window whose dashboards are recorders: ``window.shown`` lists the kinds."""
    from app.gui.main_window import MainWindow

    made = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.resize(1280, 800)
    made.shown = []
    for kind in ("length", "area"):
        monkeypatch.setattr(
            made, f"show_root_{kind}_visualization", lambda k=kind: made.shown.append(k)
        )
    made.show()
    _settle()
    yield made
    made.close()
    made.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _settle():
    for _ in range(3):
        QApplication.processEvents()


def _project(root, *, lengths=False, areas=False, skeletons=False, masks=False):
    root.mkdir(parents=True, exist_ok=True)
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    for stem in STEMS:
        assert image.save(str(root / f"{stem}.jpg"))
    if skeletons or lengths:
        (root / "skeletons").mkdir(exist_ok=True)
    if masks or areas:
        (root / "mask").mkdir(exist_ok=True)
    if skeletons:
        for stem in STEMS:
            image.save(str(root / "skeletons" / f"{stem}_fake.png"))
    if masks:
        for stem in STEMS:
            image.save(str(root / "mask" / f"{stem}.png"))
    for wanted, folder, name, headers in (
        (lengths, "skeletons", config.ROOT_LENGTHS_CSV, config.LENGTH_CSV_HEADERS),
        (areas, "mask", config.ROOT_AREAS_CSV, config.AREA_CSV_HEADERS),
    ):
        if wanted:
            with open(root / folder / name, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(headers)
                writer.writerow([STEMS[0], "1", "11", "2024.07.18", "11:15:11", "1.5", ""])
    return root


def _open(window, folder) -> None:
    window.open_path(str(folder))
    end = time.monotonic() + 5
    while time.monotonic() < end and not window._image_leaves():
        QApplication.processEvents()
    assert window._image_leaves()
    _settle()


def _enter(window, stage: str) -> None:
    window.ribbon_buttons[stage].click()
    _settle()


# ======================================================================= the first entry
def test_entering_visualize_opens_the_highlighted_length_dashboard(window, tmp_path):
    _open(window, _project(tmp_path / "p", lengths=True, areas=True, skeletons=True))
    assert window.action_bar_viz_seg.value() == "length"

    _enter(window, "Visualize")

    assert window.shown == ["length"]
    assert window.ribbon_buttons["Visualize"].isChecked()


def test_entering_visualize_again_opens_the_last_chosen_dashboard(window, tmp_path):
    _open(window, _project(tmp_path / "p", lengths=True, areas=True, skeletons=True, masks=True))
    _enter(window, "Visualize")
    window.action_bar_viz_seg.button("area").click()
    _enter(window, "Library")
    window.shown.clear()

    _enter(window, "Visualize")

    assert window.shown == ["area"]


def test_after_another_folder_opens_entering_visualize_opens_length_again(window, tmp_path):
    folder = _project(tmp_path / "p", lengths=True, areas=True, skeletons=True, masks=True)
    _open(window, folder)
    _enter(window, "Visualize")
    window.action_bar_viz_seg.button("area").click()
    _enter(window, "Library")
    _open(window, _project(tmp_path / "q", lengths=True, skeletons=True))
    window.shown.clear()

    _enter(window, "Visualize")

    assert window.shown == ["length"]
    assert window.action_bar_viz_seg.value() == "length"


# ======================================================================== the empty state
def test_no_length_csv_shows_the_next_step_instead_of_a_message_box(window, tmp_path):
    _open(window, _project(tmp_path / "p", skeletons=True))

    _enter(window, "Visualize")

    page = window.visualization_widget
    assert window.shown == []
    assert window.right_panel.currentIndex() == Panel.VISUALIZATION
    assert window.ribbon_buttons["Visualize"].isChecked()
    assert "root length" in page.title_label.text().lower()
    assert "Measure" in page.body_label.text()
    assert config.ROOT_LENGTHS_CSV in page.detail_label.text()
    assert page.action_button.isVisibleTo(page) and page.next_stage() == "Measure"

    page.action_button.click()
    _settle()
    assert window.ribbon_buttons["Measure"].isChecked()
    assert window.right_panel.currentIndex() == Panel.DISPLAY


def test_without_masks_the_area_empty_state_points_to_generate(window, tmp_path):
    _open(window, _project(tmp_path / "p"))
    _enter(window, "Visualize")
    window.action_bar_viz_seg.button("area").click()
    _settle()

    page = window.visualization_widget
    assert window.shown == []
    assert "root area" in page.title_label.text().lower()
    assert page.next_stage() == "Generate"
    assert "Generate" in page.body_label.text()


def test_the_empty_state_opens_the_dashboard_once_its_csv_exists(window, tmp_path):
    folder = _project(tmp_path / "p", skeletons=True)
    _open(window, folder)
    _enter(window, "Visualize")
    assert window.shown == []
    _enter(window, "Measure")
    _project(folder, lengths=True)  # the Measure run wrote the CSV

    _enter(window, "Visualize")

    assert window.shown == ["length"]


def test_a_folder_opened_on_the_empty_state_goes_back_to_the_library(window, tmp_path):
    _open(window, _project(tmp_path / "p"))
    _enter(window, "Visualize")
    assert window.right_panel.currentIndex() == Panel.VISUALIZATION

    _open(window, _project(tmp_path / "q"))

    assert window.ribbon_buttons["Library"].isChecked()
    assert window.right_panel.currentIndex() == Panel.DISPLAY


def test_a_run_rewriting_the_data_shows_a_wait_then_opens_the_dashboard(window, tmp_path):
    """A Measure (or skeleton) run going: no "Please wait" box; the dashboard opens when the
    run ends."""
    _open(window, _project(tmp_path / "p", lengths=True, skeletons=True))
    busy = {"length": True}
    window.skeleton_handler.is_busy = lambda kind=None: busy.get(kind, False)
    window.jobs._name = "Measuring root length"

    _enter(window, "Visualize")

    page = window.visualization_widget
    assert window.shown == []
    assert window.right_panel.currentIndex() == Panel.VISUALIZATION
    assert "Measuring root length" in page.title_label.text()
    assert not page.action_button.isVisibleTo(page)

    busy["length"] = False
    window.jobs._name = None
    window._on_jobs_busy_changed(False)  # the run ended

    assert window.shown == ["length"]


def test_a_run_ending_elsewhere_does_not_open_a_dashboard(window, tmp_path):
    _open(window, _project(tmp_path / "p", lengths=True, skeletons=True))
    window._on_jobs_busy_changed(False)
    assert window.shown == []


# ====================================================================== stage_changed wiring
def test_every_stage_change_is_reported_to_the_dashboards(window, tmp_path, monkeypatch):
    from app.gui import visualization_manager

    seen = []
    monkeypatch.setattr(
        visualization_manager,
        "stage_changed",
        lambda mw, key: seen.append((mw is window, key)),
        raising=False,
    )
    _open(window, _project(tmp_path / "p", lengths=True, skeletons=True))
    seen.clear()

    for stage in ("Measure", "Visualize", "Library", "Correct", "Library"):
        _enter(window, stage)

    assert seen == [
        (True, "Measure"),
        (True, "Visualize"),
        (True, "Library"),
        (True, "Correct"),
        (True, "Library"),
    ]


def test_a_missing_stage_changed_hook_is_fine(window, tmp_path, monkeypatch):
    from app.gui import visualization_manager

    monkeypatch.delattr(visualization_manager, "stage_changed", raising=False)
    _open(window, _project(tmp_path / "p"))
    _enter(window, "Measure")
    _enter(window, "Library")
    assert window.ribbon_buttons["Library"].isChecked()
