"""A stem that ends in an image extension is never stripped a second time (final review D1).

Image ``X.png.jpg`` has the stem ``X.png`` (only the last image extension goes). Every
lookup of its GT and its prediction keeps that stem: ``gt/skeletons/X.png.png``,
``gt/mask/X.png.png``, ``gt/X.png.png``, ``skeletons/X.png_fake.png``. Before the fix the
"stem or path" methods of ``ProjectLayout`` stripped ``X.png`` again to ``X``: the skeleton
layer opened and saved ``X.png``'s GT skeleton, Remove GT… deleted ``X.png``'s three GT files,
Measure recorded the saved GT as "image gone", and the Library marks and the metrics bar
looked under ``X``. Driven through the real Correct page, the Measure handler, the Library's
``ImageManager`` with the window's report functions, and the metrics bar's
``MeasurementIndex``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS  # noqa: E402
from app.data_processing.measurement_index import MeasurementIndex  # noqa: E402
from app.gt import Tracing, default_registry, write_tracing  # noqa: E402
from app.gui.correct.layer import SKELETON  # noqa: E402
from app.gui.image_manager import ImageManager  # noqa: E402
from app.gui.window import editors  # noqa: E402
from app.masks import write_mask_file  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    H,
    W,
    box_plane,
    draw_line,
    editor_factory,
    install_guards,
    qapp,
    write_image,
    write_mask,
    write_skeleton,
)
from tests.test_measure_gt_first import run_area, run_length  # noqa: E402

LENGTH, AREA = LENGTH_CSV_HEADERS[5], AREA_CSV_HEADERS[5]


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


@pytest.fixture
def make_editor(app):
    yield from editor_factory()


class Confirm:
    """Remove GT…'s question, answered Yes; the names it was asked about."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, _parent, name: str, detail: str) -> bool:
        self.calls.append((name, detail))
        return True


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def gt_of_x(layout: ProjectLayout, x_png: str) -> dict[str, str]:
    """The three GT files of image ``X.png``, written from its path; returns kind -> path."""
    on = np.zeros((H, W), bool)
    on[10:40, 10:60] = True
    paths = {
        "mask": layout.gt_mask_path(x_png),
        "skeleton": layout.gt_skeleton_path(x_png),
        "labels": layout.gt_path(x_png),
    }
    write_mask_file(paths["mask"], on, durable=False)
    write_skeleton(paths["skeleton"], row=30)
    write_tracing(paths["labels"], Tracing(np.ones((H, W), np.uint8)), default_registry())
    return paths


def fake_window(manager: ImageManager, open_image: str = "") -> SimpleNamespace:
    """What the window's report functions read: the Library, its rows, the metrics bar."""
    return SimpleNamespace(
        image_manager=manager,
        library_index=SimpleNamespace(refresh_status=lambda stem: None),
        metrics=SimpleNamespace(refresh_if_current=lambda stem: None),
        correct_editor=SimpleNamespace(current_image_path=open_image),
    )


def library(folder: str) -> ImageManager:
    """The Library of ``folder`` as a finished scan leaves it (its GT marks from disk)."""
    manager = ImageManager()
    manager.original_folder = folder
    layout = ProjectLayout(folder)
    manager.on_loading_finished(layout.scan_images(), {}, layout.scan_masks(), False)
    return manager


