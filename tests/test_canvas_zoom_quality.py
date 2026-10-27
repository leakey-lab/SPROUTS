"""Zoom quality of the editor and Library canvases (spec section 10, plan P3 Step 6).

Rendered offscreen on the raster viewport through the real canvases: the Correct editor with
its class labels (``CorrectEditor``; the Trace GT editor measured here until WG-T11 removed
it) and the Library display (``DisplayController`` on ``MagnifyingGraphicsView``). Thresholds
come from the contracts scout (``scratch/contracts-tests/render``), whose measurements of the
pre-merge canvases fail every photo threshold here:

* the photo is smooth when magnified (sine step ratio <= 1.5 / z, equal neighbours
  <= 0.05), exact at 100 %, and does not alias when zoomed out (chirp, 1 px stripes);
* the class labels show exact pixels when magnified and a 1 px stroke never vanishes
  when zoomed out;
* Enhance and the next image never leave a stale level; an OpenGL on/off swap keeps it;
* the Library overlay draws a generated file (2 px lines) soft, without pixel blocks.
"""

import contextlib
import os

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QStatusBar,
    QWidget,
)

from app.gui import canvas_gl, editor_prompts
from app.gui.correct.editor import CorrectEditor
from app.gui.correct.layer import CLASSES
from app.gui.display_controller import DisplayController
from app.gui.image_manager import ImageManager
from app.gui.widgets import tokens
from tests.correct_support import drag
from tests.qt_teardown import flush_deleted
from tests.render_metrics import (
    chirp_alias_std,
    chirp_image,
    chirp_pan_delta,
    equal_neighbour_fraction,
    qimage_to_rgb,
    sine_image,
    sine_step_ratio,
    stripes_image,
)

W, H = 640, 480  # photo patterns
CHIRP_H = 64
VIEW_SIZE = (1100, 800)
CANVASES = ("editor", "library")

pytestmark = pytest.mark.usefixtures("no_prompts")


@pytest.fixture(scope="module")
def patterns(tmp_path_factory):
    """The pattern images of this module, as files (both canvases decode from disk)."""
    folder = tmp_path_factory.mktemp("zoom_quality")
    images = {
        "sine": sine_image(W, H),
        "stripes": stripes_image(W, H),
        "chirp0": chirp_image(W, CHIRP_H, 0),
        "chirp1": chirp_image(W, CHIRP_H, 1),
        "flat": np.full((H, W), 90, np.uint8),
        "flat200": np.full((H, W), 200, np.uint8),
        "gradient": np.tile(np.linspace(60, 120, W).astype(np.uint8), (H, 1)),
    }
    paths = {}
    for name, gray in images.items():
        path = folder / f"{name}.png"
        cv2.imwrite(str(path), np.dstack([gray] * 3))
        paths[name] = str(path)
    return paths


class _Library:
    """What DisplayController uses from MainWindow (Single / Overlay)."""

    def __init__(self, folder: str, images: dict[str, str]):
        self.image_manager = ImageManager(None)
        self.image_manager.original_folder = folder
        self.image_manager.images = dict(images)
        self.image_manager._find_processed_base_path()
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["Single Image", "Overlay", "Side by Side"])
        self.status_bar = QStatusBar()
        self.controller = DisplayController(self)
        self.host = QWidget()
        self.controller.setup_display_area(self.host)
        self.view_mode_combo.currentIndexChanged.connect(self.controller.update_display_mode)

    def notify(self, message, kind="success", timeout=3200):
        """MainWindow's toast entry point (unused here)."""


