"""The start-up device check can be turned off and waits for the user to pause (R-SHELL-B).

Resolving the device loads torch on a pool thread: +0.4 GB and a few 40-60 ms GUI stalls
while torch initialises (it holds the GIL). They used to land 0.7-1.6 s after the window
showed, i.e. on the first clicks. Now the check starts only once the input (mouse, keys,
wheel, drag and drop) has paused for ``idle_ms`` and no task runs, and
``SPROUTS_DEVICE_CHECK=0`` or the setting ``device_check/at_startup = false`` turns it off
(the chip is left alone, as before the check, and torch is not loaded at start-up).

The resolver is a stub: torch is never loaded here.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.gui import device_check  # noqa: E402
from app.gui.device_check import DeviceCheck, device_check_enabled  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_deleted_widgets():
    yield
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class Line:
    """Stand-in status line: records the chip states, reports a running task."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.busy = False

    def set_device_check(self, state, detail: str = "") -> None:
        self.calls.append((state, detail))

    def state(self) -> str:
        return "working" if self.busy else "idle"


class Settings:
    def __init__(self, **values) -> None:
        self.values = values

    def value(self, key, default=None):
        return self.values.get(key, default)


class Resolver:
    """The pool job: records when (perf_counter) it ran."""

    def __init__(self) -> None:
        self.times: list[float] = []

    def __call__(self) -> str:
        self.times.append(time.perf_counter())
        return ""


@pytest.fixture
def window(app):
    made = []

    def make():
        widget = QWidget()
        widget.resize(200, 120)
        made.append(widget)
        return widget

    yield make
    for widget in made:
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            pass  # a test deleted it
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert device_check.QThreadPool.globalInstance().waitForDone(5000)


def pump(ms: int) -> None:
    QTest.qWait(ms)