def test_x_png_and_x_png_jpg_keep_their_own_gt(make_editor, app, tmp_path):
    folder = str(tmp_path / "project")
    layout = ProjectLayout(folder)
    x_png = write_image(os.path.join(folder, "X.png"))
    x_jpg = write_image(os.path.join(folder, "X.png.jpg"), value=60)  # a converted copy
    assert sorted(layout.scan_images()) == ["X", "X.png"]
    for stem, row in (("X", 110), ("X.png", 60)):  # what Generate writes for each
        write_skeleton(os.path.join(layout.skeletons_dir, f"{stem}_fake.png"), row=row)
        write_mask(os.path.join(layout.mask_dir, f"{stem}.png"), box_plane())
    x_gt = gt_of_x(layout, x_png)
    before = {kind: read_bytes(path) for kind, path in x_gt.items()}
    own_gt = os.path.join(layout.gt_skeletons_dir, "X.png.png")

    # The skeleton layer of X.png.jpg opens its own prediction and saves its own GT file.
    editor = make_editor(x_jpg, folder)
    skeleton = editor.layers[SKELETON]
    assert skeleton.document.source == "prediction"
    assert skeleton.document.model.mask[60].any() and not skeleton.document.model.mask[110].any()
    draw_line(editor)
    saved = []
    editor.skeleton_saved.connect(saved.append)
    assert editor.save()
    assert [os.path.normcase(p) for p in saved] == [os.path.normcase(own_gt)]
    assert os.path.isfile(own_gt)
    assert {kind: read_bytes(path) for kind, path in x_gt.items()} == before

    # The Library: each image its own marks; the report of the save marks X.png.
    manager = library(folder)
    assert manager.gt_skeleton_stems == {"X", "X.png"}
    manager.gt_skeleton_stems.discard("X.png")  # as before the save
    editors.on_skeleton_saved(fake_window(manager), saved[0])
    assert manager.gt_skeleton_stems == {"X", "X.png"}
    editors.sync_open_correction(fake_window(manager, x_jpg))
    assert manager.gt_skeleton_stems == {"X", "X.png"}
    assert os.path.normcase(manager.gt_skeleton_path_for("X.png")) == os.path.normcase(own_gt)
    assert os.path.normcase(manager.gt_skeleton_path_for("X")) == os.path.normcase(x_gt["skeleton"])
    manager.set_gt("X.png", "mask", True)
    assert manager.gt_mask_stems == {"X", "X.png"}
    manager.set_gt("X.png", "mask", False)
    assert manager.gt_mask_stems == {"X"}

    # Measure: one row per image, each from its own GT file.
    lengths = run_length(layout, app)
    assert sorted(lengths) == ["X", "X.png"]
    assert [lengths[s]["Source"] for s in ("X", "X.png")] == ["gt", "gt"]
    assert lengths["X"][LENGTH] != lengths["X.png"][LENGTH]
    areas = run_area(layout, app)
    assert [areas[s]["Source"] for s in ("X", "X.png")] == ["gt", "prediction"]

    # The metrics bar: each Library stem finds its own row and file.
    index = MeasurementIndex(folder)
    for stem in ("X", "X.png"):
        assert index.lookup(stem).length_mm == float(lengths[stem][LENGTH])
        assert index.lookup(stem).area_mm2 == float(areas[stem][AREA])
    assert index.measured_from("X.png") == {
        "length": ("gt/skeletons/X.png.png", "gt"),
        "area": ("mask/X.png.png", "prediction"),
    }
    assert index.measured_from("X") == {
        "length": ("gt/skeletons/X.png", "gt"),
        "area": ("gt/mask/X.png", "gt"),
    }
    assert index.outdated("X.png") == {"length": False, "area": False}

    # Remove GT… on X.png.jpg removes its own file; X.png's GT stays.
    confirm = Confirm()
    editor.actions.confirm_remove = confirm
    editor.remove_gt()
    assert confirm.calls and not os.path.exists(own_gt)
    assert {kind: read_bytes(path) for kind, path in x_gt.items()} == before
    editors.on_gt_removed(fake_window(manager), x_jpg)
    assert manager.gt_skeleton_stems == {"X"}
    assert manager.gt_mask_stems == manager.gt_label_stems == {"X"}

    # X.png still opens its own GT skeleton.
    editor.load_image(x_png, folder)
    assert skeleton.document.source == "gt" and skeleton.document.model.mask[30].any()


def test_x_png_jpg_alone_is_measured_from_the_gt_it_saved(make_editor, app, tmp_path):
    folder = str(tmp_path / "project")
    layout = ProjectLayout(folder)
    x_jpg = write_image(os.path.join(folder, "X.png.jpg"))
    write_skeleton(os.path.join(layout.skeletons_dir, "X.png_fake.png"), row=60)
    own_gt = os.path.join(layout.gt_skeletons_dir, "X.png.png")

    editor = make_editor(x_jpg, folder)
    skeleton = editor.layers[SKELETON]
    assert skeleton.document.source == "prediction"  # Generate's X.png_fake.png
    draw_line(editor)
    assert editor.save() and os.path.isfile(own_gt)
    assert sorted(os.listdir(layout.gt_skeletons_dir)) == ["X.png.png"]

    lengths = run_length(layout, app)
    assert [(name, row["Source"]) for name, row in lengths.items()] == [("X.png", "gt")]
    assert MeasurementIndex(folder).lookup("X.png").length_source == "gt"
    manager = library(folder)
    assert manager.gt_skeleton_stems == {"X.png"}

    confirm = Confirm()
    editor.actions.confirm_remove = confirm
    editor.remove_gt()
    assert confirm.calls == [("X.png.jpg", confirm.calls[0][1])]
    assert "gt/skeletons/X.png.png" in confirm.calls[0][1]
    assert not os.path.exists(own_gt)
    assert skeleton.document.source == "prediction"
