"""ClassesDocument: the class labels of one image, without Qt (WG-T4).

The document is the Trace GT editor's storage half (``GtTracingInterface``) with the Qt
removed: the class-id plane and its patch history, the "checked" ticks, the pixel count of
every id kept from each patch, ids read back from the canvas by exact colour match, Save
(``gt/classes.json`` first when it is missing, then ``gt/<stem>.png``, always written) and
the open of a remapped, unreadable or read-only file. It never reads ``mask/`` or
``skeletons/``.

Ported here (the old files were deleted in WG-T11): ``tests/test_gt_editor_save_order.py`` (the
registry first) and ``tests/test_gt_editor_speed_paths.py`` (``ids_from_argb``'s fallback,
the counts from patches, the exact histogram, Save's checks).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.gt import (
    Tracing,
    default_registry,
    present_keys,
    read_tracing,
    registry_path,
    save_registry,
    write_tracing,
)
from app.gui.correct import classes_document, classes_paint
from app.gui.correct.classes_document import ClassesDocument, ClassesRead, ClassesSaveResult
from app.gui.correct.classes_paint import canvas_ids, class_lut
from app.gui.correct.layer import LayerStatus
from app.io_utils import imwrite_unicode
from app.project_layout import ProjectLayout
from tests.gt_goldens_support import GT_STEM, make_gt_project

ROOT = Path(__file__).resolve().parents[1]
W, H = 640, 480
LABELS = f"gt/{GT_STEM}.png"


# ------------------------------------------------------------------------------ fixtures
def _project(tmp_path, case: str) -> SimpleNamespace:
    image, folder = make_gt_project(str(tmp_path), case)
    layout = ProjectLayout(folder)
    return SimpleNamespace(image=image, folder=folder, labels=layout.gt_path(GT_STEM))


@pytest.fixture
def gt_project(tmp_path):
    """A 640 x 480 photo, nothing under ``gt/``."""
    return _project(tmp_path, "two_classes_crossing")


@pytest.fixture
def gt_project_lossy(tmp_path):
    """``gt/<stem>.png`` whose id 2 has a key the registry does not know (one warning)."""
    return _project(tmp_path, "lossy_remap_on_load")


@pytest.fixture
def gt_project_bad_registry(tmp_path):
    """An invalid ``gt/classes.json``."""
    project = _project(tmp_path, "empty_save")
    os.makedirs(os.path.join(project.folder, "gt"))
    with open(registry_path(project.folder), "w", encoding="utf-8") as fh:
        fh.write("{")
    return project


@pytest.fixture
def recorder(monkeypatch):
    """The order the registry and the labels file are written in (the real writes run)."""
    order: list[str] = []
    store = classes_document.gt  # the document writes through app.gt
    real_registry, real_tracing = store.save_registry, store.write_tracing

    def registry(project_dir, registry):
        order.append("classes.json")
        return real_registry(project_dir, registry)

    def tracing(path, tracing, registry):
        order.append(os.path.basename(path))
        return real_tracing(path, tracing, registry)

    monkeypatch.setattr(store, "save_registry", registry)
    monkeypatch.setattr(store, "write_tracing", tracing)
    return SimpleNamespace(order=order)


# ------------------------------------------------------------------------------ helpers
def opened(project, size=(W, H)) -> ClassesDocument:
    doc = ClassesDocument()
    doc.open(project.image, size)
    return doc


def commit(doc: ClassesDocument, rect, value) -> None:
    """One action as the layer records it: a patch, its counts, a new revision."""
    x, y, w, h = rect
    after = np.full((h, w), value, np.uint8) if np.isscalar(value) else value
    patch = doc.history.commit(rect, after)
    doc.count_patch(patch, True)
    doc.touch()


def undo(doc: ClassesDocument) -> None:
    patch = doc.history.undo()
    if patch is not None:
        doc.count_patch(patch, False)
        doc.touch()


def redo(doc: ClassesDocument) -> None:
    patch = doc.history.redo()
    if patch is not None:
        doc.count_patch(patch, True)
        doc.touch()


def expected_counts(doc: ClassesDocument) -> dict[str, int]:
    """``doc.counts()`` computed from scratch: pixels per id, named by the registry."""
    names = {c.id: c.key for c in doc.registry.classes}
    names[0] = "background"
    counts = np.bincount(doc.history.plane.ravel(), minlength=256)
    return {names.get(i, str(i)): int(n) for i, n in enumerate(counts) if n}


def file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------------------ layering
#: What a class-label module must never load: Qt, torch, and the mask's storage (Design 3.7:
#: one layer, one file; the document reaches ``gt/<stem>.png`` through ``app.gt`` only).
_PROBE = (
    "import importlib, sys; importlib.import_module(sys.argv[1]); "
    "mask = ('app.masks', 'app.gui.correct.mask_document', 'app.gui.correct.mask_files'); "
    "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PyQt6', 'torch') "
    "or m in mask or m.startswith('app.masks.')))"
)


@pytest.mark.parametrize(
    "module",
    [
        "app.gui.correct.classes_document",
        "app.gui.correct.classes_files",
        "app.gui.correct.classes_paint",
    ],
)
def test_the_document_is_qt_free(module):
    """Qt-free, and it never imports the mask's storage (a static guard: a module that
    imported ``app.masks`` would fail here even if no test ever reached the call)."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, module],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_document_leaves_room_to_grow():
    lines = (ROOT / "app" / "gui" / "correct" / "classes_document.py").read_text(encoding="utf-8")
    assert len(lines.splitlines()) <= 420  # the plan's budget (the gate is 450)


