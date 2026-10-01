"""Stale temp files of killed writes are swept; GT writes use the shared atomic writer (R8).

``app.io_utils.atomic_open`` writes to a hidden ``.<name>.<8 hex>.tmp`` next to the
target and swaps it in. A process killed in between left that file behind for good
(the review's repro: ``.root_lengths.csv.78684f58.tmp``, 385 KB). Opening a project
now sweeps its output folders for such files older than an hour, off the GUI thread.

The GT store and registry had their own non-durable writers (``<path>.tmp``, no
fsync, no lock retry); they now go through ``app.io_utils`` with the saved bytes
unchanged, and their old ``<name>.tmp`` leftovers are swept from ``gt/``.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import threading
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, PngImagePlugin  # noqa: E402

from app import io_utils  # noqa: E402
from app.config import GT_BACKGROUND_ID, GT_META_CHUNK, GT_UNKNOWN_COLOR  # noqa: E402
from app.gt.registry import default_registry, save_registry  # noqa: E402
from app.gt.store import Tracing, empty_tracing, gt_path, read_tracing, write_tracing  # noqa: E402
from app.io_utils import FileLockedError, atomic_open  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402

HOUR = 3600.0
STEM = "24EFCAM1_T001_L045_2024.07.18_111618_001_SMM"


def sweep(folder, **kwargs):
    return io_utils.sweep_stale_temp_files(folder, **kwargs)


def _file(folder, name: str, *, age_s: float = 0.0) -> str:
    """Create ``folder/name`` whose mtime is ``age_s`` seconds in the past."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(str(folder), name)
    with open(path, "wb") as fh:
        fh.write(b"partial write")
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
    return path


def _sharing_violation(path) -> PermissionError:
    exc = PermissionError(errno.EACCES, "The process cannot access the file", str(path))
    exc.winerror = 32  # ERROR_SHARING_VIOLATION
    return exc


# ------------------------------------------------------------------ the sweeper


def test_a_stale_temp_file_is_removed_and_a_fresh_one_is_kept(tmp_path):
    stale = _file(tmp_path, ".root_lengths.csv.78684f58.tmp", age_s=2 * HOUR)
    fresh = _file(tmp_path, ".root_areas.csv.0badf00d.tmp", age_s=60)  # a write in progress
    target = _file(tmp_path, "root_lengths.csv", age_s=2 * HOUR)

    assert sweep(tmp_path) == [stale]
    assert sorted(os.listdir(tmp_path)) == sorted(map(os.path.basename, (fresh, target)))


def test_the_age_threshold_can_be_chosen(tmp_path):
    older = _file(tmp_path, ".a.png.00000001.tmp", age_s=90)
    younger = _file(tmp_path, ".b.png.00000002.tmp", age_s=30)

    assert sweep(str(tmp_path), older_than_s=60) == [older]
    assert os.path.exists(younger)


@pytest.mark.parametrize(
    "name",
    [
        "notes.tmp",  # not hidden
        ".hidden.txt",  # not .tmp
        "x.png.1a2b3c4d.tmp",  # no leading dot
        ".x.ABCDEF12.tmp",  # upper-case hex: token_hex never makes it
        ".x.1a2b3c4.tmp",  # 7 hex digits
        ".x.1a2b3c4d5.tmp",  # 9 hex digits
        ".x.1a2b3c4g.tmp",  # not hex
        ".1a2b3c4d.tmp",  # no target name
        ".x.png.1a2b3c4d.tmp.bak",
        "classes.json.tmp",  # a legacy GT name: swept in gt/ only
        f"{STEM}.png.tmp",
    ],
)
def test_names_that_are_not_atomic_temp_files_are_kept(tmp_path, name):
    path = _file(tmp_path, name, age_s=2 * HOUR)
    assert sweep(tmp_path) == []
    assert os.path.exists(path)


