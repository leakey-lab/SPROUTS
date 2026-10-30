"""The Library display stats a photo only when the stamp is used.

Each Library click asks the Correct page's photo source for the photo shown and its two
neighbours. The display took the file's stamp before it looked at the answer, so a source
that held nothing (the usual case in the Library) cost three wasted ``os.stat`` per click.
The stamp is now taken only for a photo the source holds, which is still shared only while
its file is the one decoded.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QComboBox, QStatusBar, QWidget

import app.gui.display_controller as display_module
from app.gui.display_controller import DisplayController
from app.gui.image_manager import ImageManager
from app.io_utils import file_stamp
from tests.qt_teardown import delete_widgets

NAMES = ("A", "B", "C")


class _Window:
    """What DisplayController uses from MainWindow."""

    def __init__(self, image_manager) -> None:
        self.image_manager = image_manager
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["Single Image", "Overlay", "Side by Side"])
        self.status_bar = QStatusBar()

    def notify(self, message, kind="success", timeout=3200) -> None:
        pass


@pytest.fixture
def library(app, tmp_path):
    paths = []
    for i, name in enumerate(NAMES):
        path = str(tmp_path / f"{name}.png")
        cv2.imwrite(path, np.full((24, 32, 3), 40 * (i + 1), np.uint8))
        paths.append(path)
    manager = ImageManager(None)
    manager.original_folder = str(tmp_path)
    manager.images = {name: path for name, path in zip(NAMES, paths, strict=True)}
    window = _Window(manager)
    controller = DisplayController(window)
    host = QWidget()
    controller.setup_display_area(host)
    host.resize(320, 240)
    host.show()
    QApplication.processEvents()

    def ring(path):
        i = paths.index(path)
        return [paths[(i + 1) % 3], paths[(i - 1) % 3]]

    controller.set_neighbour_provider(ring)
    yield controller, paths
    controller.read_ahead.close()
    delete_widgets([host, window.view_mode_combo, window.status_bar])


@pytest.fixture
def stamps(monkeypatch):
    """The paths the display stamped (its own calls, on the GUI thread)."""
    seen: list[str] = []

    def counting(path):
        seen.append(os.path.basename(path))
        return file_stamp(path)

    monkeypatch.setattr(display_module, "file_stamp", counting)
    return seen


def test_a_source_holding_nothing_costs_no_stamp(library, stamps):
    controller, paths = library
    asked: list[str] = []
    controller.set_photo_source(lambda path: asked.append(os.path.basename(path)))

    controller.display_selected_image_by_name("A")

    assert asked == ["A.png", "B.png", "C.png"]  # the photo shown, then its neighbours
    assert stamps == ["A.png"]  # only the decode's own stamp
    assert controller.read_ahead.holds(paths[0])


def test_a_held_photo_is_still_shared_only_while_its_file_is_unchanged(library, stamps):
    controller, paths = library
    held = cv2.cvtColor(cv2.imread(paths[0]), cv2.COLOR_BGR2RGB)
    held.flags.writeable = False
    stamp = file_stamp(paths[0])
    controller.set_photo_source(lambda path: (stamp, held) if path == paths[0] else None)

    controller.display_selected_image_by_name("A")
    assert stamps == ["A.png"]  # the shared photo's stamp, checked; no decode
    assert controller.read_ahead.get(paths[0]) is held

    controller.reset()
    stamps.clear()
    stale = (stamp[0], stamp[1] - 1, stamp[2])  # decoded from an older file
    controller.set_photo_source(lambda path: (stale, held) if path == paths[0] else None)
    controller.display_selected_image_by_name("A")
    assert stamps == ["A.png", "A.png"]  # the check, then the decode's own stamp
    assert controller.read_ahead.get(paths[0]) is not held
