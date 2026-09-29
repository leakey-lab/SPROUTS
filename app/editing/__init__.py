"""
Qt-free editing core shared by the editors.

- ``history``: ``SnapshotHistory``, bounded undo/redo stacks of whole-state snapshots.
- ``journal``: ``EditJournal``, the order of undoable actions across the layers of one
  image (the Correct workspace's one undo timeline).
- ``geometry``: point/segment/polyline picking and Catmull-Rom smoothing on
  ``(x, y)`` tuples.

The package stays standard library only (no Qt, numpy or torch), so it is cheap to
import and unit-testable without a QApplication.
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
