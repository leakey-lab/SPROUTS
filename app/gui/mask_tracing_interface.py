"""
Raster editors: the shared canvas editor and the mask tracing interface.

:class:`RasterEditorBase` is everything the "Correct Mask" and "Trace GT"
stages share: the canvas (image + edit layer), tool rail, dock, zoom cluster,
status chip, enhancement popover, brush preview, keyboard shortcuts, patch
undo/redo, dirty tracking and the unsaved-changes prompt. It knows nothing
about where a document is stored. Its two subclasses are siblings:

- :class:`MaskTracingInterface` edits the binary mask of ``mask/<stem>.png``
  through :class:`app.masks.MaskStore`;
- :class:`app.gui.gt_tracing_interface.GtTracingInterface` edits multi-class
  ground truth through :mod:`app.gt` and never touches ``mask/``.

Layout (SPROUTS canvas, placed by :class:`app.gui.widgets.OverlayLayout`):
icon tool rail on the left, filename/status chip top left, enhancement popover
top right, brush/opacity/actions dock bottom centre and a zoom cluster bottom
right. "Fit" keeps the image clear of those overlays.

Keyboard (from :mod:`app.gui.shortcuts`, the registry behind the F1 sheet): B
brush (hold B + wheel = brush size), E eraser, F fill, [ / ] brush size, Space
(hold) pan, H show/hide the edit layer, Ctrl+Z undo, Ctrl+Y / Ctrl+Shift+Z
redo, Ctrl+S save, Ctrl+0 fit, Ctrl+1 100%, Ctrl+= / Ctrl+- zoom,
Ctrl+wheel zoom, middle-drag pan.

The mask is shown in the SPROUTS mask green without antialiasing; on disk it
is still written as white-on-transparent PNG, so every existing reader (which
loads it as grayscale and thresholds) sees the same data as before.
"""

from __future__ import annotations

import logging
import os

import numpy as np
from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.masks import MaskFileError, MaskStore, write_mask_file

from . import shortcuts
from .brush_preview import BrushPreview
from .canvas_item import CanvasPixmapItem
from .image_normalization_interface import NormalizationControls
from .mask_drawing_tools import MaskDrawingMixin
from .mask_graphics_view import MaskTracingGraphicsView
from .patch_history import PatchHistory
from .widgets import (
    DockSlider,
    EnhancePopover,
    FloatingDock,
    IconButton,
    OverlayLayout,
    StatusChip,
    ToolRail,
    ZoomCluster,
    keep_canvas_focus,
    tokens,
)
from .widgets.buttons import ghost_button, primary_button

logger = logging.getLogger(__name__)

_Button = QMessageBox.StandardButton


