"""The editor page (Correct): entering and leaving it, and its reports.

The right panel shows one page at a time (:class:`~app.gui.panels.Panel`); leaving the
editor first offers to save its pending edits, and an editor the user keeps editing
refuses the switch. The window follows the editor's reports: its notices (toasts; a
save's toast updates in place, as does Remove GT's), the title's unsaved marker, a saved
or removed mask, skeleton or class labels and Remove GT… (the Library badge's GT marks and
the metrics; a GT skeleton is measured first, O1), and B held on the Correct canvas.

These are functions of the window, bound as :class:`~app.gui.main_window.MainWindow`
methods under their old names (``MainWindow.switch_right_panel`` is
:func:`switch_right_panel`): a stage switch costs no extra call, a slot stays a method of
the window, and code and tests keep calling the window.
"""

import logging
import os
from typing import TYPE_CHECKING

from app.gt.files import gt_files
from app.naming import image_stem, stem_key
from app.project_layout import SKELETON_SUFFIX, project_layout_of

from ..panels import Panel
from ._common import released

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)

#: The one toast of the editors' saves: a newer save updates it in place (V6).
SAVED_TOAST = "editor-saved"
#: The notices that are that toast: a save's summary and Remove GT's.
_SAVED_PREFIXES = ("Saved ", "Removed GT ")


def editors(window: "MainWindow") -> dict:
    """The editor pages that hold unsaved work, by right-panel index."""
    return {Panel.CORRECT: window.correct_editor}


def editor_canvas(window: "MainWindow"):
    """``(canvas, shortcut scope)`` of the editor page on screen, else None."""
    editor = window._editors().get(window.right_panel.currentIndex())
    view = getattr(editor, "graphics_view", None)
    scope = getattr(editor, "SHORTCUT_SCOPE", None)
    return (view, scope) if view is not None and scope else None


def on_editor_notice(window: "MainWindow", message: str, kind: str) -> None:
    """An editor's notice as a toast. A save's summary ("Saved mask · X.jpg") and Remove
    GT's ("Removed GT · X.jpg") are the one keyed save toast: saving again, or paging on
    with PgDn, updates it in place instead of stacking copies (V6). Every other notice, a
    failed save's among them, shows as is."""
    key = SAVED_TOAST if kind == "success" and message.startswith(_SAVED_PREFIXES) else None
    window.notify(message, kind, key=key)


def on_gt_saved(window: "MainWindow", image_path: str) -> None:
    """The Correct page's class labels were written: the Library's class-label mark and
    row, and the metrics bar's status ("Classes: traced"). The page's own summary is the
    save toast; no second toast here."""
    stem = image_stem(image_path)
    window.image_manager.set_gt(stem, "labels", True)
    window.library_index.refresh_status(stem)
    window.metrics.refresh_if_current(stem)


def on_gt_removed(window: "MainWindow", image_path: str) -> None:
    """Remove GT… deleted the GT files of ``image_path``: the Library's three GT marks follow
    the disk (a file that could not be deleted keeps its mark), then its row and the
    metrics (Measure reads GT first, O1). The page's own toast says what went."""
    manager, stem = window.image_manager, image_stem(image_path)
    layout = project_layout_of(manager)
    found = None if layout is None else gt_files(layout, image_path)
    for kind in ("mask", "skeleton", "labels"):
        manager.set_gt(stem, kind, found is not None and getattr(found, kind) is not None)
    window.library_index.refresh_status(stem)
    window.metrics.refresh_if_current(stem)


def on_skeleton_saved(window: "MainWindow", path: str) -> None:
    """Correct wrote a GT skeleton (``gt/skeletons/<stem>.png``, measured first, O1): the
    Library's skeleton mark, the row and the metrics bar learn of it at once. The file is the
    open image's GT, so its stem is that image's Library stem: a GT file whose name differs
    in case (``gt/skeletons/img.png`` of ``IMG.png``, O7) reports ``IMG``, not ``img``."""
    stem = image_stem(path)
    image = getattr(getattr(window, "correct_editor", None), "current_image_path", "")
    if image and stem_key(image_stem(image)) == stem_key(stem):
        stem = image_stem(image)
    window.image_manager.set_gt(stem, "skeleton", True)
    window.library_index.refresh_status(stem)
    window.metrics.refresh_if_current(stem)


def on_skeleton_cleared(window: "MainWindow", image_path: str) -> None:
    """A skeleton equal to the prediction removed its GT file: the Library's skeleton mark,
    the row and the metrics follow (Measure now reads the prediction)."""
    stem = image_stem(image_path)
    window.image_manager.set_gt(stem, "skeleton", False)
    window.library_index.refresh_status(stem)
    window.metrics.refresh_if_current(stem)


def sync_window_modified(window: "MainWindow", *_args) -> None:
    """Title marker ``*`` while any editor holds unsaved edits."""
    window.setWindowModified(any(e.has_unsaved_changes() for e in window._editors().values()))


def on_editor_dirty_changed(window: "MainWindow", *_args) -> None:
    """An editor's unsaved state changed: the title marker follows, and a save in
    Correct may have written or removed the open image's GT skeleton."""
    window._sync_window_modified()
    window._sync_open_correction()


