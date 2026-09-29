"""
Skeleton correction editor (Select / Eraser / Polyline / Connect), kept isolated
from the app's skeleton generation and measurement streams.

Key properties:
- Opening an image also opens its skeleton: the saved correction
  ``<images_folder>/skeleton_corrections/<stem>_skeleton.png`` when there is one,
  else the generated ``skeletons/<stem>_fake.png`` as a read-only starting point
  (:mod:`app.gui.skeleton_document`). The Load button (dock) opens any other file.
- Editing happens on an internal 1 px skeleton raster
  (:class:`~app.gui.skeleton_graph_model.SkeletonCorrectionModel`); the tools live in
  :mod:`app.gui.skeleton_tools`, the overlay and previews in
  :mod:`app.gui.skeleton_overlay`.
- Saving writes only ``skeleton_corrections/<stem>_skeleton.png`` (white on black,
  atomically). Unsaved edits are guarded like the mask and GT editors:
  :meth:`has_unsaved_changes` / :meth:`maybe_save_changes` (Save / Discard / Cancel).
"""

from __future__ import annotations

import logging
import os

import cv2
from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from app.config import SKELETON_OUTPUT_SIZE
from app.gui import shortcuts
from app.gui.widgets import (
    DockSlider,
    EnhancePopover,
    FloatingDock,
    IconButton,
    OverlayLayout,
    StatusChip,
    ToolRail,
    ZoomCluster,
    keep_canvas_focus,
    load_icon,
    tokens,
)
from app.gui.widgets.buttons import ghost_button, primary_button
from app.io_utils import imread_unicode, imwrite_unicode

from . import skeleton_tools as tools
from .brush_preview import BrushPreview
from .image_normalization_interface import NormalizationControls
from .skeleton_correction_graphics_view import SkeletonCorrectionGraphicsView
from .skeleton_document import (
    CORRECTED,
    DISCARD,
    GENERATED,
    SAVE,
    GeneratedResolver,
    SkeletonFiles,
    ask_unsaved,
)
from .skeleton_graph_model import SkeletonCorrectionModel
from .skeleton_overlay import SkeletonDecorations, SkeletonOverlayRenderer

logger = logging.getLogger(__name__)

LOADED = "loaded"  # skeleton_source of a file opened with the Load button

_TOOL_QT_KEYS = {getattr(Qt.Key, f"Key_{k}"): tool for tool, k in tools.TOOL_KEYS.items()}
_SMOOTH_QT_KEY = getattr(Qt.Key, f"Key_{tools.SMOOTH_KEY}")
_NO_TOOL_KEY_MODS = (
    Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
)


