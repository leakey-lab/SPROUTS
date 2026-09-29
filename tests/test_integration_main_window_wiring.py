"""MainWindow integration wiring (integrator): feedback, status surface, window state,
recents, editor lifecycle, jobs, global shortcuts and measured values.

Offscreen; projects are tmp_path folders of tiny synthetic JPGs. Every window gets
its own INI QSettings, so the user's real settings are never read or written.
"""

import csv
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

USER = Qt.ItemDataRole.UserRole
STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def make_window(app, settings):
    from app.gui.main_window import MainWindow

    windows = []

    def make(**kwargs):
        w = MainWindow(settings=kwargs.pop("settings", settings))
        windows.append(w)
        return w

    yield make
    for w in windows:
        for editor in w._editors().values():
            editor.maybe_save_changes = lambda: True
        w.close()
        w.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def mw(make_window):
    return make_window()


def _project(folder, stems=STEMS):
    folder.mkdir(parents=True, exist_ok=True)
    img = QImage(32, 24, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 60, 30))
    for stem in stems:
        assert img.save(str(folder / f"{stem}.jpg"))
    return folder


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def _load(mw, folder):
    mw.open_path(str(folder))
    assert _wait(lambda: len(mw.image_manager.images) == len(STEMS) and mw._image_leaves())


# --------------------------------------------------------------------------- #
#  Status surface + feedback (shell-chrome / foundation-runtime AR-07)
# --------------------------------------------------------------------------- #
def test_feedback_drives_the_one_status_surface(mw):
    from app.gui.ui_feedback import QtUiFeedback

    assert isinstance(mw.feedback, QtUiFeedback)
    assert mw.status_bar.isHidden()  # the statusline is the only band
    mw.feedback.status("Hello from a handler")
    assert mw.statusline_message.text() == "Hello from a handler"
    mw.feedback.task_started("Generating masks", total=10)
    mw.feedback.task_progress(4)
    assert "4 / 10" in mw.task_progress.label.text()
    mw.feedback.task_finished("Done")
    assert not hasattr(mw, "mask_handler")  # AR-18: dead MaskHandler is gone


def test_toasts_stay_above_the_statusline(mw, app):
    mw.app_stack.setCurrentIndex(1)
    mw.show()
    app.processEvents()
    assert mw._toast_bottom_offset() >= mw.statusline.height() > 0


# --------------------------------------------------------------------------- #
#  Window state + recents (UXS-04 step 5, UXS-14)
# --------------------------------------------------------------------------- #
def test_window_state_and_last_folder_round_trip(make_window, settings, tmp_path):
    folder = _project(tmp_path / "proj")
    first = make_window()
    first.resize(790, 700)
    _load(first, folder)
    first.close()
    assert settings.value("window/geometry") is not None
    assert os.path.normpath(first.window_state.last_folder()) == os.path.normpath(str(folder))

    second = make_window()
    assert (second.width(), second.height()) == (790, 700)


def test_loading_records_a_recent_project(mw, tmp_path):
    folder = _project(tmp_path / "proj")
    _load(mw, folder)
    entries = mw.recent_projects.entries()
    assert entries and os.path.normpath(entries[0].path) == os.path.normpath(str(folder))
    assert entries[0].count == len(STEMS)


def test_missing_folder_changes_nothing(mw, tmp_path):
    seen = []
    mw.notify = lambda msg, kind="success", **_k: seen.append((kind, msg))
    mw.open_path(str(tmp_path / "nope"))
    assert seen and seen[0][0] == "danger"
    assert mw.app_stack.currentIndex() == 0


# --------------------------------------------------------------------------- #
#  Editor lifecycle (Correct / Trace GT)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("attr", ["correct_editor", "gt_tracing_interface"])
def test_leaving_an_editor_that_refuses_stays_put(mw, attr):
    from app.gui.panels import Panel

    editor = getattr(mw, attr)
    mw.right_panel.setCurrentWidget(editor)
    editor.maybe_save_changes = lambda: False
    assert mw.switch_right_panel(Panel.DISPLAY) is False
    assert mw.right_panel.currentWidget() is editor
    editor.maybe_save_changes = lambda: None  # legacy stub: not a refusal
    assert mw.switch_right_panel(Panel.DISPLAY) is True


def test_open_is_cancelled_by_an_editor_that_keeps_its_edits(mw, tmp_path):
    folder = _project(tmp_path / "proj")
    calls = []
    mw.image_manager.load_images = calls.append
    mw.correct_editor.maybe_save_changes = lambda: False
    mw.open_path(str(folder))
    assert calls == [] and mw.app_stack.currentIndex() == 0
    mw.correct_editor.maybe_save_changes = lambda: True
    mw.open_path(str(folder))
    assert calls == [str(folder)]


def test_close_is_cancelled_by_an_editor_that_keeps_its_edits(mw, app):
    mw.show()
    mw.correct_editor.maybe_save_changes = lambda: False
    mw.close()
    app.processEvents()
    assert mw.isVisible()


def test_correct_editor_notices_become_toasts(mw):
    seen = []
    mw.toasts.show = lambda msg, kind="success", *a, **k: seen.append((kind, msg))
    mw.correct_editor.notice.emit("Saved skeleton · a.jpg", "success")
    mw.correct_editor.notice.emit("Could not save", "danger")
    assert seen == [("success", "Saved skeleton · a.jpg"), ("danger", "Could not save")]


