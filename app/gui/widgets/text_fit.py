"""Fitting label text into a width: wrap only when needed, elide what cannot wrap.

Shared by the status chip (file name / hint line) and the toasts, which must never grow
past the space the overlay layout or the window gives them.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QLabel

QWIDGETSIZE_MAX = 16777215  # Qt's QWIDGETSIZE_MAX: "no maximum width"


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
