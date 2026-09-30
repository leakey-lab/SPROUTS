"""SVG icon loader for the SPROUTS icon set.

Icons live in ``resources/icons/sprouts/<name>.svg`` and are authored with
``stroke="currentColor"``. Qt's :class:`QIcon` does not honour ``currentColor``,
so the loaders substitute the requested colour into the SVG text and render it
with :class:`QSvgRenderer`.

Rendering is device-pixel-ratio aware: a pixmap is rasterised at the exact device
size Qt asks for (``px`` logical pixels x the screen scale, rounded like Qt's
``qRound``) and tagged with that ratio, so icons stay crisp at 125 / 150 % Windows
scaling instead of being upscaled from a 1x bitmap. Rasters are cached per
(name, colour, px, ratio); the recoloured SVG source per (name, colour).

Theme images: the QSS (``resources/themes/dark_theme.qss``) names its images
``url(sprouts-theme:<file>)``; importing this module registers that search path
(:data:`THEME_SEARCH_PREFIX` -> ``resources/icons``), so they resolve against the
project, never the working directory. Those files carry their token colours literally
(QSS images get no ``currentColor`` substitution).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from PyQt6.QtCore import QByteArray, QDir, QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from resources.resource_utils import resource_path

from . import tokens

logger = logging.getLogger(__name__)

_ICON_DIR = resource_path("resources", "icons", "sprouts")

#: ``url(sprouts-theme:<file>)`` in the QSS finds ``resources/icons/<file>``.
THEME_SEARCH_PREFIX = "sprouts-theme"
_THEME_IMAGE_DIR = resource_path("resources", "icons")


def register_theme_search_path() -> None:
    """Let the stylesheet's ``url(sprouts-theme:<file>)`` images resolve (idempotent).

    Must run before the first styled widget is polished: Qt resolves QSS images once per
    rule and caches a miss. Importing this module does it.
    """
    known = {os.path.normcase(os.path.normpath(p)) for p in QDir.searchPaths(THEME_SEARCH_PREFIX)}
    if os.path.normcase(_THEME_IMAGE_DIR) not in known:
        QDir.addSearchPath(THEME_SEARCH_PREFIX, _THEME_IMAGE_DIR)


register_theme_search_path()

# Ratios every icon is rendered at besides the attached screens' own: 1x for plain
# screens, 2x as a sharp downscale source for a screen that appears later (a monitor
# plugged in at another scale), since Qt prefers downscaling a larger pixmap.
_BASE_SCALES = (1.0, 2.0)


def icon_path(name: str) -> str:
    return os.path.join(_ICON_DIR, f"{name}.svg")


def has_icon(name: str) -> bool:
    return os.path.exists(icon_path(name))


@lru_cache(maxsize=256)
def _svg_text(name: str) -> str | None:
    """The SVG file of ``name`` (read once; file I/O dominates a cold render), or ``None``."""
    path = icon_path(name)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Unknown icon %r: no %s", name, path)
        return None


@lru_cache(maxsize=256)
def _svg_source(name: str, color: str) -> QByteArray | None:
    """The SVG of ``name`` with ``currentColor`` replaced by ``color``; ``None`` if missing."""
    text = _svg_text(name)
    if text is None:
        return None
    return QByteArray(text.replace("currentColor", color).encode("utf-8"))


def _device_px(px: int, dpr: float) -> int:
    """Device pixels Qt requests for ``px`` logical pixels at ``dpr`` (qRound: half up)."""
    return int(px * dpr + 0.5)


@lru_cache(maxsize=1024)
def _rasterize(name: str, color: str, px: int, dpr: float) -> QPixmap:
    """``name`` in ``color``, ``px`` logical pixels, rendered for a ``dpr`` screen."""
    source = _svg_source(name, color)
    side = _device_px(px, dpr)
    if source is None or side <= 0:
        return QPixmap()
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    QSvgRenderer(source).render(painter, QRectF(0, 0, side, side))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _render(name: str, color: str, px: int) -> QPixmap:
    """1x pixmap of ``name``: the lookup every loader goes through first.

    Kept with this exact signature: tools wrap it to detect unknown icon names.
    """
    return _rasterize(name, color, px, 1.0)


def _screen_scales() -> set[float]:
    """Device pixel ratios of the attached screens (none before a QGuiApplication)."""
    if QGuiApplication.instance() is None:
        return set()
    return {screen.devicePixelRatio() for screen in QGuiApplication.screens()}


def load_pixmap(name: str, color: str = tokens.TEXT_MUTED, px: int = 18) -> QPixmap:
    """Recoloured pixmap of ``name``: ``px`` logical pixels, sharp on the densest screen."""
    px = int(px)
    base = _render(name, color, px)
    scale = max(_screen_scales(), default=1.0)
    if base.isNull() or scale == 1.0:
        return base
    return _rasterize(name, color, px, scale)


def load_icon(
    name: str,
    color: str = tokens.TEXT_MUTED,
    px: int = 18,
    *,
    checked_color: str | None = None,
) -> QIcon:
    """Return a recoloured :class:`QIcon` for ``name``, sharp at every screen scale.

    ``checked_color`` adds ``QIcon.State.On`` pixmaps in that colour, so a checkable
    button shows e.g. the accent icon while checked without swapping icons on toggle.
    """
    px = int(px)
    if _render(name, color, px).isNull():
        return QIcon()
    icon = QIcon()
    for scale in sorted(set(_BASE_SCALES) | _screen_scales()):
        icon.addPixmap(_rasterize(name, color, px, scale), QIcon.Mode.Normal, QIcon.State.Off)
        if checked_color is not None:
            icon.addPixmap(
                _rasterize(name, checked_color, px, scale), QIcon.Mode.Normal, QIcon.State.On
            )
    return icon