# ------------------------------------------------------------------------------ open
def test_opens_empty_and_never_reads_mask_or_skeletons(gt_project, monkeypatch):
    full = np.full((H, W), 255, np.uint8)
    for folder, name in (("mask", f"{GT_STEM}.png"), ("skeletons", f"{GT_STEM}_fake.png")):
        os.makedirs(os.path.join(gt_project.folder, folder))
        imwrite_unicode(os.path.join(gt_project.folder, folder, name), full, durable=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("the class labels read a model output")

    monkeypatch.setattr("app.masks.store.MaskStore.read", boom)
    monkeypatch.setattr("app.io_utils.imread_unicode", boom)
    doc = ClassesDocument()

    assert doc.open(gt_project.image, (W, H)) == []
    assert doc.counts() == {"background": W * H}
    assert not doc.is_dirty() and not doc.is_read_only() and not doc.lossy_remap()
    assert doc.status() == LayerStatus("none", "No class labels")
    assert doc.reviewed() == frozenset() and doc.active_key == "root"


def test_a_lossy_remap_opens_dirty_with_warnings_and_needs_a_prompt(gt_project_lossy):
    doc = ClassesDocument()

    notices = doc.open(gt_project_lossy.image, (W, H))

    assert [n.kind for n in notices] == ["warn"] and doc.is_dirty() and doc.lossy_remap()
    assert "unknown_x" in notices[0].message
    assert doc.status() == LayerStatus(
        "dirty",
        "Unsaved · remapped",
        "Unknown classes were mapped to Ignore:\n" + notices[0].message.split(": ", 1)[1],
    )
    assert (doc.history.plane[300:400, 350:500] == 255).all()  # unknown_x became Ignore
    assert doc.counts() == {"background": 272200, "root": 20000, "ignore": 15000}
    assert doc.reviewed() == {"root"}  # Ignore never appears


def test_a_discarded_remap_is_clean_and_only_a_save_ends_the_prompt(gt_project_lossy):
    doc = opened(gt_project_lossy)
    doc.revert()  # Discard: the remapped ids as opened, nothing written

    assert not doc.is_dirty() and doc.lossy_remap()  # a later save still writes the remap
    assert doc.save().ok
    assert not doc.lossy_remap() and doc.warnings == ()
    assert (read_tracing(gt_project_lossy.labels).ids[300:400, 350:500] == 255).all()


def test_an_invalid_registry_makes_the_document_read_only(gt_project_bad_registry):
    doc = ClassesDocument()

    notices = doc.open(gt_project_bad_registry.image, (W, H))

    # The open sends no notice: the status says it, and the layer's first refused edit
    # (LOCKED_NOTICE, once per image; review WG-T5 r1).
    assert doc.is_read_only() and doc.status().label == "Read-only" and notices == []
    assert doc.status() == LayerStatus(
        "error", "Read-only", f"gt/classes.json is invalid: {doc.registry_error}", local=True
    )
    assert doc.registry == default_registry()
    assert doc.set_ticked("root_tip", True) is False
    assert doc.save() == ClassesSaveResult(False, None, doc.registry_error)
    assert not os.path.exists(gt_project_bad_registry.labels)
    assert not doc.is_dirty()
    assert ClassesDocument.LOCKED_NOTICE.format(err=doc.registry_error) == (
        f"Editing is off: gt/classes.json is invalid ({doc.registry_error}). "
        "Fix the file, then reopen the image."
    )


NOT_SHOWN = {
    "unreadable": (
        "Saved class labels could not be read: they are not shown and are kept unless you "
        "save over them"
    ),
    "size": (
        "Saved class labels do not match the image size: they are not shown and are kept "
        "unless you save over them"
    ),
}


@pytest.mark.parametrize("kind", ["unreadable", "size"])
def test_a_file_that_cannot_be_used_opens_empty_not_shown_and_kept(gt_project, kind):
    os.makedirs(os.path.dirname(gt_project.labels))
    if kind == "unreadable":
        with open(gt_project.labels, "wb") as fh:
            fh.write(b"not a png")
    else:
        write_tracing(gt_project.labels, Tracing(np.ones((10, 10), np.uint8)), default_registry())
    before = file_bytes(gt_project.labels)
    doc = ClassesDocument()

    notices = doc.open(gt_project.image, (W, H))

    assert [(n.message, n.kind) for n in notices] == [(NOT_SHOWN[kind], "warn")]
    assert not doc.history.plane.any() and not doc.is_dirty() and doc.unloaded_file()
    assert doc.status() == LayerStatus("error", "Class labels not shown", NOT_SHOWN[kind])
    assert file_bytes(gt_project.labels) == before
    commit(doc, (10, 10, 5, 5), 1)
    assert doc.save().ok and not doc.unloaded_file()  # saved over: shown from now on
    assert read_tracing(gt_project.labels).ids.shape == (H, W)


# ------------------------------------------------------------------------------ ticks
def test_ticks_are_locked_while_pixels_exist(gt_project):
    doc = opened(gt_project)
    commit(doc, (100, 100, 20, 20), 1)  # root pixels

    assert doc.set_ticked("root", False) is False and "root" in doc.reviewed()
    assert doc.set_ticked("root", True) is False and doc.ticked() == frozenset()
    assert doc.present_keys() == {"root"}
    assert doc.set_ticked("ignore", True) is False  # Ignore is never checked
    assert doc.set_ticked("no_such_class", True) is False
    assert doc.set_ticked("nodule", True) is True and doc.reviewed() == {"root", "nodule"}

    undo(doc)  # the pixels are gone: the box unlocks
    assert doc.reviewed() == {"nodule"} and doc.set_ticked("root", True) is True


def test_a_tick_and_its_takeback_leave_the_document_clean(gt_project):
    doc = opened(gt_project)

    assert doc.set_ticked("nodule", True) and doc.is_dirty()
    assert doc.set_ticked("nodule", False) and not doc.is_dirty()


# ------------------------------------------------------------------------------ classes
def test_the_active_class_and_its_brush_size(gt_project):
    doc = opened(gt_project)
    retired = default_registry().add(key="dead_root", name="Dead root", color="#7f5af0")
    doc.apply_registry(retired.retire("dead_root"))

    assert doc.set_active_class("nodule") is True and doc.active_key == "nodule"
    assert doc.set_active_class("dead_root") is False  # retired: not paintable
    assert doc.set_active_class("no_such_class") is False and doc.active_key == "nodule"
    assert doc.brush_size("nodule") == 8  # the default brush
    doc.set_brush_size("nodule", 25)
    assert (doc.brush_size("nodule"), doc.brush_size("root")) == (25, 8)


def test_fill_source_is_the_active_class_only(gt_project):
    doc = opened(gt_project)
    commit(doc, (10, 10, 3, 3), 1)
    commit(doc, (20, 20, 3, 3), 2)
    doc.set_active_class("nodule")

    outline, value = doc.fill_source(doc.history.plane)

    assert value == 2 and outline.dtype == np.uint8
    assert np.array_equal(outline > 0, doc.history.plane == 2)
    assert set(np.unique(outline).tolist()) == {0, 255}


def test_the_lut_shows_each_class_in_its_colour_and_background_transparent(gt_project):
    doc = opened(gt_project)
    lut = doc.lut()

    assert lut.shape == (256, 4) and lut.dtype == np.uint8
    assert tuple(lut[0]) == (0, 0, 0, 0)
    assert tuple(lut[2]) == (0xB6, 0x7E, 0xFF, 255)  # nodule #ff7eb6 as B, G, R, A
    assert tuple(lut[7]) == (0x80, 0x80, 0x80, 255)  # an id no class owns: grey
    assert np.array_equal(lut, class_lut(doc.registry))


def test_apply_registry_recolours_and_keeps_the_ids(gt_project):
    doc = opened(gt_project)
    commit(doc, (10, 10, 3, 3), 1)
    assert doc.save().ok
    ids = doc.history.plane.copy()
    doc.set_active_class("nodule")
    recoloured = doc.registry.update("root", color="#123456").retire("nodule")

    doc.apply_registry(recoloured)

    assert doc.registry == recoloured and np.array_equal(doc.history.plane, ids)
    assert tuple(doc.lut()[1]) == (0x56, 0x34, 0x12, 255)
    assert doc.active_key == "root"  # the retired class is no longer paintable
    assert not doc.is_dirty()


# ------------------------------------------------------------------------------ save
def test_save_writes_the_registry_first_then_the_file(gt_project, recorder):
    doc = opened(gt_project)
    commit(doc, (100, 100, 20, 20), 1)

    result = doc.save()

    assert recorder.order == ["classes.json", f"{GT_STEM}.png"]
    assert result == ClassesSaveResult(True, doc.gt_path)
    assert os.path.samefile(doc.gt_path, gt_project.labels)
    commit(doc, (10, 10, 2, 2), 2)
    assert doc.save().ok
    assert recorder.order[2:] == [f"{GT_STEM}.png"]  # the registry exists now


def test_an_empty_save_writes_a_file(gt_project):
    doc = opened(gt_project)

    assert doc.save() == ClassesSaveResult(True, doc.gt_path)

    assert os.path.isfile(gt_project.labels)
    tracing = read_tracing(gt_project.labels)
    assert tracing.ids.shape == (H, W) and tracing.ids.max() == 0
    assert tracing.reviewed == frozenset()
    assert doc.status() == LayerStatus("clean", "GT · 0/3 checked", LABELS)


def test_save_writes_the_ticks_and_the_present_classes_and_makes_them_the_ticks(gt_project):
    doc = opened(gt_project)
    commit(doc, (100, 100, 20, 20), 1)
    doc.set_ticked("root_tip", True)

    assert doc.save().ok

    assert read_tracing(gt_project.labels).reviewed == {"root", "root_tip"}
    assert doc.ticked() == {"root", "root_tip"} and not doc.is_dirty()
    undo(doc)  # root's pixels go; its tick stays (it was saved)
    assert doc.reviewed() == {"root", "root_tip"} and doc.is_dirty()
    assert doc.status() == LayerStatus("dirty")


def test_a_failed_registry_write_leaves_the_file_alone(gt_project, monkeypatch):
    ids = np.zeros((H, W), np.uint8)
    ids[5:10, 5:10] = 2
    write_tracing(gt_project.labels, Tracing(ids, frozenset({"nodule"})), default_registry())
    before = file_bytes(gt_project.labels)  # saved by another copy: no classes.json yet
    doc = opened(gt_project)
    commit(doc, (40, 30, 1, 1), 1)

    def locked(*_args, **_kwargs):
        raise OSError("classes.json is locked")

    monkeypatch.setattr(classes_document.gt, "save_registry", locked)

    result = doc.save()

    assert not result.ok and result.error == "classes.json is locked"
    assert doc.is_dirty()
    assert doc.status() == LayerStatus("error", "Not saved", "classes.json is locked")
    assert file_bytes(gt_project.labels) == before  # the "not saved" edit is not in the file
    commit(doc, (41, 30, 1, 1), 1)
    assert doc.status() == LayerStatus("dirty")  # until the next edit


def test_a_first_save_without_a_file_writes_nothing_when_the_registry_fails(
    gt_project, monkeypatch
):
    doc = opened(gt_project)
    commit(doc, (40, 30, 1, 1), 1)

    def denied(*_args, **_kwargs):
        raise OSError("denied")

    monkeypatch.setattr(classes_document.gt, "save_registry", denied)
    assert doc.save().ok is False
    assert not os.path.exists(gt_project.labels)

    monkeypatch.undo()  # the registry can be written again: the next save writes both

    assert doc.save().ok is True
    assert read_tracing(gt_project.labels).ids[30, 40] == 1
    assert os.path.isfile(registry_path(gt_project.folder))


def test_save_writes_the_same_checks_as_present_keys(gt_project):
    doc = opened(gt_project, size=(120, 90))
    rng = np.random.default_rng(4)
    for i, _ in enumerate(_random_session(doc, rng, 30)):
        if i % 5 == 4:
            plane = doc.history.plane.copy()
            expected = doc.ticked() | present_keys(Tracing(plane), doc.registry)
            assert doc.save().ok
            tracing = read_tracing(gt_project.labels)
            assert np.array_equal(tracing.ids, plane)
            assert tracing.reviewed == expected


# ------------------------------------------------------------------------------ revert
def test_revert_puts_back_the_saved_ids_and_ticks_with_an_empty_history(gt_project):
    doc = opened(gt_project)
    commit(doc, (100, 100, 20, 20), 1)
    assert doc.save().ok
    saved = doc.history.plane.copy()
    commit(doc, (10, 10, 30, 30), 2)
    doc.set_ticked("root_tip", True)

    doc.revert()

    assert np.array_equal(doc.history.plane, saved) and doc.reviewed() == {"root"}
    assert not doc.is_dirty() and not doc.history.can_undo
    assert doc.counts() == expected_counts(doc)


# ------------------------------------------------------------------------------ counts
def _random_session(doc: ClassesDocument, rng, steps: int):
    """Random actions on the plane (boxes of random ids, Clear, undo, redo, ticks)."""
    height, width = doc.history.plane.shape
    ids = np.array([0, 1, 2, 3, 255, 7], np.uint8)  # 7: no class owns it
    keys = ["root", "nodule", "root_tip"]
    for _ in range(steps):
        op = rng.integers(0, 6)
        if op == 0:
            undo(doc)
        elif op == 1:
            redo(doc)
        elif op == 2:
            commit(doc, (0, 0, width, height), 0)  # Clear
        elif op == 3:
            doc.set_ticked(keys[int(rng.integers(0, 3))], bool(rng.integers(0, 2)))
        else:
            x, y = int(rng.integers(0, width - 1)), int(rng.integers(0, height - 1))
            w = int(rng.integers(1, width - x + 1))
            h = int(rng.integers(1, height - y + 1))
            commit(doc, (x, y, w, h), rng.choice(ids, size=(h, w)).astype(np.uint8))
        yield


@pytest.mark.parametrize("seed", range(3))
def test_counts_follow_the_patches(gt_project, seed):
    doc = opened(gt_project, size=(120, 90))
    rng = np.random.default_rng(seed)
    for _ in _random_session(doc, rng, 80):
        assert doc.counts() == expected_counts(doc)
        assert doc.present_keys() == present_keys(Tracing(doc.history.plane), doc.registry)


def test_counts_stay_exact_on_the_bincount_path(gt_project, monkeypatch):
    monkeypatch.setattr(classes_paint, "_EXACT_HIST_MAX", 50)
    doc = opened(gt_project, size=(120, 90))
    rng = np.random.default_rng(9)
    for _ in _random_session(doc, rng, 40):
        assert doc.counts() == expected_counts(doc)


def test_histogram_is_exact_above_the_float32_range():
    """cv2.calcHist counts in float32: 2**24 + 3 would read 2**24 + 4 (np.bincount there)."""
    ids = np.zeros(2**24 + 8, np.uint8)
    ids[:5] = 7
    hist = classes_paint.histogram(ids)
    assert hist[0] == 2**24 + 3 and hist[7] == 5


def test_histogram_is_exact_on_both_paths(monkeypatch):
    rng = np.random.default_rng(1)
    ids = rng.integers(0, 256, size=(301, 257)).astype(np.uint8)
    ids[:50] = 3
    histogram = classes_paint.histogram
    expected = np.bincount(ids.ravel(), minlength=256)
    assert np.array_equal(histogram(ids), expected)
    box = ids[7:200, 9:100]
    assert np.array_equal(histogram(box), np.bincount(box.ravel(), minlength=256))
    strided = ids.ravel()[::3]
    assert np.array_equal(histogram(strided), np.bincount(strided, minlength=256))
    assert np.array_equal(histogram(np.zeros((0, 5), np.uint8)), np.zeros(256))
    monkeypatch.setattr(classes_paint, "_EXACT_HIST_MAX", 100)  # above it: np.bincount
    assert np.array_equal(histogram(ids), expected)
    assert histogram(ids).dtype == np.int64


# ------------------------------------------------------------------------------ ids_from_argb
def test_ids_from_argb_keeps_the_committed_id_under_an_unmatched_colour(gt_project):
    doc = opened(gt_project)
    row = np.array([[0, 0, 1, 2, 7, 255, 0, 0]], np.uint8)  # 7: no class owns it (grey)
    commit(doc, (8, 10, 8, 1), row)
    lut = doc.lut()
    argb = lut[row].copy()  # what the committed plane shows
    argb[0, 0] = lut[2]  # nodule painted over background
    argb[0, 2] = (9, 9, 9, 255)  # a colour no class owns over root: root stays
    argb[0, 5] = 0  # erased Ignore: background
    argb[0, 6] = (9, 9, 9, 255)  # a colour no class owns over background: background

    ids = doc.ids_from_argb(argb, (8, 10, 8, 1))

    assert ids.tolist() == [[2, 0, 1, 2, 7, 0, 0, 0]]
    assert ids.dtype == np.uint8 and not np.shares_memory(ids, doc.history.plane)


def _reference_ids_from_argb(arr, registry, fallback=None):
    """Trace GT's implementation before its fast path (G3), verbatim."""
    arr = np.ascontiguousarray(arr)
    packed = arr.view("<u4")[:, :, 0]
    ids = np.zeros(packed.shape, np.uint8)
    known = arr[:, :, 3] == 0
    for c in registry.classes:
        b, g, r, a = class_lut(registry)[c.id]
        hit = packed == (int(b) | int(g) << 8 | int(r) << 16 | int(a) << 24)
        ids[hit] = c.id
        known |= hit
    other = ~known
    if fallback is not None and other.any():
        ids[other] = fallback[other]
    return ids


@pytest.mark.parametrize("seed", range(6))
def test_ids_from_argb_matches_the_reference(seed):
    """Random canvases: class colours, transparent, colours no class owns, noise alpha."""
    rng = np.random.default_rng(seed)
    registry = default_registry().add(key="hypha", name="Hypha", color="#3fa7ff").retire("nodule")
    lut = class_lut(registry)
    h, w = 37, 53
    plane_ids = np.array([0, 1, 2, 3, 4, 255, 7, 200], np.uint8)  # 7, 200: no class owns them
    fallback = rng.choice(plane_ids, size=(h, w)).astype(np.uint8)
    canvas = lut[fallback].copy()
    painted = rng.random((h, w)) < 0.3
    colours = np.vstack(
        [lut[[0, 1, 2, 3, 4, 255]], [[1, 2, 3, 255], [10, 20, 30, 128], [0, 0, 0, 255]]]
    ).astype(np.uint8)
    canvas[painted] = colours[rng.integers(0, len(colours), painted.sum())]
    for fb in (fallback, None, fallback[3:30, 5:40]):
        arr = canvas if fb is None or fb is fallback else canvas[3:30, 5:40]
        expected = _reference_ids_from_argb(arr, registry, fb)
        got = canvas_ids(arr, registry, fb)
        assert got.dtype == np.uint8 and got.shape == expected.shape
        assert np.array_equal(got, expected)
        if fb is not None:
            assert not np.shares_memory(got, fb)  # a new array: the history compares it


def test_ids_from_argb_matches_the_reference_with_a_grey_class():
    """A class in the grey shown for ids no class owns: those pixels still match like before."""
    registry = default_registry().update("nodule", color="#808080")
    lut = class_lut(registry)
    fallback = np.array([[0, 1, 7, 7], [2, 200, 255, 3]], np.uint8)
    canvas = lut[fallback].copy()
    canvas[0, 0] = lut[1]
    expected = _reference_ids_from_argb(canvas, registry, fallback)
    assert expected[0, 2] == 2 and expected[1, 1] == 2  # the grey is the nodule's colour now
    assert np.array_equal(canvas_ids(canvas, registry, fallback), expected)


# ------------------------------------------------------------------------------ files
def test_file_stamps_cover_the_file_and_the_registry_and_write_stamps_the_file(gt_project):
    doc = ClassesDocument()
    assert doc.file_stamps() == () and doc.write_stamps() == ()
    doc.open(gt_project.image, (W, H))

    stamps = doc.file_stamps()

    assert len(stamps) == 2 and doc.write_stamps() == (stamps[0],)
    assert os.path.normcase(stamps[0][0]) == os.path.normcase(gt_project.labels)
    assert os.path.normcase(stamps[1][0]) == os.path.normcase(registry_path(gt_project.folder))
    assert [stamp[1:] for stamp in stamps] == [(None,), (None,)]  # neither exists yet
    assert doc.save().ok
    assert all(len(stamp) == 3 for stamp in doc.file_stamps())


def test_read_matches_open(gt_project_lossy):
    read = ClassesDocument.read(gt_project_lossy.image, (W, H))
    doc = ClassesDocument()

    notices = doc.open(gt_project_lossy.image, (W, H), read)

    assert isinstance(read, ClassesRead) and read.nbytes == W * H
    assert doc.history.plane is read.tracing.ids  # adopted, not read again
    assert (doc.history.plane == read.tracing.ids).all()
    assert notices == ClassesDocument().open(gt_project_lossy.image, (W, H))
    assert read.warnings and read.changed and read.error == "" and read.registry_error == ""


def test_a_read_of_files_that_changed_since_is_not_used(gt_project):
    read = ClassesDocument.read(gt_project.image, (W, H))
    ids = np.zeros((H, W), np.uint8)
    ids[:10, :10] = 1
    write_tracing(gt_project.labels, Tracing(ids, frozenset({"root"})), default_registry())
    doc = ClassesDocument()

    doc.open(gt_project.image, (W, H), read)

    assert doc.history.plane[:10, :10].all() and doc.reviewed() == {"root"}


def test_matches_disk_reads_the_files_again(gt_project):
    doc = opened(gt_project)
    commit(doc, (100, 100, 20, 20), 1)
    assert doc.save().ok
    assert doc.matches_disk()

    save_registry(gt_project.folder, default_registry())
    write_tracing(gt_project.labels, Tracing(doc.history.plane, frozenset({"root"})), doc.registry)
    assert doc.matches_disk()  # rewritten as it was

    other = doc.history.plane.copy()
    other[0, 0] = 2
    write_tracing(gt_project.labels, Tracing(other, frozenset({"root"})), doc.registry)
    assert not doc.matches_disk()


def test_read_back_hands_the_clean_document_back(gt_project):
    doc = opened(gt_project)
    commit(doc, (100, 100, 20, 20), 1)
    assert doc.read_back(doc.file_stamps()) is None  # unsaved
    assert doc.save().ok
    stamps = doc.file_stamps()

    read = doc.read_back(stamps)

    assert isinstance(read, ClassesRead) and read.stamps == stamps
    assert read.tracing.ids is doc.history.plane and read.tracing.reviewed == {"root"}
    other = ClassesDocument()
    assert other.open(gt_project.image, (W, H), read) == []
    assert other.history.plane is read.tracing.ids and other.reviewed() == {"root"}


def test_read_back_is_none_while_a_remap_is_only_in_memory(gt_project_lossy):
    doc = opened(gt_project_lossy)
    doc.revert()  # clean, but the file still holds the old ids

    assert doc.read_back(doc.file_stamps()) is None
