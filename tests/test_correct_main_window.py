"""MainWindow with the Correct stage (plan P8b, contracts C17 and C19).

One Correct stage (``MainWindow.correct_editor``, right-panel page ``Panel.CORRECT``)
replaced the two editors of the mask and the skeleton: six stages with Alt+1 … Alt+6; one
prompt when leaving it (a refused leave keeps the stage and its page); Trace GT and Correct
hand over to each other; tree clicks, the arrow-key debounce and PgDn / PgUp open images in
it; the editor's save reports reach the Library badge and the metrics bar once, on every
save path, and a skeleton save reaches neither (FR-03); the generated-skeleton resolver, the
OpenGL switch, the toast offset and the F1 sheet know the page; scikit-image is imported in
the thread pool once the event loop runs, never on the GUI thread.

Offscreen. Every project is a tmp_path folder of four 200x150 photos, the first with a
model mask and a generated skeleton; every window has its own INI settings, so the user's
settings are never read or written.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint, QRect, QSettings, Qt, QThreadPool  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import (  # noqa: E402
    canvas_gl,
    image_manager,
    shell_chrome,
    shortcuts,
    skeleton_graph_model,
)
from app.gui.correct.layer import MASK, SKELETON  # noqa: E402
from app.gui.panels import Panel  # noqa: E402
from app.gui.skeleton_graph_model import SkeletonCorrectionModel  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    CANCEL,
    DISCARD,
    SAVE,
    H,
    W,
    answer_prompts,
    box_plane,
    draw_line,
    paint,
    plane,
    qapp,
    raster,
    record,
    same_path,
    write_image,
    write_mask,
    write_skeleton,
)

STEMS = tuple(f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (21, 22, 23, 24))
STAGE_NAMES = ["Library", "Trace GT", "Generate", "Correct", "Measure", "Visualize"]
#: The code under test: an exception it raises inside a Qt slot fails the test.
UNDER_TEST = tuple(
    os.path.normcase(os.path.join("app", "gui", *part))
    for part in (("main_window.py",), ("shell_chrome.py",), ("correct", "editor.py"))
)


# ============================================================================== helpers
@dataclass(frozen=True)
class Project:
    """A project folder: four photos; the first has a model mask and a generated skeleton."""

    folder: str

    def image(self, i: int) -> str:
        """The photo of image ``i``."""
        return os.path.join(self.folder, f"{STEMS[i]}.jpg")

    def mask(self, i: int) -> str:
        """The saved mask of image ``i``."""
        return os.path.join(self.folder, "mask", f"{STEMS[i]}.png")

    def correction(self, i: int) -> str:
        """The skeleton correction of image ``i``."""
        return os.path.join(self.folder, "skeleton_corrections", f"{STEMS[i]}_skeleton.png")

    def generated(self, i: int) -> str:
        """The generated skeleton of image ``i``."""
        return os.path.join(self.folder, "skeletons", f"{STEMS[i]}_fake.png")


def new_project(root) -> Project:
    """Four photos in ``root/project``; image 0 with a model mask and a generated skeleton."""
    project = Project(str(root / "project"))
    for i in range(len(STEMS)):
        write_image(project.image(i), value=60 + 20 * i)
    write_mask(project.mask(0), box_plane())
    write_skeleton(project.generated(0))
    return project


def settle() -> None:
    """Let posted events and zero timers run."""
    for _ in range(3):
        QApplication.processEvents()


def wait(predicate, timeout: float = 5.0) -> bool:
    """Run the event loop until ``predicate()`` holds (or ``timeout`` seconds pass)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def open_project(window, project: Project) -> None:
    """Open ``project`` in ``window`` (the folder scan runs in a thread) and expand the tree."""
    window.open_path(project.folder)
    assert wait(lambda: len(window.image_manager.images) == len(STEMS) and window._image_leaves())
    window.file_list.expandAll()
    settle()


def leaf(window, i: int):
    """The library row of image ``i``."""
    return window.library_index.item_for(STEMS[i])


def select(window, i: int) -> None:
    """A click on the library row of image ``i``."""
    item = leaf(window, i)
    window.file_list.setCurrentItem(item)
    window.on_tree_item_clicked(item, 0)
    settle()


