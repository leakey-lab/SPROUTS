"""The Library badge has three marks: mask, skeleton, class labels (WG-T6).

Colour says the kind (mask green, skeleton orange, class labels purple); the shape says the
state: a ring is "none", a filled circle a model prediction, a filled diamond GT (for the
class labels: "traced"). The badge reads ``ImageManager`` state at paint time, so it follows
Correct's saves and removals and a folder rescan with one row repaint and no rescan.

Offscreen. The window tests build a tmp_path project of four photos (the first with a model
mask and a generated skeleton) with the helpers of the Correct window tests.
"""

from __future__ import annotations

import os
from collections import Counter

import numpy as np
import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem, QTreeWidget

from app.gui.correct.layer import MASK, SKELETON
from app.gui.library_index import (
    LibraryIndex,
    _badge_pixmap,
    build_hierarchy,
    status_text,
)
from app.gui.widgets import tokens
from app.project_layout import ProjectLayout
from tests.correct_support import H, W, draw_line, paint, write_mask
from tests.qt_teardown import delete_widgets, flush_deleted
from tests.qt_wait import settle
from tests.test_correct_main_window import STEMS, enter, new_project, open_project, select
from tests.window_support import build_window, quiet_window

STEM = "24EFCAM1_T001_L011_2024.07.18_111530_001_SMM"

pytestmark = pytest.mark.usefixtures("window_guards")


# ============================================================================ the pixmap
def _mark_pixels(pixmap, mark: int):
    """The pixels of mark ``mark`` (0 mask, 1 skeleton, 2 labels): its 10 px column, which
    starts 1 px right of the pixmap column grid so that no diamond tip leaves it."""
    image = pixmap.toImage()
    dpr = pixmap.devicePixelRatio()
    left, right = round((10 * mark + 1) * dpr), round((10 * mark + 11) * dpr)
    for x in range(left, min(right, image.width())):
        for y in range(image.height()):
            yield image.pixelColor(x, y)


def opaque(pixmap, mark: int) -> int:
    """How many pixels of the mark are more than half covered."""
    return sum(1 for c in _mark_pixels(pixmap, mark) if c.alpha() > 128)


def dominant_colour(pixmap, mark: int) -> str:
    """The most common fully covered colour of the mark, as ``#rrggbb``."""
    counts = Counter(c.name() for c in _mark_pixels(pixmap, mark) if c.alpha() == 255)
    return counts.most_common(1)[0][0]


def test_the_badge_has_three_marks_and_the_shape_carries_the_state(app):
    pix = _badge_pixmap("gt", "prediction", True)
    assert pix.size() / pix.devicePixelRatio() == QSize(32, 12)
    ring = opaque(_badge_pixmap("none", "none", False), 0)
    circle = opaque(_badge_pixmap("prediction", "none", False), 0)
    diamond = opaque(_badge_pixmap("gt", "none", False), 0)
    assert circle > diamond > ring > 0  # 63 px² circle, ~50 px² diamond, a thin ring (dpr 2)
    assert dominant_colour(pix, 0) == tokens.MASK
    assert dominant_colour(pix, 1) == tokens.SKEL
    assert dominant_colour(pix, 2) == tokens.ACCENT
    # the labels diamond is the same shape as the other diamonds
    assert opaque(_badge_pixmap("gt", "gt", True), 2) == opaque(
        _badge_pixmap("none", "none", True), 2
    )
    assert opaque(_badge_pixmap("none", "none", True), 2) == diamond


def test_each_mark_follows_its_own_state_only(app):
    base = _badge_pixmap("none", "none", False)
    mask_only = _badge_pixmap("gt", "none", False)
    assert opaque(mask_only, 0) != opaque(base, 0)
    assert opaque(mask_only, 1) == opaque(base, 1) and opaque(mask_only, 2) == opaque(base, 2)
    skeleton_only = _badge_pixmap("none", "prediction", False)
    assert opaque(skeleton_only, 1) != opaque(base, 1)
    assert opaque(skeleton_only, 0) == opaque(base, 0) and opaque(skeleton_only, 2) == opaque(
        base, 2
    )
    labels_only = _badge_pixmap("none", "none", True)
    assert opaque(labels_only, 2) != opaque(base, 2)
    assert opaque(labels_only, 0) == opaque(base, 0) and opaque(labels_only, 1) == opaque(base, 1)
    assert dominant_colour(base, 0) == tokens.TEXT_FAINT


# ============================================================================ the words
def test_status_text_words():
    assert status_text("gt", "prediction", False) == "Mask: GT · Skeleton: prediction · No classes"
    assert status_text("none", "none", True) == "No mask · No skeleton · Classes: traced"
    assert status_text("prediction", "gt", False) == "Mask: prediction · Skeleton: GT · No classes"


# ======================================================================== the index parts
def _providers(masks=None, skeletons=None, labels=()):
    masks, skeletons, labels = dict(masks or {}), dict(skeletons or {}), set(labels)
    return (
        dict(
            path_of=lambda n: f"D:/p/{n}.jpg",
            mask_state=lambda n: masks.get(n, "none"),
            skeleton_state=lambda n: skeletons.get(n, "none"),
            has_labels=lambda n: n in labels,
        ),
        masks,
        skeletons,
        labels,
    )


@pytest.fixture
def index_with_providers(app):
    providers, masks, skeletons, labels = _providers(
        masks={STEM: "gt"}, skeletons={STEM: "prediction"}
    )
    tree = QTreeWidget()
    index = LibraryIndex(tree, **providers)
    index.build(build_hierarchy([STEM]))
    index.states = (masks, skeletons, labels)
    yield tree, index
    tree.deleteLater()
    del tree, index
    flush_deleted()


