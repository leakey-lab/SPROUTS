"""Recoloured SVG icons: sharp at fractional scale factors, checked-state pixmaps.

Audit UXS-12 / UI-18: icons were rasterised once at 1x, so Qt upscaled them (blurry at
125 / 150 %), and checked stage buttons kept the muted icon next to accent text.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess  # noqa: E402
import sys  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtCore import QSize  # noqa: E402
from PyQt6.QtGui import QColor, QIcon, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.widgets import icons, tokens  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ON, OFF, NORMAL = QIcon.State.On, QIcon.State.Off, QIcon.Mode.Normal


_APP: QApplication | None = None


@pytest.fixture(scope="module")
def app():
    # Keep the QApplication alive for the whole session: the icon loader caches QPixmaps, and a
    # QApplication garbage-collected at module teardown leaves later GUI tests with a dead app.
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _opaque_colours(image: QImage) -> set[str]:
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    return {
        QColor(image.pixel(x, y)).name()
        for y in range(image.height())
        for x in range(image.width())
        if QColor.fromRgba(image.pixel(x, y)).alpha() == 255
    }


@pytest.mark.parametrize("scale,device_px", [(1.25, 19), (1.5, 23), (2.0, 30)])
def test_icon_pixmap_matches_the_requested_scale(app, scale, device_px):
    """15 px at 150 % is 22.5 -> Qt asks for qRound = 23 device px, not an upscaled 15."""
    pm = icons.load_icon("image", tokens.TEXT_MUTED, 15).pixmap(QSize(15, 15), scale)
    assert (pm.width(), pm.height()) == (device_px, device_px)
    assert pm.devicePixelRatio() == pytest.approx(scale)


def test_checked_color_adds_an_on_state_in_that_colour(app):
    icon = icons.load_icon("image", tokens.TEXT_MUTED, 15, checked_color=tokens.ACCENT)
    off = icon.pixmap(QSize(15, 15), 1.0, NORMAL, OFF).toImage()
    on = icon.pixmap(QSize(15, 15), 1.0, NORMAL, ON).toImage()
    assert tokens.ACCENT in _opaque_colours(on)
    assert tokens.ACCENT not in _opaque_colours(off)
    assert tokens.TEXT_MUTED in _opaque_colours(off)


def test_without_checked_color_the_on_state_shows_the_normal_icon(app):
    icon = icons.load_icon("image", tokens.TEXT_MUTED, 15)
    off = icon.pixmap(QSize(15, 15), 1.0, NORMAL, OFF).toImage()
    on = icon.pixmap(QSize(15, 15), 1.0, NORMAL, ON).toImage()
    assert on == off


def test_load_pixmap_keeps_its_logical_size(app):
    pm = icons.load_pixmap("info", tokens.OK, 8)
    size = pm.deviceIndependentSize()
    assert (size.width(), size.height()) == pytest.approx((8, 8), abs=0.34)


def test_unknown_icon_is_null_and_reported(app, caplog):
    name = f"no-such-icon-{uuid.uuid4().hex}"  # unique: the loader caches lookups
    with caplog.at_level("WARNING", logger=icons.__name__):
        assert icons.load_icon(name, tokens.TEXT, 15).isNull()
        assert icons.load_pixmap(name, tokens.TEXT, 15).isNull()
    assert name in caplog.text


_HIDPI_CHILD = r"""
import sys
from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from app.gui.widgets import icons, tokens
pm = icons.load_pixmap("image", tokens.TEXT_MUTED, 15)
icon_pm = icons.load_icon("image", tokens.TEXT_MUTED, 15).pixmap(QSize(15, 15), 1.5)
native = icon_pm.toImage() == pm.toImage()
print(app.devicePixelRatio(), pm.width(), pm.height(), pm.devicePixelRatio(), native)
"""


def test_icons_render_natively_on_a_150_percent_screen():
    """On a 1.5x screen the pixmaps are drawn at 1.5x, not downscaled from 2x."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR="1.5")
    proc = subprocess.run(
        [sys.executable, "-c", _HIDPI_CHILD],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    screen_dpr, width, height, pixmap_dpr, native = proc.stdout.split()[-5:]
    assert float(screen_dpr) == pytest.approx(1.5)
    assert (int(width), int(height), float(pixmap_dpr)) == (23, 23, 1.5)
    assert native == "True"