def test_a_folder_named_like_a_temp_file_is_kept(tmp_path, caplog):
    folder = tmp_path / ".mask.png.1a2b3c4d.tmp"
    folder.mkdir()
    stamp = time.time() - 2 * HOUR
    os.utime(folder, (stamp, stamp))
    with caplog.at_level(logging.WARNING, logger=io_utils.__name__):
        assert sweep(tmp_path) == []
    assert folder.is_dir()
    assert caplog.records == []  # skipped, not a failed delete on every project load


def test_the_sweep_never_recurses(tmp_path):
    nested = _file(tmp_path / "sub", ".a.png.1a2b3c4d.tmp", age_s=2 * HOUR)
    assert sweep(tmp_path) == []
    assert os.path.exists(nested)


def test_a_missing_folder_or_a_file_gives_an_empty_list(tmp_path):
    assert sweep(tmp_path / "missing") == []
    assert sweep(_file(tmp_path, "a.png")) == []


def test_an_unreadable_folder_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    def denied(path):
        raise PermissionError(errno.EACCES, "Access is denied", str(path))

    monkeypatch.setattr(io_utils.os, "scandir", denied)
    with caplog.at_level(logging.WARNING, logger=io_utils.__name__):
        assert sweep(tmp_path) == []
    assert str(tmp_path) in caplog.text


def test_a_file_that_cannot_be_deleted_is_logged_and_skipped(tmp_path, monkeypatch, caplog):
    held = _file(tmp_path, ".a.png.1a2b3c4d.tmp", age_s=2 * HOUR)
    other = _file(tmp_path, ".b.png.1a2b3c4e.tmp", age_s=2 * HOUR)
    real_remove = os.remove

    def remove(path):
        if os.path.basename(path) == os.path.basename(held):
            raise PermissionError(errno.EACCES, "The process cannot access the file", path)
        real_remove(path)

    monkeypatch.setattr(io_utils.os, "remove", remove)
    with caplog.at_level(logging.WARNING, logger=io_utils.__name__):
        assert sweep(tmp_path) == [other]
    assert os.path.exists(held) and not os.path.exists(other)
    assert os.path.basename(held) in caplog.text


@pytest.mark.parametrize(
    "target", ["root_lengths.csv", f"{STEM}_fake.png", ".hidden", "Données_根.png", "x.tmp"]
)
def test_what_a_killed_atomic_write_leaves_is_swept(tmp_path, monkeypatch, target):
    # Keep atomic_open from cleaning up, as a killed process would.
    monkeypatch.setattr(io_utils, "_remove_quietly", lambda path: None)
    with pytest.raises(RuntimeError), atomic_open(tmp_path / target, "wb") as fh:
        fh.write(b"half a file")
        raise RuntimeError("killed mid-write")
    (leftover,) = os.listdir(tmp_path)
    stamp = time.time() - 2 * HOUR
    os.utime(tmp_path / leftover, (stamp, stamp))

    assert sweep(tmp_path) == [os.path.join(str(tmp_path), leftover)]
    assert os.listdir(tmp_path) == []


# ------------------------------------------------------------------ project folders


def test_output_dirs_are_the_folders_the_app_writes_into(tmp_path):
    root = str(tmp_path)
    join = os.path.join
    assert ProjectLayout(root).output_dirs == (
        join(root, "output", "mask"),
        join(root, "mask"),
        join(root, "skeletons"),
        join(root, "output", "skeletonizer", "test_latest", "images"),
        join(root, "skeletonizer", "test_latest", "images"),
        join(root, "skeleton_corrections"),
        join(root, "gt"),
    )
    assert os.listdir(tmp_path) == []  # describing the folders creates none