def enter(window, stage: str) -> None:
    """A click on the topbar's ``stage`` button."""
    window.ribbon_buttons[stage].click()
    settle()


def activate(window) -> None:
    """Make ``window`` the active window (keyboard shortcuts need it)."""
    window.show()
    window.activateWindow()
    assert QTest.qWaitForWindowActive(window, 3000)
    settle()


def record_toasts(window) -> list[tuple[str, str]]:
    """Every toast the window shows from now on, as ``(kind, message)``."""
    seen: list[tuple[str, str]] = []
    window.toasts.show = lambda message, kind="success", *_a, **_k: seen.append((kind, message))
    return seen


# ============================================================================= fixtures
@pytest.fixture(scope="module")
def app():
    return qapp()


def _no_dialog(*_args, **_kwargs):
    pytest.fail("a file dialog was opened")


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    """No unexpected prompt or file dialog, no GL, the user's config untouched, and no
    exception of the code under test hidden in a Qt slot (PyQt hands those to
    ``sys.excepthook``)."""
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", _no_dialog)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", _no_dialog)
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    # A loaded project schedules its stale-temp sweep 2 s later (R8) on an untracked timer:
    # a no-op here, so a window of this test never sweeps inside a later test.
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    errors = []

    def hook(kind, value, tb):
        files = [os.path.normcase(frame.filename) for frame in traceback.extract_tb(tb)]
        if any(name.endswith(part) for name in files for part in UNDER_TEST):
            errors.append(value)
        else:
            sys.__excepthook__(kind, value, tb)

    monkeypatch.setattr(sys, "excepthook", hook)
    yield
    assert errors == []


@pytest.fixture
def make_window(app, tmp_path):
    """``make(*, show=True)`` builds a MainWindow (1280x800) with its own INI settings;
    every window made is closed without prompts and deleted afterwards."""
    from app.gui.main_window import MainWindow

    made = []

    def make(*, show: bool = True):
        path = tmp_path / f"settings_{len(made)}.ini"
        window = MainWindow(settings=QSettings(str(path), QSettings.Format.IniFormat))
        made.append(window)
        window.resize(1280, 800)
        if show:
            window.show()
            settle()
        return window

    yield make
    delete_widgets(made, prepare=_quiet_window)
    QApplication.processEvents()


def _quiet_window(window) -> None:
    """No save prompt and no scheduled skeleton render when ``window`` closes."""
    for editor in getattr(window, "_editors", dict)().values():
        editor.maybe_save_changes = lambda: True
    correct = getattr(window, "correct_editor", None)
    if correct is not None:
        correct.layers[SKELETON].cancel_scheduled_render()


def in_correct(make_window, tmp_path, image: int = 0, project: Project | None = None):
    """A shown window on a new project with image ``image`` open in the Correct stage."""
    project = project or new_project(tmp_path)
    window = make_window()
    open_project(window, project)
    select(window, image)
    enter(window, "Correct")
    assert window.right_panel.currentIndex() == Panel.CORRECT
    return window, project


# ======================================================================= stages and pages
def test_the_topbar_has_six_stages_with_alt_1_to_alt_6(make_window):
    window = make_window(show=False)

    assert list(window.ribbon_buttons) == STAGE_NAMES
    assert [stage[0] for stage in shell_chrome.STAGES] == STAGE_NAMES
    assert shortcuts.STAGE_KEYS == ("Alt+1", "Alt+2", "Alt+3", "Alt+4", "Alt+5", "Alt+6")
    assert len(shortcuts.STAGE_KEYS) == len(shell_chrome.STAGES)
    bound = [s.key().toString() for s in window._global_shortcuts["go_to_stage"]]
    assert bound == list(shortcuts.STAGE_KEYS)
    assert window.ribbon_buttons["Correct"].toolTip() == (
        "Correct — Fix the mask (area) and the skeleton (length) of an image on one canvas. (Alt+4)"
    )
    assert window.welcome.stage_labels() == STAGE_NAMES  # six welcome chips


