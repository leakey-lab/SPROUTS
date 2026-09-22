"""
Mask tracing interface for drawing and editing masks.
Provides tools for brush, eraser, and flood fill operations.

Layout (SPROUTS canvas): icon tool rail on the left, filename/status chip top
left, enhancement popover top right, brush/opacity/actions dock bottom centre
and a zoom cluster bottom right.

Keyboard: B brush (hold B + wheel = brush size), E eraser, F fill, [ / ] brush
size, Space (hold) pan, H show/hide mask, Ctrl+S save, Ctrl+0 fit, Ctrl+1
100%, Ctrl+wheel zoom, middle-drag pan.

The mask is shown in the SPROUTS mask green without antialiasing; on disk it
is still written as white-on-transparent PNG, so every existing reader (which
loads it as grayscale and thresholds) sees the same data as before.
"""

import logging
import os
from collections import deque

import cv2
import numpy as np
from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QImage,
    QKeySequence,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGraphicsEllipseItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.gui.widgets import (
    CornerPanel,
    DockSlider,
    EnhancePopover,
    FloatingDock,
    IconButton,
    ToolRail,
    load_icon,
    tokens,
)

from .image_normalization_interface import ImageNormalization, NormalizationControls
from .mask_drawing_tools import MaskDrawingMixin, argb_array_to_pixmap, pixmap_to_argb_array
from .mask_graphics_view import MaskTracingGraphicsView

logger = logging.getLogger(__name__)

MASK_EXTENSIONS = (".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG")

# Grayscale value above which a stored mask pixel counts as foreground.
MASK_THRESHOLD = 200


