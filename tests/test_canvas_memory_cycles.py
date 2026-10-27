"""Canvas items are freed as soon as their last reference goes, without the cyclic GC
(app/gui/canvas_layers/, app/gui/skeleton_overlay.py, app/imaging/pyramid.py).

The Library shows every image in a fresh scene with a fresh photo item; the old scene and
its items go when the next one is set. An item caught in a reference cycle (its tile maker
a bound method of the item itself was one) outlives that until a cyclic-GC pass finds it:
until then its pyramid and tile pixmaps (about 26 MB per 2048 x 1536 image) stay
allocated, and the pass that frees them pauses whichever thread runs it (the GUI thread,
or a worker, which must not destroy pixmaps and timers). These tests turn the automatic
GC off, show each item in a view (tiles made, idle warm-up run), drop it, and check
through a weak reference that it is gone.
"""

import gc
import weakref

import numpy as np
import pytest
from PyQt6.QtCore import QRect, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QStatusBar,
    QWidget,
)

from app.gui import canvas_gl
from app.gui.canvas_layers.photo import PhotoLayerItem
from app.gui.canvas_layers.raster import RasterLayerItem
from app.gui.canvas_layers.skeleton import SkeletonLayerItem
from app.gui.canvas_view import CanvasViewMixin
from app.gui.display_controller import DisplayController
from app.gui.image_manager import ImageManager
from app.gui.skeleton_overlay import DotsItem
from app.imaging.pyramid import ImagePyramid
from tests.qt_wait import wait_until

W, H = 1400, 1000


class _View(CanvasViewMixin, QGraphicsView):
    def __init__(self):
        super().__init__(None)
        self._init_canvas()
        self.setFrameShape(QFrame.Shape.NoFrame)


@pytest.fixture
def view(app, monkeypatch):
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")  # a raster viewport, as offscreen
    view = _View()
    view.resize(400, 300)
    view.show()
    yield view
    view.setScene(None)
    view.close()
    view.deleteLater()
    app.processEvents()


@pytest.fixture
def no_gc():
    """The automatic cyclic GC off for the test: only reference counting frees."""
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    yield
    if enabled:
        gc.enable()


@pytest.fixture(autouse=True)
def short_warm_delay(monkeypatch):
    """The idle warm-ups start 10 ms after the last paint (or set_mask), not 120-150 ms:
    these tests need them run, not their delay (the warm-up tests pin that)."""
    for cls in (PhotoLayerItem, RasterLayerItem, SkeletonLayerItem):
        monkeypatch.setattr(cls, "WARM_DELAY_MS", 10)


def _idle(*items) -> None:
    """Run the event loop until no item's warm-up timer is pending (each slice restarts it;
    an item that never planned a warm-up has no timer)."""

    def idle() -> bool:
        return not any(i._warm_timer is not None and i._warm_timer.isActive() for i in items)

    assert wait_until(idle), "the idle warm-up did not finish"


def _photo(tile: int = 64) -> PhotoLayerItem:
    item = PhotoLayerItem()
    item.TILE = tile  # several tiles per level: the tiled path, not one pixmap
    item.set_image(np.random.default_rng(1).integers(0, 256, (H, W, 3), dtype=np.uint8))
    return item


def _show(view: _View, item, zooms=(0.3, 0.9, 2.0)) -> QGraphicsScene:
    """``item`` in a fresh scene of ``view``, painted at ``zooms``, each followed by the idle
    warm-up (its timers fire)."""
    scene = QGraphicsScene()
    scene.addItem(item)
    view.setScene(scene)
    view.set_content_rect(item.sceneBoundingRect())
    for zoom in zooms:
        view.set_zoom(zoom)
        view.viewport().repaint()
        _idle(item)
    return scene


def test_a_photo_item_removed_from_its_scene_is_freed_at_once(view, no_gc):
    item = _photo()
    scene = _show(view, item)
    assert item.built_tiles()  # tiles were made (and warmed ahead)
    ref = weakref.ref(item)
    scene.removeItem(item)
    del item
    assert ref() is None


def test_a_photo_item_goes_with_its_scene(view, no_gc):
    """The Library's case: the next image's scene replaces the one holding the item."""
    item = _photo()
    _show(view, item)
    ref, levels = weakref.ref(item), weakref.ref(item._levels)
    del item
    view.setScene(QGraphicsScene())
    QTest.qWait(10)
    assert ref() is None
    assert levels() is None  # with its tile pixmaps


def test_a_new_image_lets_the_old_levels_go_at_once(view, no_gc):
    """Correct keeps one photo item: the old image's pyramid and tiles go at set_image."""
    item = _photo()
    scene = _show(view, item)
    levels = weakref.ref(item._levels)
    item.set_image(np.zeros((H, W, 3), np.uint8))
    assert levels() is None
    view.viewport().repaint()
    assert item.built_tiles()  # the new image draws
    scene.removeItem(item)


def _canvas() -> QImage:
    image = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0x80336699)
    return image


