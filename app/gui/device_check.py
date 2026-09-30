"""Name the inference device on the status line soon after start-up (KNOWN_ISSUES #5).

The device chip used to read "Device not detected yet" until the first inference
resolved the runtime. :class:`DeviceCheck` resolves it in the thread pool a moment after
its window first shows: torch loads there (about 1.3 s, 0.4 GB), never on the GUI thread
and never before the first paint. The chip reads "Checking GPU…" meanwhile, then the
device, or "Device unknown" (warning colour, the reason in its tooltip) if resolving
failed. Only the public :func:`app.inference.runtime.describe_runtime` is called.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, QObject, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget

from . import shell_chrome

logger = logging.getLogger(__name__)

__all__ = ["DEVICE_CHECK_DELAY_MS", "DeviceCheck", "resolve_device_logged"]

#: The device is resolved this long after the window first shows, so the first paint and
#: layout never compete with loading torch.
DEVICE_CHECK_DELAY_MS = 250


def resolve_device_logged() -> str:
    """Resolve and cache the inference device (a thread-pool job: torch loads here).

    Returns ``""`` once the runtime knows its device, else the reason it could not (logged).
    Never raises: an exception must never leave the pool thread.
    """
    try:
        from app.inference import runtime

        runtime.describe_runtime(resolve=True)
    except Exception as exc:
        logger.exception("Could not detect the inference device")
        return str(exc) or type(exc).__name__
    return ""


class _Signals(QObject):
    """Carries 'device resolved' (``""``, or why it failed) from the pool thread.

    Unparented and kept alive by the job's closure, so a job that ends after the window
    was destroyed emits into nothing instead of a dead object.
    """

    resolved = pyqtSignal(str)


class DeviceCheck(QObject):
    """Resolves the device once ``window`` first shows and tells ``statusline``.

    ``resolve`` (a test seam) is the pool job, :func:`resolve_device_logged` by default.
    """

    def __init__(
        self,
        window: QWidget,
        statusline: shell_chrome.StatusLine,
        *,
        delay_ms: int = DEVICE_CHECK_DELAY_MS,
        resolve=None,
    ) -> None:
        super().__init__(window)
        self._statusline = statusline
        self._delay_ms = int(delay_ms)
        self._resolve = resolve
        self._started = False
        self._signals = _Signals()
        self._signals.resolved.connect(self._on_resolved)
        statusline.set_device_check(shell_chrome.CHECKING)  # neutral until resolved
        window.installEventFilter(self)

    def started(self) -> bool:
        """True once the pool job was started (at most once)."""
        return self._started

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Show and not self._started:
            self._started = True
            obj.removeEventFilter(self)  # one check per window: stop seeing its events
            QTimer.singleShot(self._delay_ms, self._start)
        return False

    def _start(self) -> None:
        """Run the job in the thread pool (never on the GUI thread)."""
        resolve = self._resolve or resolve_device_logged
        signals = self._signals

        def work() -> None:
            error = resolve()
            try:
                signals.resolved.emit(error)
            except RuntimeError:
                pass  # the window is gone

        QThreadPool.globalInstance().start(work)

    def _on_resolved(self, error: str) -> None:
        """The chip names the resolved device, or says it is unknown (and why)."""
        self._statusline.set_device_check(shell_chrome.FAILED if error else None, error)
