"""Dialog chrome under the app stylesheet (audit UXS-13 / UI-21).

Top-level dialogs inherited ``QWidget { background: transparent }`` and painted on opaque
black; class colour swatches grew to 20x42 under the global button padding; spin-box
steppers were too narrow for the native arrows to draw.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QColorDialog,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
)

from app.gt.registry import default_registry
from app.gui.class_editor_dialog import ClassEditorDialog
from app.gui.export_dataset_dialog import ExportDatasetDialog
from app.gui.widgets import tokens

APP_QSS = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    previous = a.styleSheet()
    a.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield a
    a.setStyleSheet(previous)


def _show(widget):
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    QApplication.processEvents()
    return widget.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _edge_colour(image: QImage) -> str:
    """Most common colour of the 3 px frame of a grab (the dialog's own surface)."""
    w, h = image.width(), image.height()
    frame = [
        QColor.fromRgba(image.pixel(x, y)).name(QColor.NameFormat.HexArgb)
        for y in range(h)
        for x in range(w)
        if x < 3 or y < 3 or x >= w - 3 or y >= h - 3
    ]
    return Counter(frame).most_common(1)[0][0]


def _message_box():
    box = QMessageBox(QMessageBox.Icon.Question, "Unsaved mask", "Save changes to the mask?")
    box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard)
    return box


def _colour_dialog():
    dialog = QColorDialog(QColor("#4c9bd6"))
    dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
    return dialog


DIALOGS = {
    "message-box": _message_box,
    "colour-dialog": _colour_dialog,
    "class-editor": lambda: ClassEditorDialog(default_registry()),
    "export-dataset": lambda: ExportDatasetDialog("unused-project", {}, default_registry()),
}


@pytest.mark.parametrize("factory", DIALOGS.values(), ids=DIALOGS.keys())
def test_dialogs_paint_the_dialog_surface(app, factory):
    """Opaque BG_1, not the black a transparent top-level window turns into."""
    dialog = factory()
    try:
        assert _edge_colour(_show(dialog)) == "#ff" + tokens.BG_1.lstrip("#")
    finally:
        dialog.close()


def _swatches(dialog, tooltip: str) -> list[QPushButton]:
    return [b for b in dialog.findChildren(QPushButton) if b.toolTip() == tooltip]


def test_class_colour_swatches_stay_square(app):
    dialog = ClassEditorDialog(default_registry())
    try:
        _show(dialog)
        row_swatches = _swatches(dialog, "Change colour")
        assert row_swatches, "no class rows"
        assert {s.size() for s in row_swatches} == {QSize(20, 20)}
        assert [s.size() for s in _swatches(dialog, "Choose colour")] == [QSize(22, 22)]
    finally:
        dialog.close()


def test_recoloured_swatch_stays_square(app):
    dialog = ClassEditorDialog(default_registry())
    try:
        _show(dialog)
        dialog._apply_color("nodule", "#123456")
        dialog._set_add_color("#654321")
        QApplication.processEvents()
        assert {s.size() for s in _swatches(dialog, "Change colour")} == {QSize(20, 20)}
        assert _swatches(dialog, "Choose colour")[0].size() == QSize(22, 22)
    finally:
        dialog.close()


def _up_button_rect(spin: QSpinBox):
    opt = QStyleOptionSpinBox()
    spin.initStyleOption(opt)
    return spin.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxUp, spin
    )


def test_spin_box_steppers_show_arrows_inside_the_border(app):
    spin = QSpinBox()
    spin.setRange(0, 90)
    spin.setValue(20)
    spin.resize(200, spin.sizeHint().height())
    try:
        image = _show(spin)
        up = _up_button_rect(spin)
        # the native arrow needs >= 16 px to be drawn at all (12 px buttons showed none)
        assert up.width() >= 16
        # inside the 1 px border: the rounded frame and its focus ring stay whole
        assert up.right() < spin.width() - 1 and up.top() >= 1
        light = sum(
            QColor(image.pixel(x, y)).lightness() > 180
            for y in range(up.top(), up.bottom() + 1)
            for x in range(up.left(), up.right() + 1)
        )
        assert light >= 3, "no visible arrow in the up button"
    finally:
        spin.close()
