"""ClassesLayer: the class labels as a raster layer of the Correct workspace (WG-T4).

The layer is a ``RasterLayer`` (Brush, Eraser and Fill, the ring, the history) over the
Qt-free ``ClassesDocument``: it paints in the active class's colour, Fill reads the active
class's outlines only, each class keeps its own brush size, a class with pixels stays
checked, and an invalid ``gt/classes.json`` makes it read-only with one notice per image.
A recording ``FakeHost`` stands in for the page; a real ``CorrectSession`` with
``FakePrompts`` answers "would the page ask?".

Ported here (the old files were deleted in WG-T11): the class semantics of
``tests/test_gt_tracing_interface.py`` and every case of ``tests/test_gt_unsaved_state.py``
(unsaved = the ids or the ticks differ from the saved ones; a remap on open stays unsaved
until it is saved or discarded). Keys, PgDn, the class panel and the prompts' wording are
the page's (WG-T5).
"""

from __future__ import annotations

import os
import statistics
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QMessageBox  # noqa: E402

from app.editing import planes  # noqa: E402
from app.gt import (  # noqa: E402
    Registry,
    Tracing,
    default_registry,
    gt_path,
    read_tracing,
    registry_path,
    save_registry,
    write_tracing,
)
from app.gui.correct import classes_document  # noqa: E402
from app.gui.correct.classes_document import ClassesDocument, ClassesRead  # noqa: E402
from app.gui.correct.classes_layer import ClassesLayer  # noqa: E402
from app.gui.correct.layer import (  # noqa: E402
    DiskGuardedLayer,
    GuardedSaveLayer,
    LayerStatus,
    ReadAheadLayer,
    SaveOutcome,
    missing_members,
)
from app.gui.correct.mask_tools import MaskToolHost  # noqa: E402
from app.gui.correct.session import CorrectSession, SaveReport  # noqa: E402
from app.gui.key_text import platform_keys  # noqa: E402
from app.gui.paint_canvas import pixmap_to_argb_array  # noqa: E402
from app.gui.widgets import tokens  # noqa: E402
from tests.correct_fakes import FakeHost, FakeLayer, FakePrompts  # noqa: E402
from tests.gt_goldens_support import make_gt_project  # noqa: E402

H, W = 60, 80  # the ported tests' photo
NO_MODIFIER = Qt.KeyboardModifier.NoModifier
FILL_MISS = "Fill works inside a closed outline. Close the loop first."
STRICT = os.environ.get("SPROUTS_PERF_TESTS") == "1"
strict = pytest.mark.skipif(not STRICT, reason="wall-clock budget: set SPROUTS_PERF_TESTS=1")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


@pytest.fixture
def project(tmp_path):
    """``plot_001.png`` (80 x 60) and a full-white ``mask/plot_001.png``: the class labels
    never read (or write) ``mask/``, so every test starts from empty labels."""
    folder = tmp_path / "project"
    (folder / "mask").mkdir(parents=True)
    image = str(folder / "plot_001.png")
    cv2.imwrite(image, np.full((H, W, 3), 90, np.uint8))
    mask = str(folder / "mask" / "plot_001.png")
    cv2.imwrite(mask, np.full((H, W), 255, np.uint8))
    return SimpleNamespace(folder=str(folder), image=image, mask=mask, labels=gt_path(image))


def _gt_project(tmp_path, case: str) -> SimpleNamespace:
    image, folder = make_gt_project(str(tmp_path), case)
    return SimpleNamespace(folder=folder, image=image, labels=gt_path(image))


@pytest.fixture
def gt_project(tmp_path):
    """The goldens' 640 x 480 photo, nothing under ``gt/``."""
    return _gt_project(tmp_path, "two_classes_crossing")


@pytest.fixture
def gt_project_bad_registry(tmp_path):
    """The goldens' photo with an invalid ``gt/classes.json``."""
    project = _gt_project(tmp_path, "empty_save")
    os.makedirs(os.path.join(project.folder, "gt"))
    with open(registry_path(project.folder), "w", encoding="utf-8") as fh:
        fh.write("{")
    return project


# ------------------------------------------------------------------------------ helpers
def open_layer(scene, image: str, size=(W, H)):
    """A ClassesLayer opened on ``image``; ``(layer, host, the open's notices)``."""
    host = FakeHost()
    layer = ClassesLayer(host, scene)
    notices = layer.open(image, size, os.path.dirname(image))
    return layer, host, notices


