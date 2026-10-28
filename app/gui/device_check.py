"""Name the inference device on the status line soon after start-up (KNOWN_ISSUES #5).

The device chip used to read "Device not detected yet" until the first inference
resolved the runtime. :class:`DeviceCheck` names it in the thread pool once its window
has shown, without loading torch: :func:`resolve_device_logged` asks the NVIDIA driver and
reads the installed torch build (:mod:`app.inference.device_probe`, about 20 ms, +20 MB for
the driver). The chip reads "Checking GPU…" meanwhile, then the device ("GPU · <name> ·
CUDA 12.8" or "CPU", the reason in its tooltip), or "Device unknown" (warning colour) if the
check failed. Once torch has resolved the runtime (the Generate warm-up, or a job), the
full runtime line replaces it. Waves 1-2 loaded torch here (1.3 s, 0.4 GB, GUI stalls, a
slower quit in every session); torch now loads when the user heads for Generate
(:mod:`app.gui.warmup`).

When: :data:`DEVICE_CHECK_DELAY_MS` after the window first shows, once the user's input
(mouse, keys, wheel, drag and drop, anywhere in the app) has paused for
:data:`DEVICE_CHECK_IDLE_MS` and no task (a folder load, a batch job) is running.

Opt-out: ``SPROUTS_DEVICE_CHECK=0`` (also ``false`` / ``no`` / ``off``; ``1`` forces it on)
or the setting ``device_check/at_startup = false``; the environment wins. The chip then
reads as before this check ("Device not detected yet") until the first inference resolves
the device.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget

from app.settings import app_settings
from app.workers import start_pool_job

from . import shell_chrome

logger = logging.getLogger(__name__)

__all__ = [
    "DEVICE_CHECK_DELAY_MS",
    "DEVICE_CHECK_ENV",
    "DEVICE_CHECK_IDLE_MS",
    "DEVICE_CHECK_SETTING",
    "DeviceCheck",
    "device_check_enabled",
    "resolve_device_logged",
]

#: The device is never named sooner than this after the window first shows, so the first
#: paint and layout go first.
DEVICE_CHECK_DELAY_MS = 250
#: ... and only once the user's input has paused this long (the countdown restarts on
#: every mouse move, key, wheel step or drag). The check no longer loads torch (it held
#: the interpreter lock for 40-60 ms at a time), so a short pause is enough.
DEVICE_CHECK_IDLE_MS = 250
#: Environment switch: 0 / false / no / off turns the check off, 1 / true / yes / on on.
DEVICE_CHECK_ENV = "SPROUTS_DEVICE_CHECK"
#: Setting (QSettings key) read when the environment says nothing; on by default.
DEVICE_CHECK_SETTING = "device_check/at_startup"

_OFF = frozenset({"0", "false", "no", "off"})
_ON = frozenset({"1", "true", "yes", "on"})

#: Events that mean the user is interacting (seen by an application-wide filter).
_INPUT_EVENTS = frozenset(
    {
        QEvent.Type.MouseMove,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.Wheel,
        QEvent.Type.KeyPress,
        QEvent.Type.KeyRelease,
        QEvent.Type.InputMethod,
        QEvent.Type.TouchBegin,
        QEvent.Type.TouchUpdate,
        QEvent.Type.TouchEnd,
        QEvent.Type.TabletPress,
        QEvent.Type.TabletMove,
        QEvent.Type.DragEnter,
        QEvent.Type.DragMove,
        QEvent.Type.Drop,
    }
)


def _flag(value: Any) -> bool | None:
    """True / False for an on / off word (or a bool / number), None when unset or unclear."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower() if value is not None else ""
    if text in _OFF:
        return False
    if text in _ON:
        return True
    return None


def device_check_enabled(settings: Any = None, environ: Mapping[str, str] | None = None) -> bool:
    """Whether to resolve the device at start-up (on by default).

    :data:`DEVICE_CHECK_ENV` wins over the setting :data:`DEVICE_CHECK_SETTING`; an unset or
    unreadable value of either means "no opinion". ``settings``: anything with
    ``value(key, default)``, the app's store (:func:`app.settings.app_settings`) when
    ``None``; ``environ``: ``os.environ`` when ``None``.
    """
    env = os.environ if environ is None else environ
    flag = _flag(env.get(DEVICE_CHECK_ENV))
    if flag is not None:
        return flag
    try:
        store = app_settings() if settings is None else settings
        flag = _flag(store.value(DEVICE_CHECK_SETTING, None))
    except Exception:  # noqa: BLE001 - a broken settings backend must not stop the app
        logger.warning("Could not read the %s setting", DEVICE_CHECK_SETTING, exc_info=True)
        flag = None
    return True if flag is None else flag


def resolve_device_logged():
    """Name the inference device without loading torch (the check's thread-pool job).

    Returns ``""`` when the runtime is resolved already (the chip shows it), else the
    :class:`~app.inference.device_probe.DeviceGuess` of the driver and the torch build; if
    even that fails, the reason (logged). Never raises an ``Exception``; the job around it
    reports any other ``BaseException`` the same way (none may leave the pool thread).
    """
    try:
        from app.inference import device_probe, runtime

        if runtime.describe_runtime().device != "unknown":
            return ""
        return device_probe.probe_device()
    except Exception as exc:
        logger.exception("Could not detect the inference device")
        return str(exc) or type(exc).__name__


