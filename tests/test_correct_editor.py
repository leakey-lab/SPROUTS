"""CorrectEditor (plan P7b, contract C17): the Correct page, one canvas for the mask and the
skeleton of an image.

The editor only composes the parts: the canvas (``EditorCanvasView``, input to the
``ToolRouter``), the photo, the ``MaskLayer`` and the ``SkeletonLayer``, the
``CorrectSession``, the ``EditorChrome``, the ``CorrectControls`` and the Layers panel. These
tests drive the page on the real parts, as MainWindow and the user do: opening and re-entry
(spec 9), the two layers (spec 4), the controls and the chip (spec 3.2), saving, the save
reports and the one prompt (spec 8), paging, Enhance, focus, overlays and zoom quality (spec
10). They also carry the behaviours of the old editors' tests that the swap deletes
(``test_mask_tracing_interface_smoke``, ``test_skeleton_correction_interface_smoke``,
``test_skeleton_editor_lifecycle``, ``test_skeleton_editor_tools``, the mask cases of
``test_mask_editor_behaviour`` and GT's ``test_mask_mode_unchanged``). The keys are in
``test_correct_editor_keys.py``, undo and gestures in ``test_correct_editor_history.py``;
the helpers below are shared with them and with the golden and perf tests.
"""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from app.gui import canvas_gl, shortcuts  # noqa: E402
from app.gui.correct.editor import CorrectEditor  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.correct.mask_layer import MaskLayer  # noqa: E402
from app.gui.correct.mask_tools import argb_to_plane, fill_source, mask_lut  # noqa: E402
from app.gui.correct.skeleton_layer import DraftState, SkeletonLayer  # noqa: E402
from app.gui.editor_canvas import EditorCanvasView  # noqa: E402
from app.gui.paint_canvas import PaintCanvas, pixmap_to_argb_array  # noqa: E402
from app.gui.patch_history import PatchHistory  # noqa: E402
from app.gui.skeleton_graph_model import SkeletonCorrectionModel  # noqa: E402
from app.gui.widgets import (  # noqa: E402
    EnhancePopover,
    FloatingDock,
    ToolRail,
    ZoomCluster,
    tokens,
)
from app.project_layout import ProjectLayout  # noqa: E402
from tests.correct_goldens_support import png_colour_type  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402
from tests.render_metrics import equal_neighbour_fraction, sine_image, sine_step_ratio  # noqa: E402

W, H = 200, 150  # the photos of the behaviour tests
NO_MODS = Qt.KeyboardModifier.NoModifier
LEFT, NO_BUTTON = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
SAVE = QMessageBox.StandardButton.Save
DISCARD = QMessageBox.StandardButton.Discard
CANCEL = QMessageBox.StandardButton.Cancel  # also "Keep editing" on the failed-save box
UNDER_TEST = os.path.join("correct", "editor.py")
_APP: QApplication | None = None


# ============================================================================ shared helpers
def qapp() -> QApplication:
    """The one QApplication, kept referenced for the session (icons cache pixmaps)."""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _no_file_dialog(*_args, **_kwargs):
    pytest.fail("a file dialog was opened")


