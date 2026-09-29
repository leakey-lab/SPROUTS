"""Legacy show/hide helper for the mask tracing editor (AR-18).

Nothing in the app calls it any more (the stage ribbon switches panels through
``MainWindow.switch_right_panel``); ``MainWindow`` still constructs it as
``main_window.mask_handler``. It is kept only until that attribute and the
``tests/test_import_smoke.py`` entry are removed, then this module can go.
"""

from __future__ import annotations

from typing import Any

from app.gui.panels import Panel


class MaskHandler:
    """Toggle ``main_window.right_panel`` between the display and the mask editor."""

    def __init__(self, main_window: Any) -> None:
        self.main_window = main_window

    @property
    def mask_tracing_interface(self) -> Any:
        return self.main_window.mask_tracing_interface

    def show_mask_tracing_interface(self) -> None:
        self.main_window.right_panel.setCurrentWidget(self.mask_tracing_interface)

    def hide_mask_tracing_interface(self) -> None:
        # ``main_window.display_area`` is always None; the display is panel 0.
        self.main_window.right_panel.setCurrentIndex(Panel.DISPLAY)

    def toggle_mask_tracing_interface(self) -> None:
        if self.main_window.right_panel.currentWidget() is self.mask_tracing_interface:
            self.hide_mask_tracing_interface()
        else:
            self.show_mask_tracing_interface()
