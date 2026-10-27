"""A hand-broken ``gt/classes.json`` must never abort the app (RB-09).

``"id": 1e999`` parses to ``float('inf')``; ``int()`` on it raised OverflowError
inside the Trace GT editor's ``load_image`` (called from the Library's itemClicked
slot, where PyQt6 turns an escaping exception into qFatal). ``"id": true`` was
silently accepted as id 1. Both must now surface as an invalid registry naming the bad
field, with the class labels read-only (the Correct page's third layer since WG-T11).
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

from app.gt import gt_path
from app.gui.correct.editor import CorrectEditor
from app.gui.correct.layer import CLASSES
from tests.correct_support import drag
from tests.qt_teardown import delete_widgets

_IGNORE = '{"id": 255, "key": "ignore", "name": "Ignore", "color": "#8a8f98"}'


@pytest.mark.usefixtures("no_prompts")
@pytest.mark.parametrize("raw_id", ["1e999", "true"])
def test_the_class_labels_report_a_bad_class_id_instead_of_crashing(app, tmp_path, raw_id):
    image = tmp_path / "plot_T1_L1_2024.01.01_120000.png"
    cv2.imwrite(str(image), np.full((30, 40, 3), 90, np.uint8))
    (tmp_path / "gt").mkdir()
    (tmp_path / "gt" / "classes.json").write_text(
        f'{{"version": 1, "classes": [{{"id": {raw_id}, "key": "root", "name": "Root", '
        f'"color": "#5fd6a0"}}, {_IGNORE}]}}',
        encoding="utf-8",
    )
    editor = CorrectEditor()
    notices: list[tuple[str, str]] = []
    editor.notice.connect(lambda msg, kind: notices.append((msg, kind)))
    try:
        editor.load_image(str(image), images_base_folder=str(tmp_path))  # used to qFatal
        assert editor.current_image_path == str(image)
        editor.set_active_layer(CLASSES)
        drag(editor, (5, 5), (20, 5))  # the first edit is refused, with the notice users see

        danger = [msg for msg, kind in notices if kind == "danger"]
        assert len(danger) == 1 and "classes[0].id" in danger[0]
        assert "classes[0].id" in editor.layers[CLASSES].status().tooltip  # the row / chip
        assert not editor.has_unsaved_changes()
        assert editor.save() is False  # GT saving is off: nothing is written
        assert not os.path.exists(gt_path(str(image)))
    finally:
        delete_widgets([editor])