def install_guards(monkeypatch, tmp_path):
    """No unexpected prompt or file dialog, no GL, the user's config untouched, and no
    exception of the editor hidden in a Qt slot (PyQt hands those to sys.excepthook)."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", _no_file_dialog)
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    errors = []

    def hook(kind, value, tb):
        files = [os.path.normcase(frame.filename) for frame in traceback.extract_tb(tb)]
        if any(name.endswith(os.path.normcase(UNDER_TEST)) for name in files):
            errors.append(value)
        else:
            sys.__excepthook__(kind, value, tb)

    monkeypatch.setattr(sys, "excepthook", hook)
    yield
    assert errors == []


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
        """The skeleton correction of image ``name``."""
        return os.path.join(self.folder, "skeleton_corrections", f"{name}_skeleton.png")

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


# ================================================================================= fixtures
@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


@pytest.fixture
def project(tmp_path) -> Project:
    return new_project(tmp_path)


@pytest.fixture
def answers(monkeypatch) -> Answers:
    return answer_prompts(monkeypatch)


# ======================================================================= the page and parts
SIGNALS = {
    "notice": ("Saved", "success"),
    "dirty_changed": (True,),
    "mask_saved": ("a.png",),
    "mask_cleared": ("a.png",),
    "skeleton_saved": ("a_skeleton.png",),
    "b_key_status_changed": (True,),
    "navigate_requested": (1,),
    "active_layer_changed": ("skeleton",),
}


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_the_page_has_the_signals_mainwindow_connects(make_editor, name):
    editor = make_editor(show=False)
    seen = []

    assert isinstance(getattr(CorrectEditor, name), pyqtSignal)
    getattr(editor, name).connect(lambda *args: seen.append(args))
    getattr(editor, name).emit(*SIGNALS[name])
    assert seen == [SIGNALS[name]]


def test_the_page_names_the_parts_mainwindow_and_the_shell_use(make_editor):
    editor = make_editor(show=False)
    chrome, controls = editor.chrome, editor.controls

    assert editor.EDITOR_NOUN == "mask and skeleton"
    assert editor.SHORTCUT_SCOPE == shortcuts.CORRECT
    assert isinstance(editor.graphics_view, EditorCanvasView)
    assert editor.scene.parent() is editor.graphics_view  # a parented scene
    assert isinstance(editor.dock, FloatingDock) and editor.dock is chrome.dock
    assert isinstance(editor.tool_rail, ToolRail) and editor.tool_rail is chrome.rail
    assert isinstance(editor.enhance_popover, EnhancePopover)
    assert editor.enhance_popover is chrome.popover
    assert isinstance(editor.zoom_cluster, ZoomCluster) and editor.zoom_cluster is chrome.zoom
    assert editor.status_chip is chrome.chip
    assert editor.clear_button is controls.clear_button
    for name in ("save_button", "undo_button", "redo_button"):
        assert getattr(editor, name) is getattr(controls, name)
    assert isinstance(editor.layers[MASK], MaskLayer)
    assert isinstance(editor.layers[SKELETON], SkeletonLayer)
    assert list(editor.layers) == [MASK, SKELETON] and editor.session.layers == editor.layers
    assert set(editor.layer_panel.rows) == {MASK, SKELETON}
    assert editor.photo_item.scene() is editor.scene and editor.photo_item.zValue() == 0
    for layer in editor.layers.values():
        assert layer.item.scene() is editor.scene


RAIL_TIPS = {
    (MASK, "brush"): "Brush (B) · hold B + scroll to resize",
    (MASK, "eraser"): "Mask eraser (E) · [ and ] resize",
    (MASK, "fill"): "Fill a closed outline (F)",
    (SKELETON, "select"): "Select (V) · pick a branch, Delete removes it",
    (SKELETON, "eraser"): "Skeleton eraser (E) · [ and ] resize",
    (SKELETON, "polyline"): "Polyline (P) · Enter finish · Esc cancel",
    (SKELETON, "connect"): "Connect endpoints (C)",
}


def test_the_rail_and_the_dock_name_each_tool_with_its_key(make_editor):
    editor = make_editor(show=False)

    assert {key: b.toolTip() for key, b in editor.controls.tool_buttons.items()} == RAIL_TIPS
    assert "Space" in editor.chrome.pan_button.toolTip()
    assert "(S)" in editor.controls.smooth_button.toolTip()
    assert "Ctrl+Y" in editor.redo_button.toolTip() and "Ctrl+Z" in editor.undo_button.toolTip()
    assert editor.enhance_button is editor.chrome.enhance_button
    assert editor.pan_button is editor.chrome.pan_button


def test_a_new_page_has_no_image_and_edits_the_mask(make_editor):
    editor = make_editor()

    assert editor.current_image_path == "" and editor.active_layer_id == MASK
    assert editor.layers[MASK].current_tool == "brush"
    assert editor.layers[SKELETON].current_tool == "select"
    assert editor.status_chip.text() == "No image" and editor.tool_rail.width() <= 60
    assert not editor.has_unsaved_changes() and editor.maybe_save_changes() is True
    assert editor.save() is False  # nothing to save
    editor.undo()
    editor.redo()
    drag(editor, (10, 10), (40, 30))  # nothing open: no input is taken
    assert editor.session.journal.undo_entries == ()


# ================================================================================ opening
def test_load_image_opens_both_layers_and_fits_the_photo(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view = editor.graphics_view

    assert same_path(editor.current_image_path, project.a)
    assert np.array_equal(plane(editor), box_plane())
    assert editor.layers[SKELETON].document.source == "generated"
    assert raster(editor)[110, 20:180].all()
    assert editor.photo_item.image_size() == (W, H)
    assert editor.status_chip.text() == "a.png" and not editor.has_unsaved_changes()
    fitted = view.zoom_factor
    assert fitted > 2.0  # a 200 px photo in a 1100 px page
    view.fit_to_view()
    assert view.zoom_factor == pytest.approx(fitted)


def test_load_image_decodes_the_photo_once(make_editor, project, monkeypatch):
    import app.enhancement as enhancement

    real, decoded = enhancement.imread_unicode, []

    def spy(path, *args, **kwargs):
        decoded.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(enhancement, "imread_unicode", spy)
    editor = make_editor(project.a, project.folder)
    assert len(decoded) == 1 and same_path(decoded[0], project.a)

    editor.apply_normalization()  # the Enhance render reuses the decoded photo
    editor.load_image(project.b, project.folder)
    assert len(decoded) == 2 and same_path(decoded[1], project.b)


def test_an_undecodable_image_is_reported_and_nothing_changes(make_editor, project, tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))  # unsaved: yet no prompt for an image that cannot open
    edited = plane(editor).copy()
    notices = record(editor.notice)

    editor.load_image(str(bad), str(tmp_path))

    assert notices == [("Could not open bad.png", "danger")]
    assert same_path(editor.current_image_path, project.a)
    assert np.array_equal(plane(editor), edited) and editor.has_unsaved_changes()


def test_an_undecodable_first_image_leaves_the_page_empty(make_editor, tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    editor = make_editor()
    notices = record(editor.notice)

    editor.load_image(str(bad), str(tmp_path))

    assert [kind for _message, kind in notices] == ["danger"]
    assert editor.current_image_path == "" and not editor.layers[MASK].is_open()


def test_the_generated_skeleton_opens_without_a_dialog(make_editor, project):
    editor = make_editor(project.a, project.folder)  # the guard fails on any file dialog

    assert editor.layers[SKELETON].document.source == "generated" and raster(editor).any()
    assert not editor.has_unsaved_changes()


def test_the_saved_correction_wins_over_the_generated_skeleton(make_editor, project):
    write_skeleton(project.correction(), row=40)
    editor = make_editor(project.a, project.folder)

    assert editor.layers[SKELETON].document.source == "corrected"
    assert raster(editor)[40].any() and not raster(editor)[110].any()


def test_the_injected_resolver_finds_the_generated_skeleton(make_editor, project):
    elsewhere = write_skeleton(os.path.join(project.folder, "out", "b_fake.png"), row=60)
    editor = make_editor()
    editor.set_generated_skeleton_resolver(lambda image_path: elsewhere)

    editor.load_image(project.b, project.folder)

    assert editor.layers[SKELETON].document.source == "generated" and raster(editor)[60].any()


def test_an_image_without_mask_or_skeleton_says_so(make_editor, project):
    editor = make_editor(project.b, project.folder)
    rows = editor.layer_panel.rows

    assert rows[MASK].state_label.text() == "No mask"
    assert rows[SKELETON].state_label.text() == "No skeleton"
    editor.set_active_layer(SKELETON)
    assert "No skeleton yet" in editor.status_chip.hint_label.text()


# ================================================================================= re-entry
def centre_of(view) -> QPointF:
    return view.mapToScene(view.viewport().rect().center())


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


def test_the_same_image_with_unsaved_edits_keeps_them_and_their_history(make_editor, project):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))
    edited = plane(editor).copy()

    editor.load_image(project.a, project.folder)  # the guard fails on any prompt

    assert np.array_equal(plane(editor), edited) and editor.session.can_undo()


def test_the_same_image_with_unsaved_edits_is_kept_when_its_files_change(make_editor, project):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))
    edited = plane(editor).copy()
    write_skeleton(project.generated(), row=30)  # a Generate run meanwhile
    bump(project.generated())

    editor.load_image(project.a, project.folder)

    assert np.array_equal(plane(editor), edited) and editor.session.can_undo()
    assert raster(editor)[110].any() and not raster(editor)[30].any()


def test_the_same_clean_image_keeps_documents_history_and_view(make_editor, project, monkeypatch):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))
    editor.undo()  # clean again, with a step to redo
    view = editor.graphics_view
    view.set_zoom(3.0)
    view.centerOn(QPointF(50, 40))
    centre = centre_of(view)
    opened = count_opens(editor, monkeypatch)

    editor.load_image(project.a, project.folder)

    assert opened == [] and editor.session.can_redo()
    assert view.zoom_factor == pytest.approx(3.0) and centre_of(view) == centre


def test_a_changed_mask_file_reloads_the_documents_in_the_same_view(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view = editor.graphics_view
    view.set_zoom(3.0)
    view.centerOn(QPointF(50, 40))
    centre = centre_of(view)
    other = np.zeros((H, W), np.uint8)
    other[5:25, 5:45] = 1
    write_mask(project.mask(), other)
    bump(project.mask())

    editor.load_image(project.a, project.folder)

    assert np.array_equal(plane(editor), other) and np.array_equal(alpha(editor) > 0, other > 0)
    assert view.zoom_factor == pytest.approx(3.0) and centre_of(view) == centre


def test_a_rewritten_generated_skeleton_reloads_the_skeleton(make_editor, project):
    editor = make_editor(project.a, project.folder)
    write_skeleton(project.generated(), row=30)  # a Generate run
    bump(project.generated())

    editor.load_image(project.a, project.folder)

    assert raster(editor)[30].any() and not raster(editor)[110].any()


def test_a_changed_photo_opens_fitted_again(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view = editor.graphics_view
    view.set_zoom(7.0)
    write_image(project.a, value=120)
    bump(project.a)

    editor.load_image(project.a, project.folder)

    fitted = view.zoom_factor
    assert fitted != pytest.approx(7.0)
    view.fit_to_view()
    assert view.zoom_factor == pytest.approx(fitted)


def test_another_image_opens_fitted(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view = editor.graphics_view
    fitted = view.zoom_factor
    view.set_zoom(7.0)

    editor.load_image(project.b, project.folder)

    assert view.zoom_factor == pytest.approx(fitted, rel=0.05)
    assert editor.status_chip.text() == "b.png"


def test_re_entry_after_our_own_skeleton_save_reloads_the_saved_correction(make_editor, project):
    # A generated skeleton at half size: the correction is saved at image resolution, so
    # re-entry reads back exactly the raster that was in memory (Q1 fixed).
    write_skeleton(project.generated(), row=55, w=W // 2, h=H // 2)
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    assert editor.save()
    in_memory = raster(editor).copy()

    editor.load_image(project.a, project.folder)  # leaving and re-entering Correct

    saved = cv2.imread(project.correction(), cv2.IMREAD_GRAYSCALE)
    fresh = SkeletonCorrectionModel()
    fresh.load_from_raster(saved, target_size=(W, H))
    assert np.array_equal(raster(editor), fresh.mask)
    assert np.array_equal(raster(editor), in_memory)
    assert editor.layers[SKELETON].document.source == "corrected"


# ============================================================================ the two layers
def test_set_active_layer_switches_the_edited_layer_once(make_editor, project):
    editor = make_editor(project.a, project.folder)
    switched = record(editor.active_layer_changed)

    assert editor.set_active_layer(SKELETON) is True
    assert editor.set_active_layer(SKELETON) is False
    assert switched == [SKELETON] and editor.active_layer_id == SKELETON
    assert editor.layers[SKELETON].is_active() and not editor.layers[MASK].is_active()
    rows = editor.layer_panel.rows
    assert rows[SKELETON].is_active() and not rows[MASK].is_active()


def test_a_rail_tool_of_the_other_layer_switches_to_it_with_that_tool(make_editor, project):
    editor = make_editor(project.a, project.folder)
    buttons = editor.controls.tool_buttons

    buttons[(SKELETON, "connect")].click()
    assert (editor.active_layer_id, editor.layers[SKELETON].current_tool) == (SKELETON, "connect")
    buttons[(MASK, "fill")].click()
    assert (editor.active_layer_id, editor.layers[MASK].current_tool) == (MASK, "fill")
    assert buttons[(MASK, "fill")].isChecked()


def test_a_click_on_a_layers_row_edits_that_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)

    QTest.mouseClick(editor.layer_panel.rows[SKELETON], LEFT)

    assert editor.active_layer_id == SKELETON
    assert editor.layer_panel.rows[SKELETON].is_active()


def test_a_switch_mid_drag_commits_the_stroke_and_the_rest_paints_nothing(make_editor, project):
    editor = make_editor(project.b, project.folder)
    router, event = editor.router, Mouse()
    router.canvas_press(QPoint(20, 20), event)
    router.canvas_move(QPoint(60, 20), event, True)

    QTest.mouseClick(editor.layer_panel.rows[SKELETON], LEFT)  # the skeleton row, mid-drag
    router.canvas_move(QPoint(60, 90), event, True)
    router.canvas_release(QPoint(60, 90), event)

    assert np.array_equal(
        plane(editor), stroke_plane(np.zeros((H, W), np.uint8), [(20, 20), (60, 20)])
    )
    assert editor.session.journal.undo_entries == (MASK,)
    assert not raster(editor).any()


def test_a_polyline_draft_survives_a_visit_to_the_mask(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton = editor.layers[SKELETON]
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, (40, 125))
    click(editor, (100, 130))

    editor.set_active_layer(MASK)
    assert skeleton.draft_state() == DraftState(True, 2, False)
    assert not skeleton.decorations.polyline_item.isVisible()
    assert editor.controls.prompt.isHidden()

    editor.set_active_layer(SKELETON)
    assert skeleton.decorations.polyline_item.isVisible()
    assert not editor.controls.prompt.isHidden()
    click(editor, (160, 135))
    editor.controls.finish_button.click()
    assert editor.session.journal.undo_entries == (SKELETON,)
    assert skeleton.draft_state().points == 0


def test_the_tool_of_the_edited_layer_asked_for_again_starts_over(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton, polyline = (
        editor.layers[SKELETON],
        editor.controls.tool_buttons[(SKELETON, "polyline")],
    )
    polyline.click()
    click(editor, (40, 125))
    click(editor, (100, 130))

    polyline.click()  # as today's rail and P key: the tool is taken again, the draft goes

    assert skeleton.draft_state() == DraftState(True, 0, False) and polyline.isChecked()
    assert editor.session.journal.undo_entries == ()


def test_the_other_layer_is_drawn_at_half_its_opacity(make_editor, project):
    editor = make_editor(project.a, project.folder)
    mask, skeleton = editor.layers[MASK].item, editor.layers[SKELETON].item

    assert (mask.opacity(), skeleton.opacity()) == pytest.approx((0.60, 0.425))
    editor.set_active_layer(SKELETON)
    assert (mask.opacity(), skeleton.opacity()) == pytest.approx((0.30, 0.85))


def test_a_layers_eye_hides_and_shows_its_layer_without_activating_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton, eye = editor.layers[SKELETON], editor.layer_panel.rows[SKELETON].eye_button

    eye.click()
    assert not skeleton.is_visible() and not skeleton.item.isVisible()
    assert editor.active_layer_id == MASK
    eye.click()
    assert skeleton.is_visible() and skeleton.item.isVisible()


def test_editing_a_hidden_active_layer_shows_it_first_with_a_toast(make_editor, project):
    editor = make_editor(project.a, project.folder)
    mask = editor.layers[MASK]
    editor.layer_panel.rows[MASK].eye_button.click()
    assert not mask.is_visible()
    notices = record(editor.notice)

    paint(editor, (20, 100), (60, 100))

    assert mask.is_visible() and mask.item.isVisible()
    assert editor.layer_panel.rows[MASK].eye_button.isChecked()
    assert notices == [("Mask shown", "info")] and plane(editor)[100, 40] == 1


def test_clearing_a_hidden_layer_shows_it_first(make_editor, project):
    editor = make_editor(project.a, project.folder)
    mask = editor.layers[MASK]
    editor.layer_panel.rows[MASK].eye_button.click()
    notices = record(editor.notice)

    editor.clear_active_layer()

    assert mask.is_visible() and editor.layer_panel.rows[MASK].eye_button.isChecked()
    assert notices[0] == ("Mask shown", "info") and not plane(editor).any()


def test_the_layer_settings_stay_for_the_next_image(make_editor, project):
    editor = make_editor(project.b, project.folder)
    editor.controls.opacity_slider.slider.setValue(30)  # the mask, 30 %
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()
    editor.layers[SKELETON].set_size(30)
    editor.layer_panel.rows[MASK].eye_button.click()

    editor.load_image(project.a, project.folder)  # a has a skeleton: the eraser hint shows

    mask, skeleton = editor.layers[MASK], editor.layers[SKELETON]
    assert editor.active_layer_id == SKELETON and skeleton.current_tool == "eraser"
    assert skeleton.size() == 30 and mask.opacity() == pytest.approx(0.30)
    assert not mask.is_visible() and not editor.layer_panel.rows[MASK].eye_button.isChecked()
    assert editor.layer_panel.rows[SKELETON].is_active()
    assert editor.controls.tool_buttons[(SKELETON, "eraser")].isChecked()
    assert editor.status_chip.hint_label.text().startswith("Skeleton · Eraser")


def test_endpoint_markers_show_only_while_the_skeleton_is_edited(make_editor, project):
    editor = make_editor(project.a, project.folder)
    markers = editor.layers[SKELETON].decorations.endpoints_item

    assert not markers.path().isEmpty() and not markers.isVisible()
    editor.set_active_layer(SKELETON)
    assert markers.isVisible()
    editor.set_active_layer(MASK)
    assert not markers.isVisible()


# ======================================================================= controls and chip
def test_the_rail_fades_the_tools_of_the_other_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    buttons = editor.controls.tool_buttons

    assert drawn_in(buttons[(MASK, "eraser")], tokens.TEXT_MUTED)
    assert drawn_in(buttons[(SKELETON, "eraser")], tokens.TEXT_DISABLED)
    editor.set_active_layer(SKELETON)
    assert drawn_in(buttons[(MASK, "eraser")], tokens.TEXT_DISABLED)
    assert drawn_in(buttons[(SKELETON, "eraser")], tokens.TEXT_MUTED)
    assert all(button.isEnabled() for button in buttons.values())


def test_the_size_slider_follows_the_tool_of_the_edited_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    controls = editor.controls
    brush, eraser = MaskLayer.SIZE_SPEC, SkeletonLayer.ERASER_SIZE

    def shown():
        return [spec for spec, s in controls.size_sliders.items() if s.isVisibleTo(editor.dock)]

    assert shown() == [brush] and controls.size_sliders[brush].slider.value() == 8
    for key, expected in (
        ((MASK, "eraser"), [brush]),
        ((MASK, "fill"), []),
        ((SKELETON, "select"), []),
        ((SKELETON, "eraser"), [eraser]),
        ((SKELETON, "polyline"), []),
        ((SKELETON, "connect"), []),
    ):
        controls.tool_buttons[key].click()
        assert shown() == expected, key
    controls.tool_buttons[(SKELETON, "eraser")].click()
    assert controls.size_sliders[eraser].slider.value() == 20


def test_the_size_slider_sizes_the_tool_of_the_edited_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    controls = editor.controls

    controls.size_sliders[MaskLayer.SIZE_SPEC].slider.setValue(30)
    controls.tool_buttons[(SKELETON, "eraser")].click()
    controls.size_sliders[SkeletonLayer.ERASER_SIZE].slider.setValue(11)

    assert (editor.layers[MASK].size(), editor.layers[SKELETON].size()) == (30, 11)


def test_the_opacity_slider_shows_and_sets_the_edited_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    slider = editor.controls.opacity_slider.slider
    mask, skeleton = editor.layers[MASK], editor.layers[SKELETON]

    assert slider.value() == 60
    slider.setValue(30)
    assert mask.opacity() == pytest.approx(0.30) and mask.item.opacity() == pytest.approx(0.30)
    editor.set_active_layer(SKELETON)
    assert slider.value() == 85
    slider.setValue(40)
    assert editor.controls.opacity_slider.value_label.text() == "40%"
    assert skeleton.opacity() == pytest.approx(0.40) and mask.opacity() == pytest.approx(0.30)
    assert mask.item.opacity() == pytest.approx(0.15)


def test_load_skeleton_file_is_offered_only_while_the_skeleton_is_edited(make_editor, project):
    editor = make_editor(project.a, project.folder)
    load = editor.controls.load_button

    assert not load.isEnabled()
    editor.set_active_layer(SKELETON)
    assert load.isEnabled()
    editor.set_active_layer(MASK)
    assert not load.isEnabled() and not load.isHidden()


def test_the_dock_keeps_its_place_across_tools_and_layers(make_editor, project):
    editor = make_editor(project.a, project.folder, size=(1280, 800))
    save = editor.controls.save_button
    places = set()
    for key in editor.controls.tool_buttons:
        editor.controls.tool_buttons[key].click()
        settle(editor)
        places.add((editor.dock.geometry().getRect(), save.mapTo(editor, QPoint(0, 0)).x()))
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()
    editor.controls.size_sliders[SkeletonLayer.ERASER_SIZE].slider.setValue(80)  # a wider value
    settle(editor)
    places.add((editor.dock.geometry().getRect(), save.mapTo(editor, QPoint(0, 0)).x()))

    assert len(places) == 1, places


def test_load_skeleton_file_asks_about_the_skeleton_only_then_opens_the_file(
    make_editor, project, monkeypatch, answers
):
    chosen = write_skeleton(os.path.join(project.folder, "elsewhere", "x.png"), row=20)
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (60, 100))
    draw_line(editor)
    starts = []

    def dialog(parent, title, start, filters):
        starts.append(start)
        return chosen, ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", dialog)
    answers.will(DISCARD)
    editor.controls.load_button.click()

    assert answers.seen == [("Unsaved skeleton", "Save changes to the skeleton for a.png?")]
    assert len(starts) == 1 and same_path(starts[0], project.generated())
    assert raster(editor)[20].any() and editor.layers[SKELETON].document.source == "loaded"
    assert editor.session.journal.undo_entries == (MASK,)  # the skeleton's entries are gone
    assert editor.layers[MASK].is_dirty()


def test_a_loaded_skeleton_file_leaves_no_skeleton_step_to_undo(make_editor, project, monkeypatch):
    chosen = write_skeleton(os.path.join(project.folder, "elsewhere", "x.png"), row=20)
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    assert editor.save()  # clean again, its action still in the timeline
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (chosen, ""))

    editor.controls.load_button.click()  # nothing unsaved: no prompt

    assert raster(editor)[20].any() and editor.session.journal.undo_entries == ()


def test_an_unreadable_skeleton_file_keeps_the_skeleton_timeline(make_editor, project, monkeypatch):
    bad = os.path.join(project.folder, "elsewhere", "broken.png")
    os.makedirs(os.path.dirname(bad), exist_ok=True)
    with open(bad, "wb") as fh:
        fh.write(b"not an image")
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    assert editor.save()  # clean again, its action still in the timeline
    notices = record(editor.notice)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (bad, ""))

    editor.controls.load_button.click()

    assert notices == [("Could not read skeleton broken.png", "danger")]
    assert editor.session.journal.undo_entries == (SKELETON,)
    editor.undo()
    assert not raster(editor)[130].any()  # the line's undo still works
    assert not editor.undo_button.isEnabled()


def test_loading_a_skeleton_file_needs_an_open_image(make_editor):
    editor = make_editor()
    notices = record(editor.notice)

    editor.controls.load_requested.emit()  # the guard fails on any file dialog

    assert notices == [("Open an image first", "warn")]


def test_a_cancelled_load_dialog_changes_nothing(make_editor, project, monkeypatch):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    before = raster(editor).copy()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))

    editor.controls.load_button.click()  # the guard fails on any prompt

    assert np.array_equal(raster(editor), before)
    assert editor.session.journal.undo_entries == (SKELETON,)


def test_the_polyline_prompt_shows_only_for_a_skeleton_draft(make_editor, project):
    editor = make_editor(project.a, project.folder)
    prompt, finish = editor.controls.prompt, editor.controls.finish_button
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    assert prompt.isHidden()

    click(editor, (40, 125))
    assert not prompt.isHidden() and not finish.isEnabled()
    click(editor, (100, 130))
    assert finish.isEnabled()
    editor.set_active_layer(MASK)
    assert prompt.isHidden()
    editor.set_active_layer(SKELETON)
    assert not prompt.isHidden()
    finish.click()
    assert prompt.isHidden() and editor.session.journal.undo_entries == (SKELETON,)


def test_smooth_and_cancel_in_the_prompt_act_on_the_draft(make_editor, project):
    editor = make_editor(project.a, project.folder)
    skeleton, controls = editor.layers[SKELETON], editor.controls
    before = raster(editor).copy()
    controls.tool_buttons[(SKELETON, "polyline")].click()
    for point in ((40, 125), (100, 140), (160, 125)):
        click(editor, point)

    controls.smooth_button.click()
    assert skeleton.draft_state() == DraftState(True, 3, True)
    controls.cancel_button.click()
    assert skeleton.draft_state().points == 0 and controls.prompt.isHidden()
    assert np.array_equal(raster(editor), before)


def test_the_chip_shows_the_state_of_both_layers(make_editor, project, monkeypatch):
    import app.gui.correct.skeleton_document as skeleton_document

    editor = make_editor(project.b, project.folder)
    chip = editor.status_chip
    assert chip.state() == "none"  # no mask, no skeleton
    editor.load_image(project.a, project.folder)
    assert chip.state() == "none"  # the model's mask and a generated skeleton: nothing saved
    paint(editor, (20, 100), (60, 100))
    assert chip.state() == "dirty"
    editor.undo()
    assert chip.state() == "none"

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(skeleton_document, "imwrite_unicode", fail)
    paint(editor, (20, 100), (60, 100))
    assert editor.save()
    assert chip.state() == "clean"  # a saved (GT) mask and a generated skeleton
    draw_line(editor)
    assert chip.state() == "dirty"  # an unsaved skeleton beside a saved mask
    editor.save()
    assert chip.state() == "error" and "disk full" in chip.state_label.toolTip()
    assert editor.layer_panel.rows[MASK].state_label.text() == "GT"


def test_the_chip_hint_names_the_edited_layer_and_its_tool_in_its_colour(make_editor, project):
    editor = make_editor(project.a, project.folder)
    chip = editor.status_chip

    assert chip.hint_label.text() == "Mask · Brush · drag to paint"
    assert not chip.hint_dot.isHidden() and tokens.MASK in chip.hint_dot.styleSheet()
    assert not editor.dock.isAncestorOf(chip.hint_label)
    editor.controls.tool_buttons[(MASK, "fill")].click()
    assert chip.hint_label.text() == "Mask · Fill · click inside a closed outline"
    editor.set_active_layer(SKELETON)
    assert chip.hint_label.text() == "Skeleton · Select · click a skeleton line to edit it"
    assert tokens.SKEL in chip.hint_dot.styleSheet()


def test_the_layers_rows_show_the_save_state_of_each_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    rows = editor.layer_panel.rows

    def states():
        return rows[MASK].state_label.text(), rows[SKELETON].state_label.text()

    assert states() == ("Prediction", "Generated")
    paint(editor, (20, 100), (60, 100))
    assert states() == ("Unsaved", "Generated")
    draw_line(editor)
    assert states() == ("Unsaved", "Unsaved")
    editor.save()
    assert states() == ("GT", "Saved")


# ================================================================================= saving
def test_a_mask_edit_saves_only_the_mask(make_editor, project):
    editor = make_editor(project.a, project.folder)
    masks, skeletons = record(editor.mask_saved), record(editor.skeleton_saved)
    paint(editor, (20, 100), (80, 110))
    notices = record(editor.notice)

    assert editor.save() is True

    assert masks == [project.a] and skeletons == []
    assert notices == [("Saved mask · a.png", "success")]
    assert not os.path.exists(os.path.join(project.folder, "skeleton_corrections"))
    assert png_colour_type(project.gt_mask()) == 6 and not editor.has_unsaved_changes()
    assert png_colour_type(project.mask()) == 0  # the model mask is never rewritten


def test_a_skeleton_edit_saves_only_the_correction(make_editor, project):
    with open(project.mask(), "rb") as fh:
        mask_bytes = fh.read()
    editor = make_editor(project.a, project.folder)
    masks, skeletons = record(editor.mask_saved), record(editor.skeleton_saved)
    draw_line(editor)
    notices = record(editor.notice)

    assert editor.save() is True

    assert masks == [] and len(skeletons) == 1 and same_path(skeletons[0], project.correction())
    assert notices == [("Saved skeleton · a.png", "success")]
    with open(project.mask(), "rb") as fh:
        assert fh.read() == mask_bytes
    assert png_colour_type(project.mask()) == 0  # the model mask is never rewritten


# The skeleton half: tests/test_skeleton_save_only_when_changed.py (O2: an untouched skeleton
# is not written).
@pytest.mark.parametrize("layer_id", [MASK, SKELETON])
def test_saving_with_nothing_unsaved_writes_nothing(make_editor, project, layer_id):  # O2
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(layer_id)
    notices = record(editor.notice)
    before = listing(project.folder)

    assert editor.save() is False

    assert listing(project.folder) == before
    assert notices == [("Nothing unsaved", "info")]


def test_saving_both_layers_says_so_in_one_toast(make_editor, project):
    editor = make_editor(project.a, project.folder)
    masks, skeletons = record(editor.mask_saved), record(editor.skeleton_saved)
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    notices = record(editor.notice)

    assert editor.save() is True

    assert notices == [("Saved mask and skeleton · a.png", "success")]
    assert masks == [project.a] and len(skeletons) == 1


def test_a_failed_skeleton_save_is_named_stays_unsaved_and_the_mask_is_saved(
    make_editor, project, monkeypatch
):
    import app.gui.correct.skeleton_document as skeleton_document

    editor = make_editor(project.a, project.folder)
    masks, skeletons = record(editor.mask_saved), record(editor.skeleton_saved)
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    notices = record(editor.notice)

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(skeleton_document, "imwrite_unicode", fail)
    assert editor.save() is False

    assert notices == [
        ("Saved mask · a.png", "success"),
        ("Could not save a_skeleton.png: disk full", "danger"),
    ]
    assert masks == [project.a] and skeletons == []
    assert editor.layers[SKELETON].is_dirty() and not editor.layers[MASK].is_dirty()
    assert editor.layer_panel.rows[SKELETON].state_label.text() == "Not saved"


def test_a_failed_skeleton_save_keeps_the_old_file(make_editor, project, monkeypatch):
    old = write_skeleton(project.correction(), row=40)
    with open(old, "rb") as fh:
        before = fh.read()
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    notices = record(editor.notice)

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        assert editor.save() is False

    with open(old, "rb") as fh:
        assert fh.read() == before
    assert not [f for f in os.listdir(os.path.dirname(old)) if f.endswith(".tmp")]
    assert [kind for _message, kind in notices] == ["danger"]
    assert editor.has_unsaved_changes() and editor.status_chip.state() == "error"


@pytest.mark.parametrize("layer_id", [MASK, SKELETON])
def test_an_unwritable_folder_is_a_notice_not_a_crash(make_editor, project, monkeypatch, layer_id):
    editor = make_editor(project.a, project.folder)
    if layer_id == MASK:
        paint(editor, (20, 100), (80, 110))
    else:
        draw_line(editor)
    notices = record(editor.notice)

    def denied(*_args, **_kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", denied)
    assert editor.save() is False

    assert [kind for _message, kind in notices] == ["danger"] and editor.has_unsaved_changes()


def test_the_skeleton_saves_at_image_resolution_whatever_size_was_read(make_editor, project):
    write_skeleton(project.generated(), row=55, w=W // 2, h=H // 2)
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    assert editor.save()
    saved = cv2.imread(project.correction(), cv2.IMREAD_GRAYSCALE)
    assert (saved.shape[1], saved.shape[0]) == (W, H)
    assert set(np.unique(saved)) <= {0, 255}

    editor.load_image(project.b, project.folder)  # nothing read: image size too
    draw_line(editor)
    assert editor.save()
    saved = cv2.imread(project.correction("b"), cv2.IMREAD_GRAYSCALE)
    assert (saved.shape[1], saved.shape[0]) == (W, H)


def test_an_emptied_mask_saves_an_empty_gt_mask_and_reports_it_once(make_editor, project):
    editor = make_editor(project.a, project.folder)
    cleared, masks, notices = (
        record(editor.mask_cleared),
        record(editor.mask_saved),
        record(editor.notice),
    )

    editor.clear_active_layer()
    assert not os.path.exists(project.gt_mask())  # Clear alone writes nothing
    editor.undo()
    assert plane(editor).any()
    editor.clear_active_layer()
    assert editor.save() is True

    assert os.path.exists(project.mask())  # the prediction stays
    assert png_colour_type(project.gt_mask()) == 6  # O9: an empty GT mask
    assert not cv2.imread(project.gt_mask(), cv2.IMREAD_UNCHANGED).any()
    assert masks == [project.a] and cleared == []
    assert ("Mask cleared. Ctrl+Z to undo; Save writes an empty GT mask.", "info") in notices
    assert notices[-2:] == [
        ("Saved mask · a.png", "success"),
        ("Empty mask saved as GT for a.png: root area 0", "info"),
    ]


def test_the_saved_mask_is_white_on_transparent_and_pixel_identical(make_editor, tmp_path):
    photo = write_image(str(tmp_path / "big" / "plot_001.png"), w=640, h=480)
    editor = make_editor(photo)
    editor.controls.size_sliders[MaskLayer.SIZE_SPEC].slider.setValue(9)
    paint(editor, (10, 10), (600, 400))
    expected = alpha(editor) > 0
    assert editor.save()

    path = str(tmp_path / "big" / "gt" / "mask" / "plot_001.png")
    assert os.path.getsize(path) < 64 * 1024
    rgba = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    assert np.array_equal(rgba[:, :, 3] == 255, expected)
    assert set(np.unique(rgba)) <= {0, 255}
    assert np.array_equal(rgba[:, :, :3].max(axis=2) == 255, expected)
    assert os.listdir(tmp_path / "big" / "gt" / "mask") == ["plot_001.png"]


def test_saving_writes_the_gt_mask_and_never_the_class_labels(make_editor, project):
    editor = make_editor(project.b, project.folder)
    editor.controls.size_sliders[MaskLayer.SIZE_SPEC].slider.setValue(6)
    paint(editor, (10, 10), (40, 10))
    draw_line(editor)
    assert editor.save()

    layout = ProjectLayout(project.folder)
    gt_mask = layout.gt_mask_path(project.b)
    assert os.path.isfile(gt_mask) and not os.path.exists(layout.gt_path(project.b))
    assert not os.path.exists(project.mask("b"))  # no prediction is ever written
    saved = cv2.imread(gt_mask, cv2.IMREAD_GRAYSCALE)
    assert saved.shape == (H, W) and set(np.unique(saved)) <= {0, 255}
    assert saved[10, 25] == 255 and saved[140, 190] == 0

    editor.load_image(project.b, project.folder)  # the saved files read back
    assert np.array_equal(alpha(editor) > 0, saved > 200)


def test_a_session_without_edits_writes_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view, chrome = editor.graphics_view, editor.chrome
    written = [
        record(editor.mask_saved),
        record(editor.mask_cleared),
        record(editor.skeleton_saved),
    ]
    moved = record(editor.navigate_requested)
    before = listing(project.folder)

    editor.set_active_layer(SKELETON)
    editor.controls.opacity_slider.slider.setValue(25)
    editor.set_active_layer(MASK)
    for eye in (row.eye_button for row in editor.layer_panel.rows.values()):
        eye.click()
        eye.click()
    view.set_zoom(4.0)
    view.zoom_step(-1)
    view.fit_to_view()
    chrome.pan_button.click()
    chrome.pan_button.click()
    chrome.enhance_button.click()
    chrome.enhance_button.click()
    editor.navigate(1)

    assert listing(project.folder) == before and png_colour_type(project.mask()) == 0
    assert written == [[], [], []] and moved == [1]


# ================================================================== save reports, prompt
def test_the_prompts_save_reports_each_written_file_once(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    masks, skeletons = record(editor.mask_saved), record(editor.skeleton_saved)
    answers.will(SAVE)

    assert editor.maybe_save_changes() is True

    assert masks == [project.a] and len(skeletons) == 1


def test_an_emptied_mask_saved_from_the_prompt_reports_mask_saved_once(
    make_editor, project, answers
):
    editor = make_editor(project.a, project.folder)
    editor.clear_active_layer()
    cleared, masks = record(editor.mask_cleared), record(editor.mask_saved)
    answers.will(SAVE)

    assert editor.maybe_save_changes() is True

    assert masks == [project.a] and cleared == [] and os.path.exists(project.mask())
    assert os.path.isfile(project.gt_mask())  # O9: an empty GT mask


def test_a_mask_back_to_the_prediction_removed_from_the_prompt_reports_mask_cleared_once(
    make_editor, project, answers
):
    write_mask(project.gt_mask(), np.zeros((H, W), np.uint8))
    editor = make_editor(project.a, project.folder)
    editor.load_prediction()
    cleared, masks = record(editor.mask_cleared), record(editor.mask_saved)
    answers.will(SAVE)

    assert editor.maybe_save_changes() is True

    assert cleared == [project.a] and masks == [] and not os.path.exists(project.gt_mask())


def test_a_tree_click_answered_save_reports_the_saved_mask_once(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    masks = record(editor.mask_saved)
    answers.will(SAVE)

    editor.load_image(project.b, project.folder)

    assert masks == [project.a] and same_path(editor.current_image_path, project.b)


def test_paging_saves_without_a_prompt_reports_each_file_once_and_asks_to_move(
    make_editor, project
):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    masks, skeletons, moved = (
        record(editor.mask_saved),
        record(editor.skeleton_saved),
        record(editor.navigate_requested),
    )

    editor.navigate(1)  # the guard fails on any prompt

    assert masks == [project.a] and len(skeletons) == 1 and moved == [1]
    assert not editor.has_unsaved_changes()


def test_a_failed_save_reports_nothing_and_paging_stays(make_editor, project, monkeypatch):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    masks, moved = record(editor.mask_saved), record(editor.navigate_requested)

    def denied(*_args, **_kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", denied)
    editor.navigate(1)
    assert editor.save() is False

    assert masks == [] and moved == [] and editor.has_unsaved_changes()


def test_paging_with_a_polyline_draft_stays_and_says_how_to_end_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, (40, 125))
    click(editor, (100, 130))
    moved, notices = record(editor.navigate_requested), record(editor.notice)

    editor.navigate(1)

    assert moved == [] and notices == [(editor.FINISH_LINE_FIRST, "warn")]
    assert editor.layers[SKELETON].draft_state().points == 2
    assert editor.has_unsaved_changes()  # nothing was saved either


def test_opening_another_image_announces_a_dropped_polyline_draft(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, (40, 125))
    notices = record(editor.notice)

    editor.load_image(project.b, project.folder)

    assert (SkeletonLayer.DRAFT_DISCARDED_NOTICE, "info") in notices
    assert editor.layers[SKELETON].draft_state().points == 0


def test_paging_without_edits_only_asks_for_the_image(make_editor, project):
    editor = make_editor(project.a, project.folder)
    moved = record(editor.navigate_requested)
    before = listing(project.folder)

    editor.navigate(-1)

    assert moved == [-1] and listing(project.folder) == before


def test_one_prompt_names_every_unsaved_layer(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    answers.will(CANCEL, CANCEL)

    editor.maybe_save_changes()
    draw_line(editor)
    editor.maybe_save_changes()

    assert answers.seen == [
        ("Unsaved mask", "Save changes to the mask for a.png?"),
        ("Unsaved mask and skeleton", "Save changes to the mask and skeleton for a.png?"),
    ]


def test_save_in_the_prompt_saves_then_opens_the_next_image(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    answers.will(SAVE)

    editor.load_image(project.b, project.folder)

    assert os.path.isfile(project.correction())
    assert same_path(editor.current_image_path, project.b) and not editor.has_unsaved_changes()


def test_discard_reverts_both_layers_to_their_files(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    start = raster(editor).copy()
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    answers.will(DISCARD)

    assert editor.maybe_save_changes() is True

    assert np.array_equal(plane(editor), box_plane())
    assert np.array_equal(alpha(editor) > 0, box_plane() > 0)
    assert np.array_equal(raster(editor), start)
    assert not editor.has_unsaved_changes() and editor.session.journal.undo_entries == ()
    assert not os.path.exists(project.correction())


def test_discard_on_a_tree_click_opens_the_next_image_unsaved(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    answers.will(DISCARD)

    editor.load_image(project.b, project.folder)

    assert len(answers.seen) == 1 and "a.png" in answers.seen[0][1]
    assert same_path(editor.current_image_path, project.b)
    assert not os.path.exists(project.correction())


def test_cancel_keeps_the_image_its_edits_and_their_history(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))
    draw_line(editor)
    edited = plane(editor).copy(), raster(editor).copy()
    answers.will(CANCEL, CANCEL, CANCEL)

    editor.load_image(project.b, project.folder)
    assert editor.maybe_save_changes() is False
    assert editor.reset() is False

    assert same_path(editor.current_image_path, project.a) and editor.has_unsaved_changes()
    assert np.array_equal(plane(editor), edited[0]) and np.array_equal(raster(editor), edited[1])
    assert editor.session.journal.undo_entries == (MASK, SKELETON)


def test_a_failed_save_on_a_tree_click_keeps_the_open_image(make_editor, project, answers):
    editor = make_editor(project.b, project.folder)
    paint(editor, (10, 10), (40, 10))
    edited = plane(editor).copy()
    os.makedirs(project.gt_mask("b"))  # a folder where the file must go
    answers.will(SAVE, CANCEL)  # Save, then "Keep editing"

    editor.load_image(project.a, project.folder)

    assert [title for title, _text in answers.seen] == ["Unsaved mask", "Mask not saved"]
    assert same_path(editor.current_image_path, project.b) and editor.has_unsaved_changes()
    assert np.array_equal(plane(editor), edited)


def test_a_failed_save_in_the_prompt_asks_again_keep_editing_stays_discard_goes(
    make_editor, project, answers, monkeypatch
):
    editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110))

    def denied(*_args, **_kwargs):
        raise PermissionError("read-only share")

    monkeypatch.setattr(os, "makedirs", denied)
    answers.will(SAVE, CANCEL)
    assert editor.maybe_save_changes() is False
    assert [title for title, _text in answers.seen] == ["Unsaved mask", "Mask not saved"]
    assert editor.has_unsaved_changes()

    answers.will(SAVE, DISCARD)
    assert editor.maybe_save_changes() is True
    assert not editor.has_unsaved_changes() and np.array_equal(plane(editor), box_plane())


def test_dirty_changed_fires_once_per_transition(make_editor, project):
    editor = make_editor(project.a, project.folder)
    seen = record(editor.dirty_changed)

    paint(editor, (20, 100), (80, 110))
    paint(editor, (20, 120), (80, 130))
    assert seen == [True]
    editor.undo()
    editor.undo()  # back to the saved mask: clean again
    assert seen == [True, False]
    draw_line(editor)
    editor.save()
    assert seen == [True, False, True, False]


def test_the_clean_page_never_prompts_and_reset_empties_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.load_image(project.b, project.folder)

    assert editor.maybe_save_changes() is True
    assert editor.reset() is True

    assert editor.current_image_path == "" and editor.status_chip.text() == "No image"
    assert editor.photo_item.image_size() == (0, 0)
    assert not any(layer.is_open() for layer in editor.layers.values())
    drag(editor, (10, 10), (40, 30))
    assert editor.session.journal.undo_entries == ()


def test_reset_honours_cancel(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    draw_line(editor)
    answers.will(CANCEL)

    assert editor.reset() is False

    assert same_path(editor.current_image_path, project.a) and editor.has_unsaved_changes()


# ================================================================ Enhance, focus, layout
def test_enhancement_renders_live_and_sticks_across_images(make_editor, tmp_path):
    gradient = np.tile(np.linspace(60, 120, W, dtype=np.uint8), (H, 1))
    first, second = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    for path in (first, second):
        cv2.imwrite(path, np.dstack([gradient] * 3))
    editor = make_editor(first)
    raw = editor.photo_item.pixmap().toImage()
    controls = editor.norm_controls

    controls.method_combo.setCurrentText("Contrast Stretching")
    controls.apply_button.click()
    assert controls.is_active() and editor.photo_item.pixmap().toImage() != raw

    editor.load_image(second)
    assert editor.photo_item.pixmap().toImage() != raw  # still enhanced


def test_dock_and_panel_clicks_leave_the_keys_on_the_canvas(make_editor, project):
    editor = make_editor(project.a, project.folder)
    activate(editor)
    saved = record(editor.mask_saved)
    paint(editor, (20, 100), (80, 110))
    view = editor.graphics_view

    QTest.mouseClick(editor.save_button, LEFT)
    assert len(saved) == 1
    targets = [
        editor.controls.size_sliders[MaskLayer.SIZE_SPEC].slider,
        editor.controls.opacity_slider.slider,
        editor.layer_panel.rows[SKELETON],
        editor.layer_panel.rows[MASK].eye_button,
        editor.layer_panel.rows[MASK].eye_button,
        editor.layer_panel.rows[MASK],
    ]
    for widget in targets:
        QTest.mouseClick(widget, LEFT)
        assert QApplication.focusWidget() is view, widget
    editor.enhance_popover.toggle()
    editor.norm_controls.method_combo.setFocus()
    editor.enhance_popover.toggle()
    settle(editor)
    assert QApplication.focusWidget() is view


LONG_NAME = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.png"


@pytest.mark.parametrize("layer_id", [MASK, SKELETON])
@pytest.mark.parametrize("size", [(1400, 900), (1000, 700), (820, 560), (700, 520)])
def test_the_overlays_never_collide(make_editor, tmp_path, size, layer_id):
    photo = write_image(str(tmp_path / LONG_NAME))
    editor = make_editor(photo, size=size)
    controls = editor.controls
    if layer_id == SKELETON:
        controls.tool_buttons[(SKELETON, "polyline")].click()
        click(editor, (40, 40))
        click(editor, (90, 60))  # the prompt shows
    else:
        controls.tool_buttons[(MASK, "eraser")].click()
    editor.chrome.enhance_button.click()
    settle(editor)

    chrome = editor.chrome
    candidates = (chrome.rail, chrome.dock, chrome.chip, chrome.zoom, chrome.popover)
    overlays = [w for w in (*candidates, editor.layer_panel, controls.prompt) if w.isVisible()]
    assert editor.layer_panel in overlays and chrome.popover in overlays
    assert (controls.prompt in overlays) == (layer_id == SKELETON)
    for i, a in enumerate(overlays):
        assert editor.rect().contains(a.geometry()), (a, a.geometry())
        for b in overlays[i + 1 :]:
            assert not a.geometry().intersects(b.geometry()), (a, b, a.geometry(), b.geometry())


def image_rect_in(editor, w: int, h: int) -> QRect:
    """Where the photo is shown, in the editor's coordinates."""
    view = editor.graphics_view
    rect = view.mapFromScene(QRectF(0, 0, w, h)).boundingRect()
    return QRect(view.mapTo(editor, rect.topLeft()), rect.size())