def test_the_correct_editor_is_page_one_of_the_four_right_panel_pages(make_window):
    window = make_window(show=False)

    assert window.right_panel.count() == 4
    assert window.right_panel.widget(Panel.DISPLAY) is not None
    assert window.right_panel.widget(Panel.CORRECT) is window.correct_editor
    assert window.right_panel.widget(Panel.GT_TRACING) is window.gt_tracing_interface
    assert window._editors() == {
        Panel.CORRECT: window.correct_editor,
        Panel.GT_TRACING: window.gt_tracing_interface,
    }


def test_alt_4_enters_correct_once_a_folder_is_open(make_window, tmp_path):
    window = make_window()
    alt_4 = window._global_shortcuts["go_to_stage"][3]
    alt_4.activated.emit()
    assert window.right_panel.currentIndex() == Panel.DISPLAY  # the Welcome screen: nothing

    open_project(window, new_project(tmp_path))
    select(window, 0)
    alt_4.activated.emit()
    settle()

    assert window.right_panel.currentIndex() == Panel.CORRECT
    assert window.ribbon_buttons["Correct"].isChecked()


def test_entering_correct_opens_the_selected_image_with_both_layers(make_window, tmp_path):
    window, project = in_correct(make_window, tmp_path, 0)
    editor = window.correct_editor

    assert window.right_panel.currentWidget() is editor
    assert same_path(editor.current_image_path, project.image(0))
    assert np.array_equal(plane(editor), box_plane())  # the model mask
    assert raster(editor)[110, 20 : W - 20].all()  # the generated skeleton
    assert editor.layers[SKELETON].document.source == "generated"
    page = window.action_bar_stack.currentWidget()
    assert editor.clear_button.parentWidget() is page and editor.clear_button.isVisible()
    assert editor.clear_button.text() == "Clear mask"

    enter(window, "Correct")  # clicking the open stage again never leaves it
    assert window.right_panel.currentWidget() is editor
    assert same_path(editor.current_image_path, project.image(0))


# ============================================================================ leaving
def test_leaving_correct_with_unsaved_edits_asks_once(make_window, tmp_path, monkeypatch):
    window, _project = in_correct(make_window, tmp_path, 0)
    editor = window.correct_editor
    paint(editor, (20, 100), (60, 100))
    assert window.isWindowModified()
    answers = answer_prompts(monkeypatch).will(DISCARD)

    enter(window, "Library")

    assert answers.seen == [("Unsaved mask", f"Save changes to the mask for {STEMS[0]}.jpg?")]
    assert window.right_panel.currentIndex() == Panel.DISPLAY
    assert window.ribbon_buttons["Library"].isChecked()
    assert not editor.has_unsaved_changes() and np.array_equal(plane(editor), box_plane())
    assert not window.isWindowModified()


def test_a_refused_leave_keeps_the_correct_stage_its_page_and_the_edits(
    make_window, tmp_path, monkeypatch
):
    window, project = in_correct(make_window, tmp_path, 0)
    editor = window.correct_editor
    paint(editor, (20, 100), (60, 100))
    answers = answer_prompts(monkeypatch).will(CANCEL)

    enter(window, "Measure")

    assert len(answers.seen) == 1
    assert window.right_panel.currentIndex() == Panel.CORRECT
    assert window.ribbon_buttons["Correct"].isChecked()
    assert window.action_bar_stack.currentIndex() == window._action_bar_pages["Correct"]
    assert editor.has_unsaved_changes() and window.isWindowModified()
    assert same_path(editor.current_image_path, project.image(0))


def test_trace_gt_and_correct_hand_over_with_one_prompt_each_way(
    make_window, tmp_path, monkeypatch
):
    window, project = in_correct(make_window, tmp_path, 0)
    editor, gt = window.correct_editor, window.gt_tracing_interface
    paint(editor, (20, 100), (60, 100))
    answers = answer_prompts(monkeypatch).will(SAVE)

    enter(window, "Trace GT")

    assert [title for title, _ in answers.seen] == ["Unsaved mask"]
    assert window.right_panel.currentWidget() is gt
    assert same_path(gt.current_image_path, project.image(0))
    saved = cv2.imread(project.mask(0), cv2.IMREAD_UNCHANGED)
    assert saved.shape == (H, W, 4) and saved[100, 40, 3] == 255  # the stroke was saved

    gt.begin_stroke(QPoint(10, 10))
    gt.continue_stroke(QPoint(30, 10))
    gt.end_stroke()
    answers.will(DISCARD)
    enter(window, "Correct")

    assert [title for title, _ in answers.seen] == ["Unsaved mask", "Unsaved GT"]
    assert window.right_panel.currentWidget() is editor
    assert same_path(editor.current_image_path, project.image(0))
    assert plane(editor)[100, 40] == 1 and not editor.has_unsaved_changes()


