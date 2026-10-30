"""The Library says how far it is zoomed (W7).

The editors show their zoom in the zoom cluster; the Library display had no readout, so
Ctrl+1 / Ctrl+= / the wheel changed the view without saying to what. The metrics bar has a
ZOOM cell now (with the zoom keys in its tooltip), shown while an image is selected.
"""

from __future__ import annotations

import pytest

from app.gui.key_text import platform_keys
from app.gui.metrics_bar import MetricsBar
from tests.qt_teardown import delete_widgets, flush_deleted
from tests.test_correct_main_window import new_project, open_project, select
from tests.window_support import build_window


@pytest.fixture(autouse=True)
def guards(window_guards):
    """The window guards, and the widgets a test deleteLater()s go before they are undone."""
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
    assert platform_keys("Ctrl+0") in bar._zoom_cell.toolTip()
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
    window = build_window(tmp_path / "s.ini")
    try:
        open_project(window, new_project(tmp_path))
        select(window, 1)
        view = window.display_controller.magnifying_view
        bar = window.metrics_bar

        assert not bar._zoom_cell.isHidden()
        assert bar._zoom_value.text() == f"{view.zoom_factor * 100:.0f} %"
        view.set_zoom(1.0)
        assert bar._zoom_value.text() == "100 %"
    finally:
        delete_widgets([window])
