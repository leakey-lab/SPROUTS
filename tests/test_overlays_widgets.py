"""Overlay widgets: compact dock (UXE-09), content-sized Enhance popover (UI-07), the shared
ZoomCluster (UXE-05) and StatusChip (UXE-11)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from app.gui.widgets import (  # noqa: E402
    DockSlider,
    EnhancePopover,
    FloatingDock,
    IconButton,
)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def host(app):
    """A parent that outlives the test body (a temporary one would delete its children)."""
    widget = QWidget()
    yield widget
    widget.deleteLater()


def _mask_dock(host):
    """The Correct Mask dock: two sliders, three icon actions and the Save button."""
    dock = FloatingDock(host)
    sliders = [
        DockSlider("brush", "Brush size ([ and ])", 1, 100, 8, fmt="{} px"),
        DockSlider("eye", "Mask opacity", 0, 100, 60, fmt="{}%"),
    ]
    for slider in sliders:
        dock.add_widget(slider)
    dock.add_separator()
    for name in ("undo", "redo", "trash"):
        dock.add_widget(IconButton(name, name.title()))
    dock.add_separator()
    dock.add_widget(QPushButton("Save mask"))
    return dock, sliders


# --------------------------------------------------------------------------- #
#  Compact dock (UXE-09)
# --------------------------------------------------------------------------- #
def test_compact_slider_is_narrower_and_hides_its_readout(app):
    host = QWidget()
    slider = DockSlider("brush", "Brush size ([ and ])", 1, 100, 8, fmt="{} px", parent=host)
    full = slider.sizeHint().width()
    readout = max(44, slider.value_label.sizeHint().width())  # 44 px minimum readout

    slider.set_compact(True)
    assert slider.is_compact()
    assert slider.value_label.isHidden()
    # 110 -> 64 px slider (46 px), the readout and the 8 px spacing before it.
    assert slider.sizeHint().width() == full - (46 + 8 + readout)
    assert slider.compact_savings() == 46 + 8 + readout

    slider.set_compact(False)
    assert not slider.is_compact()
    assert not slider.value_label.isHidden()
    assert slider.sizeHint().width() == full


def test_compact_slider_keeps_tracking_the_value(app):
    slider = DockSlider("eye", "Mask opacity", 0, 100, 60, fmt="{}%")
    slider.set_compact(True)
    slider.slider.setValue(40)
    slider.set_compact(False)
    assert slider.value_label.text() == "40%"


def test_dock_predicts_its_width_in_both_modes(app):
    host = QWidget()
    dock, sliders = _mask_dock(host)
    full, compact = dock.natural_width(compact=False), dock.natural_width(compact=True)
    assert compact < full

    dock.set_compact(True)
    dock.adjustSize()
    assert dock.is_compact()
    assert all(s.is_compact() for s in sliders)
    assert dock.width() == compact
    assert dock.natural_width(compact=False) == full  # still predictable while compact

    dock.set_compact(False)
    dock.adjustSize()
    assert dock.width() == full


def test_hidden_dock_sliders_do_not_count_towards_compact_savings(app):
    """The skeleton eraser slider is hidden outside the eraser tool."""
    host = QWidget()
    dock, sliders = _mask_dock(host)
    sliders[0].setVisible(False)
    compact = dock.natural_width(compact=True)
    dock.set_compact(True)
    dock.adjustSize()
    assert dock.width() == compact


# --------------------------------------------------------------------------- #
#  Enhance popover sized from its content (UI-07)
# --------------------------------------------------------------------------- #
def _three_button_content():
    """The shape of NormalizationControls' button row that used to be clipped."""
    content = QWidget()
    row = QHBoxLayout(content)
    buttons = [
        QPushButton(t) for t in ("Apply Enhancement", "Save as Defaults", "Restore Defaults")
    ]
    for button in buttons:
        row.addWidget(button)
    return content, buttons


def test_popover_is_wide_enough_for_its_content(app):
    host = QWidget()
    host.resize(1200, 800)
    popover = EnhancePopover(host)
    content, buttons = _three_button_content()
    popover.set_content(content)
    host.show()
    try:
        QTest.qWaitForWindowExposed(host)
        popover.toggle()
        QApplication.processEvents()
        for button in buttons:
            assert button.width() >= button.sizeHint().width(), button.text()
        assert popover.geometry().right() < host.width()  # still inside the host
    finally:
        host.close()


def test_popover_keeps_its_default_width_for_narrow_content(app):
    host = QWidget()
    popover = EnhancePopover(host)
    popover.set_content(QPushButton("Apply"))
    popover.toggle()
    assert popover.width() == 264


def test_popover_reports_open_and_close(app):
    host = QWidget()
    popover = EnhancePopover(host)
    popover.set_content(QPushButton("Apply"))
    states = []
    popover.toggled.connect(states.append)
    popover.toggle()
    popover.toggle()
    assert states == [True, False]


