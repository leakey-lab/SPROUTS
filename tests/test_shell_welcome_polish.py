"""Welcome screen polish (walkthrough W8, W15).

W8: a recent row's name label was 14 px tall for 17 px of text, so underscores and
descenders were cut off ("dataset_46k" read "dataset 46k") at every window size. The labels
sit in a layout inside the row's QPushButton, whose own size hints count only its text and
icon; the row squeezed them. Checked with the stylesheet's fonts and with a taller font (a
user's font or text scaling makes lines taller than the test machine's).

W15: the stage overview under the recents was drawn as six bordered pills, which read as
buttons but do nothing. It is now one line of plain text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from app.gui import welcome_screen
from app.gui.recent_projects import RecentProjectsStore
from app.gui.welcome_screen import RecentRow, WelcomeWidget
from tests.qt_teardown import delete_widgets

STAGES = ["Library", "Generate", "Correct", "Measure", "Visualize"]
QSS_PATH = Path(__file__).resolve().parents[1] / "resources" / "themes" / "dark_theme.qss"


class _Settings:
    def __init__(self, entries=()):
        self.data = {"recent_projects": json.dumps(list(entries))}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.data[key] = value


@pytest.fixture
def welcome(app):
    made = []

    def make(names=("dataset", "dataset_46k", "2024 EF image data"), *, theme=False, **kw):
        entries = [
            {"name": n, "path": rf"D:\data\{n}", "count": 46178, "ts": "2026-09-20T10:00:00"}
            for n in names
        ]
        widget = WelcomeWidget(
            lambda: None,
            lambda path: None,
            recent_store=RecentProjectsStore(_Settings(entries)),
            stage_labels=STAGES,
            probe_async=False,
            **kw,
        )
        if theme:  # the app theme (its QPushButton min-height), on this widget only
            widget.setStyleSheet(QSS_PATH.read_text(encoding="utf-8") + widget.styleSheet())
        made.append(widget)
        widget.resize(1280, 800)
        widget.show()
        for _ in range(3):
            QApplication.processEvents()
        return widget

    yield make
    delete_widgets(made)


def _taller_name_font(monkeypatch, px: str = "22px") -> None:
    original = RecentRow._label

    def label(text, color, size, **kw):
        return original(text, color, px if size == "12.5px" else size, **kw)

    monkeypatch.setattr(RecentRow, "_label", staticmethod(label))


# ------------------------------------------------------------------------ W8
NAMES = ("dataset", "dataset_46k", "2024 EF image data")
MORE = (*NAMES, "trial_B", "project_g", "q_y_j")  # more rows than show: the list scrolls


@pytest.mark.parametrize("theme", [False, True], ids=["no-theme", "app-theme"])
@pytest.mark.parametrize("names", [NAMES, MORE], ids=["3-rows", "6-rows"])
@pytest.mark.parametrize("taller", [False, True], ids=["qss-font", "taller-font"])
def test_every_recent_row_label_gets_its_full_height(welcome, monkeypatch, taller, names, theme):
    """With the app theme, QPushButton's ``min-height`` becomes an explicit minimum height,
    which the row's layout prefers to the button's size hints."""
    if taller:
        _taller_name_font(monkeypatch)
    widget = welcome(names=names, theme=theme)
    rows = widget.recent_rows()
    assert [r.entry.name for r in rows] == list(names)
    for row in rows:
        for label in (row._name_label, row._path_label, row._info_label):
            need = label.minimumSizeHint().height()
            assert label.height() >= need, (row.entry.name, label.text(), label.height(), need)


def test_the_open_button_is_as_tall_as_its_content(welcome, monkeypatch):
    _taller_name_font(monkeypatch)
    for row in welcome().recent_rows():
        content = row.open_button.layout().totalMinimumSize().height()
        assert row.open_button.minimumSizeHint().height() >= content
        assert (
            row.open_button.sizeHint().height() >= row.open_button.layout().totalSizeHint().height()
        )
        assert row.open_button.height() >= content


@pytest.mark.parametrize("theme", [False, True], ids=["no-theme", "app-theme"])
def test_four_rows_show_whole_before_the_list_scrolls(welcome, monkeypatch, theme):
    _taller_name_font(monkeypatch)
    widget = welcome(names=[f"project_{i}" for i in range(6)], theme=theme)
    viewport = widget._recent_scroll.viewport()
    shown = widget.recent_rows()[: welcome_screen.VISIBLE_RECENT_ROWS]
    for row in shown:
        bottom = row.mapTo(viewport, row.rect().bottomLeft()).y()
        assert bottom < viewport.height(), (row.entry.name, bottom, viewport.height())


# ----------------------------------------------------------------------- W15
def _stage_overview(widget: WelcomeWidget) -> list[QLabel]:
    return [
        label
        for label in widget.findChildren(QLabel)
        if any(stage in label.text() for stage in STAGES) and label.isVisible()
    ]


def test_the_stage_overview_is_plain_text_not_buttons(welcome):
    widget = welcome()
    labels = _stage_overview(widget)
    assert labels, "the stages are still named on the Welcome screen"
    for label in labels:
        style = label.styleSheet().lower()
        assert "border" not in style and "background" not in style, style
        assert label.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert label.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_the_stage_overview_names_every_stage_in_order(welcome):
    widget = welcome()
    text = " ".join(label.text() for label in _stage_overview(widget))
    positions = [text.find(stage) for stage in STAGES]
    assert -1 not in positions and positions == sorted(positions), text
    assert widget.stage_labels() == STAGES


# ---------------------------------------------------------------- long paths
def test_a_long_recent_path_is_elided_in_the_middle(welcome):
    """The drive and the folder's own name stay; the path never sets the row's width."""
    row = welcome(names=("dataset_46k",)).recent_rows()[0]
    label = row._path_label
    label.setText("D:\\" + "a rather long folder name\\" * 12 + "dataset_46k")
    QApplication.processEvents()
    shown = label.elided_text()
    assert label.minimumSizeHint().width() == 0
    assert "…" in shown and shown.startswith("D:\\") and shown.endswith("dataset_46k")
