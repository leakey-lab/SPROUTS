"""The record of the files a Measure run measured (R5), Qt-free.

A Measure run writes ``<csv>.inputs.json`` beside its CSV::

    {"csv_sha256": "<sha256 of the CSV bytes>",
     "inputs": {"mask/<stem>.png": [size, mtime_ns, "<sha256>"], ...}}

Keys are paths relative to the project folder with ``/`` separators, so the record says
which file each value was measured from. "Changed since measured" compares the inputs on
disk with that record: an input whose size and time equal the recorded ones is current;
otherwise its content is hashed and compared, so a copied or restamped folder is current
and a repainted mask with an older time is changed. A record whose ``csv_sha256`` is not
the CSV on disk describes another CSV and is ignored (the caller falls back to file times).

Hashes are kept for the session by ``(absolute path, size, mtime_ns)``: an unchanged file
is hashed at most once. A parsed record is shared while its CSV and record files keep their
size and time (:func:`load_inputs_record`). Never imports Qt or torch.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.io_utils import atomic_write_text, sha256_file

logger = logging.getLogger(__name__)

__all__ = [
    "INPUTS_SUFFIX",
    "Fingerprint",
    "InputsDiff",
    "InputsRecord",
    "Stamp",
    "content_sha256",
    "diff_inputs",
    "file_key",
    "input_key",
    "inputs_record_path",
    "is_unchanged",
    "key_prefix",
    "keys_and_folders",
    "load_inputs_record",
    "remember_sha256",
    "scan_stamps",
    "write_inputs_record",
]

INPUTS_SUFFIX = ".inputs.json"

#: ``(size, mtime_ns, sha256 hex)`` of one measured file.
Fingerprint = tuple[int, int, str]
#: ``(absolute path, size, mtime_ns)`` of one file on disk now.
Stamp = tuple[str, int, int]

_CACHE_LIMIT = 1 << 18  # entries; ~46k inputs per metric in a large project
_sha256_cache: dict[tuple[str, int, int], str] = {}
_cache_lock = threading.Lock()

_RECORDS_LIMIT = 64  # CSV paths
#: normcase(abspath(csv)) -> (((CSV size, mtime_ns), (record size, mtime_ns)), a weak
#: reference to the parsed record, or None when the record did not load).
_records: dict[str, tuple[tuple[tuple[int, int], tuple[int, int]], weakref.ref | None]] = {}
_records_lock = threading.Lock()

#: Windows drops trailing dots and spaces from the last name of a path (GetFullPathNameW),
#: so a folder or file name ending in one is keyed by :func:`input_key` itself.
_TRAILING = (" ", ".")


def inputs_record_path(csv_path: str) -> str:
    """Where the record of ``csv_path``'s inputs lives: ``<csv_path>.inputs.json``."""
    return os.fspath(csv_path) + INPUTS_SUFFIX


def input_key(path: str, project_root: str) -> str:
    """``path`` relative to ``project_root`` with ``/`` separators (e.g. ``mask/a.png``).

    A path on another drive keeps its absolute form (still one key per file)."""
    abs_path = os.path.abspath(path)
    try:
        rel = os.path.relpath(abs_path, os.path.abspath(project_root))
    except ValueError:  # another drive (Windows)
        rel = abs_path
    return rel.replace(os.sep, "/")


def key_prefix(folder: str, project_root: str) -> str | None:
    """What :func:`input_key` puts before the name of a file directly in ``folder``: ``""``
    in the project folder, else the folder's key and ``/`` (``"mask/"``). One relpath per
    folder instead of one per file (~10-30 us each on Windows).

    ``None`` for a folder above the project folder: a file there can be named like the
    next folder of the project's path, which changes its key, so each is keyed alone."""
    key = input_key(folder, project_root)
    if key == ".":
        return ""
    if set(key.split("/")) == {os.pardir}:
        return None
    # a drive or share root on another drive keeps its absolute key, which ends in "/"
    return key if key.endswith("/") else key + "/"


def _plain(head: str, name: str) -> bool:
    """True when the path ``head``/``name`` (``os.path.split``) is ``name`` in the absolute
    folder of ``head``: not when Windows path rules rewrite it (a name or a folder ending in
    a dot or a space, a name ``.`` or ``..``)."""
    return name not in ("", ".", "..") and name[-1] not in _TRAILING and head[-1:] not in _TRAILING


def file_key(path: str, project_root: str, prefixes: dict[str, str | None]) -> str:
    """``input_key(path, project_root)`` of the file ``path``, from the :func:`key_prefix`
    of its folder, looked up in (or added to) ``prefixes`` (folder -> prefix).

    A caller keeps ``prefixes`` for one batch of paths (a relative path's key follows the
    working folder). Any thread: a race computes one prefix twice."""
    head, name = os.path.split(path)
    if not _plain(head, name):
        return input_key(path, project_root)
    try:
        prefix = prefixes[head]
    except KeyError:
        prefix = prefixes[head] = key_prefix(head, project_root)
    return input_key(path, project_root) if prefix is None else prefix + name


