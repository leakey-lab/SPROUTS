"""Library display canvas (app/gui/display_controller.py).

PG-02: overlay / side by side must pair the image with ITS skeleton.
PG-03: a view-mode switch must not rebuild the library tree.
PG-13: fit on every new image, re-fit on resize, placeholder text instead of a
       blank or stale canvas (nothing selected, undecodable image).
PG-14 / UI-12: the skeleton overlay is drawn in the skeleton colour at 80 %; side by
       side shows both images at the same height.
RB-06 (display side): skeletons under non-ASCII folders still overlay.
Unified-editor plan P3 (spec 10.3, 11): the photo is a PhotoLayerItem (smooth at every
       zoom, decoded in Qt's geometry), the overlay a SkeletonLayerItem without vectors or
       halo drawn from the generated file itself (no nearest blow-up), and no item keeps a
       per-zoom device cache.
"""

import os

import numpy as np
import pytest
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QGraphicsItem,
    QGraphicsTextItem,
    QGraphicsView,
    QStatusBar,
    QWidget,
)

from app.gui.canvas_layers.photo import PhotoLayerItem
from app.gui.canvas_layers.skeleton import SkeletonLayerItem
from app.gui.display_controller import DisplayController
from app.gui.image_manager import ImageManager
from app.gui.widgets import tokens

SINGLE, OVERLAY, SIDE_BY_SIDE = 0, 1, 2
IMG_W, IMG_H = 320, 240  # library image
SKEL_W, SKEL_H = 341, 256  # Pix2Pix output size (smaller aspect, different size)
A_RGB, B_RGB, C_RGB = (90, 60, 40), (40, 60, 90), (60, 90, 40)
# B's vertical skeleton line covers file columns 99-101 (a 3 px pen at x = 100); A's
# horizontal one rows 59-61. Their centres in image pixels:
B_LINE_X = 100.5 * IMG_W / SKEL_W
A_LINE_Y = 60.5 * IMG_H / SKEL_H


def _image(path, rgb):
    img = QImage(IMG_W, IMG_H, QImage.Format.Format_RGB32)
    img.fill(QColor(*rgb))
    assert img.save(str(path))