@pytest.mark.parametrize(
    ("mask_parts", "skeleton_parts"),
    [
        (("mask",), ("skeletons",)),
        (("output", "mask"), ("output", "skeletonizer", "test_latest", "images")),
        (("output", "mask"), ("skeletonizer", "test_latest", "images")),
    ],
)
def test_output_dirs_hold_the_folders_measure_writes_its_csvs_into(
    tmp_path, mask_parts, skeleton_parts
):
    # Measure writes root_areas.csv / root_lengths.csv (+ .provenance.json) into the
    # folder it measured: skeleton_handler's resolve_masks_dir / resolve_skeletons_dir.
    layout = ProjectLayout(str(tmp_path))
    os.makedirs(os.path.join(str(tmp_path), *mask_parts))
    os.makedirs(os.path.join(str(tmp_path), *skeleton_parts))
    assert layout.resolve_masks_dir() in layout.output_dirs
    assert layout.resolve_skeletons_dir(search_test_latest=False) in layout.output_dirs


@pytest.mark.parametrize(
    ("name", "legacy"),
    [
        ("classes.json.tmp", True),
        (f"{STEM}.png.tmp", True),
        ("a.b.png.tmp", True),
        ("notes.tmp", False),
        ("classes.json", False),
        (f"{STEM}.png", False),
        ("a.jpg.tmp", False),
        ("other.json.tmp", False),
        (f"{STEM}.png.tmp.bak", False),
    ],
)
def test_legacy_gt_temp_names(name, legacy):
    # gt/store.py and gt/registry.py wrote "<path>.tmp" before R8.
    assert ProjectLayout.is_legacy_gt_temp(name) is legacy


def _stale_project(root):
    """A project with stale and fresh temp files in every output folder."""
    layout = ProjectLayout(str(root))
    stale, kept = set(), set()
    for folder in layout.output_dirs:
        stale.add(_file(folder, ".x.png.1a2b3c4d.tmp", age_s=2 * HOUR))
        kept.add(_file(folder, ".y.png.1a2b3c4e.tmp", age_s=60))
        kept.add(_file(folder, "notes.tmp", age_s=2 * HOUR))
    stale.add(_file(layout.gt_dir, "classes.json.tmp", age_s=2 * HOUR))
    stale.add(_file(layout.gt_dir, f"{STEM}.png.tmp", age_s=2 * HOUR))
    kept.add(_file(layout.gt_dir, "a.png.tmp", age_s=60))  # legacy but fresh
    kept.add(_file(layout.mask_dir, f"{STEM}.png.tmp", age_s=2 * HOUR))  # legacy: gt/ only
    kept.add(_file(layout.corrections_dir, "classes.json.tmp", age_s=2 * HOUR))
    return layout, stale, kept


def test_the_project_sweep_covers_every_output_folder(tmp_path):
    from app.gui.image_manager import sweep_project_temp_files

    layout, stale, kept = _stale_project(tmp_path)

    assert set(sweep_project_temp_files(layout)) == stale
    assert not any(os.path.exists(p) for p in stale)
    assert all(os.path.exists(p) for p in kept)


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_a_loaded_project_is_swept_on_a_daemon_thread_after_its_scan(app, tmp_path, monkeypatch):
    import cv2

    from app.gui import image_manager as im

    layout, stale, kept = _stale_project(tmp_path)
    cv2.imwrite(os.path.join(layout.root, f"{STEM}.png"), np.zeros((8, 8, 3), np.uint8))
    release, calls, scheduled = threading.Event(), [], []
    real_sweep = im.sweep_project_temp_files

    def held_sweep(swept_layout):
        if swept_layout != layout:  # a delayed sweep scheduled by an earlier test's window
            return real_sweep(swept_layout)
        calls.append((swept_layout, threading.current_thread()))
        release.wait(10)
        return real_sweep(swept_layout)

    class _Timer:  # records the deferred start instead of waiting for it
        @staticmethod
        def singleShot(ms, fn):  # noqa: N802 - Qt API name
            scheduled.append((ms, fn))

    monkeypatch.setattr(im, "sweep_project_temp_files", held_sweep)
    monkeypatch.setattr(im, "QTimer", _Timer)
    manager = im.ImageManager(None)
    started = time.monotonic()
    manager.load_images(layout.root)
    returned_after = time.monotonic() - started
    try:
        # Nothing sweeps while the library scans the same folders: the start is
        # scheduled once the scan has delivered, TEMP_SWEEP_DELAY_MS later.
        deadline = time.monotonic() + 10
        while not scheduled and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert manager.original_folder == layout.root
        ((delay, start),) = scheduled
        assert delay == im.TEMP_SWEEP_DELAY_MS > 0
        assert calls == []
        start()
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        stale_while_held = [os.path.exists(p) for p in stale]
    finally:
        release.set()
        # The manager lets a scan's thread go once it has ended.
        if manager.loader_thread is not None:
            manager.loader_thread.wait(5000)

    assert returned_after < 2.0  # load_images never waits for the sweep
    ((swept_layout, thread),) = calls
    assert swept_layout == layout
    assert thread is not threading.main_thread() and thread.daemon
    assert all(stale_while_held)
    thread.join(5)
    assert not any(os.path.exists(p) for p in stale)
    assert all(os.path.exists(p) for p in kept)


