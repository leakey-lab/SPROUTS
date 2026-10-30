"""Building the main window prints no Qt tab-order or font-size warning (X6)."""

import pytest
from PyQt6.QtCore import qInstallMessageHandler

from tests.qt_wait import settle
from tests.test_integration_main_window_wiring import (  # noqa: F401
    make_window,
    settings,
)

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


def test_building_the_main_window_logs_no_tab_order_or_font_warning(make_window):  # noqa: F811
    messages = []
    previous = qInstallMessageHandler(lambda _kind, _ctx, text: messages.append(text))
    try:
        window = make_window()
        window.show()
        settle()
    finally:
        qInstallMessageHandler(previous)
    bad = [m for m in messages if "setTabOrder" in m or "Point size <= 0" in m]
    assert not bad, bad
