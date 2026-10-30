"""Per-instance memo for dashboard figures built from session-immutable data (PV-17).

A dashboard's DataFrame does not change while it is open (see DataCache), so a
figure built for one set of callback arguments can be served again: switching
back to a view, toggling an unrelated control or a stray click then costs a
dict lookup instead of a rebuild. Use one memo per dashboard instance (never a
module-level ``lru_cache``: that would pin the instance and share figures across
dashboards, F-012) and put a data version in the key so a refresh invalidates.

Memoized figures are shared: callers must treat them as read-only. (Setting a
layout value that depends only on the key, e.g. ``uirevision``, is idempotent.)
:func:`.dash_shell.browser_figure` relies on this: it keeps what Dash sends of a
figure on the figure, with its ``uirevision``.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import TypeVar

T = TypeVar("T")


class FigureMemo:
    """Small thread-safe LRU of built figures keyed by the builder arguments."""

    def __init__(self, maxsize: int = 16) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._figures: OrderedDict[Hashable, object] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_build(self, key: Hashable, build: Callable[[], T]) -> T:
        """Return the figure memoized under ``key``, building (and storing) it if absent.

        ``build`` runs outside the lock, so concurrent callbacks never wait on each
        other's figure builds; if an exception escapes ``build`` nothing is stored.
        """
        with self._lock:
            if key in self._figures:
                self._figures.move_to_end(key)
                return self._figures[key]  # type: ignore[return-value]
        figure = build()
        with self._lock:
            self._figures[key] = figure
            self._figures.move_to_end(key)
            while len(self._figures) > self._maxsize:
                self._figures.popitem(last=False)
        return figure

    def clear(self) -> None:
        """Forget every memoized figure."""
        with self._lock:
            self._figures.clear()