def open_session(scene, image: str, prompts: FakePrompts | None = None, size=(W, H)):
    """A real CorrectSession over one ClassesLayer, opened on ``image``."""
    host = FakeHost()
    layer = ClassesLayer(host, scene)
    out = SimpleNamespace(layer=layer, host=host, prompts=prompts or FakePrompts())
    out.dirty, out.notices = [], []
    out.session = CorrectSession(
        [layer],
        prompts=out.prompts,
        parent=lambda: None,
        on_dirty_changed=out.dirty.append,
        on_notice=lambda message, kind: out.notices.append((message, kind)),
        on_history_changed=lambda: None,
        on_undone=lambda *_args: None,
        on_saved=lambda _outcome: None,
    )
    host.session = out.session
    out.session.open(image, size, os.path.dirname(image))
    return out


def stroke(layer, *points, size=None) -> None:
    """Press at the first point, a dragging move per further point, release at the last."""
    if size is not None:
        layer.set_size(size)
    layer.press(QPoint(*points[0]), NO_MODIFIER)
    for point in points[1:]:
        layer.move(QPoint(*point), True)
    layer.release(QPoint(*points[-1]))


def click(layer, point) -> None:
    layer.press(QPoint(*point), NO_MODIFIER)
    layer.release(QPoint(*point))


def ids(layer) -> np.ndarray:
    return layer.document.history.plane


def shows_the_plane(layer) -> bool:
    """The canvas is exactly the plane in the registry's colours."""
    argb = pixmap_to_argb_array(layer.canvas.image)
    return np.array_equal(argb, layer.document.lut()[ids(layer)])


def notices_of(host: FakeHost) -> list[tuple[str, str]]:
    return [(call[1], call[2]) for call in host.calls if call[0] == "notify"]


def locked_text(layer) -> str:
    return (
        "Editing is off: gt/classes.json is invalid ("
        + layer.document.registry_error
        + "). Fix the file, then reopen the image."
    )


def file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def write_with_unknown_key(image: str) -> bytes:
    """A labels file from another project: id 4 = 'hypha', unknown here (becomes Ignore)."""
    other = default_registry().add(key="hypha", name="Hypha", color="#3fa7ff")
    plane = np.zeros((H, W), np.uint8)
    plane[5:15, 5:15] = 4
    plane[30:40, 30:40] = 1
    write_tracing(gt_path(image), Tracing(plane, frozenset({"root"})), other)
    return file_bytes(gt_path(image))


# ============================================================================== contract
def test_the_classes_layer_misses_no_member_and_answers_the_optional_protocols(scene):
    layer = ClassesLayer(FakeHost(), scene)

    assert missing_members(layer) == [] and missing_members(layer, ReadAheadLayer) == []
    assert missing_members(layer, GuardedSaveLayer) == []
    assert missing_members(layer, DiskGuardedLayer) == []
    assert missing_members(layer, MaskToolHost) == []
    assert (layer.layer_id, layer.noun, layer.color, layer.Z) == (
        "classes",
        "class labels",
        tokens.ACCENT,
        1.2,
    )
    assert layer.CLEARED_NOTICE == platform_keys(
        "Class labels cleared. Ctrl+Z to undo; Save writes an empty file."
    )
    assert layer.opacity() == pytest.approx(0.60) and layer.item.zValue() == 1.2
    assert (layer.current_tool, layer.size(), layer.active_class()) == ("brush", 8, "root")
    assert isinstance(layer.document, ClassesDocument) and layer.on_classes_changed is None


def test_the_fake_classes_layer_reads_like_the_real_one(scene):
    real, fake = ClassesLayer(FakeHost(), scene), FakeLayer("classes")

    assert (fake.layer_id, fake.noun, fake.color, fake.tools) == (
        real.layer_id,
        real.noun,
        real.color,
        real.tools,
    )
    assert (fake.size(), fake.opacity()) == (real.size(), real.opacity())


def test_opens_empty_and_never_reads_the_mask(scene, project):
    before = file_bytes(project.mask)

    layer, _, notices = open_layer(scene, project.image)

    assert notices == [] and ids(layer).shape == (H, W) and not ids(layer).any()
    assert not pixmap_to_argb_array(layer.canvas.image)[:, :, 3].any()
    assert not layer.is_dirty() and layer.status() == LayerStatus("none", "No class labels")
    assert file_bytes(project.mask) == before