def keys_and_folders(paths: Iterable[str], project_root: str) -> tuple[dict[str, str], set[str]]:
    """``{path: input_key(path, project_root)}`` and the folders of the file ``paths``
    (``os.path.dirname(os.path.abspath(path))`` of each), one relpath and one abspath per
    folder rather than per file."""
    keys: dict[str, str] = {}
    folders: set[str] = set()
    known: dict[str, tuple[str | None, str]] = {}  # folder -> (key prefix, absolute folder)
    for path in paths:
        head, name = os.path.split(path)
        if _plain(head, name):
            held = known.get(head)
            if held is None:
                held = known[head] = (key_prefix(head, project_root), os.path.abspath(head))
                folders.add(held[1])
            if held[0] is not None:
                keys[path] = held[0] + name
                continue
        keys[path] = input_key(path, project_root)
        folders.add(os.path.dirname(os.path.abspath(path)))
    return keys, folders


# ------------------------------------------------------------------ hashing
_hash_file = sha256_file  # looked up at call time (tests count the files hashed)


def remember_sha256(path: str, size: int, mtime_ns: int, sha256: str) -> None:
    """Keep ``sha256`` as the content hash of ``path`` while its size and time stay."""
    key = (os.path.abspath(path), int(size), int(mtime_ns))
    with _cache_lock:
        if len(_sha256_cache) >= _CACHE_LIMIT:
            _sha256_cache.clear()
        _sha256_cache[key] = sha256


def content_sha256(path: str, size: int, mtime_ns: int) -> str | None:
    """SHA-256 of ``path`` as it was at ``(size, mtime_ns)``: from the session cache, else
    hashed now and kept; ``None`` when the file cannot be read."""
    key = (os.path.abspath(path), int(size), int(mtime_ns))
    with _cache_lock:
        cached = _sha256_cache.get(key)
    if cached is not None:
        return cached
    try:
        digest = _hash_file(key[0])
    except OSError:
        return None
    remember_sha256(key[0], key[1], key[2], digest)
    return digest


def _csv_sha256(csv_path: str) -> str | None:
    try:
        st = os.stat(csv_path)
    except OSError:
        return None
    return content_sha256(csv_path, st.st_size, st.st_mtime_ns)


# ------------------------------------------------------------------ the record
@dataclass(frozen=True)
class InputsRecord:
    """The inputs a CSV was measured from: key -> ``(size, mtime_ns, sha256)``."""

    inputs: Mapping[str, Fingerprint]


def write_inputs_record(csv_path: str, inputs: Mapping[str, Fingerprint]) -> str:
    """Atomically write the record of ``inputs`` beside ``csv_path`` (written first);
    return the record's path."""
    st = os.stat(csv_path)
    csv_hash = _hash_file(csv_path)
    remember_sha256(csv_path, st.st_size, st.st_mtime_ns, csv_hash)
    record = {
        "csv_sha256": csv_hash,
        "inputs": {
            key: [int(size), int(mtime_ns), str(sha256)]
            for key, (size, mtime_ns, sha256) in inputs.items()
        },
    }
    path = inputs_record_path(csv_path)
    atomic_write_text(path, json.dumps(record, sort_keys=True) + "\n")
    with _records_lock:  # a rewrite within one tick of a coarse file clock keeps the stamps
        _records.pop(_record_cache_key(csv_path), None)
    return path


def _fingerprint(value: object) -> Fingerprint | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    size, mtime_ns, sha256 = value
    if (
        type(size) is not int
        or type(mtime_ns) is not int
        or not isinstance(sha256, str)
        or len(sha256) != 64
    ):
        return None
    return size, mtime_ns, sha256


def _record_cache_key(csv_path: str) -> str:
    return os.path.normcase(os.path.abspath(csv_path))


