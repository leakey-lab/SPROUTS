"""Cross-module integration steps from the package notes (resource paths, shortcut
registry, overlay controls, worker errors, dashboard notice, palette)."""

from __future__ import annotations

import logging
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWebEngineWidgets  # noqa: E402, F401  (before any QApplication: main imports it)
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QPalette  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from resources.resource_utils import project_root, resource_path  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- AR-16
def test_config_project_root_is_the_shared_resolver():
    from app import config

    assert config.PROJECT_ROOT == str(project_root())
    assert config.CHECKPOINTS_DIR == os.path.join(str(project_root()), "checkpoints")


def test_dash_assets_dir_is_resolved_by_resource_utils():
    from app.visualization import dash_shell

    assert dash_shell.ASSETS_DIR == resource_path("app", "assets")
    assert os.path.isfile(os.path.join(dash_shell.ASSETS_DIR, "style.css"))


def test_icon_paths_do_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    from app.gui.ui_panels import get_icon_path

    icons = os.listdir(resource_path("resources", "icons"))
    name = next(n for n in icons if os.path.isfile(resource_path("resources", "icons", n)))
    monkeypatch.chdir(tmp_path)
    assert get_icon_path(name) == os.path.join(str(project_root()), "resources", "icons", name)


# --------------------------------------------------------------------------- shortcuts
def test_correct_sheet_documents_every_skeleton_tool_key():
    from app.gui import shortcuts, skeleton_tools

    scope = shortcuts.CORRECT
    documented = {k for spec in shortcuts.specs_for(scope) for k in spec.keys}
    assert set(skeleton_tools.TOOL_KEYS.values()) <= documented
    assert {skeleton_tools.SMOOTH_KEY, "Ctrl+S", "[", "]", "Delete", "Backspace"} <= documented
    assert shortcuts.find_spec("delete_point", scope) is None
    assert shortcuts.keys_for("delete_branch", scope) == ("Delete", "Backspace")
    actions = [spec.action for spec in shortcuts.specs_for(scope)]
    assert len(actions) == len(set(actions))


# --------------------------------------------------------------------------- overlays
def test_escape_closes_the_open_enhance_popover(app):
    from app.gui.widgets import EnhancePopover

    host = QWidget()
    host.resize(640, 480)
    popover = EnhancePopover(host)
    popover.set_content(QLabel("content"))
    seen = []
    popover.toggled.connect(seen.append)
    host.show()
    try:
        popover.toggle()
        assert not popover.isHidden()
        QTest.keyClick(popover, Qt.Key.Key_Escape)
        assert popover.isHidden()
        assert seen == [True, False]
        QTest.keyClick(popover, Qt.Key.Key_Escape)  # closed: Esc is not consumed twice
        assert seen == [True, False]
    finally:
        host.close()


def test_editor_enhance_button_shows_whether_the_popover_is_open(app):
    from app.gui.correct.editor import CorrectEditor

    iface = CorrectEditor()
    try:
        assert iface.enhance_button.isCheckable() and not iface.enhance_button.isChecked()
        iface.enhance_button.click()
        assert iface.enhance_button.isChecked() and not iface.enhance_popover.isHidden()
        iface.enhance_popover.toggle()
        assert not iface.enhance_button.isChecked()
    finally:
        iface.close()
        iface.deleteLater()
        flush_deleted()


def test_segmented_control_reports_reclicks_on_activated(app):
    from app.gui import shell_chrome
    from app.gui.widgets import SegmentedControl

    seg = SegmentedControl([("length", "Length"), ("area", "Area")], value="length")
    changed, activated = [], []
    seg.valueChanged.connect(changed.append)
    seg.activated.connect(activated.append)
    seg._on_click("length")
    seg._on_click("area")
    seg._on_click("area")
    assert changed == ["area"]
    assert activated == ["length", "area", "area"]
    assert shell_chrome._SelectorSegments is SegmentedControl


def test_stage_tooltips_name_their_alt_key(app):
    from unittest.mock import MagicMock

    from app.gui import shell_chrome, shortcuts
    from app.gui.key_text import platform_keys

    bar = shell_chrome.ShellTopbar(MagicMock())  # a stand-in window: only slots are wired
    for (key, *_), keys in zip(shell_chrome.STAGES, shortcuts.stage_keys(), strict=True):
        assert bar.stage_buttons[key].toolTip().endswith(f"({platform_keys(' / '.join(keys))})")


# --------------------------------------------------------------------------- workers
def test_file_locked_error_is_logged_without_a_traceback(app, caplog, tmp_path):
    from app.io_utils import FileLockedError
    from app.workers import GuardedThread, describe_error, is_user_condition

    exc = FileLockedError(tmp_path / "root_lengths.csv")

    class Locked(GuardedThread):
        def work(self):
            raise exc

    thread = Locked()
    messages = []
    thread.failed.connect(messages.append)
    with caplog.at_level(logging.WARNING, logger="app.handlers.worker_base"):
        thread.run()  # the error boundary, called synchronously
    assert messages == [str(exc)] == [describe_error(exc)]
    assert is_user_condition(exc) and not is_user_condition(ValueError("x"))
    records = [r for r in caplog.records if r.name == "app.handlers.worker_base"]
    assert [r.levelno for r in records] == [logging.WARNING]
    assert records[0].exc_info is None


def test_unexpected_worker_errors_keep_their_traceback(app, caplog):
    from app.workers import GuardedThread

    class Broken(GuardedThread):
        def work(self):
            raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="app.handlers.worker_base"):
        Broken().run()
    assert any(r.exc_info for r in caplog.records if r.name == "app.handlers.worker_base")


# --------------------------------------------------------------------------- FR-15
def _find(component, component_id):
    if getattr(component, "id", None) == component_id:
        return component
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            found = _find(child, component_id)
            if found is not None:
                return found
    elif children is not None and not isinstance(children, str):
        return _find(children, component_id)
    return None


def test_dashboard_page_shows_the_exclusions_notice_only_when_rows_were_left_out():
    from app.data_processing.data_processor import RowExclusions
    from app.visualization.dash_app import LENGTH_SHELL
    from app.visualization.dash_shell import DashboardShell, exclusions_note

    class Processor:
        exclusions = RowExclusions(failed=2, bad_name=0, unreadable=0)

    note = exclusions_note(Processor())
    assert note and note.startswith("2 rows are not shown")
    assert exclusions_note(object()) is None

    shell = DashboardShell(LENGTH_SHELL)
    with_note = shell.page(controls=[], chart=[], notice=note)
    assert _find(with_note, "viz-data-notice").children == note
    assert _find(shell.page(controls=[], chart=[]), "viz-data-notice") is None


# --------------------------------------------------------------------------- palette
def test_disabled_text_uses_the_disabled_tier(app):
    import main
    from app.gui.widgets import tokens

    palette = main._sprouts_palette()
    group = QPalette.ColorGroup.Disabled
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText):
        assert palette.color(group, role).name() == tokens.TEXT_DISABLED