def test_open_folder_asks_once_then_closes_both_layers(make_window, tmp_path, monkeypatch):
    window, _project = in_correct(make_window, tmp_path, 0)
    editor = window.correct_editor
    paint(editor, (20, 100), (60, 100))
    draw_line(editor)
    loads = []
    monkeypatch.setattr(window.image_manager, "load_images", loads.append)
    answers = answer_prompts(monkeypatch).will(DISCARD)
    other = tmp_path / "other"
    other.mkdir()

    window.open_path(str(other))

    name = f"{STEMS[0]}.jpg"
    expected = ("Unsaved mask and skeleton", f"Save changes to the mask and skeleton for {name}?")
    assert answers.seen == [expected]
    assert loads == [str(other)]
    assert editor.current_image_path == ""
    assert not editor.layers[MASK].is_open() and not editor.layers[SKELETON].is_open()
    assert not window.isWindowModified()


def test_closing_the_window_asks_once_and_cancel_keeps_it_open(make_window, tmp_path, monkeypatch):
    window, project = in_correct(make_window, tmp_path, 0)
    editor = window.correct_editor
    paint(editor, (20, 100), (60, 100))
    draw_line(editor)
    answers = answer_prompts(monkeypatch).will(CANCEL, DISCARD)

    window.close()
    settle()
    assert window.isVisible() and editor.has_unsaved_changes()

    window.close()
    settle()
    assert not window.isVisible()
    assert [title for title, _ in answers.seen] == ["Unsaved mask and skeleton"] * 2
    assert not editor.has_unsaved_changes()  # Discard reverted both layers
    assert np.array_equal(plane(editor), box_plane())
    assert not os.path.exists(project.correction(0))


# ======================================================================= opening images
def test_a_tree_click_opens_the_image_in_correct_and_the_metrics_follow(
    make_window, tmp_path, monkeypatch
):
    window, project = in_correct(make_window, tmp_path, 0)
    names = []
    real = window.metrics_bar.set_metrics
    monkeypatch.setattr(
        window.metrics_bar, "set_metrics", lambda *a, **k: names.append(k["name"]) or real(*a, **k)
    )

    select(window, 2)

    assert same_path(window.correct_editor.current_image_path, project.image(2))
    assert window.right_panel.currentIndex() == Panel.CORRECT
    assert names and names[-1] == STEMS[2]


def test_arrow_keys_open_the_image_they_come_to_rest_on(make_window, tmp_path):
    window, project = in_correct(make_window, tmp_path, 0)
    editor = window.correct_editor
    activate(window)
    window.file_list.setFocus()

    QTest.keyClick(window.file_list, Qt.Key.Key_Down)

    assert window._tree_load_timer.isActive()  # the 40 ms debounce: nothing loaded yet
    assert same_path(editor.current_image_path, project.image(0))
    assert wait(lambda: same_path(editor.current_image_path, project.image(1)), 2.0)


@pytest.mark.parametrize("stage", ["Correct", "Trace GT"])
def test_arrow_keys_keep_browsing_the_tree_in_the_editors(make_window, tmp_path, stage):
    window, project = in_correct(make_window, tmp_path, 0)
    enter(window, stage)
    editor = window._editors()[window.right_panel.currentIndex()]
    activate(window)
    window.file_list.setFocus()

    for i in (1, 2):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Down)
        assert wait(lambda i=i: same_path(editor.current_image_path, project.image(i)), 2.0)
        assert window.file_list.hasFocus()  # the canvas did not take the arrow keys


