"""A hand-broken ``gt/classes.json`` must never abort the app (RB-09).

``"id": 1e999`` parses to ``float('inf')``; ``int()`` on it raised OverflowError
inside ``GtTracingInterface.load_image`` (called from the Library's itemClicked
slot, where PyQt6 turns an escaping exception into qFatal). ``"id": true`` was
silently accepted as id 1. Both must now surface as an invalid-registry notice
naming the bad field, with GT saving switched off.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.gt import gt_path  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402

_IGNORE = '{"id": 255, "key": "ignore", "name": "Ignore", "color": "#8a8f98"}'


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("raw_id", ["1e999", "true"])
def test_gt_editor_reports_a_bad_class_id_instead_of_crashing(app, tmp_path, monkeypatch, raw_id):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("unexpected modal dialog"))
    image = tmp_path / "plot_T1_L1_2024.01.01_120000.png"
    cv2.imwrite(str(image), np.full((30, 40, 3), 90, np.uint8))
    (tmp_path / "gt").mkdir()
    (tmp_path / "gt" / "classes.json").write_text(
        f'{{"version": 1, "classes": [{{"id": {raw_id}, "key": "root", "name": "Root", '
        f'"color": "#5fd6a0"}}, {_IGNORE}]}}',
        encoding="utf-8",
    )
    notices: list[tuple[str, str]] = []
    editor = GtTracingInterface()
    editor.notice.connect(lambda msg, kind: notices.append((msg, kind)))

    editor.load_image(str(image))

    assert any(kind == "danger" and "classes[0].id" in msg for msg, kind in notices)
    assert editor.save_mask() is False
    assert not os.path.exists(gt_path(str(image)))