@pytest.mark.parametrize("size", [(907, 562), (1018, 662), (1658, 942)])
def test_fit_keeps_the_photo_clear_of_every_overlay(make_editor, tmp_path, size):
    photo = write_image(str(tmp_path / "plot_001.png"), w=640, h=480)
    editor = make_editor(photo, size=size)
    editor.graphics_view.fit_to_view()
    settle(editor)

    shown = image_rect_in(editor, 640, 480)
    chrome = editor.chrome
    for overlay in (chrome.rail, chrome.dock, chrome.chip, chrome.zoom, editor.layer_panel):
        assert not shown.intersects(overlay.geometry()), (overlay, shown, overlay.geometry())
    zoom = editor.graphics_view.zoom_factor
    assert editor.zoom_cluster.zoom_pct_label.text() == f"{round(zoom * 100)}%"


def test_the_zoom_readout_follows_the_canvas_up_to_2000_percent(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view = editor.graphics_view

    view.set_zoom(8.0)
    assert view.zoom_factor == pytest.approx(8.0)
    assert editor.zoom_cluster.zoom_pct_label.text() == "800%"
    view.set_zoom(100.0)
    assert view.zoom_factor == view.MAX_ZOOM


def test_the_hand_button_turns_panning_on_and_off_and_hides_the_ring(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view, pan, ring = editor.graphics_view, editor.chrome.pan_button, editor.layers[SKELETON]
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()
    editor.router.canvas_move(QPoint(100, 60), Mouse(), False)
    assert ring.eraser_ring.is_visible()

    pan.click()
    assert view.pan_mode and not ring.eraser_ring.is_visible()
    pan.click()
    assert not view.pan_mode


# ============================================================================ zoom quality
def sine_photo(tmp_path) -> str:
    path = str(tmp_path / "sine.png")
    cv2.imwrite(path, np.dstack([sine_image(640, 480)] * 3))
    return path


def centred_grab(editor, zoom: float, centre: tuple[float, float]) -> np.ndarray:
    """What the canvas shows at ``zoom`` around image point ``centre`` (no brush ring)."""
    editor.router.canvas_hover_left()
    view = editor.graphics_view
    view.set_zoom(zoom)
    view.centerOn(QPointF(*centre))
    return grab(view)


def content(editor, rgb: np.ndarray, w: int, h: int, margin: int = 3) -> np.ndarray:
    rect = editor.graphics_view.mapFromScene(QRectF(0, 0, w, h)).boundingRect()
    x0, y0 = max(0, rect.left() + margin), max(0, rect.top() + margin)
    x1, y1 = min(rgb.shape[1], rect.right() - margin), min(rgb.shape[0], rect.bottom() - margin)
    return rgb[y0:y1, x0:x1]


def test_the_photo_is_smooth_at_4x(make_editor, tmp_path):
    editor = make_editor(sine_photo(tmp_path))  # no mask, no skeleton: the photo alone

    region = content(editor, centred_grab(editor, 4.0, (320, 240)), 640, 480)

    assert region.size
    assert sine_step_ratio(region, sine_image(640, 480)) <= 1.5 / 4.0
    assert equal_neighbour_fraction(region) <= 0.05


@pytest.mark.parametrize("zoom", [4, 8])
def test_mask_pixels_are_exact_blocks_when_magnified(make_editor, tmp_path, zoom):
    photo = write_image(str(tmp_path / "flat.png"), w=640, h=480)
    editor = make_editor(photo)
    editor.controls.size_sliders[MaskLayer.SIZE_SPEC].slider.setValue(2)  # a 1 px dab
    rng = np.random.default_rng(zoom)
    x0, y0, n = 300, 220, 20
    for x, y in rng.integers(0, n, size=(60, 2)):
        click(editor, (x0 + int(x), y0 + int(y)))
    painted_px = plane(editor)[y0 : y0 + n, x0 : x0 + n] > 0
    assert 0 < painted_px.sum() < n * n

    rgb = centred_grab(editor, float(zoom), (x0 + n / 2, y0 + n / 2))
    origin = editor.graphics_view.mapFromScene(QPointF(x0, y0))
    colour = np.array([int(c) for c in bytes.fromhex(tokens.MASK[1:])])
    painted = np.round(0.6 * colour + 0.4 * 90)  # the mask at 60 % over the grey photo
    for row in range(n):
        for col in range(n):
            top, left = origin.y() + row * zoom, origin.x() + col * zoom
            block = rgb[top + 1 : top + zoom - 1, left + 1 : left + zoom - 1].reshape(-1, 3)
            assert (block == block[0]).all(), (row, col)
            expected = painted if painted_px[row, col] else np.array([90, 90, 90])
            assert np.abs(block[0].astype(int) - expected).max() <= 2, (row, col)


def test_the_skeleton_is_a_thin_line_not_blocks_at_8x(make_editor, tmp_path):
    folder = str(tmp_path / "p")
    photo = write_image(os.path.join(folder, "c.png"))
    write_skeleton(os.path.join(folder, "skeletons", "c_fake.png"), row=75)
    editor = make_editor(photo, folder)
    item = editor.layers[SKELETON].item
    item.EXTRACT_BUDGET_MS = float("inf")  # every tile's vectors in the first paint

    rgb = centred_grab(editor, 8.0, (100, 75))

    assert item.mode_painted() in ("vector", "vector-thin")
    column = editor.graphics_view.mapFromScene(QPointF(100.5, 75.5)).x()
    marked = np.abs(rgb[:, column].astype(int) - 90).max(axis=1) > 12
    assert 0 < marked.sum() <= 8  # an 8x block line with its halo would mark 24 rows


def test_an_opengl_swap_keeps_the_canvas_config_and_the_smooth_photo(
    make_editor, tmp_path, monkeypatch
):
    monkeypatch.setattr(canvas_gl, "opengl_viewports_allowed", lambda: True)
    editor = make_editor(show=False)
    view = editor.graphics_view
    assert view.viewport().inherits("QOpenGLWidget")
    hints, background = view.renderHints(), view.backgroundBrush().color().name()

    view.set_opengl_viewport_enabled(False)

    assert not view.viewport().inherits("QOpenGLWidget")
    assert view.renderHints() == hints and not hints & QPainter.RenderHint.SmoothPixmapTransform
    assert view.backgroundBrush().color().name() == background
    assert view.viewport().hasMouseTracking()
    editor.show()
    editor.load_image(sine_photo(tmp_path))
    settle(editor)
    region = content(editor, centred_grab(editor, 4.0, (320, 240)), 640, 480)
    assert sine_step_ratio(region, sine_image(640, 480)) <= 1.5 / 4.0


# ========================================================= the old skeleton tool tests
def test_a_fast_eraser_drag_leaves_no_fragments(make_editor, project):
    editor = make_editor(project.a, project.folder)  # a's line on row 110, x 20..179
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()
    editor.layers[SKELETON].set_size(6)

    drag(editor, (40, 110), *[(x, 110) for x in range(65, 161, 25)], (160, 110))

    assert not raster(editor)[110, 40:161].any()


def test_the_eraser_shows_its_work_during_a_drag_without_a_release(make_editor, project):
    editor = make_editor(project.a, project.folder)
    item, router, event = editor.layers[SKELETON].item, editor.router, Mouse()
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()
    router.canvas_press(QPoint(40, 110), event)
    settle()
    revision = item.revision()

    router.canvas_move(QPoint(90, 110), event, True)
    for _ in range(100):  # the 16 ms frame timer fires without a release
        QTest.qWait(10)
        if item.revision() != revision:
            break

    assert item.revision() != revision
    router.canvas_release(QPoint(90, 110), event)


def test_the_eraser_ring_matches_the_footprint_at_any_zoom(make_editor, project):
    editor = make_editor(project.a, project.folder)
    view, ring = editor.graphics_view, editor.layers[SKELETON].eraser_ring
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()

    for zoom in (1.0, 4.0):
        view.set_zoom(zoom)
        editor.router.canvas_move(QPoint(100, 60), Mouse(), False)
        assert ring.is_visible() and ring.rect().width() == pytest.approx(20)
        shown = view.mapFromScene(ring.rect()).boundingRect().width()
        assert shown == pytest.approx(20 * zoom, abs=2)


def test_the_eraser_ring_hides_in_the_other_tools(make_editor, project):
    editor = make_editor(project.a, project.folder)
    ring = editor.layers[SKELETON].eraser_ring
    editor.controls.tool_buttons[(SKELETON, "eraser")].click()
    editor.router.canvas_move(QPoint(100, 60), Mouse(), False)
    assert ring.is_visible()

    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    editor.router.canvas_move(QPoint(110, 60), Mouse(), False)

    assert not ring.is_visible()


def key_event(key) -> QKeyEvent:
    """A key press without modifiers, for the canvas router."""
    return QKeyEvent(QEvent.Type.KeyPress, key, NO_MODS)


def test_delete_removes_the_picked_branch_and_undo_restores_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    before = raster(editor).copy()
    notices = record(editor.notice)
    editor.controls.tool_buttons[(SKELETON, "select")].click()
    click(editor, (100, 112))
    assert editor.layers[SKELETON].draft.edit_original is not None

    assert editor.router.canvas_key_press(key_event(Qt.Key.Key_Delete))

    assert not raster(editor)[110, 30:170].any()
    assert notices[-1][0].startswith("Branch deleted") and "Ctrl+Z" in notices[-1][0]
    editor.undo()
    assert np.array_equal(raster(editor), before)


def test_delete_with_nothing_picked_changes_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)
    before = raster(editor).copy()

    editor.router.canvas_key_press(key_event(Qt.Key.Key_Delete))

    assert np.array_equal(raster(editor), before)


def test_enter_commits_the_polyline_and_esc_drops_the_draft(make_editor, project):
    editor = make_editor(project.b, project.folder)
    skeleton = editor.layers[SKELETON]
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, (50, 50))
    click(editor, (150, 60))

    editor.router.canvas_key_press(key_event(Qt.Key.Key_Escape))
    assert skeleton.draft_state().points == 0 and editor.controls.prompt.isHidden()
    assert not raster(editor).any()
    click(editor, (50, 50))
    click(editor, (150, 60))
    editor.router.canvas_key_press(key_event(Qt.Key.Key_Return))
    assert raster(editor).any() and skeleton.draft_state().points == 0


