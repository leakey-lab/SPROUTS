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
  changes, so a warm lookup is one ``os.stat`` per metric (two with the record of its
  inputs, ``<csv>.inputs.json``, read with it) plus a dict get. The calls of one metrics-bar
  refresh share those stats (:meth:`MeasurementIndex.reading`).
* Qt-free and thread-safe: :meth:`MeasurementIndex.prewarm` is meant for a
  worker thread; ``lookup(stem, load=False)`` never parses and never blocks,
  which is what the GUI thread should call.
* The GUI thread's calls (``lookup(load=False)``, ``needs_load``, ``outdated``) look
  for a CSV only where Measure writes it (a few stats). The legacy ``test_latest`` search
  walks the project folders and their parents (about 100 ms at 46k images), so only the
  worker's :meth:`MeasurementIndex.prewarm` runs it; the index keeps what that walk found,
  and :meth:`MeasurementIndex.find_lengths_csv` (the dashboards' check) uses it.
* GT first (O1): a row's ``Source`` says which file it was measured from, the GT file
  (``gt/skeletons/<stem>.png``, ``gt/mask/<stem>.png``) or the prediction next to the CSV;
  freshness follows that file, and the provenance sidecar says why a GT file was not used
  (:meth:`MeasurementIndex.gt_skip_reason`).
"""

from __future__ import annotations

import csv
import itertools
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any, NamedTuple

from app import project_layout
from app.calibration import ProjectCalibration, fov_of, method_note
from app.config import AREA_VALUE_COLUMN, LENGTH_VALUE_COLUMN, SOURCE_COLUMN
from app.gt.files import SOURCE_GT, SOURCE_PREDICTION
from app.inference.measure_inputs import (
    InputsRecord,
    content_sha256,
    file_key,
    input_key,
    inputs_record_path,
    load_inputs_record,
)
from app.naming import image_stem, stem_key
from app.project_layout import SKELETON_SUFFIX, ProjectLayout
from app.provenance import read_sidecar

logger = logging.getLogger(__name__)

__all__ = ["Measurement", "MetricTable", "MeasurementIndex"]

_IMAGE_COLUMN = "Image"
_ERROR_COLUMN = "Error"
_SOURCE_COLUMN = SOURCE_COLUMN
_Signature = tuple[int, int]
#: A table's signature: its CSV's and its record's (``None``: no record file).
_TableSignature = tuple[_Signature, _Signature | None]
_Found = tuple[str, _TableSignature] | None  # the CSV in use and its signature
_HOLDS = itertools.count(1)  # tokens of MetricTable.held


class Measurement(NamedTuple):
    """Measured values for one image; ``None`` where nothing was measured.

    ``length_source`` / ``area_source``: the row's ``Source`` cell (``"gt"`` or
    ``"prediction"``, O1); ``None`` without a row, or for a CSV written before that column.
    """

    length_mm: float | None
    area_mm2: float | None
    error: str | None
    length_source: str | None = None
    area_source: str | None = None


class _Row(NamedTuple):
    value: float | None
    error: str
    source: str | None = None
    #: The row's own stem, as Measure wrote it: its prediction's stem, which can differ in
    #: case from the stem it is looked up by (O7).
    stem: str = ""


def _key(name: str) -> str:
    """The stem ``name`` names: an image path's file stem; anything else is a stem, taken as it
    is. The Library's stem of ``X.png.jpg`` is ``X.png``: stripped again it would be ``X``,
    another image's (final review D1), so a bare name is never stripped."""
    text = str(name).strip()
    return image_stem(text) if "/" in text or "\\" in text else text


class _Rows:
    """One CSV's rows by exact stem, with a fallback by stem key (O7) for a stem that has
    no exact row; a key that two rows share (``Ab`` and ``aB``) finds nothing there, so
    a lookup never returns another image's value."""

    __slots__ = ("exact", "_by_key")

    def __init__(self, exact: dict[str, _Row]):
        self.exact = exact
        by_key: dict[str, _Row | None] = {}
        for stem, row in exact.items():
            key = stem_key(stem)
            by_key[key] = None if key in by_key else row
        self._by_key = by_key

    def get(self, stem: str) -> _Row | None:
        row = self.exact.get(stem)
        return row if row is not None else self._by_key.get(stem_key(stem))


_NO_ROWS = _Rows({})