class _Signals(QObject):
    """Carries the job's result (``""``, why it failed, or a device guess) from the pool
    thread.

    Unparented and kept alive by the job's closure, so a job that ends after the window
    was destroyed emits into nothing instead of a dead object.
    """

    resolved = pyqtSignal(object)


class DeviceCheck(QObject):
    """Names the device once ``window`` has shown and the user paused; tells ``statusline``.

    ``resolve`` (a test seam) is the pool job, :func:`resolve_device_logged` by default: it
    returns ``""`` (resolved), why it failed, or a device guess (``statusline.
    set_device_guess``, when the status line has it).
    ``enabled`` overrides :func:`device_check_enabled` (read from ``settings`` and the
    environment when ``None``). A check that is off changes nothing: no chip state, no
    filter, no job. ``statusline.state() == "working"`` (a running task) defers the job.
    """

    def __init__(
        self,
        window: QWidget,
        statusline: shell_chrome.StatusLine,
        *,
        delay_ms: int = DEVICE_CHECK_DELAY_MS,
        idle_ms: int = DEVICE_CHECK_IDLE_MS,
        resolve=None,
        enabled: bool | None = None,
        settings: Any = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._statusline = statusline
        self._resolve = resolve
        self._shown = False
        self._started = False
        self._watching = False
        self._signals = _Signals()
        self._signals.resolved.connect(self._on_resolved)
        # Input only stamps the time (cheap at a 1000 Hz mouse); the countdown checks the
        # stamp when it runs out and waits for the rest of the quiet period. perf_counter:
        # time.monotonic() ticks in 15.6 ms steps on Windows.
        self._quiet_ms = max(int(delay_ms), int(idle_ms))
        self._last_input = time.perf_counter()
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._on_idle)
        self.enabled = device_check_enabled(settings) if enabled is None else bool(enabled)
        if not self.enabled:
            logger.info(
                "Start-up device check off (%s / %s): the device is resolved by the first job",
                DEVICE_CHECK_ENV,
                DEVICE_CHECK_SETTING,
            )
            return
        statusline.set_device_check(shell_chrome.CHECKING)  # neutral until resolved
        window.installEventFilter(self)

    def started(self) -> bool:
        """True once the pool job was started (at most once)."""
        return self._started

    def cancel(self) -> None:
        """Never start the check (the window closes): no job, no filter, no timer."""
        self._started = True
        self._idle.stop()
        self._watch_input(False)

    def watching_input(self) -> bool:
        """True while the application's input is watched (shown, job not started yet)."""
        return self._watching

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        kind = event.type()
        if kind in _INPUT_EVENTS:
            self._last_input = time.perf_counter()  # the user is busy: the quiet period restarts
        elif kind == QEvent.Type.Show and not self._shown and obj is self._window:
            self._shown = True
            obj.removeEventFilter(self)  # one check per window: stop seeing its events
            self._watch_input(True)
            self._last_input = time.perf_counter()
            self._idle.start(self._quiet_ms)
        return False

    def _watch_input(self, on: bool) -> None:
        """Install / remove the application-wide filter that sees every input event."""
        app = QCoreApplication.instance()
        if app is None or on == self._watching:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._watching = on

    def _on_idle(self) -> None:
        """Start once the input paused for the whole quiet period and no task is running;
        else wait for the rest of the period (or a whole one after a task)."""
        if self._started:
            return
        quiet_for = (time.perf_counter() - self._last_input) * 1000.0
        if quiet_for < self._quiet_ms:
            self._idle.start(max(1, int(self._quiet_ms - quiet_for) + 1))
            return
        state = getattr(self._statusline, "state", None)
        if callable(state) and state() == "working":
            self._idle.start(self._quiet_ms)
            return
        self._start()

    def _start(self) -> None:
        """Run the job in the thread pool (never on the GUI thread)."""
        self._started = True
        self._watch_input(False)
        resolve = self._resolve or resolve_device_logged
        signals = self._signals

        def work() -> None:
            try:
                result = resolve()
            except BaseException as exc:  # noqa: BLE001 - SystemExit too: it must report back
                # Unreported, the chip would read "Checking GPU…" for good.
                logger.exception("Could not detect the inference device")
                result = str(exc) or type(exc).__name__
            try:
                signals.resolved.emit(result)
            except RuntimeError:
                pass  # the window is gone

        start_pool_job(work, QThreadPool.globalInstance())

    def _on_resolved(self, result) -> None:
        """The chip names the device (a guess, or the resolved runtime), or says it is
        unknown (and why)."""
        if result is None or isinstance(result, str):
            self._statusline.set_device_check(shell_chrome.FAILED if result else None, result or "")
            return
        problem = getattr(result, "problem", "")
        show_guess = getattr(self._statusline, "set_device_guess", None)
        if problem:
            self._statusline.set_device_check(shell_chrome.FAILED, problem)
        elif callable(show_guess):
            show_guess(result)
        else:
            self._statusline.set_device_check(None)
