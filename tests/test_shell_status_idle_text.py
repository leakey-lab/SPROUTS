"""The status line's idle text counts images in words that agree ("1 image")."""

from __future__ import annotations

from types import SimpleNamespace

from app.gui.shell_chrome import _idle_text_for


def _text(count: int) -> str:
    window = SimpleNamespace(image_manager=SimpleNamespace(images=dict.fromkeys(range(count))))
    return _idle_text_for(window)()


def test_one_image_is_singular():
    assert _text(1) == "Ready · 1 image"


def test_many_images_have_a_thousands_separator():
    assert _text(3) == "Ready · 3 images"
    assert _text(46178) == "Ready · 46,178 images"


def test_no_images_is_just_ready():
    assert _text(0) == "Ready"
