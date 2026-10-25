"""A library row is drawn in the longest short form that fits, never cut mid-word (V4).

At the default 240 px library every date row read "2024.07.18 · 8 ima…" after W10 gave the
rows 3 px more left padding: the delegate picked a form against the style's text rect
(``SE_ItemViewItemText``), but the style draws the text 3 px further in on each side
(``PM_FocusFrameHMargin`` + 1, the text margins of ``QCommonStyle``), so a form that "fit"
by up to 6 px was cut by Qt. The fit now measures the rect the text is drawn in, and the
rows have the room they had before W10 (the selected row's 3 px accent bar comes off the
right padding), so the default panel shows "2024.07.18 · 8 images" again.

What the tests check is what Qt really draws: a paint engine records the text the tree's
own paint hands to it, after the style's eliding (font-independent: offscreen Qt draws no
glyphs without a font directory, so pixels cannot tell).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPaintDevice, QPaintEngine, QPainter, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication, QHeaderView, QTreeWidget  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import file_tree_manager  # noqa: E402
from app.gui.library_index import (  # noqa: E402
    LibraryIndex,
    LibraryItemDelegate,
    index_names,
    label_forms,
)
from app.gui.ui_panels import library_tree_qss  # noqa: E402
from tests.qt_teardown import delete_widgets, flush_deleted  # noqa: E402

EIGHT = [f"24EFCAM1_T001_L{p:03d}_2024.07.18_1115{p:02d}_001_SMM" for p in range(44, 52)]
MANY = [f"24EFCAM1_T002_L{p:03d}_2024.07.19_10{p % 60:02d}30_001_SMM" for p in range(1, 129)]
ELLIPSIS = "…"


# ------------------------------------------------------------- what the style draws
class _RecordingEngine(QPaintEngine):
    """Keeps the text of every text item drawn (and draws nothing)."""

    def __init__(self) -> None:
        super().__init__(QPaintEngine.PaintEngineFeature.AllFeatures)
        self.texts: list[str] = []

    def begin(self, _device) -> bool:
        return True

    def end(self) -> bool:
        return True

    def updateState(self, _state) -> None:  # noqa: N802 - Qt API
        pass

    def drawTextItem(self, _point, item) -> None:  # noqa: N802 - Qt API
        self.texts.append(item.text())

    def drawPixmap(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawImage(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawTiledPixmap(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawPolygon(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawPath(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawRects(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawLines(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawEllipse(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def drawPoints(self, *_args) -> None:  # noqa: N802 - Qt API
        pass

    def type(self) -> QPaintEngine.Type:
        return QPaintEngine.Type.User


class _RecordingDevice(QPaintDevice):
    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self.engine = _RecordingEngine()
        self._size = (width, height)

    def paintEngine(self) -> QPaintEngine:  # noqa: N802 - Qt API
        return self.engine

    def metric(self, metric) -> int:
        m = QPaintDevice.PaintDeviceMetric
        return {
            m.PdmWidth: self._size[0],
            m.PdmHeight: self._size[1],
            m.PdmWidthMM: self._size[0],
            m.PdmHeightMM: self._size[1],
            m.PdmDpiX: 96,
            m.PdmDpiY: 96,
            m.PdmPhysicalDpiX: 96,
            m.PdmPhysicalDpiY: 96,
            m.PdmDepth: 32,
            m.PdmDevicePixelRatio: 1,
            m.PdmDevicePixelRatioScaled: int(QPaintDevice.devicePixelRatioFScale()),
        }.get(metric, 0)


def drawn(tree: QTreeWidget, item) -> str:
    """The text the tree's own paint draws in ``item``'s row (after the style's eliding)."""
    viewport = tree.viewport()
    device = _RecordingDevice(viewport.width(), viewport.height())
    painter = QPainter(device)
    viewport.render(painter, QPoint(), QRegion(tree.visualItemRect(item)))
    painter.end()
    return "".join(device.engine.texts)


class _NoFit(LibraryItemDelegate):
    """The library delegate (badges, decoration) without the form fitting."""

    @staticmethod
    def _fit_text(option) -> None:
        return None


def drawn_as(tree: QTreeWidget, index: LibraryIndex, item, text: str) -> str:
    """What the style draws in ``item``'s row for ``text`` as it is (no shorter form)."""
    fitting = tree.itemDelegate()
    plain = _NoFit(index.status_of, index.tooltip_for, tree)
    full = item.text(0)
    tree.setItemDelegate(plain)
    item.setText(0, text)
    try:
        return drawn(tree, item)
    finally:
        item.setText(0, full)
        tree.setItemDelegate(fitting)
        plain.deleteLater()


# ------------------------------------------------------------------------ the tree
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def library(app):
    """The library tree as the window builds it (its stylesheet, indentation, header)."""
    tree = QTreeWidget()
    tree.setHeaderLabels(["Field → Tube → Date → Images"])
    tree.setTextElideMode(Qt.TextElideMode.ElideRight)
    tree.setUniformRowHeights(True)
    tree.setIndentation(20)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    tree.setStyleSheet(library_tree_qss())
    index = LibraryIndex(tree)
    index.build(*index_names(EIGHT + MANY))
    tree.expandAll()
    tree.show()
    QApplication.processEvents()
    made = [tree]
    yield tree, index
    del tree, index
    delete_widgets(made)


def rows(tree: QTreeWidget):
    """The 8-image date row, the 128-image date row and an image row."""
    field = tree.topLevelItem(0)
    eight, many = field.child(0).child(0), field.child(1).child(0)
    assert eight.text(0) == "2024.07.18 · 8 images"
    assert many.text(0) == "2024.07.19 · 128 images"
    return eight, many, eight.child(3)


def check_row(tree: QTreeWidget, index: LibraryIndex, item) -> str:
    """Assert ``item`` is drawn in the longest of its forms the style draws whole (only the
    shortest may be cut, when even that does not fit); returns what was drawn."""
    forms = label_forms(item.text(0))
    shown = drawn(tree, item)
    width = tree.width()
    if shown in forms:
        longer = forms[: forms.index(shown)]
        for form in longer:  # each of them would have been cut
            assert ELLIPSIS in drawn_as(tree, index, item, form), (width, form, shown)
    else:  # even the shortest form is cut (to nothing when "…" does not fit either)
        assert shown.endswith(ELLIPSIS) or not shown, (width, shown)
        assert shown == drawn_as(tree, index, item, forms[-1]), (width, shown)
    return shown


def resize(tree: QTreeWidget, width: int) -> None:
    tree.resize(width, 400)
    QApplication.processEvents()


# --------------------------------------------------------------------------- tests
def test_a_date_row_is_never_cut_inside_a_form(library):
    """From a wide panel down to a very narrow one (1 px steps): the row text is a whole
    form; a form the style would cut is never chosen (the bug: a 6 px window per form)."""
    tree, index = library
    eight, _many, _leaf = rows(tree)
    seen = set()
    for width in range(480, 90, -1):  # wide enough for the full text in any font
        resize(tree, width)
        seen.add(check_row(tree, index, eight))
    assert set(label_forms(eight.text(0))) <= seen  # every form was the right one somewhere


def test_count_rows_and_image_rows_keep_whole_forms_too(library):
    tree, index = library
    _eight, many, leaf = rows(tree)
    for width in range(360, 90, -2):
        resize(tree, width)
        check_row(tree, index, many)
        check_row(tree, index, leaf)


def test_a_selected_row_shows_the_same_form_as_an_unselected_one(library):
    """W10 kept: the accent bar takes the place of padding, so selecting a row never
    switches it to another form."""
    tree, index = library
    eight, _many, _leaf = rows(tree)
    for width in range(300, 110, -3):
        resize(tree, width)
        tree.clearSelection()
        QApplication.processEvents()
        plain = drawn(tree, eight)
        eight.setSelected(True)
        QApplication.processEvents()
        assert drawn(tree, eight) == plain == check_row(tree, index, eight), width


# ----------------------------------------------------------- the window's default
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from app.gui import image_manager
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    made = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.resize(1280, 800)
    made.show()
    QApplication.processEvents()
    yield made
    made.close()
    made.deleteLater()
    del made
    flush_deleted()


def test_the_default_library_draws_every_date_row_whole(window, tmp_path):
    """The 240 px library of a 1280x800 window: no date row is cut (the walkthrough saw
    '2024.07.18 · 8 ima…' on every one)."""
    import time

    project = tmp_path / "project"
    project.mkdir()
    photo = QImage(32, 24, QImage.Format.Format_RGB32)
    photo.fill(QColor(90, 60, 30))
    for stem in EIGHT:
        assert photo.save(str(project / f"{stem}.jpg"))
    window.open_path(str(project))
    end = time.monotonic() + 10
    while time.monotonic() < end and not window._image_leaves():
        QApplication.processEvents()
        time.sleep(0.01)
    tree = window.file_list
    tree.expandAll()
    QApplication.processEvents()
    assert window.body_splitter.sizes()[0] == 240
    date_row = tree.topLevelItem(0).child(0).child(0)
    assert date_row.text(0) == "2024.07.18 · 8 images"
    shown = check_row(tree, file_tree_manager.library_index_for(window), date_row)
    assert shown and ELLIPSIS not in shown  # some text was drawn (the recorder saw it), whole
