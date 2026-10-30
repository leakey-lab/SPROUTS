"""Fitting label text into a width: wrap only when needed, elide what cannot wrap.

Shared by the status chip (file name / hint line), the toasts, the metrics bar's image name
and the one-line elided labels (status line, loading card, recent projects, class names),
which must never grow past the space the overlay layout or the window gives them.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFontMetrics, QFontMetricsF, QPainter
from PyQt6.QtWidgets import QLabel

QWIDGETSIZE_MAX = 16777215  # Qt's QWIDGETSIZE_MAX: "no maximum width"


class ElidedLabel(QLabel):
    """One line of text, elided to the label's width when painted (middle by default).

    ``text()`` keeps the whole text (callers put it in the tooltip too) and
    :meth:`elided_text` is what shows. The minimum width is 0, so a long text, e.g. a
    folder name, never widens the layout around it or cuts a letter in half.
    """

    def __init__(
        self,
        text: str = "",
        parent=None,
        *,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
    ):
        super().__init__(text, parent)
        self._mode = mode

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def elided_text(self) -> str:
        """The text as painted at the current width."""
        return self.fontMetrics().elidedText(self.text(), self._mode, self.contentsRect().width())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), int(self.alignment()), self.elided_text())
        painter.end()


def label_advance(metrics: QFontMetrics, text: str) -> int:
    """The width a label gives ``text``: its fractional advance rounded up. A font with
    fractional advances (DejaVu on Linux) makes ``horizontalAdvance`` round to the nearest
    pixel, so when the fraction is under .5 a name chosen by it ends one pixel wider than the
    budget it was chosen for."""
    return math.ceil(QFontMetricsF(metrics).horizontalAdvance(text))


def fit_forms(
    forms: Sequence[str],
    metrics: QFontMetrics,
    width: int,
    fits: Callable[[str], bool] | None = None,
) -> str:
    """The first of ``forms`` (most complete first) that ``fits``, else the last one, elided
    to ``width`` px: at its end, or in the middle when it is the only form.

    ``fits`` defaults to "whole in ``width`` px" (:func:`label_advance`)."""
    if len(forms) == 1:
        return metrics.elidedText(forms[0], Qt.TextElideMode.ElideMiddle, width)
    if fits is None:
        for form in forms:
            if label_advance(metrics, form) <= width:
                return form  # whole in the width: Qt would not elide it
        return metrics.elidedText(forms[-1], Qt.TextElideMode.ElideRight, width)
    shown = next((form for form in forms if fits(form)), forms[-1])
    return metrics.elidedText(shown, Qt.TextElideMode.ElideRight, width)


def elide_long_words(text: str, fits: Callable[[str], bool], elide: Callable[[str], str]) -> str:
    """Replace every space-separated word that does not ``fit`` by ``elide(word)``.

    Word wrapping cannot break a long path, so such a word is shortened instead.
    """
    return " ".join(word if fits(word) else elide(word) for word in text.split(" "))


def wrap_label(label: QLabel, max_width: int) -> None:
    """Keep ``label`` on one line while its text fits in ``max_width`` px, else wrap it
    at exactly ``max_width``. Calls are no-ops when nothing changes (no relayout churn)."""
    label.ensurePolished()
    wrap = label.fontMetrics().horizontalAdvance(label.text()) > max_width
    if label.wordWrap() != wrap:
        label.setWordWrap(wrap)
    if wrap:
        label.setFixedWidth(max_width)
    else:
        label.setMinimumWidth(0)
        label.setMaximumWidth(QWIDGETSIZE_MAX)