def _ask(parent, icon, title, text, info, buttons, default, *, rename=None) -> _Button:
    """Modal question; returns the chosen standard button (Esc / close = ``default`` escape)."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(info)
    box.setStandardButtons(buttons)
    box.setDefaultButton(default)
    box.setEscapeButton(_Button.Cancel)
    for button, label in (rename or {}).items():
        box.button(button).setText(label)
    result = box.exec()
    try:
        return _Button(result)
    except ValueError:
        return _Button.Cancel


class RasterEditorBase(QWidget, MaskDrawingMixin):
    """
    Canvas editor over one image and an editable plane (see :mod:`mask_drawing_tools`).

    Subclasses decide what the plane means and where it is stored:

    - ``_open_document(image_path, pixmap) -> (plane, dirty)`` reads it;
    - ``save_mask(save_path=None) -> bool`` writes it;
    - ``_plane_lut()`` / ``_argb_to_plane()`` / ``_fill_source()`` map it to canvas colours;
    - ``_status()`` names its save state for the status chip.
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
    SHORTCUT_SCOPE = shortcuts.CORRECT_MASK
    CLEARED_NOTICE = "Cleared. Ctrl+Z to undo."
    CLEAR_TIP = "Clear the canvas. Undo with Ctrl+Z."

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_image_path = ""
        self.zoom_factor = 1.0
        # One entry per stroke / fill / clear; the committed plane lives here too.
        self.edits = PatchHistory(on_change=self._sync_history_buttons)
        self.b_key_pressed = False
        self._b_wheel_used = False
        self.dirty = False
        self._last_hover = None
        self._preview_allowed = True
        self._stroke_rect = None

        self.last_point = None
        self.drawing = False
        self.brush_color = QColor(tokens.MASK)
        self.brush_size = self.DEFAULT_BRUSH
        self.mask_pixmap = None
        self.image_pixmap = None

        self.initUI()

    # ================================================================== UI
    def initUI(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.graphics_view = MaskTracingGraphicsView(self)
        self.scene = QGraphicsScene(self)
        self.graphics_view.setScene(self.scene)
        main_layout.addWidget(self.graphics_view)

        self.image_item = QGraphicsPixmapItem()
        self.image_item.setZValue(0)
        self.image_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.scene.addItem(self.image_item)

        # Reads mask_pixmap at paint time: painting a stroke never deep-copies it.
        self.mask_item = CanvasPixmapItem(lambda: self.mask_pixmap)
        self.mask_item.setZValue(1)
        self.mask_item.setOpacity(self.DEFAULT_OPACITY / 100)
        self.scene.addItem(self.mask_item)

        # Brush ring in scene space, so it matches the stroke at any zoom.
        self.brush_preview = BrushPreview(self.scene)
        self._preview_outer, self._preview_inner = (
            self.brush_preview.outer,
            self.brush_preview.inner,
        )

        self._create_controls()
        self._build_overlays()
        self._connect_signals()
        self._install_shortcuts()
        self.graphics_view.installEventFilter(self)
        self.graphics_view.refresh_cursor()
        self._style_brush_preview()
        self._sync_history_buttons()
        self._update_status_chip()

    def _tip(self, text: str, action: str) -> str:
        return shortcuts.tooltip(text, action, self.SHORTCUT_SCOPE)

    def _create_controls(self):
        """Construct all editor controls (placed into floating overlays)."""
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

        self.pan_button = IconButton("hand", "Pan (hold Space, or middle-drag)", checkable=True)
        # Legacy name: the Draw/Pan toggle. Checked now means "pan".
        self.mode_toggle = self.pan_button
        self.mask_visible_button = IconButton(
            "eye", self._tip(f"Show {noun}", "toggle_overlay"), checkable=True
        )
        self.mask_visible_button.setChecked(True)
        self.enhance_button = IconButton("contrast", "Image enhancement", checkable=True)

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
        # Text variant of Clear for the topbar action bar (shell_chrome
        # reparents it there); intentionally not placed in the dock.
        self.clear_button = ghost_button(
            f"Clear {noun}", "trash", tooltip=self.CLEAR_TIP, compact=True
        )
        keep_canvas_focus(self.clear_button)

        # Zoom readout is driven by the view; the hidden slider mirrors it
        # (percent) for callers that still set zoom through a slider.
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.zoom_slider.setRange(
            round(MaskTracingGraphicsView.MIN_ZOOM * 100),
            round(MaskTracingGraphicsView.MAX_ZOOM * 100),
        )
        self.zoom_slider.setValue(100)
        self.zoom_slider.hide()

        self.norm_controls = NormalizationControls(self)

    def _build_overlays(self):
        """Build the floating in-window overlays and hand their placement to one layout."""
        self.tool_rail = ToolRail(self)
        self.dock = FloatingDock(self)
        self.enhance_popover = EnhancePopover(self)

        self.tool_rail.add_widget(self.brush_button)
        self.tool_rail.add_widget(self.eraser_button)
        self.tool_rail.add_widget(self.fill_button)
        self.tool_rail.add_separator()
        self.tool_rail.add_widget(self.pan_button)
        self.tool_rail.add_widget(self.mask_visible_button)
        self.tool_rail.add_separator()
        self.tool_rail.add_widget(self.enhance_button)

        self.dock.add_widget(self.size_container)
        self.dock.add_widget(self.opacity_container)
        self.dock.add_separator()
        self.dock.add_widget(self.undo_button)
        self.dock.add_widget(self.redo_button)
        self.dock.add_widget(self.clear_icon_button)
        self.dock.add_separator()
        self.dock.add_widget(self.save_button)

        self.enhance_popover.set_content(self.norm_controls)

        # Top-left: filename + save state.
        self.status_chip = StatusChip(self)
        self.file_label = self.status_chip.file_label
        self.state_label = self.status_chip.state_label

        # Bottom-right: zoom cluster.
        self.zoom_cluster = ZoomCluster(self)
        self.zoom_out_button = self.zoom_cluster.zoom_out_button
        self.zoom_pct_label = self.zoom_cluster.zoom_pct_label
        self.zoom_in_button = self.zoom_cluster.zoom_in_button
        self.fit_button = self.zoom_cluster.fit_button

        self.overlay_layout = OverlayLayout(self)
        self.overlay_layout.register(
            rail=self.tool_rail,
            dock=self.dock,
            popover=self.enhance_popover,
            zoom_cluster=self.zoom_cluster,
            top_chip=self.status_chip,
        )
        # Every fit (button, Ctrl+0, load, resize re-fit) keeps the image clear of them.
        self.graphics_view.set_fit_insets_provider(self.overlay_layout.insets)
        self.zoom_cluster.bind(self.graphics_view)

    def _reposition_overlays(self):
        """Re-place the floating overlays over the current widget bounds."""
        self.overlay_layout.relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self.graphics_view.setFocus()

    def _connect_signals(self):
        """Connect all signals to their handlers."""
        self.size_slider.valueChanged.connect(self.update_brush_size)
        self.opacity_slider.valueChanged.connect(self.update_opacity)
        self.zoom_slider.valueChanged.connect(self.update_zoom)
        self.graphics_view.zoom_changed.connect(self._on_zoom_changed)

        self.tool_button_group.buttonToggled.connect(self._on_tool_toggled)
        self.pan_button.toggled.connect(self.toggle_mode)
        self.mask_visible_button.toggled.connect(self.mask_item.setVisible)
        self.enhance_button.clicked.connect(self.enhance_popover.toggle)
        self.enhance_popover.toggled.connect(self._on_enhance_toggled)
        self.enhance_popover.toggled.connect(self.enhance_button.setChecked)

        self.clear_button.clicked.connect(self.clear_mask)
        self.clear_icon_button.clicked.connect(self.clear_mask)
        self.save_button.clicked.connect(lambda: self.save_mask())
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.norm_controls.enhancement_changed.connect(self.apply_normalization)

    def _install_shortcuts(self):
        """One QShortcut per registry key (no key bound twice: Ctrl+Y used to be dead)."""
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
                "zoom_in": lambda: view.zoom_by(ZoomCluster.ZOOM_IN_STEP),
                "zoom_out": lambda: view.zoom_by(ZoomCluster.ZOOM_OUT_STEP),
            },
        )
        self._shortcuts = [sc for group in installed.values() for sc in group]
        # Keep the legacy attribute names.
        self.undo_shortcut, self.redo_shortcut = installed["undo"][0], installed["redo"][0]

    # ============================================================ keyboard
    def eventFilter(self, obj, event):
        """Hold-keys on the canvas: Space = pan, B = brush (+ wheel resizes)."""
        if obj is getattr(self, "graphics_view", None) and event.type() in (
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        ):
            key = event.key()
            if key in (Qt.Key.Key_Space, Qt.Key.Key_B) and event.modifiers() in (
                Qt.KeyboardModifier.NoModifier,
                Qt.KeyboardModifier.KeypadModifier,
            ):
                if not event.isAutoRepeat():
                    pressed = event.type() == QEvent.Type.KeyPress
                    if key == Qt.Key.Key_Space:
                        self.graphics_view.set_space_pan(pressed)
                    else:
                        self._on_b_key(pressed)
                return True
        return super().eventFilter(obj, event)

    def _on_b_key(self, pressed: bool):
        if pressed:
            self.b_key_pressed = True
            self._b_wheel_used = False
        else:
            self.b_key_pressed = False
            # A tap (no wheel while held) selects the brush tool.
            if not self._b_wheel_used:
                self.brush_button.setChecked(True)
        self.b_key_status_changed.emit(self.b_key_pressed)

    def _on_enhance_toggled(self, shown: bool):
        """Closing the Enhance popover gives the keys back to the canvas."""
        if not shown:
            self.graphics_view.setFocus()

    # ================================================================ tools
    def _on_tool_toggled(self, _button, checked: bool):
        if not checked:
            return
        self._style_brush_preview()
        self._sync_brush_preview()

    def toggle_mode(self, pan: bool | None = None):
        """Toggle the sticky pan mode (the rail's hand button)."""
        if pan is None:
            pan = self.pan_button.isChecked()
        self.graphics_view.set_mode(bool(pan))

    def step_brush_size(self, direction: int):
        """Grow/shrink the brush proportionally (used by [ ], and B + wheel)."""
        if self.b_key_pressed:
            self._b_wheel_used = True
        size = self.size_slider.value()
        step = max(1, round(size * 0.12))
        self.size_slider.setValue(size + step * (1 if direction > 0 else -1))

    def update_brush_size(self, value):
        """Update brush size (image pixels) and its preview ring."""
        self.brush_size = value
        self._sync_brush_preview()

    def update_opacity(self, value):
        """Update the edit layer's display opacity (0-100%)."""
        self.mask_item.setOpacity(value / 100)

    def update_zoom(self, value):
        """Zoom from the (hidden) percent slider."""
        if abs(self.graphics_view.zoom_factor * 100 - value) >= 1:
            self.graphics_view.set_zoom(value / 100)

    def _on_zoom_changed(self, factor: float):
        self.zoom_factor = factor
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(round(factor * 100))
        self.zoom_slider.blockSignals(False)

    # ======================================================= brush preview
    def _style_brush_preview(self):
        erasing = hasattr(self, "eraser_button") and self.eraser_button.isChecked()
        if erasing:
            self.brush_preview.set_style(tokens.DANGER, dashed=True)
        else:
            self.brush_preview.set_style(tokens.TEXT)

    def update_brush_preview(self, pos: QPoint):
        self._last_hover = pos
        self._sync_brush_preview()

    def set_brush_preview_visible(self, visible: bool):
        self._preview_allowed = bool(visible)
        self._sync_brush_preview()

    def _sync_brush_preview(self):
        show = (
            self._preview_allowed
            and self._last_hover is not None
            and self.mask_pixmap is not None
            and not self.fill_button.isChecked()
        )
        self.brush_preview.set_visible(show)
        if show:
            self.brush_preview.set_diameter(self.brush_size)
            self.brush_preview.move_to_pixel(self._last_hover)

    # ============================================================= strokes
    def _edit_blocked(self) -> bool:
        """True when edits are refused (subclasses: e.g. nothing could be saved)."""
        return False

    def begin_stroke(self, pos: QPoint):
        if self.mask_pixmap is None or self._edit_blocked():
            self.drawing = False  # continue_stroke then draws nothing
            return
        self.drawing = True
        self.last_point = None
        self._stroke_rect = None
        if self.fill_button.isChecked():
            if self.flood_fill(pos):
                self.mark_dirty()
                self._after_stroke()
            else:
                self.notice.emit(
                    "Fill works inside a closed outline. Close the loop first.", "info"
                )
            return
        self.draw_point(pos)
        self.mark_dirty()

    def continue_stroke(self, pos: QPoint):
        if self.drawing and not self.fill_button.isChecked():
            self.draw_point(pos)

    def end_stroke(self):
        self.drawing = False
        self.last_point = None
        if self.commit_stroke() is not None:
            self._after_stroke()

    def clear_mask(self):
        """Empty the canvas. Undoable; the saved file changes only on Save."""
        if self.edits.plane is None or self._edit_blocked():
            return
        self.end_stroke()
        self._clear_canvas()
        self.mark_dirty()
        self._after_stroke()
        self.notice.emit(self.CLEARED_NOTICE, "info")

    def _sync_history_buttons(self):
        if hasattr(self, "redo_button"):
            self.undo_button.setEnabled(self.edits.can_undo)
            self.redo_button.setEnabled(self.edits.can_redo)

    # ====================================================== dirty / status
    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.dirty_changed.emit(True)
        self._update_status_chip()

    def _set_clean(self):
        if self.dirty:
            self.dirty = False
            self.dirty_changed.emit(False)
        self._update_status_chip()

    def has_unsaved_changes(self) -> bool:
        return bool(self.dirty and self.current_image_path)

    def _status(self) -> tuple[str, str | None, str]:
        """``(state, label, tooltip)`` for the status chip (see :class:`StatusChip`)."""
        return ("dirty", None, "") if self.dirty else ("none", None, "")

    def _update_status_chip(self):
        chip = getattr(self, "status_chip", None)
        if chip is None:
            return
        if not self.current_image_path:
            chip.set_text("No image")
            chip.set_state("none")
            return
        chip.set_text(os.path.basename(self.current_image_path))
        state, label, tooltip = self._status()
        chip.set_state(state, label, tooltip=tooltip)

    def maybe_save_changes(self) -> bool:
        """Offer to save unsaved edits. Returns True when it is safe to move on.

        True = saved, discarded, or nothing to save. False = the user chose
        Cancel, or chose Save, the save failed (a notice says why) and they
        chose "Keep editing": the caller must then stay on this image.
        """
        if not self.has_unsaved_changes():
            return True
        noun, name = self.EDITOR_NOUN, os.path.basename(self.current_image_path)
        choice = _ask(
            self,
            QMessageBox.Icon.Question,
            f"Unsaved {noun}",
            f"Save changes to the {noun} for {name}?",
            "Discarded edits cannot be recovered.",
            _Button.Save | _Button.Discard | _Button.Cancel,
            _Button.Save,
        )
        if choice == _Button.Discard:
            self._set_clean()
            return True
        if choice != _Button.Save:
            return False
        if self.save_mask():
            return True
        choice = _ask(
            self,
            QMessageBox.Icon.Warning,
            f"{noun[:1].upper()}{noun[1:]} not saved",
            f"The {noun} for {name} could not be saved.",
            "Discard the edits, or keep editing? Discarded edits are lost.",
            _Button.Discard | _Button.Cancel,
            _Button.Cancel,
            rename={_Button.Cancel: "Keep editing"},
        )
        if choice == _Button.Discard:
            self._set_clean()
            return True
        return False

    def reset(self) -> bool:
        """A new folder was opened: prompt, then drop the image and the history.

        Returns False (and keeps everything) when the user kept editing.
        """
        if not self.maybe_save_changes():
            return False
        self.current_image_path = ""
        self.image_pixmap = None
        self.mask_pixmap = None
        self.edits.reset(None)
        self.drawing = False
        self.last_point = None
        self._stroke_rect = None
        self.image_item.setPixmap(QPixmap())
        self.mask_item.sync()
        self.graphics_view.clear_content_rect()
        self._sync_brush_preview()
        self._set_clean()
        return True

    # ========================================================= load / save
    def _open_document(self, image_path: str, pixmap: QPixmap) -> tuple[np.ndarray, bool]:
        """Read the plane of ``image_path``: ``(uint8 (h, w) plane, dirty_on_load)``."""
        raise NotImplementedError

    def _document_opened(self) -> None:
        """Hook: a document was loaded into ``edits.plane``. No-op here."""

    def load_image(self, image_path):
        """Open ``image_path`` for editing, with its saved document if one exists.

        Re-selecting the open image keeps unsaved edits; opening another one
        first offers to save them and stays put when the user keeps editing.
        """
        if self.has_unsaved_changes():
            if image_path == self.current_image_path:
                return  # the open image was re-selected: keep editing, don't reload
            if not self.maybe_save_changes():
                return  # the user kept editing the open image

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.notice.emit(f"Could not open {os.path.basename(image_path)}", "danger")
            return

        plane, dirty_on_load = self._open_document(image_path, pixmap)
        self.current_image_path = image_path
        self.drawing = False
        self.last_point = None
        self._stroke_rect = None
        self.image_pixmap = pixmap
        self.edits.reset(plane)
        self.mask_pixmap = self._plane_to_pixmap()
        self.update_display()
        if self.norm_controls.is_active():
            self.apply_normalization()  # the enhancement sticks across images
        self._document_opened()

        self.graphics_view.set_content_rect(QRectF(self.image_pixmap.rect()))
        self.graphics_view.fit_to_view()
        if dirty_on_load:
            self.mark_dirty()
        else:
            self._set_clean()
        if self.isVisible():
            self.graphics_view.setFocus()

    def update_display(self):
        """Push the current image and edit layer to the scene."""
        if self.image_pixmap is None or self.mask_pixmap is None:
            return
        self.image_item.setPixmap(self.image_pixmap)
        self.mask_item.sync()

    def refresh_mask_display(self):
        """Repaint the whole edit layer (after it was replaced or changed wholesale)."""
        self.mask_item.sync()

    def refresh_mask_region(self, rect: QRectF):
        """Repaint ``rect`` (image pixels) of the edit layer, e.g. one stroke segment."""
        self.mask_item.update(rect)

    def save_mask(self, save_path=None) -> bool:
        raise NotImplementedError

    def _finish_stroke(self) -> None:
        """Commit a stroke still in progress (Ctrl+S while the mouse is down)."""
        if self.drawing:
            self.end_stroke()

    # ========================================================= enhancement
    def apply_normalization(self):
        """Show the current image with the Enhance settings (raw when enhancement is off)."""
        if not self.current_image_path:
            return
        pix = self.norm_controls.display_pixmap(self.current_image_path)
        if pix is None:
            self.notice.emit("Could not read the image for enhancement", "danger")
            return
        self.image_pixmap = pix
        self.image_item.setPixmap(pix)