def test_status_of_is_a_state_triple(index_with_providers):
    _tree, index = index_with_providers
    assert index.status_of(STEM) == ("gt", "prediction", False)
    assert index.status_of("other") == ("none", "none", False)


def test_the_tooltip_names_the_state_of_each_kind(index_with_providers):
    _tree, index = index_with_providers
    assert index.tooltip_for(STEM) == (
        f"{STEM}\nMask: GT · Skeleton: prediction · No classes\nD:/p/{STEM}.jpg"
    )


def test_the_delegate_caches_one_icon_per_state_triple(index_with_providers):
    _tree, index = index_with_providers
    delegate = index._delegate
    delegate.status_icon("gt", "prediction", False)
    delegate.status_icon("none", "none", True)
    assert len(delegate._icons) == 2
    delegate.status_icon("gt", "prediction", False)
    assert len(delegate._icons) == 2


@pytest.mark.parametrize(
    "state", [("none", "none", False), ("prediction", "gt", False), ("gt", "gt", True)]
)
def test_the_badge_is_not_tinted_when_selected(index_with_providers, state):
    _tree, index = index_with_providers
    icon = index._delegate.status_icon(*state)
    size = QSize(32, 12)
    normal = icon.pixmap(size, QIcon.Mode.Normal).toImage()
    for mode in (QIcon.Mode.Selected, QIcon.Mode.Active):
        assert icon.pixmap(size, mode).toImage() == normal, mode


def test_the_delegate_draws_the_state_the_providers_report(index_with_providers):
    tree, index = index_with_providers
    masks, _skeletons, labels = index.states
    delegate = tree.itemDelegate()
    model_index = tree.indexFromItem(index.item_for(STEM))

    def icon_key():
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, model_index)
        assert not option.icon.isNull() and option.decorationSize == QSize(32, 12)
        return option.icon.cacheKey()

    before = icon_key()
    masks[STEM] = "prediction"
    index.refresh_status(STEM)
    assert icon_key() != before
    changed = icon_key()
    labels.add(STEM)
    assert icon_key() != changed


# ========================================================================== in the window
@pytest.fixture
def window(app, tmp_path):
    made = [build_window(tmp_path / "s.ini")]
    yield made[0]
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def icon_key(window, stem: str) -> int:
    """The cache key of the badge the Library paints for ``stem``."""
    delegate = window.file_list.itemDelegate()
    option = QStyleOptionViewItem()
    delegate.initStyleOption(
        option, window.file_list.indexFromItem(window.library_index.item_for(stem))
    )
    assert not option.icon.isNull()
    return option.icon.cacheKey()


def badge_words(window, stem: str) -> str:
    return status_text(*window.library_index.status_of(stem))


def test_the_badge_follows_saves_removals_and_a_rescan(window, tmp_path, monkeypatch):
    project = new_project(tmp_path)  # image 0: a model mask and a generated skeleton
    open_project(window, project)
    stem, manager, index = STEMS[0], window.image_manager, window.library_index
    assert index.status_of(stem) == ("prediction", "prediction", False)
    key = icon_key(window, stem)

    repaints = []
    real = index.refresh_status
    monkeypatch.setattr(
        index, "refresh_status", lambda name=None: repaints.append(name) or real(name)
    )
    select(window, 0)
    enter(window, "Correct")
    editor = window.correct_editor

    paint(editor, (20, 100), (60, 100))  # a mask save (WG-T2)
    assert editor.save()
    settle()
    assert index.status_of(stem) == ("gt", "prediction", False)
    assert stem in repaints  # the save itself repainted the row
    after_mask = icon_key(window, stem)
    assert after_mask != key

    repaints.clear()
    editor.set_active_layer(SKELETON)
    draw_line(editor)  # a skeleton save (WG-T3)
    assert editor.save()
    settle()
    assert index.status_of(stem) == ("gt", "gt", False)
    assert stem in repaints
    assert icon_key(window, stem) != after_mask
    assert badge_words(window, stem) == "Mask: GT · Skeleton: GT · No classes"

    editor.set_active_layer(MASK)  # back to the prediction: the GT mask goes
    editor.load_prediction()
    assert editor.save()
    settle()
    assert not manager.has_gt_mask(stem) and manager.has_gt_skeleton(stem)
    assert index.status_of(stem) == ("prediction", "gt", False)

    # class labels come from the scan of gt/<stem>.png: a rescan finds them
    labels = ProjectLayout(project.folder).gt_path(stem)
    write_mask(labels, np.ones((H, W), np.uint8))
    assert index.status_of(stem)[2] is False
    manager.refresh_outputs()
    index.refresh_status()
    assert index.status_of(stem) == ("prediction", "gt", True)
    assert badge_words(window, stem) == "Mask: prediction · Skeleton: GT · Classes: traced"


def test_a_gt_mask_on_disk_reaches_the_badge_and_correct_through_the_load(window, tmp_path):
    project = new_project(tmp_path)  # image 0 has a model mask in mask/
    write_mask(project.gt_mask(0), np.ones((H, W), np.uint8))  # and a saved GT mask in gt/mask/
    assert os.path.isfile(project.mask(0))

    open_project(window, project)  # the normal load path: no set_gt by hand

    stem = STEMS[0]
    assert window.image_manager.has_mask(stem)  # the prediction is still there
    assert window.image_manager.mask_state(stem) == "gt"
    assert window.library_index.status_of(stem)[0] == "gt"
    assert badge_words(window, stem).startswith("Mask: GT")
    assert badge_words(window, STEMS[1]).startswith("No mask")
    select(window, 0)
    enter(window, "Correct")
    assert window.correct_editor.layers[MASK].status().label == "GT"
