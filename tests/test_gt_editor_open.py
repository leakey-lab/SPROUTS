"""Trace GT: opening an image (G3 speed, G4 bug), like the Correct editor's open.

- The open image again, clean and unchanged on disk: the tracing, its history and the view
  stay (entering the stage re-selects the open image; it re-read the GT file, rebuilt the
  canvas and refitted the view every time: about 50 ms at 2048 x 1536).
- Its GT file changed on disk: the tracing reloads, same view. The photo changed: a new open,
  fitted. An invalid gt/classes.json is re-read each time (a fixed file takes effect).
- Another image is decoded before the unsaved-changes prompt: an unreadable one leaves the
  open tracing alone. Before, the prompt came first, so Save / Discard ran for an image that
  then failed to open (G4: e.g. Discard dropped the edits and the editor stayed put).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import app.gui.gt_tracing_interface as gti  # noqa: E402
from app.gt import Tracing, default_registry, gt_path, save_registry, write_tracing  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

H, W = 60, 80


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def editor(app):
    made = []

    def _make(image_path=None):
        iface = GtTracingInterface()
        made.append(iface)
        iface.resize(900, 700)
        if image_path:
            iface.load_image(image_path)
        return iface

    yield _make
    delete_widgets(made)


def _photo(folder, name="plot_001.png", value=90, size=(H, W)):
    path = str(folder / name)
    cv2.imwrite(path, np.full((*size, 3), value, np.uint8))
    return path


def _stroke(iface, *points, size=6):
    iface.size_slider.setValue(size)
    iface.begin_stroke(QPoint(*points[0]))
    for p in points[1:]:
        iface.continue_stroke(QPoint(*p))
    iface.end_stroke()


def _shown(iface):
    """Laid out, so the view can fit (a hidden view fits on its first resize)."""
    iface.show()
    QApplication.processEvents()
    return iface.graphics_view


def _bump_mtime(path, seconds=10):
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


@pytest.fixture
def reads(monkeypatch):
    """Count the GT file reads of the editor."""
    calls = []
    real = gti.read_tracing

    def _read(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(gti, "read_tracing", _read)
    return calls


def test_reselecting_the_clean_open_image_keeps_tracing_history_and_view(editor, tmp_path, reads):
    path = _photo(tmp_path)
    iface = editor(path)
    _stroke(iface, (10, 10), (40, 10))
    assert iface.save_mask()
    _stroke(iface, (10, 40), (40, 40))
    iface.undo()  # back at the saved tracing; save anyway: one undo and one redo left
    assert iface.save_mask()
    view = _shown(iface)
    view.set_zoom(4.0)
    view.centerOn(20, 20)
    zoom, center = view.zoom_factor, view.mapToScene(view.viewport().rect().center())
    reads.clear()

    iface.load_image(path)

    assert reads == []  # the GT file was not read again
    assert iface.edits.can_undo and iface.edits.can_redo  # the history stayed
    assert view.zoom_factor == zoom
    assert view.mapToScene(view.viewport().rect().center()) == center
    assert not iface.has_unsaved_changes()


def test_a_gt_file_changed_on_disk_reloads_in_the_same_view(editor, tmp_path, reads):
    path = _photo(tmp_path)
    iface = editor(path)
    _stroke(iface, (10, 10), (40, 10))
    assert iface.save_mask()
    view = _shown(iface)
    view.set_zoom(3.0)
    zoom = view.zoom_factor

    ids = np.zeros((H, W), np.uint8)
    ids[30:40, 30:40] = 2  # someone else's tracing
    write_tracing(gt_path(path), Tracing(ids, frozenset({"nodule"})), default_registry())
    _bump_mtime(gt_path(path))
    reads.clear()
    iface.load_image(path)

    assert reads == [gt_path(path)]
    assert np.array_equal(iface.edits.plane, ids)
    assert np.array_equal(iface._pixmap_to_ids(), ids)
    assert iface._reviewed == {"nodule"} and iface._present == {"nodule"}
    assert not iface.edits.can_undo  # a new document, a new history
    assert view.zoom_factor == zoom  # same view
    assert not iface.has_unsaved_changes()

    os.remove(gt_path(path))  # the file went away: an empty tracing, "No GT"
    iface.load_image(path)
    assert not iface.edits.plane.any() and "No GT" in iface.state_label.text()


def test_a_changed_photo_opens_again_fitted(editor, tmp_path):
    path = _photo(tmp_path)
    iface = editor(path)
    view = _shown(iface)
    view.set_zoom(1.5)
    _photo(tmp_path, value=200, size=(H * 2, W * 2))  # same name, new content and size
    iface.load_image(path)
    assert iface.canvas_size().width() == W * 2 and iface.canvas_size().height() == H * 2
    zoom = view.zoom_factor
    assert zoom != pytest.approx(1.5)  # not the view of before
    view.fit_to_view()
    assert view.zoom_factor == pytest.approx(zoom)  # it was fitted already


def test_an_invalid_registry_is_read_again_on_reselect(editor, tmp_path):
    (tmp_path / "gt").mkdir()
    (tmp_path / "gt" / "classes.json").write_text("{", encoding="utf-8")
    path = _photo(tmp_path)
    iface = editor(path)
    assert iface.save_mask() is False
    save_registry(str(tmp_path), default_registry())  # fixed (the stamp may not change)
    iface.load_image(path)
    assert iface.save_mask() is True


def test_an_unreadable_image_leaves_unsaved_edits_alone(editor, tmp_path):
    path = _photo(tmp_path)
    bad = str(tmp_path / "plot_002.png")
    with open(bad, "wb") as fh:
        fh.write(b"not an image")
    notices = []
    iface = editor(path)
    iface.notice.connect(lambda msg, kind: notices.append((msg, kind)))
    _stroke(iface, (10, 10), (40, 10))

    iface.load_image(bad)  # the autouse guard fails on any prompt

    assert ("Could not open plot_002.png", "danger") in notices
    assert iface.current_image_path == path and iface.has_unsaved_changes()
    assert iface.edits.plane[10, 25] == 1


def test_saving_does_not_count_as_a_change_on_disk(editor, tmp_path, reads):
    """The editor's own save re-stamps the file: the next re-select reads nothing."""
    path = _photo(tmp_path)
    iface = editor(path)
    _stroke(iface, (10, 10), (40, 10))
    assert iface.save_mask()
    reads.clear()
    iface.load_image(path)
    assert reads == [] and iface.edits.can_undo
