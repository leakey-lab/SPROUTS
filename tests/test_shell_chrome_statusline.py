"""StatusLine: one status surface bound to QStatusBar, runtime chip (UXS-06/UI-14/PG-12)."""

from __future__ import annotations

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMainWindow, QStatusBar  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui.shell_chrome import StatusLine, runtime_label  # noqa: E402
from app.gui.task_progress import TaskProgressWidget  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

RUNTIME = "app.inference.runtime"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window(idle=None):
    win = QMainWindow()
    line = StatusLine(idle_text=idle)
    win.setCentralWidget(line)
    bar = QStatusBar()
    win.setStatusBar(bar)
    tp = TaskProgressWidget()
    bar.addPermanentWidget(tp)
    line.adopt(bar, tp)
    return win, line, bar, tp


def test_status_bar_messages_show_in_the_statusline(app):
    win, line, bar, _tp = _window()
    bar.showMessage("Calculating root lengths...")
    assert line.message.text() == "Calculating root lengths..."
    bar.clearMessage()
    assert line.message.text() == "Ready"


def test_idle_text_is_injectable(app):
    win, line, bar, _tp = _window(idle=lambda: "Ready · 40 images")
    bar.showMessage("x")
    bar.clearMessage()
    assert line.message.text() == "Ready · 40 images"


def test_adopt_hides_the_status_bar_and_hosts_progress(app):
    win, line, bar, tp = _window()
    win.show()
    app.processEvents()
    assert not bar.isVisible()
    assert tp.parentWidget() is line
    tp.start("Generating masks")
    app.processEvents()
    assert tp.isVisible()
    assert line.state() == "working"
    tp.fail("nope")
    assert line.state() == "error"
    win.close()


def test_adopt_is_idempotent(app):
    win, line, bar, tp = _window()
    line.adopt(bar, tp)
    seen = []
    bar.messageChanged.connect(seen.append)
    bar.showMessage("once")
    assert line.message.text() == "once"


def test_runtime_label_never_imports_the_runtime(monkeypatch):
    monkeypatch.delitem(sys.modules, RUNTIME, raising=False)
    assert runtime_label() == "Device: auto"
    assert RUNTIME not in sys.modules


def _fake_runtime(monkeypatch, *, resolved, cuda, name="NVIDIA RTX"):
    mod = types.ModuleType(RUNTIME)
    cfg = types.SimpleNamespace(is_cuda=cuda, device_name=name if cuda else "cpu")

    def get_runtime():
        return cfg

    get_runtime.cache_info = lambda: types.SimpleNamespace(currsize=1 if resolved else 0)
    mod.get_runtime = get_runtime
    monkeypatch.setitem(sys.modules, RUNTIME, mod)
    return mod


def test_runtime_label_cpu(monkeypatch):
    _fake_runtime(monkeypatch, resolved=True, cuda=False)
    assert runtime_label() == "CPU"


def test_runtime_label_gpu(monkeypatch):
    _fake_runtime(monkeypatch, resolved=True, cuda=True)
    assert runtime_label() == "GPU · NVIDIA RTX"


def test_runtime_label_unresolved_does_not_probe(monkeypatch):
    mod = _fake_runtime(monkeypatch, resolved=False, cuda=True)
    mod.get_runtime = types.SimpleNamespace(cache_info=mod.get_runtime.cache_info)  # not callable
    assert runtime_label() == "Device: auto"


def test_runtime_label_prefers_describe_runtime(monkeypatch):
    mod = _fake_runtime(monkeypatch, resolved=True, cuda=False)
    mod.describe_runtime = lambda: "CPU · masks ok"
    assert runtime_label() == "CPU · masks ok"


def test_runtime_summary_object_and_missing_models_warn(app, monkeypatch):
    mod = _fake_runtime(monkeypatch, resolved=True, cuda=False)
    mod.describe_runtime = lambda: types.SimpleNamespace(
        text="CPU · FP32 · mask model missing", models_ok=False
    )
    _win, line, _bar, _tp = _window()
    line.refresh_runtime()
    assert line.info.toolTip().endswith("CPU · FP32 · mask model missing")
    assert line.info.property("models_ok") is False


def test_long_runtime_text_is_elided(app, monkeypatch):
    mod = _fake_runtime(monkeypatch, resolved=True, cuda=False)
    mod.describe_runtime = lambda: "GPU · " + "X" * 400
    _win, line, _bar, _tp = _window()
    line.refresh_runtime()
    assert line.info.sizeHint().width() <= 340
    assert "X" * 400 in line.info.toolTip()


def test_statusline_chip_is_not_hard_coded(app, monkeypatch):
    _fake_runtime(monkeypatch, resolved=True, cuda=False)
    _win, line, _bar, _tp = _window()
    line.refresh_runtime()
    assert line.info.text() == "CPU"
    assert "CUDA 12.8" not in line.info.text()


def test_main_window_uses_one_status_surface(app):
    from app.gui.main_window import MainWindow
    from app.gui.shell_chrome import install_status_surface

    mw = MainWindow()
    try:
        install_status_surface(mw)  # idempotent: MainWindow may already have done it
        mw.app_stack.setCurrentIndex(1)
        mw.show()
        app.processEvents()
        assert not mw.status_bar.isVisible()
        mw.status_bar.showMessage("Mask saved for a.png", 3000)
        assert mw.statusline_message.text() == "Mask saved for a.png"
        assert mw.task_progress.parentWidget() is mw.statusline
        mw.statusline.refresh_runtime()
        assert mw.statusline_info.toolTip().endswith(runtime_label())  # real, not hard-coded
        assert mw.statusline.height() == 28
    finally:
        mw.close()
        mw.deleteLater()
        flush_deleted()
