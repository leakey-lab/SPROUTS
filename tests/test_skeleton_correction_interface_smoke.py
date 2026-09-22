"""Smoke test for the modern (PR6) SkeletonCorrectionInterface overlay layout.

Instantiates the interface under an offscreen QApplication and asserts that the
floating overlays exist and that the underlying control widgets/attrs survived
the reparenting into the ToolRail / FloatingDock / EnhancePopover / polyline
prompt, plus that key signal paths (undo/redo shortcuts, programmatic tool
selection) still function.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.skeleton_correction_interface import SkeletonCorrectionInterface  # noqa: E402
from app.gui.widgets import EnhancePopover, FloatingDock, ToolRail  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def test_overlays_present(app):
    iface = SkeletonCorrectionInterface()

    # Floating overlays exist and are the expected types.
    assert isinstance(iface.tool_rail, ToolRail)
    assert isinstance(iface.dock, FloatingDock)
    assert isinstance(iface.enhance_popover, EnhancePopover)
    assert iface.polyline_prompt is not None

    # Tool buttons preserved and still in the exclusive group.
    for btn in (
        iface.select_button,
        iface.eraser_button,
        iface.polyline_button,
        iface.connect_button,
    ):
        assert btn is not None
        assert btn in iface.tool_group.buttons()

    # Toggles / actions / sliders / polyline-prompt buttons survived.
    assert iface.mode_toggle is not None
    assert iface.smooth_polyline_toggle is not None
    assert iface.save_skeleton_button is not None
    assert iface.load_skeleton_button is not None
    assert iface.undo_button is not None
    assert iface.redo_button is not None
    assert iface.clear_button is not None
    assert iface.eraser_slider is not None
    assert iface.opacity_slider is not None
    assert iface.finish_polyline_button is not None
    assert iface.cancel_polyline_button is not None
    assert iface.status_label is not None
    assert iface.norm_controls is not None
    assert iface.enhance_button is not None

    # Default tool is Select; eraser-only slider hidden initially.
    # isHidden() tracks explicit show/hide even when the host isn't on-screen.
    assert iface.select_button.isChecked()
    assert iface.eraser_container.isHidden()


def test_tool_selection_and_shortcuts(app):
    iface = SkeletonCorrectionInterface()

    # Programmatic polyline tool selection drives _on_tool_changed.
    iface.polyline_button.setChecked(True)
    iface._on_tool_changed(iface.polyline_button)
    assert iface.current_tool == iface.TOOL_POLYLINE
    assert iface.smooth_polyline_toggle.isEnabled()

    # Eraser tool makes the eraser slider container visible.
    iface.eraser_button.setChecked(True)
    iface._on_tool_changed(iface.eraser_button)
    assert iface.current_tool == iface.TOOL_ERASER
    assert not iface.eraser_container.isHidden()

    # Undo/redo shortcuts are wired and fire without error (no skeleton loaded).
    iface.undo_shortcut.activated.emit()
    iface.redo_shortcut.activated.emit()

    # Resize triggers overlay reposition without error.
    iface.resize(900, 700)


def test_hint_lives_outside_dock_and_clears_polyline_prompt(app):
    """The tool hint sits in its own top-left chip, not the bottom dock.

    Regression: the multi-line hint used to be packed into the FloatingDock,
    where the dock height clipped it.
    """
    from PyQt6.QtCore import QPoint

    from app.gui.widgets import CornerPanel, DockSlider

    iface = SkeletonCorrectionInterface()
    iface.resize(820, 500)
    iface.show()

    assert isinstance(iface.hint_panel, CornerPanel)
    assert iface.status_label.parent() is iface.hint_panel
    assert not iface.dock.isAncestorOf(iface.status_label)

    # Dock sliders are DockSliders whose readout tracks the value.
    assert isinstance(iface.opacity_container, DockSlider)
    assert isinstance(iface.eraser_container, DockSlider)
    iface.opacity_slider.setValue(40)
    assert iface.opacity_label.text() == "40%"
    assert iface.skeleton_item.opacity() == 0.4

    # Long polyline hint with the Finish/Cancel prompt showing: the hint chip
    # must never overlap the prompt (beside it, or below it when narrow).
    iface.model.set_empty((640, 480))
    iface.polyline_button.setChecked(True)
    iface._on_tool_changed(iface.polyline_button)
    iface._polyline_smooth = True
    iface._polyline_points[:] = [QPoint(10, 10), QPoint(50, 60)]
    iface._update_polyline_buttons_enabled()
    iface._reposition_overlays()

    assert not iface.polyline_prompt.isHidden()
    for width in (1600, 1100, 820, 600):
        iface.resize(width, 500)
        app.processEvents()
        iface._reposition_overlays()
        hint = iface.hint_panel.geometry()
        prompt = iface.polyline_prompt.geometry()
        assert not hint.intersects(prompt), (width, hint, prompt)
    iface.close()