def test_clear_empties_the_skeleton_and_undo_restores_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.set_active_layer(SKELETON)

    editor.clear_active_layer()
    assert not raster(editor).any()
    editor.undo()
    assert raster(editor)[110, 20:180].all()


def test_the_topbar_clear_button_follows_and_clears_the_edited_layer(make_editor, project):
    editor = make_editor(project.a, project.folder)
    button = editor.clear_button

    assert button.text() == "Clear mask"
    editor.set_active_layer(SKELETON)
    assert button.text() == "Clear skeleton"
    button.click()

    assert not raster(editor).any() and np.array_equal(plane(editor), box_plane())
    assert editor.session.journal.undo_entries == (SKELETON,)


def test_a_connect_drag_joins_two_endpoints(make_editor, project):
    folder = project.folder
    ends = np.zeros((H, W), np.uint8)
    ends[100, 20:80] = 255
    ends[100, 120:180] = 255
    path = os.path.join(folder, "skeletons", "b_fake.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, ends)
    editor = make_editor(project.b, folder)
    editor.controls.tool_buttons[(SKELETON, "connect")].click()

    drag(editor, (79, 100), (100, 100), (120, 100))

    assert raster(editor)[100, 80:120].all()


def test_drawing_the_skeleton_changes_no_skeleton_pixel(make_editor, project):
    editor = make_editor(project.a, project.folder)
    before = raster(editor).copy()

    editor.layers[SKELETON].render_skeleton()
    centred_grab(editor, 8.0, (100, 110))

    assert np.array_equal(raster(editor), before)


# ============================================================ the old mask editor tests
def test_a_stroke_makes_the_mask_unsaved_and_the_save_clears_that(make_editor, project):
    editor = make_editor(project.b, project.folder)
    assert not editor.has_unsaved_changes()

    paint(editor, (10, 10), (40, 10))
    assert editor.has_unsaved_changes()
    assert editor.save()
    assert not editor.has_unsaved_changes()


def test_an_eraser_click_erases(make_editor, project):
    editor = make_editor(project.b, project.folder)
    sizes = editor.controls.size_sliders
    sizes[MaskLayer.SIZE_SPEC].slider.setValue(20)
    click(editor, (30, 30))
    editor.controls.tool_buttons[(MASK, "eraser")].click()
    sizes[MaskLayer.SIZE_SPEC].slider.setValue(4)

    click(editor, (30, 30))

    assert alpha(editor)[30, 30] == 0 and alpha(editor)[30, 36] > 0


def test_fill_fills_inside_a_closed_outline_and_says_so_outside(make_editor, project):
    editor = make_editor(project.b, project.folder)
    editor.controls.size_sliders[MaskLayer.SIZE_SPEC].slider.setValue(3)
    paint(editor, (20, 15), (50, 15), (50, 45), (20, 45), (20, 15))
    notices = record(editor.notice)
    editor.controls.tool_buttons[(MASK, "fill")].click()
    depth = len(editor.session.journal)

    click(editor, (5, 5))  # outside: a notice, no history entry
    assert notices == [("Fill works inside a closed outline. Close the loop first.", "info")]
    assert len(editor.session.journal) == depth

    click(editor, (35, 30))
    assert alpha(editor)[30, 35] > 0 and alpha(editor)[5, 5] == 0
    assert len(editor.session.journal) == depth + 1
