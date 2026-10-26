"""The mask as an edit layer of the Correct workspace: :class:`MaskLayer` (plan contract C13).

``MaskLayer`` is a :class:`~app.gui.correct.raster_layer.RasterLayer` (the canvas, the item
at z 1.0, the Brush / Eraser / Fill tools, the ring, the history replay and the view state)
over the Qt-free :class:`~app.gui.correct.mask_document.MaskDocument` (the 0/1 plane, its
patch history, the saved baseline, the file I/O). It adds the mask's identity, its paint
rules (:mod:`app.gui.correct.mask_tools`) and the document members of ``EditLayer``.

On screen the mask is ``tokens.MASK`` without antialiasing; on disk it stays white on
transparent RGBA, saved as GT (``gt/mask/<stem>.png``; the prediction in ``mask/`` is never
written), so saved files are today's for the same edits. "Load prediction" puts the
prediction back as one undoable action.
"""

from __future__ import annotations

import os
from functools import partial

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsScene

from app.gui.correct.layer import (
    MASK,
    LayerHost,
    LayerStatus,
    Notice,
    SaveOutcome,
    project_label,
)
from app.gui.correct.mask_document import SOURCE_GT, MaskDocument, MaskRead
from app.gui.correct.mask_tools import argb_to_plane, fill_source, mask_lut
from app.gui.correct.raster_layer import RasterLayer
from app.gui.widgets import tokens

__all__ = ["MaskLayer"]


class MaskLayer(RasterLayer):
    """The mask of the open image as an edit layer (``EditLayer``; the tools' ``MaskToolHost``).

    ``document`` (a ``MaskDocument``), ``canvas``, ``item`` (in ``scene`` at z 1.0) and
    ``ring`` (the brush ring, a ``BrushPreview``) are its parts; ``host`` hears of new
    actions and UI changes. A new layer is active and visible, with the brush at 8 px.
    """

    layer_id = MASK
    noun = "mask"
    color = tokens.MASK
    Z = 1.0
    DEFAULT_OPACITY = 0.60
    CLEARED_NOTICE = "Mask cleared. Ctrl+Z to undo; Save writes an empty GT mask."
    document: MaskDocument

    def __init__(self, host: LayerHost, scene: QGraphicsScene) -> None:
        """Build the parts in ``scene``; nothing is open until :meth:`open`."""
        self.brush_color = QColor(self.color)
        lut = mask_lut(self.color)
        super().__init__(
            host,
            scene,
            document=MaskDocument(),
            lut=lambda: lut,
            to_plane=argb_to_plane,
            fill_source=fill_source,
        )

    # ------------------------------------------------------------------ document
    def read_ahead(self, image_path: str, images_base_folder: str | None):
        """``ReadAheadLayer``: a worker reader of ``image_path``'s saved mask."""
        return partial(MaskDocument.read, image_path)

    def open(
        self,
        image_path: str,
        size: tuple[int, int],
        images_base_folder: str | None,
        ahead: object | None = None,
    ) -> list[Notice]:
        """Open the saved mask of ``image_path`` (``size`` = (w, h)); the notices to show.

        A stroke in progress is committed first. The mask lives next to the image, so
        ``images_base_folder`` only roots the file labels. ``ahead``: a :meth:`read_ahead` result.
        """
        self.finish_gesture()
        self._root = images_base_folder or os.path.dirname(image_path)
        read = ahead if isinstance(ahead, MaskRead) else None
        notices = self.document.open(image_path, size, read, root=self._root)
        self._replaced()
        return notices

    def close(self) -> None:
        """Forget the document (a stroke in progress is committed first)."""
        self.finish_gesture()
        self.document.close()
        self._replaced()

    def is_open(self) -> bool:
        """True while a mask is open."""
        return self.document.is_open()

    def file_stamps(self) -> tuple:
        """The GT mask and the prediction's names, stamped or absent (``()`` when closed)."""
        return self.document.file_stamps()

    def write_stamps(self) -> tuple:
        """``DiskGuardedLayer``: the GT mask, the one file a save writes or removes."""
        return self.document.write_stamps()

    def matches_disk(self) -> bool:
        """``DiskGuardedLayer``: reading the mask files now would open the saved mask."""
        return self.document.matches_disk()

    def is_dirty(self) -> bool:
        """True when the plane differs from the saved mask (a pixel comparison)."""
        return self.document.is_dirty()

    def save(self) -> SaveOutcome:
        """Write the GT mask (``MaskDocument.save``); a stroke is committed first."""
        self.finish_gesture()
        if not self.document.is_open():
            return SaveOutcome(self.layer_id, ok=False, message="No mask is open", kind="danger")
        result = self.document.save()
        self._changed()
        return self.document.outcome(result)

    def unloaded_label(self) -> str:
        """``DiskGuardedLayer``: ``"gt/mask/<stem>.png"`` while that GT file exists and was not
        shown (a save would replace it); ``""`` otherwise (a prediction is never replaced)."""
        document = self.document
        if not document.unloaded_file() or document.source != SOURCE_GT:
            return ""
        stamp = document.write_stamps()[0]
        if stamp[1:] == (None,):  # gone since the open: nothing left to replace
            return ""
        return project_label(stamp[0], self._root)

    def load_prediction(self) -> bool:
        """Replace the plane with the prediction as one undoable action; False (nothing
        changes) without a prediction or when the plane is the prediction already."""
        self.finish_gesture()
        prediction = self.document.prediction()
        return prediction is not None and self.replace_plane(prediction)

    def revert(self) -> list[Notice]:
        """Discard: back to the saved mask in memory, history cleared (no notices)."""
        self.finish_gesture()
        if self.document.is_open():
            self.document.revert()
            self._replaced()
        return []

    def status(self) -> LayerStatus:
        """Save state: a failed save until the next edit, unsaved, "Mask not shown", "GT",
        "Prediction" or "No mask"."""
        return self.document.status()