def test_pgdn_and_pgup_on_the_canvas_save_then_open_the_next_and_previous_image(
    make_window, tmp_path
):
    window, project = in_correct(make_window, tmp_path, 1)
    editor = window.correct_editor
    activate(window)
    paint(editor, (20, 100), (60, 100))
    saved = record(window.mask_saved)
    view = editor.graphics_view
    view.setFocus()

    QTest.keyClick(view, Qt.Key.Key_PageDown)  # the guard fails on any prompt
    settle()

    assert len(saved) == 1 and same_path(saved[0], project.image(1))
    assert os.path.isfile(project.mask(1))
    assert same_path(editor.current_image_path, project.image(2))
    assert window.file_list.currentItem() is leaf(window, 2)

    QTest.keyClick(view, Qt.Key.Key_PageUp)
    settle()
    assert same_path(editor.current_image_path, project.image(1))
    assert window.file_list.currentItem() is leaf(window, 1)


def test_pgdn_in_the_library_while_correcting_saves_then_opens_the_next_image(
    make_window, tmp_path
):
    window, project = in_correct(make_window, tmp_path, 1)
    editor = window.correct_editor
    activate(window)
    paint(editor, (20, 100), (60, 100))
    window.file_list.setFocus()

    QTest.keyClick(window.file_list, Qt.Key.Key_PageDown)
    settle()

    assert os.path.isfile(project.mask(1)) and not editor.has_unsaved_changes()
    assert same_path(editor.current_image_path, project.image(2))
    assert window.file_list.currentItem() is leaf(window, 2)


def test_paging_steps_from_the_image_open_in_correct_not_the_tree_selection(make_window, tmp_path):
    window, project = in_correct(make_window, tmp_path, 0)
    window.file_list.setCurrentItem(leaf(window, 2))  # arrow keys moved on: nothing loaded
    window._tree_load_timer.stop()

    window.navigate_image(1)
    settle()

    assert same_path(window.correct_editor.current_image_path, project.image(1))
    assert window.file_list.currentItem() is leaf(window, 1)


def test_cancel_on_a_tree_click_keeps_the_open_image_selected(make_window, tmp_path, monkeypatch):
    window, project = in_correct(make_window, tmp_path, 0)
    editor = window.correct_editor
    paint(editor, (20, 100), (60, 100))
    answers = answer_prompts(monkeypatch).will(CANCEL)

    select(window, 2)

    assert len(answers.seen) == 1
    assert same_path(editor.current_image_path, project.image(0))
    assert editor.has_unsaved_changes()
    assert window.file_list.currentItem() is leaf(window, 0)


# ====================================================================== save reports
def test_the_editor_signals_are_connected_once_whatever_the_stage_visits(make_window, tmp_path):
    window = make_window()
    project = new_project(tmp_path)
    open_project(window, project)
    select(window, 0)
    editor = window.correct_editor
    for _ in range(3):
        enter(window, "Correct")
        enter(window, "Library")

    for signal in (
        editor.mask_saved,
        editor.mask_cleared,
        editor.b_key_status_changed,
        editor.navigate_requested,
        editor.notice,
        editor.dirty_changed,
    ):
        assert editor.receivers(signal) == 1
    assert editor.receivers(editor.skeleton_saved) == 0  # FR-03
    saved = record(window.mask_saved)
    editor.mask_saved.emit(project.image(1))
    assert saved == [project.image(1)]


def test_a_mask_save_badges_the_image_and_refreshes_the_metrics_without_stat_per_image(
    make_window, tmp_path, monkeypatch
):
    window, project = in_correct(make_window, tmp_path, 1)  # image 1 has no mask
    editor = window.correct_editor
    assert window.metrics_bar._status_value.text() == "No mask"
    paint(editor, (20, 100), (60, 100))
    toasts = record_toasts(window)
    stats: list[str] = []
    real_stat, real_exists = os.stat, os.path.exists

    def stat(path, *args, **kwargs):
        stats.append(os.fspath(path) if isinstance(path, (str, os.PathLike)) else "")
        return real_stat(path, *args, **kwargs)

    def exists(path):
        stats.append(os.fspath(path) if isinstance(path, (str, os.PathLike)) else "")
        return real_exists(path)

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os.path, "exists", exists)
    assert editor.save()
    settle()
    monkeypatch.setattr(os, "stat", real_stat)
    monkeypatch.setattr(os.path, "exists", real_exists)

    other_images = [p for p in stats if any(stem in p for stem in STEMS if stem != STEMS[1])]
    assert stats and other_images == []  # the spy saw the save; no other image was looked up
    assert window.image_manager.has_mask(STEMS[1])
    assert window.library_index.status_of(STEMS[1]) == (True, False)
    assert window.metrics_bar._status_value.text() == "Mask ready"
    assert toasts == [("success", f"Saved mask · {STEMS[1]}.jpg")]  # the editor's toast only
    assert not window.isWindowModified()


