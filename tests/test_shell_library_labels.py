"""Library labels keep their informative part at laptop widths (KNOWN_ISSUES #5, H3).

Before: at the default 240 px library a date row with a 3-digit count read
"2024.07.18 · 128 im…", at the 170 px minimum an image row lost its time ("L44 · 1…"),
and the metrics bar elided the image name in the middle, which drops exactly the tube,
L-number and date ("24EFCAM1_T00…_001_SMM"). Now a narrow row is shown in a shorter form
that keeps its head ("2024.07.18 · 128", "2024.07.18"; "L44 · 11:15", "L44"), the name cell
falls back to "T1 · L44 · 2024.07.18 · 11:15:30" and shorter, and the full text is in the
tooltip. The item texts themselves (search, accessibility, tests) are unchanged.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from PyQt6.QtGui import QHelpEvent  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QStyle,
    QStyleOptionViewItem,
    QToolTip,
    QTreeWidget,
)

from app.gui.library_index import (  # noqa: E402
    LibraryIndex,
    fit_label,
    index_names,
    label_forms,
    stem_forms,
)
from app.gui.metrics_bar import MetricsBar  # noqa: E402

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_deleted_widgets():
    """Widgets a test deleteLater()s are destroyed before the next test (never at exit)."""
    yield
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ------------------------------------------------------------------ Qt-free forms
@pytest.mark.parametrize(
    ("text", "forms"),
    [
        ("L44 · 11:15:30", ["L44 · 11:15:30", "L44 · 11:15", "L44"]),
        (
            "2024.07.18 · 128 images",
            ["2024.07.18 · 128 images", "2024.07.18 · 128", "2024.07.18", "07.18"],
        ),
        ("2024.07.18 · 1 image", ["2024.07.18 · 1 image", "2024.07.18 · 1", "2024.07.18", "07.18"]),
        ("No Date · 46,178 images", ["No Date · 46,178 images", "No Date · 46,178", "No Date"]),
        ("L7 · 24EF_T001_L007_nodate", ["L7 · 24EF_T001_L007_nodate", "L7"]),
        ("T1", ["T1"]),
        ("plain", ["plain"]),
        ("", [""]),
    ],
)
def test_label_forms_keep_the_head(text, forms):
    assert label_forms(text) == forms


def test_stem_forms_keep_tube_depth_and_date():
    assert stem_forms(STEM) == [
        STEM,
        "T1 · L44 · 2024.07.18 · 11:15:30",
        "T1 · L44 · 2024.07.18",
        "T1 · L44",
    ]
    assert stem_forms("24EF_T001_L007_nodate") == ["24EF_T001_L007_nodate", "T1 · L7"]
    assert stem_forms("plain_name") == ["plain_name"]


def test_fit_label_takes_the_first_form_that_fits():
    forms = label_forms("2024.07.18 · 128 images")
    assert fit_label(forms, lambda t: len(t) <= 30) == "2024.07.18 · 128 images"
    assert fit_label(forms, lambda t: len(t) <= 16) == "2024.07.18 · 128"
    assert fit_label(forms, lambda t: len(t) <= 12) == "2024.07.18"
    assert fit_label(forms, lambda t: len(t) <= 6) == "07.18"
    assert fit_label(forms, lambda t: False) == "07.18"  # Qt elides the shortest


# ------------------------------------------------------------------ the tree rows
def _tree(app, width: int) -> tuple[QTreeWidget, LibraryIndex]:
    names = [f"24EFCAM1_T001_L{p:03d}_2024.07.18_11{p % 60:02d}30_001_SMM" for p in range(1, 129)]
    tree = QTreeWidget()
    tree.setHeaderLabels(["Library"])
    tree.setTextElideMode(Qt.TextElideMode.ElideRight)
    index = LibraryIndex(tree)
    index.build(*index_names(names))
    tree.expandAll()
    tree.resize(width, 600)
    tree.show()
    QApplication.processEvents()
    return tree, index


def _painted(tree: QTreeWidget, item) -> tuple[str, int]:
    """(text the delegate paints for ``item``, width of its text rect)."""
    model_index = tree.indexFromItem(item)
    option = QStyleOptionViewItem()
    option.initFrom(tree.viewport())
    option.rect = tree.visualRect(model_index)
    option.widget = tree
    tree.itemDelegate().initStyleOption(option, model_index)
    room = tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, tree).width()
    return option.text, room


def _date_row(tree):
    return tree.topLevelItem(0).child(0).child(0)


@pytest.mark.parametrize("width", list(range(150, 420, 10)))
def test_a_date_row_shows_the_longest_form_that_fits(app, width):
    tree, _index = _tree(app, width)
    row = _date_row(tree)
    assert row.text(0) == "2024.07.18 · 128 images"  # the item text is unchanged
    painted, room = _painted(tree, row)
    forms = label_forms(row.text(0))
    assert painted in forms
    metrics = tree.fontMetrics()
    fitting = [f for f in forms if metrics.horizontalAdvance(f) <= room]
    assert painted == (fitting[0] if fitting else forms[-1])
    tree.deleteLater()


def test_a_narrow_image_row_drops_seconds_then_the_time(app):
    tree, _index = _tree(app, 200)
    leaf = _date_row(tree).child(43)
    full = leaf.text(0)
    metrics = tree.fontMetrics()
    for width in range(120, 400, 5):
        tree.resize(width, 600)
        QApplication.processEvents()
        painted, room = _painted(tree, leaf)
        assert painted in label_forms(full)
        if metrics.horizontalAdvance(full) <= room:
            assert painted == full
    tree.deleteLater()


def test_a_shortened_group_row_has_its_full_text_as_tooltip(app, monkeypatch):
    tree, _index = _tree(app, 140)
    row = _date_row(tree)
    painted, _room = _painted(tree, row)
    assert painted != row.text(0)
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: shown.append(args[1]))
    rect = tree.visualItemRect(row)
    event = QHelpEvent(QEvent.Type.ToolTip, rect.center(), tree.mapToGlobal(rect.center()))
    option = QStyleOptionViewItem()
    option.initFrom(tree.viewport())
    option.rect = rect
    option.widget = tree
    handled = tree.itemDelegate().helpEvent(event, tree, option, tree.indexFromItem(row))
    assert handled and shown == ["2024.07.18 · 128 images"]
    tree.deleteLater()


# --------------------------------------------------------------- the metrics bar
def _name_label(app, name: str, width: int):
    """The metrics bar's name label showing ``name``, ``width`` px of contents wide."""
    bar = MetricsBar()
    bar.set_metrics(name=name)
    label = bar._name_value
    label.setParent(None)
    label.setFixedWidth(width + label.contentsMargins().left() + label.contentsMargins().right())
    label.show()
    QApplication.processEvents()
    bar.deleteLater()
    return label


def test_the_name_cell_falls_back_to_tube_depth_date_and_time(app):
    forms = stem_forms(STEM)
    probe = _name_label(app, STEM, 2000)
    advance = probe.fontMetrics().horizontalAdvance
    assert probe._elided() == STEM and probe.toolTip() == ""  # room for the whole name
    probe.deleteLater()
    for i, form in enumerate(forms[1:], start=1):
        label = _name_label(app, STEM, advance(form))
        assert label.text() == STEM and label.toolTip() == STEM
        assert label._elided() == form, (i, label._elided())
        label.deleteLater()
    tiny = _name_label(app, STEM, advance(forms[-1]) - 4)
    assert tiny._elided().startswith("T1") and tiny._elided().endswith("…")
    tiny.deleteLater()


def test_an_unparsed_name_still_elides_in_the_middle(app):
    name = "an_image_name_without_tube_or_depth_" + "x" * 80
    probe = _name_label(app, name, 2000)
    half = probe.fontMetrics().horizontalAdvance(name) // 2
    probe.deleteLater()
    label = _name_label(app, name, half)
    painted = label._elided()
    assert "…" in painted and painted.startswith("an_") and painted.endswith("x")
    assert label.toolTip() == name
    label.deleteLater()
