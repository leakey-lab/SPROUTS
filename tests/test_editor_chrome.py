"""EditorChrome (C10): the floating overlays every canvas editor shares (rail, dock, Enhance
popover, status chip, zoom cluster, Pan and Enhance buttons), placed by one OverlayLayout
over the editor widget, with fit insets, focus return and click-without-focus wired once.
"""

import gc
import os
import sys
import traceback

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.enhancement import EnhanceDefaultsStore  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402
from app.gui.editor_chrome import EditorChrome  # noqa: E402
from app.gui.image_normalization_interface import NormalizationControls  # noqa: E402
from app.gui.widgets import (  # noqa: E402
    DockSlider,
    EnhancePopover,
    FloatingDock,
    IconButton,
    OverlayLayout,
    StatusChip,
    ToolRail,
    ZoomCluster,
    tokens,
)
from app.gui.widgets.buttons import primary_button  # noqa: E402
from app.gui.widgets.overlay_geometry import LayoutMetrics  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

M = LayoutMetrics()
UNDER_TEST = {"editor_chrome.py", "editor_canvas.py", "test_editor_chrome.py"}
# A camera photo's name and a polyline hint: a chip wide enough to drop below a wide prompt.
PHOTO_NAME = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg"
POLYLINE_HINT = "Skeleton · Polyline · 1 point · click to add more"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No prompt, no GL, no write to the real user config, and no exception of the code under
    test hidden in a slot (PyQt hands those to sys.excepthook). Another module's leftover
    widget (e.g. a queued repaint) only gets its traceback printed."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    errors = []

    def hook(kind, value, tb):
        files = {os.path.basename(frame.filename) for frame in traceback.extract_tb(tb)}
        if files & UNDER_TEST:
            errors.append(value)
        else:
            sys.__excepthook__(kind, value, tb)

    monkeypatch.setattr(sys, "excepthook", hook)
    yield
    assert errors == []


class Page(QWidget):
    """A minimal editor page: the canvas fills it and the chrome floats over it."""

    def __init__(self, store: EnhanceDefaultsStore):
        super().__init__()
        self.view = EditorCanvasView()
        scene = QGraphicsScene(self.view)
        pixmap = QPixmap(640, 480)
        pixmap.fill(QColor(tokens.BG_2))
        scene.addItem(QGraphicsPixmapItem(pixmap))
        self.view.setScene(scene)
        self.view.set_content_rect(QRectF(0, 0, 640, 480))
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.view)
        self.norm = NormalizationControls(store=store)
        self.chrome = EditorChrome(self, self.view, self.norm)