def sync_open_correction(window: "MainWindow") -> None:
    """Keep the Library's GT-skeleton state of the image open in Correct in step with
    the disk (W17; a GT skeleton is measured, O1, WG-T8).

    A save reports itself (:func:`on_skeleton_saved`, :func:`on_skeleton_cleared`); this
    catches the rest. It runs when the editor's unsaved state changes and whenever another
    image is selected: one ``os.path.isfile`` of that image's ``gt/skeletons/<stem>.png``;
    while the Library marks a GT skeleton the exact name lacks, its stem compared without
    case too (O7), as the scan and the skeleton layer find it (on a case-sensitive disk that
    lists the folder, so only then).
    """
    manager = window.image_manager
    path = getattr(window.correct_editor, "current_image_path", "")
    set_gt = getattr(manager, "set_gt", None)
    if not path or not callable(set_gt):
        return
    stem = image_stem(path)
    layout = project_layout_of(manager)
    if layout is None or manager.get_image_path(stem) is None:
        return
    # The path, not the stem: a stem is never stripped twice (D1).
    present = os.path.isfile(layout.gt_skeleton_path(path)) or (
        manager.has_gt_skeleton(stem) and layout.find_gt_skeleton(path) is not None
    )
    if present != manager.has_gt_skeleton(stem):
        set_gt(stem, "skeleton", present)
        window.library_index.refresh_status(stem)


def follow_generated_outputs(window: "MainWindow") -> None:
    """A Generate run finished: the image open in Correct shows its new outputs.

    Only while Correct is the page on screen, the image has no unsaved edits (those are
    never replaced; a pending polyline counts) and no drag is in progress (a stroke or a
    Connect drag is never cut off): opening the same image again reads the files that
    changed. Entering Correct later does the same. Not bound on the window:
    ``on_outputs_changed`` calls it.
    """
    editor = window.correct_editor
    path = editor.current_image_path
    if window.right_panel.currentIndex() != Panel.CORRECT or not path:
        return
    if editor.has_unsaved_changes():
        return
    if any(layer.gesture_active() for layer in editor.layers.values()):
        return
    editor.load_image(path, images_base_folder=window.image_manager.original_folder)


def generated_skeleton_for(window: "MainWindow", image_path: str) -> str | None:
    """The skeleton the last generation run wrote for ``image_path`` (None: editor default)."""
    base = getattr(window.image_manager, "processed_base_path", None)
    if not base or not image_path:
        return None
    return os.path.join(base, f"{image_stem(image_path)}{SKELETON_SUFFIX}")


def switch_right_panel(window: "MainWindow", panel) -> bool:
    """Show ``panel`` (a :class:`Panel`, its index or its key); returns False
    (and stays put) if the editor being left refuses. An unknown key does nothing.

    Leaving the Correct editor first offers to save pending edits; the editor refuses
    when the user cancels, or when its save failed and the user kept editing.
    """
    try:
        target = Panel.coerce(panel)
    except ValueError:
        logger.warning("Unknown right panel %r", panel)
        return True
    current = window.right_panel.currentIndex()
    editor = window._editors().get(current)
    if editor is not None and target != current and not released(editor):
        return False
    window.right_panel.setCurrentIndex(target)
    return True


def toggle_correct(window: "MainWindow") -> None:
    """Enter / leave the Correct stage (``Panel.CORRECT``), then show the selected image
    there (or in the Library). A leave the editor refuses (unsaved edits the user keeps)
    stays in it."""
    window._toggle_editor(Panel.CORRECT)


def toggle_editor(window: "MainWindow", panel: Panel) -> None:
    """Enter the ``panel`` editor, or leave it for the display page when it is on screen;
    then the selected image opens in the editor entered."""
    # Status badges read image_manager at paint time: no recolour pass here
    # (a saved mask keeps its badge in every stage, F-016).
    target = panel if window.right_panel.currentIndex() != panel else Panel.DISPLAY
    if not window.switch_right_panel(target):
        return  # the editor being left kept unsaved edits: stay there
    if target == Panel.DISPLAY:
        return  # the display page shows the selection itself (_show_selection_on_display)
    current_item = window.file_list.currentItem()
    if current_item:
        window.on_image_selected(current_item)


def on_b_key_status_changed(window: "MainWindow", is_pressed):
    """B held on the Correct canvas: the status line says how to resize the brush."""
    if is_pressed:
        window.status_bar.showMessage("Scroll to resize the brush; release B when done", 3000)
    else:
        window.status_bar.clearMessage()


def on_mask_saved(window: "MainWindow", image_path):
    """A mask was written: record it for the Library badge and refresh the metrics.

    The editor's own summary toast says so; no second toast here.
    """
    # mask_saved -> highlight_saved_mask records the mask in image_manager.
    window.mask_saved.emit(image_path)
    window.metrics.refresh_if_current(image_stem(image_path))


def on_mask_cleared(window: "MainWindow", image_path):
    """An emptied mask removed the saved file: the badge and the metrics follow."""
    # mask_cleared -> unhighlight_cleared_mask forgets the mask in image_manager.
    window.mask_cleared.emit(image_path)
    window.metrics.refresh_if_current(image_stem(image_path))
