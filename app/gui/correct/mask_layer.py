"""The mask as an edit layer of the Correct workspace: :class:`MaskLayer` (plan contract C13).

``MaskLayer`` is a :class:`~app.gui.correct.raster_layer.RasterLayer` (the canvas, the item
at z 1.0, the Brush / Eraser / Fill tools, the ring, the history replay, the view state and
the document members of ``EditLayer``) over the Qt-free
:class:`~app.gui.correct.mask_document.MaskDocument` (the 0/1 plane, its patch history, the
saved baseline, the file I/O). It adds the mask's identity and its paint rules
(:mod:`app.gui.correct.mask_tools`).

On screen the mask is ``tokens.MASK`` without antialiasing; on disk it stays white on
transparent RGBA, saved as GT (``gt/mask/<stem>.png``; the prediction in ``mask/`` is never
written), so saved files are today's for the same edits. "Load prediction" puts the
prediction back as one undoable action.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsScene

from app.gui.correct.layer import MASK, LayerHost
from app.gui.correct.mask_document import SOURCE_GT, MaskDocument, MaskRead
from app.gui.correct.mask_tools import argb_to_plane, fill_source, mask_lut
from app.gui.correct.raster_layer import RasterLayer
from app.gui.key_text import platform_keys
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
    CLEARED_NOTICE = platform_keys("Mask cleared. Ctrl+Z to undo; Save writes an empty GT mask.")
    NOT_OPEN = "No mask is open"
    READ = MaskRead
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

    def unloaded_label(self) -> str:
        """``DiskGuardedLayer``: ``"gt/mask/<stem>.png"`` while that GT file exists and was not
        shown (a save would replace it); ``""`` otherwise (a prediction is never replaced)."""
        return super().unloaded_label() if self.document.source == SOURCE_GT else ""

    def load_prediction(self) -> bool:
        """Replace the plane with the prediction as one undoable action; False (nothing
        changes) without a prediction or when the plane is the prediction already."""
        self.finish_gesture()
        prediction = self.document.prediction()
        return prediction is not None and self.replace_plane(prediction)
