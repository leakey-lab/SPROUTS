"""Library tree selection under the app stylesheet (audit UI-10, QSS part).

The selected row's indentation (branch area) was filled with ``palette(Highlight)``, a
solid lavender block beside the item's own accent bar. The fix must not cost the expand
arrow of a selected parent row: Qt stops drawing the native arrow for any ``::branch``
cell that has its own background rule.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QIcon, QImage, QPalette, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

from app.gui.widgets import tokens
from tests.qt_teardown import flush_deleted

APP_QSS = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"
# The library tree's own stylesheet (ui_panels.py) sets a translucent selection fill.
LIBRARY_QSS = f"""
QTreeWidget {{ background-color: {tokens.BG_1}; border: 1px solid {tokens.BORDER}; }}
QTreeWidget::item {{ padding: 4px; border-radius: 5px; }}
QTreeWidget::item:selected {{ background-color: {tokens.SEL_SOFT}; color: {tokens.TEXT}; }}
"""


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    previous_qss, previous_palette = a.styleSheet(), a.palette()
    palette = QPalette(previous_palette)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens.SEL))  # as main.py does
    a.setPalette(palette)
    a.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield a
    a.setStyleSheet(previous_qss)
    a.setPalette(previous_palette)


@pytest.fixture
def tree(app):
    widget = QTreeWidget()
    widget.setHeaderHidden(True)
    widget.setIndentation(20)
    widget.setStyleSheet(LIBRARY_QSS)
    field = QTreeWidgetItem(widget, ["24EF"])
    tube = QTreeWidgetItem(field, ["T1"])
    date = QTreeWidgetItem(tube, ["2024.07.18"])
    for i in range(3):
        QTreeWidgetItem(date, [f"L4{i} - 11:15:3{i}"])
    widget.expandAll()
    widget.resize(260, 220)
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    yield widget
    widget.close()
    widget.deleteLater()
    del widget, field, tube, date
    flush_deleted()


def _select(tree: QTreeWidget, item: QTreeWidgetItem) -> tuple[QImage, object]:
    tree.setCurrentItem(item)
    QApplication.processEvents()
    image = tree.viewport().grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    return image, tree.visualItemRect(item)


def test_selected_row_indentation_is_not_highlight_filled(tree):
    leaf = tree.topLevelItem(0).child(0).child(0).child(0)
    image, rect = _select(tree, leaf)
    y = rect.center().y()
    branch = [QColor(image.pixel(x, y)).name() for x in range(rect.left())]
    assert branch, "leaf should be indented"
    assert branch.count(tokens.SEL) / len(branch) < 0.05


def test_selected_parent_keeps_its_expand_arrow(tree):
    """The arrow cell (innermost indentation step) holds a glyph, not a flat fill."""
    date = tree.topLevelItem(0).child(0).child(0)
    image, rect = _select(tree, date)
    cell = [
        QColor(image.pixel(x, y))
        for x in range(max(0, rect.left() - tree.indentation()), rect.left())
        for y in range(rect.top(), rect.bottom() + 1)
    ]
    fill = Counter(c.rgb() for c in cell).most_common(1)[0][0]
    fill_colour = QColor.fromRgb(fill)
    glyph = [
        c
        for c in cell
        if abs(c.red() - fill_colour.red())
        + abs(c.green() - fill_colour.green())
        + abs(c.blue() - fill_colour.blue())
        > 60
    ]
    assert len(glyph) >= 6, "the expand/collapse arrow of the selected row is gone"


def _red_pixels(image: QImage) -> int:
    return sum(
        (c := QColor(image.pixel(x, y))).red() > 150 and c.green() < 90 and c.blue() < 90
        for y in range(image.height())
        for x in range(image.width())
    )


def test_status_icons_render_under_the_app_stylesheet(tree):
    """Contract for library status markers (audit UI-09): the app QSS sets
    ``QTreeWidget::item { color }``, which overrides ``setForeground`` on every row, and
    selected rows always draw HighlightedText - but DecorationRole icons still render in
    normal AND selected rows, so per-image status must be an icon (or a delegate badge)."""
    date = tree.topLevelItem(0).child(0).child(0)
    normal, selected = date.child(1), date.child(0)
    marker = QPixmap(10, 10)
    marker.fill(QColor("#ff0000"))
    for item in (normal, selected):
        item.setIcon(0, QIcon(marker))
    tree.setCurrentItem(selected)
    QApplication.processEvents()
    for item in (normal, selected):
        grab = tree.viewport().grab(tree.visualItemRect(item)).toImage()
        assert _red_pixels(grab) >= 50, item.text(0)
