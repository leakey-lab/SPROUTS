"""MaskHandler (AR-18): hiding the tracing editor returns to the display panel.

It used ``main_window.display_area``, which is always None, so the toggle left the
right panel on a blank/None widget.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QStackedWidget

from app.gui.panels import Panel
from app.handlers.mask_handler import MaskHandler


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_toggle_mask_tracing_returns_to_the_display_panel(qapp):
    panel = QStackedWidget()
    display, tracing = QLabel("display"), QLabel("tracing")
    panel.addWidget(display)
    panel.addWidget(tracing)
    window = SimpleNamespace(right_panel=panel, mask_tracing_interface=tracing, display_area=None)
    handler = MaskHandler(window)

    handler.toggle_mask_tracing_interface()
    assert panel.currentIndex() == Panel.MASK_TRACING
    handler.toggle_mask_tracing_interface()
    assert panel.currentIndex() == Panel.DISPLAY