def test_a_tree_click_answered_save_reports_the_mask_once_and_badges_it(
    make_window, tmp_path, monkeypatch
):
    window, project = in_correct(make_window, tmp_path, 1)
    editor = window.correct_editor
    paint(editor, (20, 100), (60, 100))
    saved = record(window.mask_saved)
    answers = answer_prompts(monkeypatch).will(SAVE)

    select(window, 2)

    assert len(answers.seen) == 1
    assert len(saved) == 1 and same_path(saved[0], project.image(1))
    assert os.path.isfile(project.mask(1))
    assert window.library_index.status_of(STEMS[1])[0] is True
    assert same_path(editor.current_image_path, project.image(2))


def test_an_emptied_mask_saved_on_a_tree_click_clears_the_badge_once(
    make_window, tmp_path, monkeypatch
):
    window, project = in_correct(make_window, tmp_path, 0)  # image 0 has the model mask
    editor = window.correct_editor
    assert window.library_index.status_of(STEMS[0])[0] is True
    editor.clear_active_layer()
    cleared = record(window.mask_cleared)
    answers = answer_prompts(monkeypatch).will(SAVE)

    select(window, 1)

    assert len(answers.seen) == 1
    assert len(cleared) == 1 and same_path(cleared[0], project.image(0))
    assert not os.path.exists(project.mask(0))
    assert window.library_index.status_of(STEMS[0])[0] is False
    assert not window.image_manager.has_mask(STEMS[0])