class SkeletonCorrectionInterface(QWidget):
    """Main widget for skeleton correction editing."""

    skeleton_saved = pyqtSignal(str)
    dirty_changed = pyqtSignal(bool)
    notice = pyqtSignal(str, str)  # (message, kind): success | info | warn | danger

    TOOL_SELECT = tools.SELECT
    TOOL_ERASER = tools.ERASER
    TOOL_POLYLINE = tools.POLYLINE
    TOOL_CONNECT = tools.CONNECT

    EDITOR_NOUN = "skeleton"
    SHORTCUT_SCOPE = shortcuts.CORRECT_SKELETON
    RENDER_INTERVAL_MS = 16  # eraser drags repaint at most once per frame
    ERASER_RANGE = (1, 80)
    ERASER_KEY_STEP = 2

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_image_path: str = ""
        self.current_image_name: str = ""
        self.images_base_folder: str | None = None
        self.skeleton_source: str | None = None  # CORRECTED | GENERATED | LOADED | None

        self._save_size: tuple[int, int] | None = None  # (w, h) of the opened raster
        self._original_image_pixmap: QPixmap | None = None
        self._generated_resolver: GeneratedResolver | None = None
        self._dirty = False
        self._save_error = ""
        self._error_revision = -1

        self.model = SkeletonCorrectionModel()
        self.draft = tools.PolylineDraft()
        self.tools = tools.build_tools(self)
        self.current_tool = self.TOOL_SELECT
        self.pan_mode = False

        # The eraser slider is the *diameter* in image pixels (the ring shows it);
        # the erase radius is half of it.
        self.eraser_radius = 20
        self.draw_thickness = 3
        self.overlay_opacity = 0.85

        self._renderer = SkeletonOverlayRenderer()
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(self.RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(lambda: self.render_skeleton(full=False))

        self._build_ui()
        installed = shortcuts.install_shortcuts(
            self, self.SHORTCUT_SCOPE, {"undo": self.undo, "redo": self.redo}
        )
        self.undo_shortcut, self.redo_shortcut = installed["undo"][0], installed["redo"][0]
        self._refresh_chip()
        self.refresh_hint()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.graphics_view = SkeletonCorrectionGraphicsView(self)
        self.scene = QGraphicsScene()
        self.graphics_view.setScene(self.scene)
        self.graphics_view.installEventFilter(self)  # hide the eraser ring on Leave
        main_layout.addWidget(self.graphics_view)

        self.image_item = QGraphicsPixmapItem()
        self.image_item.setZValue(0)
        self.scene.addItem(self.image_item)

        self.skeleton_item = QGraphicsPixmapItem()
        self.skeleton_item.setZValue(1)
        self.skeleton_item.setOpacity(self.overlay_opacity)
        self.scene.addItem(self.skeleton_item)

        self.decorations = SkeletonDecorations(self.scene)
        self.eraser_ring = BrushPreview(self.scene)
        self.eraser_ring.set_style(tokens.DANGER, dashed=True)

        self._build_overlays()
        self._create_control_panel()

        self.overlay_layout = OverlayLayout(self.graphics_view)
        self.overlay_layout.register(
            rail=self.tool_rail,
            dock=self.dock,
            popover=self.enhance_popover,
            zoom_cluster=self.zoom_cluster,
            top_chip=self.hint_panel,
            prompt=self.polyline_prompt,
        )
        self.graphics_view.set_fit_insets_provider(self.overlay_layout.insets)
        self.zoom_cluster.bind(self.graphics_view)

    def _build_overlays(self) -> None:
        """Rail (tools), dock (sliders + actions), Enhance popover, zoom cluster,
        file/state chip and the top-centre polyline prompt, all over the canvas."""
        host = self.graphics_view
        self.tool_rail = ToolRail(host)
        self.dock = FloatingDock(host)
        self.enhance_popover = EnhancePopover(host)
        self.zoom_cluster = ZoomCluster(host)
        # File name + save state, with the tool hint on its second line.
        self.hint_panel = StatusChip(host)
        self.status_label = self.hint_panel.hint_label

        self.polyline_prompt = QFrame(host)
        self.polyline_prompt.setObjectName("polylinePrompt")
        self.polyline_prompt.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.polyline_prompt.setStyleSheet(f"""
            QFrame#polylinePrompt {{
                background-color: {tokens.BG_1};
                border: 1px solid {tokens.BORDER_STRONG};
                border-radius: 13px;
            }}
        """)
        self._polyline_prompt_layout = QHBoxLayout(self.polyline_prompt)
        self._polyline_prompt_layout.setContentsMargins(6, 6, 6, 6)
        self._polyline_prompt_layout.setSpacing(3)
        self.polyline_prompt.hide()

        # --- Tools -> rail (key in every tooltip) ---
        def tool_button(icon: str, tip: str) -> IconButton:
            return IconButton(icon, tip, checkable=True, on_color=tokens.SKEL)

        keys = tools.TOOL_KEYS
        self.select_button = tool_button(
            "cursor", f"Select ({keys[tools.SELECT]}) · pick a branch, Delete removes it"
        )
        self.eraser_button = tool_button("erase", f"Eraser ({keys[tools.ERASER]}) · [ ] resize")
        self.polyline_button = tool_button(
            "polyline", f"Polyline ({keys[tools.POLYLINE]}) · Enter finish · Esc cancel"
        )
        self.connect_button = tool_button("link", f"Connect endpoints ({keys[tools.CONNECT]})")
        self._tool_buttons = {
            tools.SELECT: self.select_button,
            tools.ERASER: self.eraser_button,
            tools.POLYLINE: self.polyline_button,
            tools.CONNECT: self.connect_button,
        }
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        for button in self._tool_buttons.values():
            self.tool_group.addButton(button)
            self.tool_rail.add_widget(button)
        self.select_button.setChecked(True)

        self.tool_rail.add_separator()
        # Checked = pan (the mask editor's hand button); Space-hold and middle-drag
        # pan in every tool.
        self.mode_toggle = IconButton(
            "hand", "Pan (hold Space, or middle-drag)", checkable=True, on_color=tokens.SKEL
        )
        self.tool_rail.add_widget(self.mode_toggle)
        self.smooth_polyline_toggle = tool_button(
            "smooth", f"Smooth polyline ({tools.SMOOTH_KEY}) · needs 3+ points"
        )
        self.smooth_polyline_toggle.setEnabled(False)  # Polyline tool only
        self.tool_rail.add_widget(self.smooth_polyline_toggle)

    def _reposition_overlays(self) -> None:
        """Place the floating overlays now (the layout also follows resizes by itself)."""
        self.overlay_layout.relayout()

    def _create_control_panel(self) -> None:
        """Construct the dock, prompt and Enhance controls and wire every signal."""
        scope = self.SHORTCUT_SCOPE
        # Opening an image already opens its skeleton, so Load is a secondary icon action.
        self.load_skeleton_button = IconButton("load", "Load another skeleton file…")
        self.save_skeleton_button = primary_button(
            "Save skeleton", "save", tooltip="Save to skeleton_corrections/ (Ctrl+S)", compact=True
        )
        self.undo_button = IconButton("undo", shortcuts.tooltip("Undo", "undo", scope))
        self.redo_button = IconButton("redo", shortcuts.tooltip("Redo", "redo", scope))
        self.clear_button = IconButton("trash", "Clear all skeleton lines")

        self.finish_polyline_button = primary_button("Finish line (Enter)", "check", compact=True)
        self.cancel_polyline_button = ghost_button("Cancel (Esc)", compact=True)
        self.cancel_polyline_button.setIcon(load_icon("xmark", tokens.DANGER, 16))
        self._polyline_prompt_layout.addWidget(self.finish_polyline_button)
        self._polyline_prompt_layout.addWidget(self.cancel_polyline_button)
        keep_canvas_focus(self.polyline_prompt)

        low, high = self.ERASER_RANGE
        self.eraser_container = DockSlider(
            "erase", "Eraser size ([ and ])", low, high, self.eraser_radius, fmt="{} px"
        )
        self.eraser_slider = self.eraser_container.slider
        self.eraser_label = self.eraser_container.value_label
        self.dock.add_widget(self.eraser_container)
        self.eraser_container.setVisible(False)

        self.opacity_container = DockSlider(
            "overlay",
            "Skeleton overlay opacity",
            5,
            100,
            int(self.overlay_opacity * 100),
            fmt="{}%",
        )
        self.opacity_slider = self.opacity_container.slider
        self.opacity_label = self.opacity_container.value_label
        self.dock.add_widget(self.opacity_container)

        self.dock.add_separator()
        self.dock.add_widget(self.load_skeleton_button)
        self.dock.add_widget(self.undo_button)
        self.dock.add_widget(self.redo_button)
        self.dock.add_widget(self.clear_button)
        self.dock.add_separator()
        self.dock.add_widget(self.save_skeleton_button)

        self.tool_group.buttonClicked.connect(self._on_tool_changed)
        self.load_skeleton_button.clicked.connect(self.load_skeleton_via_dialog)
        self.save_skeleton_button.clicked.connect(lambda: self.save_skeleton())
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.clear_button.clicked.connect(self.clear_skeleton)
        self.finish_polyline_button.clicked.connect(self.finish_polyline)
        self.cancel_polyline_button.clicked.connect(self.cancel_polyline)
        self.eraser_slider.valueChanged.connect(self._on_eraser_radius_changed)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.mode_toggle.toggled.connect(self._on_mode_toggle)
        self.smooth_polyline_toggle.toggled.connect(self._on_smooth_polyline_toggled)

        # Image enhancement -> top-right popover, toggled from the rail.
        self.norm_controls = NormalizationControls(self)
        self.norm_controls.enhancement_changed.connect(self.apply_normalization)
        self.enhance_popover.set_content(self.norm_controls)
        self.enhance_button = IconButton("contrast", "Image enhancement", checkable=True)
        self.enhance_button.clicked.connect(self.enhance_popover.toggle)
        self.enhance_popover.toggled.connect(self.enhance_button.setChecked)
        self.tool_rail.add_separator()
        self.tool_rail.add_widget(self.enhance_button)

    # ------------------------------------------------------------------ host API (tools)
    def clamp_to_image(self, pt: QPoint) -> QPoint:
        w, h = self.image_size()
        if w <= 0 or h <= 0:
            return QPoint(0, 0)
        return QPoint(max(0, min(pt.x(), w - 1)), max(0, min(pt.y(), h - 1)))

    def image_size(self) -> tuple[int, int]:
        pix = self.image_item.pixmap()
        return (0, 0) if pix is None or pix.isNull() else (pix.width(), pix.height())

    def eraser_footprint_radius(self) -> int:
        """Erase radius in image pixels (half the slider diameter, as the ring shows)."""
        return max(1, int(round(float(self.eraser_radius) / 2.0)))

    def render_skeleton(self, *, full: bool = True) -> None:
        """Repaint the overlay; ``full`` also refreshes endpoints, dirty state and hint."""
        self.skeleton_item.setPixmap(self._renderer.pixmap(self.model.mask))
        if not full:
            return
        mask = self.model.mask
        self.decorations.set_endpoints([] if mask is None else self.model.endpoints())
        self.decorations.set_endpoints_visible(True)
        self._sync_dirty()
        self.refresh_hint()

    def schedule_render(self) -> None:
        """Coalesce drag repaints: one overlay rebuild per RENDER_INTERVAL_MS (PE-02)."""
        if not self._render_timer.isActive():
            self._render_timer.start()

    def cancel_scheduled_render(self) -> None:
        self._render_timer.stop()

    def refresh_draft(self, cursor: QPoint | None = None) -> None:
        """Redraw the polyline preview and handles, the prompt and the hint."""
        if self.draft.points:
            self.decorations.show_polyline(self.draft.points, cursor, smooth=self.draft.smooth)
        else:
            self.decorations.clear_polyline()
        self._update_polyline_buttons_enabled()

    def refresh_hint(self) -> None:
        """Show a short, context-sensitive hint under the file name."""
        if not self.current_image_path:
            self._set_status("Select an image to correct its skeleton.")
            return
        empty = self.model.mask is None or not self.model.mask.any()
        if empty and not self.draft.points and self.current_tool != self.TOOL_POLYLINE:
            self._set_status(
                "No skeleton yet · Generate, Load one, or draw with Polyline (P)",
                "Generate skeletons first, open a file with Load (dock), or draw the roots "
                "with the Polyline tool.",
            )
            return
        self._set_status(*self.tools[self.current_tool].hint())

    def switch_tool(self, tool_id: str, *, keep_draft: bool = False) -> None:
        """Make ``tool_id`` the active tool (``keep_draft``: Select hands a branch over)."""
        self._tool_buttons[tool_id].setChecked(True)
        self._activate_tool(tool_id, keep_draft=keep_draft)

    def show_eraser_ring(self, pos: QPoint | None) -> None:
        """Show the eraser footprint at ``pos`` (hidden for None, other tools and pan)."""
        show = pos is not None and self.current_tool == self.TOOL_ERASER and not self.pan_mode
        if show:
            self.eraser_ring.set_diameter(self.eraser_radius)
            self.eraser_ring.move_to_pixel(pos)
        self.eraser_ring.set_visible(show)

    def notify(self, message: str, kind: str = "info") -> None:
        self.notice.emit(message, kind)

    # ------------------------------------------------------------------ view callbacks
    def on_zoom_changed(self, _zoom_factor: float) -> None:
        """Called by the view on every zoom change (the ring is scene-space: nothing to do)."""

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Leave and obj is getattr(self, "graphics_view", None):
            self.show_eraser_ring(None)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ document lifecycle
    def set_generated_skeleton_resolver(self, resolver: GeneratedResolver | None) -> None:
        """Inject ``image_path -> generated skeleton path | None`` (e.g. from the image
        manager's processed folder); ``<folder>/skeletons/<stem>_fake.png`` otherwise."""
        self._generated_resolver = resolver

    def _files(self) -> SkeletonFiles:
        return SkeletonFiles(
            self.current_image_path,
            self.images_base_folder or os.path.dirname(self.current_image_path),
            self._generated_resolver,
        )

    def has_unsaved_changes(self) -> bool:
        return bool(self.current_image_path) and self.model.has_unsaved_changes()

    def maybe_save_changes(self) -> bool:
        """Offer to save unsaved edits. True when it is safe to move on.

        Save writes the correction (False if that fails: the notice says why and the
        edits stay); Discard reverts to the file on disk; Cancel returns False.
        """
        if not self.has_unsaved_changes():
            return True
        answer = ask_unsaved(self, self.EDITOR_NOUN, os.path.basename(self.current_image_path))
        if answer == SAVE:
            return self.save_skeleton()
        if answer == DISCARD:
            self._open_start_skeleton()
            return True
        return False

    def reset(self) -> bool:
        """A new folder was opened: prompt, then forget the image. False = user stayed."""
        if not self.maybe_save_changes():
            return False
        self._clear_transient()
        self.current_image_path = ""
        self.current_image_name = ""
        self.images_base_folder = None
        self.skeleton_source = None
        self._original_image_pixmap = None
        self.image_item.setPixmap(QPixmap())
        self.model = SkeletonCorrectionModel()
        self.graphics_view.clear_content_rect()
        self.render_skeleton(full=True)
        self._refresh_chip()
        return True

    def load_image(self, image_path: str, images_base_folder: str | None = None) -> None:
        """Open ``image_path`` with its skeleton (saved correction, else generated).

        Re-selecting the open image keeps unsaved edits; opening another one first
        asks what to do with them and stays put on Cancel or a failed save.
        """
        same = bool(self.current_image_path) and _same_file(image_path, self.current_image_path)
        if same and self.has_unsaved_changes():
            return
        if not same and not self.maybe_save_changes():
            return

        pix = QPixmap(image_path)
        if pix.isNull():
            self.notice.emit(f"Could not open {os.path.basename(image_path)}", "danger")
            return
        self.current_image_path = image_path
        self.current_image_name = os.path.splitext(os.path.basename(image_path))[0]
        self.images_base_folder = images_base_folder or os.path.dirname(image_path)

        self._original_image_pixmap = pix
        self.image_item.setPixmap(pix)
        self.scene.setSceneRect(QRectF(pix.rect()))
        self._clear_transient()
        self._open_start_skeleton()

        self.graphics_view.set_content_rect(QRectF(pix.rect()))
        self.graphics_view.fit_to_view()
        if self.norm_controls.is_active():
            self.apply_normalization()

    def _open_start_skeleton(self) -> None:
        """(Re)open the skeleton on disk for the current image; the document is clean."""
        w, h = self.image_size()
        self.model.set_empty((max(1, w), max(1, h)))
        self._save_size = None
        self._save_error = ""
        self.skeleton_source = None
        path, kind = self._files().start_file()
        if path and self._read_skeleton(path):
            self.skeleton_source = kind
        self.draft.reset()
        self.refresh_draft()
        self.render_skeleton(full=True)

    def _read_skeleton(self, path: str) -> bool:
        gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            self.notice.emit(f"Could not read skeleton {os.path.basename(path)}", "danger")
            return False
        self._save_size = (int(gray.shape[1]), int(gray.shape[0]))
        self.model.load_from_raster(gray, target_size=self.image_size())
        return True

    def load_skeleton_via_dialog(self) -> None:
        """Open another skeleton file (the dialog starts on the generated skeleton)."""
        if not self.current_image_path:
            self.notice.emit("Open an image first", "warn")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Skeleton File",
            self._files().dialog_start(),
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
        )
        if not path or not self.maybe_save_changes():
            return
        self._clear_transient()
        if self._read_skeleton(path):
            files = self._files()
            if _same_file(path, files.correction_path):
                self.skeleton_source = CORRECTED
            elif files.generated_path() and _same_file(path, files.generated_path()):
                self.skeleton_source = GENERATED
            else:
                self.skeleton_source = LOADED
        self.render_skeleton(full=True)

    def save_skeleton(self) -> bool:
        """Write ``skeleton_corrections/<stem>_skeleton.png`` atomically. True on success."""
        if not self.current_image_path or self.model.mask is None:
            return False
        out_path = self._files().correction_path
        name = os.path.basename(out_path)
        rendered = self.model.render_to_size(self._save_size or SKELETON_OUTPUT_SIZE)
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            imwrite_unicode(out_path, rendered)
        except (OSError, ValueError) as exc:
            logger.warning("Could not save skeleton to %s: %s", out_path, exc)
            self._save_error = str(exc)
            self._error_revision = self.model.revision
            self._refresh_chip()
            self.notice.emit(f"Could not save {name}: {exc}", "danger")
            return False
        self.model.mark_saved()
        self.skeleton_source = CORRECTED
        self._save_error = ""
        self._sync_dirty()
        self.notice.emit(f"Skeleton saved · {name}", "success")
        self.skeleton_saved.emit(out_path)
        return True

    def _sync_dirty(self) -> None:
        dirty = self.has_unsaved_changes()
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirty_changed.emit(dirty)
        self.undo_button.setEnabled(self.model.can_undo)
        self.redo_button.setEnabled(self.model.can_redo)
        self._refresh_chip()

    def _refresh_chip(self) -> None:
        chip = self.hint_panel
        if not self.current_image_path:
            chip.set_text("No image")
            chip.set_state("none")
            return
        chip.set_text(os.path.basename(self.current_image_path))
        if self._save_error and self.model.revision == self._error_revision:
            chip.set_state("error", "Not saved", tooltip=self._save_error)
        elif self._dirty:
            chip.set_state("dirty")
        elif self.skeleton_source == CORRECTED:
            chip.set_state("clean", "Saved", tooltip=self._files().correction_path)
        elif self.skeleton_source == GENERATED:
            empty = self.model.mask is None or not self.model.mask.any()
            chip.set_state(
                "none", "Generated · empty" if empty else "Generated", tooltip="Not corrected yet"
            )
        elif self.skeleton_source == LOADED:
            chip.set_state("none", "Loaded file")
        else:
            chip.set_state("none", "No skeleton")

    # ------------------------------------------------------------------ tool selection
    def _on_tool_changed(self, button: QAbstractButton) -> None:
        tool_id = next((t for t, b in self._tool_buttons.items() if b is button), None)
        if tool_id is not None:
            self._activate_tool(tool_id)

    def _activate_tool(self, tool_id: str, *, keep_draft: bool = False) -> None:
        self.tools[self.current_tool].deactivate()
        if not keep_draft:
            self.draft.reset()
        self.current_tool = tool_id
        self.eraser_container.setVisible(tool_id == self.TOOL_ERASER)
        self.show_eraser_ring(None)
        self.decorations.clear_connect()
        self.decorations.highlight_endpoint(None)
        self.graphics_view.refresh_cursor()
        self.refresh_draft()

    def _on_mode_toggle(self, checked: bool) -> None:
        self.pan_mode = bool(checked)
        self.graphics_view.set_pan_mode(self.pan_mode)
        if self.pan_mode:
            self.show_eraser_ring(None)
        self.graphics_view.refresh_cursor()

    def _update_polyline_buttons_enabled(self) -> None:
        in_polyline = self.current_tool == self.TOOL_POLYLINE
        n = len(self.draft.points)
        self.finish_polyline_button.setEnabled(in_polyline and n >= 2)
        self.cancel_polyline_button.setEnabled(in_polyline and n > 0)
        self.smooth_polyline_toggle.setEnabled(in_polyline)
        self.polyline_prompt.setVisible(in_polyline and n > 0)
        self.refresh_hint()

    def _set_status(self, text: str, tooltip: str = "") -> None:
        """Show ``text`` on the chip's hint line (full help in ``tooltip``)."""
        self.hint_panel.set_hint(text, tooltip)

    def _on_eraser_radius_changed(self, v: int) -> None:
        self.eraser_radius = int(v)
        if self.eraser_ring.is_visible():
            self.eraser_ring.set_diameter(self.eraser_radius)

    def _on_smooth_polyline_toggled(self, checked: bool) -> None:
        self.draft.smooth = bool(checked)
        if self.current_tool == self.TOOL_POLYLINE and self.draft.points:
            self.refresh_draft(self.draft.points[-1])

    def _on_opacity_changed(self, v: int) -> None:
        self.overlay_opacity = float(v) / 100.0
        self.skeleton_item.setOpacity(self.overlay_opacity)

    # ------------------------------------------------------------------ actions
    def _clear_transient(self) -> None:
        """Stop the tool gesture in progress and drop the draft and previews."""
        self.tools[self.current_tool].deactivate()
        self.draft.reset()
        self.decorations.clear_transient()
        self.cancel_scheduled_render()
        self.refresh_draft()

    def clear_skeleton(self) -> None:
        if not self.current_image_path or self.model.mask is None:
            return
        self._clear_transient()
        self.model.push_undo()
        self.model.clear()
        self.render_skeleton(full=True)

    def undo(self) -> None:
        # Mid-polyline, undo removes the last point first.
        if self.current_tool == self.TOOL_POLYLINE and self.draft.points:
            self.draft.points.pop()
            if not self.draft.points:
                self.draft.reset()
            self.refresh_draft(self.draft.points[-1] if self.draft.points else None)
            return
        self._step_history(self.model.undo)

    def redo(self) -> None:
        self._step_history(self.model.redo)

    def _step_history(self, step) -> None:
        if step():
            self._clear_transient()
            self.render_skeleton(full=True)

    def finish_polyline(self) -> None:
        """Commit the current polyline (Finish button, Enter, double-click)."""
        if self.current_tool == self.TOOL_POLYLINE and len(self.draft.points) >= 2:
            self.tools[self.TOOL_POLYLINE].commit()

    def cancel_polyline(self) -> None:
        """Drop the polyline in progress without changing the skeleton."""
        self._clear_transient()

    # ------------------------------------------------------------------ view input
    def on_tool_mouse_press(self, pos: QPoint, event) -> None:
        if self.current_image_path and self.model.mask is not None:
            modifiers = event.modifiers() if event is not None else Qt.KeyboardModifier.NoModifier
            self.tools[self.current_tool].press(pos, modifiers)

    def on_tool_mouse_move(self, pos: QPoint, _event) -> None:
        if self.current_image_path and self.model.mask is not None:
            self.tools[self.current_tool].move(pos)

    def on_tool_mouse_release(self, pos: QPoint, _event) -> None:
        if self.current_image_path and self.model.mask is not None:
            self.tools[self.current_tool].release(pos)

    def on_tool_mouse_double_click(self, pos: QPoint, _event) -> None:
        if self.current_image_path and self.model.mask is not None:
            self.tools[self.current_tool].double_click(pos)

    def on_key_press(self, event: QKeyEvent) -> bool:
        """Canvas keys: Esc, Ctrl+S, tool letters, Smooth, [ ], then the tool's own keys."""
        key, mods = event.key(), event.modifiers()
        if key == Qt.Key.Key_Escape:
            self._clear_transient()
            return True
        if key == Qt.Key.Key_S and mods & Qt.KeyboardModifier.ControlModifier:
            self.save_skeleton()
            return True
        if not mods & _NO_TOOL_KEY_MODS:
            if key in _TOOL_QT_KEYS:
                self.switch_tool(_TOOL_QT_KEYS[key])
                return True
            if key == _SMOOTH_QT_KEY and self.current_tool == self.TOOL_POLYLINE:
                self.smooth_polyline_toggle.toggle()
                return True
            if key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight):
                step = self.ERASER_KEY_STEP
                delta = step if key == Qt.Key.Key_BracketRight else -step
                self.eraser_slider.setValue(self.eraser_slider.value() + delta)
                return True
        return self.tools[self.current_tool].key(event)

    # ------------------------------------------------------------------ enhancement
    def apply_normalization(self) -> None:
        """Show the image with the Enhance settings (the raw image when they are off)."""
        if not self.current_image_path:
            return
        pix = self.norm_controls.display_pixmap(self.current_image_path)
        if pix is None:
            self.notice.emit("Could not read the image for enhancement", "danger")
            return
        original = self._original_image_pixmap
        if original is not None and pix.size() != original.size():
            pix = pix.scaled(
                original.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.image_item.setPixmap(pix)


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
