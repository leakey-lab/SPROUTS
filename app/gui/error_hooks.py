"""Last-resort handling for exceptions that nothing else caught.

PyQt6 calls ``qFatal`` when an exception escapes a slot or a reimplemented
``QThread.run`` while ``sys.excepthook`` is Python's default: the process
aborts, no traceback reaches the log and every unsaved edit is lost.
``main()`` therefore installs:

* ``install_exception_hooks(app)`` - ``sys.excepthook`` and
  ``threading.excepthook`` log the full traceback at CRITICAL and hand a
  one-line summary to a ``CrashDialogPresenter``, which shows at most one
  non-modal critical dialog at a time on the GUI thread (queued from any
  thread; later errors are counted into the open dialog; never ``exec()``-ed,
  so no nested event loop);
* ``install_qt_message_handler()`` - Qt's own warnings go to the ``qt`` logger
  instead of a console nobody sees;
* ``enable_fault_log(path)`` - a native crash dumps every thread's Python
  stack to ``path``.

``ExceptionHooks`` is Qt-free; the presenter is its thin Qt adapter.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import IO

from PyQt6.QtCore import (
    QCoreApplication,
    QObject,
    Qt,
    QtMsgType,
    pyqtSignal,
    pyqtSlot,
    qInstallMessageHandler,
)
from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox

from app.logging_config import current_log_file
from app.workers import describe_error

logger = logging.getLogger(__name__)

#: ``notify(summary, details)``: a one-line summary and the formatted traceback.
Notify = Callable[[str, str], None]

_installed: ExceptionHooks | None = None
_qt_handler: Callable[..., None] | None = None  # keeps the installed handler alive
_fault_stream: IO[str] | None = None  # must stay open while faulthandler uses it

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


class ExceptionHooks:
    """Logs unhandled exceptions and forwards a summary to ``notify``.

    ``KeyboardInterrupt`` goes to ``sys.__excepthook__``. An exception raised
    while one is being reported (on the same thread) is printed by
    ``sys.__excepthook__`` instead of being handled recursively.
    """

    def __init__(self, notify: Notify | None = None) -> None:
        self._notify = notify
        self._reporting = threading.local()
        self._previous: tuple[Callable[..., object], Callable[..., object]] | None = None

    @property
    def installed(self) -> bool:
        """True while these hooks are installed."""
        return self._previous is not None

    def install(self) -> None:
        """Replace ``sys.excepthook`` and ``threading.excepthook`` (idempotent)."""
        if self._previous is not None:
            return
        self._previous = (sys.excepthook, threading.excepthook)
        sys.excepthook = self._sys_hook
        threading.excepthook = self._thread_hook

    def uninstall(self) -> None:
        """Restore the hooks that were active before ``install()``."""
        if self._previous is None:
            return
        previous_sys, previous_thread = self._previous
        self._previous = None
        if sys.excepthook == self._sys_hook:
            sys.excepthook = previous_sys
        if threading.excepthook == self._thread_hook:
            threading.excepthook = previous_thread

    def handle(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
        *,
        origin: str | None = None,
    ) -> None:
        """Log one unhandled exception and notify; never raises."""
        if issubclass(exc_type, KeyboardInterrupt) or getattr(self._reporting, "active", False):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        self._reporting.active = True
        try:
            where = origin or f"thread {threading.current_thread().name}"
            logger.critical(
                "Unhandled exception in %s", where, exc_info=(exc_type, exc_value, exc_tb)
            )
            if self._notify is not None:
                summary = describe_error(exc_value) if exc_value is not None else exc_type.__name__
                details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                try:
                    self._notify(summary, details)
                except Exception:
                    logger.exception("Could not report the unhandled exception")
        finally:
            self._reporting.active = False

    def _sys_hook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.handle(exc_type, exc_value, exc_tb)

    def _thread_hook(self, args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, SystemExit):
            return  # threading's default hook ignores SystemExit too
        name = args.thread.name if args.thread is not None else "?"
        self.handle(args.exc_type, args.exc_value, args.exc_traceback, origin=f"thread {name}")


class CrashDialogPresenter(QObject):
    """Shows unhandled-exception summaries in one non-modal critical dialog.

    ``report()`` is thread-safe: the request is queued to this object's (GUI)
    thread. While the dialog is open, further reports only raise its "N more
    errors" count. The dialog's "don't show again" box mutes the presenter for
    the rest of the session (errors are still logged).
    """

    _requested = pyqtSignal(str, str)

    def __init__(
        self, parent: QObject | None = None, *, log_file: Path | str | None = None
    ) -> None:
        super().__init__(parent)
        self._log_file = log_file
        self._dialog: QMessageBox | None = None
        self._extra = 0
        self._muted = False
        self._requested.connect(self._present, Qt.ConnectionType.QueuedConnection)

    @property
    def dialog(self) -> QMessageBox | None:
        """The open dialog, if any."""
        return self._dialog

    @property
    def muted(self) -> bool:
        """True after the user chose not to see further error dialogs."""
        return self._muted

    def report(self, summary: str, details: str) -> None:
        """Queue an error for display (any thread; never blocks, never raises)."""
        try:
            self._requested.emit(summary, details)
        except RuntimeError:  # the presenter is gone: the application is shutting down
            pass

    @pyqtSlot(str, str)
    def _present(self, summary: str, details: str) -> None:
        if self._muted:
            return
        try:
            if self._dialog is not None:
                self._extra += 1
                self._dialog.setInformativeText(self._informative_text())
                return
            self._extra = 0
            self._dialog = self._build_dialog(summary, details)
            self._dialog.show()
        except Exception:
            self._muted = True  # a broken dialog must not turn every error into another
            logger.exception("Could not show the error dialog; further errors are only logged")

    def _build_dialog(self, summary: str, details: str) -> QMessageBox:
        box = QMessageBox(QApplication.activeWindow())
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("SPROUTS - unexpected error")
        box.setText(f"Something went wrong:\n{summary}")
        box.setInformativeText(self._informative_text())
        box.setDetailedText(details)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        # Parent the checkbox to the box: PyQt6's setCheckBox() does not take
        # ownership, so a parentless temporary is garbage-collected and the box
        # then dereferences a deleted widget (access violation on relayout).
        box.setCheckBox(QCheckBox("Don't show error dialogs again this session", box))
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.finished.connect(self._on_dialog_finished)
        return box

    def _informative_text(self) -> str:
        parts = [
            "SPROUTS is still running, but the last action may not have finished. Save your work."
        ]
        if self._log_file:
            parts.append(f"Details were written to {self._log_file}")
        if self._extra:
            noun = "error" if self._extra == 1 else "errors"
            parts.append(f"{self._extra} more {noun} occurred since; see the log.")
        return "\n\n".join(parts)

    def _on_dialog_finished(self, _result: int) -> None:
        box, self._dialog = self._dialog, None
        checkbox = box.checkBox() if box is not None else None
        if checkbox is not None and checkbox.isChecked():
            self._muted = True


def install_exception_hooks(app: QCoreApplication | None = None) -> ExceptionHooks:
    """Install the global exception hooks once and return them.

    Errors are always logged; the critical dialog is added only when a
    ``QApplication`` exists (call this from the GUI thread, after creating it).
    """
    global _installed
    if _installed is not None and _installed.installed:
        return _installed
    app = app if app is not None else QCoreApplication.instance()
    notify: Notify | None = None
    if isinstance(app, QApplication):
        notify = CrashDialogPresenter(app, log_file=current_log_file()).report
    _installed = ExceptionHooks(notify=notify)
    _installed.install()
    return _installed


def install_qt_message_handler(logger_name: str = "qt") -> Callable[..., None] | None:
    """Route Qt's debug/warning/critical/fatal messages into ``logger_name``.

    Returns:
        The previously installed handler (pass it to ``qInstallMessageHandler``
        to restore it).
    """
    global _qt_handler
    qt_logger = logging.getLogger(logger_name)

    def handler(msg_type: QtMsgType, context: object, message: str) -> None:
        level = _QT_LEVELS.get(msg_type, logging.WARNING)
        category = getattr(context, "category", None)
        if category and category != "default":
            qt_logger.log(level, "[%s] %s", category, message)
        else:
            qt_logger.log(level, "%s", message)

    _qt_handler = handler
    return qInstallMessageHandler(handler)


def enable_fault_log(path: Path) -> bool:
    """Dump every thread's Python stack to ``path`` on a native crash.

    Returns:
        False (and logs a warning) if the file cannot be opened.
    """
    global _fault_stream
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(path, "a", encoding="utf-8")  # noqa: SIM115 - lives for the process
    except OSError as exc:
        logger.warning("Crash log disabled: cannot open %s (%s)", path, exc)
        return False
    faulthandler.enable(file=stream, all_threads=True)
    previous, _fault_stream = _fault_stream, stream
    if previous is not None:
        previous.close()
    return True
