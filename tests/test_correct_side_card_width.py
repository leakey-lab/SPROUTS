"""The Correct page's side card follows its rows: when a Layers row's state label changes, the
card takes its new width (LayerPanel's contract: it grows when a label needs more room, and
narrows again). Guard of the overlay measuring patches (Qt toolkit items 4 and 7).

Qt refreshes a nested layout's size hint one event-loop pass per level, and the card holds
the Layers panel one level down, so a measure right after the change read the old hint.
Today the card lags one change behind (S02-01, owner question #4): a label is cut, or the
card stays too wide.
"""

from __future__ import annotations

import pytest

from app.gui.correct.layer import CLASSES, SKELETON, LayerStatus
from tests.correct_support import settle
from tests.qt_wait import settle as run_events

#: Realistic row labels, long and short, in the order the probe of S02-01 used.
LABELS = (
    (CLASSES, "error", "Class labels not shown"),
    (CLASSES, "none", "No class labels"),
    (SKELETON, "error", "Skeleton not shown"),
    (SKELETON, "none", "Prediction"),
    (CLASSES, "dirty", "Unsaved · remapped"),
    (CLASSES, "clean", "GT · 12/15 checked"),
    (CLASSES, "none", "No class labels"),
)


def _card_after_each_label(make_editor, project) -> list[tuple[str, int, int, list[str]]]:
    """For each label: ``(label, card width, card size hint, cut row labels)`` once the
    events have run."""
    editor = make_editor(project.a, project.folder, size=(1400, 900))
    settle(editor)
    card, panel = editor.side_panels, editor.layer_panel
    seen = []
    for layer_id, state, text in LABELS:
        panel.set_status(layer_id, LayerStatus(state, text))
        run_events()  # what the page does: no extra relayout after the events
        cut = [
            row.state_label.text()
            for row in panel.rows.values()
            if row.state_label.isVisible()
            and row.state_label.width() < row.state_label.sizeHint().width()
        ]
        seen.append((text, card.width(), card.sizeHint().width(), cut))
    return seen


def test_the_card_is_measured_after_label_changes(make_editor, project, correct_guards):
    seen = _card_after_each_label(make_editor, project)
    assert len(seen) == len(LABELS)
    assert all(width > 0 and hint > 0 for _text, width, hint, _cut in seen)


@pytest.mark.xfail(
    strict=True,
    reason="S02-01 (owner question #4): the side card keeps a stale width when a Layers row "
    "label changes (a label is cut, or the card stays too wide)",
)
def test_the_card_takes_its_rows_width_after_every_label_change(
    make_editor, project, correct_guards
):
    seen = _card_after_each_label(make_editor, project)
    assert [(text, width, cut) for text, width, _hint, cut in seen] == [
        (text, hint, []) for text, _width, hint, _cut in seen
    ]
