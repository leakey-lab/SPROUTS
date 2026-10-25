"""The editor pages (Correct and Trace GT): entering and leaving them, and their reports.

The right panel shows one page at a time (:class:`~app.gui.panels.Panel`); leaving an
editor first offers to save its pending edits, and an editor the user keeps editing
refuses the switch. The window follows the editors' reports: their notices (toasts; a
save's toast, worded alike in both editors, updates in place), the title's unsaved marker,
a saved or removed mask (the Library badge and the metrics), B held on the Correct canvas,
and the corrected skeleton of the image open in Correct (display only, FR-03).

These are functions of the window, bound as :class:`~app.gui.main_window.MainWindow`
methods under their old names (``MainWindow.switch_right_panel`` is
:func:`switch_right_panel`): a stage switch costs no extra call, a slot stays a method of
the window, and code and tests keep calling the window.
"""

import logging
import os
from typing import TYPE_CHECKING

from app.naming import image_stem

from ..image_manager import project_layout_of
from ..panels import Panel
from ._common import released

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)

#: The one toast of the editors' saves: a newer save updates it in place (V6).
SAVED_TOAST = "editor-saved"


def editors(window: "MainWindow") -> dict:
    """The editor pages that hold unsaved work, by right-panel index."""
    return {
        Panel.CORRECT: window.correct_editor,
        Panel.GT_TRACING: window.gt_tracing_interface,
    }


def editor_canvas(window: "MainWindow"):
    """``(canvas, shortcut scope)`` of the editor page on screen, else None."""
    editor = window._editors().get(window.right_panel.currentIndex())
    view = getattr(editor, "graphics_view", None)
    scope = getattr(editor, "SHORTCUT_SCOPE", None)
    return (view, scope) if view is not None and scope else None


def on_editor_notice(window: "MainWindow", message: str, kind: str) -> None:
    """An editor's notice as a toast. A save's summary ("Saved mask · X.jpg") is the one
    keyed save toast: saving again, or paging on with PgDn, updates it in place instead of
    stacking copies (V6). Every other notice, a failed save's among them, shows as is."""
    key = SAVED_TOAST if kind == "success" and message.startswith("Saved ") else None
    window.notify(message, kind, key=key)


def on_gt_saved(window: "MainWindow", image_path: str) -> None:
    """Trace GT wrote a tracing: the editors' save toast, worded as Correct's (V6)."""
    window.notify(f"Saved GT · {os.path.basename(image_path)}", key=SAVED_TOAST)


def sync_window_modified(window: "MainWindow", *_args) -> None:
    """Title marker ``*`` while any editor holds unsaved edits."""
    window.setWindowModified(any(e.has_unsaved_changes() for e in window._editors().values()))


def on_editor_dirty_changed(window: "MainWindow", *_args) -> None:
    """An editor's unsaved state changed: the title marker follows, and a save in
    Correct may have written the open image's skeleton correction."""
    window._sync_window_modified()
    window._sync_open_correction()


def sync_open_correction(window: "MainWindow") -> None:
    """Keep the Library's "corrected" state of the image open in Correct in step with
    the disk (W17, display only: Measure reads the generated skeleton, FR-03).

    Correct's ``skeleton_saved`` stays unconnected (FR-03), so this runs when the
    editor's unsaved state changes (a save) and whenever another image is selected:
    one ``os.path.isfile`` of that image's correction.
    """
    manager = window.image_manager
    path = getattr(window.correct_editor, "current_image_path", "")
    set_correction = getattr(manager, "set_correction", None)
    if not path or not callable(set_correction):
        return
    stem = image_stem(path)
    layout = project_layout_of(manager)
    if layout is None or manager.get_image_path(stem) is None:
        return
    present = os.path.isfile(layout.corrected_skeleton_path(stem))
    if present != manager.has_correction(stem):
        set_correction(stem, present)
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
    return os.path.join(base, f"{image_stem(image_path)}_fake.png")


def switch_right_panel(window: "MainWindow", panel) -> bool:
    """Show ``panel`` (a :class:`Panel`, its index or its key); returns False
    (and stays put) if the editor being left refuses. An unknown key does nothing.

    Leaving the Correct or Trace GT editor first offers to save pending
    edits; the editor refuses when the user cancels, or when its save failed
    and the user kept editing.
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


def toggle_gt_tracing(window: "MainWindow") -> None:
    """Enter / leave the multi-class GT editor (``Panel.GT_TRACING``), like Correct."""
    window._toggle_editor(Panel.GT_TRACING)


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
