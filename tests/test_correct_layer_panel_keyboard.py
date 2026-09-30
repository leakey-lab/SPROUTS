"""The Layers panel from the keyboard (walkthrough W18).

Tab used to skip the panel: its rows and eyes refused the focus entirely, so the layers could
be picked and shown or hidden only with the mouse or the page keys. Now Tab reaches each row
and its eye (a click still never takes the focus: the canvas keeps its hold keys, Space and
B), the focused one draws a focus ring, Space or Enter on a row edits its layer, Space on an
eye shows or hides it, and the page's keys keep working from there.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    activate,
    editor_factory,
    install_guards,
    new_project,
    qapp,
    settle,
)

Focus = Qt.FocusPolicy


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


@pytest.fixture
def page(make_editor, tmp_path):
    """The page on an image, active, the keys on its canvas."""
    project = new_project(tmp_path)
    editor = make_editor(project.a, project.folder)
    activate(editor)
    return editor


def tab_chain(editor, presses: int = 80) -> list[QWidget]:
    """The widgets Tab visits from the canvas, in order (until it comes back to the canvas)."""
    seen = []
    for _ in range(presses):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        widget = QApplication.focusWidget()
        if widget is editor.graphics_view:
            break
        seen.append(widget)
    return seen


def pixels(widget) -> np.ndarray:
    settle()
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    width = image.width()
    return rows.reshape(image.height(), image.bytesPerLine())[:, : width * 4].reshape(
        image.height(), width, 4
    )


def ring_changed(widget) -> float:
    """Share of the widget's outermost pixel frame that changed when it took the focus by Tab
    (a 1 px ring with rounded corners repaints most of it)."""
    before = pixels(widget)
    widget.setFocus(Qt.FocusReason.TabFocusReason)
    assert widget.hasFocus()
    after = pixels(widget)
    frame = np.ones(before.shape[:2], bool)
    frame[1:-1, 1:-1] = False
    return float((before != after).any(axis=2)[frame].mean())


def test_tab_reaches_each_row_then_its_eye_mask_first(page):
    rows = page.layer_panel.rows
    wanted = [rows[MASK], rows[MASK].eye_button, rows[SKELETON], rows[SKELETON].eye_button]

    chain = tab_chain(page)

    assert all(widget in chain for widget in wanted), chain
    start = chain.index(wanted[0])
    assert chain[start : start + 4] == wanted


def test_a_click_still_never_takes_the_focus(page):
    panel = page.layer_panel
    for widget in (panel, *panel.findChildren(QWidget)):
        assert not widget.focusPolicy() & Focus.ClickFocus, widget
    for row in panel.rows.values():
        assert row.focusPolicy() == Focus.TabFocus
        assert row.eye_button.focusPolicy() == Focus.TabFocus


@pytest.mark.parametrize("part", ["row", "eye"])
def test_the_focused_row_or_eye_draws_a_focus_ring(page, part):
    row = page.layer_panel.rows[SKELETON]
    widget = row if part == "row" else row.eye_button

    assert ring_changed(widget) > 0.5


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_space_or_enter_on_a_row_edits_its_layer(page, key):
    rows = page.layer_panel.rows
    rows[SKELETON].setFocus(Qt.FocusReason.TabFocusReason)

    QTest.keyClick(rows[SKELETON], key)

    assert page.active_layer_id == SKELETON and rows[SKELETON].is_active()
    rows[MASK].setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(rows[MASK], key)
    assert page.active_layer_id == MASK


def test_space_on_an_eye_hides_then_shows_its_layer(page):
    eye = page.layer_panel.rows[MASK].eye_button
    eye.setFocus(Qt.FocusReason.TabFocusReason)

    QTest.keyClick(eye, Qt.Key.Key_Space)
    assert not page.layers[MASK].is_visible() and not eye.isChecked()
    QTest.keyClick(eye, Qt.Key.Key_Space)
    assert page.layers[MASK].is_visible() and eye.isChecked()


def test_the_page_keys_still_work_from_a_focused_row(page):
    row = page.layer_panel.rows[MASK]
    row.setFocus(Qt.FocusReason.TabFocusReason)

    QTest.keyClick(row, Qt.Key.Key_2)
    assert page.active_layer_id == SKELETON
    QTest.keyClick(row, Qt.Key.Key_H)
    assert not any(layer.is_visible() for layer in page.layers.values())
