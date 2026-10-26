"""
Provenance records for the files SPROUTS writes (FAIR4RS R1.2).

A record says which software, code revision, model weights, calibration and
numeric runtime produced an output, so results stay comparable across releases
(the metric CSVs themselves carry a frozen schema and cannot). Writers attach
it as a JSON sidecar next to one output (``<output>.provenance.json``, e.g. for
``root_lengths.csv``) or, for a batch of images, as a run manifest inside the
output folder (``_sprouts_run.json``). Loaders never see either: they filter
by image/CSV extensions.

Cheap and side-effect free: never imports torch or Qt, never runs a
subprocess, never initialises CUDA. Library versions and precision flags are
read only from modules that are already imported. Hashing model weights
(~0.3 s for both checkpoints, cold) is memoised per (path, size, mtime), but
the first call should still happen off the GUI thread.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import functools
import hashlib
import json
import logging
import math
import os
import platform
import re
import stat
import sys
import threading
from collections.abc import Iterable, Mapping
from typing import Any

from app import APP_FULL_NAME, APP_NAME, __version__
from app.config import CALIBRATION, PROJECT_ROOT
from app.io_utils import atomic_write_text

logger = logging.getLogger(__name__)

__all__ = [
    "BUILD_INFO_FILENAME",
    "RUN_HISTORY_FILENAME",
    "RUN_MANIFEST_FILENAME",
    "SCHEMA",
    "SIDECAR_SUFFIX",
    "app_info",
    "append_run_history",
    "build_record",
    "calibration_info",
    "csv_calibration",
    "file_sha256",
    "git_commit",
    "minimal_record",
    "names_digest",
    "portable_path",
    "read_sidecar",
    "run_history_path",
    "run_manifest_path",
    "same_calibration",
    "runtime_info",
    "sidecar_path",
    "write_run_manifest",
    "write_sidecar",
]

SCHEMA = "sprouts-provenance/1"
SIDECAR_SUFFIX = ".provenance.json"
#: One record per batch run, inside the output folder (scanners ignore .json).
RUN_MANIFEST_FILENAME = "_sprouts_run.json"
#: One JSON line per run that wrote or failed an input, appended (the manifest holds the latest).
RUN_HISTORY_FILENAME = "_sprouts_runs.jsonl"
#: Written next to the code by frozen builds (no .git): ``{"git_commit": "<sha>"}``.
BUILD_INFO_FILENAME = "build_info.json"

# Import names whose versions are recorded when already loaded.
_PACKAGES = (
    "numpy",
    "cv2",
    "PIL",
    "skimage",
    "scipy",
    "torch",
    "torchvision",
    "pandas",
    "plotly",
    "dash",
)
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_SHA256_CACHE_LIMIT = 256

_sha256_cache: dict[tuple[str, int, int], str] = {}
_git_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


# ------------------------------------------------------------------ identity
def app_info() -> dict[str, str]:
    """Name and version of this software."""
    return {"name": APP_NAME, "full_name": APP_FULL_NAME, "version": __version__}


def calibration_info() -> dict[str, float]:
    """The physical calibration every metric depends on (read-only)."""
    return dataclasses.asdict(CALIBRATION)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _git_dir(root: str) -> str | None:
    dot_git = os.path.join(root, ".git")
    if os.path.isdir(dot_git):
        return dot_git
    if os.path.isfile(dot_git):  # worktree / submodule: "gitdir: <path>"
        text = _read_text(dot_git).strip()
        if text.startswith("gitdir:"):
            return os.path.normpath(os.path.join(root, text[len("gitdir:") :].strip()))
    return None


def _head_commit(git_dir: str) -> str | None:
    head = _read_text(os.path.join(git_dir, "HEAD")).strip()
    if not head.startswith("ref:"):
        return head if _SHA.fullmatch(head) else None  # detached HEAD
    ref = head[len("ref:") :].strip()
    common = git_dir
    if os.path.isfile(os.path.join(git_dir, "commondir")):
        common = os.path.normpath(
            os.path.join(git_dir, _read_text(os.path.join(git_dir, "commondir")).strip())
        )
    for base in dict.fromkeys((git_dir, common)):
        loose = os.path.join(base, *ref.split("/"))
        if os.path.isfile(loose):
            sha = _read_text(loose).strip()
            return sha if _SHA.fullmatch(sha) else None
    packed = os.path.join(common, "packed-refs")
    if os.path.isfile(packed):
        for line in _read_text(packed).splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and _SHA.fullmatch(parts[0]):
                return parts[0]
    return None  # unborn branch


def _build_info_commit(root: str) -> str | None:
    path = os.path.join(root, BUILD_INFO_FILENAME)
    if not os.path.isfile(path):
        return None
    commit = json.loads(_read_text(path)).get("git_commit")
    return commit if isinstance(commit, str) and _SHA.fullmatch(commit) else None


def git_commit(root: str = PROJECT_ROOT) -> str | None:
    """Commit the code in ``root`` was checked out at, or None if unknown.

    Reads ``.git`` directly (loose refs, packed-refs, worktrees; no
    subprocess); frozen builds fall back to ``build_info.json``. A found commit
    is cached for the process: it describes the code that was loaded.
    """
    root = os.path.abspath(os.fspath(root))
    cached = _git_cache.get(root)
    if cached is not None:
        return cached
    commit = None
    try:
        git_dir = _git_dir(root)
        commit = _head_commit(git_dir) if git_dir else None
        if commit is None:
            commit = _build_info_commit(root)
    except (OSError, ValueError, UnicodeDecodeError, AttributeError, RecursionError) as exc:
        # RecursionError: a build_info.json nested deeper than the recursion limit (R4).
        logger.debug("Could not read the git commit of %s: %s", root, exc)
    if commit is not None:
        with _cache_lock:
            _git_cache[root] = commit
    return commit


# ------------------------------------------------------------------ hashing
def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str | None:
    """SHA-256 hex digest of a file (streamed), or None if it cannot be read.

    Memoised by (absolute path, size, mtime), so re-hashing an unchanged
    checkpoint is free and an edited one is hashed again.
    """
    abspath = os.path.abspath(os.fspath(path))
    try:
        st = os.stat(abspath)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    key = (abspath, st.st_size, st.st_mtime_ns)
    cached = _sha256_cache.get(key)
    if cached is not None:
        return cached
    try:
        digest = _hash_file(abspath)
    except OSError:
        return None
    with _cache_lock:
        if len(_sha256_cache) >= _SHA256_CACHE_LIMIT:
            _sha256_cache.clear()
        _sha256_cache[key] = digest
    return digest


def names_digest(names: Iterable[str]) -> str:
    """SHA-256 of the sorted, newline-joined ``names``: identifies a batch's input set."""
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()


