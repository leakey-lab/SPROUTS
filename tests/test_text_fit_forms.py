"""``text_fit.fit_forms`` / ``label_advance``: the one "first form that fits" rule of the
status chip's file name and the metrics bar's image name."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QFont, QFontMetrics, QFontMetricsF  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.widgets.text_fit import fit_forms, label_advance  # noqa: E402

FORMS = ["T1 · L44 · 2024.07.18 · 11:15:30", "T1 · L44 · 2024.07.18", "T1 · L44"]
LONE = "an_image_name_without_tube_or_depth_" * 3


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=[9.5, 11.0, 12.5], ids=lambda pt: f"{pt}pt")
def metrics(app, request):
    font = QFont(app.font())
    font.setPointSizeF(request.param)  # fractional pixel sizes: fractional advances
    return QFontMetrics(font)


def test_the_first_form_whole_in_the_width_is_shown_whole(metrics):
    for form in FORMS:
        assert fit_forms(FORMS, metrics, label_advance(metrics, form)) == form


def test_without_a_form_that_fits_the_shortest_is_elided_at_its_end(metrics):
    shown = fit_forms(FORMS, metrics, label_advance(metrics, FORMS[-1]) - 1)
    assert shown.endswith("…") and FORMS[-1].startswith(shown[:-1])


def test_a_lone_form_is_elided_in_the_middle(metrics):
    shown = fit_forms([LONE], metrics, 120)
    assert "…" in shown and shown[0] == LONE[0] and shown[-1] == LONE[-1]
    assert fit_forms([LONE], metrics, label_advance(metrics, LONE)) == LONE


def test_the_caller_can_decide_which_form_fits(metrics):
    """The form a caller's ``fits`` picks is shown, still elided to the width."""
    width = label_advance(metrics, FORMS[1])
    assert fit_forms(FORMS, metrics, width, lambda form: form == FORMS[2]) == FORMS[2]
    first = fit_forms(FORMS, metrics, width, lambda form: True)
    assert first == metrics.elidedText(FORMS[0], Qt.TextElideMode.ElideRight, width) != FORMS[0]


def test_a_text_within_its_label_advance_is_drawn_whole(metrics):
    """``label_advance`` rounds a fractional advance up, so Qt never cuts such a text."""
    for text in (*FORMS, LONE):
        width = label_advance(metrics, text)
        assert width >= QFontMetricsF(metrics).horizontalAdvance(text) > width - 1
        assert metrics.elidedText(text, Qt.TextElideMode.ElideRight, width) == text
