"""Library display canvas (app/gui/display_controller.py).

PG-02: overlay / side by side must pair the image with ITS skeleton.
PG-03: a view-mode switch must not rebuild the library tree.
PG-13: fit on every new image, re-fit on resize, placeholder text instead of a
       blank or stale canvas (nothing selected, undecodable image).
PG-14 / UI-12: the skeleton overlay is drawn in the skeleton colour; side by
       side shows both images at the same height.
RB-06 (display side): skeletons under non-ASCII folders still overlay.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage, QPainter, QPen  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QGraphicsPixmapItem,
    QGraphicsTextItem,
    QStatusBar,
    QWidget,
)

from app.gui.display_controller import DisplayController  # noqa: E402
from app.gui.image_manager import ImageManager  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402

SINGLE, OVERLAY, SIDE_BY_SIDE = 0, 1, 2
IMG_W, IMG_H = 320, 240  # library image
SKEL_W, SKEL_H = 341, 256  # Pix2Pix output size (smaller aspect, different size)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _image(path, color):
    img = QImage(IMG_W, IMG_H, QImage.Format.Format_RGB32)
    img.fill(color)
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
    _image(root / "A.png", QColor(90, 60, 40))
    _image(root / "B.png", QColor(40, 60, 90))
    _image(root / "C.png", QColor(60, 90, 40))
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
        os.path.splitext(f)[0]: str(root / f) for f in os.listdir(root) if f.endswith(".png")
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


def _pixmap_items(view):
    items = [i for i in view.scene().items() if isinstance(i, QGraphicsPixmapItem)]
    return sorted(items, key=lambda i: (i.zValue(), i.sceneBoundingRect().left()))


def _texts(view):
    return [i.toPlainText() for i in view.scene().items() if isinstance(i, QGraphicsTextItem)]


def _overlay_image(view) -> QImage:
    overlays = [i for i in _pixmap_items(view) if i.zValue() >= 1]
    assert len(overlays) == 1, "expected one skeleton overlay item"
    return overlays[0].pixmap().toImage()


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
    overlay = _overlay_image(view)
    x_line = round(100 * IMG_W / SKEL_W)  # B's vertical line, scaled to the image
    y_off = round(20 * IMG_H / SKEL_H)
    assert overlay.pixelColor(x_line, y_off).alpha() > 0  # B's skeleton is drawn
    y_line = round(60 * IMG_H / SKEL_H)
    assert overlay.pixelColor(10, y_line).alpha() == 0  # A's skeleton is not

    combo.setCurrentIndex(SIDE_BY_SIDE)
    assert os.path.basename(controller.current_fake_image) == "B_fake.png"


def test_image_without_a_skeleton_shows_no_stale_overlay(display):
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("A")
    controller.display_selected_image_by_name("C")
    assert controller.current_fake_image is None
    assert [i.zValue() for i in _pixmap_items(view)] == [0]


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
    assert _pixmap_items(view) == []
    assert any(text.strip() for text in _texts(view))


@pytest.mark.parametrize("mode", [SINGLE, OVERLAY, SIDE_BY_SIDE])
def test_undecodable_image_replaces_the_canvas_with_a_message(display, mode):
    controller, window, _host, view, root = display
    (root / "broken.jpg").write_bytes(b"not an image")
    window.image_manager.images["broken"] = str(root / "broken.jpg")
    window.view_mode_combo.setCurrentIndex(mode)
    controller.display_selected_image_by_name("A")

    controller.display_selected_image_by_name("broken")

    assert _pixmap_items(view) == []  # nothing stale from A
    assert any("broken.jpg" in text for text in _texts(view))
    assert any("broken.jpg" in msg and kind == "danger" for msg, kind in window.notices)


def test_reset_forgets_the_image_and_shows_the_placeholder(display):
    controller, _window, _host, view, _root = display
    controller.display_selected_image_by_name("A")
    controller.reset()
    assert controller.current_image is None
    assert _pixmap_items(view) == []
    assert _texts(view)


# ------------------------------------------------------------------ PG-14 / UI-12
def test_overlay_is_drawn_in_the_skeleton_colour(display):
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("B")

    x_line = round(100 * IMG_W / SKEL_W)
    color = _overlay_image(view).pixelColor(x_line, 50)
    skel = QColor(tokens.SKEL)
    assert abs(color.red() - skel.red()) <= 2
    assert abs(color.green() - skel.green()) <= 2
    assert abs(color.blue() - skel.blue()) <= 2
    assert abs(color.alpha() - DisplayController.OVERLAY_ALPHA) <= 1


def test_side_by_side_shows_both_images_at_the_same_height(display):
    controller, window, _host, view, _root = display
    window.view_mode_combo.setCurrentIndex(SIDE_BY_SIDE)
    controller.display_selected_image_by_name("A")

    left, right = _pixmap_items(view)
    lr, rr = left.sceneBoundingRect(), right.sceneBoundingRect()
    assert lr.height() == pytest.approx(IMG_H)
    assert rr.height() == pytest.approx(lr.height())
    assert rr.top() == pytest.approx(lr.top())
    assert rr.left() >= lr.right()  # beside, not overlapping


# ------------------------------------------------------------------ RB-06 display side
def test_overlay_reads_skeletons_under_non_ascii_folders(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")
    controller, window, _host, view, _root = _make_display(app, _project(tmp_path / "Données_根系"))
    window.view_mode_combo.setCurrentIndex(OVERLAY)
    controller.display_selected_image_by_name("B")
    x_line = round(100 * IMG_W / SKEL_W)
    assert _overlay_image(view).pixelColor(x_line, 50).alpha() > 0
