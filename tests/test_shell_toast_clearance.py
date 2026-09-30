"""Toasts keep clear of the bottom chrome at every window size (KNOWN_ISSUES #5, H1).

Before: the stack only kept above the statusline and a Correct dock, so on the Library
page the toasts covered the metrics bar (at 1280 x 800 the "Loaded 40 images" toast sat on
the AREA / FOV / STATUS cells), and in an editor they could cover the zoom cluster. The
"Loaded N images" toast is shown while the empty state is still up, so it must also move
when the display page (with its metrics bar) replaces it.

Offscreen; a tmp_path project of small photos; every window has its own INI settings.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QPoint, QRect, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui.panels import Panel  # noqa: E402

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]
LONG = "Mask saved for 24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg"
SIZES = [(1280, 800), (1366, 768), (1024, 640)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    """A loaded project schedules its stale-temp sweep 2 s later (R8) on an untracked timer;
    here it is a no-op, so a window of this test never sweeps inside a later test."""
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def make_window(app, tmp_path):
    from app.gui.main_window import MainWindow

    made = []

    def make(width: int, height: int):
        path = tmp_path / f"settings_{len(made)}.ini"
        window = MainWindow(settings=QSettings(str(path), QSettings.Format.IniFormat))
        made.append(window)
        window.resize(width, height)
        window.show()
        settle()
        return window

    yield make
    for window in made:
        for editor in window._editors().values():
            editor.maybe_save_changes = lambda: True
        window.close()
        window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def settle() -> None:
    for _ in range(3):
        QApplication.processEvents()


def wait(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def project(root) -> str:
    folder = root / "project"
    folder.mkdir(parents=True)
    img = QImage(64, 48, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 60, 30))
    for stem in STEMS:
        assert img.save(str(folder / f"{stem}.jpg"))
    return str(folder)


def load(window, folder: str) -> None:
    window.open_path(folder)
    assert wait(lambda: len(window.library_index.leaves()) == len(STEMS))
    settle()


def rect_in(window, widget) -> QRect:
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def assert_clear(window, *chrome) -> None:
    """Every live toast is inside the window and overlaps none of ``chrome``."""
    toasts = window.toasts.toasts()
    assert toasts
    for toast in toasts:
        box = toast.geometry()
        assert window.rect().contains(box), (box, window.rect())
        for widget in chrome:
            assert widget.isVisible()
            assert not box.intersects(rect_in(window, widget)), (toast.message(), widget)


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_toasts_stay_clear_of_the_metrics_bar_on_the_library_page(
    make_window, tmp_path, width, height
):
    window = make_window(width, height)
    load(window, project(tmp_path))
    item = window.library_index.leaves()[0]
    window.file_list.setCurrentItem(item)
    window.on_image_selected(item)
    window.notify("Loaded 3 images")
    window.notify(LONG)
    settle()

    assert_clear(window, window.metrics_bar, window.statusline)


def test_the_load_toast_moves_off_the_metrics_bar_once_the_display_page_shows(
    make_window, tmp_path
):
    window = make_window(1280, 800)
    assert window.display_page_stack.currentIndex() == 1  # the empty state, no metrics bar

    load(window, project(tmp_path))  # "Loaded 3 images" is shown before the page flips

    assert window.metrics_bar.isVisible()
    assert [t.message() for t in window.toasts.toasts()] == ["Loaded 3 images"]
    assert_clear(window, window.metrics_bar, window.statusline)


@pytest.mark.parametrize(("width", "height"), [(1280, 800), (1024, 640)])
def test_toasts_stay_clear_of_an_editors_dock_and_zoom_cluster(
    make_window, tmp_path, width, height
):
    window = make_window(width, height)
    load(window, project(tmp_path))
    item = window.library_index.leaves()[0]
    window.file_list.setCurrentItem(item)
    window.on_tree_item_clicked(item, 0)
    window.ribbon_buttons["Correct"].click()
    settle()
    assert window.right_panel.currentIndex() == Panel.CORRECT
    editor = window.correct_editor
    window.notify(LONG)
    settle()

    assert_clear(window, editor.dock, editor.zoom_cluster, window.statusline)


def test_toasts_follow_a_window_resize(make_window, tmp_path):
    window = make_window(1366, 768)
    load(window, project(tmp_path))
    window.notify(LONG)
    settle()

    window.resize(1024, 640)
    settle()

    assert_clear(window, window.metrics_bar, window.statusline)
