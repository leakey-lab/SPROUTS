"""
File tree management for the main window.

Thin, name-stable wrappers over the window's :class:`~app.gui.library_index.LibraryIndex`
(tree building, O(1) item lookup, per-image status). Status is drawn from
``image_manager`` state by the index's delegate, so saving or clearing a mask only
updates that state and repaints one row.
"""

import os

from app.gui import library_index
from app.gui.library_index import LibraryIndex


def natural_sort_key(text):
    """Sort key that orders numbers naturally (T1, T2, T10, T20); "No Tube" last."""
    return library_index.natural_sort_key(text)


def library_index_for(main_window) -> LibraryIndex:
    """The window's library index (created on first use for windows built without one)."""
    index = getattr(main_window, "library_index", None)
    if index is None:
        manager = getattr(main_window, "image_manager", None)
        index = LibraryIndex(
            main_window.file_list,
            path_of=getattr(manager, "get_image_path", None),
            has_mask=getattr(manager, "has_mask", None),
            has_skeleton=getattr(manager, "has_skeleton", None),
            has_correction=getattr(manager, "has_correction", None),
        )
        main_window.library_index = index
        main_window.file_list.library_index = index  # for find_tree_item_by_image_name
    return index


def find_tree_item_by_image_name(file_list, image_name):
    """The tree row of ``image_name`` (O(1) through the index), or None."""
    index = getattr(file_list, "library_index", None)
    if index is None:
        index = LibraryIndex(file_list)
        file_list.library_index = index
    return index.item_for(image_name)


def populate_file_list(main_window):
    """Rebuild the library tree from ``image_manager``'s hierarchy (keeps the search)."""
    manager = main_window.image_manager
    library_index_for(main_window).build(
        manager.get_hierarchical_structure(), manager.get_leaf_labels()
    )


def update_file_list_mask_status(main_window):
    """Repaint every row's status badge (the badges read ``image_manager`` directly)."""
    library_index_for(main_window).refresh_status()


def _stem(image_path):
    return os.path.splitext(os.path.basename(os.path.normpath(image_path)))[0]


def highlight_saved_mask(main_window, image_path):
    """Record the saved mask of ``image_path`` and repaint its row."""
    manager = main_window.image_manager
    manager.set_mask(image_path, manager.mask_path_for(image_path))
    library_index_for(main_window).refresh_status(_stem(image_path))


def unhighlight_cleared_mask(main_window, image_path):
    """Record that ``image_path`` has no mask any more and repaint its row."""
    main_window.image_manager.set_mask(image_path, None)
    library_index_for(main_window).refresh_status(_stem(image_path))
