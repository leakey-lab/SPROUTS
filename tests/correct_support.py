"""Helpers of the Correct page tests: photos and projects on disk, the editor factory, the
prompt answers, input on the canvas and what the layers hold.

They are the helpers that ``test_correct_editor.py`` shares with the other Correct, golden
and perf tests. ``conftest.py`` wraps four of them in fixtures (``correct_guards``,
``make_editor``, ``project``, ``answers``). ``install_guards`` is composed from
``tests.guards``; the user's config needs no line here, as every test has its own config
folder (``conftest.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.gui.correct.editor import CorrectEditor
from app.gui.correct.layer import MASK, SKELETON
from app.gui.correct.mask_tools import argb_to_plane, fill_source, mask_lut
from app.gui.paint_canvas import PaintCanvas, pixmap_to_argb_array
from app.gui.patch_history import PatchHistory
from app.gui.widgets import tokens
from tests import guards
from tests.qt_teardown import delete_widgets

W, H = 200, 150  # the photos of the behaviour tests
NO_MODS = Qt.KeyboardModifier.NoModifier
LEFT, NO_BUTTON = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
SAVE = QMessageBox.StandardButton.Save
DISCARD = QMessageBox.StandardButton.Discard
CANCEL = QMessageBox.StandardButton.Cancel  # also "Keep editing" on the failed-save box
UNDER_TEST = os.path.join("correct", "editor.py")
_APP: QApplication | None = None


def qapp() -> QApplication:
    """The one QApplication, kept referenced for the session (icons cache pixmaps)."""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def install_guards(monkeypatch, tmp_path):
    """No unexpected prompt or file dialog, no GL, and no exception of the editor hidden in a
    Qt slot (PyQt hands those to sys.excepthook). ``tmp_path`` is kept for the callers."""
    guards.refuse_prompts(monkeypatch)
    guards.refuse_file_dialogs(monkeypatch)
    guards.gl_off(monkeypatch)
    yield from guards.record_slot_errors(monkeypatch, UNDER_TEST)


def settle(editor=None) -> None:
    """Let posted events run (and place the overlays of ``editor``)."""
    for _ in range(3):
        QApplication.processEvents()
    if editor is not None:
        editor.chrome.relayout()
        QApplication.processEvents()


def editor_factory():
    """``make(image=None, base=None, *, size=(1100, 800), show=True)`` builds a CorrectEditor
    (opened on ``image``); every page made is deleted deterministically afterwards."""
    made: list[CorrectEditor] = []

    def make(image=None, base=None, *, size=(1100, 800), show=True) -> CorrectEditor:
        editor = CorrectEditor()
        editor.resize(*size)
        made.append(editor)
        if show:
            editor.show()
            settle(editor)
        if image is not None:
            editor.load_image(image, base)
            settle(editor)
        return editor

    yield make
    # Delete while the wrappers are intact (host -> layout -> host cycle).
    delete_widgets(made, prepare=lambda editor: editor.layers[SKELETON].cancel_scheduled_render())


@dataclass(frozen=True)
class Project:
    """A project folder: photos ``a`` (model mask + generated skeleton) and ``b`` (neither)."""

    folder: str
    a: str
    b: str

    def mask(self, name: str = "a") -> str:
        """The model's mask (the prediction) of image ``name``."""
        return os.path.join(self.folder, "mask", f"{name}.png")

    def gt_mask(self, name: str = "a") -> str:
        """The saved (GT) mask of image ``name``: what a mask save writes."""
        return os.path.join(self.folder, "gt", "mask", f"{name}.png")

    def correction(self, name: str = "a") -> str:
        """The saved (GT) skeleton of image ``name``: what a skeleton save writes."""
        return os.path.join(self.folder, "gt", "skeletons", f"{name}.png")

    def generated(self, name: str = "a") -> str:
        """The generated skeleton of image ``name``."""
        return os.path.join(self.folder, "skeletons", f"{name}_fake.png")


def box_plane(w: int = W, h: int = H) -> np.ndarray:
    """The model mask of image ``a``: one filled box."""
    plane = np.zeros((h, w), np.uint8)
    plane[30:70, 40:110] = 1
    return plane


