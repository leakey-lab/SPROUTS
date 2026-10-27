"""Primary / ghost button factories (audit AR-17): one button system, styled by the app QSS.

The factories only name the button (``primaryButton`` / ``ghostButton``), so its look
comes from ``dark_theme.qss`` - there is no per-module button stylesheet to drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.gui.widgets import tokens
from app.gui.widgets.buttons import ghost_button, primary_button

APP_QSS = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    previous = a.styleSheet()
    a.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield a
    a.setStyleSheet(previous)


def _render(button) -> QImage:
    button.show()
    QTest.qWaitForWindowExposed(button)
    QApplication.processEvents()
    return button.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _colours(image: QImage) -> set[str]:
    return {
        QColor(image.pixel(x, y)).name()
        for y in range(image.height())
        for x in range(image.width())
        if QColor.fromRgba(image.pixel(x, y)).alpha() == 255
    }


def test_primary_button_is_filled_with_the_accent(app):
    button = primary_button("Save", "save", tooltip="Save the mask (Ctrl+S)")
    try:
        image = _render(button)
        assert QColor(image.pixel(6, image.height() // 2)).name() == tokens.ACCENT
        assert button.text() == "Save"
        assert button.toolTip() == "Save the mask (Ctrl+S)"
        assert button.cursor().shape() == Qt.CursorShape.PointingHandCursor
    finally:
        button.close()


def test_primary_icon_uses_the_ink_colour(app):
    button = primary_button("Save", "save")
    pixmap = button.icon().pixmap(button.iconSize())
    assert tokens.ACCENT_INK in _colours(pixmap.toImage())


def test_ghost_button_has_no_fill(app):
    button = ghost_button("Clear", "trash")
    try:
        image = _render(button)
        assert QColor.fromRgba(image.pixel(6, image.height() // 2)).alpha() == 0
        assert tokens.TEXT_MUTED in _colours(button.icon().pixmap(QSize(16, 16)).toImage())
    finally:
        button.close()


def test_compact_buttons_are_32_px_tall(app):
    regular, compact = primary_button("Export"), primary_button("Export", compact=True)
    try:
        _render(regular), _render(compact)
        assert compact.height() == 32
        assert regular.height() > compact.height()
    finally:
        regular.close()
        compact.close()


def test_icon_only_button_is_named_for_assistive_tech(app):
    button = ghost_button("", "trash", tooltip="Clear mask")
    assert button.accessibleName() == "Clear mask"
    labelled = ghost_button("Clear", "trash", tooltip="Clear mask")
    assert labelled.accessibleName() == ""  # Qt already announces the text