# ------------------------------------------------------------------ GT writers


def _hex_to_rgb(color):
    return [int(color[i : i + 2], 16) for i in (1, 3, 5)]


def _pre_r8_write_tracing(path, tracing, registry):
    """``app.gt.store.write_tracing`` before R8, verbatim: the golden for the saved bytes."""
    ids = np.ascontiguousarray(tracing.ids, dtype=np.uint8)
    palette = [0, 0, 0] * 256
    unknown = _hex_to_rgb(GT_UNKNOWN_COLOR)
    for i in np.unique(ids).tolist():
        if i != GT_BACKGROUND_ID:
            palette[3 * i : 3 * i + 3] = unknown
    for c in registry.classes:
        palette[3 * c.id : 3 * c.id + 3] = _hex_to_rgb(c.color)
    image = Image.fromarray(ids, mode="P")
    image.putpalette(palette)
    meta = {
        "v": 1,
        "classes": {str(c.id): c.key for c in registry.classes},
        "reviewed": sorted(tracing.reviewed),
    }
    info = PngImagePlugin.PngInfo()
    info.add_text(GT_META_CHUNK, json.dumps(meta))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        image.save(fh, format="PNG", pnginfo=info, transparency=GT_BACKGROUND_ID)
    os.replace(tmp, path)