# ============================================================================== painting
def test_painting_class_b_over_a_leaves_b_and_the_eraser_leaves_background(scene, gt_project):
    layer, _, _ = open_layer(scene, gt_project.image, (640, 480))
    layer.set_active_class("root")
    stroke(layer, (100, 200), (300, 200), size=8)
    layer.set_active_class("nodule")
    stroke(layer, (200, 100), (200, 300), size=8)

    plane = ids(layer)
    assert (plane[200, 150], plane[200, 200], plane[150, 200], plane[250, 250]) == (1, 2, 2, 0)

    layer.set_tool("eraser")
    stroke(layer, (180, 200), (220, 200), size=20)

    assert (plane[200, 200], plane[200, 150], plane[120, 200]) == (0, 1, 2)
    assert shows_the_plane(layer)
    assert layer.document.present_keys() == {"root", "nodule"}


def test_painted_classes_save_their_ids_and_ticks_and_never_touch_the_mask(scene, project):
    mask_before = file_bytes(project.mask)
    layer, _, _ = open_layer(scene, project.image)
    layer.set_active_class("root")
    stroke(layer, (10, 30), (70, 30), size=6)
    layer.set_active_class("nodule")
    stroke(layer, (40, 5), (40, 55), size=6)
    assert layer.is_dirty()

    outcome = layer.save()

    assert outcome == SaveOutcome("classes", ok=True, path=layer.document.gt_path)
    assert not layer.is_dirty()
    tracing = read_tracing(project.labels)
    assert tracing is not None and tracing.ids.shape == (H, W)
    assert tracing.ids[30, 15] == 1 and tracing.ids[30, 65] == 1  # root only
    assert tracing.ids[30, 40] == 2 and tracing.ids[10, 40] == 2  # the crossing is nodule
    assert tracing.ids[50, 10] == 0 and set(np.unique(tracing.ids).tolist()) == {0, 1, 2}
    assert tracing.reviewed == {"root", "nodule"}
    assert os.path.exists(registry_path(project.folder))
    assert file_bytes(project.mask) == mask_before
    assert sorted(os.listdir(os.path.join(project.folder, "mask"))) == ["plot_001.png"]
    assert layer.status() == LayerStatus("clean", "GT · 2/3 checked", "gt/plot_001.png")


def test_fill_is_limited_to_the_active_class_outline(scene, project):
    layer, host, _ = open_layer(scene, project.image)
    layer.set_active_class("root")
    stroke(layer, (15, 10), (45, 10), (45, 40), (15, 40), (15, 10), size=3)
    layer.set_active_class("nodule")
    stroke(layer, (65, 50), size=5)  # a dot outside the square
    before, depth = ids(layer).copy(), len(layer.document.history.undo_stack)
    host.calls.clear()
    layer.set_tool("fill")

    click(layer, (30, 25))  # nodule active: no nodule outline around the click

    assert notices_of(host) == [(FILL_MISS, "info")]
    assert len(layer.document.history.undo_stack) == depth and np.array_equal(ids(layer), before)

    layer.set_active_class("root")
    click(layer, (30, 25))  # the root square is a closed outline: its inside becomes root

    assert (ids(layer)[25, 30], ids(layer)[50, 65], ids(layer)[55, 5]) == (1, 2, 0)
    assert len(layer.document.history.undo_stack) == depth + 1
    assert shows_the_plane(layer)


def test_the_brush_colour_is_the_active_class_colour(scene, gt_project):
    layer, _, _ = open_layer(scene, gt_project.image, (640, 480))

    layer.set_active_class("nodule")
    assert layer.brush_color.name() == "#ff7eb6"
    layer.set_active_class("ignore")
    assert layer.brush_color.name() == "#8a8f98"
    layer.set_active_class("no_such_class")  # refused: the active class stays
    assert layer.active_class() == "ignore"
    layer.set_active_class("root")
    assert layer.brush_color.name() == "#5fd6a0"

    stroke(layer, (50, 50), size=6)
    assert tuple(pixmap_to_argb_array(layer.canvas.image)[50, 50]) == (0xA0, 0xD6, 0x5F, 255)
    layer.hover(QPoint(60, 60))  # the ring keeps the layer's colour
    assert layer.ring.inner.pen().color() == QColor(tokens.ACCENT)


