"""Toasts keep clear of the Welcome screen's About button (KNOWN_ISSUES #7).

At 1024 x 640 a danger toast (it stays until closed) covered the About button in the
Welcome screen's footer: the toast column reaches it and nothing on that screen was
chrome to keep clear of. The Welcome screen now names its About button to the window's
:class:`~app.gui.toast_clearance.ToastClearance` while it is shown.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication, QStackedWidget, QVBoxLayout, QWidget  # noqa: E402

from app.gui.recent_projects import RecentProjectsStore  # noqa: E402
from app.gui.toast_clearance import ToastClearance  # noqa: E402
from app.gui.welcome_screen import WelcomeWidget  # noqa: E402
from app.gui.widgets.toasts import ToastManager  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

DANGER = "Could not open D:/data/minirhizotron/2024/tube_01: the folder is not readable"


class _Settings:
    def __init__(self) -> None:
        self.data = {"recent_projects": json.dumps([])}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - QSettings API
        self.data[key] = value


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Scene:
    """A 1024 x 640 window: the Welcome screen and a working page in a stack, the toasts
    with their clearance (no other chrome), as the main window builds them."""

    def __init__(self) -> None:
        self.host = QWidget()
        self.host.resize(1024, 640)
        column = QVBoxLayout(self.host)
        column.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.welcome = WelcomeWidget(
            lambda: None,
            lambda _path: None,
            recent_store=RecentProjectsStore(_Settings()),
            probe_async=False,
            on_about=lambda: None,
        )
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(QWidget())
        column.addWidget(self.stack)
        self.toasts = ToastManager(self.host)
        self.clearance = ToastClearance(self.host, self.toasts, lambda: [])
        self.clearance.keep_clear_of(self.welcome.about_button)
        self.host.show()
        settle()

    def about_rect(self) -> QRect:
        about = self.welcome.about_button
        return QRect(about.mapTo(self.host, QPoint(0, 0)), about.size())


def settle() -> None:
    for _ in range(3):
        QApplication.processEvents()


@pytest.fixture
def scene(app):
    made = Scene()
    yield made
    widgets = [made.host]
    del made
    delete_widgets(widgets)


def test_the_about_button_is_in_the_toast_column(scene):
    """The case itself: without a clearance the toast column reaches the button."""
    left = scene.toasts.column_left(0)
    assert scene.about_rect().right() >= left
    assert scene.about_rect().bottom() >= 640 - 16 - 60  # in the bottom toast's band


def test_a_danger_toast_keeps_clear_of_the_about_button(scene):
    scene.toasts.show(DANGER, "danger")
    settle()

    (toast,) = scene.toasts.toasts()
    assert scene.host.rect().contains(toast.geometry())
    assert not toast.geometry().intersects(scene.about_rect())


def test_the_stack_goes_back_down_once_the_welcome_screen_is_left(scene):
    scene.toasts.show(DANGER, "danger")
    settle()
    scene.stack.setCurrentIndex(1)  # a folder was opened: the working shell shows
    settle()

    (toast,) = scene.toasts.toasts()
    assert toast.geometry().bottom() == 640 - 16 - 1  # its corner, as before


def test_the_main_window_keeps_toasts_off_the_about_button(app, tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QMessageBox

    from app.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    made = [MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))]
    window = made[0]
    try:
        window.resize(1024, 640)
        window.show()
        settle()
        assert window.app_stack.currentWidget() is window.welcome
        window.notify(DANGER, "danger")
        settle()

        about = window.welcome.about_button
        about_rect = QRect(about.mapTo(window, QPoint(0, 0)), about.size())
        (toast,) = window.toasts.toasts()
        assert window.rect().contains(toast.geometry())
        assert not toast.geometry().intersects(about_rect)
    finally:
        del window
        delete_widgets(made)


def test_the_stack_lifts_again_when_the_welcome_screen_returns(scene):
    scene.stack.setCurrentIndex(1)
    settle()
    scene.toasts.show(DANGER, "danger")
    settle()
    scene.stack.setCurrentIndex(0)
    settle()

    (toast,) = scene.toasts.toasts()
    assert not toast.geometry().intersects(scene.about_rect())
