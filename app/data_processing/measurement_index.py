"""Read-only per-image lookup of measured root length and area (KNOWN_ISSUES #3).

The Measure batch threads write ``root_lengths.csv`` / ``root_areas.csv``
(schema :data:`app.config.LENGTH_CSV_HEADERS` / :data:`AREA_CSV_HEADERS`) and
nothing ever read them back per image, so the display metrics bar showed ``—``.
:class:`MeasurementIndex` reads those CSVs where the handlers write them
(located through :class:`app.project_layout.ProjectLayout`, the same locator
the visualisations use) and answers ``lookup(stem)``.

Contract:

* It never computes a metric. Values are returned exactly as the CSV holds
  them; a row whose ``Error`` column is filled gives ``None`` plus the message,
  never the ``0`` the thread writes for failures.
* A CSV is parsed once and re-parsed only when its ``(mtime_ns, size)``
  changes, so a warm lookup is one ``os.stat`` per metric plus a dict get.
* Qt-free and thread-safe: :meth:`MeasurementIndex.prewarm` is meant for a
  worker thread; ``lookup(stem, load=False)`` never parses and never blocks,
  which is what the GUI thread should call.
* The GUI thread's calls (``lookup(load=False)``, ``needs_load``, ``outdated``) look
  for a CSV only where Measure writes it today (a few stats). The legacy
  ``test_latest`` search walks the project folders and their parents (about 60 ms
  at 46k images), so only the worker's :meth:`MeasurementIndex.prewarm` runs it.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import NamedTuple

from app.config import AREA_VALUE_COLUMN, LENGTH_VALUE_COLUMN
from app.naming import image_stem
from app.project_layout import SKELETON_SUFFIX, ProjectLayout

logger = logging.getLogger(__name__)

__all__ = ["Measurement", "MetricTable", "MeasurementIndex"]

_IMAGE_COLUMN = "Image"
_ERROR_COLUMN = "Error"
_Signature = tuple[int, int]


class Measurement(NamedTuple):
    """Measured values for one image; ``None`` where nothing was measured."""

    length_mm: float | None
    area_mm2: float | None
    error: str | None


class _Row(NamedTuple):
    value: float | None
    error: str


def _key(name: str) -> str:
    return image_stem(str(name).strip())


def _signature(path: str) -> _Signature | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read_metric_csv(path: str, value_column: str) -> dict[str, _Row]:
    """``stem -> _Row`` for one metric CSV; ``{}`` (logged) when it is unreadable."""
    rows: dict[str, _Row] = {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            i_img = header.index(_IMAGE_COLUMN)
            i_val = header.index(value_column)
            i_err = header.index(_ERROR_COLUMN) if _ERROR_COLUMN in header else None
            for rec in reader:
                if len(rec) <= max(i_img, i_val):
                    continue
                err = rec[i_err].strip() if i_err is not None and i_err < len(rec) else ""
                try:
                    value = None if err else float(rec[i_val])
                except ValueError:
                    value = None
                rows[_key(rec[i_img])] = _Row(value, err)
    except (OSError, UnicodeDecodeError, csv.Error, ValueError) as exc:
        logger.warning("Cannot read measurements from %s: %s", path, exc)
        return {}
    return rows


class MetricTable:
    """One metric CSV, located lazily and re-parsed only when it changes.

    ``locate`` returns the CSV path (or ``None``); ``locate_fast`` (default: ``locate``)
    is the search the GUI thread's calls (``get(load=False)``, :meth:`needs_load`) use,
    while :meth:`load` and ``get(load=True)`` (a worker's) use ``locate``, which may walk
    folders. A path that was found is re-used until it disappears; a failed search is
    retried at most every ``relocate_after_s`` seconds.
    """

    def __init__(
        self,
        value_column: str,
        locate: Callable[[], str | None],
        *,
        locate_fast: Callable[[], str | None] | None = None,
        relocate_after_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.value_column = value_column
        self._locate = locate
        self._locate_fast = locate_fast or locate
        self._relocate_after_s = relocate_after_s
        self._clock = clock
        self._lock = threading.Lock()
        self._path: str | None = None
        self._searched_at: float | None = None  # the last search (either kind)
        self._searched_full_at: float | None = None  # the last full search
        self._loaded_sig: _Signature | None = None
        self._rows: dict[str, _Row] = {}

    @property
    def path(self) -> str | None:
        """The CSV in use, or ``None`` when none has been found."""
        return self._path

    def invalidate(self) -> None:
        """Forget the located path and the parsed rows (e.g. after a Measure run)."""
        with self._lock:
            self._path, self._searched_at, self._searched_full_at = None, None, None
            self._loaded_sig, self._rows = None, {}

    def _resolve(self, full: bool) -> tuple[str, _Signature] | None:
        """The CSV and its signature; ``full`` searches with ``locate``, else ``locate_fast``."""
        if self._path is not None:
            sig = _signature(self._path)
            if sig is not None:
                return self._path, sig
            self._path = None  # moved or deleted: search again now
            self._searched_at = self._searched_full_at = None
        now = self._clock()
        last = self._searched_full_at if full else self._searched_at
        if last is not None and now - last < self._relocate_after_s:
            return None
        self._searched_at = now
        if full:
            self._searched_full_at = now
        path = (self._locate if full else self._locate_fast)()
        sig = _signature(path) if path else None
        if sig is None:
            return None
        self._path = os.path.normpath(path)
        return self._path, sig

    def _current(self, load: bool) -> dict[str, _Row] | None:
        """Rows matching the CSV on disk, or ``None`` (absent, or cold with ``load=False``).

        ``load=False`` (the GUI thread) searches with ``locate_fast`` only."""
        found = self._resolve(full=load)
        if found is None:
            self._loaded_sig, self._rows = None, {}
            return None
        path, sig = found
        if sig != self._loaded_sig:
            if not load:
                return None
            self._rows = _read_metric_csv(path, self.value_column)
            self._loaded_sig = sig
        return self._rows

    def get(self, stem: str, *, load: bool = True) -> _Row | None:
        """Row for ``stem``; with ``load=False`` never parses and never blocks."""
        if not self._lock.acquire(blocking=load):
            return None  # a worker is parsing: report nothing rather than wait
        try:
            rows = self._current(load)
            return None if rows is None else rows.get(_key(stem))
        finally:
            self._lock.release()

    def needs_load(self) -> bool:
        """True when a CSV exists whose current contents are not parsed yet (looked for
        with ``locate_fast``: a GUI-thread call)."""
        with self._lock:
            found = self._resolve(full=False)
            return found is not None and found[1] != self._loaded_sig

    def load(self) -> None:
        """Parse the CSV now if it changed (call from a worker thread)."""
        with self._lock:
            self._current(load=True)


class MeasurementIndex:
    """Measured length / area per image of one project folder (read-only).

    ``project_root`` is the folder the library loaded (``ProjectLayout.root``).
    """

    def __init__(
        self,
        project_root: str,
        *,
        relocate_after_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.layout = ProjectLayout(project_root)
        opts = {"relocate_after_s": relocate_after_s, "clock": clock}
        self._length = MetricTable(
            LENGTH_VALUE_COLUMN,
            self.layout.find_lengths_csv,  # with the legacy test_latest walk: workers only
            locate_fast=partial(self.layout.find_lengths_csv, search_test_latest=False),
            **opts,
        )
        self._area = MetricTable(AREA_VALUE_COLUMN, self.layout.find_areas_csv, **opts)

    @property
    def root(self) -> str:
        return self.layout.root

    def lookup(self, stem: str, *, load: bool = True) -> Measurement:
        """``(length_mm, area_mm2, error)`` for ``stem`` (a stem, file name or path).

        ``load=False`` answers only from already-parsed CSVs (GUI thread); pair
        it with :meth:`needs_load` + :meth:`prewarm` in a worker.
        """
        length = self._length.get(stem, load=load)
        area = self._area.get(stem, load=load)
        errors = [
            f"{label}: {row.error}"
            for label, row in (("Length", length), ("Area", area))
            if row is not None and row.error
        ]
        return Measurement(
            length.value if length is not None else None,
            area.value if area is not None else None,
            "; ".join(errors) or None,
        )

    def needs_load(self) -> bool:
        """True when a CSV exists whose current contents are not parsed yet."""
        return self._length.needs_load() or self._area.needs_load()

    def prewarm(self) -> None:
        """Look for the CSVs everywhere (legacy folders too) and parse any new or changed
        one (≈90–360 ms for 46k rows, plus the legacy search: run off the GUI thread)."""
        self._length.load()
        self._area.load()

    def invalidate(self) -> None:
        """Re-locate and re-read both CSVs on the next lookup (after a Measure run)."""
        self._length.invalidate()
        self._area.invalidate()

    def sources(self) -> dict[str, str | None]:
        """``{"length": csv path | None, "area": csv path | None}`` currently in use."""
        return {"length": self._length.path, "area": self._area.path}

    def outdated(self, stem: str) -> dict[str, bool]:
        """``{"length": bool, "area": bool}``: whether the input each value of ``stem`` was
        measured from changed after its CSV was written.

        Each metric against its own input: length against the generated skeleton
        ``<csv dir>/<stem>_fake.png``, area against the mask ``<csv dir>/<stem>.png`` (the
        handlers write each CSV next to the files they measured), so a saved mask never
        outdates the length. A metric without a row for ``stem`` is never outdated. mtime
        based, so a copied folder may report ``True`` spuriously; it never changes a value.
        """
        stem = _key(stem)
        return {
            "length": self._table_outdated(self._length, stem, stem + SKELETON_SUFFIX),
            "area": self._table_outdated(self._area, stem, stem + ".png"),
        }

    def is_outdated(self, stem: str) -> bool:
        """True when either value of ``stem`` is outdated (see :meth:`outdated`)."""
        return any(self.outdated(stem).values())

    @staticmethod
    def _table_outdated(table: MetricTable, stem: str, file_name: str) -> bool:
        csv_path = table.path
        if csv_path is None or table.get(stem, load=False) is None:
            return False
        csv_sig = _signature(csv_path)
        src_sig = _signature(os.path.join(os.path.dirname(csv_path), file_name))
        return csv_sig is not None and src_sig is not None and src_sig[0] > csv_sig[0]
