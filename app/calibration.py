"""Per-project field of view (owner decision O3; M2 step 2).

A project can state its own camera field of view in ``sprouts_project.json`` next to its
images::

    {"schema": 1,
     "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5,
                     "reference_size_px": [640, 480], "source": "how it was measured"}}

Measure uses it and records it in each CSV's provenance (:func:`calibration_record`); a
missing file means the app's default (:data:`app.config.CALIBRATION`), and an invalid one
the default too, with the reason in :attr:`ProjectCalibration.problem`. A CSV measured with
another field of view, or (length) another length method, is flagged outdated, not hidden
(:func:`method_note`): unknown (no sidecar, or no ``calibration`` in it) is not "different".

Qt-free and light: the standard library and :mod:`app.config` only.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from app import config
from app.config import Calibration

__all__ = [
    "LENGTH_METHOD",
    "PROJECT_FILENAME",
    "PROJECT_SCHEMA",
    "CalibrationWords",
    "ProjectCalibration",
    "aspect_mismatches",
    "calibration_record",
    "calibration_words",
    "default_calibration",
    "fov_label",
    "fov_of",
    "load_for_output",
    "load_project_calibration",
    "method_note",
    "project_file_path",
    "same_fov",
]

PROJECT_FILENAME = "sprouts_project.json"
PROJECT_SCHEMA = 1
#: How Measure turns a skeleton into a length (recorded as ``parameters.estimator``).
LENGTH_METHOD = "8-connected arc length with per-axis pixel pitch"

_SOURCE_PROJECT = "project"
_SOURCE_DEFAULT = "default"
#: How a CSV recorded with another field of view is described (:func:`method_note`).
#: ``uses`` is :attr:`CalibrationWords.uses`: "this project uses" / "this app now uses".
_FOV_NOTE = (
    "Measured with field of view {recorded}; {uses} {current}. "
    "Measure again before comparing with new values."
)
_METHOD_NOTE = (
    "Measured with an earlier length method. Measure again before comparing with new values."
)
_CACHE_LIMIT = 64
#: The largest ``reference_size_px`` value accepted (keeps its ratio a finite float).
_MAX_SIZE_PX = 1_000_000_000
#: How many folders above an output file :func:`load_for_output` looks for the project file
#: (``<root>/output/skeletonizer/test_latest/images/root_lengths.csv`` is 5).
_OUTPUT_LEVELS = 5
_FOV_KEYS = ("fov_width_mm", "fov_height_mm")


@dataclass(frozen=True)
class ProjectCalibration:
    """The field of view a project measures with, and where it came from.

    ``source``: ``"project"`` (its ``sprouts_project.json``) or ``"default"`` (the app's).
    ``reference_size_px``: the ``(width, height)`` the file says the field of view was
    measured at, if it says. ``evidence``: the file's ``calibration.source`` text.
    ``problem``: why an existing project file was not used (``""``: none).
    """

    calibration: Calibration
    source: str
    reference_size_px: tuple[int, int] | None = None
    evidence: str = ""
    problem: str = ""


def default_calibration() -> ProjectCalibration:
    """The app's field of view (:data:`app.config.CALIBRATION`, read at call time)."""
    return ProjectCalibration(config.CALIBRATION, _SOURCE_DEFAULT)


def project_file_path(project_root: str | os.PathLike[str]) -> str:
    """Where the project file of ``project_root`` lives."""
    return os.path.join(os.fspath(project_root), PROJECT_FILENAME)


class _Invalid(ValueError):
    """The project file cannot be used; the message says why (shown to the user)."""


def _positive_float(value: Any) -> float | None:
    """``value`` as a float when it is a finite number above 0 (a bool, text, ``NaN``,
    ``inf`` and an integer too large for a float are not); else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def fov_of(calibration: Any) -> dict[str, float] | None:
    """``{"fov_width_mm", "fov_height_mm"}`` of a provenance ``calibration`` mapping when both
    are finite numbers above 0 (as floats); else ``None``. The one validator of a recorded
    field of view (:func:`app.provenance.csv_calibration` and :func:`method_note` use it)."""
    if not isinstance(calibration, Mapping):
        return None
    found: dict[str, float] = {}
    for key in _FOV_KEYS:
        number = _positive_float(calibration.get(key))
        if number is None:
            return None
        found[key] = number
    return found


def same_fov(a: Mapping[str, float], b: Mapping[str, float]) -> bool:
    """True when two fields of view (``fov_width_mm``, ``fov_height_mm``) are equal as numbers
    (``math.isclose``, ``rel_tol=1e-9``): the one rule that decides a CSV's recorded field of
    view is another one (:func:`app.provenance.same_calibration` is this)."""
    return all(math.isclose(a[k], b[k], rel_tol=1e-9) for k in _FOV_KEYS)


def _positive_number(value: Any, key: str) -> float:
    number = _positive_float(value)
    if number is None:
        raise _Invalid(f'"calibration.{key}" must be a number above 0')
    return number


def _whole_number(value: Any) -> int | None:
    """``value`` as an int when it is a whole number from 1 to :data:`_MAX_SIZE_PX`."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and 0 < value <= _MAX_SIZE_PX:
        return value
    return None


def _parse(text: str) -> ProjectCalibration:
    """The project calibration the file's ``text`` states; :class:`_Invalid` when it cannot
    be used. Unknown keys are ignored."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise _Invalid("it is not valid JSON") from exc
    schema = data.get("schema") if isinstance(data, dict) else None
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != PROJECT_SCHEMA:
        raise _Invalid('"schema" must be 1')
    cal = data.get("calibration")
    if not isinstance(cal, dict):
        raise _Invalid('"calibration" is missing')
    width = _positive_number(cal.get("fov_width_mm"), "fov_width_mm")
    height = _positive_number(cal.get("fov_height_mm"), "fov_height_mm")
    reference = None
    if cal.get("reference_size_px") is not None:
        size = cal["reference_size_px"]
        pair = [_whole_number(v) for v in size] if isinstance(size, list) else []
        if len(pair) != 2 or any(v is None for v in pair):
            raise _Invalid('"calibration.reference_size_px" must be two whole numbers above 0')
        reference = (pair[0], pair[1])
    evidence = cal.get("source")
    if evidence is not None and not isinstance(evidence, str):
        raise _Invalid('"calibration.source" must be text')
    return ProjectCalibration(
        Calibration(width, height), _SOURCE_PROJECT, reference, evidence or ""
    )


#: ``(path, mtime_ns, size) -> the parsed file, or the problem text``.
_cache: dict[tuple[str, int, int], ProjectCalibration | str] = {}
_cache_lock = threading.Lock()


def load_project_calibration(
    project_root: str | os.PathLike[str] | None,
) -> ProjectCalibration:
    """The field of view of the project in ``project_root``: its ``sprouts_project.json``,
    else the app's default (``problem`` empty). An invalid or unreadable file gives the
    default with ``problem`` saying why. The file is read as ``utf-8-sig`` and parsed once
    per ``(path, mtime_ns, size)``."""
    if project_root is None:
        return default_calibration()
    path = os.path.abspath(project_file_path(project_root))
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return default_calibration()
    except OSError:
        return replace(default_calibration(), problem="it could not be read")
    key = (path, st.st_mtime_ns, st.st_size)
    found = _cache.get(key)
    if found is None:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            found = "it is not valid JSON"
        except OSError:
            return replace(default_calibration(), problem="it could not be read")
        else:
            try:
                found = _parse(text)
            except _Invalid as exc:
                found = str(exc)
        with _cache_lock:
            if len(_cache) >= _CACHE_LIMIT:
                _cache.clear()
            _cache[key] = found
    if isinstance(found, str):
        return replace(default_calibration(), problem=found)
    return found


def load_for_output(
    output_path: str, project_root: str | os.PathLike[str] | None = None
) -> ProjectCalibration:
    """The project calibration of an output file: its ``project_root``'s when the caller
    knows it; else the project file of the nearest folder above it, at most five levels up
    (``<root>/skeletons/root_lengths.csv`` is two, ``<root>/output/mask/…`` three, the legacy
    ``<root>/[output/]skeletonizer/test_latest/images/…`` four or five); else the default."""
    if project_root is not None:
        return load_project_calibration(project_root)
    folder = os.path.dirname(os.path.abspath(output_path))
    for _level in range(_OUTPUT_LEVELS):
        parent = os.path.dirname(folder)
        if parent == folder:  # the drive's root
            break
        folder = parent
        if os.path.isfile(project_file_path(folder)):
            return load_project_calibration(folder)
    return default_calibration()


def calibration_record(pc: ProjectCalibration) -> dict[str, Any]:
    """The provenance ``calibration`` of a measurement made with ``pc``:
    ``fov_width_mm``, ``fov_height_mm``, ``source``; from a project file also ``file``,
    ``evidence`` and (when the file states it) ``reference_size_px``."""
    record: dict[str, Any] = {
        "fov_width_mm": pc.calibration.fov_width_mm,
        "fov_height_mm": pc.calibration.fov_height_mm,
        "source": pc.source,
    }
    if pc.source == _SOURCE_PROJECT:
        record["file"] = PROJECT_FILENAME
        record["evidence"] = pc.evidence
        if pc.reference_size_px is not None:
            record["reference_size_px"] = list(pc.reference_size_px)
    return record


def _fov_pair(calibration: Calibration | Mapping[str, Any]) -> tuple[float, float]:
    """``(width, height)`` of a :class:`Calibration` or of a mapping with its two keys (a
    sidecar's ``calibration``)."""
    if isinstance(calibration, Mapping):
        return calibration["fov_width_mm"], calibration["fov_height_mm"]
    return calibration.fov_width_mm, calibration.fov_height_mm


class CalibrationWords(NamedTuple):
    """Whose field of view the current one is, in the words the export, the metrics bar and
    About share (lower case; a sentence capitalises the first letter)."""

    #: ``"this project's"`` / ``"this app's"`` (followed by "field of view" or a colon).
    owner: str
    #: ``"this project uses"`` / ``"this app now uses"`` (followed by the field of view).
    uses: str


_PROJECT_WORDS = CalibrationWords("this project's", "this project uses")
_APP_WORDS = CalibrationWords("this app's", "this app now uses")


def calibration_words(current: ProjectCalibration | Mapping[str, Any] | None) -> CalibrationWords:
    """The words for ``current`` (a :class:`ProjectCalibration`, or its
    :func:`calibration_record`): "project" when it comes from the project file, else "app"
    (``None``, the default, or an invalid file)."""
    if isinstance(current, ProjectCalibration):
        source = current.source
    elif isinstance(current, Mapping):
        source = current.get("source")
    else:
        source = None
    return _PROJECT_WORDS if source == _SOURCE_PROJECT else _APP_WORDS


def fov_label(calibration: Calibration | Mapping[str, float]) -> str:
    """Display text of a field of view, e.g. ``18×13 mm`` or ``18×13.5 mm``."""
    return _label(*_fov_pair(calibration))


def _label(width: float, height: float) -> str:
    return f"{width:g}×{height:g} mm"


def method_note(
    sidecar: Mapping[str, Any] | None, *, kind: str, current: ProjectCalibration
) -> str:
    """Why a CSV's values cannot be compared with new ones, from its provenance ``sidecar``;
    ``""`` when there is nothing to say, or the sidecar or its ``calibration`` is missing.

    The field-of-view sentence when its width or height differs from ``current``'s
    (:func:`same_fov`; "this project uses" for a project file's calibration, else "this app
    now uses": :func:`calibration_words`); for ``kind == "length"`` also the method sentence
    when its ``parameters.estimator`` is another than :data:`LENGTH_METHOD`. Both: one space
    apart."""
    if not isinstance(sidecar, Mapping):
        return ""
    recorded = fov_of(sidecar.get("calibration"))
    if recorded is None:
        return ""
    notes = []
    now = dict(zip(_FOV_KEYS, _fov_pair(current.calibration), strict=True))
    if not same_fov(recorded, now):
        notes.append(
            _FOV_NOTE.format(
                recorded=fov_label(recorded),
                uses=calibration_words(current).uses,
                current=fov_label(current.calibration),
            )
        )
    if kind == "length":
        parameters = sidecar.get("parameters")
        estimator = parameters.get("estimator") if isinstance(parameters, Mapping) else None
        if estimator is not None and estimator != LENGTH_METHOD:
            notes.append(_METHOD_NOTE)
    return " ".join(notes)


def aspect_mismatches(
    sizes_px: Mapping[str, int], reference: tuple[int, int] | None, *, tolerance: float = 0.01
) -> int:
    """How many measured files (``{"WxH": count}``) have a width / height ratio more than
    ``tolerance`` (1 %) away from ``reference``'s; 0 without a reference."""
    if reference is None:
        return 0
    wanted = reference[0] / reference[1]
    mismatches = 0
    for size, count in sizes_px.items():
        try:
            width, height = (int(part) for part in str(size).split("x"))
        except ValueError:
            continue
        if width <= 0 or height <= 0:
            continue
        if abs((width / height) / wanted - 1) > tolerance:
            mismatches += count
    return mismatches
