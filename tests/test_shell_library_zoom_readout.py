"""The Library says how far it is zoomed (W7).

The editors show their zoom in the zoom cluster; the Library display had no readout, so
Ctrl+1 / Ctrl+= / the wheel changed the view without saying to what. The metrics bar has a
ZOOM cell now (with the zoom keys in its tooltip), shown while an image is selected.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import canvas_gl  # noqa: E402
from app.gui.metrics_bar import MetricsBar  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402
from tests.test_correct_editor import qapp  # noqa: E402
from tests.test_correct_main_window import new_project, open_project, select, settle  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    from app.gui import image_manager

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: pytest.fail("dialog"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    yield
    flush_deleted()


def test_the_zoom_cell_formats_the_factor(app):
    bar = MetricsBar()
    bar.set_metrics(name="img", status="No mask")
    bar.set_zoom(1.0)
    assert not bar._zoom_cell.isHidden() and bar._zoom_value.text() == "100 %"
    bar.set_zoom(4.3467)
    assert bar._zoom_value.text() == "435 %"
    bar.set_zoom(0.084)
    assert bar._zoom_value.text() == "8.4 %"
    assert "Ctrl+0" in bar._zoom_cell.toolTip()
    bar.set_metrics()  # nothing selected: no zoom to report
    assert bar._zoom_cell.isHidden()
    bar.deleteLater()


def test_the_zoom_cell_never_widens_the_window_and_goes_first_when_narrow(app):
    """The window fits 900 px (UXS-04): the ZOOM cell adds nothing to the bar's minimum and
    hides while the other cells need its room."""
    bar = MetricsBar()
    bar.set_metrics(
        length=1234.56,
        area=987.65,
        status="Skeleton corrected",
        name="24EFCAM1_T001_L044_2024.07.18_111530_001_SMM",
    )
    bar.show()  # a hidden widget gets its resize events only when it is shown
    without = bar.minimumSizeHint().width()
    bar.resize(without + 400, bar.height())
    bar.set_zoom(4.35)
    assert not bar._zoom_cell.isHidden()
    assert bar.minimumSizeHint().width() == without

    bar.resize(without, bar.height())  # no room for it
    assert bar._zoom_cell.isHidden()
    bar.resize(without + 400, bar.height())
    assert not bar._zoom_cell.isHidden()
    bar.close()
    bar.deleteLater()


def test_the_library_zoom_is_on_the_metrics_bar(app, tmp_path):
    from app.gui.main_window import MainWindow

    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        window.resize(1280, 800)
        window.show()
        settle()
        open_project(window, new_project(tmp_path))
        select(window, 1)
        view = window.display_controller.magnifying_view
        bar = window.metrics_bar

        assert not bar._zoom_cell.isHidden()
        assert bar._zoom_value.text() == f"{view.zoom_factor * 100:.0f} %"
        view.set_zoom(1.0)
        assert bar._zoom_value.text() == "100 %"
    finally:
        window.close()
        window.deleteLater()
        flush_deleted()
