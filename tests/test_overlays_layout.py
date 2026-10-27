"""OverlayLayout: places an editor's floating overlays together (UXE-09, UI-15, UI-07, UXE-08).

The host below carries the same overlays as the Correct Mask / Trace GT editors, built from
the real widgets: tool rail, dock (two sliders, three actions, Save), zoom cluster, status
chip, Enhance popover and the GT class panel.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from app.gt.registry import default_registry
from app.gui.class_panel import ClassPanel
from app.gui.widgets import (
    DockSlider,
    EnhancePopover,
    FloatingDock,
    IconButton,
    OverlayLayout,
    StatusChip,
    ToolRail,
    ZoomCluster,
)
from tests.qt_teardown import flush_deleted

LONG_NAME = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg"


class _Editor:
    """A shown canvas host with an editor's overlays, managed by an OverlayLayout."""

    def __init__(self, size=(1018, 662), *, side_panel=True):
        self.host = QWidget()
        self.host.resize(*size)
        self.rail = ToolRail(self.host)
        for name in ("brush", "erase", "fill", "hand", "eye", "contrast"):
            self.rail.add_widget(IconButton(name, name.title(), checkable=True))
        self.dock = FloatingDock(self.host)
        self.sliders = [
            DockSlider("brush", "Brush size ([ and ])", 1, 100, 8, fmt="{} px"),
            DockSlider("eye", "Mask opacity", 0, 100, 60, fmt="{}%"),
        ]
        for slider in self.sliders:
            self.dock.add_widget(slider)
        self.dock.add_separator()
        for name in ("undo", "redo", "trash"):
            self.dock.add_widget(IconButton(name, name.title()))
        self.dock.add_separator()
        self.save = QPushButton("Save mask")
        self.dock.add_widget(self.save)
        self.zoom = ZoomCluster(self.host)
        self.chip = StatusChip(self.host)
        self.chip.set_text(LONG_NAME)
        self.chip.set_state("clean", "Saved · 1/3 checked")
        self.popover = EnhancePopover(self.host)
        content = QFrame()
        content.setMinimumSize(252, 245)  # the 264x257 Enhance popover of the audit
        self.popover.set_content(content)
        self.panel = None
        if side_panel:
            self.panel = ClassPanel(self.host)
            self.panel.set_registry(default_registry())
        self.host.show()
        QTest.qWaitForWindowExposed(self.host)
        self.layout = OverlayLayout(self.host)
        self.layout.register(
            rail=self.rail,
            dock=self.dock,
            zoom_cluster=self.zoom,
            top_chip=self.chip,
            popover=self.popover,
            side_panel=self.panel,
        )

    def overlays(self):
        widgets = [self.rail, self.dock, self.zoom, self.chip, self.popover, self.panel]
        return [w for w in widgets if w is not None and w.isVisible()]

    def collisions(self):
        items = self.overlays()
        return [
            (type(a).__name__, type(b).__name__)
            for i, a in enumerate(items)
            for b in items[i + 1 :]
            if a.geometry().intersects(b.geometry())
        ]

    def outside(self):
        return [
            type(w).__name__ for w in self.overlays() if not self.host.rect().contains(w.geometry())
        ]

    def resize(self, width, height):
        self.host.resize(width, height)
        QApplication.processEvents()

    def close(self):
        self.host.close()
        self.host.deleteLater()


@pytest.fixture
def editor(app):
    ed = _Editor()
    yield ed
    ed.close()
    del ed
    flush_deleted()


def test_register_rejects_unknown_roles(editor):
    with pytest.raises(ValueError):
        editor.layout.register(toolbar=QWidget())


@pytest.mark.parametrize("popover_open", [False, True])
def test_overlays_stay_apart_and_inside_the_canvas_while_it_resizes(editor, popover_open):
    if popover_open:
        editor.popover.toggle()
    # Narrowest canvas the compact dock fits in, whatever the platform fonts measure.
    narrowest = editor.dock.natural_width(compact=True) + 2 * 14
    failures = []
    for width in range(max(640, narrowest), 1401, 80):
        for height in range(520, 901, 80):
            editor.resize(width, height)
            if editor.collisions() or editor.outside():
                failures.append(((width, height), editor.collisions(), editor.outside()))
    assert not failures, failures[:4]


def test_zoom_cluster_no_longer_covers_save_at_the_minimum_window(editor):
    """UI-15: the Correct Mask canvas is 907x562 at the 1169x700 minimum window."""
    editor.resize(907, 562)
    save = QRect(editor.save.mapTo(editor.host, editor.save.rect().topLeft()), editor.save.size())
    assert not save.intersects(editor.zoom.geometry())
    assert not editor.dock.geometry().intersects(editor.zoom.geometry())