def wait(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def move(target: QWidget, i: int = 0) -> None:
    point = QPointF(10 + i % 50, 10)
    QApplication.sendEvent(
        target,
        QMouseEvent(
            QEvent.Type.MouseMove,
            point,
            point,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


# ------------------------------------------------------------------ the opt-out
@pytest.mark.parametrize("value", ["0", "false", "No", " off "])
def test_the_environment_turns_the_check_off(value):
    assert device_check_enabled(Settings(), {"SPROUTS_DEVICE_CHECK": value}) is False


@pytest.mark.parametrize("env", [{}, {"SPROUTS_DEVICE_CHECK": "1"}, {"SPROUTS_DEVICE_CHECK": ""}])
def test_the_check_is_on_by_default(env):
    assert device_check_enabled(Settings(), env) is True


@pytest.mark.parametrize("stored", [False, "false", "0", 0])
def test_the_setting_turns_the_check_off(stored):
    settings = Settings(**{"device_check/at_startup": stored})
    assert device_check_enabled(settings, {}) is False


def test_the_environment_wins_over_the_setting():
    off = Settings(**{"device_check/at_startup": "false"})
    on = Settings(**{"device_check/at_startup": True})
    assert device_check_enabled(off, {"SPROUTS_DEVICE_CHECK": "1"}) is True
    assert device_check_enabled(on, {"SPROUTS_DEVICE_CHECK": "0"}) is False


def test_an_unreadable_setting_keeps_the_check_on():
    class Broken:
        def value(self, key, default=None):
            raise OSError("registry unavailable")

    assert device_check_enabled(Settings(**{"device_check/at_startup": "maybe"}), {}) is True
    assert device_check_enabled(Broken(), {}) is True


def test_a_check_that_is_off_never_runs_nor_touches_the_chip(app, window):
    widget, line, resolve = window(), Line(), Resolver()
    check = DeviceCheck(widget, line, delay_ms=10, idle_ms=30, resolve=resolve, enabled=False)
    widget.show()
    pump(250)
    assert resolve.times == []
    assert line.calls == []  # the chip is left alone (no "Checking GPU…")
    assert not check.started() and not check.watching_input()


def test_the_window_passes_its_settings_opt_out(app, window, monkeypatch):
    monkeypatch.delenv("SPROUTS_DEVICE_CHECK", raising=False)
    widget, line, resolve = window(), Line(), Resolver()
    settings = Settings(**{"device_check/at_startup": "false"})
    DeviceCheck(widget, line, delay_ms=10, idle_ms=30, resolve=resolve, settings=settings)
    widget.show()
    pump(250)
    assert resolve.times == [] and line.calls == []


# ---------------------------------------------------------------- the schedule
def test_without_input_the_check_starts_once_the_idle_period_passed(app, window):
    widget, line, resolve = window(), Line(), Resolver()
    check = DeviceCheck(widget, line, delay_ms=10, idle_ms=200, resolve=resolve, enabled=True)
    assert line.calls == [("checking", "")]
    pump(300)
    assert resolve.times == []  # never before the window shows
    shown = time.perf_counter()
    widget.show()
    assert check.watching_input()
    assert wait(lambda: resolve.times)
    assert resolve.times[0] - shown >= 0.18
    assert wait(lambda: line.calls[-1] == (None, ""))
    assert not check.watching_input()  # the application filter is gone


def test_the_check_waits_while_the_user_keeps_moving_the_mouse(app, window):
    widget, line, resolve = window(), Line(), Resolver()
    DeviceCheck(widget, line, delay_ms=10, idle_ms=250, resolve=resolve, enabled=True)
    widget.show()
    other = QWidget()  # input anywhere in the app counts, not only on the watched window
    try:
        for i in range(16):  # 0.8 s of mouse movement, every 50 ms
            move(other if i % 2 else widget, i)
            last_input = time.perf_counter()
            pump(50)
            assert resolve.times == [], f"started during the input (step {i})"
        assert wait(lambda: resolve.times)
        assert resolve.times[0] - last_input >= 0.24  # the whole quiet period (250 ms)
    finally:
        other.deleteLater()


@pytest.mark.parametrize("kind", ["key", "click", "wheel"])
def test_keys_clicks_and_wheel_count_as_input(app, window, kind):
    widget, line, resolve = window(), Line(), Resolver()
    DeviceCheck(widget, line, delay_ms=10, idle_ms=250, resolve=resolve, enabled=True)
    widget.show()
    point = QPointF(5, 5)
    for _ in range(8):
        if kind == "key":
            event = QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_PageDown, Qt.KeyboardModifier.NoModifier
            )
        elif kind == "click":
            event = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                point,
                point,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        else:
            event = QWheelEvent(
                point,
                point,
                QPointF(0, 0).toPoint(),
                QPointF(0, 120).toPoint(),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
        QApplication.sendEvent(widget, event)
        pump(100)
        assert resolve.times == []
    assert wait(lambda: resolve.times)


def test_a_running_task_defers_the_check(app, window):
    widget, line, resolve = window(), Line(), Resolver()
    line.busy = True  # e.g. the first folder is loading
    DeviceCheck(widget, line, delay_ms=10, idle_ms=100, resolve=resolve, enabled=True)
    widget.show()
    pump(450)
    assert resolve.times == []
    line.busy = False
    assert wait(lambda: resolve.times)


def test_input_after_the_start_is_not_watched_and_starts_nothing_twice(app, window):
    widget, line, resolve = window(), Line(), Resolver()
    check = DeviceCheck(widget, line, delay_ms=10, idle_ms=50, resolve=resolve, enabled=True)
    widget.show()
    assert wait(lambda: resolve.times)
    assert not check.watching_input()
    for i in range(5):
        move(widget, i)
    widget.hide()
    widget.show()  # a second show does not check again
    pump(200)
    assert len(resolve.times) == 1 and check.started()


def test_a_window_closed_before_the_check_leaves_no_job_and_no_filter(app, window):
    widget, line, resolve = window(), Line(), Resolver()
    DeviceCheck(widget, line, delay_ms=10, idle_ms=150, resolve=resolve, enabled=True)
    widget.show()
    pump(30)
    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    other = QWidget()
    try:
        move(other)  # the application filter of the deleted check must not be called
        pump(300)
    finally:
        other.deleteLater()
    assert resolve.times == []