def test_a_raster_item_with_coverage_tiles_is_freed_at_once(view, no_gc):
    canvas = _canvas()
    item = RasterLayerItem(lambda: canvas)
    scene = _show(view, item, zooms=(0.3, 0.6, 2.0))
    assert item.coverage_tiles() and item.built_tiles()  # coverage and exact tiles made
    item.canvas_changed(QRect(10, 10, 40, 40))
    view.set_zoom(0.3)
    view.viewport().repaint()  # coverage remade over the edit
    ref = weakref.ref(item)
    scene.removeItem(item)
    del item
    assert ref() is None


def test_a_raster_item_goes_with_its_scene(view, no_gc):
    canvas = _canvas()
    item = RasterLayerItem(lambda: canvas)
    _show(view, item, zooms=(0.3,))
    assert item.coverage_tiles()
    ref = weakref.ref(item)
    del item
    view.setScene(QGraphicsScene())
    QTest.qWait(10)
    assert ref() is None


@pytest.mark.parametrize("vectors", [True, False], ids=["correct", "library"])
def test_a_skeleton_item_is_freed_at_once(view, no_gc, vectors):
    mask = np.zeros((H, W), np.uint8)
    mask[500, 100:1300] = 255
    mask[100:900, 700] = 255
    item = SkeletonLayerItem(halo=vectors, vectors=vectors)
    scene = QGraphicsScene()
    scene.addItem(item)
    item.set_mask(mask)  # in a scene: the idle vector warm-up is set up
    view.setScene(scene)
    view.set_content_rect(item.sceneBoundingRect())
    for zoom in (0.3, 1.0, 3.0):
        view.set_zoom(zoom)
        view.viewport().repaint()
        _idle(item)
    assert item.mode_painted()
    ref = weakref.ref(item)
    scene.removeItem(item)
    del item
    assert ref() is None


def test_a_dots_item_is_freed_at_once(view, no_gc):
    item = DotsItem(4)
    item.set_points(np.array([[10.0, 10.0], [300.0, 200.0], [900.0, 700.0]]))
    scene = QGraphicsScene()
    scene.addItem(item)
    view.setScene(scene)
    view.set_content_rect(QRectF(0, 0, W, H))
    view.viewport().repaint()
    ref = weakref.ref(item)
    scene.removeItem(item)
    del item
    assert ref() is None


class _Window:
    """What the Library's DisplayController uses from MainWindow."""

    def __init__(self, image_manager):
        self.image_manager = image_manager
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["Single Image", "Overlay", "Side by Side"])
        self.status_bar = QStatusBar()

    def notify(self, message, kind="success", timeout=3200):
        pass


def _library_project(root):
    """Photos A and B (640 x 480) with generated skeletons (341 x 256)."""
    (root / "skeletons").mkdir(parents=True)
    for name, rgb in (("A", (90, 60, 40)), ("B", (40, 60, 90))):
        photo = QImage(640, 480, QImage.Format.Format_RGB32)
        photo.fill(QColor(*rgb))
        assert photo.save(str(root / f"{name}.png"))
        skeleton = QImage(341, 256, QImage.Format.Format_RGB32)
        skeleton.fill(QColor(0, 0, 0))
        painter = QPainter(skeleton)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(0, 60, 340, 60)
        painter.end()
        assert skeleton.save(str(root / "skeletons" / f"{name}_fake.png"))
    return root


@pytest.mark.parametrize("mode", ["Single Image", "Overlay", "Side by Side"])
def test_the_library_lets_the_previous_image_go_at_once(app, tmp_path, monkeypatch, no_gc, mode):
    """Stepping in the Library frees the previous image's photo and skeleton items when the
    next image's scene replaces theirs: their pixmaps and pyramids (about 26 MB per 2048 px
    image) never pile up for a GC pass."""
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    root = _library_project(tmp_path / "project")
    manager = ImageManager(None)
    manager.original_folder = str(root)
    manager.images = {name: str(root / f"{name}.png") for name in ("A", "B")}
    manager._find_processed_base_path()
    manager.reload_for_view_mode(mode)
    window = _Window(manager)
    window.view_mode_combo.setCurrentText(mode)
    controller = DisplayController(window)
    host = QWidget()
    controller.setup_display_area(host)
    host.resize(700, 520)
    host.show()
    try:
        controller.display_selected_image_by_name("A")
        view = controller.magnifying_view
        view.viewport().repaint()
        kinds = (PhotoLayerItem, SkeletonLayerItem)
        _idle(*[i for i in view.scene().items() if isinstance(i, kinds)])  # the idle warm-up
        shown = [weakref.ref(i) for i in view.scene().items() if isinstance(i, kinds)]
        assert len(shown) == (1 if mode == "Single Image" else 2)
        controller.display_selected_image_by_name("B")
        assert [r() for r in shown] == [None] * len(shown)
    finally:
        controller.read_ahead.close()
        host.close()
        host.deleteLater()
        app.processEvents()


def test_a_pyramid_is_freed_at_once(no_gc):
    pyramid = ImagePyramid(np.zeros((H, W, 3), np.uint8))
    pyramid.level(3)
    pyramid.invalidate((0, 0, 50, 50))
    pyramid.level(3)
    ref = weakref.ref(pyramid)
    del pyramid
    assert ref() is None