def _size_and_time(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def load_inputs_record(csv_path: str) -> InputsRecord | None:
    """The record of ``csv_path``'s inputs, or ``None`` when it is missing, unreadable,
    malformed, or written for another CSV (its ``csv_sha256`` is not the CSV on disk).

    One parse is shared while the CSV and the record keep their size and time, and while a
    caller still holds it (a weak reference: the metrics bar's index keeps the record of
    each loaded CSV), so the Measure status count and :meth:`MeasureRun.unchanged` reuse
    it (a 46k-entry record is ~7.6 MB of JSON; parsing it holds the GIL ~50-90 ms). The
    record is read-only. Nothing is kept without both files, nor when one of them could not
    be read (a sharing violation, a file not downloaded yet): the next call reads again."""
    try:
        stamps = (_size_and_time(csv_path), _size_and_time(inputs_record_path(csv_path)))
    except OSError:
        return _read_inputs_record(csv_path)[0]
    key = _record_cache_key(csv_path)
    with _records_lock:
        held = _records.get(key)
    if held is not None and held[0] == stamps:
        if held[1] is None:
            return None
        record = held[1]()
        if record is not None:
            return record
    record, keep = _read_inputs_record(csv_path)
    if keep:
        with _records_lock:
            if len(_records) >= _RECORDS_LIMIT and key not in _records:
                _records.clear()
            _records[key] = (stamps, None if record is None else weakref.ref(record))
    return record


def _read_inputs_record(csv_path: str) -> tuple[InputsRecord | None, bool]:
    """:func:`load_inputs_record` without the shared parse, and whether that answer holds
    while both files keep their size and time (False: the record or the CSV could not be
    read, which may work next time)."""
    try:
        with open(inputs_record_path(csv_path), encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError:
        return None, False
    except (ValueError, RecursionError):
        return None, True
    if not isinstance(data, dict):
        return None, True
    csv_hash, raw = data.get("csv_sha256"), data.get("inputs")
    if not isinstance(csv_hash, str) or not isinstance(raw, dict):
        return None, True
    inputs: dict[str, Fingerprint] = {}
    for key, value in raw.items():
        fingerprint = _fingerprint(value)
        if fingerprint is None:
            return None, True
        inputs[key] = fingerprint
    on_disk = _csv_sha256(csv_path)
    if on_disk is None:
        return None, False
    if on_disk != csv_hash:
        return None, True
    return InputsRecord(inputs=MappingProxyType(inputs)), True


# ------------------------------------------------------------------ comparing
def scan_stamps(
    folder: str,
    suffix: str,
    project_root: str,
    *,
    stem_of: Callable[[str], str | None] | None = None,
) -> dict[str, Stamp]:
    """``input key -> (absolute path, size, mtime_ns)`` of the ``*suffix`` files of ``folder``.

    One ``os.scandir`` (the entry's stat, free on Windows). Hidden files (the
    ``.<name>.tmp`` of a write in progress) are not inputs and ``suffix`` matches
    regardless of case, unless ``stem_of`` (a name's stem, ``None``: not an input, e.g.
    :func:`~app.inference.input_split.png_stem`) decides instead, as Measure's own
    listing does. Never raises: an unreadable folder has no files.
    """
    stamps: dict[str, Stamp] = {}
    folder = os.path.abspath(folder)
    try:
        with os.scandir(folder) as entries:
            prefix = key_prefix(folder, project_root)  # the keys' folder part, once
            for entry in entries:
                name = entry.name
                if stem_of is not None:
                    if stem_of(name) is None:
                        continue
                elif name.startswith(".") or not name.lower().endswith(suffix):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    st = entry.stat()
                except OSError:
                    continue
                path = entry.path  # os.path.join(folder, name)
                if prefix is None or name[-1] in _TRAILING:
                    key = input_key(path, project_root)
                else:
                    key = prefix + name
                stamps[key] = (path, st.st_size, st.st_mtime_ns)
    except OSError:
        pass
    return stamps


@dataclass(frozen=True)
class InputsDiff:
    """The inputs on disk against a record: ``inputs`` on disk, ``changed`` of them since
    measured, ``new`` (not measured) and ``removed`` (measured, now gone)."""

    inputs: int
    changed: int
    new: int
    removed: int


def _is_current(fingerprint: Fingerprint, stamp: Stamp) -> bool:
    """Size and time equal, else the content hash equals the recorded one."""
    path, size, mtime_ns = stamp
    if (size, mtime_ns) == fingerprint[:2]:
        return True
    return content_sha256(path, size, mtime_ns) == fingerprint[2]


def diff_inputs(
    record: InputsRecord, current: Mapping[str, Stamp], *, prefix: str | None = None
) -> InputsDiff:
    """Compare ``current`` (from :func:`scan_stamps`) with ``record``. ``prefix`` (e.g.
    ``"mask/"``) restricts "removed" to the recorded keys under it."""
    recorded = record.inputs
    changed = new = 0
    for key, stamp in current.items():
        fingerprint = recorded.get(key)
        if fingerprint is None:
            new += 1
        elif not _is_current(fingerprint, stamp):
            changed += 1
    removed = sum(
        1 for key in recorded if key not in current and (prefix is None or key.startswith(prefix))
    )
    return InputsDiff(inputs=len(current), changed=changed, new=new, removed=removed)


def is_unchanged(record: InputsRecord | None, current: Mapping[str, Stamp]) -> bool:
    """True only when ``record`` exists, names exactly the files of ``current`` and none of
    them changed (stops at the first difference)."""
    if record is None:
        return False
    recorded = record.inputs
    if len(recorded) != len(current):
        return False
    for key, stamp in current.items():
        fingerprint = recorded.get(key)
        if fingerprint is None or not _is_current(fingerprint, stamp):
            return False
    return True
