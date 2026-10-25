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
is hashed at most once. Never imports Qt or torch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.io_utils import atomic_write_text

logger = logging.getLogger(__name__)

__all__ = [
    "INPUTS_SUFFIX",
    "Fingerprint",
    "InputsDiff",
    "InputsRecord",
    "Stamp",
    "content_sha256",
    "diff_inputs",
    "input_key",
    "inputs_record_path",
    "is_unchanged",
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


# ------------------------------------------------------------------ hashing
def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def load_inputs_record(csv_path: str) -> InputsRecord | None:
    """The record of ``csv_path``'s inputs, or ``None`` when it is missing, unreadable,
    malformed, or written for another CSV (its ``csv_sha256`` is not the CSV on disk)."""
    try:
        with open(inputs_record_path(csv_path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    csv_hash, raw = data.get("csv_sha256"), data.get("inputs")
    if not isinstance(csv_hash, str) or not isinstance(raw, dict):
        return None
    inputs: dict[str, Fingerprint] = {}
    for key, value in raw.items():
        fingerprint = _fingerprint(value)
        if fingerprint is None:
            return None
        inputs[key] = fingerprint
    if _csv_sha256(csv_path) != csv_hash:
        return None
    return InputsRecord(inputs=inputs)


# ------------------------------------------------------------------ comparing
def scan_stamps(
    folder: str,
    suffix: str,
    project_root: str,
    *,
    keep: Callable[[str], bool] | None = None,
) -> dict[str, Stamp]:
    """``input key -> (absolute path, size, mtime_ns)`` of the ``*suffix`` files of ``folder``.

    One ``os.scandir`` (the entry's stat, free on Windows). Hidden files (the
    ``.<name>.tmp`` of a write in progress) are not inputs; ``suffix`` matches regardless
    of case; ``keep(stem)`` (the name without ``suffix``) filters. Never raises: an
    unreadable folder has no files.
    """
    stamps: dict[str, Stamp] = {}
    cut = len(suffix)
    folder = os.path.abspath(folder)
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                name = entry.name
                if name.startswith(".") or not name.lower().endswith(suffix):
                    continue
                if keep is not None and not keep(name[: len(name) - cut]):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    st = entry.stat()
                except OSError:
                    continue
                path = os.path.join(folder, name)
                stamps[input_key(path, project_root)] = (path, st.st_size, st.st_mtime_ns)
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