class _Canvas:
    """One canvas under test: shows an image file and grabs its viewport at a zoom.

    ``images`` (library name -> path) and ``folder`` feed the Library's image manager.
    """

    def __init__(self, kind: str, folder: str, images: dict[str, str]):
        self.kind = kind
        if kind == "editor":
            self.editor = CorrectEditor()
            self.widget, self.view = self.editor, self.editor.graphics_view
        else:
            self.library = _Library(folder, images)
            self.widget = self.library.host
            self.view = self.library.controller.magnifying_view

    def present(self) -> "_Canvas":
        """Show the canvas at ``VIEW_SIZE``."""
        self.widget.resize(*VIEW_SIZE)
        self.widget.show()
        QApplication.processEvents()
        return self

    def show(self, name: str, path: str) -> None:
        """Open the image ``path`` (library name ``name``) in the canvas."""
        if self.kind == "editor":
            with _discarding():
                self.editor.load_image(path, images_base_folder=os.path.dirname(path))
            for layer_id in self.editor.layers:  # the photo alone
                self.editor.set_layer_visible(layer_id, False)
        else:
            self.library.image_manager.images[name] = path
            self.library.controller.display_selected_image_by_name(name)

    def grab(self, zoom: float, centre: tuple[float, float]) -> np.ndarray:
        """RGB of the viewport at ``zoom``, centred on image point ``centre`` (no brush ring)."""
        if self.kind == "editor":
            self.editor.router.canvas_hover_left()
        self.view.set_zoom(zoom)
        self.view.centerOn(QPointF(*centre))
        self.view.viewport().repaint()
        QApplication.processEvents()
        return qimage_to_rgb(self.view.viewport().grab().toImage())

    def image_rect(self, w: int, h: int):
        """Device rect of the image ``(0, 0, w, h)`` in the viewport."""
        return self.view.mapFromScene(QRectF(0, 0, w, h)).boundingRect()

    def content(self, rgb: np.ndarray, w: int, h: int, margin: int = 3) -> np.ndarray:
        """The part of ``rgb`` that shows the image, ``margin`` px inside its edges."""
        rect = self.image_rect(w, h)
        x0, y0 = max(0, rect.left() + margin), max(0, rect.top() + margin)
        x1 = min(rgb.shape[1], rect.right() - margin)
        y1 = min(rgb.shape[0], rect.bottom() - margin)
        return rgb[y0:y1, x0:x1]

    def close(self) -> None:
        """Close the canvas widget (its edits discarded)."""
        if self.kind == "editor":
            with _discarding():
                self.editor.reset()
        self.widget.close()
        self.widget.deleteLater()
        flush_deleted()  # deterministic


@contextlib.contextmanager
def _discarding():
    """The unsaved-changes prompt answers Discard meanwhile."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(editor_prompts, "ask_unsaved", lambda *_a: editor_prompts.DISCARD)
        yield


@pytest.fixture(scope="module")
def canvases(app, patterns):
    """Both canvases, built once for the module on the raster viewport."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(canvas_gl.ENV_VAR, "0")
        folder = os.path.dirname(patterns["sine"])
        built = {kind: _Canvas(kind, folder, patterns).present() for kind in CANVASES}
        yield built
        for canvas in built.values():
            canvas.close()


def _gray(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float64).mean(axis=2)


# ------------------------------------------------------------------ photo, magnified
@pytest.mark.parametrize("kind", CANVASES)
@pytest.mark.parametrize("zoom", [2.0, 4.0, 8.0, 16.0])
def test_photo_is_smooth_when_magnified(canvases, patterns, kind, zoom):
    canvas = canvases[kind]
    canvas.show("sine", patterns["sine"])
    region = canvas.content(canvas.grab(zoom, (W / 2, H / 2)), W, H)
    assert region.size
    assert sine_step_ratio(region, sine_image(W, H)) <= 1.5 / zoom
    assert equal_neighbour_fraction(region) <= 0.05


@pytest.mark.parametrize("kind", CANVASES)
def test_photo_is_exact_at_100_percent(canvases, patterns, kind):
    canvas = canvases[kind]
    canvas.show("sine", patterns["sine"])
    rgb = canvas.grab(1.0, (W / 2, H / 2))
    rect = canvas.image_rect(W, H)
    shown = _gray(rgb[rect.top() : rect.top() + H, rect.left() : rect.left() + W])
    assert shown.shape == (H, W)
    assert np.abs(shown - sine_image(W, H)).max() <= 1


