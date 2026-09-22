"""Smoke tests for the read-only display metrics bar (Track M).

Instantiates ``MetricsBar`` under an offscreen QApplication and exercises
``set_metrics`` with real values and with ``None`` (em-dash fallback). Also
asserts the metrics bar mounts in the right panel without disturbing the
display-page stack / right-panel indices.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Allow the MainWindow mount test to construct the window offscreen (MainWindow
# transitively imports QtWebEngine via the visualization modules).
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui.metrics_bar import MetricsBar  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def test_metrics_bar_instantiates(app):
    bar = MetricsBar()
    # Fixed FOV cell is always populated.
    assert bar._status_value.text() == "—"
    assert bar.height() == MetricsBar.HEIGHT  # 26


def test_set_metrics_with_values(app):
    bar = MetricsBar()
    bar.set_metrics(length=12.5, area=6.7, status="Skeleton ready")
    assert bar._length_value.text() == "12.50 mm"
    assert bar._area_value.text() == "6.70 mm²"
    assert bar._status_value.text() == "Skeleton ready"


def test_set_metrics_with_none(app):
    bar = MetricsBar()
    bar.set_metrics(length=4.0, area=2.0, status="x")
    bar.set_metrics()  # all None -> all dashes
    assert bar._length_value.text() == "—"
    assert bar._area_value.text() == "—"
    assert bar._status_value.text() == "—"


def test_metrics_bar_mounts_without_disturbing_stack(app):
    """Track M3: the bar mounts under the display page and the QStackedWidget
    page indices (0 = display, 1 = empty state) and right-panel are unchanged."""
    from app.gui.main_window import MainWindow

    w = MainWindow()
    try:
        assert isinstance(w.metrics_bar, MetricsBar)
        # Stack indices unchanged: 2 pages, empty state still at index 1.
        assert w.display_page_stack.count() == 2
        assert w.display_page_stack.indexOf(w.empty_state) == 1
        # Bar is a child of the display page (stack index 0), not its own page.
        display_page = w.display_page_stack.widget(0)
        assert w.metrics_bar in display_page.findChildren(MetricsBar)
        # Right panel pages: display / mask tracing / visualization / skeleton
        # correction / GT tracing (the metrics bar adds none).
        assert w.right_panel.count() == 5
        # Read-only refresh runs without error.
        w._refresh_metrics_bar(None)
    finally:
        w.close()