# --------------------------------------------------------------------------- #
#  ZoomCluster (UXE-05): one zoom control shared by every canvas editor
# --------------------------------------------------------------------------- #
class _View(QObject):
    """The canvas-view zoom contract (CanvasViewMixin): records what the cluster asks."""

    zoom_changed = pyqtSignal(float)

    def __init__(self, zoom=1.0):
        super().__init__()
        self.zoom_factor = zoom
        self.calls = []

    def zoom_by(self, step):
        self.calls.append(("zoom_by", step))

    def zoom_step(self, direction):
        self.calls.append(("zoom_step", direction))

    def fit_to_view(self):
        self.calls.append(("fit_to_view",))

    def set_zoom(self, factor):
        self.calls.append(("set_zoom", factor))


def test_zoom_cluster_buttons_drive_the_bound_view(host):
    from app.gui.widgets import ZoomCluster

    cluster = ZoomCluster(host)
    view = _View()
    cluster.bind(view)
    cluster.zoom_in_button.click()
    cluster.zoom_out_button.click()
    cluster.fit_button.click()
    assert view.calls == [("zoom_step", 1), ("zoom_step", -1), ("fit_to_view",)]


def test_zoom_cluster_readout_follows_the_view(host):
    from app.gui.widgets import ZoomCluster

    cluster = ZoomCluster(host)
    view = _View(zoom=1.86)
    cluster.bind(view)
    assert cluster.zoom_pct_label.text() == "186%"  # current zoom shown on bind
    view.zoom_changed.emit(8.0)
    assert cluster.zoom_pct_label.text() == "800%"


def test_zoom_readout_toggles_between_actual_pixels_and_fit(host):
    from app.gui.widgets import ZoomCluster

    cluster = ZoomCluster(host)
    view = _View(zoom=0.43)
    cluster.bind(view)
    cluster.zoom_pct_label.click()
    assert view.calls[-1] == ("set_zoom", 1.0)
    view.zoom_changed.emit(1.0)
    cluster.zoom_pct_label.click()
    assert view.calls[-1] == ("fit_to_view",)


def test_rebinding_the_zoom_cluster_releases_the_previous_view(host):
    from app.gui.widgets import ZoomCluster

    cluster = ZoomCluster(host)
    first, second = _View(), _View(zoom=2.0)
    cluster.bind(first)
    cluster.bind(second)
    first.zoom_changed.emit(5.0)
    cluster.zoom_in_button.click()
    assert cluster.zoom_pct_label.text() == "200%"
    assert first.calls == [] and second.calls == [("zoom_step", 1)]


def test_zoom_cluster_fit_can_be_overridden_for_insets(host):
    """Editors pass a fit that honours OverlayLayout.insets(); both fit paths use it."""
    from app.gui.widgets import ZoomCluster

    cluster = ZoomCluster(host)
    view = _View(zoom=1.0)
    fits = []
    cluster.bind(view, fit=lambda: fits.append("fit"))
    cluster.fit_button.click()
    cluster.zoom_pct_label.click()  # at 100 %: the readout fits again
    assert fits == ["fit", "fit"]
    assert ("fit_to_view",) not in view.calls


def test_zoom_cluster_controls_have_accessible_names(host):
    from app.gui.widgets import ZoomCluster

    cluster = ZoomCluster(host)
    cluster.bind(_View(zoom=1.5))
    names = [
        w.accessibleName()
        for w in (
            cluster.zoom_out_button,
            cluster.zoom_pct_label,
            cluster.zoom_in_button,
            cluster.fit_button,
        )
    ]
    assert names == ["Zoom out", "Zoom 150%", "Zoom in", "Fit to window"]


# --------------------------------------------------------------------------- #
#  StatusChip (UXE-11): file name + save state (+ optional hint line)
# --------------------------------------------------------------------------- #
def test_status_chip_shows_the_file_and_its_state(host):
    from app.gui.widgets import StatusChip, tokens

    chip = StatusChip(host)
    chip.set_text("T001_L044.jpg")
    chip.set_state("dirty")
    assert chip.file_label.text() == "T001_L044.jpg"
    assert "Unsaved" in chip.state_label.text() and tokens.WARN in chip.state_label.text()
    assert chip.state() == "dirty"

    chip.set_state("clean")
    assert "Saved" in chip.state_label.text() and tokens.OK in chip.state_label.text()
    chip.set_state("clean", "Saved · 2/3 checked")
    assert "Saved · 2/3 checked" in chip.state_label.text()
    chip.set_state("error", "GT unreadable", tooltip="The GT file could not be used")
    assert tokens.DANGER in chip.state_label.text()
    assert chip.state_label.toolTip() == "The GT file could not be used"


def test_status_chip_none_state_shows_nothing_unless_labelled(host):
    from app.gui.widgets import StatusChip, tokens

    chip = StatusChip(host)
    chip.set_text("No image")
    chip.set_state("none")
    assert chip.state_label.isHidden()
    chip.set_state("none", "No mask")
    assert not chip.state_label.isHidden()
    assert "No mask" in chip.state_label.text() and tokens.TEXT_FAINT in chip.state_label.text()


