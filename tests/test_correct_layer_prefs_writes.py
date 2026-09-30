"""LayerPrefs writes only what changed (X-RUNTIME F6).

The Correct page stores the layers' opacity and visibility every time it is hidden (each
stage switch, and quitting). It used to write all four values and flush the settings on
every hide, 22-58 ms per switch with an INI file; now a value is written only when it
differs from what the settings hold (as read at restore, or last written), and the
settings are flushed only after a write. Every test uses its own INI file.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.correct.editor import CorrectEditor  # noqa: E402
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.correct.layer_prefs import LayerPrefs  # noqa: E402
from tests.test_correct_editor import install_guards, qapp, settle  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


class Layer:
    """The part of an edit layer LayerPrefs reads and sets."""

    def __init__(self, layer_id: str, opacity: float = 0.6, visible: bool = True) -> None:
        self.layer_id = layer_id
        self._opacity, self._visible = opacity, visible

    def opacity(self) -> float:
        return self._opacity

    def set_opacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))

    def is_visible(self) -> bool:
        return self._visible

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)


class Recording:
    """A QSettings stand-in over a real INI QSettings that records writes and flushes."""

    def __init__(self, path: str) -> None:
        self.inner = QSettings(path, QSettings.Format.IniFormat)
        self.writes: list[tuple[str, object]] = []
        self.syncs = 0

    def value(self, key, *args):
        return self.inner.value(key, *args)

    def setValue(self, key, value) -> None:  # noqa: N802 - QSettings' name
        self.writes.append((key, value))
        self.inner.setValue(key, value)

    def sync(self) -> None:
        self.syncs += 1
        self.inner.sync()

    def status(self):
        return self.inner.status()


@pytest.fixture
def ini(tmp_path):
    return str(tmp_path / "settings.ini")


def stored_prefs(ini: str, **values) -> None:
    settings = QSettings(ini, QSettings.Format.IniFormat)
    for key, value in values.items():
        settings.setValue(key.replace("__", "/"), value)
    settings.sync()


# ---------------------------------------------------------------------------- LayerPrefs
def test_a_store_with_nothing_changed_writes_and_flushes_nothing(ini):
    stored_prefs(
        ini,
        correct__mask__opacity=0.35,
        correct__mask__visible=True,
        correct__skeleton__opacity=0.85,
        correct__skeleton__visible=False,
    )
    layers = [Layer(MASK), Layer(SKELETON, 0.85)]
    settings = Recording(ini)
    prefs = LayerPrefs(settings)
    prefs.restore(layers)

    prefs.store(layers)

    assert settings.writes == [] and settings.syncs == 0


def test_only_the_value_that_changed_is_written_then_flushed_once(ini):
    stored_prefs(ini, correct__mask__opacity=0.35, correct__mask__visible=True)
    layers = [Layer(MASK), Layer(SKELETON, 0.85)]
    settings = Recording(ini)
    prefs = LayerPrefs(settings)
    prefs.restore(layers)
    prefs.store(layers)  # the skeleton had nothing stored yet: its two values are written
    settings.writes.clear()
    settings.syncs = 0

    layers[0].set_opacity(0.5)
    prefs.store(layers)

    assert settings.writes == [("correct/mask/opacity", 0.5)] and settings.syncs == 1
    settings.writes.clear()
    prefs.store(layers)  # written once: the next store has nothing to write
    assert settings.writes == [] and settings.syncs == 1
    assert float(QSettings(ini, QSettings.Format.IniFormat).value("correct/mask/opacity")) == 0.5


def test_the_first_store_writes_every_value_nothing_was_stored_for(ini):
    layers = [Layer(MASK), Layer(SKELETON, 0.85, visible=False)]
    settings = Recording(ini)
    prefs = LayerPrefs(settings)
    prefs.restore(layers)

    prefs.store(layers)

    assert sorted(key for key, _value in settings.writes) == [
        "correct/mask/opacity",
        "correct/mask/visible",
        "correct/skeleton/opacity",
        "correct/skeleton/visible",
    ]
    assert settings.syncs == 1


def test_an_unreadable_stored_value_is_written_again(ini):
    stored_prefs(ini, correct__mask__opacity="abc", correct__mask__visible="maybe")
    layers = [Layer(MASK)]
    settings = Recording(ini)
    prefs = LayerPrefs(settings)
    prefs.restore(layers)

    prefs.store(layers)

    assert settings.writes == [("correct/mask/opacity", 0.6), ("correct/mask/visible", True)]


def test_without_a_restore_every_value_is_written_as_before(ini):
    settings = Recording(ini)

    LayerPrefs(settings).store([Layer(MASK)])

    assert len(settings.writes) == 2 and settings.syncs == 1


# ------------------------------------------------------------------------------ the page
def test_hiding_the_page_again_without_changes_writes_nothing(app, ini):
    editor = CorrectEditor(settings=QSettings(ini, QSettings.Format.IniFormat))
    try:
        editor.resize(900, 700)
        editor.show()
        settle(editor)
        editor.hide()  # the first hide stores the defaults (nothing was stored yet)
        settle()
        recording = Recording(ini)
        editor._prefs.settings, recording.inner = recording, editor._prefs.settings

        for _switch in range(3):  # stage switches back and forth
            editor.show()
            settle(editor)
            editor.hide()
            settle()
        assert recording.writes == [] and recording.syncs == 0

        editor.show()
        settle(editor)
        editor.set_layer_visible(SKELETON, False)
        editor.hide()
        settle()
        assert recording.writes == [("correct/skeleton/visible", False)]
        assert recording.syncs == 1
    finally:
        editor.layers[SKELETON].cancel_scheduled_render()
        editor.close()
        editor.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
