"""Trace GT: clicking the open image in the library gives the keys back to the canvas.

A click on an image row in the library takes the keyboard focus to the tree; opening an
image then hands it to the canvas, so B / E / F, the digit class keys and Ctrl+Z work at
once. Since the open image is kept when it is selected again (same file, unchanged on disk,
2026-09-30), that path returned before the hand-over: the keys stayed on the tree, where a
digit jumps the selection by type-ahead instead of picking a class (walkthrough W1 shape).
"""

import gc
import os
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402

H, W = 60, 80


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    """No modal prompt, no GL probing, no write to the real user config."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


def _flush_deleted() -> None:
    """Delete what deleteLater() queued, then deliver the queued calls that free the signal
    proxies of deleted widgets, and collect: three rounds, as one round can queue more."""
    for _ in range(3):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.sendPostedEvents()
    gc.collect()


@pytest.fixture(scope="module", autouse=True)
def _flush_earlier_tests(app):
    """Dead wrappers that earlier test files left queued go first."""
    _flush_deleted()


@pytest.fixture
def window(app, tmp_path):
    """A shown, active window: a stand-in for the library tree next to the GT editor
    (deleted afterwards with every dead wrapper: see tests/test_gt_tracing_interface.py)."""
    host = QWidget()
    host.resize(1100, 700)
    row = QHBoxLayout(host)
    library = QLineEdit("library")
    iface = GtTracingInterface()
    row.addWidget(library)
    row.addWidget(iface, 1)
    path = str(tmp_path / "plot_001.png")
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    host.show()
    host.activateWindow()
    assert QTest.qWaitForWindowActive(host, 3000)
    iface.load_image(path)
    # Weak proxies: pytest keeps a fixture's value until after its teardown, and a held
    # wrapper would outlive the flush below.
    yield weakref.proxy(library), weakref.proxy(iface), path
    host.close()
    host.deleteLater()
    del host, row, library, iface
    _flush_deleted()


@pytest.mark.parametrize("dirty", [False, True], ids=["clean", "unsaved"])
def test_selecting_the_open_image_again_gives_the_canvas_the_keys(window, dirty):
    library, iface, path = window
    view = iface.graphics_view
    iface.begin_stroke(QPoint(20, 20))
    iface.end_stroke()
    if not dirty:
        assert iface.save_mask()
    history = len(iface.undo_stack)
    library.setFocus()
    QApplication.processEvents()
    assert library.hasFocus()
    transform = view.transform()

    iface.load_image(path)  # the library row of the open image was clicked
    QApplication.processEvents()

    assert QApplication.focusWidget() is view
    # Still the same document and view: the image was not reopened.
    assert len(iface.undo_stack) == history == 1
    assert iface.has_unsaved_changes() is dirty
    assert view.transform() == transform


def test_a_hidden_editor_leaves_the_focus_alone(window):
    library, iface, path = window
    iface.hide()
    library.setFocus()
    QApplication.processEvents()

    iface.load_image(path)

    assert library.hasFocus()