def test_re_entering_correct_after_a_skeleton_save_shows_the_saved_correction(
    make_window, tmp_path
):
    project = new_project(tmp_path)
    # A generated skeleton at half size: the correction is saved at image resolution, so
    # re-entry shows exactly the raster that was in memory (Q1 fixed).
    write_skeleton(project.generated(0), row=55, w=W // 2, h=H // 2)
    window, _project = in_correct(make_window, tmp_path, 0, project)
    editor = window.correct_editor
    draw_line(editor)
    assert editor.save()
    in_memory = raster(editor).copy()

    enter(window, "Measure")
    enter(window, "Correct")

    saved = cv2.imread(project.correction(0), cv2.IMREAD_GRAYSCALE)
    fresh = SkeletonCorrectionModel()
    fresh.load_from_raster(saved, target_size=(W, H))
    assert np.array_equal(raster(editor), fresh.mask)
    assert np.array_equal(raster(editor), in_memory)
    assert editor.layers[SKELETON].document.source == "corrected"


def test_a_skeleton_save_changes_neither_has_skeleton_nor_the_metrics_bar(
    make_window, tmp_path, monkeypatch
):
    window, project = in_correct(make_window, tmp_path, 1)  # no generated skeleton
    editor = window.correct_editor
    before = window.metrics_bar._status_value.text()
    calls = []
    real = window.metrics_bar.set_metrics
    monkeypatch.setattr(
        window.metrics_bar, "set_metrics", lambda *a, **k: calls.append(k) or real(*a, **k)
    )

    draw_line(editor)
    assert editor.save()
    settle()

    assert os.path.isfile(project.correction(1))
    assert editor.receivers(editor.skeleton_saved) == 0
    assert not window.image_manager.has_skeleton(STEMS[1])
    assert window.library_index.status_of(STEMS[1]) == (False, False)
    assert calls == [] and window.metrics_bar._status_value.text() == before


# ============================================================================ the page
def test_the_generated_skeleton_resolver_reaches_the_skeleton_layer(make_window, tmp_path):
    project = new_project(tmp_path)
    run_output = tmp_path / "run_output"
    write_skeleton(str(run_output / f"{STEMS[1]}_fake.png"), row=40)
    window = make_window()
    open_project(window, project)
    window.image_manager.processed_base_path = str(run_output)  # the last generation run

    select(window, 1)
    enter(window, "Correct")

    editor = window.correct_editor
    assert raster(editor)[40, 20 : W - 20].all()
    assert editor.layers[SKELETON].document.source == "generated"


def test_the_opengl_switch_reaches_the_display_correct_and_trace_gt_canvases(
    make_window, monkeypatch
):
    window = make_window(show=False)
    views = [
        window.display_controller.magnifying_view,
        window.correct_editor.graphics_view,
        window.gt_tracing_interface.graphics_view,
    ]
    seen = []
    for view in views:
        monkeypatch.setattr(
            view, "set_opengl_viewport_enabled", lambda on, v=view: seen.append((v, on))
        )

    window.set_opengl_viewports_enabled(False)

    assert [view for view, _ in seen] == views and not any(on for _, on in seen)


def test_toasts_stay_clear_of_the_correct_dock(make_window, tmp_path):
    window, _project = in_correct(make_window, tmp_path, 0)
    window.resize(1000, 700)
    settle()
    dock = window.correct_editor.dock
    assert dock.isVisible()

    toast = window.toasts.show("Saved mask and skeleton · " + STEMS[0] + ".jpg", timeout=20_000)
    settle()

    dock_rect = QRect(dock.mapTo(window, QPoint(0, 0)), dock.size())
    toast_rect = toast.geometry()
    assert toast_rect.left() <= dock_rect.right() and toast_rect.right() >= dock_rect.left()
    assert toast_rect.bottom() < dock_rect.top()


def test_f1_opens_the_sheet_on_the_correct_keys_in_the_correct_stage(make_window, monkeypatch):
    from app.gui import main_window

    window = make_window()
    enter(window, "Correct")
    sections = []
    monkeypatch.setattr(
        main_window.ShortcutsDialog, "exec", lambda dialog: sections.append(dialog.section_titles())
    )

    window.show_shortcuts()

    assert sections == [["Correct", "Global", "Library", "Trace GT"]]


def test_holding_b_on_the_canvas_says_how_to_resize_the_brush(make_window, tmp_path):
    window, _project = in_correct(make_window, tmp_path, 0)
    view = window.correct_editor.graphics_view

    QTest.keyPress(view, Qt.Key.Key_B)
    held = window.status_bar.currentMessage()
    QTest.keyRelease(view, Qt.Key.Key_B)

    assert held.startswith("Scroll to resize the brush")
    assert window.status_bar.currentMessage() == ""


# ============================================================================ prewarm
def _flush_pending_prewarms() -> None:
    """Windows of earlier tests may still have a zero timer or a pool job pending."""
    settle()
    assert QThreadPool.globalInstance().waitForDone(10_000)


def test_scikit_image_is_prewarmed_once_in_the_thread_pool(make_window, monkeypatch):
    _flush_pending_prewarms()
    threads = []
    started = threading.Event()

    def spy() -> None:
        threads.append(threading.get_ident())
        started.set()

    monkeypatch.setattr(skeleton_graph_model, "prewarm_skeletonize", spy)

    window = make_window(show=False)
    # Nothing starts while the window is built: the import waits for the event loop
    # (main.py shows the window first), so it never competes with the start-up.
    assert not started.wait(0.3)

    assert wait(lambda: threads, 5.0)
    assert QThreadPool.globalInstance().waitForDone(5000)
    settle()
    assert len(threads) == 1
    assert threads[0] != threading.main_thread().ident
    assert window.isHidden()  # the prewarm does not need the window shown


def test_a_failing_prewarm_is_logged_not_raised(make_window, monkeypatch, caplog):
    _flush_pending_prewarms()
    hooked = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: hooked.append(exc))

    def fail() -> None:
        raise RuntimeError("scikit-image is broken")

    monkeypatch.setattr(skeleton_graph_model, "prewarm_skeletonize", fail)
    with caplog.at_level(logging.WARNING):
        make_window(show=False)
        assert wait(
            lambda: any("scikit-image is broken" in str(r.exc_info) for r in caplog.records), 5.0
        )
        assert QThreadPool.globalInstance().waitForDone(5000)
    assert hooked == []