def test_open_popover_pushes_the_class_panel_below_it(editor):
    """UI-07 / UXE-09: at 1280x800 the popover used to hide the class panel's gear."""
    centred = editor.panel.geometry()
    editor.popover.toggle()
    assert editor.popover.isVisible()
    assert not editor.popover.geometry().intersects(editor.panel.geometry())
    editor.popover.toggle()
    assert editor.panel.geometry() == centred


def test_dock_turns_compact_on_a_narrow_canvas_and_recovers(editor):
    editor.resize(editor.dock.natural_width(compact=False) + 20, 700)
    assert editor.dock.is_compact()
    assert all(slider.value_label.isHidden() for slider in editor.sliders)
    editor.resize(1400, 800)
    assert not editor.dock.is_compact()
    assert not any(slider.value_label.isHidden() for slider in editor.sliders)


def test_panel_reposition_goes_through_the_layout(editor):
    """Existing ``overlay.reposition()`` calls must not undo the collision-free layout."""
    editor.resize(907, 562)
    placed = editor.dock.geometry()
    editor.dock.reposition()
    editor.chip.reposition()
    assert editor.dock.geometry() == placed
    assert not editor.dock.geometry().intersects(editor.zoom.geometry())


def test_status_chip_changes_are_laid_out_again(editor):
    editor.resize(760, 640)
    editor.popover.toggle()
    editor.chip.set_text("x" * 200 + ".jpg")
    assert not editor.chip.geometry().intersects(editor.popover.geometry())


def test_showing_a_registered_prompt_moves_the_chip_clear_of_it(editor):
    prompt = QFrame(editor.host)
    row = QHBoxLayout(prompt)
    row.addWidget(QPushButton("Finish line (Enter)"))
    row.addWidget(QPushButton("Cancel line (Esc)"))
    prompt.hide()
    editor.layout.register(prompt=prompt)
    editor.resize(760, 640)
    prompt.show()
    QApplication.processEvents()
    assert not prompt.geometry().intersects(editor.chip.geometry())
    assert editor.host.rect().contains(prompt.geometry())


def test_insets_keep_a_fitted_image_clear_of_the_fixed_overlays(editor):
    """UXE-08: fit-to-view inside these margins never runs under the rail, dock, chip or
    class panel."""
    editor.resize(1018, 662)
    m = editor.layout.insets()
    clear = editor.host.rect().adjusted(m.left(), m.top(), -m.right(), -m.bottom())
    assert clear.width() > 0 and clear.height() > 0
    for overlay in (editor.rail, editor.dock, editor.chip, editor.panel):
        assert not clear.intersects(overlay.geometry()), type(overlay).__name__


def test_insets_follow_the_current_canvas_size(editor):
    """The view asks for insets while resizing: they must match the new size at once."""
    editor.host.resize(900, 600)
    assert editor.layout.insets().bottom() == 600 - editor.dock.geometry().y() + 8


def test_asking_for_the_insets_leaves_a_chip_below_the_prompt_as_placed(app):
    """Every zoom and fit asks for the insets. A chip too wide to sit beside the polyline
    prompt drops below it at its natural size; measuring it for the row beside the prompt
    used to leave it narrowed there (name elided, hint wrapped) until the next relayout."""
    host = QWidget()
    host.resize(900, 600)
    chip = StatusChip(host)
    chip.set_text(LONG_NAME)
    chip.set_hint("Skeleton · Polyline · 1 point · click to add more")
    prompt = QFrame(host)
    prompt.setFixedSize(658, 44)
    layout = OverlayLayout(host)
    host.show()
    QTest.qWaitForWindowExposed(host)
    layout.register(top_chip=chip, prompt=prompt)
    QApplication.processEvents()
    try:
        assert chip.geometry().top() > prompt.geometry().bottom()  # the two-row case
        placed = (chip.geometry(), chip.file_label.text(), chip.hint_label.text())

        margins = layout.insets()

        assert (chip.geometry(), chip.file_label.text(), chip.hint_label.text()) == placed
        assert margins.top() == chip.geometry().y() + chip.geometry().height() + 8
    finally:
        host.close()
        host.deleteLater()
        flush_deleted()


def test_layout_settles_after_a_change(editor, monkeypatch):
    """No relayout ping-pong: a change costs a handful of passes, then the layout is idle."""
    calls = []
    original = editor.layout.relayout
    monkeypatch.setattr(editor.layout, "relayout", lambda: (calls.append(1), original())[1])
    editor.resize(700, 600)  # compact dock, lifted zoom cluster, fitted chip
    editor.popover.toggle()
    QTest.qWait(100)
    settled = len(calls)
    QTest.qWait(200)
    assert len(calls) == settled
    assert settled < 10