def test_per_class_brush_size_is_remembered(scene, gt_project):
    layer, _, _ = open_layer(scene, gt_project.image, (640, 480))
    layer.set_active_class("root")
    layer.set_size(20)

    layer.set_active_class("root_tip")
    assert layer.size() == 8  # its own, the default
    layer.set_size(2)
    layer.set_active_class("root")
    assert layer.size() == 20
    layer.set_active_class("root_tip")
    assert layer.size() == 2


def test_retired_class_pixels_survive_open_and_save(scene, project):
    registry = (
        default_registry()
        .add(key="dead_root", name="Dead root", color="#7f5af0")
        .retire("dead_root")
    )
    assert registry.by_key("dead_root").id == 4
    save_registry(project.folder, registry)
    plane = np.zeros((H, W), np.uint8)
    plane[5:15, 5:15] = 4
    write_tracing(project.labels, Tracing(plane, frozenset({"nodule"})), registry)

    layer, _, notices = open_layer(scene, project.image)
    assert notices == [] and not layer.is_dirty()
    layer.set_active_class("root")
    stroke(layer, (50, 40), (70, 40), size=4)
    assert layer.save().ok

    tracing = read_tracing(project.labels)
    assert (tracing.ids[5:15, 5:15] == 4).all() and tracing.ids[40, 60] == 1
    assert set(np.unique(tracing.ids).tolist()) == {0, 1, 4}
    assert {"root", "nodule"} <= tracing.reviewed


