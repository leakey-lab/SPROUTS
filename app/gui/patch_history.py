"""
Patch-based undo/redo for the raster editors (Correct Mask and Trace GT).

Qt-free. An editor keeps its document as one ``(h, w)`` uint8 *plane* (mask:
0/1, GT: class ids) next to the ARGB canvas it paints on. :class:`PatchHistory`
owns that committed plane and records every action (stroke, fill, clear) as
one :class:`Patch`: the box that changed, before and after. Undo writes the
``before`` values back, redo the ``after`` values, so the history costs the
size of the edited boxes, not a full canvas copy per action (a 25-deep history
of full ARGB snapshots was 300 MiB at 2048x1536).

Large patches (a clear, a big fill) are stored zlib-compressed. The stacks
are bounded by entry count and by a byte budget (see
:class:`app.editing.history.SnapshotHistory`, which does the bookkeeping).
"""

from __future__ import annotations

import zlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.editing.history import SnapshotHistory

Rect = tuple[int, int, int, int]  # x, y, width, height

DEFAULT_MAX_ENTRIES = 100
DEFAULT_BYTE_BUDGET = 64 * 1024 * 1024
# Regions at least this large are stored zlib-compressed (level 1: fast).
COMPRESS_MIN_BYTES = 16 * 1024
_ENTRY_OVERHEAD = 128  # rough per-patch Python object cost, counted in nbytes


def _pack(region: np.ndarray, compress: bool) -> bytes | np.ndarray:
    region = np.ascontiguousarray(region)
    return zlib.compress(region.tobytes(), 1) if compress else region.copy()


@dataclass(frozen=True, eq=False)
class Patch:
    """One undoable action: the box ``rect`` of the plane, before and after it."""

    x: int
    y: int
    width: int
    height: int
    dtype: np.dtype
    _before: bytes | np.ndarray
    _after: bytes | np.ndarray

    @classmethod
    def capture(
        cls,
        x: int,
        y: int,
        before: np.ndarray,
        after: np.ndarray,
        *,
        compress_min: int = COMPRESS_MIN_BYTES,
    ) -> Patch:
        """Record ``before``/``after`` (equal 2-D shapes) placed at ``(x, y)``; both are copied."""
        if before.shape != after.shape or before.ndim != 2:
            raise ValueError(f"patch shapes differ: {before.shape} vs {after.shape}")
        compress = before.nbytes >= compress_min
        height, width = before.shape
        return cls(
            int(x),
            int(y),
            int(width),
            int(height),
            before.dtype,
            _pack(before, compress),
            _pack(after, compress),
        )

    @property
    def rect(self) -> Rect:
        return self.x, self.y, self.width, self.height

    @property
    def is_empty(self) -> bool:
        return self.width == 0 or self.height == 0

    @property
    def slices(self) -> tuple[slice, slice]:
        """``plane[patch.slices]`` is the patched box."""
        return slice(self.y, self.y + self.height), slice(self.x, self.x + self.width)

    def _unpack(self, data: bytes | np.ndarray) -> np.ndarray:
        if isinstance(data, np.ndarray):
            return data
        flat = np.frombuffer(zlib.decompress(data), dtype=self.dtype)
        return flat.reshape(self.height, self.width)

    def before(self) -> np.ndarray:
        """The box as it was before the action (read-only: do not modify)."""
        return self._unpack(self._before)

    def after(self) -> np.ndarray:
        """The box as the action left it (read-only: do not modify)."""
        return self._unpack(self._after)

    @property
    def nbytes(self) -> int:
        """Stored size (compressed where it was compressed)."""
        return _stored_size(self._before) + _stored_size(self._after) + _ENTRY_OVERHEAD


def _stored_size(data: bytes | np.ndarray) -> int:
    return data.nbytes if isinstance(data, np.ndarray) else len(data)


class PatchHistory:
    """The committed plane of a raster editor plus its patch undo/redo stacks.

    ``reset(plane)`` when a document is opened; ``commit(rect, after)`` after
    each action; ``undo()`` / ``redo()`` restore the plane and return the patch
    whose ``rect`` the caller repaints. ``undo_stack`` / ``redo_stack`` are the
    live stacks (``len()`` = number of actions; ``clear()`` forgets them).
    """

    def __init__(
        self,
        *,
        maxlen: int = DEFAULT_MAX_ENTRIES,
        byte_budget: int | None = DEFAULT_BYTE_BUDGET,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._history: SnapshotHistory[Patch] = SnapshotHistory(
            maxlen, byte_budget=byte_budget, sizeof=lambda patch: patch.nbytes, on_change=on_change
        )
        self.plane: np.ndarray | None = None

    # -------------------- state --------------------
    @property
    def undo_stack(self) -> deque[Patch]:
        return self._history.undo_stack

    @property
    def redo_stack(self) -> deque[Patch]:
        return self._history.redo_stack

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    @property
    def nbytes(self) -> int:
        return self._history.nbytes

    @property
    def maxlen(self) -> int:
        return self._history.maxlen

    def clip(self, rect: Rect) -> Rect:
        """``rect`` intersected with the plane (zero size when outside or no plane)."""
        if self.plane is None:
            return 0, 0, 0, 0
        x, y, w, h = rect
        height, width = self.plane.shape
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(width, int(x) + int(w)), min(height, int(y) + int(h))
        return x0, y0, max(0, x1 - x0), max(0, y1 - y0)

    # -------------------- operations --------------------
    def reset(self, plane: np.ndarray | None) -> None:
        """Adopt ``plane`` (not copied) as the committed state and forget the history."""
        self.plane = plane
        self._history.clear()

    def commit(self, rect: Rect, after: np.ndarray) -> Patch:
        """Write ``after`` over ``rect`` of the plane and record it as one undoable action.

        Only the box of pixels that actually changed is stored; an action that
        changed nothing still counts as one (empty) entry, as each action always did.
        """
        if self.plane is None:
            raise ValueError("no plane: call reset() first")
        x, y, w, h = rect
        if self.clip(rect) != (x, y, w, h) or after.shape != (h, w):
            raise ValueError(f"rect {rect} / region {after.shape} do not fit the plane")
        region = self.plane[y : y + h, x : x + w]
        changed = region != after
        rows, cols = np.nonzero(changed.any(axis=1))[0], np.nonzero(changed.any(axis=0))[0]
        if rows.size:
            r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
            patch = Patch.capture(x + c0, y + r0, region[r0:r1, c0:c1], after[r0:r1, c0:c1])
            region[r0:r1, c0:c1] = after[r0:r1, c0:c1]
        else:
            empty = np.zeros((0, 0), self.plane.dtype)
            patch = Patch.capture(x, y, empty, empty)
        self._history.push(patch)
        return patch

    def undo(self) -> Patch | None:
        """Restore the state before the last action; ``None`` when there is nothing to undo."""
        if not self._history.can_undo:
            return None
        patch = self._history.undo_stack[-1]
        self._history.undo(patch)  # moves this same patch onto the redo stack
        self._write(patch, patch.before())
        return patch

    def redo(self) -> Patch | None:
        """Re-apply the last undone action; ``None`` when there is nothing to redo."""
        if not self._history.can_redo:
            return None
        patch = self._history.redo_stack[-1]
        self._history.redo(patch)
        self._write(patch, patch.after())
        return patch

    def _write(self, patch: Patch, values: np.ndarray) -> None:
        if self.plane is not None and not patch.is_empty:
            self.plane[patch.slices] = values