def _pre_r8_save_registry(project_dir, registry):
    """``app.gt.registry.save_registry`` before R8, verbatim (text mode: CRLF on Windows)."""
    path = os.path.join(project_dir, "gt", "classes.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(json.dumps(registry.to_dict(), indent=2))
    os.replace(path + ".tmp", path)
    return path


def _tracings():
    cycle = np.resize(np.array([0, 1, 2, 3, 255], np.uint8), (30, 40))
    unknown = np.zeros((12, 9), np.uint8)
    unknown[2:5, 3:7] = 7  # an id no class owns: GT_UNKNOWN_COLOR
    return {
        "classes": Tracing(cycle, frozenset({"root", "nodule"})),
        "empty": empty_tracing(6, 7),
        "unknown_id": Tracing(unknown, frozenset({"root_tip"})),
    }


@pytest.mark.parametrize("kind", sorted(_tracings()))
def test_write_tracing_bytes_are_unchanged(tmp_path, kind):
    tracing = _tracings()[kind]
    registry = default_registry().add(key="dead_root", name="Dead root", color="#9aa0a6")
    old, new = str(tmp_path / "old" / "a.png"), str(tmp_path / "new" / "gt" / "a.png")
    _pre_r8_write_tracing(old, tracing, registry)
    write_tracing(new, tracing, registry)
    with open(old, "rb") as a, open(new, "rb") as b:
        assert b.read() == a.read()
    assert os.listdir(os.path.dirname(new)) == ["a.png"]


def test_save_registry_bytes_are_unchanged(tmp_path):
    registry = default_registry().add(key="dead_root", name="Dead root", color="#9aa0a6")
    old = _pre_r8_save_registry(str(tmp_path / "old"), registry)
    new = save_registry(str(tmp_path / "new"), registry)
    with open(old, "rb") as a, open(new, "rb") as b:
        assert b.read() == a.read()
    assert os.listdir(os.path.dirname(new)) == ["classes.json"]


def test_gt_saves_are_durable(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(io_utils.os, "fsync", lambda fd: synced.append(fd))
    write_tracing(str(tmp_path / "gt" / "a.png"), empty_tracing(4, 4), default_registry())
    assert len(synced) == 1
    save_registry(str(tmp_path), default_registry())
    assert len(synced) == 2


def test_gt_writes_use_the_shared_temp_name(tmp_path, monkeypatch):
    swapped = []
    real_replace = os.replace

    def spy(src, dst):
        swapped.append((os.path.basename(src), os.path.basename(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(io_utils.os, "replace", spy)
    write_tracing(gt_path(str(tmp_path / f"{STEM}.jpg")), empty_tracing(4, 4), default_registry())
    save_registry(str(tmp_path), default_registry())

    assert [dst for _, dst in swapped] == [f"{STEM}.png", "classes.json"]
    for src, dst in swapped:  # ".<name>.<8 hex>.tmp", never "<name>.tmp"
        assert re.fullmatch(rf"\.{re.escape(dst)}\.[0-9a-f]{{8}}\.tmp", src), src
    assert sorted(os.listdir(tmp_path / "gt")) == sorted([f"{STEM}.png", "classes.json"])


@pytest.mark.parametrize("which", ["tracing", "registry"])
def test_a_gt_file_held_open_raises_file_locked_error(tmp_path, monkeypatch, which):
    registry = default_registry()
    path = str(tmp_path / "gt" / ("a.png" if which == "tracing" else "classes.json"))

    def save(ids):
        if which == "tracing":
            write_tracing(path, Tracing(ids), registry)
        else:
            save_registry(str(tmp_path), registry.add(key="k", name="K", color="#010203"))

    save(np.zeros((4, 4), np.uint8))
    with open(path, "rb") as fh:
        before = fh.read()
    attempts = []

    def locked(src, dst):
        attempts.append(dst)
        raise _sharing_violation(dst)

    monkeypatch.setattr(io_utils.os, "replace", locked)
    monkeypatch.setattr(io_utils.time, "sleep", lambda s: None)
    with pytest.raises(FileLockedError, match="open in another program"):
        save(np.ones((4, 4), np.uint8))
    assert len(attempts) == len(io_utils.REPLACE_RETRY_DELAYS) + 1  # the lock retry
    with open(path, "rb") as fh:
        assert fh.read() == before
    assert os.listdir(tmp_path / "gt") == [os.path.basename(path)]


def test_trace_gt_reports_a_locked_gt_file(app, tmp_path, monkeypatch):
    import cv2
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QMessageBox

    from app.gui.gt_tracing_interface import GtTracingInterface

    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("unexpected modal dialog"))
    image = str(tmp_path / f"{STEM}.png")
    cv2.imwrite(image, np.full((30, 40, 3), 90, np.uint8))
    notices = []
    editor = GtTracingInterface()
    editor.resize(900, 700)
    editor.notice.connect(lambda msg, kind: notices.append((msg, kind)))
    editor.load_image(image)
    editor.begin_stroke(QPoint(5, 5))
    editor.continue_stroke(QPoint(20, 5))
    editor.end_stroke()

    def locked(src, dst):
        raise _sharing_violation(dst)

    monkeypatch.setattr(io_utils.os, "replace", locked)
    monkeypatch.setattr(io_utils.time, "sleep", lambda s: None)
    notices.clear()

    assert editor.save_mask() is False  # reported, not raised
    assert any(kind == "danger" and "open in another program" in msg for msg, kind in notices)
    assert editor.has_unsaved_changes()
    assert read_tracing(gt_path(image)) is None
    editor.deleteLater()
