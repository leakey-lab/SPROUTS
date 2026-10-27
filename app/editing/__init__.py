"""
Qt-free editing core shared by the editors.

- ``history``: ``SnapshotHistory``, bounded undo/redo stacks of whole-state snapshots.
- ``journal``: ``EditJournal``, the order of undoable actions across the layers of one
  image (the Correct workspace's one undo timeline).
- ``geometry``: point/segment/polyline picking and Catmull-Rom smoothing on
  ``(x, y)`` tuples.
- ``thinning``: ``thin_raster`` (a skeleton file thinned as Correct opens it),
  ``is_generated_as_opened`` and the lazy scikit-image import (numpy and cv2; imported
  on its own, not by this package).
- ``planes``: ``planes_equal``, ``SavedPlane`` and ``PlaneDocument``, the plane compare and
  the saved state the Correct mask and class-label documents share (the skeleton model
  keeps its saved skeleton in a ``SavedPlane`` too; numpy and cv2; imported on its own, not
  by this package).

The package imports neither PyQt6 nor torch, so it is unit-testable without a
QApplication and ``app.inference`` may import it. ``history``, ``journal`` and
``geometry`` (all this ``__init__`` imports) are standard library only, so importing
the package stays cheap.
"""

from __future__ import annotations

from app.editing.geometry import (
    catmull_rom_bezier,
    dist2_point_to_segment,
    nearest_polyline_index,
    nearest_segment_index,
    nearest_vertex_index,
    sample_catmull_rom,
)
from app.editing.history import SnapshotHistory
from app.editing.journal import EditJournal

__all__ = [
    "EditJournal",
    "SnapshotHistory",
    "catmull_rom_bezier",
    "dist2_point_to_segment",
    "nearest_polyline_index",
    "nearest_segment_index",
    "nearest_vertex_index",
    "sample_catmull_rom",
]
