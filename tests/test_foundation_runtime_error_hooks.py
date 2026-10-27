"""
Tests for the global exception hooks (``app/gui/error_hooks.py``).

PyQt6 calls ``qFatal`` (the process aborts, no traceback logged, unsaved edits
lost) when an exception escapes a slot or ``QThread.run`` while
``sys.excepthook`` is Python's default. The subprocess test pins that the
installed hooks keep the app alive; a control run without them proves the
premise. The in-process tests cover logging, delegation of KeyboardInterrupt,
re-entrance safety and the single coalescing, non-modal dialog.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
import threading
import time

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer, qInstallMessageHandler, qWarning
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from app.gui import error_hooks
from app.gui.error_hooks import (
    CrashDialogPresenter,
    ExceptionHooks,
    install_exception_hooks,
    install_qt_message_handler,
)
from tests.qt_teardown import delete_widgets

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SLOT_AND_THREAD_SCRIPT = textwrap.dedent("""
    import os, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {repo!r})
    from PyQt6.QtCore import QThread, QTimer
    from PyQt6.QtWidgets import QApplication, QPushButton

    app = QApplication([])
    if {install}:
        import logging
        logging.basicConfig(level=logging.INFO)
        from app.gui.error_hooks import install_exception_hooks
        install_exception_hooks(app)

    def slot():
        raise OSError("mask folder is read-only")

    class Worker(QThread):
        def run(self):
            raise PermissionError(13, "Permission denied", "root_lengths.csv")

    button = QPushButton("save")
    button.clicked.connect(slot)
    worker = Worker()
    QTimer.singleShot(50, button.click)
    QTimer.singleShot(80, worker.start)
    QTimer.singleShot(800, lambda: (worker.wait(), print("STILL ALIVE", flush=True), app.quit()))
    app.exec()
    """)


def _run_script(install: bool) -> subprocess.CompletedProcess:
    code = _SLOT_AND_THREAD_SCRIPT.format(repo=REPO_ROOT, install=install)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.002)


def _exc_info(exc: BaseException):
    try:
        raise exc
    except BaseException as caught:  # noqa: BLE001 - building exc_info on purpose
        return type(caught), caught, caught.__traceback__


# --- the regression this module exists for ------------------------------------------


def test_slot_and_thread_exceptions_no_longer_abort_the_app():
    control = _run_script(install=False)
    assert control.returncode != 0, "premise: PyQt6 aborts without an excepthook"
    assert "STILL ALIVE" not in control.stdout

    hooked = _run_script(install=True)
    assert hooked.returncode == 0, hooked.stderr
    assert "STILL ALIVE" in hooked.stdout
    assert "OSError: mask folder is read-only" in hooked.stderr
    assert "PermissionError" in hooked.stderr and "root_lengths.csv" in hooked.stderr
    assert hooked.stderr.count("Traceback (most recent call last)") >= 2


def test_without_a_qapplication_errors_are_logged_but_no_dialog_is_built():
    code = textwrap.dedent(f"""
        import sys, threading
        sys.path.insert(0, {REPO_ROOT!r})
        import logging
        logging.basicConfig(level=logging.INFO)
        from PyQt6.QtCore import QCoreApplication
        from app.gui.error_hooks import install_exception_hooks
        hooks = install_exception_hooks()
        worker = threading.Thread(target=lambda: 1 / 0, name="no-gui")
        worker.start(); worker.join()
        print(QCoreApplication.instance() is None, hooks._notify is None)
        """)
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["True", "True"]
    assert "Unhandled exception in thread no-gui" in proc.stderr
    assert "ZeroDivisionError" in proc.stderr


# --- ExceptionHooks core ------------------------------------------------------------


def test_handle_logs_critical_with_traceback_and_notifies(caplog):
    seen = []
    hooks = ExceptionHooks(notify=lambda summary, details: seen.append((summary, details)))
    with caplog.at_level(logging.CRITICAL, logger=error_hooks.logger.name):
        hooks.handle(*_exc_info(ValueError("bad pixel")))
    record = caplog.records[-1]
    assert record.levelno == logging.CRITICAL
    assert record.exc_info[0] is ValueError
    assert seen[0][0] == "ValueError: bad pixel"
    assert "Traceback (most recent call last)" in seen[0][1]


def test_keyboard_interrupt_goes_to_the_default_hook(monkeypatch):
    delegated, notified = [], []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: delegated.append(a[0]))
    hooks = ExceptionHooks(notify=lambda *a: notified.append(a))
    hooks.handle(*_exc_info(KeyboardInterrupt()))
    assert delegated == [KeyboardInterrupt]
    assert notified == []


def test_failing_notifier_never_escapes_the_hook(caplog):
    def broken_notify(summary, details):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    hooks = ExceptionHooks(notify=broken_notify)
    with caplog.at_level(logging.CRITICAL, logger=error_hooks.logger.name):
        hooks.handle(*_exc_info(TypeError("oops")))  # must not raise
    assert any(r.exc_info and r.exc_info[0] is TypeError for r in caplog.records)


def test_reentrant_exception_is_not_handled_recursively(monkeypatch):
    fallback = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: fallback.append(a[0]))
    calls = []

    def reentrant_notify(summary, details):
        calls.append(summary)
        hooks.handle(*_exc_info(RuntimeError("raised while reporting")))

    hooks = ExceptionHooks(notify=reentrant_notify)
    hooks.handle(*_exc_info(ValueError("first")))
    assert calls == ["ValueError: first"]
    assert fallback == [RuntimeError]


def test_thread_hook_logs_and_ignores_system_exit(caplog):
    seen = []
    hooks = ExceptionHooks(notify=lambda summary, details: seen.append(summary))
    hooks.install()
    try:
        with caplog.at_level(logging.CRITICAL, logger=error_hooks.logger.name):
            worker = threading.Thread(target=lambda: 1 / 0, name="decode-worker")
            worker.start()
            worker.join(5)
            quiet = threading.Thread(target=sys.exit, name="quiet")
            quiet.start()
            quiet.join(5)
    finally:
        hooks.uninstall()
    assert seen == ["ZeroDivisionError: division by zero"]
    assert any("decode-worker" in r.getMessage() for r in caplog.records)


def test_install_is_idempotent_and_uninstall_restores(qapp):
    before_sys, before_thread = sys.excepthook, threading.excepthook
    hooks = install_exception_hooks(qapp)
    try:
        assert install_exception_hooks(qapp) is hooks
        assert sys.excepthook is not before_sys
        assert threading.excepthook is not before_thread
    finally:
        hooks.uninstall()
    assert sys.excepthook is before_sys
    assert threading.excepthook is before_thread
    fresh = install_exception_hooks(qapp)  # a new install after uninstall works
    fresh.uninstall()
    assert fresh is not hooks


# --- CrashDialogPresenter -----------------------------------------------------------


def test_burst_of_errors_shows_one_non_modal_dialog(qapp):
    presenter = CrashDialogPresenter(log_file=None)
    try:
        presenter.report("ValueError: first", "tb1")
        presenter.report("ValueError: second", "tb2")
        worker = threading.Thread(target=presenter.report, args=("OSError: third", "tb3"))
        worker.start()
        worker.join(5)
        _pump()
        dialog = presenter.dialog
        assert dialog is not None and dialog.isVisible()
        assert not dialog.isModal()
        assert "ValueError: first" in dialog.text()
        assert "2 more" in dialog.informativeText()
        assert dialog.detailedText() == "tb1"
        dialog.close()
        _pump()
        assert presenter.dialog is None
        presenter.report("KeyError: later", "tb4")
        _pump()
        assert presenter.dialog is not None and "KeyError: later" in presenter.dialog.text()
    finally:
        if presenter.dialog is not None:
            presenter.dialog.close()
        _pump()


def test_dont_show_again_mutes_the_presenter(qapp):
    presenter = CrashDialogPresenter(log_file=None)
    presenter.report("ValueError: first", "tb")
    _pump()
    presenter.dialog.checkBox().setChecked(True)
    presenter.dialog.close()
    _pump()
    presenter.report("ValueError: again", "tb")
    _pump()
    assert presenter.muted is True
    assert presenter.dialog is None


def test_a_dialog_deleted_with_its_parent_counts_as_closed(qapp, monkeypatch):
    """The box is parented to the active window. Closing that dialog (WA_DeleteOnClose, or
    deleteLater after exec) deletes the box without finished(): the next error must show a
    new box, not mute every later dialog (S10-01)."""
    parent = QDialog()  # the prompt or sheet that is the active window when the error comes
    parent.show()
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: parent))
    presenter = CrashDialogPresenter(log_file=None)
    try:
        presenter.report("ValueError: first", "tb1")
        _pump()
        first = presenter.dialog
        assert first is not None and first.parent() is parent
        delete_widgets([parent])  # the crash box goes with it
        assert sip.isdeleted(first)
        monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: None))
        presenter.report("OSError: second", "tb2")
        _pump()
        assert presenter.muted is False
        shown = presenter.dialog
        assert shown is not None and not sip.isdeleted(shown) and shown.isVisible()
        assert "OSError: second" in shown.text()
        assert "more" not in shown.informativeText()
    finally:
        if presenter.dialog is not None and not sip.isdeleted(presenter.dialog):
            presenter.dialog.close()
        _pump()


def test_an_error_queued_while_the_box_closes_keeps_one_dialog(qapp, monkeypatch):
    """A report queued before the user's OK builds the next box before the first one's
    deferred delete runs; that delete must not orphan it (at most one dialog)."""
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: None))
    presenter = CrashDialogPresenter(log_file=None)
    try:
        presenter.report("ValueError: first", "tb1")
        _pump()
        first = presenter.dialog
        assert first is not None

        def queue_then_ok():
            presenter.report("OSError: second", "tb2")  # posted before the deferred delete
            first.button(QMessageBox.StandardButton.Ok).click()

        loop = QEventLoop()
        QTimer.singleShot(0, queue_then_ok)
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        assert sip.isdeleted(first)
        second = presenter.dialog
        assert second is not None and not sip.isdeleted(second) and second.isVisible()
        presenter.report("KeyError: third", "tb3")
        _pump()
        title = second.windowTitle()
        visible = [
            w
            for w in QApplication.topLevelWidgets()
            if isinstance(w, QMessageBox) and w.isVisible() and w.windowTitle() == title
        ]
        assert visible == [second]
        assert "1 more error" in second.informativeText()
    finally:
        if presenter.dialog is not None and not sip.isdeleted(presenter.dialog):
            presenter.dialog.close()
        _pump()


def test_dialog_names_the_log_file(qapp, tmp_path):
    log_file = tmp_path / "app.log"
    presenter = CrashDialogPresenter(log_file=log_file)
    presenter.report("ValueError: x", "tb")
    _pump()
    try:
        assert str(log_file) in presenter.dialog.informativeText()
    finally:
        presenter.dialog.close()
        _pump()


# --- Qt messages ---------------------------------------------------------------------


def test_qt_warnings_are_routed_to_the_logger(caplog):
    previous = install_qt_message_handler()
    try:
        with caplog.at_level(logging.WARNING, logger="qt"):
            qWarning(b"QThread: something odd")
    finally:
        qInstallMessageHandler(previous)
    assert any(
        r.name == "qt" and r.levelno == logging.WARNING and "something odd" in r.getMessage()
        for r in caplog.records
    )
