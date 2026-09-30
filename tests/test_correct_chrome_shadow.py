"""The floating panels' drop shadow is blitted from a cache (KNOWN_ISSUES #6: on the raster
canvas every frame re-rendered and re-blurred the five panels' shadows) and looks like the
QGraphicsDropShadowEffect it replaces."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QWidget  # noqa: E402

from app.gui.widgets import effects  # noqa: E402
from app.gui.widgets.effects import CachedDropShadow, pop_shadow  # noqa: E402
from app.gui.widgets.overlays import FloatingDock, ProgressOverlay, ToolRail  # noqa: E402

_APP = None


@pytest.fixture(scope="module")
def app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture
def host(app):
    widget = QWidget()
    widget.setStyleSheet("background-color: #3a4a5a;")  # a light canvas shows the shadow
    widget.resize(520, 300)
    yield widget
    widget.close()
    widget.deleteLater()


def panel(host, size=(300, 60), pos=(110, 90)) -> QFrame:
    frame = QFrame(host)
    frame.setStyleSheet(
        "QFrame { background-color: #1b1c24; border: 1px solid #373a4c; border-radius: 13px; }"
    )
    frame.setGeometry(*pos, *size)
    return frame


def grab(widget) -> np.ndarray:
    widget.show()
    for _ in range(3):
        QApplication.processEvents()
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), np.uint8)
    return (
        rows.reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
        .reshape(image.height(), image.width(), 4)
        .astype(int)
    )


def stock_pop(widget) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(50)
    effect.setOffset(0, 18)
    effect.setColor(QColor(0, 0, 0, 180))
    widget.setGraphicsEffect(effect)


@pytest.mark.parametrize("size", [(300, 60), (48, 400), (160, 40)])
def test_the_cached_shadow_looks_like_the_stock_one(host, size):
    frame = panel(host, size, pos=(110, 60) if size[1] < 200 else (200, 20))
    stock_pop(frame)
    before = grab(host)
    pop_shadow(frame)
    after = grab(host)

    difference = np.abs(before - after)
    assert difference.max() <= 2
    assert difference.mean() < 0.01
    assert np.abs(before - grab_without_panel(host, frame)).max() > 20  # a shadow was drawn


def grab_without_panel(host, frame) -> np.ndarray:
    frame.hide()
    try:
        return grab(host)
    finally:
        frame.show()


def test_the_shadow_is_made_once_per_size(host, monkeypatch):
    effects._cache.clear()
    made = []
    real = effects._shadow_image
    monkeypatch.setattr(
        effects, "_shadow_image", lambda *args: made.append(args[:2]) or real(*args)
    )
    frame = panel(host, (222, 55))
    pop_shadow(frame)

    for _ in range(3):
        grab(host)
        host.repaint()
    frame.resize(230, 55)
    grab(host)
    grab(host)

    assert made == [(222, 55), (230, 55)]


def test_it_reports_the_bounds_of_the_stock_effect(app):
    stock = QGraphicsDropShadowEffect()
    stock.setBlurRadius(50)
    stock.setOffset(0, 18)
    cached = CachedDropShadow(blur=50, dx=0, dy=18, alpha=180)

    rect = QRectF(0, 0, 300, 60)

    assert cached.boundingRectFor(rect) == stock.boundingRectFor(rect)
    assert (cached.blurRadius(), cached.xOffset(), cached.yOffset()) == (50, 0, 18)
    assert cached.color() == QColor(0, 0, 0, 180)


def test_every_floating_panel_uses_the_cached_shadow(host):
    for widget in (ToolRail(host), FloatingDock(host), ProgressOverlay(host)):
        effect = widget.graphicsEffect() or widget.findChild(QFrame).graphicsEffect()
        assert isinstance(effect, CachedDropShadow)


def test_the_cache_is_bounded(host):
    effects._cache.clear()
    for width in range(effects._CACHE_SIZE + 10):
        effects.shadow_pixmap(40 + width, 30, 1.0, 13.0, 50, 0, 18, 180)

    assert len(effects._cache) == effects._CACHE_SIZE


_AT_SCALE = """
import numpy as np
from PyQt6.QtWidgets import QApplication, QWidget
app = QApplication([])
import tests.test_correct_chrome_shadow as t
from app.gui.widgets.effects import pop_shadow
worst = 0
for size, pos in (((300, 60), (110, 60)), ((48, 400), (200, 20))):
    host = QWidget()
    host.setStyleSheet("background-color: #3a4a5a;")
    host.resize(520, 300)
    frame = t.panel(host, size, pos=pos)
    t.stock_pop(frame)
    before = t.grab(host)
    pop_shadow(frame)
    after = t.grab(host)
    worst = max(worst, int(np.abs(before - after).max()))
    host.close()
print(app.devicePixelRatio(), worst)
"""


@pytest.mark.parametrize("scale", ["1.5", "2"])
def test_the_cached_shadow_looks_like_the_stock_one_on_high_dpi_screens(scale):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale)
    run = subprocess.run(
        [sys.executable, "-c", _AT_SCALE], cwd=root, env=env, capture_output=True, text=True,
        timeout=120,
    )  # fmt: skip

    assert run.returncode == 0, run.stderr
    dpr, worst = run.stdout.split()
    assert float(dpr) == float(scale) and int(worst) <= 2
