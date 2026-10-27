"""Regression test: zoomed-out image views must paint the canvas colour outside the image.

The global SPROUTS stylesheet sets ``QWidget { background-color: transparent; }``.
Qt's style-sheet style then turns off ``autoFillBackground`` on each graphics
view's viewport, so nothing clears it between frames. A view with no background
brush of its own paints nothing outside the scene. Once the zoomed-out image is
smaller than the viewport, the OpenGL viewport keeps pixels from earlier frames,
and each zoom step draws a smaller copy on top of the old ones ("hall of
mirrors").

The OpenGL path cannot be pixel-checked offscreen, so the test pins two things:
- each view owns an opaque canvas-colour background brush, which is the thing
  that repaints the area outside the scene on every backend, and keeps it
  across OpenGL <-> QWidget viewport swaps;
- after a swap to the QWidget viewport and a zoom-out to the minimum zoom,
  the pixels outside the scene really are the canvas colour.

The views are discovered, not listed: every class under ``app/`` whose bases name
``QGraphicsView``, or (transitively) another discovered class, is checked, so a new
canvas is covered the moment it exists. Each case imports its view lazily, so a
module that fails to import fails only its own cases.
"""

import ast
import importlib
import textwrap
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.gui.brush_preview import BrushPreview
from app.gui.widgets import tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_QSS = REPO_ROOT / "resources" / "themes" / "dark_theme.qss"

CANVAS = QColor(tokens.BG_0)
#: Views that must exist (the discovery would otherwise pass vacuously on an empty result).
REQUIRED_VIEWS = {"MagnifyingGraphicsView", "EditorCanvasView"}


# --------------------------------------------------------------------------- discovery
@dataclass(frozen=True)
class ViewClass:
    """A ``QGraphicsView`` subclass found in the source (not imported yet)."""

    module: str
    name: str
    bases: tuple[str, ...]
    sets_background: bool  # its own __init__ calls configure_canvas_background


def _base_names(node: ast.ClassDef) -> tuple[str, ...]:
    """``Name`` bases by id, ``module.Name`` bases by attribute."""
    names = (getattr(base, "id", getattr(base, "attr", None)) for base in node.bases)
    return tuple(name for name in names if name)


def _init_sets_background(node: ast.ClassDef) -> bool:
    for member in node.body:
        if isinstance(member, ast.FunctionDef) and member.name == "__init__":
            return any(
                isinstance(call, ast.Call)
                and getattr(call.func, "id", getattr(call.func, "attr", None))
                == "configure_canvas_background"
                for call in ast.walk(member)
            )
    return False


def discover_view_classes(sources: dict[str, str]) -> dict[str, ViewClass]:
    """The view classes of ``sources`` (``{module: source}``) by name.

    A class is a view when a base names ``QGraphicsView`` or another view class of
    ``sources``, in any module and in any definition order.
    """
    classes = [
        ViewClass(module, node.name, _base_names(node), _init_sets_background(node))
        for module, source in sources.items()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef)
    ]
    view_names = {"QGraphicsView"}
    while True:
        views = [cls for cls in classes if view_names.intersection(cls.bases)]
        names = {cls.name for cls in views}
        if names <= view_names:
            break
        view_names |= names
    found: dict[str, ViewClass] = {}
    for cls in views:
        other = found.setdefault(cls.name, cls)
        assert other is cls, f"two view classes named {cls.name}: {other.module}, {cls.module}"
    return found


def sets_canvas_background(view: ViewClass, found: dict[str, ViewClass]) -> bool:
    """True when ``view``'s own ``__init__``, or a view ancestor's, sets the background."""
    if view.sets_background:
        return True
    return any(sets_canvas_background(found[base], found) for base in view.bases if base in found)