# ------------------------------------------------------------------ photo, minified
def _chirp_band(canvas, rgb: np.ndarray, zoom: float) -> np.ndarray:
    """The rendered chirp from its left edge (as the scout cropped it)."""
    rect = canvas.image_rect(W, CHIRP_H)
    return rgb[rect.top() + 1 : rect.bottom() - 1, rect.left() : rect.left() + round(W * zoom)]


def _chirps(canvas, patterns, zoom):
    out = []
    for name in ("chirp0", "chirp1"):  # the second is shifted by one source pixel (a pan)
        canvas.show(name, patterns[name])
        out.append(_chirp_band(canvas, canvas.grab(zoom, (W / 2, CHIRP_H / 2)), zoom))
    return out


@pytest.mark.parametrize("kind", CANVASES)
@pytest.mark.parametrize("zoom", [0.2, 0.25, 0.33, 0.4])
def test_photo_does_not_alias_when_zoomed_out(canvases, patterns, kind, zoom):
    still, panned = _chirps(canvases[kind], patterns, zoom)
    assert chirp_alias_std(still, zoom) <= 35
    assert chirp_pan_delta(still, panned, zoom) <= 50


# Between 0.5 and 1 the contracted photo item draws level 0 bilinear (C6, spec 10.1): a plain
# bilinear resample of this chirp measures 61.3 / 95.6 at 0.6 and 48.7 / 73.4 at 0.75, nearest
# (the pre-merge editors) 88.6 / 154.6 and 88.2 / 155.8. The plan's 45 needs an area filter
# the contract does not have (see notes/P3.md, CONTRACT ISSUES).
@pytest.mark.parametrize("kind", CANVASES)
@pytest.mark.parametrize("zoom, max_alias, max_pan", [(0.6, 65, 100), (0.75, 52, 78)])
def test_photo_does_not_shimmer_between_half_and_full_size(
    canvases, patterns, kind, zoom, max_alias, max_pan
):
    still, panned = _chirps(canvases[kind], patterns, zoom)
    assert chirp_alias_std(still, zoom) <= max_alias
    assert chirp_pan_delta(still, panned, zoom) <= max_pan


@pytest.mark.parametrize("kind", CANVASES)
@pytest.mark.parametrize("zoom", [0.25, 0.5])
def test_zoomed_out_photo_never_shows_a_wrong_uniform_colour(canvases, patterns, kind, zoom):
    canvas = canvases[kind]
    canvas.show("stripes", patterns["stripes"])
    region = canvas.content(canvas.grab(zoom, (W / 2, H / 2)), W, H)
    assert region.size
    assert abs(_gray(region).mean() - 127.5) <= 12


# ------------------------------------------------------------------ no stale level
@pytest.mark.parametrize("kind", CANVASES)
def test_the_next_image_leaves_nothing_of_the_last_one_when_zoomed_out(canvases, patterns, kind):
    canvas = canvases[kind]
    canvas.show("gradient", patterns["gradient"])
    canvas.grab(0.25, (W / 2, H / 2))  # builds and draws the gradient's level
    canvas.show("flat200", patterns["flat200"])
    region = canvas.content(canvas.grab(0.25, (W / 2, H / 2)), W, H)
    assert region.size and np.abs(_gray(region) - 200).max() <= 1


def test_enhance_on_and_off_changes_the_zoomed_out_photo(app, patterns, monkeypatch):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    canvas = _Canvas("editor", "", {}).present()  # its own editor: Enhance sticks to it
    try:
        canvas.show("gradient", patterns["gradient"])
        raw = _gray(canvas.content(canvas.grab(0.25, (W / 2, H / 2)), W, H))
        controls = canvas.editor.norm_controls
        controls.method_combo.setCurrentText("Contrast Stretching")
        controls.apply_button.click()
        enhanced = _gray(canvas.content(canvas.grab(0.25, (W / 2, H / 2)), W, H))
        assert np.abs(enhanced - raw).mean() > 10  # the stretch reached the shown level
        controls.original_check.setChecked(True)  # "Show original"
        original = _gray(canvas.content(canvas.grab(0.25, (W / 2, H / 2)), W, H))
        assert np.abs(original - raw).max() <= 1
    finally:
        canvas.close()