def test_status_chip_rejects_unknown_states(host):
    from app.gui.widgets import StatusChip

    with pytest.raises(ValueError):
        StatusChip(host).set_state("modified")


def test_long_file_names_are_elided_in_the_middle(host):
    from app.gui.widgets import StatusChip

    # A camera name keeps its tube, L-number and date instead (test_correct_status_chip.py).
    name = "24EFCAM1_scan_001_SMM_" + "x" * 80 + ".jpg"
    chip = StatusChip(host, max_name_width=200)
    chip.set_text(name)
    shown = chip.file_label.text()
    assert "…" in shown and shown.startswith("24EF") and shown.endswith(".jpg")
    assert chip.file_label.toolTip() == name
    assert chip.text() == name


def test_status_chip_fits_the_width_it_is_given(host):
    from app.gui.widgets import StatusChip

    chip = StatusChip(host)
    chip.set_text("24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg")
    chip.set_state("clean")
    chip.adjustSize()
    natural = chip.width()

    chip.fit_to_width(natural - 80)
    chip.adjustSize()
    assert chip.width() <= natural - 80
    chip.fit_to_width(None)
    chip.adjustSize()
    assert chip.width() == natural


def test_status_chip_hint_line_wraps_and_belongs_to_the_chip(host):
    from app.gui.widgets import StatusChip

    chip = StatusChip(host, max_hint_width=300)
    assert chip.hint_label.isHidden()
    chip.set_hint("Polyline · click to place points · Ctrl+click a segment adds a bend " * 2)
    assert not chip.hint_label.isHidden()
    assert chip.hint_label.parent() is chip
    assert chip.hint_label.wordWrap() and chip.hint_label.width() <= 300
    chip.set_hint("")
    assert chip.hint_label.isHidden()


def test_status_chip_hint_dot_shows_the_layer_colour_before_the_text(host):
    from PyQt6.QtCore import Qt

    from app.gui.widgets import StatusChip, tokens

    chip = StatusChip(host)
    chip.set_hint("Mask · Brush", "Paint the mask", dot=tokens.MASK)
    chip.show()
    QApplication.processEvents()
    assert not chip.hint_dot.isHidden()
    assert chip.hint_dot.text() == "●"
    assert tokens.MASK in chip.hint_dot.styleSheet()
    assert chip.hint_dot.parent() is chip
    assert chip.hint_dot.geometry().right() < chip.hint_label.geometry().left()  # before it
    assert chip.hint_label.text() == "Mask · Brush"
    assert chip.hint_label.toolTip() == "Paint the mask"
    assert chip.hint_label.textFormat() == Qt.TextFormat.PlainText

    chip.set_hint("Skeleton · Polyline", dot=tokens.SKEL)
    assert tokens.SKEL in chip.hint_dot.styleSheet()
    assert tokens.MASK not in chip.hint_dot.styleSheet()
    chip.set_hint("Skeleton · Polyline", dot=None)
    assert chip.hint_dot.isHidden()


def test_status_chip_hint_without_a_dot_behaves_as_before(host):
    from PyQt6.QtCore import Qt

    from app.gui.widgets import StatusChip, tokens

    chip = StatusChip(host)
    chip.set_hint("<b>Brush</b> & more", "tip")
    assert chip.hint_dot.isHidden()
    assert chip.hint_label.text() == "<b>Brush</b> & more"  # plain text, never markup
    assert chip.hint_label.textFormat() == Qt.TextFormat.PlainText
    assert chip.hint_label.toolTip() == "tip"
    chip.set_hint("", dot=tokens.MASK)  # no hint: no dot either
    assert chip.hint_label.isHidden() and chip.hint_dot.isHidden()


def test_status_chip_dot_is_the_size_of_the_hint_text(host):
    from app.gui.widgets import StatusChip, tokens

    chip = StatusChip(host)
    chip.set_hint("Mask · Brush", dot=tokens.MASK)
    chip.ensurePolished()
    assert chip.hint_dot.font().pixelSize() == chip.hint_label.font().pixelSize() == 12


def test_status_chip_width_fit_accounts_for_the_dot(host):
    from app.gui.widgets import StatusChip, tokens

    hint = "Skeleton · Polyline · 3 points · drag points to adjust them"
    chip = StatusChip(host, max_hint_width=460)
    chip.set_text("T001_L044.jpg")
    chip.set_state("dirty")
    chip.set_hint(hint, dot=tokens.SKEL)
    chip.adjustSize()
    natural = chip.width()
    for budget in (natural - 60, 260):
        chip.fit_to_width(budget)
        chip.adjustSize()
        assert chip.width() <= budget
        assert chip.hint_label.wordWrap()
        assert chip.hint_dot.geometry().right() < chip.hint_label.geometry().left()
    chip.fit_to_width(None)
    chip.adjustSize()
    assert chip.width() == natural