def portable_path(path: str | os.PathLike[str], root: str | os.PathLike[str] | None = None) -> str:
    """``path`` relative to ``root`` with ``/`` separators, else just its file name.

    Keeps user names and drive layouts out of records that get shared.
    ``root`` defaults to the application folder.
    """
    abs_path = os.path.abspath(os.fspath(path))
    abs_root = os.path.abspath(os.fspath(PROJECT_ROOT if root is None else root))
    try:
        rel = os.path.relpath(abs_path, abs_root)
    except ValueError:  # another drive (Windows)
        return os.path.basename(abs_path)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return os.path.basename(abs_path)
    return rel.replace(os.sep, "/")


# ------------------------------------------------------------------ runtime
def _plain(value: Any) -> Any:
    """JSON-friendly copy of a runtime value (dtype/device objects become text)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    return str(value)


@functools.lru_cache(maxsize=1)
def _platform_info() -> tuple[str, str, str]:
    return platform.python_version(), platform.python_implementation(), platform.platform()


def _package_versions() -> dict[str, str]:
    versions = {}
    for name in _PACKAGES:
        module = sys.modules.get(name)
        version = getattr(module, "__version__", None) if module is not None else None
        if version is not None:
            versions[name] = str(version)
    qtcore = sys.modules.get("PyQt6.QtCore")
    if qtcore is not None:
        pyqt = getattr(qtcore, "PYQT_VERSION_STR", "?")
        versions["PyQt6"] = f"{pyqt} (Qt {getattr(qtcore, 'QT_VERSION_STR', '?')})"
    return versions


def _torch_info(torch: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"version": getattr(torch, "__version__", None)}
    probes = {
        "cuda": lambda: torch.version.cuda,
        "cudnn": lambda: torch.backends.cudnn.version(),
        "cudnn_allow_tf32": lambda: bool(torch.backends.cudnn.allow_tf32),
        "matmul_allow_tf32": lambda: bool(torch.backends.cuda.matmul.allow_tf32),
        "float32_matmul_precision": lambda: torch.get_float32_matmul_precision(),
        "cudnn_benchmark": lambda: bool(torch.backends.cudnn.benchmark),
        "cudnn_deterministic": lambda: bool(torch.backends.cudnn.deterministic),
    }
    for key, probe in probes.items():
        try:
            info[key] = _plain(probe())
        except Exception:  # noqa: BLE001 - describing the runtime must never fail
            continue
    try:
        if torch.cuda.is_initialized():  # never create a CUDA context just to describe it
            info["cuda_device"] = torch.cuda.get_device_name()
    except Exception:  # noqa: BLE001
        pass
    return info


def _inference_runtime() -> dict[str, Any] | None:
    """Fields of ``app.inference.runtime``'s resolved RuntimeConfig, if any.

    Uses ``peek_runtime()`` when the module offers it; otherwise reads
    ``get_runtime()`` only after it has been resolved (lru_cache hit), so it
    never triggers device detection itself.
    """
    module = sys.modules.get("app.inference.runtime")
    if module is None:
        return None
    try:
        peek = getattr(module, "peek_runtime", None)
        if callable(peek):
            runtime = peek()
        else:
            get_runtime = getattr(module, "get_runtime", None)
            cache_info = getattr(get_runtime, "cache_info", None)
            runtime = get_runtime() if cache_info is not None and cache_info().currsize else None
        if runtime is None:
            return None
        if dataclasses.is_dataclass(runtime):
            return {f.name: _plain(getattr(runtime, f.name)) for f in dataclasses.fields(runtime)}
        return {"description": repr(runtime)}
    except Exception as exc:  # noqa: BLE001 - describing the runtime must never fail
        logger.debug("Could not describe the inference runtime: %s", exc)
        return None


def runtime_info() -> dict[str, Any]:
    """Python, OS, loaded library versions and (if loaded) torch precision flags."""
    python, implementation, system = _platform_info()
    info: dict[str, Any] = {
        "python": python,
        "implementation": implementation,
        "platform": system,
        "packages": _package_versions(),
    }
    torch = sys.modules.get("torch")
    if torch is not None:
        info["torch"] = _torch_info(torch)
    inference = _inference_runtime()
    if inference is not None:
        info["inference"] = inference
    return info


# ------------------------------------------------------------------ records
def _model_entry(path: str | os.PathLike[str]) -> dict[str, Any]:
    path = os.fspath(path)
    try:
        size: int | None = os.path.getsize(path)
    except OSError:
        size = None
    return {
        "path": portable_path(path, PROJECT_ROOT),
        "sha256": file_sha256(path) if size is not None else None,
        "size": size,
    }


def build_record(
    kind: str,
    *,
    inputs: Mapping[str, Any] | None = None,
    models: Mapping[str, str | os.PathLike[str]] | None = None,
    parameters: Mapping[str, Any] | None = None,
    outputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Provenance record for one output or batch run.

    ``kind`` names the activity (e.g. ``"mask_generation"``, ``"root_length"``);
    ``models`` maps a model name to its checkpoint path, recorded with its
    SHA-256 and size; ``inputs``/``parameters``/``outputs`` are free-form
    JSON-able sections supplied by the writer (prefer :func:`portable_path`
    for paths). ``created_utc`` is ISO-8601 UTC with a ``Z`` suffix.
    """
    return {
        "schema": SCHEMA,
        "kind": kind,
        "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app": app_info(),
        "git_commit": git_commit(PROJECT_ROOT),
        "calibration": calibration_info(),
        "runtime": runtime_info(),
        "models": {name: _model_entry(path) for name, path in (models or {}).items()},
        "inputs": dict(inputs or {}),
        "parameters": dict(parameters or {}),
        "outputs": dict(outputs or {}),
    }