# ------------------------------------------------------------------ GL on / off
@pytest.mark.parametrize("kind", CANVASES)
def test_an_opengl_swap_keeps_the_view_config_and_the_smooth_photo(
    app, patterns, monkeypatch, kind
):
    """Config level (offscreen cannot draw GL): a GL viewport and back leaves the render
    hints as they were, and the photo is still smooth at 4x."""
    monkeypatch.setattr(canvas_gl, "opengl_viewports_allowed", lambda: True)
    canvas = _Canvas(kind, os.path.dirname(patterns["sine"]), patterns)  # not shown yet
    try:
        assert canvas.view.viewport().inherits("QOpenGLWidget")
        hints = canvas.view.renderHints()
        canvas.view.set_opengl_viewport_enabled(False)
        assert not canvas.view.viewport().inherits("QOpenGLWidget")
        assert canvas.view.renderHints() == hints
        if kind == "editor":  # the editor view leaves pixmap smoothing to its items
            assert not hints & QPainter.RenderHint.SmoothPixmapTransform
        canvas.present()
        canvas.show("sine", patterns["sine"])
        region = canvas.content(canvas.grab(4.0, (W / 2, H / 2)), W, H)
        assert sine_step_ratio(region, sine_image(W, H)) <= 1.5 / 4.0
        assert equal_neighbour_fraction(region) <= 0.05
    finally:
        canvas.close()


# ------------------------------------------------------------------ the class labels
def _paint_classes(editor, size: int) -> None:
    """The class labels edited with the root class's brush of ``size``, shown."""
    editor.set_active_layer(CLASSES)
    editor.layers[CLASSES].set_active_class("root")
    editor.controls.size_changed.emit(size)  # the dock's size slider


def _class_ids(editor) -> np.ndarray:
    return editor.layers[CLASSES].document.history.plane


@pytest.mark.parametrize("zoom", [4, 8])
def test_class_labels_show_exact_pixels_when_magnified(canvases, patterns, zoom):
    canvas = canvases["editor"]
    editor = canvas.editor
    canvas.show("flat", patterns["flat"])
    editor.set_layer_visible(CLASSES, True)
    _paint_classes(editor, 2)
    rng = np.random.default_rng(zoom)
    x0, y0, n = 300, 220, 24
    for x, y in rng.integers(0, n, size=(80, 2)):
        drag(editor, (x0 + int(x), y0 + int(y)))
    plane = _class_ids(editor)[y0 : y0 + n, x0 : x0 + n] > 0
    assert 0 < plane.sum() < n * n

    rgb = canvas.grab(float(zoom), (x0 + n / 2, y0 + n / 2))
    origin = canvas.view.mapFromScene(QPointF(x0, y0))
    root = np.array([int(c) for c in bytes.fromhex(tokens.MASK[1:])])
    painted = np.round(0.6 * root + 0.4 * 90)  # the class colour at 60 % over the grey
    for row in range(n):
        for col in range(n):
            top, left = origin.y() + row * zoom, origin.x() + col * zoom
            block = rgb[top + 1 : top + zoom - 1, left + 1 : left + zoom - 1].reshape(-1, 3)
            assert (block == block[0]).all(), (row, col)  # one saved pixel, one flat block
            expected = painted if plane[row, col] else np.array([90, 90, 90])
            assert np.abs(block[0].astype(int) - expected).max() <= 2, (row, col)


