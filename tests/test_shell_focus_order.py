"""Keyboard focus in the Library follows the screen, and every control has a name (H6).

Before: Tab went search -> expand -> collapse -> tree -> canvas -> the view-mode control
(right end of the top bar) -> the Library stage (left end) -> Load: the action widgets are
reparented into the top bar last, so they sat at the end of the focus chain. The icon-only
expand / collapse buttons, the search field, the tree and the canvas had no accessible
name (a screen reader said "button", "edit", "tree").
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_deleted_widgets():
    """Widgets a test deleteLater()s are destroyed before the next test (never at exit)."""
    yield
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    """A loaded project schedules its stale-temp sweep 2 s later (R8) on an untracked timer;
    here it is a no-op, so a window of this test never sweeps inside a later test."""
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    folder = tmp_path / "project"
    folder.mkdir()
    img = QImage(32, 24, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 60, 30))
    for stem in STEMS:
        assert img.save(str(folder / f"{stem}.jpg"))
    mw = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    mw.resize(1280, 800)
    mw.show()
    mw.activateWindow()
    assert QTest.qWaitForWindowActive(mw, 3000)
    mw.open_path(str(folder))
    end = time.monotonic() + 5
    while time.monotonic() < end and len(mw.library_index.leaves()) < len(STEMS):
        QApplication.processEvents()
        QTest.qWait(10)
    yield mw
    mw.close()
    mw.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _tab_walk(start, steps: int) -> list:
    start.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    seen = [QApplication.focusWidget()]
    for _ in range(steps):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        QApplication.processEvents()
        seen.append(QApplication.focusWidget())
    return seen


def test_tab_follows_the_top_bar_then_the_library_then_the_canvas(window):
    mw = window
    seg = mw.action_bar_view_seg
    walk = _tab_walk(mw.ribbon_buttons["Library"], 7)
    assert walk == [
        mw.ribbon_buttons["Library"],
        seg.button(seg.value()),
        mw.topbar_load_button,
        mw.library_search,
        mw.expand_all_button,
        mw.collapse_all_button,
        mw.file_list,
        mw.display_controller.magnifying_view,
    ]


def test_the_measure_stage_actions_come_after_its_stage_button(window):
    mw = window
    mw.ribbon_buttons["Measure"].click()
    QApplication.processEvents()
    walk = _tab_walk(mw.ribbon_buttons["Measure"], 3)
    assert walk == [
        mw.ribbon_buttons["Measure"],
        mw.calculate_length_button,
        mw.calculate_area_button,
        mw.topbar_load_button,
    ]


def test_welcome_tab_order_follows_the_screen(app):
    import json

    from app.gui.recent_projects import RecentProjectsStore
    from app.gui.welcome_screen import WelcomeWidget

    class Settings(dict):
        def value(self, key, default=None):
            return self.get(key, default)

        def setValue(self, key, value):  # noqa: N802 - Qt API name
            self[key] = value

    raw = json.dumps([{"path": "C:/a", "count": 1}, {"path": "C:/b", "count": 2}])
    store = RecentProjectsStore(Settings(recent_projects=raw))
    welcome = WelcomeWidget(
        on_get_started=lambda: None,
        recent_store=store,
        probe_async=False,
        path_exists=lambda _p: True,
        on_about=lambda: None,
    )
    welcome.resize(1280, 800)
    welcome.show()
    welcome.activateWindow()
    assert QTest.qWaitForWindowActive(welcome, 3000)
    rows = welcome.recent_rows()
    walk = _tab_walk(welcome.browse_button, 5)
    assert walk[:5] == [
        welcome.browse_button,
        rows[0].open_button,
        rows[0].remove_button,
        rows[1].open_button,
        rows[1].remove_button,
    ]
    assert walk[5].text() == "About"  # the footer comes last

    welcome.refresh_recents()  # rebuilt rows keep the order
    rows = welcome.recent_rows()
    assert _tab_walk(welcome.browse_button, 1)[1] is rows[0].open_button
    welcome.close()
    welcome.deleteLater()


def test_library_controls_have_accessible_names(window):
    mw = window
    assert mw.library_search.accessibleName() == "Search images"
    assert mw.expand_all_button.accessibleName() == "Expand all groups"
    assert mw.collapse_all_button.accessibleName() == "Collapse all groups"
    assert mw.file_list.accessibleName() == "Image library"
    assert mw.display_controller.magnifying_view.accessibleName() == "Image display"
