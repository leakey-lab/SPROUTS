"""The Correct page: :class:`CorrectEditor` (plan contract C17), the mask and the skeleton of
an image on one canvas.

A composition root (spec section 12.2) with no document, tool or history logic: the canvas
(``EditorCanvasView``, input to the ``ToolRouter``) shows the photo (``PhotoLayerItem``, z 0)
under the ``MaskLayer`` and the ``SkeletonLayer``; the ``CorrectSession`` opens, saves,
prompts (``app.gui.editor_prompts``) and keeps the one undo timeline, and its report of each
layer save becomes ``mask_saved`` / ``mask_cleared`` / ``skeleton_saved`` on every path; the
overlays are the ``EditorChrome``, the ``CorrectControls`` and the ``LayerPanel``; the keys
are the registry's Correct rows (``keys``). ``Neighbours`` (``paging``) knows the image's place
in the library and reads the next images ahead; ``LayerPrefs`` keeps the layers' opacity and
visibility across restarts. The editor is the layers' ``LayerHost``, by delegation.
Every transition (tool, layer, visibility, save, paging, open, Load, Clear, leaving) first
ends a drag in progress as its release would (the mid-gesture rule of
:mod:`app.gui.correct.layer`); opening, a layer switch, paging and leaving also make the
canvas forget it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QRectF, QSettings, QSize, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QGraphicsScene, QVBoxLayout, QWidget

from app.gui import editor_prompts, shortcuts
from app.gui.canvas_layers.photo import PhotoLayerItem
from app.gui.correct.chip import chip_content, show_on_chip, title
from app.gui.correct.controls import CorrectControls
from app.gui.correct.keys import install_page_shortcuts
from app.gui.correct.layer import MASK, SKELETON, LayerStatus, SaveOutcome
from app.gui.correct.layer_panel import LayerPanel
from app.gui.correct.layer_prefs import LayerPrefs
from app.gui.correct.mask_layer import MaskLayer
from app.gui.correct.paging import Neighbours, PagingApi
from app.gui.correct.session import CorrectSession
from app.gui.correct.skeleton_layer import SkeletonLayer
from app.gui.correct.tool_router import ToolRouter
from app.gui.editor_canvas import EditorCanvasView
from app.gui.editor_chrome import EditorChrome
from app.gui.image_normalization_interface import NormalizationControls

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from app.gui.correct.layer import EditLayer
    from app.gui.correct.paging import PositionProvider
    from app.gui.skeleton_document import GeneratedResolver

__all__ = ["CorrectEditor"]

_SKELETON_FILES = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class CorrectEditor(PagingApi, QWidget):
    """The Correct stage's page: the mask (root area) and the skeleton (root length) of one
    image on one canvas, one of them edited at a time. The parts are attributes by name
    (``layers``, ``session``, ``router``, ``chrome``, ``controls``, ``layer_panel``, ...)."""

    notice = pyqtSignal(str, str)
    dirty_changed = pyqtSignal(bool)
    mask_saved = pyqtSignal(str)  # image path: a mask file was written
    mask_cleared = pyqtSignal(str)  # image path: an empty mask removed the saved file
    skeleton_saved = pyqtSignal(str)  # correction path (FR-03: MainWindow does not connect it)
    b_key_status_changed = pyqtSignal(bool)
    navigate_requested = pyqtSignal(int)  # PgDn / PgUp after the dirty layers were saved
    active_layer_changed = pyqtSignal(str)

    EDITOR_NOUN = "mask and skeleton"
    FINISH_LINE_FIRST = "Finish the line (Enter) or cancel it (Esc) first"
    SHORTCUT_SCOPE = shortcuts.CORRECT

    def __init__(self, parent: QWidget | None = None, *, settings: QSettings | None = None) -> None:
        """``settings``: where the layers' opacity and visibility are kept across restarts
        (None: the app's QSettings; nothing on the ``offscreen`` platform)."""
        super().__init__(parent)
        self._size = QSize()  # the open image; empty while nothing is open (no input then)
        self._photo: np.ndarray | None = None  # its decoded photo (Enhance renders from it)
        self._chip: tuple = ()  # what the status chip shows (it is updated on changes only)
        self._placed: tuple = (None, None)  # (path, its library position) the chip shows
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
        """The canvas and its scene (photo, mask, skeleton), the session and the router."""
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        self.graphics_view = view = EditorCanvasView(parent=self)
        self.scene = QGraphicsScene(view)
        view.setScene(self.scene)
        column.addWidget(view)
        self.photo_item = PhotoLayerItem()
        self.photo_item.setZValue(0)
        self.scene.addItem(self.photo_item)
        self._skeleton = SkeletonLayer(self, self.scene)
        self.layers: dict[str, EditLayer] = {
            MASK: MaskLayer(self, self.scene),
            SKELETON: self._skeleton,
        }
        self.session = CorrectSession(
            list(self.layers.values()),
            prompts=editor_prompts,
            parent=lambda: self,
            on_dirty_changed=self.dirty_changed.emit,
            on_notice=self.notice.emit,
            on_history_changed=self._refresh,
            on_undone=self._undone,
            on_saved=self._report,
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
        """The chrome, the page's controls on it and the Layers panel (right edge)."""
        self.norm_controls = NormalizationControls(self)
        self.chrome = chrome = EditorChrome(self, self.graphics_view, self.norm_controls)
        self.controls = controls = CorrectControls(self, chrome, self.layers)
        self.layer_panel = LayerPanel(list(self.layers.values()), self)
        chrome.register(side_panel=self.layer_panel)
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
        controls.load_requested.connect(self.load_skeleton_file)
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
        """The edited layer: ``"mask"`` or ``"skeleton"``."""
        return self.router.active_id

    def load_image(self, image_path: str, images_base_folder: str | None = None) -> None:
        """Open ``image_path`` with its mask and skeleton, fitted (spec section 9).

        The open image again keeps unsaved edits; clean, it keeps documents, history and view
        unless a file changed on disk (the documents reload, same view; a changed photo opens
        fitted). Another image is decoded first (unreadable: a toast, nothing changes), then
        unsaved edits are asked about (Cancel stays). A neighbour read ahead, or the photo
        the Library decoded, is adopted instead of decoding again (``set_neighbour_provider``,
        ``set_photo_source``); the image left is kept as read (``paging``).
        """
        session = self.session
        self._end_drag()
        if session.image_path and _same_file(image_path, session.image_path):
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
        """PgDn / PgUp: save the unsaved layers (no prompt), then ask for the next image. A
        polyline draft that could be finished is never saved: the page stays and says so, with
        the skeleton edited and shown, so the Enter / Esc it names reach the draft. A branch
        picked with Select and left as it was is dropped (nothing to keep)."""
        self._end_drag()
        draft = self._skeleton.draft
        if draft.pending():
            self.set_active_layer(SKELETON)
            self.set_layer_visible(SKELETON, True)
            return self.notice.emit(self.FINISH_LINE_FIRST, "warn")
        if draft.plain_pick():
            self._skeleton.cancel_polyline()
        if self.session.save_dirty().ok:  # a failed save stays (its toast says why)
            self.navigate_requested.emit(delta)

    def set_generated_skeleton_resolver(self, resolver: GeneratedResolver | None) -> None:
        """Where the skeleton layer finds an image's generated skeleton (None: skeletons/)."""
        self._skeleton.set_resolver(resolver)

    def set_position_provider(self, provider: PositionProvider | None) -> None:
        """``provider(path)``: ``(index, count)`` of the image in the library (1-based) or
        None, for the chip's "Image 12 / 40"."""
        self.paging.set_position_provider(provider)
        self._placed = (None, None)  # asked again
        self._refresh()

    def save(self) -> bool:
        """Ctrl+S: save the unsaved layers (none unsaved: the edited one); True when all did."""
        self.session.finish_gestures()
        report = self.session.save(self.active_layer_id)  # nothing open: nothing saved
        return bool(report.outcomes) and report.ok

    def undo(self) -> None:
        """Ctrl+Z: the newest action of either layer (a polyline draft loses a point first)."""
        self.session.undo(self.active_layer_id)

    def redo(self) -> None:
        """Ctrl+Y / Ctrl+Shift+Z: the newest undone action of either layer."""
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
        if not self.router.set_active(layer_id):
            return False
        self.graphics_view.end_gesture()
        self.controls.show_layer(layer_id)
        self.layer_panel.set_active(layer_id)
        self._refresh()
        self.active_layer_changed.emit(layer_id)
        return True

    def swap_layer(self) -> None:
        """X: edit the other layer."""
        self.set_active_layer(SKELETON if self.active_layer_id == MASK else MASK)

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

    def toggle_layers(self) -> None:
        """H: hide both layers to see the photo; show both when none is shown."""
        show = not any(layer.is_visible() for layer in self.layers.values())
        for layer_id in self.layers:
            self.set_layer_visible(layer_id, show)

    def toggle_reference(self) -> None:
        """Shift+H: show or hide the layer that is not edited."""
        other = SKELETON if self.active_layer_id == MASK else MASK
        self.set_layer_visible(other, not self.layers[other].is_visible())

    def load_skeleton_file(self) -> None:
        """Open another skeleton file; an unsaved skeleton is asked about first."""
        self.session.finish_gestures()
        skeleton = self._skeleton
        if not skeleton.is_open():
            self.notice.emit("Open an image first", "warn")
            return
        start = skeleton.dialog_start()  # the generated skeleton, else skeletons/
        path, _ = QFileDialog.getOpenFileName(self, "Select Skeleton File", start, _SKELETON_FILES)
        if not path or not self.session.release([SKELETON]):
            return
        for notice in skeleton.load_file(path):
            self.notice.emit(notice.message, notice.kind)
        if skeleton.document.last_load_ok:  # unreadable: nothing changed, the timeline stays
            self.session.forget(SKELETON)  # its own history is gone too

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

    def _report(self, outcome: SaveOutcome) -> None:
        """One layer save attempt -> MainWindow's signals; a failed save reports nothing."""
        self.paging.forget(self.session.image_path)  # its files changed: nothing read stays
        if not outcome.ok:
            return
        if outcome.layer_id == MASK and (outcome.path or outcome.cleared):
            (self.mask_saved if outcome.path else self.mask_cleared).emit(self.session.image_path)
        elif outcome.layer_id == SKELETON and outcome.path:
            self.skeleton_saved.emit(outcome.path)

    def _refresh(self) -> None:
        """Show the layers' state: the edited layer's controls, Undo / Redo, the polyline
        prompt, the Layers rows and the chip. Runs once per change, never per drag frame."""
        layer, controls = self._active(), self.controls
        controls.sync(layer)
        controls.set_history(self.session.can_undo(), self.session.can_redo())
        controls.set_draft(self._skeleton.draft_state())
        statuses = {layer_id: each.status() for layer_id, each in self.layers.items()}
        for layer_id, status in statuses.items():
            self.layer_panel.set_status(layer_id, status)
        self._refresh_chip(layer, statuses)

    def _refresh_chip(self, layer: EditLayer, statuses: dict[str, LayerStatus]) -> None:
        """The chip (:mod:`app.gui.correct.chip`); the library position is asked once per
        image; changes only."""
        path = self.session.image_path
        if self._placed[0] != path:
            self._placed = (path, self.paging.position(path))
        shown = chip_content(path, statuses, layer, self._placed[1])
        show_on_chip(self.status_chip, shown, self._chip)
        self._chip = shown
