"""MainWindow in the tests: build one on its own INI settings, open a folder and wait until
the load has ended, close it without prompts.

A fixture keeps its windows in a list and deletes them with ``qt_teardown.delete_widgets``,
which empties the list, so no wrapper outlives the flush::

    made = [build_window(tmp_path / "s.ini")]
    yield made[0]
    delete_widgets(made, prepare=quiet_window)

``MainWindow`` is imported when a window is built, so importing this module loads no app
window code.
"""

from __future__ import annotations

from pathlib import Path

from tests.qt_wait import settle, wait_until

#: The size the window tests use.
WINDOW_SIZE = (1280, 800)


def build_window(settings_path=None, *, settings=None, size=WINDOW_SIZE, show=True, **kwargs):
    """A MainWindow on an INI ``QSettings`` at ``settings_path`` (or on ``settings``), never on
    the user's settings; resized to ``size`` and shown and settled, unless ``size`` is None
    or ``show`` is False. ``kwargs`` go to ``MainWindow``."""
    from PyQt6.QtCore import QSettings

    from app.gui.main_window import MainWindow

    if settings is None:
        if settings_path is None:
            raise TypeError("build_window needs settings_path or settings")
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, **kwargs)
    if size is not None:
        window.resize(*size)
    if show:
        window.show()
        settle()
    return window


def quiet_window(window) -> None:
    """No save prompt and no scheduled skeleton render when ``window`` closes (a
    ``delete_widgets`` prepare step)."""
    for editor in getattr(window, "_editors", dict)().values():
        editor.maybe_save_changes = lambda: True
    correct = getattr(window, "correct_editor", None)
    if correct is not None:
        from app.gui.correct.layer import SKELETON

        correct.layers[SKELETON].cancel_scheduled_render()


def write_photos(folder, stems, size=(32, 24), color=(90, 60, 30), suffix=".jpg"):
    """A flat ``color`` photo ``<stem><suffix>`` of ``size`` for each of ``stems`` in
    ``folder`` (made if missing); returns ``folder``."""
    from PyQt6.QtGui import QColor, QImage

    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor(*color))
    for stem in stems:
        assert image.save(str(path / f"{stem}{suffix}"))
    return folder


def open_folder(window, folder, count: int, timeout: float = 5.0) -> None:
    """Open ``folder`` in ``window`` and wait until that load has ended with ``count`` images
    in the Library (asserted).

    The load ends when ``window._loading_project`` is False again. Waiting only for the
    image count and the tree's rows is not enough: the Library keeps the previous project
    until the scan delivers, so a folder opened after one with as many images passed at once.
    """
    window.open_path(str(folder))
    assert wait_until(
        lambda: (
            not window._loading_project
            and len(window.image_manager.images) == count
            and bool(window._image_leaves())
        ),
        timeout,
    ), f"opening {folder}: the load did not end with {count} images in {timeout} s"
