"""``ImageManager``: the library hierarchy cache, what the window is told when a scan is
adopted, and ``load_images`` without a folder.

The dead parts of the manager went (S07-06): a second copy of ``parse_image_name``,
``get_image_names``, a folder dialog nothing opened, the ``has_fake_real_pairs`` flag that
was always False when it was read, and a cache flag that only repeated
``_hierarchy_cache is not None``. These tests pin the behaviour those parts had.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QFileDialog  # noqa: E402

from app.gui import image_manager as im  # noqa: E402
from app.gui.library_index import index_names  # noqa: E402
from app.project_layout import SKELETON_SUFFIX  # noqa: E402

STEMS = ["plot_T1_L1_2024.01.01_120000", "plot_T1_L2_2024.01.01_120000"]


class _Window:
    """The one main-window slot :meth:`ImageManager.on_loading_finished` calls."""

    def __init__(self) -> None:
        self.loaded: list[tuple] = []

    def on_loading_finished(self, *args) -> None:
        self.loaded.append(args)


def _images(root) -> dict[str, str]:
    return {stem: os.path.join(str(root), f"{stem}.png") for stem in STEMS}


def _count_index_names(monkeypatch) -> list[int]:
    calls: list[int] = []

    def counted(names):
        calls.append(1)
        return index_names(names)

    monkeypatch.setattr(im, "index_names", counted)
    return calls


def test_the_hierarchy_is_built_once_until_another_load(monkeypatch, tmp_path):
    calls = _count_index_names(monkeypatch)
    manager = im.ImageManager(None)
    manager.on_loading_finished(_images(tmp_path), {}, {}, False)  # no folder: no scans
    assert calls == []  # lazily, on the first use

    first = manager.get_hierarchical_structure()
    assert manager.get_hierarchical_structure() is first
    assert set(manager.get_leaf_labels()) == set(STEMS)
    assert len(calls) == 1

    manager.on_loading_finished(_images(tmp_path), {}, {}, False)
    second = manager.get_hierarchical_structure()
    assert second is not first and second == first
    assert len(calls) == 2


def test_a_precomputed_hierarchy_is_kept_even_when_empty(monkeypatch, tmp_path):
    """The loader thread's hierarchy is used as it is: only None means "not built"."""
    hierarchy, labels = index_names(_images(tmp_path))
    calls = _count_index_names(monkeypatch)
    manager = im.ImageManager(None)

    manager.on_loading_finished(
        _images(tmp_path), {}, {}, False, hierarchy=hierarchy, labels=labels
    )
    assert manager.get_hierarchical_structure() is hierarchy
    assert manager.get_leaf_labels() is labels

    manager.on_loading_finished(_images(tmp_path), {}, {}, False, hierarchy={}, labels={})
    assert manager.get_hierarchical_structure() == {}
    assert manager.get_leaf_labels() == {}
    assert calls == []


def test_clearing_the_images_drops_the_cached_hierarchy(monkeypatch, tmp_path):
    calls = _count_index_names(monkeypatch)
    manager = im.ImageManager(None)
    manager.on_loading_finished(_images(tmp_path), {}, {}, False)
    assert manager.get_hierarchical_structure()

    manager._clear_images()
    assert manager.get_hierarchical_structure() == {}  # rebuilt, from no images
    assert len(calls) == 2


def test_the_window_is_told_no_fake_real_pairs_even_after_an_overlay_was_found(tmp_path):
    """The window's 4th argument is False on every load, also after an overlay skeleton was
    found (that flag was reset by the load itself before it was read)."""
    (tmp_path / "skeletons").mkdir()
    skeleton = tmp_path / "skeletons" / f"{STEMS[0]}{SKELETON_SUFFIX}"
    skeleton.write_bytes(b"x")
    window = _Window()
    manager = im.ImageManager(window)
    manager.original_folder = str(tmp_path)

    manager.on_loading_finished(_images(tmp_path), {}, {}, False)
    manager.reload_for_view_mode("Overlay")
    assert manager.get_fake_image_path(STEMS[0]) == str(skeleton)
    manager.on_loading_finished(_images(tmp_path), {}, {}, True)

    assert [args[3] for args in window.loaded] == [False, False]
    assert all(args[1] is manager.fake_images for args in window.loaded)
    assert manager.fake_images == {}  # a load forgets the overlay paths


@pytest.mark.parametrize("folder", [None, ""])
def test_load_images_without_a_folder_starts_nothing(monkeypatch, folder):
    """The window's own load action owns the folder dialog; the manager never opens one."""
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: pytest.fail("a dialog opened")
    )
    manager = im.ImageManager(None)
    manager.load_images(folder)
    assert manager.loader_thread is None and manager._generation == 0