def write_image(path: str, w: int = W, h: int = H, value: int = 90) -> str:
    """A flat grey photo."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, np.full((h, w, 3), value, np.uint8))
    return path


def write_mask(path: str, plane: np.ndarray) -> str:
    """A model mask as the generator writes it: 8-bit grey, 255 where ``plane`` is set."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, (plane > 0).astype(np.uint8) * 255)
    return path


def write_skeleton(path: str, row: int = 110, w: int = W, h: int = H) -> str:
    """A skeleton file with one straight line on ``row`` (20 px in from both sides)."""
    raster = np.zeros((h, w), np.uint8)
    raster[row, 20 : w - 20] = 255
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, raster)
    return path


def bump(path: str) -> None:
    """Move the file's modification time on (as a later write would)."""
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))


def new_project(tmp_path) -> Project:
    """Photos a.png (with a model mask and a generated skeleton) and b.png (without)."""
    folder = str(tmp_path / "project")
    project = Project(
        folder,
        write_image(os.path.join(folder, "a.png")),
        write_image(os.path.join(folder, "b.png"), value=60),
    )
    write_mask(project.mask(), box_plane())
    write_skeleton(project.generated())
    return project


class Answers:
    """The unsaved-changes boxes shown, as ``(title, text)``; ``will`` queues the buttons."""

    def __init__(self) -> None:
        self.queue: list = []
        self.seen: list[tuple[str, str]] = []

    def will(self, *buttons) -> Answers:
        """Answer the next boxes with ``buttons``, in order."""
        self.queue.extend(buttons)
        return self


def answer_prompts(monkeypatch) -> Answers:
    """Replace ``QMessageBox.exec``: each box is recorded and answered from the queue."""
    answers = Answers()

    def exec_(box):
        answers.seen.append((box.windowTitle(), box.text()))
        if not answers.queue:
            pytest.fail(f"unexpected prompt {box.text()!r}")
        return answers.queue.pop(0)

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return answers


class Mouse:
    """The part of a mouse event the canvas router reads: its keyboard modifiers."""

    def __init__(self, modifiers=NO_MODS) -> None:
        self._modifiers = modifiers

    def modifiers(self):
        """The keyboard modifiers of the event."""
        return self._modifiers


def drag(editor, *points, modifiers=NO_MODS) -> None:
    """A left drag on the canvas input (the router): press, a dragging move per point, release."""
    router, event = editor.router, Mouse(modifiers)
    first, *rest = [QPoint(*p) for p in points]
    router.canvas_press(first, event)
    for point in rest:
        router.canvas_move(point, event, True)
    router.canvas_release(rest[-1] if rest else first, event)


def click(editor, point, modifiers=NO_MODS) -> None:
    """A left click at image pixel ``point``."""
    drag(editor, point, modifiers=modifiers)


def paint(editor, *points) -> None:
    """A stroke of the mask's current tool (the mask becomes the edited layer)."""
    editor.set_active_layer(MASK)
    drag(editor, *points)


def draw_line(editor, start=(40, 125), end=(160, 135)) -> None:
    """A two-point skeleton polyline, finished with the prompt's button (one action)."""
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, start)
    click(editor, end)
    editor.controls.finish_button.click()


def plane(editor) -> np.ndarray:
    """The mask plane (0 / 1) of the open image."""
    return editor.layers[MASK].document.history.plane


def raster(editor) -> np.ndarray:
    """The skeleton raster (0 / 255) of the open image."""
    return editor.layers[SKELETON].document.model.mask


def alpha(editor) -> np.ndarray:
    """The alpha of the mask canvas as drawn."""
    return pixmap_to_argb_array(editor.layers[MASK].canvas.image)[:, :, 3]


