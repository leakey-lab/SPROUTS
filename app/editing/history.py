"""
Bounded undo/redo history of whole-state snapshots, shared by the editors.

Qt-free: a snapshot can be anything (an ndarray mask, a QPixmap, ...). The
owner keeps the *current* state; the history only stores the states around it:

- ``push(before)`` records the state an edit is about to change and forgets
  the redo branch;
- ``undo(current)`` parks ``current`` on the redo stack and returns the state
  to restore (``None`` when there is nothing to undo);
- ``redo(current)`` does the reverse;
- ``drop_redo()`` forgets the redo branch when an action elsewhere on a shared
  timeline ends it (the Correct workspace's per-layer histories).

Snapshots must not be mutated once handed over: push copies, not live buffers.
The history is bounded by entry count (``maxlen``) and optionally by memory
(``byte_budget``).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


def _nbytes(snapshot: object) -> int:
    """Default snapshot size: the ``nbytes`` of an ndarray-like snapshot."""
    try:
        return int(snapshot.nbytes)  # type: ignore[attr-defined]
    except AttributeError:
        raise TypeError(
            f"cannot size a {type(snapshot).__name__} snapshot: pass sizeof= to SnapshotHistory"
        ) from None


class SnapshotHistory(Generic[T]):
    """Undo and redo stacks of snapshots, with an entry cap and an optional byte cap.

    With a ``byte_budget``, the oldest undo snapshots are dropped first, then
    the farthest redo snapshots, until both stacks fit. The newest undo
    snapshot and the next redo snapshot are always kept, so one undo (and the
    redo after it) works even when a single snapshot exceeds the budget.

    ``on_change`` (constructor argument or attribute) is called with no
    arguments after every call that changed the stacks, e.g. to refresh the
    enabled state of Undo/Redo buttons.
    """

    def __init__(
        self,
        maxlen: int = 25,
        *,
        byte_budget: int | None = None,
        sizeof: Callable[[T], int] | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        if maxlen < 1:
            raise ValueError(f"maxlen must be at least 1, got {maxlen}")
        if byte_budget is not None and byte_budget <= 0:
            raise ValueError(f"byte_budget must be positive, got {byte_budget}")
        self._maxlen = int(maxlen)
        self._byte_budget = byte_budget
        self._sizeof: Callable[[T], int] = sizeof or _nbytes
        self.on_change = on_change
        self._undo: deque[T] = deque(maxlen=self._maxlen)
        self._redo: deque[T] = deque(maxlen=self._maxlen)

    # -------------------- state --------------------
    @property
    def maxlen(self) -> int:
        """Maximum number of snapshots on each stack."""
        return self._maxlen

    @property
    def byte_budget(self) -> int | None:
        """Memory cap over both stacks, or ``None`` for entry-count bounding only."""
        return self._byte_budget

    @property
    def undo_stack(self) -> deque[T]:
        """Snapshots to undo to, oldest first. Read-only: change it through the methods."""
        return self._undo

    @property
    def redo_stack(self) -> deque[T]:
        """Snapshots to redo to, farthest first (``redo`` pops from the right)."""
        return self._redo

    @property
    def can_undo(self) -> bool:
        """True when there is a state to undo to."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """True when there is a state to redo to."""
        return bool(self._redo)

    @property
    def nbytes(self) -> int:
        """Total size of every stored snapshot, as measured by ``sizeof``."""
        return sum(map(self._sizeof, self._undo)) + sum(map(self._sizeof, self._redo))

    # -------------------- operations --------------------
    def push(self, snapshot: T) -> None:
        """Record the state an edit is about to change; the redo branch is forgotten."""
        self._undo.append(snapshot)
        self._redo.clear()
        self._enforce_budget()
        self._changed()

    def undo(self, current: T) -> T | None:
        """Park ``current`` for redo and return the previous state (``None``: nothing to undo)."""
        if not self._undo:
            return None
        previous = self._undo.pop()
        self._redo.append(current)
        self._enforce_budget()
        self._changed()
        return previous

    def redo(self, current: T) -> T | None:
        """Park ``current`` for undo and return the next state (``None``: nothing to redo)."""
        if not self._redo:
            return None
        following = self._redo.pop()
        self._undo.append(current)
        self._enforce_budget()
        self._changed()
        return following

    def drop_redo(self) -> None:
        """Forget the redo branch and keep the undo stack.

        For a history that shares one timeline with other histories: a new action
        elsewhere ends this branch, as ``push`` would. ``on_change`` fires only when
        something was dropped.
        """
        if not self._redo:
            return
        self._redo.clear()
        self._changed()

    def clear(self) -> None:
        """Forget every snapshot (e.g. when a new document is loaded)."""
        if not (self._undo or self._redo):
            return
        self._undo.clear()
        self._redo.clear()
        self._changed()

    def map(self, fn: Callable[[T], T]) -> None:
        """Replace every snapshot ``s`` with ``fn(s)``, keeping the order.

        For edits that must reach the history too, such as a GT class remap
        that renumbers the ids stored in every snapshot.
        """
        if not (self._undo or self._redo):
            return
        for stack in (self._undo, self._redo):
            # In place, so callers holding undo_stack/redo_stack keep the live stacks.
            rewritten = [fn(snapshot) for snapshot in stack]
            stack.clear()
            stack.extend(rewritten)
        self._enforce_budget()
        self._changed()

    # -------------------- internals --------------------
    def _enforce_budget(self) -> None:
        if self._byte_budget is None:
            return
        total = self.nbytes
        while total > self._byte_budget and len(self._undo) > 1:
            total -= self._sizeof(self._undo.popleft())
        while total > self._byte_budget and len(self._redo) > 1:
            total -= self._sizeof(self._redo.popleft())

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()
