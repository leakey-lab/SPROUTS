"""The Correct page: :class:`CorrectEditor` (plan contract C17), the mask, the skeleton and
the class labels of an image on one canvas.

A composition root (spec section 12.2) with no document, tool or history logic: the canvas
(``EditorCanvasView``, input to the ``ToolRouter``) shows the photo (``PhotoLayerItem``, z 0)
under the ``MaskLayer``, the ``ClassesLayer`` and the ``SkeletonLayer``; the
``CorrectSession`` opens, saves, prompts (``app.gui.editor_prompts``) and keeps the one undo
timeline; ``PageActions`` (``actions``) has the page's file actions and turns the session's
save reports into the page's signals; the overlays are the ``EditorChrome``, the
``CorrectControls`` and the side card (``SidePanels``: the Layers panel above the class
panel); the keys are the registry's Correct rows (``keys``). ``paging`` knows the library
position and reads the next images ahead; ``LayerPrefs`` keeps the layers' opacity and
visibility. The editor is the layers' ``LayerHost``, by delegation.
Every transition (tool, layer, visibility, save, paging, open, Load, Clear, leaving) first
ends a drag in progress as its release would (the mid-gesture rule of
:mod:`app.gui.correct.layer`); opening, a layer switch, paging and leaving also make the
canvas forget it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QRectF, QSettings, QSize, pyqtSignal
from PyQt6.QtWidgets import QGraphicsScene, QVBoxLayout, QWidget

from app.gui import editor_prompts, shortcuts
from app.gui.canvas_layers.photo import PhotoLayerItem
from app.gui.class_panel import ClassPanel
from app.gui.correct.actions import PageActions
from app.gui.correct.chip import ChipView, title
from app.gui.correct.classes_layer import ClassesLayer
from app.gui.correct.controls import CorrectControls
from app.gui.correct.keys import install_page_shortcuts
from app.gui.correct.layer import CLASSES, MASK, SKELETON
from app.gui.correct.layer_panel import LayerPanel
from app.gui.correct.layer_prefs import LayerPrefs
from app.gui.correct.mask_layer import MaskLayer
from app.gui.correct.paging import Neighbours, PagingApi
from app.gui.correct.session import CorrectSession
from app.gui.correct.side_panels import SidePanels
from app.gui.correct.skeleton_layer import SkeletonLayer
from app.gui.correct.tool_router import ToolRouter
from app.gui.editor_canvas import EditorCanvasView
from app.gui.editor_chrome import EditorChrome
from app.gui.image_normalization_interface import NormalizationControls
from app.io_utils import same_file

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from app.gui.correct.layer import EditLayer
    from app.gui.correct.paging import PositionProvider
    from app.gui.skeleton_document import GeneratedResolver

__all__ = ["CorrectEditor"]


class CorrectEditor(PagingApi, QWidget):
    """The Correct stage's page: the mask (root area), the skeleton (root length) and the
    class labels (GT for training) of one image on one canvas, one edited at a time. The
    parts are attributes by name (``layers``, ``session``, ``side_panels``, ``actions`` ...)."""

    notice = pyqtSignal(str, str)
    dirty_changed = pyqtSignal(bool)
    mask_saved = pyqtSignal(str)  # image path: the GT mask was written (an empty one too)
    mask_cleared = pyqtSignal(str)  # image path: a mask equal to the prediction removed its GT
    skeleton_saved = pyqtSignal(str)  # GT skeleton path (measured, O1; the Library: dirty_changed)
    skeleton_cleared = pyqtSignal(str)  # image path: the skeleton equals the prediction, GT removed
    gt_saved = pyqtSignal(str)  # image path: its class labels were written (gt/<stem>.png)
    gt_removed = pyqtSignal(str)  # image path: Remove GT… deleted its GT files
    b_key_status_changed = pyqtSignal(bool)
    navigate_requested = pyqtSignal(int)  # PgDn / PgUp after the dirty layers were saved
    active_layer_changed = pyqtSignal(str)

    EDITOR_NOUN = "mask, skeleton and class labels"
    FINISH_LINE_FIRST = "Finish the line (Enter) or cancel it (Esc) first"
    SHORTCUT_SCOPE = shortcuts.CORRECT

    def __init__(self, parent: QWidget | None = None, *, settings: QSettings | None = None) -> None:
        """``settings``: where the layers' opacity and visibility are kept across restarts
        (None: the app's QSettings; nothing on the ``offscreen`` platform)."""
        super().__init__(parent)
        self._size = QSize()  # the open image; empty while nothing is open (no input then)
        self._photo: np.ndarray | None = None  # its decoded photo (Enhance renders from it)
        self.previous_layer_id: str | None = None  # the layer edited before: X goes back
        self._build_parts()
        self._build_overlays()
        self._connect()
        self._prefs = LayerPrefs.for_app(settings)
        self._prefs.restore(self.layers.values())
        for layer_id, layer in self.layers.items():
            self.layer_panel.set_visible_state(layer_id, layer.is_visible())
        self._shortcuts = install_page_shortcuts(self)
        self.graphics_view.refresh_cursor()
        self._refresh()

    # ------------------------------------------------------------------ building
    def _build_parts(self) -> None:
        """The canvas and its scene (photo, the layers), the actions, session and router."""
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        self.graphics_view = view = EditorCanvasView(parent=self)
        self.scene = QGraphicsScene(view)
        view.setScene(self.scene)
        column.addWidget(view)
        self.photo_item = PhotoLayerItem()
        self.photo_item.setZValue(0)
        self.scene.addItem(self.photo_item)
        self._skeleton = skeleton = SkeletonLayer(self, self.scene)
        mask, classes = MaskLayer(self, self.scene), ClassesLayer(self, self.scene)
        self.layers: dict[str, EditLayer] = {MASK: mask, SKELETON: skeleton, CLASSES: classes}
        self.actions = actions = PageActions(self)  # file actions and save reports
        self.session = CorrectSession(
            list(self.layers.values()),
            prompts=editor_prompts,
            parent=lambda: self,
            on_dirty_changed=self.dirty_changed.emit,
            on_notice=self.notice.emit,
            on_history_changed=self._refresh,
            on_undone=self._undone,
            on_saved=actions.report,
        )
        self.router = ToolRouter(
            self.layers,
            canvas_size=lambda: self._size,
            on_tool_request=self.request_tool,
            on_b_hold=self.b_key_status_changed.emit,
            on_layer_shown=self._shown,
        )
        view.set_sink(self.router)
        view.installEventFilter(self)  # focus-out ends the hold keys (eventFilter)
        self.paging = Neighbours(self, self.layers)  # library position, read-ahead

    def _build_overlays(self) -> None:
        """The chrome, the page's controls on it and the side card (right edge)."""
        self.norm_controls = NormalizationControls(self)
        self.chrome = chrome = EditorChrome(self, self.graphics_view, self.norm_controls)
        self.controls = controls = CorrectControls(self, chrome, self.layers)
        self.layer_panel = LayerPanel(list(self.layers.values()), self)
        self.class_panel = ClassPanel(self)
        self.side_panels = SidePanels(self.layer_panel, self.class_panel, self)
        self.side_panels.bind_classes(self.layers[CLASSES], self.graphics_view, self.edit_classes)
        chrome.register(side_panel=self.side_panels)
        chrome.order_tab_focus()  # Tab follows the screen, not the build order
        self._chip = ChipView(chrome.chip, self.paging.position)  # changes only
        # The parts MainWindow, the shell and the tests use by name.
        self.tool_rail, self.dock, self.enhance_popover = chrome.rail, chrome.dock, chrome.popover
        self.status_chip, self.zoom_cluster = chrome.chip, chrome.zoom
        self.pan_button, self.enhance_button = chrome.pan_button, chrome.enhance_button
        self.save_button, self.clear_button = controls.save_button, controls.clear_button
        self.undo_button, self.redo_button = controls.undo_button, controls.redo_button

    def _connect(self) -> None:
        """The controls' and the panel's requests to the page's handlers."""
        controls, panel, skeleton = self.controls, self.layer_panel, self._skeleton
        controls.tool_requested.connect(self.request_tool)
        controls.size_changed.connect(lambda px: self._active().set_size(px))
        controls.opacity_changed.connect(lambda value: self._active().set_opacity(value))
        controls.load_requested.connect(self.actions.load_skeleton_file)
        controls.prediction_requested.connect(self.actions.load_prediction)
        controls.undo_requested.connect(self.undo)
        controls.redo_requested.connect(self.redo)
        controls.clear_requested.connect(self.clear_active_layer)
        controls.save_requested.connect(self.save)
        controls.finish_line_requested.connect(skeleton.finish_polyline)
        controls.cancel_line_requested.connect(skeleton.cancel_polyline)
        controls.smooth_toggled.connect(skeleton.set_smooth)
        panel.activate_requested.connect(self.set_active_layer)
        panel.visibility_toggled.connect(self.set_layer_visible)
        self.norm_controls.enhancement_changed.connect(self.apply_normalization)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Shown: the canvas takes the keys (Space, B, the gesture keys)."""
        super().showEvent(event)
        self.graphics_view.setFocus()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Hidden (another stage, or the app quits): keep the layers' opacity and visibility."""
        super().hideEvent(event)
        self._prefs.store(self.layers.values())

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        """The canvas lost the keyboard: B and Space, if held, will not see their release."""
        view = getattr(self, "graphics_view", None)  # None while the page is being torn down
        if watched is view and event.type() == QEvent.Type.FocusOut:
            self.router.canvas_focus_lost()
            if view.space_pan:
                view.set_space_pan(False)
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ MainWindow API
    @property
    def current_image_path(self) -> str:
        """The image open on the page ("" when none)."""
        return self.session.image_path

    @property
    def active_layer_id(self) -> str:
        """The edited layer: ``"mask"``, ``"skeleton"`` or ``"classes"``."""
        return self.router.active_id

    def load_image(self, image_path: str, images_base_folder: str | None = None) -> None:
        """Open ``image_path`` with its layers, fitted (spec section 9).

        The open image again keeps unsaved edits; clean, it keeps documents, history and view
        unless a file changed on disk (the documents reload, same view; a changed photo opens
        fitted). Another image is decoded first (unreadable: a toast, nothing changes), then
        unsaved edits are asked about (Cancel stays). A neighbour read ahead, or the photo
        the Library decoded, is adopted instead of decoding again (``set_neighbour_provider``,
        ``set_photo_source``); the image left is kept as read (``paging``).
        """
        session = self.session
        self._end_drag()
        if session.image_path and same_file(image_path, session.image_path):
            if session.is_dirty():
                return
            if not session.image_changed():
                if session.files_changed():
                    session.reload_documents()
                return self.paging.opened(image_path, images_base_folder)  # read ahead again
        ahead = self.paging.photo_for(image_path, self.norm_controls.source_image)
        if ahead is None:
            self.notice.emit(f"Could not open {os.path.basename(image_path)}", "danger")
            return
        if not session.release():
            self.paging.put_back(ahead)  # the user kept editing the open image
            return
        self.paging.leaving(session)  # kept as read: going back to it reads nothing again
        photo = ahead.photo
        height, width = photo.shape[:2]
        self._size, self._photo = QSize(width, height), photo
        session.open(image_path, (width, height), images_base_folder, ahead.layers)
        self.norm_controls.set_source_path(image_path)
        self.photo_item.set_image(self.norm_controls.apply_to(photo))
        view = self.graphics_view
        view.set_content_rect(QRectF(0, 0, width, height))
        view.fit_to_view()
        if self.isVisible():
            view.setFocus()
        self.paging.opened(image_path, images_base_folder, ahead)

    def has_unsaved_changes(self) -> bool:
        """True when a layer of the open image differs from its saved state."""
        return self.session.is_dirty()

    def maybe_save_changes(self) -> bool:
        """The one prompt about the unsaved layers; False = the caller stays on this image."""
        self._end_drag()
        return self.session.release()

    def reset(self) -> bool:
        """A new folder: the prompt, then an empty page. False (nothing changes) = stay."""
        self._end_drag()
        if not self.session.reset():
            return False
        self._size, self._photo = QSize(), None
        self.paging.closed()
        self.photo_item.set_image(None)
        self.graphics_view.clear_content_rect()
        return True

    def navigate(self, delta: int) -> None:
        """PgDn / PgUp: save the unsaved layers (``PageActions.save_for_paging``), then ask for
        the next image. A polyline draft that could be finished is never saved: the page stays
        and says so, with the skeleton edited and shown, so the Enter / Esc it names reach the
        draft. A branch picked with Select and left as it was is dropped (nothing to keep)."""
        self._end_drag()
        draft = self._skeleton.draft
        if draft.pending():
            self.set_active_layer(SKELETON)
            self.set_layer_visible(SKELETON, True)
            return self.notice.emit(self.FINISH_LINE_FIRST, "warn")
        if draft.plain_pick():
            self._skeleton.cancel_polyline()
        if self.actions.save_for_paging():  # a failed save, or Cancel, stays
            self.navigate_requested.emit(delta)

    def set_generated_skeleton_resolver(self, resolver: GeneratedResolver | None) -> None:
        """Where the skeleton layer finds an image's generated skeleton (None: skeletons/)."""
        self._skeleton.set_resolver(resolver)

    def set_position_provider(self, provider: PositionProvider | None) -> None:
        """``provider(path)``: ``(index, count)`` of the image in the library (1-based) or
        None, for the chip's "Image 12 / 40"."""
        self.paging.set_position_provider(provider)
        self._chip.forget_position()  # asked again
        self._refresh()

    def save(self) -> bool:
        """Ctrl+S: save the unsaved layers (none: "Nothing unsaved"); True when all did."""
        report = self.session.save(self.active_layer_id)  # ends a gesture; nothing open: no save
        return bool(report.outcomes) and report.ok

    def undo(self) -> None:
        """Ctrl+Z: the newest action of any layer (a polyline draft loses a point first)."""
        self.session.undo(self.active_layer_id)

    def redo(self) -> None:
        """Ctrl+Y / Ctrl+Shift+Z: the newest undone action of any layer."""
        self.session.redo()

    def clear_active_layer(self) -> None:
        """Clear the edited layer as one undoable action (shown first when it is hidden)."""
        self.session.finish_gestures()
        layer = self._active()
        if layer.is_open() and not layer.is_visible():
            layer.set_visible(True)
            self._shown(layer.layer_id)
        layer.clear()

    def apply_normalization(self) -> None:
        """Show the open photo with the Enhance settings (the raw photo when they are off),
        rendered from the photo decoded at open (the one the documents were opened for)."""
        if self.session.image_path and self._photo is not None:
            self.photo_item.set_image(self.norm_controls.apply_to(self._photo))

    # ------------------------------------------------------------------ layers and tools
    def set_active_layer(self, layer_id: str) -> bool:
        """Edit ``layer_id`` (a drag in progress ends first); True when the layer changed."""
        self.session.finish_gestures()
        previous = self.router.active_id
        if not self.router.set_active(layer_id):
            return False
        self.previous_layer_id = previous
        self.graphics_view.end_gesture()
        self.controls.show_layer(layer_id)
        self.side_panels.set_active_layer(layer_id)  # its row; the class panel for the labels
        self._refresh()
        self.active_layer_changed.emit(layer_id)
        return True

    def swap_layer(self) -> None:
        """X: edit the layer edited before (none yet: the mask or the skeleton, the other)."""
        other = SKELETON if self.active_layer_id == MASK else MASK
        self.set_active_layer(self.previous_layer_id or other)

    def select_class(self, number: int) -> None:
        """1-9, 0: paint with the n-th class (0: Ignore), while the class labels are edited."""
        if self.active_layer_id == CLASSES:
            self.side_panels.pick_class(number)

    def request_tool(self, layer_id: str, tool_id: str) -> None:
        """A tool was asked for (rail, tool key, B tap): edit its layer with it. A switched-to
        layer keeps that tool if it has it (a polyline draft resumes); on the edited layer the
        tool is taken again, as today."""
        switched = self.set_active_layer(layer_id)  # a drag in progress ends first
        layer = self.layers[layer_id]
        if not switched or layer.current_tool != tool_id:
            layer.set_tool(tool_id)

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        """Show or hide ``layer_id`` (the Layers eye); a drag in progress ends first."""
        self.session.finish_gestures()
        layer = self.layers[layer_id]
        layer.set_visible(visible)
        self.layer_panel.set_visible_state(layer_id, layer.is_visible())

    def toggle_layers(self, *, others: bool = False) -> None:
        """H: hide every layer to see the photo (``others``: every layer but the edited one);
        show them all when none of them is shown."""
        ids = [i for i in self.layers if not (others and i == self.active_layer_id)]
        show = not any(self.layers[layer_id].is_visible() for layer_id in ids)
        for layer_id in ids:
            self.set_layer_visible(layer_id, show)

    def toggle_reference(self) -> None:
        """Shift+H: show or hide the layers that are not edited."""
        self.toggle_layers(others=True)

    def load_prediction(self) -> None:
        """Load prediction: the edited layer's prediction as one undoable action (if any)."""
        self.actions.load_prediction()

    def load_skeleton_file(self) -> None:
        """Open another skeleton file; an unsaved skeleton is asked about first."""
        self.actions.load_skeleton_file()

    def edit_classes(self) -> None:
        """Classes…: the class editor; OK saves gt/classes.json and recolours the labels."""
        self.actions.edit_classes()

    def remove_gt(self) -> None:
        """Remove GT…: delete the open image's GT files once the user confirms."""
        self.actions.remove_gt()

    # ------------------------------------------------------------------ LayerHost
    def notify(self, message: str, kind: str = "info") -> None:
        """A layer's toast."""
        self.notice.emit(message, kind)

    def layer_edited(self, layer_id: str) -> None:
        """A layer recorded a new undoable action: the timeline takes it."""
        self.session.layer_edited(layer_id)

    def layer_changed(self, layer_id: str) -> None:
        """Something the page shows of a layer changed (its state, tool, hint or draft)."""
        self.session.layer_changed(layer_id)

    def is_panning(self) -> bool:
        """True while a left drag pans the canvas (the brush rings hide then)."""
        return self.graphics_view.is_panning_mode()

    # ------------------------------------------------------------------ internals
    def _active(self) -> EditLayer:
        return self.layers[self.router.active_id]

    def _end_drag(self) -> None:
        """Open, paging, leaving: a drag in progress ends and the canvas forgets it."""
        self.session.finish_gestures()
        self.graphics_view.end_gesture()

    def _shown(self, layer_id: str) -> None:
        """A hidden layer was shown to be edited: its eye, and a toast naming it."""
        self.layer_panel.set_visible_state(layer_id, True)
        self.notice.emit(f"{title(self.layers[layer_id].noun)} shown", "info")

    def _undone(self, layer_id: str, action: str) -> None:
        """An undo / redo stepped ``layer_id``: shown if hidden; the other layer says so."""
        layer = self.layers[layer_id]
        if layer_id != self.active_layer_id:
            self.layer_panel.flash(layer_id)
            self.notice.emit(f"{title(action)} · {layer.noun}", "info")
        if not layer.is_visible():
            layer.set_visible(True)
            self.layer_panel.set_visible_state(layer_id, True)

    def _refresh(self) -> None:
        """Show the layers' state: the edited layer's controls, Undo / Redo, the polyline
        prompt, Remove GT…, the rows and the chip. Once per change, never per drag frame."""
        layer, controls = self._active(), self.controls
        controls.sync(layer)
        controls.set_history(self.session.can_undo(), self.session.can_redo())
        controls.set_draft(self._skeleton.draft_state())
        self.actions.sync()
        statuses = {layer_id: each.status() for layer_id, each in self.layers.items()}
        for layer_id, status in statuses.items():
            self.layer_panel.set_status(layer_id, status)
        self._chip.show(self.session.image_path, statuses, layer)
