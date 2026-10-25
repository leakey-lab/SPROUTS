"""MainWindow library behaviour: loading, outputs refresh, mask status, navigation (library package).

Offscreen; every project is a tmp_path folder of tiny synthetic JPGs.
"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QThread  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tests.qt_teardown import flush_deleted  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

USER = Qt.ItemDataRole.UserRole
STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13, 14, 15)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    """A loaded project schedules its stale-temp sweep 2 s later (R8) on an untracked timer;
    here it is a no-op, so a window of this test never sweeps inside a later test."""
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


@pytest.fixture
def mw(app, monkeypatch):
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_record_recent_project", lambda self, count: None)
    w = MainWindow()
    monkeypatch.setattr(w.correct_editor, "maybe_save_changes", lambda: None)
    toasts = []
    monkeypatch.setattr(w, "notify", lambda msg, kind="success", **_k: toasts.append((kind, msg)))
    w.toasts_seen = toasts
    yield w
    w.close()
    w.deleteLater()
    del w
    flush_deleted()


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


def _open(mw, folder):
    mw.open_path(str(folder))
    assert _wait(lambda: mw.image_manager.images and mw.library_index.leaves())
    return folder


def _select(mw, stem):
    leaf = mw.find_tree_item_by_image_name(stem)
    mw.file_list.setCurrentItem(leaf)
    mw.on_image_selected(leaf)
    return leaf


# ------------------------------------------------------------------ PG-01 outputs
def test_load_results_accepts_the_skeletons_folder(mw, tmp_path, monkeypatch):
    folder = _open(mw, _project(tmp_path / "p"))
    _select(mw, STEMS[0])
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a))
    skel = folder / "skeletons"
    skel.mkdir()
    QImage(8, 8, QImage.Format.Format_Grayscale8).save(str(skel / f"{STEMS[0]}_fake.png"))

    mw.load_results(str(skel))

    assert errors == []
    assert os.path.normcase(mw.image_manager.processed_base_path) == os.path.normcase(str(skel))
    assert mw.image_manager.has_skeleton(STEMS[0])
    assert mw.metrics_bar._status_value.text() == "Skeleton ready"


def test_load_results_keeps_the_legacy_images_layout(mw, tmp_path, monkeypatch):
    _open(mw, _project(tmp_path / "p"))
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a))
    legacy = tmp_path / "p" / "out" / "images"
    legacy.mkdir(parents=True)
    mw.load_results(str(legacy.parent))
    assert errors == []


def test_load_results_reports_a_missing_folder(mw, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a))
    mw.load_results(str(tmp_path / "nope"))
    assert len(errors) == 1


def test_on_outputs_changed_picks_up_generated_masks(mw, tmp_path):
    folder = _open(mw, _project(tmp_path / "p"))
    _select(mw, STEMS[1])
    assert mw.metrics_bar._status_value.text() == "No mask"
    (folder / "mask").mkdir()
    QImage(8, 8, QImage.Format.Format_ARGB32).save(str(folder / "mask" / f"{STEMS[1]}.png"))
    mw.on_outputs_changed()
    assert mw.image_manager.has_mask(STEMS[1])
    assert mw.metrics_bar._status_value.text() == "Mask ready"


# ------------------------------------------------------------------ PG-09 / PG-08
def test_saved_mask_counts_and_survives_stage_switches_without_stats(mw, tmp_path, monkeypatch):
    folder = _open(mw, _project(tmp_path / "p"))
    _select(mw, STEMS[2])
    image_path = str(folder / f"{STEMS[2]}.jpg")
    mw.toggle_correct()
    mw.on_mask_saved(image_path)  # an editor save is GT (WG-T2), never a prediction
    assert mw.image_manager.has_gt_mask(STEMS[2]) and not mw.mask_exists(STEMS[2])

    calls = []
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: calls.append(p) or real_exists(p))
    mw.toggle_correct()  # leave
    mw.toggle_correct()  # enter again
    mw.toggle_correct()  # leave
    monkeypatch.setattr(os.path, "exists", real_exists)
    # Only the editor looks up the open image's own mask: no per-leaf status stats.
    mask_stats = [p for p in calls if os.path.basename(os.path.dirname(str(p))) == "mask"]
    assert all(STEMS[2] in str(p) for p in mask_stats)
    assert mw.image_manager.has_gt_mask(STEMS[2])

    mw.on_mask_cleared(image_path)
    assert not mw.image_manager.has_gt_mask(STEMS[2])


# ------------------------------------------------------------------ PG-03
def test_view_mode_switch_keeps_tree_selection_and_filter(mw, tmp_path, monkeypatch):
    import app.gui.file_tree_manager as ftm

    _open(mw, _project(tmp_path / "p"))
    leaf = _select(mw, STEMS[3])
    mw.file_list.expandAll()
    calls = []
    monkeypatch.setattr(ftm, "populate_file_list", lambda m: calls.append(m))
    for i in (1, 2, 0):
        mw.view_mode_combo.setCurrentIndex(i)
    assert calls == []
    assert mw.file_list.currentItem() is leaf
    assert leaf.parent().isExpanded()


# ------------------------------------------------------------------ PG-10
def test_down_arrow_in_library_shows_next_image(mw, tmp_path):
    _open(mw, _project(tmp_path / "p"))
    mw.show()
    mw.file_list.expandAll()
    _select(mw, STEMS[0])
    mw.file_list.setFocus()
    QTest.keyClick(mw.file_list, Qt.Key.Key_Down)
    QTest.keyClick(mw.file_list, Qt.Key.Key_Down)
    assert _wait(lambda: mw.display_controller.current_name == STEMS[2], 2.0)


def test_pgdn_in_correct_opens_next_image(mw, tmp_path, monkeypatch):
    folder = _open(mw, _project(tmp_path / "p"))
    loaded = []
    monkeypatch.setattr(
        mw.correct_editor, "load_image", lambda path, images_base_folder=None: loaded.append(path)
    )
    _select(mw, STEMS[0])
    mw.toggle_correct()
    mw.show()
    mw.activateWindow()
    assert QTest.qWaitForWindowActive(mw, 3000)
    view = mw.correct_editor.graphics_view
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    assert loaded[-1] == str(folder / f"{STEMS[1]}.jpg")
    assert mw.file_list.currentItem().data(0, USER) == STEMS[1]
    QTest.keyClick(mw.file_list, Qt.Key.Key_PageUp)
    assert mw.file_list.currentItem().data(0, USER) == STEMS[0]


# ------------------------------------------------------------------ PG-11 / UXS-05
def test_missing_folder_keeps_the_library(mw, tmp_path, monkeypatch):
    _open(mw, _project(tmp_path / "p"))
    loads = []
    monkeypatch.setattr(mw.image_manager, "load_images", loads.append)
    mw.open_path(str(tmp_path / "moved"))
    assert loads == []
    assert len(mw._image_leaves()) == len(STEMS)
    assert mw.toasts_seen[-1][0] == "danger" and "not found" in mw.toasts_seen[-1][1]


def test_image_less_folder_warns_and_keeps_the_library(mw, tmp_path):
    _open(mw, _project(tmp_path / "p"))
    empty = tmp_path / "empty"
    empty.mkdir()
    mw.open_path(str(empty))
    assert _wait(lambda: mw.toasts_seen and mw.toasts_seen[-1][0] == "warn")
    assert len(mw._image_leaves()) == len(STEMS)
    assert os.path.normcase(mw.image_manager.original_folder) == os.path.normcase(
        str(tmp_path / "p")
    )
    assert not any(kind == "success" and "0 image" in msg for kind, msg in mw.toasts_seen)


def test_image_less_first_folder_returns_to_welcome(mw, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    mw.open_path(str(empty))
    assert _wait(lambda: mw.toasts_seen and mw.toasts_seen[-1][0] == "warn")
    assert mw.app_stack.currentIndex() == 0


def test_load_success_is_one_formatted_message(mw, tmp_path):
    _open(mw, _project(tmp_path / "p"))
    assert [m for k, m in mw.toasts_seen if k == "success"] == ["Loaded 5 images"]


# ------------------------------------------------------------------ PG-15
def test_reopening_never_terminates_and_drops_the_stale_scan(mw, tmp_path, monkeypatch):
    a = _project(tmp_path / "a", STEMS[:2])
    b = _project(tmp_path / "b", STEMS[2:])
    monkeypatch.setattr(QThread, "terminate", lambda self: pytest.fail("terminate() called"))
    finished = []
    real = mw.on_loading_finished
    monkeypatch.setattr(mw, "on_loading_finished", lambda *a: finished.append(1) or real(*a))
    mw.image_manager.load_images(str(a))
    mw.image_manager.load_images(str(b))
    assert _wait(lambda: finished)
    QTest.qWait(150)
    assert finished == [1]
    assert sorted(mw.image_manager.images) == sorted(STEMS[2:])


def test_scan_failure_is_reported_as_a_failure(app, tmp_path):
    from app.gui.image_manager import ImageLoaderThread

    thread = ImageLoaderThread(str(tmp_path / "gone"))
    got = {"finished": [], "failed": []}
    thread.finished.connect(lambda *a: got["finished"].append(a))
    thread.failed.connect(got["failed"].append)
    thread.run()  # synchronously
    assert got["finished"] == [] and len(got["failed"]) == 1


# ------------------------------------------------------------------ AR-07 / RB-07
def test_window_exposes_job_registry_and_toggles_every_view(mw, monkeypatch):
    from app.handlers.job_registry import JobRegistry

    assert isinstance(mw.jobs, JobRegistry)
    seen = []
    views = [
        mw.display_controller.magnifying_view,
        mw.correct_editor.graphics_view,
        mw.gt_tracing_interface.graphics_view,
    ]
    for v in views:
        monkeypatch.setattr(v, "set_opengl_viewport_enabled", lambda e, v=v: seen.append((v, e)))
    mw.set_opengl_viewports_enabled(False)
    assert [v for v, _ in seen] == views and all(e is False for _, e in seen)


# ------------------------------------------------------------------ PI-10
def test_skeleton_tooltips_do_not_claim_masks_are_the_input(mw):
    # generate_button's tooltip is re-set by shell_chrome (see notes/library.md).
    tip = mw.generate_all_button.toolTip().lower()
    assert "from the masks" not in tip and "original images" in tip


# ------------------------------------------------------------------ R7 / O15
def test_open_path_is_refused_while_a_job_runs(mw, tmp_path, monkeypatch):
    _open(mw, _project(tmp_path / "a"))
    # The busy registry is undone before the fixture closes the window: closing with a
    # job running asks, in a modal box, whether to stop it.
    with monkeypatch.context() as patch:
        patch.setattr(mw.jobs, "is_busy", lambda: True)
        patch.setattr(mw.jobs, "running_name", lambda: "Generating masks")
        mw.task_progress.start("Generating masks", on_cancel=lambda: None)
        mw.open_path(str(_project(tmp_path / "b", STEMS[:2])))
        QTest.qWait(50)
    assert (
        "warn",
        "Generating masks is running. Cancel it or wait before opening another folder.",
    ) in mw.toasts_seen
    assert os.path.normcase(mw.image_manager.original_folder) == os.path.normcase(
        str(tmp_path / "a")
    )
    assert mw.task_progress._operation == "Generating masks"
    assert not mw.task_progress.cancel_button.isHidden()


def test_the_folder_dialog_is_not_opened_while_a_job_runs(mw, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    with monkeypatch.context() as patch:  # undone before the window closes (as above)
        patch.setattr(mw.jobs, "is_busy", lambda: True)
        patch.setattr(mw.jobs, "running_name", lambda: "Generating masks")
        patch.setattr(
            QFileDialog,
            "getExistingDirectory",
            lambda *a, **k: pytest.fail("the folder dialog opened during a job"),
        )
        mw.load_images()
    assert mw.toasts_seen[-1][0] == "warn"