def _module_name(path: Path) -> str:
    parts = path.relative_to(REPO_ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@cache
def app_view_classes() -> dict[str, ViewClass]:
    """Every view class under ``app/`` (parsed once per session)."""
    sources = {
        _module_name(path): path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "app").rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    return discover_view_classes(sources)


VIEWS = [
    pytest.param(view, id=view.name)
    for view in sorted(app_view_classes().values(), key=lambda v: (v.module, v.name))
]


def _load(view: ViewClass) -> type[QGraphicsView]:
    return getattr(importlib.import_module(view.module), view.name)


# --------------------------------------------------------------------------- pixel checks
@pytest.fixture(scope="module")
def app():
    """QApplication with the real app stylesheet (as ``main.apply_stylesheet`` loads it)."""
    a = QApplication.instance() or QApplication([])
    previous = a.styleSheet()
    a.setStyleSheet(APP_QSS.read_text(encoding="utf-8"))
    yield a
    a.setStyleSheet(previous)


def _min_zoom(view: QGraphicsView) -> float:
    return getattr(view, "MIN_ZOOM", None) or view.min_zoom


def _image_scene(w: int, h: int) -> QGraphicsScene:
    pm = QPixmap(w, h)
    pm.fill(QColor(255, 0, 255))  # nothing like the canvas colour
    scene = QGraphicsScene()
    scene.addItem(QGraphicsPixmapItem(pm))
    scene.setSceneRect(QRectF(0, 0, w, h))
    return scene


def _assert_canvas_brush(view: QGraphicsView) -> None:
    brush = view.backgroundBrush()
    assert brush.style() == Qt.BrushStyle.SolidPattern, "view has no background brush"
    assert brush.isOpaque(), "view background brush is not opaque"
    assert brush.color().name() == CANVAS.name()


@pytest.mark.parametrize("view_class", VIEWS)
def test_view_owns_opaque_canvas_brush_across_viewport_swaps(app, view_class):
    view = _load(view_class)(None)
    _assert_canvas_brush(view)

    view.set_opengl_viewport_enabled(True)
    _assert_canvas_brush(view)
    view.set_opengl_viewport_enabled(False)
    _assert_canvas_brush(view)


@pytest.mark.parametrize("view_class", VIEWS)
def test_zoomed_out_view_paints_canvas_colour_outside_scene(app, view_class):
    view = _load(view_class)(None)
    # Swap to the OpenGL viewport and back, so the check runs on a freshly
    # created QWidget viewport (the path used while visualizations are open).
    view.set_opengl_viewport_enabled(True)
    view.set_opengl_viewport_enabled(False)
    assert type(view.viewport()).__name__ == "QWidget"

    view.setScene(_image_scene(800, 600))
    view.resize(400, 300)
    view.show()
    app.processEvents()

    # Seed: at 100 % the image covers the whole viewport.
    view.setTransform(QTransform())
    view.centerOn(400, 300)
    view.viewport().grab()

    zoom = _min_zoom(view)
    view.setTransform(QTransform.fromScale(zoom, zoom))
    view.centerOn(400, 300)
    app.processEvents()

    img = view.viewport().grab().toImage()
    dpr = img.width() / view.viewport().width()
    scene_in_vp = view.mapFromScene(view.sceneRect()).boundingRect()
    assert scene_in_vp.width() < view.viewport().width() / 2  # really zoomed out

    vw, vh = view.viewport().width(), view.viewport().height()
    samples = [QPoint(2, 2), QPoint(vw - 3, 2), QPoint(2, vh - 3), QPoint(vw - 3, vh - 3)]
    samples += [QPoint(vw // 2, 2), QPoint(2, vh // 2)]
    for p in samples:
        assert not scene_in_vp.contains(p)
        c = img.pixelColor(int(p.x() * dpr), int(p.y() * dpr))
        assert (c.red(), c.green(), c.blue(), c.alpha()) == (
            CANVAS.red(),
            CANVAS.green(),
            CANVAS.blue(),
            255,
        ), f"pixel {p.x()},{p.y()} outside the scene is {c.name(QColor.NameFormat.HexArgb)}"

    view.close()


def _frame_after_interaction(app, view_cls, mode=None):
    """The window's backing store (what is on screen) after zooming out and then
    sweeping the brush ring across the zoomed-out image."""
    view = view_cls(None)
    if mode is not None:
        view.setViewportUpdateMode(mode)
    scene = _image_scene(800, 600)
    view.setScene(scene)
    view.resize(400, 300)
    view.show()
    app.processEvents()
    view.set_zoom(3.0)
    app.processEvents()
    for _ in range(12):
        view.zoom_by(0.8)
        app.processEvents()
    ring = BrushPreview(scene)
    ring.set_diameter(40)
    ring.set_visible(True)
    for i in range(30):
        ring.move_to_pixel(QPoint(100 + 19 * i, 150 + 7 * i))
        app.processEvents()
    frame = app.primaryScreen().grabWindow(view.winId()).toImage()
    view.close()
    return frame


@pytest.mark.parametrize("view_class", VIEWS)
def test_partial_raster_updates_leave_no_stale_pixels(app, monkeypatch, view_class):
    """PE-05: the raster viewport repaints only dirty regions. That must put the
    same pixels on screen as full-viewport updates: no stale copies around the
    zoomed-out image and no trails behind the moving brush ring."""
    monkeypatch.setenv("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")
    view_cls = _load(view_class)
    partial = _frame_after_interaction(app, view_cls)
    full = _frame_after_interaction(
        app, view_cls, QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    )
    assert not partial.isNull()
    assert partial == full


# --------------------------------------------------------------------------- the guard
_SYNTHETIC_SOURCES = {
    "pkg.base": textwrap.dedent(
        """
        class Plain(QGraphicsView):
            def __init__(self, parent=None):
                super().__init__(parent)
                configure_canvas_background(self)

        class Mixin:
            pass
        """
    ),
    "pkg.derived": textwrap.dedent(
        """
        class Grandchild(Mixin, Child):
            def __init__(self):
                super().__init__()

        class Child(Plain):
            pass

        class Bare(QtWidgets.QGraphicsView):
            def __init__(self):
                super().__init__()

        class NotAView(Mixin):
            pass
        """
    ),
}


def test_discovery_is_transitive_and_flags_views_without_the_canvas_background():
    """Subclasses of a view are views too (in any module and definition order), and a view
    is covered when its own __init__ or one of its view ancestors sets the background."""
    found = discover_view_classes(_SYNTHETIC_SOURCES)
    assert set(found) == {"Plain", "Child", "Grandchild", "Bare"}
    assert (found["Child"].module, found["Plain"].module) == ("pkg.derived", "pkg.base")
    covered = {name for name, view in found.items() if sets_canvas_background(view, found)}
    assert covered == {"Plain", "Child", "Grandchild"}


def test_every_graphics_view_subclass_sets_the_canvas_background():
    """Every view class in app/ calls configure_canvas_background in its own __init__ or
    inherits a view class that does, and the discovery finds the known canvases."""
    found = app_view_classes()
    assert REQUIRED_VIEWS <= set(found), sorted(found)
    uncovered = sorted(
        name for name, view in found.items() if not sets_canvas_background(view, found)
    )
    assert uncovered == [], f"views without configure_canvas_background: {uncovered}"
