"""
Qt-free editing core shared by the editors.

- ``history``: ``SnapshotHistory``, bounded undo/redo stacks of whole-state snapshots.
- ``geometry``: point/segment/polyline picking and Catmull-Rom smoothing on
  ``(x, y)`` tuples.

Standard library only (no Qt, numpy or torch), so it is cheap to import and
unit-testable without a QApplication.
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

__all__ = [
    "SnapshotHistory",
    "catmull_rom_bezier",
    "dist2_point_to_segment",
    "nearest_polyline_index",
    "nearest_segment_index",
    "nearest_vertex_index",
    "sample_catmull_rom",
]
