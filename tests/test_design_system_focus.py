"""Keyboard focus must be visible under the app stylesheet (WCAG 2.4.7, audit UXS-08).

Each control is styled only by ``dark_theme.qss``. It is grabbed while a neighbour holds
focus and again after it takes focus with the Tab reason; the ring must repaint a real
share of the control (its frame, or its indicator / handle).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_QSS = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    previous = a.styleSheet()
    a.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield a
    a.setStyleSheet(previous)


@pytest.fixture
def host(app):
    """An active top-level window with a focus sink; the control under test goes below it."""
    window = QWidget()
    window.resize(320, 260)
    layout = QVBoxLayout(window)
    sink = QLineEdit("sink")
    layout.addWidget(sink)
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowExposed(window)
    yield window, layout, sink
    window.close()
    window.deleteLater()


def _grab(widget: QWidget) -> QImage:
    QApplication.processEvents()
    return widget.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _changed(before: QImage, after: QImage, band: int | None = None) -> float:
    """Share of pixels that changed (``band``: only the outer frame this many px wide)."""
    assert before.size() == after.size()
    w, h = before.width(), before.height()
    total = changed = 0
    for y in range(h):
        for x in range(w):
            if band is not None and band <= x < w - band and band <= y < h - band:
                continue
            total += 1
            changed += before.pixel(x, y) != after.pixel(x, y)
    return changed / total


def _focus_change(host, widget: QWidget, band: int | None) -> float:
    _window, layout, sink = host
    layout.addWidget(widget)
    widget.show()
    sink.setFocus(Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()
    assert sink.hasFocus(), "offscreen window did not activate"
    before = _grab(widget)
    widget.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert widget.hasFocus()
    return _changed(before, _grab(widget), band)


def _button(object_name: str | None = None) -> QPushButton:
    button = QPushButton("Export")
    if object_name:
        button.setObjectName(object_name)
    return button


def _tool_button(checked: bool) -> QToolButton:
    button = QToolButton()
    button.setText("Library")
    button.setCheckable(True)
    button.setChecked(checked)
    return button


def _tree() -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setFixedHeight(90)
    QTreeWidgetItem(tree, ["24EF"])
    return tree


# (id, factory, frame band in px or None for "anywhere", minimum changed share)
FRAMED = [
    ("push-button", lambda: _button(), 1, 0.5),
    ("primary-button", lambda: _button("primaryButton"), 1, 0.5),
    ("ghost-button", lambda: _button("ghostButton"), 1, 0.5),
    ("tool-button", lambda: _tool_button(False), 1, 0.5),
    ("tool-button-checked", lambda: _tool_button(True), 1, 0.5),
    ("tree", _tree, 1, 0.5),
    ("line-edit", lambda: QLineEdit("T001"), 1, 0.5),
    ("combo-box", lambda: _combo(), 1, 0.5),
]


def _combo() -> QComboBox:
    combo = QComboBox()
    combo.addItems(["Single", "Overlay"])
    return combo


def _checkbox(checked: bool) -> QCheckBox:
    box = QCheckBox("Keep each tube in one split")
    box.setChecked(checked)
    return box


def _radio(checked: bool) -> QRadioButton:
    radio = QRadioButton("Overlay")
    radio.setChecked(checked)
    return radio


def _slider() -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(40)
    slider.setFixedWidth(200)
    return slider


# Indicators and handles: the ring is drawn on a small sub-control, so count pixels.
SUBCONTROL = [
    ("checkbox", lambda: _checkbox(False)),
    ("checkbox-checked", lambda: _checkbox(True)),
    ("radio-checked", lambda: _radio(True)),
    ("slider", _slider),
]


@pytest.mark.parametrize(
    "factory,band,minimum", [c[1:] for c in FRAMED], ids=[c[0] for c in FRAMED]
)
def test_focus_ring_repaints_the_frame(host, factory, band, minimum):
    assert _focus_change(host, factory(), band) >= minimum


@pytest.mark.parametrize("factory", [c[1] for c in SUBCONTROL], ids=[c[0] for c in SUBCONTROL])
def test_focus_ring_repaints_the_indicator(host, factory):
    widget = factory()
    share = _focus_change(host, widget, None)
    # a 16 px indicator / 15 px handle ring is >= 40 px; the widget is ~200 x 20 px
    assert share * widget.width() * widget.height() >= 40


def test_tree_focus_ring_does_not_shift_rows(host):
    """The ring replaces a transparent border of the same width, so rows do not jump."""
    _window, layout, sink = host
    tree = _tree()
    layout.addWidget(tree)
    tree.show()
    sink.setFocus(Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()
    unfocused = tree.viewport().geometry()
    tree.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert tree.viewport().geometry() == unfocused


def test_class_colour_swatch_shows_focus_on_any_colour(app):
    """Swatches style their own border (to show the class colour), so they carry the ring."""
    from app.gt.registry import default_registry
    from app.gui.class_editor_dialog import ClassEditorDialog

    dialog = ClassEditorDialog(default_registry())
    dialog.show()
    dialog.activateWindow()
    QTest.qWaitForWindowExposed(dialog)
    try:
        swatch = next(b for b in dialog.findChildren(QPushButton) if b.toolTip() == "Change colour")
        name_edit = dialog.findChildren(QLineEdit)[0]
        name_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        QApplication.processEvents()
        assert name_edit.hasFocus(), "offscreen dialog did not activate"
        before = _grab(swatch)
        swatch.setFocus(Qt.FocusReason.TabFocusReason)
        QApplication.processEvents()
        assert swatch.hasFocus()
        assert _changed(before, _grab(swatch), band=1) >= 0.5
    finally:
        dialog.close()
