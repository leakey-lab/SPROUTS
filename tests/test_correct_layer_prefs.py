"""The Correct layers keep their opacity and visibility across app restarts (QSettings;
KNOWN_ISSUES #6). Every test gives the page its own INI file: the user's settings are
never read or written."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings

from app.gui.correct.editor import CorrectEditor
from app.gui.correct.layer import MASK, SKELETON
from app.gui.correct.layer_prefs import LayerPrefs
from app.settings import MemorySettings
from tests.correct_support import new_project, settle
from tests.qt_teardown import delete_widgets

pytestmark = pytest.mark.usefixtures("correct_guards")


@pytest.fixture
def ini(tmp_path):
    return str(tmp_path / "settings.ini")


@pytest.fixture
def pages(app):
    made: list[CorrectEditor] = []

    def make(settings=None, image=None, base=None) -> CorrectEditor:
        editor = CorrectEditor(settings=settings)
        editor.resize(1100, 800)
        editor.show()
        settle(editor)
        if image is not None:
            editor.load_image(image, base)
            settle(editor)
        made.append(editor)
        return editor

    yield make
    delete_widgets(made, prepare=lambda editor: editor.layers[SKELETON].cancel_scheduled_render())


def settings_at(path: str) -> QSettings:
    return QSettings(path, QSettings.Format.IniFormat)


def restart(editor: CorrectEditor) -> None:
    """What quitting the app does to the page: it is hidden with its window."""
    editor.hide()
    settle()


def test_opacity_and_visibility_come_back_after_a_restart(pages, ini, tmp_path):
    project = new_project(tmp_path)
    first = pages(settings_at(ini), project.a, project.folder)
    first.controls.opacity_slider.slider.setValue(35)  # the mask is the edited layer
    first.set_layer_visible(SKELETON, False)
    restart(first)

    second = pages(settings_at(ini), project.b, project.folder)

    mask, skeleton = second.layers[MASK], second.layers[SKELETON]
    assert mask.opacity() == pytest.approx(0.35) and mask.is_visible()
    assert not skeleton.is_visible()
    assert skeleton.opacity() == pytest.approx(skeleton.DEFAULT_OPACITY)
    assert second.controls.opacity_slider.slider.value() == 35
    assert not second.layer_panel.rows[SKELETON].eye_button.isChecked()
    assert second.layer_panel.rows[MASK].eye_button.isChecked()
    assert mask.item.opacity() == pytest.approx(0.35)  # drawn as restored
    assert not skeleton.item.isVisible()


def test_a_restored_hidden_layer_is_shown_again_to_be_edited(pages, ini, tmp_path):
    project = new_project(tmp_path)
    first = pages(settings_at(ini))
    first.set_layer_visible(MASK, False)
    restart(first)
    second = pages(settings_at(ini), project.a, project.folder)
    notices = []
    second.notice.connect(lambda message, kind: notices.append(message))

    second.clear_active_layer()  # the mask, hidden: shown first

    assert second.layers[MASK].is_visible() and "Mask shown" in notices


def test_the_values_are_stored_under_the_correct_group(pages, ini):
    first = pages(settings_at(ini))
    first.set_active_layer(SKELETON)
    first.controls.opacity_slider.slider.setValue(50)
    restart(first)

    stored = settings_at(ini)
    assert float(stored.value("correct/skeleton/opacity")) == pytest.approx(0.5)
    assert stored.value("correct/mask/visible") in (True, "true")


@pytest.mark.parametrize(
    "opacity, visible",
    [("abc", "maybe"), ("7.5", "2"), ("-1", ""), ("nan", None)],
)
def test_unreadable_values_leave_the_defaults(pages, ini, opacity, visible):
    stored = settings_at(ini)
    stored.setValue("correct/mask/opacity", opacity)
    if visible is not None:
        stored.setValue("correct/mask/visible", visible)
    stored.sync()

    page = pages(settings_at(ini))

    mask = page.layers[MASK]
    assert mask.opacity() == pytest.approx(mask.DEFAULT_OPACITY) and mask.is_visible()


def test_a_page_without_settings_on_the_offscreen_platform_keeps_nothing(pages, monkeypatch):
    made = []
    monkeypatch.setattr(
        "app.settings.QSettings",
        lambda *args, **kwargs: made.append(args) or pytest.fail("QSettings made"),
    )

    page = pages()
    page.set_layer_visible(MASK, False)
    restart(page)

    assert made == [] and isinstance(LayerPrefs.for_app(None).settings, MemorySettings)
    again = pages()  # a fresh store: nothing of the first page was kept
    assert again.layers[MASK].is_visible()
