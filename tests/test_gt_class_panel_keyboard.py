"""The Trace GT class panel from the keyboard (walkthrough W18).

Tab never reached the panel: every part of it refused the focus, so the "checked" boxes
(the classes looked for on an image, which the dataset export filters on) could be ticked
only with the mouse. Now Tab reaches the gear and then each box that can change, in class
order (also after the classes change); the focused box draws the theme's focus ring and
Space ticks it, the focus staying there for the next box. A click still never takes the
focus (the canvas keeps Space for panning, spec 3.4), a click on a box hands the keys back
to the canvas, and a box that locks while it has the focus (its class got pixels) does too,
so the focus never moves on to the next box by itself.
"""

from __future__ import annotations

import os
import sys
import types
import weakref
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox, QWidget  # noqa: E402

from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

H, W = 60, 80
Focus = Qt.FocusPolicy
APP_QSS = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"
KEYS = ("root", "nodule", "root_tip")


@pytest.fixture(scope="module")
def app():
    """The app stylesheet: the focus ring under test is the theme's."""
    a = QApplication.instance() or QApplication([])
    previous = a.styleSheet()
    a.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield a
    a.setStyleSheet(previous)


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No modal prompt, no GL probing, no write to the real user config."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def iface(app, tmp_path):
    """A shown, active GT editor on an image, the keys on its canvas; deleted afterwards
    (with every dead wrapper: see tests/test_gt_tracing_interface.py)."""
    editor = GtTracingInterface()
    editor.resize(1100, 720)
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    editor.show()
    editor.activateWindow()
    assert QTest.qWaitForWindowActive(editor, 3000)
    editor.load_image(path)
    QApplication.processEvents()
    editor.graphics_view.setFocus()
    # A weak proxy: pytest keeps a fixture's value until after its teardown, and a held
    # wrapper would outlive the flush below.
    yield weakref.proxy(editor)
    editor.close()
    editor.deleteLater()
    del editor
    flush_deleted()


def _box(iface, key) -> QCheckBox:
    box = iface.class_panel._rows[key].checkbox
    assert isinstance(box, QCheckBox)
    return box


def tab_chain(iface, presses: int = 80) -> list[QWidget]:
    """The widgets Tab visits from the canvas, in order (until it is back on the canvas)."""
    seen = []
    for _ in range(presses):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        widget = QApplication.focusWidget()
        if widget is iface.graphics_view:
            break
        seen.append(widget)
    return seen


def _pixels(widget) -> np.ndarray:
    QApplication.processEvents()
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    width = image.width()
    return rows.reshape(image.height(), image.bytesPerLine())[:, : width * 4].reshape(
        image.height(), width, 4
    )


def test_tab_reaches_the_gear_then_each_box_in_class_order(iface):
    panel = iface.class_panel
    wanted = [panel._gear, *(_box(iface, key) for key in KEYS)]

    chain = tab_chain(iface)

    assert all(widget in chain for widget in wanted), chain
    start = chain.index(panel._gear)
    assert chain[start : start + len(wanted)] == wanted


def test_a_click_still_never_takes_the_focus(iface):
    panel = iface.class_panel
    for widget in (panel, *panel.findChildren(QWidget)):
        assert not widget.focusPolicy() & Focus.ClickFocus, widget
    assert panel._gear.focusPolicy() == Focus.TabFocus
    for key in KEYS:
        assert _box(iface, key).focusPolicy() == Focus.TabFocus
        assert panel._rows[key].focusPolicy() == Focus.NoFocus