def test_selection_follows_an_editor_that_kept_its_image(mw, tmp_path):
    folder = _project(tmp_path / "proj")
    _load(mw, folder)
    leaves = mw._image_leaves()
    kept = mw.image_manager.get_image_path(leaves[0].data(0, USER))
    editor = mw.correct_editor
    mw.right_panel.setCurrentWidget(editor)
    editor.load_image(kept, str(folder))
    editor.load_image = lambda *_a, **_k: None  # it keeps its image (the prompt said Cancel)
    mw.on_image_selected(leaves[2])
    assert mw.file_list.currentItem() is leaves[0]


# --------------------------------------------------------------------------- #
#  Jobs (RB-04 / RB-07)
# --------------------------------------------------------------------------- #
def test_generate_all_chains_skeletons_only_on_success(mw):
    seen = {}
    mw.mask_generation_handler.generate_masks = lambda on_success=None: seen.setdefault(
        "on_success", on_success
    )
    mw.generate_all()
    assert seen["on_success"] == mw.skeleton_handler.generate_skeleton


def test_job_buttons_are_disabled_while_a_job_runs(mw):
    mw.generate_all_button.setToolTip("Generate masks and skeletons")
    mw.jobs._name = "Generating masks"
    mw._on_jobs_busy_changed(True)
    assert not mw.generate_all_button.isEnabled()
    assert "Generating masks is running" in mw.calculate_length_button.toolTip()
    mw.jobs._name = None
    mw._on_jobs_busy_changed(False)
    assert mw.generate_all_button.isEnabled()
    assert mw.generate_all_button.toolTip() == "Generate masks and skeletons"


def test_close_while_a_job_runs_asks_first(mw, app, monkeypatch):
    mw.show()
    stopped = []
    monkeypatch.setattr(mw.jobs, "is_busy", lambda: True)
    monkeypatch.setattr(mw.jobs, "stop_all", lambda timeout_ms=5000: stopped.append(1) or True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    mw.close()
    app.processEvents()
    assert mw.isVisible() and not stopped
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    mw.close()
    assert stopped == [1]


# --------------------------------------------------------------------------- #
#  Global shortcuts (UXS-10) + About (UXS-15)
# --------------------------------------------------------------------------- #
def test_global_shortcuts_come_from_the_registry(mw):
    from app.gui import shell_chrome, shortcuts

    keys = {
        action: [s.key().toString() for s in bound]
        for action, bound in mw._global_shortcuts.items()
    }
    assert keys["open_folder"] == list(shortcuts.keys_for("open_folder", shortcuts.GLOBAL))
    assert len(keys["go_to_stage"]) == len(mw.ribbon_buttons) == len(shell_chrome.STAGES)
    assert mw._help_shortcut_f1.key().toString() == "F1"
    for bound in mw._global_shortcuts.values():
        assert all(s.context() == Qt.ShortcutContext.WindowShortcut for s in bound)
    # Paging keys are never window-wide (the editors bind their own): no ambiguity.
    editors = set(mw._editors().values())
    assert all(s.parent() not in editors for s in mw._paging_shortcuts)
    assert all(
        s.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut for s in mw._paging_shortcuts
    )


def test_stage_shortcut_needs_a_loaded_folder(mw, tmp_path):
    from app.gui import shell_chrome

    measure = [key for key, *_ in shell_chrome.STAGES].index("Measure")
    slot = mw._global_shortcuts["go_to_stage"][measure]
    slot.activated.emit()
    assert not mw.ribbon_buttons["Measure"].isChecked()  # Welcome: nothing happens
    _load(mw, _project(tmp_path / "proj"))
    slot.activated.emit()
    assert mw.ribbon_buttons["Measure"].isChecked()


def test_about_and_shortcut_sheet_open(mw, monkeypatch):
    from app.gui import about_dialog, main_window

    opened = []
    monkeypatch.setattr(about_dialog.AboutDialog, "exec", lambda self: opened.append("about"))
    monkeypatch.setattr(main_window.ShortcutsDialog, "exec", lambda self: opened.append("keys"))
    mw.show_about()
    mw.show_shortcuts()
    assert opened == ["about", "keys"]


# --------------------------------------------------------------------------- #
#  Measured values in the metrics bar (AR-06 / UXS-03, KNOWN_ISSUES #3)
# --------------------------------------------------------------------------- #
def test_metrics_bar_shows_the_measured_csv_values(mw, tmp_path):
    from app import config

    folder = _project(tmp_path / "proj")
    (folder / "skeletons").mkdir()
    with open(folder / "skeletons" / "root_lengths.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(config.LENGTH_CSV_HEADERS)
        writer.writerow([STEMS[1], "1", "12", "2024.07.18", "11:15:12", "18.77", ""])
    calls = []
    real = mw.metrics_bar.set_metrics
    mw.metrics_bar.set_metrics = lambda *a, **k: calls.append(k) or real(*a, **k)
    _load(mw, folder)
    item = mw.library_index.item_for(STEMS[1])
    mw.file_list.setCurrentItem(item)
    mw.on_image_selected(item)
    assert _wait(lambda: calls and calls[-1].get("length") == pytest.approx(18.77))
    assert calls[-1]["name"] == STEMS[1]
    assert calls[-1]["source"].endswith("root_lengths.csv")
