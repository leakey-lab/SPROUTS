"""Trace GT's canvas editor: :class:`RasterEditorBase`, one editable plane over a photo.

The shell of the "Trace GT" stage (its subclass is
:class:`app.gui.gt_tracing_interface.GtTracingInterface`), composed of the parts every canvas
editor shares:

* :class:`~app.gui.editor_canvas.EditorCanvasView`: zoom, fit and pan; the pointer, wheel and
  key input that is not a pan reaches the editor through its ``canvas_*`` methods (the
  editor is the view's ``InputSink``);
* :class:`~app.gui.canvas_layers.photo.PhotoLayerItem` (z 0): the photo, smooth at every zoom;
* :class:`~app.gui.canvas_layers.raster.RasterLayerItem` (z 1): the edit layer, exact pixels
  when magnified and area coverage when minified;
* :class:`~app.gui.paint_canvas.PaintCanvas`: brush, eraser, fill, clear and patch undo on the
  canvas image, over the document's plane in a :class:`~app.gui.patch_history.PatchHistory`;
* :class:`~app.gui.editor_chrome.EditorChrome`: tool rail, dock, Enhance popover, status chip
  and zoom cluster, placed by one ``OverlayLayout``;
* :mod:`app.gui.editor_prompts`: the unsaved-changes prompts;
* :class:`~app.gui.brush_preview.BrushPreview`: the brush ring (z 10).

The photo is decoded once per open, through the Enhance controls' source cache
(``NormalizationControls.display_image``), so an enhancement sticks across images. Dirty is a
flag, set by every edit, undo and redo and cleared by a save or by Discard. Discard puts the
document back as it was last saved (or opened) and clears the history, like the Correct
editor's Discard: the editor keeps a copy of the plane each time it becomes clean.

A subclass decides what the plane means and where it is stored: ``_open_document`` reads it,
``save_mask`` writes it, ``_plane_lut`` / ``_argb_to_plane`` / ``_fill_source`` map it to canvas
colours and back, and ``_status`` names its save state for the status chip.

Keys (the Trace GT scope of :mod:`app.gui.shortcuts`, the registry behind the F1 sheet): B brush
(hold B + wheel = brush size), E eraser, F fill, [ / ] brush size, H show / hide the layer,
Ctrl+Z undo, Ctrl+Y / Ctrl+Shift+Z redo, Ctrl+S save, Ctrl+0 fit, Ctrl+1 100 %, Ctrl+= / Ctrl+-
zoom along the zoom ladder, Ctrl+wheel zoom; Space (hold) or a middle drag pans.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QButtonGroup, QGraphicsScene, QVBoxLayout, QWidget

from . import editor_prompts, shortcuts
from .brush_preview import BrushPreview
from .canvas_layers.photo import PhotoLayerItem
from .canvas_layers.raster import RasterLayerItem
from .canvas_view import PIXEL_GRID_OFF, PIXEL_GRID_ON
from .editor_canvas import EditorCanvasView
from .editor_chrome import EditorChrome
from .image_normalization_interface import NormalizationControls
from .paint_canvas import PaintCanvas, Rect
from .patch_history import Patch, PatchHistory
from .widgets import DockSlider, IconButton, keep_canvas_focus, tokens
from .widgets.buttons import ghost_button, primary_button

__all__ = ["RasterEditorBase"]

# B hold / tap: the key alone (a keypad B counts), never with Ctrl / Shift / Alt.
_PLAIN = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)


def _file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a readable file, ``(path, None)`` otherwise."""
    try:
        stat = os.stat(path)
    except OSError:
        return (path, None)
    return (path, stat.st_mtime_ns, stat.st_size)