# ============================================================================== ticks
def test_ticks_are_locked_while_the_class_has_pixels(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    heard: list[int] = []
    layer.on_classes_changed = lambda: heard.append(1)
    layer.set_active_class("root")
    heard.clear()

    stroke(layer, (10, 30), (70, 30), size=6)
    assert heard  # the class panel hears of the new pixels

    assert layer.set_ticked("root", False) is False and "root" in layer.document.reviewed()
    assert layer.set_ticked("nodule", True) is True
    assert layer.document.reviewed() == {"root", "nodule"}

    layer.clear()  # the pixels are gone: root is no longer checked
    assert layer.document.reviewed() == {"nodule"}
    layer.undo()  # they are back: root is checked and locked again
    assert layer.document.reviewed() == {"root", "nodule"}
    assert layer.set_ticked("root", False) is False


def test_an_empty_save_writes_a_file_with_the_ticks(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    assert layer.set_ticked("nodule", True) is True
    assert layer.is_dirty()

    assert layer.save().ok

    tracing = read_tracing(project.labels)
    assert tracing is not None and tracing.ids.shape == (H, W) and not tracing.ids.any()
    assert tracing.reviewed == {"nodule"}


def test_a_refused_tick_still_tells_the_panel(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    heard: list[int] = []
    layer.on_classes_changed = lambda: heard.append(1)

    assert layer.set_ticked("ignore", True) is False

    assert heard == [1]  # the panel puts the box back


# ============================================================================== registry
def test_press_and_clear_are_refused_while_read_only_with_one_notice(
    scene, gt_project_bad_registry
):
    host = FakeHost()
    layer = ClassesLayer(host, scene)
    notices = layer.open(gt_project_bad_registry.image, (640, 480), gt_project_bad_registry.folder)
    assert notices == [] and layer.is_read_only()  # the first refusal says it (WG-T5 r1)

    layer.press(QPoint(10, 10), NO_MODIFIER)
    layer.release(QPoint(10, 10))
    assert layer.clear() is False

    assert notices_of(host).count((locked_text(layer), "danger")) == 1
    assert notices_of(host) == [(locked_text(layer), "danger")]
    assert not ids(layer).any() and not layer.can_undo() and not layer.is_dirty()


def test_replace_plane_is_refused_while_read_only(scene, gt_project_bad_registry):
    host = FakeHost()
    layer = ClassesLayer(host, scene)
    layer.open(gt_project_bad_registry.image, (640, 480), gt_project_bad_registry.folder)
    plane = np.ones((480, 640), np.uint8)

    assert layer.replace_plane(plane) is False

    assert not ids(layer).any() and not layer.can_undo() and not layer.is_dirty()
    assert notices_of(host) == [(locked_text(layer), "danger")]
    assert ("layer_edited", "classes") not in host.calls


def test_every_edit_is_refused_while_read_only(scene, project):
    """While classes.json is invalid nothing can be saved, so nothing can be edited."""
    os.makedirs(os.path.join(project.folder, "gt"))
    with open(registry_path(project.folder), "w", encoding="utf-8") as fh:
        fh.write("{")
    layer, host, _ = open_layer(scene, project.image)
    assert layer.status().label == "Read-only"

    stroke(layer, (10, 10), (30, 10), size=6)  # brush
    layer.set_tool("eraser")
    stroke(layer, (10, 10), (30, 10))  # eraser
    layer.set_tool("fill")
    click(layer, (20, 20))  # fill
    assert layer.clear() is False
    assert layer.set_ticked("nodule", True) is False  # a user tick

    assert notices_of(host) == [(locked_text(layer), "danger")]  # the first refusal says why
    assert layer.document.ticked() == frozenset() and not ids(layer).any()
    assert len(layer.document.history.undo_stack) == 0 and not layer.is_dirty()

    layer.open(project.image, (W, H), project.folder)  # the next open says it again
    stroke(layer, (10, 10), (30, 10))
    assert notices_of(host) == [(locked_text(layer), "danger")] * 2


@pytest.mark.parametrize("bad_json", ["{", "[]", '{"version": 1, "classes": [7]}'])
def test_an_invalid_registry_refuses_the_save(scene, project, bad_json):
    os.makedirs(os.path.join(project.folder, "gt"))
    with open(registry_path(project.folder), "w", encoding="utf-8") as fh:
        fh.write(bad_json)

    layer, _, notices = open_layer(scene, project.image)

    assert notices == []  # the status and the first refused edit say it (WG-T5 r1)
    error = layer.document.registry_error
    assert layer.status() == LayerStatus(
        "error", "Read-only", f"gt/classes.json is invalid: {error}", local=True
    )
    stroke(layer, (10, 10), (30, 10))  # refused: nothing could be saved
    assert not ids(layer).any()
    assert layer.save() == SaveOutcome(
        "classes",
        ok=False,
        message=f"Not saved: gt/classes.json is invalid ({error})",
        kind="danger",
    )
    with open(registry_path(project.folder), encoding="utf-8") as fh:
        assert fh.read() == bad_json
    assert not os.path.exists(project.labels) and not layer.is_dirty()


def test_an_invalid_registry_never_leaves_unsaveable_edits(scene, project):
    """A file whose ids would be remapped opens clean while classes.json is invalid."""
    write_with_unknown_key(project.image)
    with open(registry_path(project.folder), "w", encoding="utf-8") as fh:
        fh.write("{")

    layer, _, _ = open_layer(scene, project.image)

    assert layer.is_read_only() and not layer.is_dirty()
    assert layer.status().label == "Read-only" and not layer.save_needs_prompt()


def test_a_fixed_registry_unblocks_the_save(scene, project):
    os.makedirs(os.path.join(project.folder, "gt"))
    with open(registry_path(project.folder), "w", encoding="utf-8") as fh:
        fh.write("{")
    layer, _, _ = open_layer(scene, project.image)
    stroke(layer, (10, 10), (30, 10))
    assert layer.save().ok is False

    save_registry(project.folder, default_registry())  # the user fixes the file
    layer.open(project.image, (W, H), project.folder)  # reopening the image reads it again
    stroke(layer, (10, 10), (30, 10))

    assert not layer.is_read_only() and layer.save().ok
    assert read_tracing(project.labels).ids[10, 20] == 1


def test_apply_registry_recolours_without_changing_ids(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    layer.set_active_class("root")
    stroke(layer, (10, 30), (70, 30), size=6)
    layer.set_active_class("nodule")
    stroke(layer, (40, 5), (40, 55), size=6)
    before = ids(layer).copy()
    heard: list[int] = []
    layer.on_classes_changed = lambda: heard.append(1)
    new_registry = layer.registry().update("root", color="#123456", name="Main root")

    layer.apply_registry(new_registry)

    assert layer.registry() == new_registry and heard
    assert np.array_equal(ids(layer), before) and shows_the_plane(layer)
    assert tuple(pixmap_to_argb_array(layer.canvas.image)[30, 15, :3]) == (0x56, 0x34, 0x12)
    layer.undo()  # the history holds ids: undoing the nodule stroke keeps the root ids
    assert (ids(layer)[30, 15], ids(layer)[30, 40], ids(layer)[10, 40]) == (1, 1, 0)
    assert shows_the_plane(layer)


# ============================================================================== files
def test_a_label_file_of_another_size_is_not_shown_and_is_kept(scene, project):
    write_tracing(project.labels, Tracing(np.ones((10, 10), np.uint8)), default_registry())
    before = file_bytes(project.labels)

    layer, _, notices = open_layer(scene, project.image)

    assert [n.kind for n in notices] == ["warn"]
    assert not ids(layer).any() and not layer.is_dirty()
    assert layer.unloaded_label() == "gt/plot_001.png"  # the session asks before replacing it
    assert file_bytes(project.labels) == before


@pytest.mark.parametrize("kind", ["corrupt", "wrong_size"])
def test_an_unusable_label_file_is_flagged_until_saved(scene, project, kind):
    if kind == "corrupt":
        os.makedirs(os.path.dirname(project.labels))
        with open(project.labels, "wb") as fh:
            fh.write(b"not a png")
    else:
        write_tracing(project.labels, Tracing(np.ones((10, 10), np.uint8)), default_registry())
    layer, _, _ = open_layer(scene, project.image)
    status = layer.status()
    assert (status.state, status.label) == ("error", "Class labels not shown")

    stroke(layer, (10, 10), (30, 10))
    assert layer.status() == LayerStatus("dirty")
    assert layer.save().ok
    assert layer.status().state == "clean" and layer.unloaded_label() == ""


def test_a_failed_save_stays_unsaved_and_says_why(scene, project, monkeypatch):
    layer, _, _ = open_layer(scene, project.image)
    stroke(layer, (10, 10), (30, 10), size=6)

    def disk_full(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(classes_document.gt, "write_tracing", disk_full)

    outcome = layer.save()

    assert outcome == SaveOutcome(
        "classes",
        ok=False,
        message=f"Could not save class labels to {layer.document.gt_path}: disk full",
        kind="danger",
    )
    assert layer.is_dirty() and layer.status() == LayerStatus("error", "Not saved", "disk full")
    assert not os.path.exists(project.labels)


def test_the_read_ahead_reads_what_open_reads_and_the_clean_document_is_handed_back(scene, project):
    write_with_unknown_key(project.image)
    layer, _, _ = open_layer(scene, project.image)
    reader = layer.read_ahead(project.image, project.folder)

    payload = reader((W, H))
    assert isinstance(payload, ClassesRead)
    layer.open(project.image, (W, H), project.folder, ahead=payload)

    assert ids(layer) is payload.tracing.ids and layer.is_dirty()  # adopted: a remap pending
    assert layer.read_back(layer.file_stamps()) is None  # not as on disk
    assert layer.save().ok
    back = layer.read_back(layer.file_stamps())
    assert isinstance(back, ClassesRead) and back.tracing.ids is ids(layer)


# ============================================================================== remaps
def test_a_lossy_remap_asks_before_it_is_saved_and_discard_keeps_the_file(scene, project):
    before = write_with_unknown_key(project.image)

    opened = open_session(scene, project.image, FakePrompts(unsaved=["discard"]))

    layer = opened.layer
    assert any(kind == "warn" and "hypha" in message for message, kind in opened.notices)
    assert layer.is_dirty() and layer.save_needs_prompt()
    assert layer.status().label == "Unsaved · remapped"
    assert opened.session.release() is True  # the normal prompt; Discard
    assert opened.prompts.asked == [("unsaved", None, "class labels", "plot_001.png")]
    assert file_bytes(project.labels) == before and not layer.is_dirty()


def test_saving_a_lossy_remap_writes_ignore_and_ends_the_prompt(scene, project):
    write_with_unknown_key(project.image)
    layer, _, _ = open_layer(scene, project.image)

    assert layer.save().ok

    assert (read_tracing(project.labels).ids[5:15, 5:15] == 255).all()
    assert not layer.save_needs_prompt() and layer.status().label != "Unsaved · remapped"


def test_a_lossless_remap_is_saved_without_a_prompt(scene, project):
    """Ids that differ only by number (same keys) are remapped and saved silently."""
    swapped = default_registry().to_dict()
    swapped["classes"][0]["id"], swapped["classes"][1]["id"] = 2, 1  # root=2, nodule=1
    plane = np.zeros((H, W), np.uint8)
    plane[5:15, 5:15] = 2  # root in the other project's ids
    write_tracing(project.labels, Tracing(plane, frozenset({"root"})), Registry.from_dict(swapped))

    opened = open_session(scene, project.image)

    assert not [n for n in opened.notices if n[1] == "warn"]
    assert opened.layer.is_dirty() and not opened.layer.save_needs_prompt()
    assert opened.layer.status() == LayerStatus("dirty")
    assert opened.session.save_dirty().ok and opened.prompts.asked == []
    assert (read_tracing(project.labels).ids[5:15, 5:15] == 1).all()


# ============================================================================== unsaved state
def test_undoing_every_stroke_leaves_nothing_to_save(scene, project):
    opened = open_session(scene, project.image)
    layer = opened.layer
    layer.set_active_class("root")
    stroke(layer, (10, 30), (70, 30), size=6)
    layer.set_active_class("nodule")
    stroke(layer, (40, 5), (40, 55), size=6)

    layer.undo()
    assert layer.is_dirty()  # the root stroke is still there
    layer.undo()

    assert not layer.is_dirty() and opened.dirty == [True, False]
    assert layer.status() == LayerStatus("none", "No class labels")  # as opened
    assert opened.session.release() is True and opened.prompts.asked == []
    layer.redo()
    assert layer.is_dirty() and opened.dirty == [True, False, True]


def test_undo_back_to_the_saved_labels_is_saved_again(scene, project):
    opened = open_session(scene, project.image)
    layer = opened.layer
    layer.set_active_class("root")
    stroke(layer, (10, 30), (70, 30), size=6)
    assert layer.save().ok
    layer.set_active_class("nodule")
    stroke(layer, (40, 5), (40, 55), size=6)
    opened.dirty.clear()

    layer.undo()
    assert not layer.is_dirty()
    assert layer.status() == LayerStatus("clean", "GT · 1/3 checked", "gt/plot_001.png")
    layer.redo()
    assert layer.is_dirty() and layer.status() == LayerStatus("dirty")
    layer.undo()
    assert not layer.is_dirty() and opened.dirty == [False, True, False]


def test_leaving_after_undoing_everything_asks_nothing_and_writes_nothing(scene, project):
    opened = open_session(scene, project.image)
    stroke(opened.layer, (10, 30), (70, 30))
    opened.layer.undo()

    assert opened.session.release() is True  # leaving: no prompt

    assert opened.prompts.asked == [] and not os.path.exists(project.labels)


def test_pgdn_after_undoing_everything_writes_nothing(scene, project):
    opened = open_session(scene, project.image)
    stroke(opened.layer, (10, 30), (70, 30))
    opened.layer.undo()

    assert opened.session.save_dirty() == SaveReport()  # PgDn's save: nothing is dirty

    assert not os.path.exists(project.labels)  # unchanged labels are not written


def test_a_stroke_that_changed_nothing_is_no_change(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    layer.set_tool("eraser")  # erasing empty labels changes no pixel
    layer.set_size(6)
    layer.press(QPoint(10, 30), NO_MODIFIER)
    layer.move(QPoint(70, 30), True)
    assert layer.gesture_active()  # the stroke is not committed yet

    layer.release(QPoint(70, 30))

    assert not layer.is_dirty() and layer.status() == LayerStatus("none", "No class labels")


def test_a_click_outside_the_image_changes_nothing(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    stroke(layer, (W + 40, H + 40))  # a dab entirely off the canvas
    assert not layer.is_dirty() and not layer.can_undo()


def test_clearing_back_to_the_opened_labels_is_no_change(scene, project):
    """Pixels decide, not the history: two actions later the labels are as opened."""
    layer, _, _ = open_layer(scene, project.image)
    stroke(layer, (10, 30), (70, 30))
    layer.clear()

    assert not layer.is_dirty() and len(layer.document.history.undo_stack) == 2
    layer.undo()  # the stroke is back
    assert layer.is_dirty()


def test_erasing_a_stroke_by_hand_is_saved_again(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    assert layer.save().ok  # empty labels on disk
    layer.set_active_class("root")
    stroke(layer, (10, 30), (70, 30), size=6)
    assert layer.is_dirty()

    layer.set_tool("eraser")
    stroke(layer, (5, 30), (75, 30), size=20)

    assert not ids(layer).any() and not layer.is_dirty()
    assert layer.status() == LayerStatus("clean", "GT · 0/3 checked", "gt/plot_001.png")


def test_clearing_empty_labels_changes_nothing(scene, project):
    opened = open_session(scene, project.image)
    assert opened.layer.clear() is True
    assert not opened.layer.is_dirty() and opened.dirty == []


def test_ticking_and_unticking_a_class_changes_nothing(scene, project):
    opened = open_session(scene, project.image)

    opened.layer.set_ticked("nodule", True)
    assert opened.layer.is_dirty()
    opened.layer.set_ticked("nodule", False)

    assert not opened.layer.is_dirty() and opened.dirty == [True, False]


def test_a_tick_still_differs_after_its_stroke_is_undone(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    layer.set_ticked("root_tip", True)
    stroke(layer, (10, 30), (70, 30))
    layer.undo()

    assert layer.is_dirty()  # the tick is still new
    assert layer.save().ok
    assert read_tracing(project.labels).reviewed == {"root_tip"}


def test_a_remapped_file_stays_unsaved_after_undoing_every_stroke(scene, project):
    write_with_unknown_key(project.image)
    opened = open_session(scene, project.image, FakePrompts(unsaved=["discard"]))
    layer = opened.layer
    assert layer.is_dirty() and layer.status().label == "Unsaved · remapped"

    stroke(layer, (10, 40), (70, 40))
    layer.undo()

    assert layer.is_dirty() and layer.status().label == "Unsaved · remapped"
    assert opened.session.release() is True  # Discard: the file stays as it is
    assert not layer.is_dirty()
    stroke(layer, (10, 40), (70, 40))
    layer.undo()
    assert not layer.is_dirty()  # back at the labels as Discard left them


# ============================================================================== cost
def test_a_class_count_change_needs_no_pixel_compare(scene, project, monkeypatch):
    """The document keeps pixel counts per class: while they differ from the saved ones the
    labels are unsaved without a full pass; the pixels are compared only when they match."""
    calls = []
    real = planes.planes_equal
    monkeypatch.setattr(planes, "planes_equal", lambda a, b: calls.append(1) or real(a, b))
    layer, _, _ = open_layer(scene, project.image)
    stroke(layer, (10, 30), (70, 30))
    stroke(layer, (10, 40), (70, 40))
    layer.undo()
    assert layer.is_dirty() and calls == []

    layer.undo()  # every class count is back: one pass decides

    assert not layer.is_dirty() and calls == [1]


def test_the_same_class_counts_elsewhere_are_still_a_change(scene, project):
    layer, _, _ = open_layer(scene, project.image)
    layer.set_active_class("root")
    stroke(layer, (15, 15))  # one dab
    assert layer.save().ok
    saved = layer.document.counts()
    layer.set_tool("eraser")
    stroke(layer, (15, 15), size=30)  # erased
    layer.set_tool("brush")
    layer.set_size(8)

    stroke(layer, (50, 40))  # the same dab elsewhere: the same pixel counts

    assert layer.document.counts() == saved and saved["root"] > 0
    assert layer.is_dirty()


def _big_layer(scene, project):
    layer, _, _ = open_layer(scene, project.image, (2048, 1536))
    layer.set_active_class("root")
    stroke(layer, (100, 100), (1900, 1400), size=40)
    layer.undo()  # equal planes: the comparison runs to the last pixel
    return layer


def _median_check_ms(layer, count=30) -> float:
    times = []
    for _ in range(count):
        layer.document.touch()  # a new revision: the next check compares again
        t0 = time.perf_counter()
        layer.is_dirty()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def test_the_check_is_cheap_on_a_2048_plane(scene, project):
    """About 0.05 ms (cv2.norm over 3.1 MB); 2 ms leaves 40x headroom on a busy machine."""
    layer = _big_layer(scene, project)
    assert not layer.is_dirty()
    assert _median_check_ms(layer) < 2.0


@strict
def test_the_check_is_within_0_25_ms_on_a_2048_plane(scene, project):
    assert _median_check_ms(_big_layer(scene, project), 200) <= 0.25
