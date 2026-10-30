"""The selected library row looks like the others, plus the selection (W10).

Under the app stylesheet a selected row got a 3 px accent bar (``border-left``) that only
selected rows had, so its badge and label moved 3 px right (and a label at the edge of
fitting switched to a shorter form), and Qt painted the status badge in ``QIcon``'s
Selected mode, which tints it with the highlight colour (green and orange turned teal and
salmon). Every row now reserves the bar's width, and the badge has its own Selected
pixmap, so selecting a row changes its background and bar only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QIcon, QImage, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
)

from app.gui.widgets import tokens
from tests.qt_teardown import flush_deleted

APP_QSS = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"
STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]


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
def library(app):
    """A library tree styled like the app's (its own stylesheet over the app's)."""
    from app.gui.library_index import LibraryIndex, index_names
    from app.gui.ui_panels import library_tree_qss

    tree = QTreeWidget()
    tree.setStyleSheet(library_tree_qss())
    index = LibraryIndex(
        tree,
        path_of=lambda n: n,
        mask_state=lambda n: "prediction",
        skeleton_state=lambda n: "prediction",
    )
    index.build(*index_names(STEMS))
    tree.expandAll()
    tree.resize(260, 300)
    tree.show()
    QTest.qWaitForWindowExposed(tree)
    yield tree, index
    tree.close()
    tree.deleteLater()
    del tree, index
    flush_deleted()


def _rects(tree, index, item, selected: bool) -> tuple[int, int]:
    """x of the badge and of the text of ``item``, painted selected or not."""
    model_index = tree.indexFromItem(item)
    option = QStyleOptionViewItem()
    option.rect = tree.visualRect(model_index)
    index._delegate.initStyleOption(option, model_index)
    if selected:
        option.state |= QStyle.StateFlag.State_Selected
    else:
        option.state &= ~QStyle.StateFlag.State_Selected
    style = tree.style()
    decoration = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, option, tree)
    text = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, tree)
    return decoration.x(), text.x()


def test_selecting_a_row_does_not_move_its_badge_or_label(library):
    tree, index = library
    leaf = index.leaves()[1]
    assert _rects(tree, index, leaf, True) == _rects(tree, index, leaf, False)


def test_the_selected_row_keeps_its_accent_bar(library):
    tree, index = library
    leaf = index.leaves()[1]
    tree.setCurrentItem(leaf)
    QApplication.processEvents()
    rect = tree.visualItemRect(leaf)
    image = tree.viewport().grab(rect).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    y = image.height() // 2
    left = [QColor(image.pixel(x, y)) for x in range(0, 6)]
    bar = QColor(tokens.SEL)
    assert any(
        abs(c.red() - bar.red()) + abs(c.green() - bar.green()) + abs(c.blue() - bar.blue()) < 40
        for c in left
    ), [c.name() for c in left]


@pytest.mark.parametrize(
    "state",
    [
        ("none", "none", False),
        ("prediction", "none", False),
        ("none", "gt", False),
        ("gt", "prediction", True),
    ],
)
def test_the_status_badge_is_not_tinted_when_selected(library, state):
    _tree, index = library
    icon = index._delegate.status_icon(*state)
    size = QSize(32, 12)
    normal = icon.pixmap(size, QIcon.Mode.Normal).toImage()
    for mode in (QIcon.Mode.Selected, QIcon.Mode.Active):
        assert icon.pixmap(size, mode).toImage() == normal, mode
