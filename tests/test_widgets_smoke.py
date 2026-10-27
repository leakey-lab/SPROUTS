"""Smoke tests for the SPROUTS design-system widgets package.

Instantiates every widget + the icon loader under an offscreen QApplication so
regressions in the presentation layer surface in CI without a display.
"""

from PyQt6.QtWidgets import QLabel, QWidget

from app.gui import widgets
from app.gui.widgets import tokens


def test_tokens_rgba():
    assert tokens.rgba("#c39af6", 0.14) == "rgba(195, 154, 246, 0.14)"
    assert tokens.ACCENT == "#c39af6"
    assert tokens.MASK == "#5fd6a0"
    assert tokens.SKEL == "#f0a868"


def test_icons_present(app):
    for name in (
        "brush",
        "erase",
        "fill",
        "save",
        "trash",
        "polyline",
        "link",
        "cursor",
        "contrast",
        "sprouts_logo",
    ):
        assert widgets.has_icon(name), f"missing icon {name}"
        assert not widgets.load_icon(name, tokens.ACCENT, 18).isNull()
        assert not widgets.load_pixmap(name, tokens.MASK, 20).isNull()


def test_segmented_control(app):
    seg = widgets.SegmentedControl(
        [
            ("single", "Single", "single"),
            ("overlay", "Overlay", "overlay"),
            ("split", "Side by side", "split"),
        ],
        value="overlay",
    )
    assert seg.value() == "overlay"
    got = []
    seg.valueChanged.connect(got.append)
    seg.setValue("split")
    assert seg.value() == "split"
    assert got == ["split"]


def test_icon_button(app):
    btn = widgets.IconButton("brush", "Brush", checkable=True)
    assert not btn.isChecked()
    btn.setChecked(True)
    assert btn.isChecked()


def test_overlays(app):
    host = QWidget()
    host.resize(800, 600)
    rail = widgets.ToolRail(host)
    rail.add_widget(widgets.IconButton("brush", checkable=True))
    rail.add_separator()
    rail.add_widget(widgets.IconButton("erase", checkable=True))
    rail.reposition()

    dock = widgets.FloatingDock(host)
    dock.add_widget(QLabel("Size"))
    dock.reposition()

    pop = widgets.EnhancePopover(host)
    pop.set_content(QLabel("Enhance"))
    pop.toggle()
    assert not pop.isHidden()
    pop.toggle()
    assert pop.isHidden()

    prog = widgets.ProgressOverlay(host)
    prog.start("Generating masks…")
    prog.set_progress(42)
    prog.finish()

    toast = widgets.Toast(host, "Mask saved", kind="success")
    toast.show_toast()
    toast.dismiss()


def test_toast_manager(app):
    host = QWidget()
    host.resize(800, 600)
    mgr = widgets.ToastManager(host)
    mgr.show("Loaded 42 images", kind="success")
    mgr.show("GPU unavailable — using CPU", kind="warn")
    mgr.reposition()