class MaskTracingInterface(RasterEditorBase):
    """Editor of the binary mask ``mask/<stem>.png`` (brush, eraser, flood fill)."""

    mask_saved = pyqtSignal(str)
    mask_cleared = pyqtSignal(str)

    EDITOR_NOUN = "mask"
    SHORTCUT_SCOPE = shortcuts.CORRECT_MASK
    CLEARED_NOTICE = "Mask cleared. Ctrl+Z to undo, Save to remove the file."
    CLEAR_TIP = "Clear the canvas. Undo with Ctrl+Z; Save removes the file."

    # Plane value 0 = no mask, 1 = mask (shown opaque in brush_color).
    _has_saved_mask = False
    mask_store: MaskStore | None = None

    # ----------------------------------------------------- plane <-> canvas
    def _plane_lut(self) -> np.ndarray:
        c = self.brush_color
        lut = np.zeros((256, 4), np.uint8)
        lut[1:] = (c.blue(), c.green(), c.red(), 255)
        return lut

    def _argb_to_plane(self, argb: np.ndarray, rect) -> np.ndarray:
        return (argb[:, :, 3] > 0).astype(np.uint8)

    def _fill_source(self, plane: np.ndarray) -> tuple[np.ndarray, int]:
        return (plane > 0).astype(np.uint8) * 255, 1

    def _status(self) -> tuple[str, str | None, str]:
        if self.dirty:
            return "dirty", None, ""
        if self._has_saved_mask:
            return "clean", None, ""
        return "none", "No mask", ""

    # ------------------------------------------------------------ storage
    def _open_document(self, image_path: str, pixmap: QPixmap) -> tuple[np.ndarray, bool]:
        self.mask_store = MaskStore.for_image(image_path)
        self._has_saved_mask = False
        height, width = pixmap.height(), pixmap.width()
        plane = np.zeros((height, width), np.uint8)
        try:
            on = self.mask_store.read(image_path)
        except MaskFileError as exc:
            logger.warning("Failed to load existing mask: %s", exc)
            return plane, False
        if on is None:
            return plane, False
        if on.shape != (height, width):
            logger.warning(
                "Mask of %s is %sx%s, image is %sx%s; ignoring it",
                image_path,
                on.shape[1],
                on.shape[0],
                width,
                height,
            )
            self.notice.emit("Saved mask size does not match the image; starting empty", "warn")
            return plane, False
        plane[on] = 1
        self._has_saved_mask = True
        return plane, False

    def reset(self) -> bool:
        if not super().reset():
            return False
        self._has_saved_mask = False
        self.mask_store = None
        self._update_status_chip()
        return True

    def save_mask(self, save_path=None) -> bool:
        """Write the mask as white-on-transparent PNG (atomically; the old file survives errors).

        An empty mask removes the saved file instead (the image then counts as
        having no mask, as before via Clear).
        """
        if self.edits.plane is None or not self.current_image_path:
            return False
        self._finish_stroke()
        on = self.edits.plane > 0
        name = os.path.basename(self.current_image_path)

        if save_path is None and not on.any():
            try:
                removed = self.mask_store.remove(self.current_image_path)
            except OSError as e:
                self.notice.emit(f"Could not remove mask: {e}", "danger")
                return False
            self._has_saved_mask = False
            self._set_clean()
            if removed:
                self.mask_cleared.emit(self.current_image_path)
                self.notice.emit(f"Empty mask: removed saved mask for {name}", "info")
            return True

        path = save_path or self.mask_store.path_for(self.current_image_path)
        try:
            write_mask_file(path, on)
        except (OSError, ValueError) as exc:
            self.notice.emit(f"Could not save mask to {path}: {exc}", "danger")
            return False

        self._has_saved_mask = True
        self._set_clean()
        self.mask_saved.emit(self.current_image_path)
        return True


__all__ = ["MaskTracingInterface", "RasterEditorBase"]