def _skeleton(path, *, vertical: bool):
    """White-on-black skeleton: one vertical (x=100) or horizontal (y=60) line."""
    img = QImage(SKEL_W, SKEL_H, QImage.Format.Format_RGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setPen(QPen(QColor(255, 255, 255), 3))
    if vertical:
        p.drawLine(100, 0, 100, SKEL_H - 1)
    else:
        p.drawLine(0, 60, SKEL_W - 1, 60)
    p.end()
    assert img.save(str(path))


def _project(root):
    """A (horizontal skeleton), B (vertical skeleton), C (no skeleton)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "skeletons").mkdir()
    _image(root / "A.png", A_RGB)
    _image(root / "B.png", B_RGB)
    _image(root / "C.png", C_RGB)
    _skeleton(root / "skeletons" / "A_fake.png", vertical=False)
    _skeleton(root / "skeletons" / "B_fake.png", vertical=True)
    return root


class _Window:
    """What DisplayController uses from MainWindow."""

    def __init__(self, image_manager):
        self.image_manager = image_manager
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["Single Image", "Overlay", "Side by Side"])
        self.status_bar = QStatusBar()
        self.notices = []
        self.populate_calls = 0

    def notify(self, message, kind="success", timeout=3200):
        self.notices.append((message, kind))

    def populate_file_list(self):
        self.populate_calls += 1


@pytest.fixture
def display(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")
    return _make_display(app, _project(tmp_path / "project"))


def _make_display(app, root):
    manager = ImageManager(None)
    manager.original_folder = str(root)
    manager.images = {
        os.path.splitext(f)[0]: str(root / f)
        for f in os.listdir(root)
        if f.endswith((".png", ".jpg"))
    }
    manager._find_processed_base_path()
    window = _Window(manager)
    controller = DisplayController(window)
    host = QWidget()
    controller.setup_display_area(host)
    view = controller.magnifying_view
    window.view_mode_combo.currentIndexChanged.connect(controller.update_display_mode)
    host.resize(700, 520)
    host.show()
    app.processEvents()
    return controller, window, host, view, root


def _layer_items(view):
    """The photo and skeleton items on the canvas, bottom-up then left to right."""
    items = [i for i in view.scene().items() if isinstance(i, (PhotoLayerItem, SkeletonLayerItem))]
    return sorted(items, key=lambda i: (i.zValue(), i.sceneBoundingRect().left()))


def _texts(view):
    return [i.toPlainText() for i in view.scene().items() if isinstance(i, QGraphicsTextItem)]


def _overlay(view) -> SkeletonLayerItem:
    overlays = [i for i in _layer_items(view) if isinstance(i, SkeletonLayerItem)]
    assert len(overlays) == 1, "expected one skeleton overlay item"
    return overlays[0]


def _grab(view) -> QImage:
    return view.viewport().grab().toImage()


def _rgb_at(view, image: QImage, x: float, y: float) -> tuple[int, int, int]:
    """What the canvas shows at image point (x, y)."""
    colour = image.pixelColor(view.mapFromScene(QPointF(x, y)))
    return colour.red(), colour.green(), colour.blue()


def _near(rgb, expected, tolerance=3):
    return all(abs(a - b) <= tolerance for a, b in zip(rgb, expected, strict=True))


def _blend(over, under, alpha):
    return tuple(round(alpha * a + (1 - alpha) * b) for a, b in zip(over, under, strict=True))


# ------------------------------------------------------------------------ PG-02
def test_overlay_pairs_the_current_image_with_its_own_skeleton(display):
    controller, window, _host, view, _root = display
    combo = window.view_mode_combo
    combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("A")
    combo.setCurrentIndex(SINGLE)
    controller.display_selected_image_by_name("B")
    combo.setCurrentIndex(OVERLAY)  # back to overlay without reselecting

    assert os.path.basename(controller.current_fake_image) == "B_fake.png"
    grab = _grab(view)
    assert not _near(_rgb_at(view, grab, B_LINE_X, 20), B_RGB)  # B's skeleton is drawn
    assert _rgb_at(view, grab, 10, A_LINE_Y) == B_RGB  # A's skeleton is not

    combo.setCurrentIndex(SIDE_BY_SIDE)
    assert os.path.basename(controller.current_fake_image) == "B_fake.png"


def test_image_without_a_skeleton_shows_no_stale_overlay(display):
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("A")
    controller.display_selected_image_by_name("C")
    assert controller.current_fake_image is None
    assert [type(i) for i in _layer_items(view)] == [PhotoLayerItem]


# ------------------------------------------------------------------------ PG-03
def test_view_mode_switches_do_not_rebuild_the_library_tree(display):
    controller, window, _host, _view, _root = display
    controller.display_selected_image_by_name("A")
    for index in (OVERLAY, SIDE_BY_SIDE, SINGLE):
        window.view_mode_combo.setCurrentIndex(index)
    assert window.populate_calls == 0


# ------------------------------------------------------------------------ PG-13
def test_new_images_are_fitted_and_refit_on_resize(app, display):
    controller, _window, host, view, _root = display
    controller.display_selected_image_by_name("A")
    view.zoom_by(3.0)
    controller.display_selected_image_by_name("B")  # a new image is fitted again

    def fit_zoom():
        m = view.FIT_MARGIN
        vw, vh = view.viewport().width(), view.viewport().height()
        return min((vw - 2 * m) / IMG_W, (vh - 2 * m) / IMG_H)

    assert view.zoom_factor == pytest.approx(fit_zoom())
    host.resize(1000, 800)
    app.processEvents()
    assert view.zoom_factor == pytest.approx(fit_zoom())


def test_setup_shows_a_placeholder_and_returns_the_canvas(app):
    controller = DisplayController(_Window(ImageManager(None)))
    host = QWidget()
    view = controller.setup_display_area(host)
    assert view is controller.magnifying_view
    assert _layer_items(view) == []
    assert any(text.strip() for text in _texts(view))


@pytest.mark.parametrize("mode", [SINGLE, OVERLAY, SIDE_BY_SIDE])
def test_undecodable_image_replaces_the_canvas_with_a_message(display, mode):
    controller, window, _host, view, root = display
    (root / "broken.jpg").write_bytes(b"not an image")
    window.image_manager.images["broken"] = str(root / "broken.jpg")
    window.view_mode_combo.setCurrentIndex(mode)
    controller.display_selected_image_by_name("A")

    controller.display_selected_image_by_name("broken")

    assert _layer_items(view) == []  # nothing stale from A
    assert any("broken.jpg" in text for text in _texts(view))
    assert any("broken.jpg" in msg and kind == "danger" for msg, kind in window.notices)


def test_reset_forgets_the_image_and_shows_the_placeholder(display):
    controller, _window, _host, view, _root = display
    controller.display_selected_image_by_name("A")
    controller.reset()
    assert controller.current_image is None
    assert _layer_items(view) == []
    assert _texts(view)


# ------------------------------------------------------------------ PG-14 / UI-12
def test_overlay_is_drawn_in_the_skeleton_colour_at_80_percent(display):
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("B")

    skel = QColor(tokens.SKEL)
    line = _rgb_at(view, _grab(view), B_LINE_X, 50)
    assert _near(line, _blend((skel.red(), skel.green(), skel.blue()), B_RGB, 0.8))
    overlay = _overlay(view)
    assert overlay.opacity() == pytest.approx(0.8) and overlay.zValue() == 1


def test_overlay_is_the_generated_file_itself_without_a_halo(display):
    """No nearest blow-up to the photo size: the item holds the file's pixels and its
    transform scales them; no halo darkens the photo beside the line."""
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("B")

    overlay = _overlay(view)
    assert overlay.boundingRect() == QRectF(0, 0, SKEL_W, SKEL_H)
    assert overlay.sceneBoundingRect() == QRectF(0, 0, IMG_W, IMG_H)
    grab = _grab(view)
    left = view.mapFromScene(QPointF(B_LINE_X - 8, 40))
    right = view.mapFromScene(QPointF(B_LINE_X + 8, 60))
    window_rgb = np.array(
        [
            [grab.pixelColor(x, y).getRgb()[:3] for x in range(left.x(), right.x() + 1)]
            for y in range(left.y(), right.y() + 1)
        ]
    )
    assert (window_rgb.min(axis=(0, 1)) >= np.array(B_RGB) - 2).all()  # nothing darker


@pytest.mark.parametrize("zoom", [0.4, 1.0, 4.0, 8.0])
def test_overlay_is_pooled_when_minified_and_bilinear_otherwise(display, zoom):
    """Library (spec D31): the pooled raster when minified, else the file drawn bilinear."""
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("B")
    view.set_zoom(zoom)
    _grab(view)
    scale = zoom * IMG_W / SKEL_W  # device px per file px
    assert _overlay(view).mode_painted() == ("raster-lod" if scale < 1 else "raster-smooth")


def test_side_by_side_shows_both_images_at_the_same_height(display):
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(SIDE_BY_SIDE)
    controller.display_selected_image_by_name("A")

    left, right = _layer_items(view)
    assert isinstance(left, PhotoLayerItem) and isinstance(right, PhotoLayerItem)
    assert right.image_size() == (SKEL_W, SKEL_H)  # the file itself, scaled by the item
    lr, rr = left.sceneBoundingRect(), right.sceneBoundingRect()
    assert lr.height() == pytest.approx(IMG_H)
    assert rr.height() == pytest.approx(lr.height())
    assert rr.top() == pytest.approx(lr.top())
    assert rr.left() >= lr.right()  # beside, not overlapping


@pytest.mark.parametrize("mode", [SINGLE, OVERLAY, SIDE_BY_SIDE])
def test_nothing_is_re_rendered_into_a_cache_per_zoom_step(display, mode):
    """A device cache is redrawn (and on GL uploaded again) at every zoom step: 1-6 ms per
    zoom frame, for the items (spec 10.4) and for the view background (a solid colour)."""
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(mode)
    controller.display_selected_image_by_name("B")
    items = _layer_items(view)
    assert items and isinstance(items[0], PhotoLayerItem)
    assert all(i.cacheMode() == QGraphicsItem.CacheMode.NoCache for i in items)
    assert view.cacheMode() == QGraphicsView.CacheModeFlag.CacheNone


def test_photo_is_decoded_in_qt_geometry(app, tmp_path, monkeypatch):
    """EXIF orientation is ignored, as every other canvas and every saved mask does."""
    from PIL import Image

    monkeypatch.setenv("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")
    root = _project(tmp_path / "project")
    picture = Image.fromarray(np.full((40, 80, 3), 128, np.uint8))
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees clockwise to display"
    picture.save(str(root / "R.jpg"), exif=exif.tobytes())
    controller, _window, _host, view, _root = _make_display(app, root)
    controller.display_selected_image_by_name("R")
    (photo,) = _layer_items(view)
    assert photo.image_size() == (80, 40)


# ------------------------------------------------------------------ RB-06 display side
def test_overlay_reads_skeletons_under_non_ascii_folders(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")
    controller, window, _host, view, _root = _make_display(app, _project(tmp_path / "Données_根系"))
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("B")
    assert isinstance(_layer_items(view)[0], PhotoLayerItem)
    assert not _near(_rgb_at(view, _grab(view), B_LINE_X, 50), B_RGB)