def minimal_record(kind: str, *, outputs: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """The record to write when :func:`build_record` failed (R5).

    Same top-level keys as :func:`build_record`, so readers keep working, filled only
    from constants and the writer's ``outputs``: ``git_commit`` is None and
    ``runtime``, ``models``, ``inputs`` and ``parameters`` are empty (not described).
    ``provenance_error`` says why. Better than leaving an older record in place,
    which would describe a different run.
    """
    return {
        "schema": SCHEMA,
        "kind": kind,
        "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app": app_info(),
        "git_commit": None,
        "calibration": calibration_info(),
        "runtime": {},
        "models": {},
        "inputs": {},
        "parameters": {},
        "outputs": dict(outputs),
        "provenance_error": reason,
    }


def sidecar_path(output_path: str | os.PathLike[str]) -> str:
    """Where the record for ``output_path`` lives: ``<output_path>.provenance.json``."""
    return os.fspath(output_path) + SIDECAR_SUFFIX


def read_sidecar(output_path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """The record parsed from ``<output_path>.provenance.json``; ``None`` when there is
    none, it cannot be read, or it is not a JSON object."""
    try:
        with open(sidecar_path(output_path), encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError, RecursionError):  # ValueError: bad JSON or bad UTF-8
        return None
    return record if isinstance(record, dict) else None


def csv_calibration(csv_path: str) -> dict[str, float] | None:
    """The field of view ``csv_path`` was measured with (``fov_width_mm``, ``fov_height_mm``)
    from its provenance sidecar; ``None`` without a sidecar, or one whose two values are not
    both finite numbers above zero (a bool, text, ``NaN`` and ``inf`` are not)."""
    record = read_sidecar(csv_path)
    calibration = record.get("calibration") if record is not None else None
    if not isinstance(calibration, dict):
        return None
    found: dict[str, float] = {}
    for key in ("fov_width_mm", "fov_height_mm"):
        value = calibration.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        found[key] = float(value)
    return found


def same_calibration(a: Mapping[str, float], b: Mapping[str, float]) -> bool:
    """True when two fields of view (``fov_width_mm``, ``fov_height_mm``) are equal as
    numbers: the one rule that decides a CSV's recorded field of view is another one."""
    return all(math.isclose(a[k], b[k]) for k in ("fov_width_mm", "fov_height_mm"))


def _json_default(obj: Any) -> Any:
    if isinstance(obj, os.PathLike):
        return os.fspath(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    return str(obj)


def _write_json(path: str, record: Mapping[str, Any]) -> str:
    text = json.dumps(record, indent=2, ensure_ascii=False, default=_json_default)
    atomic_write_text(path, text + "\n")
    return path


def write_sidecar(output_path: str | os.PathLike[str], record: Mapping[str, Any]) -> str:
    """Atomically write ``record`` next to ``output_path``; return the sidecar path."""
    return _write_json(sidecar_path(output_path), record)


def run_manifest_path(output_dir: str | os.PathLike[str]) -> str:
    """Where the record of the last batch run into ``output_dir`` lives."""
    return os.path.join(os.fspath(output_dir), RUN_MANIFEST_FILENAME)


def write_run_manifest(output_dir: str | os.PathLike[str], record: Mapping[str, Any]) -> str:
    """Atomically write ``record`` as ``<output_dir>/_sprouts_run.json`` (replaces the last one)."""
    return _write_json(run_manifest_path(output_dir), record)


def run_history_path(output_dir: str | os.PathLike[str]) -> str:
    """Where the history of the batch runs into ``output_dir`` lives (one JSON line per run)."""
    return os.path.join(os.fspath(output_dir), RUN_HISTORY_FILENAME)


def append_run_history(output_dir: str | os.PathLike[str], entry: Mapping[str, Any]) -> str:
    """Append ``entry`` as one compact JSON line to ``<output_dir>/_sprouts_runs.jsonl``."""
    path = run_history_path(output_dir)
    line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False, default=_json_default)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
    return path