def _imread(path: str, flags: int):
    """cv2.imread that also works for non-ASCII Windows paths."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


_PRIMARY_QSS = f"""
QPushButton#primaryBtn {{
    background-color: {tokens.ACCENT}; color: {tokens.ACCENT_INK};
    border: none; border-radius: 8px; padding: 0 14px 0 10px;
    min-height: 32px; font-size: 12.5px; font-weight: 600;
}}
QPushButton#primaryBtn:hover {{ background-color: #cfaefa; }}
QPushButton#primaryBtn:pressed {{ background-color: {tokens.ACCENT_PRESS}; }}
"""

_GHOST_QSS = f"""
QPushButton#ghostBtn {{
    background: transparent; color: {tokens.TEXT_MUTED};
    border: 1px solid {tokens.BORDER_STRONG}; border-radius: 8px;
    padding: 0 12px 0 9px; min-height: 30px; font-size: 12.5px;
}}
QPushButton#ghostBtn:hover {{ background-color: {tokens.BG_3}; color: {tokens.TEXT}; }}
"""


class MaskTracingInterface(QWidget, MaskDrawingMixin):
    """
    Main widget for mask tracing with brush, eraser, and flood fill tools.
    Inherits drawing functionality from MaskDrawingMixin.
    """

    mask_saved = pyqtSignal(str)
    mask_cleared = pyqtSignal(str)
    b_key_status_changed = pyqtSignal(bool)
    # (message, kind) for transient feedback; MainWindow routes it to a toast.
    notice = pyqtSignal(str, str)
    dirty_changed = pyqtSignal(bool)

    DEFAULT_BRUSH = 8
    DEFAULT_OPACITY = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_image_path = ""
        self.mask_directory = ""
        self.zoom_factor = 1.0
        # Bounded undo/redo history. deque(maxlen=...) auto-evicts the oldest
        # entry on append in O(1).
        self.max_stack_size = 25
        self.undo_stack = deque(maxlen=self.max_stack_size)
        self.redo_stack = deque(maxlen=self.max_stack_size)
        self.b_key_pressed = False
        self._b_wheel_used = False
        self.dirty = False
        self._has_saved_mask = False
        self._fit_pending = False
        self._last_hover = None
        self._preview_allowed = True

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
        self.graphics_view.setBackgroundBrush(QColor(tokens.BG_0))
        self.scene = QGraphicsScene(self)
        self.graphics_view.setScene(self.scene)
        main_layout.addWidget(self.graphics_view)

        self.image_item = QGraphicsPixmapItem()
        self.image_item.setZValue(0)
        self.image_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.scene.addItem(self.image_item)

        self.mask_item = QGraphicsPixmapItem()
        self.mask_item.setZValue(1)
        self.mask_item.setOpacity(self.DEFAULT_OPACITY / 100)
        self.mask_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.scene.addItem(self.mask_item)

        self._build_brush_preview()
        self._create_controls()
        self._build_overlays()
        self._connect_signals()
        self._install_shortcuts()
        self.graphics_view.installEventFilter(self)
        self.graphics_view.refresh_cursor()
        self._update_status_chip()

    def _build_brush_preview(self):
        """Brush ring drawn in scene space, so it matches the stroke at any zoom."""
        self._preview_outer = QGraphicsEllipseItem()
        outer_pen = QPen(QColor(0, 0, 0, 170), 3)
        outer_pen.setCosmetic(True)
        self._preview_outer.setPen(outer_pen)
        self._preview_inner = QGraphicsEllipseItem()
        for item in (self._preview_outer, self._preview_inner):
            item.setZValue(10)
            item.setVisible(False)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(item)
        self._style_brush_preview()

    def _style_brush_preview(self):
        erasing = hasattr(self, "eraser_button") and self.eraser_button.isChecked()
        pen = QPen(QColor(tokens.DANGER if erasing else "#ffffff"), 1)
        pen.setCosmetic(True)
        if erasing:
            pen.setStyle(Qt.PenStyle.DashLine)
        self._preview_inner.setPen(pen)

    def _create_controls(self):
        """Construct all editor controls (placed into floating overlays)."""
        # --- tools (rail) ---
        self.brush_button = IconButton(
            "brush", "Brush (B) · hold B + scroll to resize", checkable=True, on_color=tokens.MASK
        )
        self.eraser_button = IconButton("erase", "Eraser (E)", checkable=True)
        self.fill_button = IconButton(
            "fill", "Fill a closed outline (F)", checkable=True, on_color=tokens.MASK
        )
        self.tool_button_group = QButtonGroup(self)
        self.tool_button_group.setExclusive(True)
        for button in (self.brush_button, self.eraser_button, self.fill_button):
            self.tool_button_group.addButton(button)
        self.brush_button.setChecked(True)

        self.pan_button = IconButton("hand", "Pan (hold Space, or middle-drag)", checkable=True)
        # Legacy name: the Draw/Pan toggle. Checked now means "pan".
        self.mode_toggle = self.pan_button
        self.mask_visible_button = IconButton("eye", "Show mask (H)", checkable=True)
        self.mask_visible_button.setChecked(True)
        self.enhance_button = IconButton("contrast", "Image enhancement")

        # --- dock ---
        self.size_container = DockSlider(
            "brush", "Brush size ([ and ])", 1, 100, self.DEFAULT_BRUSH, fmt="{} px"
        )
        self.size_slider = self.size_container.slider
        self.size_label = self.size_container.value_label
        self.opacity_container = DockSlider(
            "eye", "Mask opacity", 0, 100, self.DEFAULT_OPACITY, fmt="{}%"
        )
        self.opacity_slider = self.opacity_container.slider
        self.opacity_label = self.opacity_container.value_label

        self.undo_button = IconButton("undo", "Undo (Ctrl+Z)")
        self.redo_button = IconButton("redo", "Redo (Ctrl+Y)")
        self.clear_icon_button = IconButton("trash", "Clear mask (undoable)")

        self.save_button = QPushButton("Save mask")
        self.save_button.setObjectName("primaryBtn")
        self.save_button.setStyleSheet(_PRIMARY_QSS)
        self.save_button.setIcon(load_icon("save", tokens.ACCENT_INK, 16))
        self.save_button.setIconSize(QSize(16, 16))
        self.save_button.setToolTip("Save mask (Ctrl+S)")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)

        # Text variant of Clear for the topbar action bar (shell_chrome
        # reparents it there); intentionally not placed in the dock.
        self.clear_button = QPushButton("Clear mask")
        self.clear_button.setObjectName("ghostBtn")
        self.clear_button.setStyleSheet(_GHOST_QSS)
        self.clear_button.setIcon(load_icon("trash", tokens.TEXT_MUTED, 15))
        self.clear_button.setToolTip("Clear the canvas. Undo with Ctrl+Z; Save removes the file.")

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
        """Build the floating in-window overlays (presentation only)."""
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
        self.status_chip = CornerPanel(self, corner="tl")
        self.status_chip._box.setContentsMargins(11, 5, 11, 5)
        self.status_chip._box.setSpacing(9)
        self.file_label = QLabel("No image")
        self.file_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-family: {tokens.MONO}; font-size: 12px;"
            "background: transparent;"
        )
        self.state_label = QLabel()
        self.state_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_chip.add_widget(self.file_label)
        self.status_chip.add_widget(self.state_label)

        # Bottom-right: zoom cluster.
        self.zoom_cluster = CornerPanel(self, corner="br", v_margin=18)
        self.zoom_out_button = IconButton("zoomOut", "Zoom out (Ctrl+-)")
        self.zoom_in_button = IconButton("zoomIn", "Zoom in (Ctrl+=)")
        self.fit_button = IconButton("fit", "Fit to window (Ctrl+0)")
        self.zoom_pct_label = QLabel("100%")
        self.zoom_pct_label.setFixedWidth(50)
        self.zoom_pct_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_pct_label.setToolTip("Actual pixels (Ctrl+1)")
        self.zoom_pct_label.setStyleSheet(
            f"color: {tokens.TEXT}; font-family: {tokens.MONO}; font-size: 12px;"
            "background: transparent;"
        )
        self.zoom_cluster.add_widget(self.zoom_out_button)
        self.zoom_cluster.add_widget(self.zoom_pct_label)
        self.zoom_cluster.add_widget(self.zoom_in_button)
        self.zoom_cluster.add_separator()
        self.zoom_cluster.add_widget(self.fit_button)

        # Position overlays once the initial layout has settled.
        QTimer.singleShot(0, self._reposition_overlays)

    def _overlays(self):
        return [
            getattr(self, name, None)
            for name in ("tool_rail", "dock", "enhance_popover", "status_chip", "zoom_cluster")
        ]

    def _reposition_overlays(self):
        """Reposition the floating overlays over the current widget bounds."""
        for overlay in self._overlays():
            if overlay is not None:
                overlay.reposition()
                overlay.raise_()

    def resizeEvent(self, event):
        """Keep floating overlays positioned on widget resize."""
        super().resizeEvent(event)
        self._reposition_overlays()
        self._fit_if_pending()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_if_pending)
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

        self.clear_button.clicked.connect(self.clear_mask)
        self.clear_icon_button.clicked.connect(self.clear_mask)
        self.save_button.clicked.connect(lambda: self.save_mask())
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.norm_controls.apply_button.clicked.connect(self.apply_normalization)

        self.zoom_in_button.clicked.connect(lambda: self.graphics_view.zoom_by(1.25))
        self.zoom_out_button.clicked.connect(lambda: self.graphics_view.zoom_by(0.8))
        self.fit_button.clicked.connect(self.graphics_view.fit_to_view)

    def _install_shortcuts(self):
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        bindings = [
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
            ("Ctrl+Y", self.redo),
            (QKeySequence.StandardKey.Save, lambda: self.save_mask()),
            ("E", lambda: self.eraser_button.setChecked(True)),
            ("F", lambda: self.fill_button.setChecked(True)),
            ("H", self.mask_visible_button.toggle),
            ("[", lambda: self.step_brush_size(-1)),
            ("]", lambda: self.step_brush_size(1)),
            ("Ctrl+0", self.graphics_view.fit_to_view),
            ("Ctrl+1", lambda: self.graphics_view.set_zoom(1.0)),
            ("Ctrl+=", lambda: self.graphics_view.zoom_by(1.25)),
            ("Ctrl++", lambda: self.graphics_view.zoom_by(1.25)),
            ("Ctrl+-", lambda: self.graphics_view.zoom_by(0.8)),
        ]
        self._shortcuts = []
        for key, slot in bindings:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(context)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)
        # Keep the legacy attribute names.
        self.undo_shortcut, self.redo_shortcut = self._shortcuts[0], self._shortcuts[1]

    # ============================================================ keyboard
    def eventFilter(self, obj, event):
        """Hold-keys on the canvas: Space = pan, B = brush (+ wheel resizes)."""
        if obj is self.graphics_view and event.type() in (
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
        """Update mask display opacity (0-100%)."""
        self.mask_item.setOpacity(value / 100)

    def update_zoom(self, value):
        """Zoom from the (hidden) percent slider."""
        if abs(self.graphics_view.zoom_factor * 100 - value) >= 1:
            self.graphics_view.set_zoom(value / 100)

    def _on_zoom_changed(self, factor: float):
        self.zoom_factor = factor
        self.zoom_pct_label.setText(f"{round(factor * 100)}%")
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(round(factor * 100))
        self.zoom_slider.blockSignals(False)

    def _fit_if_pending(self):
        if self._fit_pending and self.image_pixmap and self.graphics_view.viewport().width() > 2:
            self._fit_pending = False
            self.graphics_view.fit_to_view()

    # ======================================================= brush preview
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
        for item in (self._preview_outer, self._preview_inner):
            item.setVisible(show)
        if not show:
            return
        r = self.brush_size / 2
        cx, cy = self._last_hover.x() + 0.5, self._last_hover.y() + 0.5
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        self._preview_outer.setRect(rect)
        self._preview_inner.setRect(rect)

    # ============================================================= strokes
    def begin_stroke(self, pos: QPoint):
        self.drawing = True
        self.last_point = None
        if self.fill_button.isChecked():
            snapshot = self.mask_pixmap.copy()
            if self.flood_fill(pos):
                self.undo_stack.append(snapshot)
                self.redo_stack.clear()
                self.mark_dirty()
            else:
                self.notice.emit(
                    "Fill works inside a closed outline. Close the loop first.", "info"
                )
            return
        self.save_for_undo()
        self.draw_point(pos)
        self.mark_dirty()

    def continue_stroke(self, pos: QPoint):
        if self.drawing and not self.fill_button.isChecked():
            self.draw_point(pos)

    def end_stroke(self):
        self.drawing = False
        self.last_point = None

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

    def _update_status_chip(self):
        if not hasattr(self, "state_label"):
            return
        if not self.current_image_path:
            name, text, color = "No image", "", tokens.TEXT_FAINT
        else:
            name = os.path.basename(self.current_image_path)
            if self.dirty:
                text, color = "Unsaved", tokens.WARN
            elif self._has_saved_mask:
                text, color = "Saved", tokens.OK
            else:
                text, color = "No mask", tokens.TEXT_FAINT
        self.file_label.setText(name)
        self.state_label.setText(
            f'<span style="color:{color}; font-size:11.5px;">● {text}</span>' if text else ""
        )
        self.state_label.setVisible(bool(text))
        self.status_chip.reposition()

    def maybe_save_changes(self):
        """If the current mask has unsaved edits, ask whether to save them."""
        if not self.has_unsaved_changes():
            return
        name = os.path.basename(self.current_image_path)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved mask")
        box.setText(f"Save changes to the mask for {name}?")
        box.setInformativeText("Discarded edits cannot be recovered.")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        if box.exec() == QMessageBox.StandardButton.Save:
            self.save_mask()
        else:
            self._set_clean()

    def reset(self):
        """Drop the current image and history (e.g. a new folder was opened)."""
        self.maybe_save_changes()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.last_point = None
        self.drawing = False
        if self.mask_pixmap:
            self.mask_pixmap.fill(Qt.GlobalColor.transparent)
            self.refresh_mask_display()
        self._set_clean()

    # ========================================================= load / save
    def find_mask_path(self, image_path):
        """Find the mask file saved next to ``image_path`` (``mask/<stem>.<ext>``)."""
        base_name = os.path.splitext(os.path.basename(os.path.normpath(image_path)))[0]
        mask_directory = os.path.join(os.path.dirname(image_path), "mask")
        for ext in MASK_EXTENSIONS:
            mask_path = os.path.normpath(os.path.join(mask_directory, base_name + ext))
            if os.path.exists(mask_path):
                return mask_path
        return None

    def load_image(self, image_path):
        """Open ``image_path`` for editing, with its saved mask if one exists."""
        if image_path != self.current_image_path:
            self.maybe_save_changes()

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.notice.emit(f"Could not open {os.path.basename(image_path)}", "danger")
            return

        self.current_image_path = image_path
        self.mask_directory = os.path.join(os.path.dirname(image_path), "mask")
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.drawing = False
        self.last_point = None

        self.image_pixmap = pixmap
        self.mask_pixmap = QPixmap(pixmap.size())
        self.mask_pixmap.fill(Qt.GlobalColor.transparent)
        self._has_saved_mask = False

        mask_path = self.find_mask_path(image_path)
        if mask_path:
            gray = _imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if gray is None:
                logger.warning("Failed to load existing mask %s", mask_path)
            elif gray.shape[:2] != (pixmap.height(), pixmap.width()):
                logger.warning(
                    "Mask %s is %sx%s, image is %sx%s; ignoring it",
                    mask_path,
                    gray.shape[1],
                    gray.shape[0],
                    pixmap.width(),
                    pixmap.height(),
                )
                self.notice.emit("Saved mask size does not match the image; starting empty", "warn")
            else:
                self.mask_pixmap = self._mask_to_pixmap(gray > MASK_THRESHOLD)
                self._has_saved_mask = True

        self.update_display()
        self.graphics_view.set_content_rect(QRectF(self.image_pixmap.rect()))
        self._fit_pending = True
        self._fit_if_pending()
        self._set_clean()
        self._update_status_chip()
        if self.isVisible():
            self.graphics_view.setFocus()

    def _mask_to_pixmap(self, on: np.ndarray) -> QPixmap:
        """Boolean mask -> display pixmap (opaque brush colour where True)."""
        c = self.brush_color
        arr = np.zeros((*on.shape, 4), dtype=np.uint8)
        arr[on] = (c.blue(), c.green(), c.red(), 255)
        return argb_array_to_pixmap(arr)

    def update_display(self):
        """Push the current image and mask pixmaps to the scene."""
        if not self.image_pixmap or not self.mask_pixmap:
            return
        self.image_item.setPixmap(self.image_pixmap)
        self.mask_item.setPixmap(self.mask_pixmap)

    def refresh_mask_display(self):
        """Push only the mask pixmap (called on every stroke segment)."""
        if self.mask_pixmap:
            self.mask_item.setPixmap(self.mask_pixmap)

    def _mask_save_path(self) -> str:
        stem = os.path.splitext(os.path.basename(self.current_image_path))[0]
        return os.path.normpath(os.path.join(self.mask_directory, stem + ".png"))

    def _remove_saved_masks(self) -> bool:
        stem = os.path.splitext(os.path.basename(self.current_image_path))[0]
        removed = False
        for ext in MASK_EXTENSIONS:
            path = os.path.join(self.mask_directory, stem + ext)
            if os.path.exists(path):
                os.remove(path)
                removed = True
        return removed

    def save_mask(self, save_path=None) -> bool:
        """Write the mask as white-on-transparent PNG.

        An empty mask removes the saved file instead (the image then counts as
        having no mask, as before via Clear).
        """
        if not (self.mask_pixmap and self.current_image_path):
            return False

        on = pixmap_to_argb_array(self.mask_pixmap)[:, :, 3] > 0
        name = os.path.basename(self.current_image_path)

        if save_path is None and not on.any():
            try:
                removed = self._remove_saved_masks()
            except OSError as e:
                self.notice.emit(f"Could not remove mask: {e}", "danger")
                return False
            self._has_saved_mask = False
            self._set_clean()
            if removed:
                self.mask_cleared.emit(self.current_image_path)
                self.notice.emit(f"Empty mask: removed saved mask for {name}", "info")
            return True

        path = save_path or self._mask_save_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = np.zeros((*on.shape, 4), dtype=np.uint8)
        out[on] = 255
        height, width = on.shape
        image = QImage(out.data, width, height, width * 4, QImage.Format.Format_ARGB32)
        if not image.save(path, "PNG", 100):
            self.notice.emit(f"Could not save mask to {path}", "danger")
            return False

        self._has_saved_mask = True
        self._set_clean()
        self.mask_saved.emit(self.current_image_path)
        return True

    def clear_mask(self):
        """Empty the canvas. Undoable; the saved file is only removed on Save."""
        if not self.mask_pixmap:
            return
        self.save_for_undo()
        self.mask_pixmap.fill(Qt.GlobalColor.transparent)
        self.refresh_mask_display()
        self.mark_dirty()
        self.notice.emit("Mask cleared. Ctrl+Z to undo, Save to remove the file.", "info")

    # ========================================================= enhancement
    def apply_normalization(self):
        """Apply image normalization/enhancement to the displayed image."""
        if not self.current_image_path:
            return

        img = _imread(self.current_image_path, cv2.IMREAD_COLOR)
        if img is None:
            self.notice.emit("Could not read the image for enhancement", "danger")
            return

        method = self.norm_controls.method_combo.currentText()
        if method == "CLAHE":
            clip_limit = self.norm_controls.clip_slider.value() / 10.0
            tile_size = self.norm_controls.tile_slider.value()
            enhanced = ImageNormalization.apply_clahe(
                img, clip_limit=clip_limit, tile_size=(tile_size, tile_size)
            )
        else:  # Contrast Stretching
            lower = self.norm_controls.lower_slider.value()
            upper = self.norm_controls.upper_slider.value()
            enhanced = ImageNormalization.apply_contrast_stretching(
                img, lower_percentile=lower, upper_percentile=upper
            )

        rgb_img = np.ascontiguousarray(cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB))
        height, width, _ = rgb_img.shape
        q_img = QImage(rgb_img.data, width, height, width * 3, QImage.Format.Format_RGB888)
        self.image_pixmap = QPixmap.fromImage(q_img)
        self.image_item.setPixmap(self.image_pixmap)