def stroke_plane(base: np.ndarray, points, size: int = 8, *, erase: bool = False) -> np.ndarray:
    """What today's painting makes of ``base`` after one stroke (a PaintCanvas replay)."""
    history = PatchHistory()
    history.reset(base.copy())
    canvas = PaintCanvas(
        history,
        lut=mask_lut,
        to_plane=argb_to_plane,
        fill_source=fill_source,
        on_region=lambda rect: None,
        on_replaced=lambda: None,
    )
    canvas.rebuild()
    canvas.begin(QPoint(*points[0]), QColor(tokens.MASK), size, erase=erase)
    for point in points[1:]:
        canvas.extend(QPoint(*point))
    canvas.commit()
    return history.plane.copy()


def record(signal) -> list:
    """Every emission of ``signal``: its argument, or the tuple of its arguments."""
    seen: list = []
    signal.connect(lambda *args: seen.append(args[0] if len(args) == 1 else args))
    return seen


def activate(editor) -> None:
    """Show ``editor`` in the active window with the keyboard focus on its canvas."""
    editor.show()
    editor.activateWindow()
    assert QTest.qWaitForWindowActive(editor, 3000)
    editor.graphics_view.setFocus()
    settle(editor)


def device_pos(view, x: int, y: int) -> QPointF:
    """A viewport position the canvas maps to image pixel ``(x, y)`` (it floors positions)."""
    centre = view.viewportTransform().map(QPointF(x + 0.5, y + 0.5))
    for dx in (0, 1, -1, 2, -2):
        for dy in (0, 1, -1, 2, -2):
            pos = QPointF(round(centre.x()) + dx, round(centre.y()) + dy)
            if view.map_to_image(pos) == QPoint(x, y):
                return pos
    raise AssertionError(f"no viewport position shows pixel {(x, y)}")


def mouse(view, kind, xy, button=LEFT, buttons=LEFT, modifiers=NO_MODS) -> None:
    """A real mouse event on the canvas at image pixel ``xy``."""
    viewport = view.viewport()
    pos = device_pos(view, *xy)
    glob = QPointF(viewport.mapToGlobal(pos.toPoint()))
    QApplication.sendEvent(viewport, QMouseEvent(kind, pos, glob, button, buttons, modifiers))


def view_drag(view, points, *, release: bool = True) -> None:
    """A left drag of real mouse events through the canvas view."""
    first, *rest = points
    mouse(view, QEvent.Type.MouseButtonPress, first)
    for point in rest:
        mouse(view, QEvent.Type.MouseMove, point, NO_BUTTON, LEFT)
    if release:
        mouse(view, QEvent.Type.MouseButtonRelease, rest[-1] if rest else first, LEFT, NO_BUTTON)


def listing(folder: str) -> dict[str, tuple[int, int]]:
    """Every file under ``folder``: relative path -> (mtime_ns, size)."""
    files = {}
    for root, _dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            stat = os.stat(path)
            files[os.path.relpath(path, folder)] = (stat.st_mtime_ns, stat.st_size)
    return files


def same_path(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` name the same file."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def drawn_in(button, colour: str) -> bool:
    """Every well-covered pixel of ``button``'s icon is ``colour`` (within antialiasing)."""
    target = QColor(colour)
    image = button.icon().pixmap(button.iconSize(), 1.0).toImage()
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    found = [
        QColor.fromRgba(image.pixel(x, y))
        for y in range(image.height())
        for x in range(image.width())
        if QColor.fromRgba(image.pixel(x, y)).alpha() >= 200
    ]
    return bool(found) and all(
        max(
            abs(c.red() - target.red()),
            abs(c.green() - target.green()),
            abs(c.blue() - target.blue()),
        )
        <= 2
        for c in found
    )


def grab(view) -> np.ndarray:
    """RGB of what the canvas shows now."""
    view.viewport().repaint()
    QApplication.processEvents()
    image = view.viewport().grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, : image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def count_opens(editor, monkeypatch) -> list[str]:
    """Record every (re)open of a layer's document."""
    opened: list[str] = []
    for layer_id, layer in editor.layers.items():
        real = layer.open

        def spy(*args, _real=real, _id=layer_id):
            opened.append(_id)
            return _real(*args)

        monkeypatch.setattr(layer, "open", spy)
    return opened