def _same_file(a: str, b: str) -> bool:
    """True when both are set and name the same file (case / separator-insensitive on Windows)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class RasterEditorBase(QWidget):
    """Canvas editor over one image and an editable plane (the Trace GT stage's shell).

    Subclasses decide what the plane means and where it is stored:

    - ``_open_document(image_path, size) -> (plane, dirty)`` reads it (``size`` is the
      photo's ``QSize``);
    - ``save_mask(save_path=None) -> bool`` writes it;
    - ``_plane_lut()`` / ``_argb_to_plane()`` / ``_fill_source()`` map it to canvas colours;
    - ``_status()`` names its save state for the status chip.

    The editor is the ``InputSink`` of its canvas: the ``canvas_*`` methods receive the
    input the view does not use for panning, in image pixels.
    """

    b_key_status_changed = pyqtSignal(bool)
    # (message, kind) for transient feedback; MainWindow routes it to a toast.
    notice = pyqtSignal(str, str)
    dirty_changed = pyqtSignal(bool)

    DEFAULT_BRUSH = 8
    DEFAULT_OPACITY = 60
    # What the editor saves, as shown in its labels and the unsaved-changes prompt.
    EDITOR_NOUN = "tracing"
    # Registry scope of the editor's keys (app.gui.shortcuts).
    SHORTCUT_SCOPE = shortcuts.TRACE_GT
    CLEARED_NOTICE = "Cleared. Ctrl+Z to undo."
    CLEAR_TIP = "Clear the canvas. Undo with Ctrl+Z."
    FILL_MISS_NOTICE = "Fill works inside a closed outline. Close the loop first."
    # The status chip's hint line while no image is open.
    EMPTY_HINT = "Select an image in the library to edit it."

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_image_path = ""
        # One entry per stroke / fill / clear; the committed plane lives here too.
        self.edits = PatchHistory(on_change=self._sync_history_buttons)
        # The plane as last saved or opened (a copy): what Discard goes back to.
        self._saved_plane: np.ndarray | None = None
        # File stamps of the open photo and of its document files: re-selecting the open
        # image reloads only what changed on disk.
        self._photo_stamp: tuple = ()
        self._document_stamps: tuple = ()
        self._chip_shown: tuple | None = None  # what the status chip shows
        self.b_key_pressed = False
        self._b_wheel_used = False
        self.dirty = False
        self.drawing = False  # a stroke (or a fill click) is in progress
        self._last_hover: QPoint | None = None
        self._preview_allowed = True
        self.brush_color = QColor(tokens.MASK)
        self.brush_size = self.DEFAULT_BRUSH

        self._build_canvas()
        self._create_controls()
        self._build_overlays()
        self._connect_signals()
        self._install_shortcuts()
        self.graphics_view.refresh_cursor()
        self._style_brush_preview()
        self._sync_history_buttons()
        self._sync_open_controls()
        self._update_status_chip()

    # ================================================================== UI
    def _build_canvas(self) -> None:
        """The view (input to this editor), the scene and its layers, and the paint engine."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics_view = EditorCanvasView(sink=self, parent=self)
        self.scene = QGraphicsScene(self)
        self.graphics_view.setScene(self.scene)
        layout.addWidget(self.graphics_view)

        self.paint = PaintCanvas(
            self.edits,
            lut=self._plane_lut,
            to_plane=self._argb_to_plane,
            fill_source=self._fill_source,
            on_region=self.refresh_mask_region,
            on_replaced=self.refresh_mask_display,
            on_patch=self._on_patch,
        )
        self.image_item = PhotoLayerItem()
        self.image_item.setZValue(0)
        self.scene.addItem(self.image_item)
        # Reads the canvas at paint time: painting a stroke never copies it.
        self.mask_item = RasterLayerItem(lambda: self.paint.image)
        self.mask_item.setZValue(1)
        self.mask_item.setOpacity(self.DEFAULT_OPACITY / 100)
        self.scene.addItem(self.mask_item)
        # Brush ring in scene space, so it matches the stroke at any zoom.
        self.brush_preview = BrushPreview(self.scene)

    def _tip(self, text: str, action: str) -> str:
        return shortcuts.tooltip(text, action, self.SHORTCUT_SCOPE)

    def _create_controls(self) -> None:
        """Construct the editor's own controls (placed on the chrome's rail and dock)."""
        noun = self.EDITOR_NOUN
        # --- tools (rail) ---
        self.brush_button = IconButton(
            "brush", "Brush (B) · hold B + scroll to resize", checkable=True, on_color=tokens.MASK
        )
        self.eraser_button = IconButton("erase", self._tip("Eraser", "eraser"), checkable=True)
        self.fill_button = IconButton(
            "fill", self._tip("Fill a closed outline", "fill"), checkable=True, on_color=tokens.MASK
        )
        self.tool_button_group = QButtonGroup(self)
        self.tool_button_group.setExclusive(True)
        for button in (self.brush_button, self.eraser_button, self.fill_button):
            self.tool_button_group.addButton(button)
        self.brush_button.setChecked(True)
        self.mask_visible_button = IconButton(
            "eye", self._tip(f"Show {noun}", "toggle_overlay"), checkable=True
        )
        self.mask_visible_button.setChecked(True)

        # --- dock ---
        self.size_container = DockSlider(
            "brush", "Brush size ([ and ])", 1, 100, self.DEFAULT_BRUSH, fmt="{} px"
        )
        self.size_slider = self.size_container.slider
        self.size_label = self.size_container.value_label
        self.opacity_container = DockSlider(
            "eye", f"{noun[:1].upper()}{noun[1:]} opacity", 0, 100, self.DEFAULT_OPACITY, fmt="{}%"
        )
        self.opacity_slider = self.opacity_container.slider
        self.opacity_label = self.opacity_container.value_label

        redo_keys = " / ".join(shortcuts.keys_for("redo", self.SHORTCUT_SCOPE))
        self.undo_button = IconButton("undo", self._tip("Undo", "undo"))
        self.redo_button = IconButton("redo", f"Redo ({redo_keys})")
        self.clear_icon_button = IconButton("trash", f"Clear {noun} (undoable)")
        self.save_button = primary_button(
            f"Save {noun}", "save", tooltip=self._tip(f"Save {noun}", "save"), compact=True
        )
        # Text variant of Clear for the topbar action bar (shell_chrome reparents it
        # there); intentionally not placed in the dock.
        self.clear_button = ghost_button(
            f"Clear {noun}", "trash", tooltip=self.CLEAR_TIP, compact=True
        )
        keep_canvas_focus(self.clear_button)
        self.norm_controls = NormalizationControls(self)

    def _build_overlays(self) -> None:
        """The shared chrome (rail, dock, Enhance popover, chip, zoom) with this editor's controls."""
        self.chrome = chrome = EditorChrome(self, self.graphics_view, self.norm_controls)
        self.tool_rail, self.dock, self.enhance_popover = chrome.rail, chrome.dock, chrome.popover
        self.pan_button = chrome.pan_button
        self.mode_toggle = self.pan_button  # legacy name of the Draw / Pan toggle
        self.enhance_button = chrome.enhance_button
        self.status_chip = chrome.chip
        self.file_label, self.state_label = chrome.chip.file_label, chrome.chip.state_label
        self.zoom_cluster = zoom = chrome.zoom
        self.zoom_out_button, self.zoom_pct_label = zoom.zoom_out_button, zoom.zoom_pct_label
        self.zoom_in_button, self.fit_button = zoom.zoom_in_button, zoom.fit_button
        self.overlay_layout = chrome.layout

        rail = self.tool_rail
        for button in (self.brush_button, self.eraser_button, self.fill_button):
            rail.add_widget(button)
        rail.add_separator()
        rail.add_widget(self.pan_button)
        rail.add_widget(self.mask_visible_button)
        rail.add_separator()
        rail.add_widget(self.enhance_button)

        dock = self.dock
        dock.add_widget(self.size_container)
        dock.add_widget(self.opacity_container)
        dock.add_separator()
        for button in (self.undo_button, self.redo_button, self.clear_icon_button):
            dock.add_widget(button)
        dock.add_separator()
        dock.add_widget(self.save_button)

    def _reposition_overlays(self) -> None:
        """Re-place the floating overlays over the current widget bounds."""
        self.overlay_layout.relayout()

    def showEvent(self, event) -> None:
        """Shown: the canvas takes the keys (Space, B, the canvas zoom keys)."""
        super().showEvent(event)
        self.graphics_view.setFocus()

    def _connect_signals(self) -> None:
        """Connect the controls to their handlers (the chrome wires Pan and Enhance)."""
        self.size_slider.valueChanged.connect(self.update_brush_size)
        self.opacity_slider.valueChanged.connect(self.update_opacity)
        self.tool_button_group.buttonToggled.connect(self._on_tool_toggled)
        self.mask_visible_button.toggled.connect(self.mask_item.setVisible)
        self.clear_button.clicked.connect(self.clear_mask)
        self.clear_icon_button.clicked.connect(self.clear_mask)
        self.save_button.clicked.connect(lambda: self.save_mask())
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.norm_controls.enhancement_changed.connect(self.apply_normalization)

    def _install_shortcuts(self) -> None:
        """One QShortcut per registry key of the editor's scope (no key bound twice)."""
        view = self.graphics_view
        installed = shortcuts.install_shortcuts(
            self,
            self.SHORTCUT_SCOPE,
            {
                "undo": self.undo,
                "redo": self.redo,
                "save": lambda: self.save_mask(),
                "eraser": lambda: self.eraser_button.setChecked(True),
                "fill": lambda: self.fill_button.setChecked(True),
                "toggle_overlay": self.mask_visible_button.toggle,
                "brush_smaller": lambda: self.step_brush_size(-1),
                "brush_larger": lambda: self.step_brush_size(1),
                "fit": lambda: view.fit_to_view(),
                "zoom_100": lambda: view.set_zoom(1.0),
                "zoom_in": lambda: view.zoom_step(1),
                "zoom_out": lambda: view.zoom_step(-1),
                "pixel_grid": self._toggle_pixel_grid,
            },
        )
        self._shortcuts = [sc for group in installed.values() for sc in group]
        # Keep the legacy attribute names.
        self.undo_shortcut, self.redo_shortcut = installed["undo"][0], installed["redo"][0]

    def _toggle_pixel_grid(self) -> None:
        """Flip the canvas pixel grid and say so (it only shows at 800 % and above)."""
        on = self.graphics_view.toggle_pixel_grid()
        self.notice.emit(PIXEL_GRID_ON if on else PIXEL_GRID_OFF, "info")

    # ================================================================ canvas input (InputSink)
    def canvas_size(self) -> QSize:
        """The canvas size in image pixels; empty while nothing is open (no input then)."""
        image = self.paint.image
        return QSize() if image is None else image.size()

    def canvas_press(self, pos: QPoint, event: QMouseEvent) -> bool:
        """A left press at ``pos``: start a stroke or fill; True when the drag is the editor's."""
        self.begin_stroke(pos)
        return self.drawing

    def canvas_move(self, pos: QPoint, event: QMouseEvent, dragging: bool) -> None:
        """The pointer is over ``pos``: the ring follows it; a drag extends the stroke."""
        self.update_brush_preview(pos)
        if dragging:
            self.continue_stroke(pos)

    def canvas_release(self, pos: QPoint, event: QMouseEvent) -> None:
        """The drag ended: commit the stroke as one undoable action."""
        self.end_stroke()

    def canvas_double_click(self, pos: QPoint, event: QMouseEvent) -> None:
        """Nothing: its first click already painted one dab (one undo entry)."""

    def canvas_wheel(self, event: QWheelEvent) -> bool:
        """With B held the wheel sizes the brush (consumed); otherwise the view handles it."""
        if not self.b_key_pressed:
            return False
        dy = event.angleDelta().y()
        if dy:
            self.step_brush_size(1 if dy > 0 else -1)
        return True

    def canvas_key_press(self, event: QKeyEvent) -> bool:
        """B goes down: hold it and turn the wheel to size the brush (consumed)."""
        return self._b_key(event, pressed=True)

    def canvas_key_release(self, event: QKeyEvent) -> bool:
        """B comes up: a tap (no wheel while held) picks the brush (consumed)."""
        return self._b_key(event, pressed=False)

    def canvas_hover_left(self) -> None:
        """The pointer left the canvas: hide the brush ring."""
        self.set_brush_preview_visible(False)

    def canvas_pan_changed(self, panning: bool) -> None:
        """A left drag now pans (or paints again): the ring shows only while it paints."""
        self.set_brush_preview_visible(not panning)

    def _b_key(self, event: QKeyEvent, *, pressed: bool) -> bool:
        if event.key() != Qt.Key.Key_B or event.modifiers() not in _PLAIN:
            return False
        if not event.isAutoRepeat():
            self._on_b_key(pressed)
        return True

    def _on_b_key(self, pressed: bool) -> None:
        if pressed:
            self.b_key_pressed = True
            self._b_wheel_used = False
        else:
            self.b_key_pressed = False
            # A tap (no wheel while held) selects the brush tool.
            if not self._b_wheel_used:
                self.brush_button.setChecked(True)
        self.b_key_status_changed.emit(self.b_key_pressed)

    # ================================================================ tools
    def _on_tool_toggled(self, _button, checked: bool) -> None:
        if not checked:
            return
        self._style_brush_preview()
        self._sync_brush_preview()

    def toggle_mode(self, pan: bool | None = None) -> None:
        """Turn the sticky pan mode on or off (default: follow the rail's hand button)."""
        if pan is None:
            pan = self.pan_button.isChecked()
        self.graphics_view.set_mode(bool(pan))

    def step_brush_size(self, direction: int) -> None:
        """Grow / shrink the brush by 12 % (at least 1 px): [ and ], and B + wheel."""
        if self.b_key_pressed:
            self._b_wheel_used = True
        size = self.size_slider.value()
        step = max(1, round(size * 0.12))
        self.size_slider.setValue(size + step * (1 if direction > 0 else -1))

    def update_brush_size(self, value: int) -> None:
        """Set the brush size (image pixels) and its ring."""
        self.brush_size = value
        self._sync_brush_preview()

    def update_opacity(self, value: int) -> None:
        """Set the edit layer's display opacity (0-100 %)."""
        self.mask_item.setOpacity(value / 100)

    # ======================================================= brush preview
    def _style_brush_preview(self) -> None:
        if self.eraser_button.isChecked():
            self.brush_preview.set_style(tokens.DANGER, dashed=True)
        else:
            self.brush_preview.set_style(tokens.TEXT)

    def update_brush_preview(self, pos: QPoint) -> None:
        """Centre the brush ring on image pixel ``pos``."""
        self._last_hover = pos
        self._sync_brush_preview()

    def set_brush_preview_visible(self, visible: bool) -> None:
        """Allow the ring (pointer on the canvas, not panning) or hide it."""
        self._preview_allowed = bool(visible)
        self._sync_brush_preview()

    def _sync_brush_preview(self) -> None:
        show = (
            self._preview_allowed
            and self._last_hover is not None
            and self.paint.image is not None
            and not self.fill_button.isChecked()
        )
        self.brush_preview.set_visible(show)
        if show:
            self.brush_preview.set_diameter(self.brush_size)
            self.brush_preview.move_to_pixel(self._last_hover)

    # ============================================================= strokes
    @property
    def mask_pixmap(self) -> QImage | None:
        """The canvas image (``Format_ARGB32_Premultiplied``); None while nothing is open.

        Painted in place: do not hand it to a Qt object that keeps a copy (such as
        ``setPixmap``), or the next stroke has to copy the whole canvas.
        """
        return self.paint.image

    @property
    def undo_stack(self):
        """Undoable actions, oldest first (one entry per stroke, fill or clear)."""
        return self.edits.undo_stack

    @property
    def redo_stack(self):
        """Undone actions, the next redo last."""
        return self.edits.redo_stack

    def _edit_blocked(self) -> bool:
        """True when edits are refused (subclasses: e.g. nothing could be saved)."""
        return False

    def begin_stroke(self, pos: QPoint) -> None:
        """Start a stroke at image pixel ``pos`` (with Fill: fill the outline around it)."""
        if self.paint.image is None or self._edit_blocked():
            self.drawing = False  # continue_stroke then draws nothing
            return
        self.drawing = True
        if self.fill_button.isChecked():
            if self.flood_fill(pos):
                self.mark_dirty()
                self._after_stroke()
            else:
                self.notice.emit(self.FILL_MISS_NOTICE, "info")
            return
        erase = self.eraser_button.isChecked()
        self.paint.begin(pos, self.brush_color, self.brush_size, erase=erase)
        self.mark_dirty()

    def continue_stroke(self, pos: QPoint) -> None:
        """Paint the segment to image pixel ``pos`` (only during a brush / eraser stroke)."""
        if self.drawing and not self.fill_button.isChecked():
            self.paint.extend(pos)

    def end_stroke(self) -> None:
        """End the stroke: what it painted becomes one undoable action."""
        self.drawing = False
        if self.paint.commit() is not None:
            self._after_stroke()

    def flood_fill(self, pos: QPoint) -> bool:
        """Fill the closed outline around ``pos`` (one undoable action); False outside all."""
        return self.paint.fill(pos) is not None

    def clear_mask(self) -> None:
        """Empty the canvas. Undoable; the saved file changes only on Save."""
        if self.edits.plane is None or self._edit_blocked():
            return
        self.end_stroke()
        self.paint.clear()
        self.mark_dirty()
        self._after_stroke()
        self.notice.emit(self.CLEARED_NOTICE, "info")

    def undo(self) -> None:
        """Undo the last action (ignored while a stroke is in progress)."""
        self._step_history(self.paint.undo)

    def redo(self) -> None:
        """Redo the last undone action (ignored while a stroke is in progress)."""
        self._step_history(self.paint.redo)

    def _step_history(self, step: Callable[[], Patch | None]) -> None:
        if self.drawing or step() is None:
            return
        self.mark_dirty()
        self._after_stroke()

    def _finish_stroke(self) -> None:
        """Commit a stroke still in progress (Ctrl+S while the mouse is down)."""
        if self.drawing:
            self.end_stroke()

    def _on_patch(self, patch: Patch, forward: bool) -> None:
        """Hook: ``patch`` was applied (``forward``) or reverted. No-op here."""

    def _after_stroke(self) -> None:
        """Hook: an action changed the canvas (stroke, fill, clear, undo, redo). No-op here."""

    def _sync_history_buttons(self) -> None:
        self.undo_button.setEnabled(self.edits.can_undo)
        self.redo_button.setEnabled(self.edits.can_redo)

    def refresh_mask_display(self) -> None:
        """Repaint the whole edit layer (the canvas was replaced or changed wholesale)."""
        self.mask_item.canvas_replaced()

    def refresh_mask_region(self, rect: QRect | QRectF) -> None:
        """Repaint ``rect`` (image pixels) of the edit layer, e.g. one stroke segment."""
        if isinstance(rect, QRectF):
            rect = rect.toAlignedRect()
        self.mask_item.canvas_changed(rect)

    # ====================================================== dirty / status
    def mark_dirty(self) -> None:
        """There are unsaved edits (``dirty_changed`` on the transition)."""
        if not self.dirty:
            self.dirty = True
            self.dirty_changed.emit(True)
        self._update_status_chip()

    def _set_clean(self) -> None:
        """The document is its saved state now (saved, opened unchanged or discarded)."""
        self._remember_saved()
        if self.dirty:
            self.dirty = False
            self.dirty_changed.emit(False)
        self._update_status_chip()

    def _remember_saved(self) -> None:
        """Keep a copy of the plane as the saved state, the one Discard goes back to.

        Subclasses extend it to keep the rest of their saved state.
        """
        plane = self.edits.plane
        self._saved_plane = None if plane is None else plane.copy()

    def _restore_saved(self) -> None:
        """Hook: Discard put the saved plane back; restore the rest of the saved state."""

    def revert_to_saved(self) -> None:
        """Discard: back to the document as last saved (or opened); the history is cleared.

        A stroke in progress is dropped with the rest. With nothing open it only marks
        the editor clean.
        """
        saved = self._saved_plane
        if saved is not None and self.edits.plane is not None:
            self._drop_stroke()
            # Adopted as the plane: _set_clean keeps a fresh copy as the saved state.
            self.edits.reset(saved)
            self.paint.rebuild()
            self._restore_saved()
        self._set_clean()

    def has_unsaved_changes(self) -> bool:
        """True when an open image has unsaved edits."""
        return bool(self.dirty and self.current_image_path)

    def _status(self) -> tuple[str, str | None, str]:
        """``(state, label, tooltip)`` for the status chip (see :class:`StatusChip`)."""
        return ("dirty", None, "") if self.dirty else ("none", None, "")

    def _hint(self) -> tuple[str, str]:
        """``(text, tooltip)`` of the chip's hint line: what to do while no image is open."""
        return ("", "") if self.current_image_path else (self.EMPTY_HINT, "")

    def _update_status_chip(self) -> None:
        """Show the file name, the save state and the hint; only a change reaches the chip.

        Every edit asks (twice), and each chip update re-places all the overlays.
        """
        if not self.current_image_path:
            shown = ("No image", ("none", None, ""), self._hint())
        else:
            name = os.path.basename(self.current_image_path)
            shown = (name, self._status(), self._hint())
        old = self._chip_shown or (None, None, None)
        self._chip_shown = shown
        chip = self.status_chip
        if shown[0] != old[0]:
            chip.set_text(shown[0])
        if shown[1] != old[1]:
            state, label, tooltip = shown[1]
            chip.set_state(state, label, tooltip=tooltip)
        if shown[2] != old[2]:
            chip.set_hint(*shown[2])

    def _sync_open_controls(self) -> None:
        """Save and Clear act on the open image: off while none is open."""
        is_open = bool(self.current_image_path)
        for button in (self.save_button, self.clear_icon_button, self.clear_button):
            button.setEnabled(is_open)

    def maybe_save_changes(self) -> bool:
        """Offer to save unsaved edits. Returns True when it is safe to move on.

        True = saved, discarded (the document goes back to its saved state, see
        :meth:`revert_to_saved`), or nothing to save. False = the user chose Cancel, or
        chose Save, the save failed (a notice says why) and they chose "Keep editing": the
        caller must then stay on this image.
        """
        if not self.has_unsaved_changes():
            return True
        noun, name = self.EDITOR_NOUN, os.path.basename(self.current_image_path)
        choice = editor_prompts.ask_unsaved(self, noun, name)
        if choice == editor_prompts.DISCARD:
            self.revert_to_saved()
            return True
        if choice != editor_prompts.SAVE:
            return False
        if self.save_mask():
            return True
        if editor_prompts.ask_after_failed_save(self, noun, name) == editor_prompts.DISCARD:
            self.revert_to_saved()
            return True
        return False

    def reset(self) -> bool:
        """A new folder was opened: prompt, then drop the image and the history.

        Returns False (and keeps everything) when the user kept editing.
        """
        if not self.maybe_save_changes():
            return False
        self.current_image_path = ""
        self._photo_stamp = self._document_stamps = ()
        self._drop_stroke()
        self.edits.reset(None)
        self.paint.rebuild()
        self.image_item.set_image(None)
        self.graphics_view.clear_content_rect()
        self._sync_brush_preview()
        self._sync_open_controls()
        self._set_clean()
        return True

    def _drop_stroke(self) -> None:
        """A new document replaces the canvas: the stroke and the drag in progress end."""
        self.drawing = False
        self.graphics_view.end_gesture()

    # ========================================================= load / save
    @property
    def image_pixmap(self) -> QPixmap | None:
        """The photo as shown (Enhance applied) at full resolution; None when there is none."""
        pixmap = self.image_item.pixmap()
        return None if pixmap.isNull() else pixmap

    def _open_document(self, image_path: str, size: QSize) -> tuple[np.ndarray, bool]:
        """Read the plane of ``image_path`` (a ``size`` photo): ``(uint8 plane, dirty_on_load)``.

        Subclasses implement it.
        """
        raise NotImplementedError

    def _document_opened(self) -> None:
        """Hook: a document was loaded into ``edits.plane``. No-op here."""

    def _document_files(self) -> tuple[str, ...]:
        """Hook: the files the open document was read from (a change on disk reloads it)."""
        return ()

    def _stamp_document(self) -> None:
        """Remember the document files' stamps (after an open or a save of the document)."""
        self._document_stamps = tuple(_file_stamp(path) for path in self._document_files())

    def _document_changed(self) -> bool:
        """True when a document file changed on disk since it was opened or saved here."""
        return tuple(_file_stamp(path) for path in self._document_files()) != self._document_stamps

    def load_image(self, image_path: str) -> None:
        """Open ``image_path`` for editing, with its saved document if one exists.

        Like the Correct editor: the open image again keeps unsaved edits; clean, it keeps
        the document, its history and the view unless a file changed on disk (the document
        reloads, same view; a changed photo opens fitted). Another image is decoded first
        (unreadable: a notice, nothing changes), then unsaved edits are asked about (Cancel
        or a failed save stays). The photo is decoded once, shown with the current
        enhancement, and fitted to the canvas.
        """
        if self.edits.plane is not None and _same_file(image_path, self.current_image_path):
            if self.has_unsaved_changes():
                return  # the open image was re-selected: keep editing, don't reload
            if _file_stamp(self.current_image_path) == self._photo_stamp:
                if self._document_changed():
                    self._open_plane(self.current_image_path, self.canvas_size())
                return  # same photo: keep the view (and, unchanged, the document)

        image = self.norm_controls.display_image(image_path)
        if image is None:
            self.notice.emit(f"Could not open {os.path.basename(image_path)}", "danger")
            return
        if not self.maybe_save_changes():
            return  # the user kept editing the open image
        height, width = image.shape[:2]
        self._photo_stamp = _file_stamp(image_path)
        self._open_plane(image_path, QSize(width, height))
        self.image_item.set_image(image)

        view = self.graphics_view
        view.set_content_rect(QRectF(0, 0, width, height))
        view.fit_to_view()
        if self.isVisible():
            view.setFocus()

    def _open_plane(self, image_path: str, size: QSize) -> None:
        """Read the document of ``image_path`` into the plane, with a new history."""
        plane, dirty_on_load = self._open_document(image_path, size)
        self.current_image_path = image_path
        self._drop_stroke()
        self.edits.reset(plane)
        self.paint.rebuild()
        self._document_opened()
        self._stamp_document()
        self._sync_open_controls()
        if dirty_on_load:
            self._remember_saved()  # Discard goes back to the document as opened
            self.mark_dirty()
        else:
            self._set_clean()

    def save_mask(self, save_path: str | None = None) -> bool:
        """Write the plane of the open image; True when saved. Subclasses implement it."""
        raise NotImplementedError

    # ========================================================= plane <-> canvas
    def _plane_lut(self) -> np.ndarray:
        """``(256, 4)`` uint8 B, G, R, A colour of each plane value. Subclasses implement it."""
        raise NotImplementedError

    def _argb_to_plane(self, argb: np.ndarray, rect: Rect) -> np.ndarray:
        """Plane values of canvas box ``rect`` from its B, G, R, A pixels ``argb``.

        Subclasses implement it.
        """
        raise NotImplementedError

    def _fill_source(self, plane: np.ndarray) -> tuple[np.ndarray, int]:
        """``(uint8 0/255 outline image, plane value to fill with)``. Subclasses implement it."""
        raise NotImplementedError

    # ========================================================= enhancement
    def apply_normalization(self) -> None:
        """Show the open image with the Enhance settings (raw when enhancement is off)."""
        if not self.current_image_path:
            return
        image = self.norm_controls.display_image(self.current_image_path)
        if image is None:
            self.notice.emit("Could not read the image for enhancement", "danger")
            return
        self.image_item.set_image(image)
