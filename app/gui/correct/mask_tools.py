"""The tools of the Correct mask layer: Brush, Eraser and Fill (plan contract C13).

Each tool is a small class with the ``LayerTool`` shape of :mod:`app.gui.correct.layer`
(``press`` / ``move`` / ``release`` / ``double_click`` / ``key`` / ``deactivate`` /
``hint``). It turns pointer input into :class:`~app.gui.paint_canvas.PaintCanvas` calls on
its layer, which it reaches only through :class:`MaskToolHost` (the ``MaskLayer``).

A tool keeps no drag state of its own: the stroke in progress lives in the canvas
(``PaintCanvas.in_stroke``) and the layer decides which tool receives the rest of a drag
(the mid-gesture rule of C2), so a release never reaches a tool that did not begin the
drag. The painting is today's, pixel for pixel: pixel centres, round caps, no
antialiasing, one undoable action per stroke, fill or clear. A future raster tool is one
more class in :data:`TOOL_CLASSES`.

The mask's paint rules live here too (today's ``MaskTracingInterface._plane_lut`` /
``_argb_to_plane`` / ``_fill_source``): :func:`mask_lut` (the canvas colour of each plane
value), :func:`argb_to_plane` (a painted canvas box back to plane values) and
:func:`fill_source` (what Fill looks at). :class:`FootprintRing` shows the current tool's
footprint on the pointer's pixel (the layer's brush ring).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar, Protocol

import cv2
import numpy as np
from PyQt6.QtGui import QColor

from app.gui.brush_preview import BrushPreview
from app.gui.correct.layer import ToolSpec
from app.gui.widgets import tokens

if TYPE_CHECKING:  # pragma: no cover
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QGraphicsScene

    from app.gui.paint_canvas import PaintCanvas, Rect

__all__ = [
    "FILL_MISS_NOTICE",
    "TOOL_CLASSES",
    "BrushTool",
    "EraserTool",
    "FillTool",
    "FootprintRing",
    "MaskTool",
    "MaskToolHost",
    "argb_to_plane",
    "build_tools",
    "fill_source",
    "mask_lut",
]

#: Today's notice for a Fill click outside every closed outline (no undo entry).
FILL_MISS_NOTICE = "Fill works inside a closed outline. Close the loop first."


def mask_lut(color: QColor | str = tokens.MASK) -> np.ndarray:
    """Canvas colour per plane value, ``(256, 4)`` B, G, R, A: 0 clear, else opaque ``color``."""
    c = QColor(color)
    lut = np.zeros((256, 4), np.uint8)
    lut[1:] = (c.blue(), c.green(), c.red(), 255)
    return lut


def argb_to_plane(argb: np.ndarray, rect: Rect) -> np.ndarray:
    """Painted canvas pixels back to plane values: any alpha is 1.

    Today's ``(argb[:, :, 3] > 0).astype(np.uint8)``, done by OpenCV: equal values,
    2-4x faster (every stroke commit reads its box back through it).
    """
    if not argb.size:
        return np.zeros(argb.shape[:2], np.uint8)
    alpha = cv2.extractChannel(np.ascontiguousarray(argb), 3)
    return cv2.threshold(alpha, 0, 1, cv2.THRESH_BINARY)[1]


def fill_source(plane: np.ndarray) -> tuple[np.ndarray, int]:
    """What Fill looks at: the mask as a 0/255 outline image, filled with plane value 1."""
    return (plane > 0).astype(np.uint8) * 255, 1


class MaskToolHost(Protocol):
    """What a mask tool may use of its layer (``MaskLayer`` implements it)."""

    canvas: PaintCanvas
    brush_color: QColor

    def size(self) -> int:
        """The brush size in image pixels (the brush and the eraser share it)."""

    def commit_stroke(self) -> None:
        """End the stroke in progress as one undoable action (no-op without one)."""

    def notify(self, message: str, kind: str = "info") -> None:
        """Show ``message`` as a toast (``kind``: success, info, warn or danger)."""


class MaskTool:
    """Base mask tool: ignores every input; subclasses override what they use.

    ``spec`` is the tool as the rail shows it; a sized tool (``spec.sized``) shows the
    brush ring, dashed when the tool ``erase``-s.
    """

    tool_id: ClassVar[str] = ""
    spec: ClassVar[ToolSpec]
    erase: ClassVar[bool] = False
    hint_text: ClassVar[tuple[str, str]] = ("", "")

    def __init__(self, host: MaskToolHost) -> None:
        """A tool of ``host``'s layer."""
        self.host = host

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """Mouse press at image pixel ``pos``."""

    def move(self, pos: QPoint) -> None:
        """A drag move to ``pos`` (only moves of the drag this tool began arrive here)."""

    def release(self, pos: QPoint) -> None:
        """End of the drag this tool began, at ``pos``."""

    def double_click(self, pos: QPoint) -> None:
        """Nothing: a double-click's second press arrives here, so it paints one dab, as today."""

    def key(self, event: QKeyEvent) -> bool:
        """The mask tools have no gesture keys: nothing is consumed."""
        return False

    def deactivate(self) -> None:
        """Another tool was chosen: end what this tool left in progress (nothing here)."""

    def hint(self) -> tuple[str, str]:
        """``(short hint, tooltip)`` for the status chip."""
        return self.hint_text


