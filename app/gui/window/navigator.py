"""The library navigator: browsing the library tree and opening the image it selects.

A click opens an image; the arrow keys move the selection and the image loads once they
come to rest (:data:`TREE_LOAD_DELAY_MS`), so holding a key decodes only the image it stops
on; PgDn / PgUp step through the shown images, from the image open in an editor when one
is on screen. The selected image opens on the page on screen (the display or Correct),
and the metrics bar follows.

These are functions of the window, bound as :class:`~app.gui.main_window.MainWindow`
methods under their old names (``MainWindow.on_image_selected`` is
:func:`on_image_selected`): a key press or a click costs no extra call, a slot stays a
method of the window, and code and tests keep calling the window. Their state lives on
the window: ``_shown_leaf_name`` (the image whose row was last loaded) and
``_tree_load_timer`` (the debounce), made in ``MainWindow.__init__``, and
``_paging_shortcuts``.
"""

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt

from app.naming import image_stem

from .. import shortcuts
from ..panels import Panel
from ._common import USER_ROLE, same_path

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

#: Keyboard browsing in the library loads the image this long after the last move,
#: so holding an arrow key decodes only the image it stops on.
TREE_LOAD_DELAY_MS = 40


def image_leaves(window: "MainWindow", *, visible_only: bool = False) -> list:
    """Image leaves of the library tree, in display order."""
    return window.library_index.leaves(visible_only=visible_only)


def leaf_for_image(window: "MainWindow", image_path: str, leaves: list | None = None):
    """The leaf whose image is ``image_path`` (O(1)), or None.

    With ``leaves``, only a leaf among them counts.
    """
    if not image_path:
        return None
    name = image_stem(image_path)
    item = window.library_index.item_for(name)
    if item is None or not same_path(window.image_manager.get_image_path(name), image_path):
        return None
    if leaves is not None and item not in leaves:
        return None
    return item


def select_tree_image(window: "MainWindow", image_path: str) -> None:
    """Make ``image_path``'s leaf the tree's current item (without loading it)."""
    item = window._leaf_for_image(image_path)
    if item is not None:
        window._shown_leaf_name = item.data(0, USER_ROLE)  # it is already open
        window.file_list.setCurrentItem(item)
        window.file_list.scrollToItem(item)


def navigate_image(window: "MainWindow", delta: int) -> None:
    """Select and open the next/previous visible image leaf in the library tree.

    In the editor (Correct), steps from the image open in it; otherwise
    (or when nothing is open there) from the tree selection.
    """
    current = None
    editor = window._editors().get(window.right_panel.currentIndex())
    if editor is not None:
        current = window._leaf_for_image(editor.current_image_path)
    if current is None:
        current = window.file_list.currentItem()
    target = window.library_index.step(current, delta, visible_only=True)
    if target is None:
        return
    window.file_list.setCurrentItem(target)
    window.file_list.scrollToItem(target)
    window.on_image_selected(target)


def on_tree_current_changed(window: "MainWindow", current, _previous=None) -> None:
    """Arrow keys moved the library selection: load it once the keys come to rest."""
    if current is not None and current.data(0, USER_ROLE):
        window._tree_load_timer.start()


def load_current_tree_item(window: "MainWindow") -> None:
    """The arrow keys came to rest: open the tree's current image, unless it is shown."""
    item = window.file_list.currentItem()
    name = item.data(0, USER_ROLE) if item is not None else None
    if name and name != window._shown_leaf_name:
        browsing = window.file_list.hasFocus()  # the arrow keys keep stepping the tree
        window.on_image_selected(item)
        if browsing:
            window.file_list.setFocus()


def install_image_paging_shortcuts(window: "MainWindow") -> None:
    """PgDn / PgUp (registry: Global next/previous image) open the next / previous
    image from the library, the chrome and the display. The editor page (Correct)
    binds the same keys itself (it saves first), so these are scoped to the widgets
    outside it instead of window-wide (no ambiguity)."""
    window._paging_shortcuts = []
    owners = (
        window.left_panel,
        window.ribbon,
        window.statusline,
        window.right_panel.widget(Panel.DISPLAY),
    )
    for owner in owners:
        installed = shortcuts.install_shortcuts(
            owner,
            shortcuts.GLOBAL,
            {
                "next_image": lambda: window._page_image(1),
                "previous_image": lambda: window._page_image(-1),
            },
            context=Qt.ShortcutContext.WidgetWithChildrenShortcut,  # one owner has them
        )
        window._paging_shortcuts += installed["next_image"] + installed["previous_image"]


def page_image(window: "MainWindow", delta: int) -> None:
    """PgDn / PgUp outside the editors: the editor on screen moves (it saves first),
    else the library selection."""
    editor = window._editors().get(window.right_panel.currentIndex())
    if editor is not None:
        # From the library while an editor is open: it saves before it moves.
        editor.navigate(delta)
    else:
        window.navigate_image(delta)


def on_tree_item_clicked(window: "MainWindow", item, column):
    """Handle tree item clicks - expand/collapse folders, load images"""
    # Get the stored image name from the item's user data
    image_name = item.data(0, Qt.ItemDataRole.UserRole)

    # If item is not an image (it's a folder node), toggle expansion
    if image_name is None:
        item.setExpanded(not item.isExpanded())
        return

    # If it's an image, load it
    window.on_image_selected(item)


def on_image_selected(window: "MainWindow", item):
    """Load and display the selected image"""
    # Get the stored image name from the item's user data
    image_name = item.data(0, Qt.ItemDataRole.UserRole)

    # If item is not an image (it's a folder node), do nothing
    if image_name is None:
        return

    image_path = window.image_manager.get_image_path(image_name)
    if image_path is None:
        return
    window._shown_leaf_name = image_name
    window._sync_open_correction()  # the image Correct had open may have been corrected

    current = window.right_panel.currentWidget()
    if current is window.correct_editor:
        window.correct_editor.load_image(
            image_path, images_base_folder=window.image_manager.original_folder
        )
        if window._kept_other_image(window.correct_editor, image_path):
            return
    else:
        window.display_controller.display_selected_image_by_name(image_name)

    # Read-only: refresh the metrics strip for the now-selected image.
    window.metrics.refresh(image_name)


def kept_other_image(window: "MainWindow", editor, image_path: str) -> bool:
    """True when ``editor`` kept its open image (the user cancelled or its save
    failed): the library is pointed back at that image."""
    kept = getattr(editor, "current_image_path", None)
    if kept and not same_path(kept, image_path):
        window._select_tree_image(kept)
        return True
    return False


def show_selection_on_display(window: "MainWindow", index: int) -> None:
    """The display page is back on screen (a stage left an editor or a dashboard): it
    shows the library's selection with its metrics. The editors move the selection on
    while it is hidden (PgDn, a clicked row), and may have saved a correction, so its
    last image could sit under another image's name and values."""
    if index != Panel.DISPLAY:
        return
    item = window.file_list.currentItem()
    name = item.data(0, USER_ROLE) if item is not None else None
    if not name or window.image_manager.get_image_path(name) is None:
        return  # nothing selected, or a row of the folder being replaced
    window._shown_leaf_name = name
    window._sync_open_correction()  # a correction saved in Correct shows in the overlay
    window.display_controller.display_selected_image_by_name(name)
    window.metrics.refresh(name)