def test_space_ticks_the_focused_box_and_the_focus_stays_for_the_next_one(iface):
    nodule, root_tip = _box(iface, "nodule"), _box(iface, "root_tip")
    nodule.setFocus(Qt.FocusReason.TabFocusReason)

    QTest.keyClick(nodule, Qt.Key.Key_Space)

    assert nodule.isChecked() and iface._reviewed == {"nodule"}
    assert iface.has_unsaved_changes()
    assert nodule.hasFocus()
    QTest.keyClick(nodule, Qt.Key.Key_Tab)
    assert root_tip.hasFocus()
    QTest.keyClick(root_tip, Qt.Key.Key_Space)
    assert iface._reviewed == {"nodule", "root_tip"}
    QTest.keyClick(root_tip, Qt.Key.Key_Space)
    assert iface._reviewed == {"nodule"} and not root_tip.isChecked()


def test_a_click_on_a_box_hands_the_keys_back_to_the_canvas(iface):
    view = iface.graphics_view
    _box(iface, "root_tip").setFocus(Qt.FocusReason.TabFocusReason)
    nodule = _box(iface, "nodule")

    QTest.mouseClick(nodule, Qt.MouseButton.LeftButton)

    assert nodule.isChecked() and iface._reviewed == {"nodule"}
    assert QApplication.focusWidget() is view
    QTest.keyPress(view, Qt.Key.Key_Space)  # Space pans; it never ticks a box
    assert view.space_pan
    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert iface._reviewed == {"nodule"} and not _box(iface, "root_tip").isChecked()


@pytest.mark.parametrize("ticked", [False, True], ids=["unchecked", "checked"])
def test_the_focused_box_draws_a_focus_ring(iface, ticked):
    box = _box(iface, "nodule")
    if ticked:
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)
        assert box.isChecked()
    iface.graphics_view.setFocus()
    before = _pixels(box)

    box.setFocus(Qt.FocusReason.TabFocusReason)
    assert box.hasFocus()
    after = _pixels(box)

    assert (before != after).any(axis=2).mean() > 0.1


def test_a_box_that_locks_while_it_has_the_focus_hands_the_keys_to_the_canvas(iface):
    iface.set_active_class("root")
    iface.begin_stroke(QPoint(20, 20))
    iface.end_stroke()
    root = _box(iface, "root")
    assert not root.isEnabled()  # the class has pixels: locked checked
    iface.undo()
    assert root.isEnabled() and not root.isChecked()
    root.setFocus(Qt.FocusReason.TabFocusReason)

    iface.redo()  # the pixels are back: the box locks under the focus

    assert not root.isEnabled() and root.isChecked()
    assert QApplication.focusWidget() is iface.graphics_view


def test_the_class_keys_still_work_from_a_focused_box(iface):
    box = _box(iface, "nodule")
    box.setFocus(Qt.FocusReason.TabFocusReason)

    QTest.keyClick(box, Qt.Key.Key_3)

    assert iface.class_panel.active_key() == "root_tip"
    assert not box.isChecked()


def _fake_class_editor(monkeypatch, result):
    """Replace ClassEditorDialog with a stub that accepts with ``result``."""

    class _FakeDialog:
        def __init__(self, registry, parent=None):
            pass

        def exec(self):
            return 1

        def result_registry(self):
            return result

        def deleteLater(self):  # noqa: N802 - Qt API
            pass

    fake = types.ModuleType("app.gui.class_editor_dialog")
    fake.ClassEditorDialog = _FakeDialog
    monkeypatch.setitem(sys.modules, "app.gui.class_editor_dialog", fake)


def test_the_boxes_follow_the_gear_after_the_classes_change(iface, monkeypatch):
    bigger = iface.registry.add(key="dead_root", name="Dead root", color="#7f5af0").add(
        key="hypha", name="Hypha", color="#3fa7ff"
    )
    _fake_class_editor(monkeypatch, bigger)
    iface.edit_classes()
    QApplication.processEvents()
    panel = iface.class_panel
    wanted = [panel._gear, *(_box(iface, c.key) for c in bigger.labelled())]
    iface.graphics_view.setFocus()

    chain = tab_chain(iface)

    start = chain.index(panel._gear)
    assert chain[start : start + len(wanted)] == wanted