@pytest.mark.parametrize("row", [240, 241, 242, 243])
def test_a_1px_class_label_stroke_stays_visible_when_zoomed_out(canvases, patterns, row):
    """Every row parity: the stroke shows as coverage, it never drops out of the render."""
    canvas = canvases["editor"]
    editor = canvas.editor
    canvas.show("flat", patterns["flat"])
    _paint_classes(editor, 1)
    drag(editor, (40, row), (600, row))
    for zoom in (0.25, 0.33, 0.5, 0.75):
        editor.set_layer_visible(CLASSES, True)
        shown = canvas.grab(zoom, (W / 2, H / 2)).astype(int)
        editor.set_layer_visible(CLASSES, False)
        hidden = canvas.grab(zoom, (W / 2, H / 2)).astype(int)
        start = canvas.view.mapFromScene(QPointF(40, row + 0.5))
        end = canvas.view.mapFromScene(QPointF(600, row + 0.5))
        visible = np.abs(shown - hidden).sum(axis=2) > 12
        band = visible[start.y() - 2 : start.y() + 3, start.x() + 2 : end.x() - 2]
        assert band.any(axis=0).mean() >= 0.95, zoom


# ------------------------------------------------------------------ Library overlay
def test_library_overlay_draws_a_2px_generated_file_soft(app, tmp_path, monkeypatch):
    """A generated file (lines 2 px wide) at 4 device px per file pixel: every file pixel
    of a line shows, and across a line no flat run is longer than 5 device px (bilinear
    gives 4 between the two pixel centres, a nearest draw 8)."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    fw, fh = 341, 256
    fake = np.zeros((fh, fw), np.uint8)
    fake[20:236, 100:102] = 255  # a vertical line, 2 px wide
    fake[120:122, 20:321] = 255  # a horizontal line, 2 px wide
    (tmp_path / "skeletons").mkdir()
    cv2.imwrite(str(tmp_path / "skeletons" / "plot_fake.png"), fake)
    photo = str(tmp_path / "plot.png")
    cv2.imwrite(photo, np.full((H, W, 3), 128, np.uint8))
    canvas = _Canvas("library", str(tmp_path), {"plot": photo}).present()
    try:
        canvas.library.view_mode_combo.setCurrentIndex(1)  # Overlay
        canvas.show("plot", photo)
        sx = W / fw  # image px per file px
        zoom = 4.0 / sx  # 4 device px per file px
        centre = (101 * sx, 121 * (H / fh))
        overlay = canvas.grab(zoom, centre).astype(int)
        (skeleton,) = [i for i in canvas.view.scene().items() if i.zValue() == 1]
        skeleton.setVisible(False)
        photo_only = canvas.grab(zoom, centre).astype(int)
        skeleton.setVisible(True)

        def device(fx: float, fy: float) -> QPoint:
            return canvas.view.mapFromScene(QPointF(fx * sx, fy * (H / fh)))

        ys, xs = np.nonzero(fake)
        on_screen = 0
        for fx, fy in zip(xs, ys, strict=True):
            p = device(fx + 0.5, fy + 0.5)
            if 0 <= p.x() < overlay.shape[1] and 0 <= p.y() < overlay.shape[0]:
                on_screen += 1
                assert (overlay[p.y(), p.x()] != photo_only[p.y(), p.x()]).any(), (fx, fy)
        assert on_screen > 100

        def longest_equal_run(pixels: np.ndarray) -> int:
            same = (pixels[1:] == pixels[:-1]).all(axis=1)
            best = run = 1
            for equal in same:
                run = run + 1 if equal else 1
                best = max(best, run)
            return best

        for fy in (60, 90, 150, 190):  # across the vertical line, away from the crossing
            left, right = device(100, fy + 0.5), device(102, fy + 0.5)
            assert longest_equal_run(overlay[left.y(), left.x() : right.x()]) <= 5, fy
        for fx in (40, 150, 200):  # across the horizontal line
            top, bottom = device(fx + 0.5, 120), device(fx + 0.5, 122)
            assert longest_equal_run(overlay[top.y() : bottom.y(), top.x()]) <= 5, fx
    finally:
        canvas.close()