def _signature(path: str) -> _Signature | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _table_signature(path: str) -> _TableSignature | None:
    """The CSV's ``(mtime_ns, size)`` and its inputs record's (a record written after the
    CSV changes it); ``None`` when the CSV is absent."""
    sig = _signature(path)
    return None if sig is None else (sig, _signature(inputs_record_path(path)))


def _gt_skipped_of(record: dict[str, Any] | None) -> dict[str, str]:
    """``stem_key -> reason`` of the GT files a Measure run did not use, from the CSV's
    provenance ``record`` (``inputs.gt.skipped``); empty when it has none."""
    try:
        skipped = record["inputs"]["gt"]["skipped"]
        return {stem_key(str(item["stem"])): str(item["reason"]) for item in skipped}
    except KeyError, TypeError:
        return {}


def _method_of(record: dict[str, Any] | None, calibration: dict[str, float] | None) -> dict | None:
    """What :func:`app.calibration.method_note` reads of a CSV's provenance: the field of
    view it was measured with and its ``parameters.estimator``; ``None`` without a record."""
    if record is None:
        return None
    parameters = record.get("parameters")
    estimator = parameters.get("estimator") if isinstance(parameters, dict) else None
    return {"calibration": calibration, "parameters": {"estimator": estimator}}


def _read_metric_csv(path: str, value_column: str) -> _Rows:
    """The rows of one metric CSV by stem (:class:`_Rows`); none (logged) when it is
    unreadable. The ``Source`` column is read when the header has it (a CSV written before
    it gives ``None``)."""
    rows: dict[str, _Row] = {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            i_img = header.index(_IMAGE_COLUMN)
            i_val = header.index(value_column)
            i_err = header.index(_ERROR_COLUMN) if _ERROR_COLUMN in header else None
            i_src = header.index(_SOURCE_COLUMN) if _SOURCE_COLUMN in header else None
            for rec in reader:
                if len(rec) <= max(i_img, i_val):
                    continue
                err = rec[i_err].strip() if i_err is not None and i_err < len(rec) else ""
                src = rec[i_src].strip() if i_src is not None and i_src < len(rec) else ""
                try:
                    value = None if err else float(rec[i_val])
                except ValueError:
                    value = None
                stem = rec[i_img].strip()  # Measure's stems
                rows[stem] = _Row(value, err, src or None, stem)
    except (OSError, UnicodeDecodeError, csv.Error, ValueError) as exc:
        logger.warning("Cannot read measurements from %s: %s", path, exc)
        return _NO_ROWS
    return _Rows(rows)


class MetricTable:
    """One metric CSV, located lazily and re-parsed only when it changes.

    ``locate`` returns the CSV path (or ``None``); ``locate_fast`` (default: ``locate``)
    is the search the GUI thread's calls (``get(load=False)``, :meth:`needs_load`) use,
    while :meth:`load` and ``get(load=True)`` (a worker's) use ``locate``, which may walk
    folders. A path that was found is re-used until it disappears; a failed search is
    retried at most every ``relocate_after_s`` seconds. The record of the CSV's inputs
    (``<csv>.inputs.json``, R5) is read with it: :attr:`record`.
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
        self._loaded_sig: _TableSignature | None = None
        self._rows: _Rows = _NO_ROWS
        self._record: InputsRecord | None = None
        self._gt_skipped: dict[str, str] = {}
        self._calibration: dict[str, float] | None = None
        self._method: dict | None = None
        # A :meth:`held` block: (thread id, token); and its one look for the CSV, (token,
        # found), kept under the lock.
        self._hold: tuple[int, int] | None = None
        self._held: tuple[int, _Found] | None = None

    @contextmanager
    def held(self) -> Iterator[None]:
        """Within the block, this thread's calls that do not load (``get(load=False)``,
        :meth:`needs_load`) look for the CSV and stat it once for all of them, not once each
        (a metrics-bar refresh asked four times). A load meanwhile (a worker's :meth:`load`)
        drops what was held: the calls after it look again."""
        previous = self._hold
        self._hold = (threading.get_ident(), next(_HOLDS))
        try:
            yield
        finally:
            self._hold, self._held = previous, None

    @property
    def path(self) -> str | None:
        """The CSV in use, or ``None`` when none has been found."""
        return self._path

    @property
    def record(self) -> InputsRecord | None:
        """The record of the inputs the loaded CSV was measured from; ``None`` when it has
        none (or one written for another CSV)."""
        return self._record

    @property
    def calibration(self) -> dict[str, float] | None:
        """The field of view the loaded CSV was measured with (``fov_width_mm``,
        ``fov_height_mm``, from its provenance sidecar); ``None`` without a sidecar (or before
        the CSV is loaded)."""
        return self._calibration

    @property
    def method(self) -> dict | None:
        """The loaded CSV's field of view and length method (``calibration``,
        ``parameters.estimator``) as :func:`app.calibration.method_note` reads them; ``None``
        without a provenance sidecar (or before the CSV is loaded)."""
        return self._method

    def gt_skip_reason(self, stem: str) -> str:
        """Why the GT file of ``stem`` was not measured (``size``, ``unedited``,
        ``unreadable``: the CSV's provenance), ``""`` when it was or there is none."""
        return self._gt_skipped.get(stem_key(stem), "")

    def invalidate(self) -> None:
        """Forget the located path and the parsed rows (e.g. after a Measure run)."""
        with self._lock:
            self._path, self._searched_at, self._searched_full_at = None, None, None
            self._loaded_sig, self._rows, self._record = None, _NO_ROWS, None
            self._gt_skipped, self._calibration, self._method = {}, None, None
            self._held = None

    def _resolve(self, full: bool) -> _Found:
        """The CSV and its signature (lock held); ``full`` searches with ``locate``, else
        ``locate_fast``, once per :meth:`held` block of this thread."""
        if full:
            self._held = None  # a load: the held calls after it look again
            return self._find(full)
        hold = self._hold
        token = hold[1] if hold is not None and hold[0] == threading.get_ident() else None
        held = self._held
        if token is not None and held is not None and held[0] == token:
            return held[1]
        found = self._find(full)
        if token is not None:
            self._held = (token, found)
        return found

    def _find(self, full: bool) -> _Found:
        """:meth:`_resolve`'s look for the CSV, with its stats."""
        if self._path is not None:
            sig = _table_signature(self._path)
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
        if not path:
            return None
        path = os.path.normpath(path)
        sig = _table_signature(path)
        if sig is None:
            return None
        self._path = path
        return self._path, sig

    def _current(self, load: bool) -> _Rows | None:
        """Rows matching the CSV on disk, or ``None`` (absent, or cold with ``load=False``).

        ``load=False`` (the GUI thread) searches with ``locate_fast`` only."""
        found = self._resolve(full=load)
        if found is None:
            self._loaded_sig, self._rows, self._record = None, _NO_ROWS, None
            self._gt_skipped, self._calibration, self._method = {}, None, None
            return None
        path, sig = found
        if sig != self._loaded_sig:
            if not load:
                return None
            if self._loaded_sig is None or sig[0] != self._loaded_sig[0]:
                self._rows = _read_metric_csv(path, self.value_column)
            self._record = load_inputs_record(path)
            sidecar = read_sidecar(path)  # written with the record; read once per load
            self._gt_skipped = _gt_skipped_of(sidecar)
            self._calibration = fov_of(sidecar.get("calibration")) if sidecar else None
            self._method = _method_of(sidecar, self._calibration)
            self._loaded_sig = sig
        return self._rows

    def get(self, stem: str, *, load: bool = True) -> _Row | None:
        """Row for ``stem`` (its exact stem first, then without case, O7); with
        ``load=False`` never parses and never blocks."""
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


class _Walk:
    """The legacy ``test_latest`` walk (:func:`app.project_layout.find_test_latest_dir`,
    looked up at call time), keeping what it found. It never refers back to its index, so
    refcounting frees an index: a cycle would survive a ``gc.freeze()`` (:mod:`app.gc_policy`)."""

    __slots__ = ("found",)

    def __init__(self) -> None:
        #: ``(folder or None,)``: what the last walk found; ``None`` before one ran.
        self.found: tuple[str | None] | None = None

    def __call__(self, root: str) -> str | None:
        found = project_layout.find_test_latest_dir(root)
        self.found = (found,)
        return found


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
        #: The legacy ``test_latest`` walk and what it last found (``found``), which
        #: :meth:`invalidate` keeps: the app never makes such a folder.
        self._walk = _Walk()
        opts = {"relocate_after_s": relocate_after_s, "clock": clock}
        self._length = MetricTable(
            LENGTH_VALUE_COLUMN,
            # with the legacy test_latest walk: workers only
            partial(self.layout.find_lengths_csv, walk=self._walk),
            locate_fast=partial(self.layout.find_lengths_csv, search_test_latest=False),
            **opts,
        )
        self._area = MetricTable(AREA_VALUE_COLUMN, self.layout.find_areas_csv, **opts)
        #: ``{"length": {stem_key: path}, "area": {...}}``: the GT files (``gt/skeletons/``,
        #: ``gt/mask/``) as :meth:`prewarm` listed them on a worker. The GUI thread's lookups
        #: (:meth:`outdated`, :meth:`measured_from`) only stat; they never list a folder.
        self._gt_listing: dict[str, dict[str, str]] = {"length": {}, "area": {}}
        #: folder -> :func:`app.inference.measure_inputs.key_prefix` (for :meth:`_input_key`).
        self._prefixes: dict[str, str | None] = {}

    @property
    def root(self) -> str:
        return self.layout.root

    @contextmanager
    def reading(self) -> Iterator[None]:
        """One group of this thread's calls (a metrics-bar refresh): those that do not load
        (``lookup(load=False)``, :meth:`needs_load`, :meth:`outdated`,
        :meth:`measured_from`) stat each CSV and its record once for all of them, 4 stats
        instead of 16 (:meth:`MetricTable.held`)."""
        with self._length.held(), self._area.held():
            yield

    def _input_key(self, path: str) -> str:
        """``input_key(path, root)``: one relpath per folder for an absolute path (a relative
        one follows the working folder, so it is keyed alone)."""
        root = self.layout.root
        if os.path.isabs(path) and os.path.isabs(root):
            return file_key(path, root, self._prefixes)
        return input_key(path, root)

    def find_lengths_csv(self) -> str | None:
        """The project's ``root_lengths.csv``, as :meth:`ProjectLayout.find_lengths_csv`
        finds it, with stats only once a walk for a legacy ``test_latest`` folder has run
        (:meth:`prewarm`, at project open): that walk's folder is used. Before one ran it
        walks itself, as the layout does. The dashboards' check on the GUI thread."""
        walked = self._walk.found
        if walked is None:
            return self.layout.find_lengths_csv(walk=self._walk)
        return self.layout.find_lengths_csv(walk=lambda _root: walked[0])

    def lookup(self, stem: str, *, load: bool = True) -> Measurement:
        """``(length_mm, area_mm2, error, length_source, area_source)`` for ``stem`` (a
        stem as the Library lists it, or an image path; a bare name is a stem, ``_key``).

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
            length.source if length is not None else None,
            area.source if area is not None else None,
        )

    def needs_load(self) -> bool:
        """True when a CSV exists whose current contents are not parsed yet."""
        return self._length.needs_load() or self._area.needs_load()

    def prewarm(self) -> None:
        """Look for the CSVs everywhere (legacy folders too) and parse any new or changed
        one (≈90–360 ms for 46k rows, plus the legacy search: run off the GUI thread), and
        list the GT folders (one scandir each), which the GUI thread's lookups use."""
        self._length.load()
        self._area.load()
        layout = self.layout
        self._gt_listing = {
            "length": {stem_key(stem): path for stem, path in layout.scan_gt_skeletons().items()},
            "area": {stem_key(stem): path for stem, path in layout.scan_gt_masks().items()},
        }

    def _gt_path(self, metric: str, stem: str) -> str:
        """``gt/skeletons/<stem>.png`` (length) or ``gt/mask/<stem>.png`` (area): the name
        *Correct* saves under, ``stem`` taken as it is (never stripped again, D1)."""
        layout = self.layout
        if metric == "length":
            return layout.gt_skeleton_path_for_stem(stem)
        return layout.gt_mask_path_for_stem(stem)

    def _gt_file(self, metric: str, stem: str) -> str | None:
        """The GT file of ``stem`` for ``metric``, with a few stats and never a folder
        listing (GUI thread). The file named exactly like the image
        (``gt/skeletons/<stem>.png`` / ``gt/mask/<stem>.png``, the name *Correct* saves
        under) wins when it is another file than the one the last :meth:`prewarm` listed for
        its stem key, as Measure's ``split_inputs`` keeps it (a case-sensitive disk with
        both ``o.png`` and ``O.png``). Otherwise the listed file, while it is still there:
        its name as on disk, so its record key matches (a case-insensitive disk finds
        ``O.png`` for ``o`` under either name). Else the exact name (a GT file saved since)."""
        path = self._gt_path(metric, stem)
        listed = self._gt_listing[metric].get(stem_key(stem))
        if listed is not None and os.path.isfile(listed):
            try:
                same = listed == path or not os.path.isfile(path) or os.path.samefile(listed, path)
            except OSError:  # one of them vanished since the stat: keep the one still there
                same = not os.path.isfile(path)
            return listed if same else path  # two files of one key: the one named like the image
        return path if os.path.isfile(path) else None

    def invalidate(self) -> None:
        """Re-locate and re-read both CSVs on the next lookup (after a Measure run)."""
        self._length.invalidate()
        self._area.invalidate()

    def calibrations(self) -> dict[str, dict[str, float] | None]:
        """``{"length": cal | None, "area": cal | None}``: the field of view each loaded CSV
        was measured with (``fov_width_mm``, ``fov_height_mm``); ``None`` for a metric without
        a CSV, or whose CSV has no provenance sidecar. Parsed state only (GUI thread)."""
        return {"length": self._length.calibration, "area": self._area.calibration}

    def method_notes(self, current: ProjectCalibration) -> dict[str, str]:
        """``{"length": note, "area": note}``: why each loaded CSV's values cannot be
        compared with new ones measured with ``current`` (another field of view, or for
        length another length method; :func:`app.calibration.method_note`); ``""`` = none.
        Parsed state only (GUI thread)."""
        return {
            "length": method_note(self._length.method, kind="length", current=current),
            "area": method_note(self._area.method, kind="area", current=current),
        }

    def sources(self) -> dict[str, str | None]:
        """``{"length": csv path | None, "area": csv path | None}`` currently in use."""
        return {"length": self._length.path, "area": self._area.path}

    def by_content(self) -> dict[str, bool]:
        """``{"length": bool, "area": bool}``: whether :meth:`outdated` compares that
        metric's inputs by content (its CSV has a record of them) rather than file time."""
        return {"length": self._length.record is not None, "area": self._area.record is not None}

    def gt_skip_reason(self, stem: str) -> dict[str, str]:
        """``{"length": reason, "area": reason}`` for each metric whose last Measure run did
        not use the GT file of ``stem`` (``size``, ``unedited``, ``unreadable``; from the
        CSVs' provenance, ``inputs.gt.skipped``); metrics without such a file are left out."""
        stem = _key(stem)
        found = {
            "length": self._length.gt_skip_reason(stem),
            "area": self._area.gt_skip_reason(stem),
        }
        return {metric: reason for metric, reason in found.items() if reason}

    def measured_from(self, stem: str) -> dict[str, tuple[str, str] | None]:
        """``{"length": (file, source), "area": (file, source)}``: the file each value of
        ``stem`` was measured from, relative to the project folder (``gt/skeletons/g.png``,
        ``skeletons/p_fake.png``), and its row's ``Source`` (``gt`` / ``prediction``; a CSV
        without the column is ``prediction``); ``None`` for a metric without a row. Only
        parsed CSVs are read (GUI thread); a GT file is looked for with a stat
        (:meth:`_gt_file`)."""
        stem = _key(stem)
        length = self._measured("length", stem, SKELETON_SUFFIX)
        area = self._measured("area", stem, ".png")
        return {
            metric: None if found is None else (self._input_key(found[0]), found[1])
            for metric, found in (("length", length), ("area", area))
        }

    def _table(self, metric: str) -> MetricTable:
        return self._length if metric == "length" else self._area

    def _measured(self, metric: str, stem: str, suffix: str) -> tuple[str, str] | None:
        """``(path, source)`` of the file ``stem``'s row of ``metric`` was measured from."""
        table = self._table(metric)
        csv_path = table.path
        row = table.get(stem, load=False) if csv_path is not None else None
        if row is None:
            return None
        if row.source == SOURCE_GT:
            return self._gt_file(metric, stem) or self._gt_path(metric, stem), SOURCE_GT
        return _prediction_of(csv_path, row, suffix), SOURCE_PREDICTION

    def outdated(self, stem: str) -> dict[str, bool]:
        """``{"length": bool, "area": bool}``: whether the files each value of ``stem`` was
        measured from changed since it was measured.

        Each metric against its own files (a saved mask never outdates the length): the
        file the row names (``Source``, O1) — the GT file (``gt/skeletons/<stem>.png``,
        ``gt/mask/<stem>.png``) for a ``gt`` row, else the prediction next to the CSV
        (``<csv dir>/<stem>_fake.png``, ``<csv dir>/<stem>.png``, with the row's own stem,
        which may differ in case, O7; a second prediction named exactly like the image makes
        it outdated: Measure would read that one) — and, for a
        ``prediction`` row, the image's GT file when one exists (one saved after the CSV
        makes the value outdated; a regenerated prediction under a ``gt`` row does not). A
        metric without a row for ``stem`` is never outdated. With the record of the CSV's
        inputs (``<csv>.inputs.json``, R5) each file is compared with its entry: equal size
        and time, else equal content, is current (a copied or restamped folder stays
        current); a file that is gone, or has no entry, is outdated. Without a record, a file
        written after the CSV is outdated (mtime, so a copied folder may report ``True``
        spuriously): for a ``prediction`` row its prediction and the image's GT file, if
        any; for a ``gt`` row its GT file alone, so a regenerated prediction under it does
        not count (the status line, which sees no rows, compares an image's GT file alone
        when it has one, else its prediction). A row of a CSV without the ``Source`` column
        (written before O1) is outdated beside a GT file: Measure would now read that file.
        It never changes a value; it never lists a folder (GT files: :meth:`_gt_file`).
        """
        stem = _key(stem)
        return {
            "length": self._table_outdated("length", stem, SKELETON_SUFFIX),
            "area": self._table_outdated("area", stem, ".png"),
        }

    def is_outdated(self, stem: str) -> bool:
        """True when either value of ``stem`` is outdated (see :meth:`outdated`)."""
        return any(self.outdated(stem).values())

    def _table_outdated(self, metric: str, stem: str, suffix: str) -> bool:
        table = self._table(metric)
        csv_path = table.path
        row = table.get(stem, load=False) if csv_path is not None else None
        if row is None:
            return False
        gt = self._gt_file(metric, stem)
        if gt is not None and row.source is None:
            return True  # a CSV from before O1: Measure would now read this GT file
        if gt is None and row.source == SOURCE_GT:
            return True  # the file it was measured from is gone
        prediction = _prediction_of(csv_path, row, suffix)
        if row.source != SOURCE_GT and _another_named_like(stem, prediction, suffix):
            return True  # a new Measure would read that one (split_inputs keeps its name)
        record = table.record
        if record is not None:
            files = [gt] if row.source == SOURCE_GT else [prediction]
            if gt is not None and row.source != SOURCE_GT:
                files.append(gt)  # not used then: one saved or changed since outdates
            return any(_differs_from_record(record, path, self._input_key) for path in files)
        csv_sig = _signature(csv_path)
        if csv_sig is None:
            return False
        if row.source == SOURCE_PREDICTION:
            # measured from its prediction (its GT file, if any, was not used): both count
            files = [prediction, *([gt] if gt is not None else [])]
        else:
            files = [gt if gt is not None else prediction]
        signatures = (_signature(path) for path in files)
        return any(sig is not None and sig[0] > csv_sig[0] for sig in signatures)


def _prediction_of(csv_path: str, row: _Row, suffix: str) -> str:
    """The prediction ``row`` was measured from: ``<csv dir>/<row's own stem><suffix>``."""
    return os.path.join(os.path.dirname(csv_path), row.stem + suffix)


def _another_named_like(stem: str, prediction: str, suffix: str) -> bool:
    """True when the row's ``prediction`` is named in another case than image ``stem`` and a
    second file named exactly like the image is beside it (a case-sensitive disk)."""
    exact = os.path.join(os.path.dirname(prediction), stem + suffix)
    if exact == prediction or not os.path.isfile(exact):
        return False
    try:
        return not os.path.samefile(exact, prediction)
    except OSError:  # the row's file vanished since the stat: the other one is what is left
        return True


def _differs_from_record(record: InputsRecord, source: str, key_of: Callable[[str], str]) -> bool:
    """True when ``source`` is gone, has no entry in ``record`` (under ``key_of(source)``,
    its :func:`~app.inference.measure_inputs.input_key`), or its content differs from the
    recorded one (hashed only when its size or time differ)."""
    try:
        st = os.stat(source)
    except OSError:
        return True
    entry = record.inputs.get(key_of(source))
    if entry is None:
        return True
    if (st.st_size, st.st_mtime_ns) == entry[:2]:
        return False
    return content_sha256(source, st.st_size, st.st_mtime_ns) != entry[2]