class SidePanel(QFrame):
    """A stand-in for the Layers / class panel (OverlayLayout's side_panel hooks)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(155, 200)

    def preferred_height(self, host_height: int) -> int:
        return 200

    def fit_height(self, height: int) -> None:
        self.setFixedHeight(max(0, height))


@pytest.fixture
def make_page(app, tmp_path):
    made = []

    def make(size=(1000, 700), *, show=True):
        store = EnhanceDefaultsStore(
            user_path=str(tmp_path / "user.json"), factory_path=str(tmp_path / "factory.json")
        )
        page = Page(store)
        page.resize(*size)
        if show:
            page.show()
            settle(app)
        made.append(page)
        return page

    yield make
    # Delete the pages now, while their Python wrappers are intact. Left to the cyclic GC
    # (page -> chrome -> OverlayLayout -> page), the layout's event filter can see events
    # during the C++ teardown after the collector has already emptied its __dict__.
    delete_widgets(made)


def settle(app, page=None) -> None:
    for _ in range(3):
        app.processEvents()
    if page is not None:
        page.chrome.relayout()


def show_active(app, page) -> None:
    page.activateWindow()
    assert QTest.qWaitForWindowActive(page, 3000)
    settle(app)


def right(widget) -> int:
    geo = widget.geometry()
    return geo.x() + geo.width()


def bottom(widget) -> int:
    geo = widget.geometry()
    return geo.y() + geo.height()


def record_fit_insets(page) -> list:
    """From now on, every insets answer the canvas's fits get from the chrome's layout."""
    seen = []
    layout = page.chrome.layout

    def insets():
        seen.append(layout.insets())
        return seen[-1]

    page.view.set_fit_insets_provider(insets)
    return seen


# --------------------------------------------------------------------------- building
def test_the_chrome_builds_its_overlays_on_the_host(make_page):
    page = make_page(show=False)
    chrome = page.chrome
    kinds = [
        (chrome.rail, ToolRail),
        (chrome.dock, FloatingDock),
        (chrome.popover, EnhancePopover),
        (chrome.chip, StatusChip),
        (chrome.zoom, ZoomCluster),
    ]
    for widget, kind in kinds:
        assert isinstance(widget, kind) and widget.parent() is page
    assert isinstance(chrome.layout, OverlayLayout)
    assert page.norm.parent() is chrome.popover  # the popover's content
    assert chrome.popover.isHidden()
    for button, icon_tip in (
        (chrome.pan_button, "Pan (hold Space, or middle-drag)"),
        (chrome.enhance_button, "Image enhancement"),
    ):
        assert isinstance(button, IconButton) and button.isCheckable()
        assert button.toolTip() == icon_tip
        assert button not in chrome.rail.findChildren(IconButton)  # the editor places them
    assert chrome.rail.findChildren(QAbstractButton) == []
    assert chrome.dock.findChildren(QAbstractButton) == []


def test_the_five_overlays_follow_the_host_through_one_layout(app, make_page):
    page = make_page((1000, 700))
    chrome = page.chrome
    page.resize(1400, 900)
    settle(app)
    assert chrome.rail.x() == M.margin
    assert (chrome.chip.x(), chrome.chip.y()) == (M.margin, M.margin)
    assert right(chrome.zoom) == 1400 - M.margin and bottom(chrome.zoom) == 900 - M.bottom_margin
    assert bottom(chrome.dock) == 900 - M.bottom_margin
    chrome.enhance_button.click()
    settle(app)
    assert right(chrome.popover) == 1400 - M.margin and chrome.popover.y() == M.margin


def test_register_adds_the_side_panel_and_prompt_roles(app, make_page):
    page = make_page((1400, 900))
    chrome = page.chrome
    assert chrome.layout.insets().right() == M.default_inset
    panel = SidePanel(page)
    panel.show()  # created after the page was shown
    chrome.register(side_panel=panel)
    settle(app, page)
    assert right(panel) == 1400 - M.margin
    assert chrome.layout.insets().right() == M.margin + panel.width() + M.fit_padding
    prompt = QFrame(page)
    prompt.setFixedSize(320, 44)
    prompt.show()
    chrome.register(prompt=prompt)
    settle(app, page)
    assert prompt.y() == M.margin and abs(prompt.x() + 160 - 700) <= 1


def test_fit_keeps_clear_of_the_overlays(app, make_page):
    page = make_page((1000, 700))
    insets = page.chrome.layout.insets()
    assert page.view.fit_insets() == insets
    assert insets.left() > M.default_inset  # the rail
    page.view.fit_to_view()
    shown = page.view.mapFromScene(QRectF(0, 0, 640, 480)).boundingRect()
    assert shown.left() >= insets.left() - 1


def test_zooms_and_fits_leave_a_chip_below_the_prompt_as_placed(app, make_page):
    """Every zoom and fit asks the layout for the insets. A chip moved below the polyline
    prompt (no room beside it) used to be left narrowed to that room: name elided, hint
    wrapped over the rail."""
    page = make_page((900, 600))
    chrome, view = page.chrome, page.view
    chrome.chip.set_text(PHOTO_NAME)
    chrome.chip.set_hint(POLYLINE_HINT)
    prompt = QFrame(page)
    prompt.setFixedSize(658, 44)
    prompt.show()
    chrome.register(prompt=prompt)
    settle(app, page)
    chip = chrome.chip
    assert chip.y() > bottom(prompt)  # the chip sits below the prompt
    placed = (chip.geometry(), chip.file_label.text(), chip.hint_label.text())

    for action in (
        lambda: view.zoom_step(-1),
        lambda: view.zoom_step(+1),
        lambda: view.set_zoom(2.0),
        view.fit_to_view,
    ):
        action()
        assert (chip.geometry(), chip.file_label.text(), chip.hint_label.text()) == placed


def test_the_resize_refit_gets_the_insets_of_the_new_size(app, make_page):
    """A fitted canvas re-fits from its own resize event, which the page's layout sends
    before the overlay layout sees the page's resize. The fit must still get the insets of
    the new size, where the chip drops below the prompt, not those of the last plan."""
    page = make_page((1400, 700))
    chrome = page.chrome
    chrome.chip.set_text(PHOTO_NAME)
    chrome.chip.set_hint(POLYLINE_HINT)
    prompt = QFrame(page)
    prompt.setFixedSize(658, 44)
    prompt.show()
    chrome.register(prompt=prompt)
    settle(app, page)
    assert chrome.chip.y() == M.margin  # beside the prompt
    before = chrome.layout.insets()
    page.view.fit_to_view()
    seen = record_fit_insets(page)

    page.resize(900, 700)
    settle(app, page)

    assert chrome.chip.y() > bottom(prompt)  # below it now
    assert seen and seen[0] == chrome.layout.insets()
    assert seen[0].top() > before.top()


def test_a_fit_while_a_relayout_is_pending_counts_the_shown_overlay(app, make_page):
    """The prompt shown and a fit in the same call, with the relayout still queued: the fit
    already keeps clear of the chip the prompt pushes below it."""
    page = make_page((1400, 700))
    chrome = page.chrome
    chrome.chip.set_text(PHOTO_NAME)
    chrome.chip.set_hint(POLYLINE_HINT)
    prompt = QFrame(page)
    prompt.setFixedSize(1100, 44)
    prompt.hide()
    chrome.register(prompt=prompt)
    settle(app, page)
    assert chrome.chip.y() == M.margin
    seen = record_fit_insets(page)

    prompt.show()
    page.view.fit_to_view()
    settle(app, page)

    assert chrome.chip.y() > bottom(prompt)
    assert seen and seen[0].top() == bottom(chrome.chip) + M.fit_padding
    assert seen[0] == chrome.layout.insets()


# --------------------------------------------------------------------------- behaviour
def test_the_enhance_button_opens_the_popover_and_follows_it(app, make_page):
    page = make_page()
    chrome = page.chrome
    chrome.enhance_button.click()
    assert not chrome.popover.isHidden() and chrome.enhance_button.isChecked()
    chrome.popover.toggle()  # e.g. Esc inside the popover
    assert chrome.popover.isHidden() and not chrome.enhance_button.isChecked()
    chrome.popover.toggle()
    assert chrome.enhance_button.isChecked()
    chrome.enhance_button.click()
    assert chrome.popover.isHidden() and not chrome.enhance_button.isChecked()


def test_closing_the_popover_gives_the_keys_back_to_the_canvas(app, make_page):
    page = make_page()
    show_active(app, page)
    chrome = page.chrome
    chrome.enhance_button.click()
    page.norm.method_combo.setFocus()
    settle(app)
    assert QApplication.focusWidget() is page.norm.method_combo
    QTest.keyClick(chrome.popover, Qt.Key.Key_Escape)
    settle(app)
    assert chrome.popover.isHidden()
    assert QApplication.focusWidget() is page.view


def test_the_wiring_outlives_the_chrome_object(app, make_page):
    """An editor may keep only the parts; the connections must not depend on the chrome."""
    page = make_page()
    popover, enhance = page.chrome.popover, page.chrome.enhance_button
    page.chrome = None
    gc.collect()
    show_active(app, page)
    enhance.click()
    page.norm.method_combo.setFocus()
    enhance.click()
    settle(app)
    assert popover.isHidden()
    assert QApplication.focusWidget() is page.view


def test_clicks_on_rail_dock_and_popover_buttons_leave_the_focus_on_the_canvas(app, make_page):
    page = make_page()
    chrome = page.chrome
    chrome.rail.add_widget(chrome.pan_button)
    chrome.rail.add_widget(chrome.enhance_button)
    save = primary_button("Save", "save")
    chrome.dock.add_widget(save)
    show_active(app, page)
    page.view.setFocus()
    QTest.mouseClick(chrome.pan_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(save, Qt.MouseButton.LeftButton)
    QTest.mouseClick(chrome.enhance_button, Qt.MouseButton.LeftButton)
    settle(app)
    QTest.mouseClick(page.norm.original_check, Qt.MouseButton.LeftButton)
    QTest.mouseClick(page.norm.apply_button, Qt.MouseButton.LeftButton)
    assert QApplication.focusWidget() is page.view
    assert page.norm.is_active()  # the clicks did land


def test_the_pan_button_toggles_the_canvas_pan_mode(make_page):
    page = make_page()
    page.chrome.pan_button.click()
    assert page.view.pan_mode
    page.chrome.pan_button.click()
    assert not page.view.pan_mode


def test_the_zoom_cluster_drives_and_follows_the_canvas(app, make_page):
    page = make_page()
    chrome = page.chrome
    page.view.set_zoom(2.0)
    assert chrome.zoom.zoom_pct_label.text() == "200%"
    chrome.zoom.fit_button.click()
    settle(app)
    assert page.view.zoom_factor < 2.0
    assert chrome.zoom.zoom_pct_label.text() == f"{round(page.view.zoom_factor * 100)}%"


# --------------------------------------------------------------------------- layout
@pytest.mark.parametrize("size", [(1400, 900), (700, 520)])
def test_the_overlays_never_overlap(app, make_page, size):
    page = make_page(size)
    chrome = page.chrome
    for i in range(7):
        chrome.rail.add_widget(IconButton("brush", f"Tool {i}", checkable=True))
        if i in (2, 6):
            chrome.rail.add_separator()
    chrome.rail.add_widget(chrome.pan_button)
    chrome.rail.add_separator()
    chrome.rail.add_widget(chrome.enhance_button)
    chrome.dock.add_widget(DockSlider("brush", "Brush size", 1, 100, 8, fmt="{} px"))
    chrome.dock.add_widget(DockSlider("eye", "Opacity", 0, 100, 60, fmt="{}%"))
    chrome.dock.add_separator()
    for icon in ("load", "undo", "redo", "trash"):
        chrome.dock.add_widget(IconButton(icon, icon))
    chrome.dock.add_separator()
    chrome.dock.add_widget(primary_button("Save", "save", compact=True))
    chrome.chip.set_text("24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg")
    chrome.chip.set_state("dirty")
    chrome.chip.set_hint("Skeleton · Polyline · 3 points · drag points to adjust")
    panel, prompt = SidePanel(page), QFrame(page)
    prompt.setFixedSize(330, 44)
    panel.show()  # created after the page was shown
    prompt.show()
    chrome.register(side_panel=panel, prompt=prompt)
    chrome.enhance_button.click()
    settle(app, page)
    overlays = [chrome.rail, chrome.dock, chrome.chip, chrome.zoom, chrome.popover, panel, prompt]
    shown = [w for w in overlays if w.isVisible()]
    assert len(shown) == len(overlays)
    for i, a in enumerate(shown):
        assert page.rect().contains(a.geometry()), (a, a.geometry())
        for b in shown[i + 1 :]:
            assert not a.geometry().intersects(b.geometry()), (a, b, a.geometry(), b.geometry())
