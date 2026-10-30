"""A checked checkbox shows a check mark, not just a colour (walkthrough W9).

The theme drew a checked indicator as a solid accent square: the state was carried by the
fill alone, and in the Trace GT class panel a checked "reviewed" box looked like one more
class colour swatch. The checked indicator now carries a check mark in the accent ink
(WCAG 1.4.11: at least 3:1 against the fill), from an SVG shipped in resources/icons that
the QSS reaches through the ``sprouts-theme:`` search path (never the working directory).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PyQt6.QtCore import QDir
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton

import app.design.tokens as tokens
from app.gui.widgets import icons  # registers the theme search path
from tests.qt_teardown import flush_deleted

ROOT = Path(__file__).resolve().parents[1]
QSS_PATH = ROOT / "resources" / "themes" / "dark_theme.qss"
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_URL = re.compile(r"url\(\s*([^)\s]+)\s*\)")


def _qss() -> str:
    return QSS_PATH.read_text(encoding="utf-8")


def _image_for(selector: str) -> str:
    """The ``image: url(...)`` of the rule whose selector list contains ``selector``."""
    for selectors, body in _RULE.findall(_COMMENT.sub("", _qss())):
        if selector in [s.strip() for s in selectors.split(",")]:
            match = re.search(r"image\s*:\s*url\(\s*([^)\s]+)\s*\)", body)
            if match:
                return match.group(1)
    raise AssertionError(f"no image for {selector}")


def _theme_file(url: str) -> Path:
    prefix, _, name = url.partition(":")
    assert prefix == icons.THEME_SEARCH_PREFIX, url
    return ROOT / "resources" / "icons" / name


def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    color = QColor(hex_color)
    r, g, b = (channel(v) for v in (color.red(), color.green(), color.blue()))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_the_checked_indicator_has_a_mark_image_the_app_can_find():
    for selector in ("QCheckBox::indicator:checked", "QCheckBox::indicator:checked:disabled"):
        url = _image_for(selector)
        path = _theme_file(url)
        assert path.is_file(), path
        # The prefix resolves through the search path (not the working directory).
        assert str(ROOT / "resources" / "icons") in [
            os.path.normpath(p) for p in QDir.searchPaths(icons.THEME_SEARCH_PREFIX)
        ]


def test_the_marks_use_token_inks_with_enough_contrast():
    enabled = _theme_file(_image_for("QCheckBox::indicator:checked")).read_text("utf-8")
    disabled = _theme_file(_image_for("QCheckBox::indicator:checked:disabled")).read_text("utf-8")
    assert tokens.ACCENT_INK in enabled.lower()
    assert tokens.ACCENT_DISABLED_INK in disabled.lower()
    assert "currentcolor" not in enabled.lower()  # QSS images get no colour substitution
    assert _contrast(tokens.ACCENT_INK, tokens.ACCENT) >= 3.0  # WCAG 1.4.11 non-text


def _indicator_pixels(box: QCheckBox) -> list[QColor]:
    """The opaque pixels of the box's indicator, as drawn."""
    option = QStyleOptionButton()
    box.initStyleOption(option)
    rect = box.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, box)
    image = box.grab().toImage()
    scale = image.devicePixelRatio()
    pixels = []
    for y in range(int(rect.top() * scale), int((rect.bottom() + 1) * scale)):
        for x in range(int(rect.left() * scale), int((rect.right() + 1) * scale)):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() == 255:
                pixels.append(pixel)
    return pixels


def _near(pixel: QColor, hex_color: str, tolerance: int = 12) -> bool:
    ref = QColor(hex_color)
    return all(
        abs(a - b) <= tolerance
        for a, b in zip(
            (pixel.red(), pixel.green(), pixel.blue()),
            (ref.red(), ref.green(), ref.blue()),
            strict=True,
        )
    )


def test_a_checked_box_draws_a_mark_on_the_accent_fill(app):
    box = QCheckBox("Only fully checked images")
    box.setStyleSheet(_qss())  # the app theme, on this widget only
    box.resize(box.sizeHint())
    try:
        box.setChecked(True)
        pixels = _indicator_pixels(box)
        fill = sum(_near(p, tokens.ACCENT) for p in pixels)
        ink = sum(_near(p, tokens.ACCENT_INK) for p in pixels)
        assert fill >= 40, "the checked box keeps its accent fill"
        assert ink >= 6, f"a checked box is only a colour (no mark): {ink} ink pixels"
    finally:
        box.deleteLater()
        flush_deleted()