class BrushTool(MaskTool):
    """Paint the mask: a round dab at the press, then a round-capped segment per move."""

    tool_id = "brush"
    spec = ToolSpec("brush", "brush", "Brush", "B", "hold B + scroll to resize", True)
    hint_text = (
        "Brush · drag to paint",
        "Click and drag to paint the mask. Hold B and scroll, or press [ and ], to resize "
        "the brush.",
    )

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """Start a stroke: its first dab at ``pos``, in the layer's colour and brush size."""
        host = self.host
        host.canvas.begin(pos, host.brush_color, host.size(), erase=self.erase)

    def move(self, pos: QPoint) -> None:
        """Paint the segment from the stroke's last point to ``pos``."""
        self.host.canvas.extend(pos)

    def release(self, pos: QPoint) -> None:
        """End the stroke as one undoable action (the release point adds nothing, as today)."""
        self.host.commit_stroke()

    def deactivate(self) -> None:
        """Leaving the tool commits a stroke still in progress (there is no abort)."""
        self.host.commit_stroke()


class EraserTool(BrushTool):
    """Erase the mask with the brush's footprint (``DestinationOut``); its ring is dashed."""

    tool_id = "eraser"
    spec = ToolSpec("eraser", "erase", "Eraser", "E", "[ and ] resize", True)
    erase = True
    hint_text = (
        "Eraser · drag to erase",
        "Click and drag to erase the mask. [ and ] change the eraser size.",
    )


class FillTool(MaskTool):
    """Fill the closed outline under the click (its outer contour: holes are filled too)."""

    tool_id = "fill"
    spec = ToolSpec(
        "fill", "fill", "Fill a closed outline", "F", "click inside a closed outline", False
    )
    hint_text = (
        "Fill · click inside a closed outline",
        "Click inside a closed outline to fill it, holes included. Outside every closed "
        "outline nothing is filled.",
    )

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        """Fill the outline around ``pos`` (one undoable action); outside, say so instead."""
        if self.host.canvas.fill(pos) is None:
            self.host.notify(FILL_MISS_NOTICE, "info")


#: The mask tools in rail order.
TOOL_CLASSES: tuple[type[MaskTool], ...] = (BrushTool, EraserTool, FillTool)


def build_tools(host: MaskToolHost) -> dict[str, MaskTool]:
    """One instance of every mask tool for ``host``, keyed by tool id, in rail order."""
    return {cls.tool_id: cls(host) for cls in TOOL_CLASSES}


class FootprintRing:
    """The current tool's footprint on the pointer's pixel: the layer's brush ring.

    ``preview`` (a :class:`~app.gui.brush_preview.BrushPreview` in the layer colour, above
    both layers) shows while the layer allows it (active, visible, open, a sized tool) and
    ``is_panning()`` is False; it is dashed for a tool that erases. :meth:`place` runs on
    every pointer move, so it only moves the ring unless its visibility changes.
    """

    def __init__(
        self, scene: QGraphicsScene, color: str, is_panning: Callable[[], bool], diameter: int
    ) -> None:
        """A hidden solid ring of ``color`` and ``diameter`` px in ``scene``."""
        self.preview = BrushPreview(scene, color=color)
        self.preview.set_diameter(diameter)
        self._color = color
        self._is_panning = is_panning
        self._pos: QPoint | None = None
        self._allowed = False
        self._shown = False

    def set_tool(self, tool: MaskTool) -> None:
        """Draw ``tool``'s footprint: dashed when the tool erases."""
        self.preview.set_style(self._color, dashed=tool.erase)

    def set_diameter(self, diameter: int) -> None:
        """The footprint's diameter in image pixels (the brush size)."""
        self.preview.set_diameter(diameter)

    def allow(self, allowed: bool, *, forget: bool = False) -> None:
        """Whether the ring may show; ``forget`` drops the pointer until the next place."""
        self._allowed = allowed
        self.place(None if forget else self._pos)

    def place(self, pos: QPoint | None) -> None:
        """The pointer is on image pixel ``pos`` (None: unknown): ring there, or hidden."""
        self._pos = pos
        show = pos is not None and self._allowed and not self._is_panning()
        if show != self._shown:
            self._shown = show
            self.preview.set_visible(show)
        if show:
            self.preview.move_to_pixel(pos)
