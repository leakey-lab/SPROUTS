"""tests/window_support.py: a MainWindow on its own settings, a folder opened until its load
has ended, and a quiet close."""

import time
from types import SimpleNamespace

import pytest
from PyQt6.QtGui import QImage

from app.gui import image_manager
from tests.qt_teardown import delete_widgets
from tests.qt_wait import wait_until
from tests.window_support import (
    WINDOW_SIZE,
    build_window,
    open_folder,
    quiet_window,
    write_photos,
)

pytestmark = pytest.mark.usefixtures("window_guards")

FIRST = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]
SECOND = [f"24EFCAM1_T002_L0{d}_2024.07.18_1116{d}_001_SMM" for d in (31, 32, 33)]


@pytest.fixture
def make_window(app, tmp_path):
    made: list = []

    def make(**kwargs):
        made.append(build_window(tmp_path / f"s{len(made)}.ini", **kwargs))
        return made[-1]

    yield make
    delete_widgets(made, prepare=quiet_window)


@pytest.fixture
def slow_scan(monkeypatch):
    """Every folder scan starts ``delay[0]`` seconds late, as on a busy machine."""
    delay = [0.3]
    real = image_manager.ImageLoaderThread.work

    def work(self):
        time.sleep(delay[0])
        real(self)

    monkeypatch.setattr(image_manager.ImageLoaderThread, "work", work)
    return delay


def _stems(window) -> list[str]:
    return sorted(window.image_manager.images)


def test_build_window_needs_its_own_settings():
    with pytest.raises(TypeError):
        build_window()


def test_build_window_makes_a_shown_window_on_ini_settings(make_window):
    window = make_window()
    assert window.isVisible()
    assert window.height() == WINDOW_SIZE[1]
    assert window.width() >= WINDOW_SIZE[0]  # the window's minimum width may be larger
    assert window._persist_window_state  # explicit settings, never the user's

    hidden = make_window(size=None, show=False)
    assert not hidden.isVisible()


def test_open_folder_waits_for_the_load_to_end(make_window, tmp_path):
    window = make_window()
    open_folder(window, write_photos(tmp_path / "first", FIRST), len(FIRST))

    assert not window._loading_project
    assert _stems(window) == FIRST
    assert len(window._image_leaves()) == len(FIRST)


def test_a_second_folder_of_as_many_images_is_waited_for_too(make_window, tmp_path, slow_scan):
    window = make_window()
    open_folder(window, write_photos(tmp_path / "first", FIRST), len(FIRST))

    # The Library keeps the first folder until the late scan delivers the second.
    open_folder(window, write_photos(tmp_path / "second", SECOND), len(SECOND))

    assert not window._loading_project
    assert _stems(window) == SECOND


def test_open_folder_fails_when_the_load_does_not_end_in_time(make_window, tmp_path, slow_scan):
    window = make_window()
    slow_scan[0] = 1.0
    with pytest.raises(AssertionError, match="did not end with 3 images in 0.2 s"):
        open_folder(window, write_photos(tmp_path / "first", FIRST), len(FIRST), timeout=0.2)
    assert wait_until(lambda: not window._loading_project)  # no late scan in a later test


def test_write_photos_writes_one_flat_photo_per_stem(tmp_path):
    folder = write_photos(tmp_path / "new" / "photos", ["a", "b"], size=(16, 8), suffix=".png")
    assert folder == tmp_path / "new" / "photos"
    assert sorted(path.name for path in folder.iterdir()) == ["a.png", "b.png"]
    image = QImage(str(folder / "a.png"))
    assert (image.width(), image.height()) == (16, 8)
    assert image.pixelColor(3, 3).getRgb()[:3] == (90, 60, 30)


def test_quiet_window_answers_every_editor_and_skips_what_a_window_lacks(make_window):
    window = make_window(show=False)
    quiet_window(window)
    assert all(editor.maybe_save_changes() is True for editor in window._editors().values())

    quiet_window(SimpleNamespace())  # no editors, no Correct page
