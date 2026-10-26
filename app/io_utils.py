"""
Crash-safe, Unicode-safe file writing and image I/O (Qt-free).

Every file the app produces (metric CSVs, masks, skeletons, GT files, sidecars)
should be written through this module so that:

* a crash, disk-full or network drop mid-write never destroys the previous
  complete file: data goes to a temp file in the same folder, is flushed and
  (by default) fsync'ed, then atomically swapped in with ``os.replace``;
* a target held open by another program (Excel keeps a CSV locked) raises
  :class:`FileLockedError`, a ``PermissionError`` whose message tells the user
  what to do, instead of an opaque ``[WinError 5]``;
* image paths with non-ASCII characters work on Windows, where ``cv2.imread`` /
  ``cv2.imwrite`` silently fail (``Données``, ``José``, CJK folder names).

``durable=True`` (the default, for hand-made data and CSVs) also survives a
power cut; bulk writers of regenerable outputs (46k generated masks) may pass
``durable=False``: still never half-written, but skips the fsync, which costs
0.6-4 ms per file on Windows.

A process killed mid-write leaves its hidden temp file behind;
:func:`sweep_stale_temp_files` removes such leftovers once they are old (R8).

``cv2`` and ``numpy`` are imported lazily so importing this module stays cheap.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
import sys
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "REPLACE_RETRY_DELAYS",
    "FileLockedError",
    "atomic_open",
    "atomic_write_bytes",
    "atomic_write_text",
    "file_stamp",
    "imread_unicode",
    "imwrite_unicode",
    "path_key",
    "same_file",
    "sweep_stale_temp_files",
]

#: Windows errors meaning "another process has the file open":
#: ERROR_ACCESS_DENIED (os.replace over an open file), ERROR_SHARING_VIOLATION,
#: ERROR_LOCK_VIOLATION.
_LOCK_WINERRORS = frozenset({5, 32, 33})

#: Pauses (seconds) before retrying a replace that hit a lock. Antivirus and
#: search indexers hold fresh files for a few ms; Excel holds them until closed.
REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.25)

_WRITE_MODES = frozenset({"w", "wb", "wt"})

# A temp file is ".<target name>.<token>.tmp", the token 8 lowercase hex digits.
# _temp_path makes the name and _is_temp_name recognises it from the same parts,
# so the stale-file sweep cannot drift from the writer (R8).
_TEMP_TOKEN_BYTES = 4  # secrets.token_hex: two hex digits per byte
_TEMP_SUFFIX = ".tmp"
_HEX_DIGITS = frozenset("0123456789abcdef")
# (X1) A temp path must fit wherever its target fits: Windows without long-path support
# allows paths of up to 259 characters (MAX_PATH less the NUL), and file names are limited
# to 255 characters on NTFS but to 255 UTF-8 bytes on ext4 and APFS (O21). The temp name is
# 14 characters longer than the target's.
_MAX_PATH_CHARS = 259
_MAX_NAME_CHARS = 255


class FileLockedError(PermissionError):
    """A write failed because another program holds the target file open."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = os.fspath(path)
        self.message = (
            f"{os.path.basename(self.path)} is open in another program "
            "(for example Excel). Close it and try again."
        )
        super().__init__(13, self.message, self.path)  # errno.EACCES

    def __str__(self) -> str:
        return self.message

    def __reduce__(self):
        return (type(self), (self.path,))


def _current_platform() -> str:
    """``sys.platform``, read through one accessor that tests replace (never ``sys``)."""
    return sys.platform


def path_key(path: str | os.PathLike[str], platform: str | None = None) -> str:
    """A string that is equal for two spellings of one file on ``platform``.

    Windows folds case (``normcase``); macOS (APFS) also folds case and treats composed and
    decomposed Unicode as one name, so it compares NFC, case-folded; elsewhere paths are
    compared as written. The darwin key uses the full ``casefold()``, so ``"ß"`` and ``"ss"``
    compare equal: it errs on the "same file" side. The path is made absolute first. ``platform=None`` is this machine's
    platform, read at call time.
    """
    platform = _current_platform() if platform is None else platform
    absolute = os.path.abspath(path)
    if platform == "win32":
        return os.path.normcase(absolute)
    if platform == "darwin":
        return unicodedata.normalize("NFC", absolute).casefold()
    return absolute


def same_file(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """True when ``a`` and ``b`` are both given and name one path on this platform
    (:func:`path_key`). An empty name (no file open) is the same as nothing."""
    return bool(os.fspath(a)) and bool(os.fspath(b)) and path_key(a) == path_key(b)


def file_stamp(path: str) -> tuple:
    """``(path, mtime_ns, size)`` of a regular file, ``(path, None)`` for anything else.

    A missing path, a folder named like a file and an unreadable entry all read as
    ``(path, None)``. Two stamps of one path are equal while the file was not rewritten:
    the read-aheads, the open documents and the save guards compare them.
    """
    try:
        st = os.stat(path)
    except OSError:
        return (path, None)
    if not stat.S_ISREG(st.st_mode):
        return (path, None)
    return (path, st.st_mtime_ns, st.st_size)


def _temp_path(target: str, platform: str | None = None) -> str:
    """A unique sibling of ``target`` that no image/CSV scanner will pick up.

    ``.<target name>.<token>.tmp``, with the target name cut short (to one character at
    least) where the full one would make the temp path longer than a target path that
    fits in MAX_PATH, or the temp name longer than 255 characters (X1) - 255 UTF-8 bytes
    off Windows, where ext4 and APFS count bytes. A target path over MAX_PATH exists only
    with long paths enabled: then only the name limit applies.
    """
    platform = _current_platform() if platform is None else platform
    folder, name = os.path.split(target)
    tail = f".{secrets.token_hex(_TEMP_TOKEN_BYTES)}{_TEMP_SUFFIX}"
    keep = _MAX_NAME_CHARS - 1 - len(tail)
    full = len(os.path.abspath(target))
    if full <= _MAX_PATH_CHARS:
        keep = min(keep, _MAX_PATH_CHARS - (full - len(name)) - 1 - len(tail))
    kept = name[: max(1, keep)]
    if platform != "win32":
        # os.fsencode counts the bytes the file system sees, and accepts the surrogate escapes
        # of a name that is not valid UTF-8 (os.listdir returns those on Linux and macOS).
        while len(kept) > 1 and len(os.fsencode(f".{kept}{tail}")) > _MAX_NAME_CHARS:
            kept = kept[:-1]
    return os.path.join(folder, f".{kept}{tail}")


def _is_temp_name(name: str) -> bool:
    """True for a file name :func:`_temp_path` makes (R8)."""
    if not (name.startswith(".") and name.endswith(_TEMP_SUFFIX)):
        return False
    target, _, token = name[1 : -len(_TEMP_SUFFIX)].rpartition(".")
    return bool(target) and len(token) == 2 * _TEMP_TOKEN_BYTES and _HEX_DIGITS.issuperset(token)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _is_lock(exc: OSError, target: str) -> bool:
    """True if ``exc`` means another program holds ``target`` open (Windows)."""
    if getattr(exc, "winerror", None) not in _LOCK_WINERRORS:
        return False
    # A read-only target also fails with "access denied"; that is not a lock.
    return not os.path.exists(target) or os.access(target, os.W_OK)


def _replace(src: str, target: str) -> None:
    """``os.replace`` with a short retry on sharing violations."""
    for delay in (*REPLACE_RETRY_DELAYS, None):
        try:
            os.replace(src, target)
            return
        except PermissionError as exc:
            if not _is_lock(exc, target):
                raise
            if delay is None:
                raise FileLockedError(target) from exc
            time.sleep(delay)


@contextmanager
def atomic_open(
    path: str | os.PathLike[str], mode: str = "w", *, durable: bool = True, **open_kwargs: Any
) -> Iterator[IO]:
    """Open a temp file that replaces ``path`` only if the ``with`` block succeeds.

    ``mode`` is ``"w"``/``"wt"`` (text, UTF-8 unless ``encoding`` is given) or
    ``"wb"``; other keyword arguments go to :func:`open` (e.g. ``newline=""``
    for :mod:`csv`). On any exception the temp file is removed and the previous
    ``path`` is left untouched. The parent folder must exist. ``durable``
    fsyncs the data before the swap.

    Raises:
        FileLockedError: ``path`` is held open by another program.
        OSError: any other I/O failure.
    """
    if mode not in _WRITE_MODES:
        raise ValueError(f"atomic_open supports modes {sorted(_WRITE_MODES)}, not {mode!r}")
    if "b" not in mode:
        open_kwargs.setdefault("encoding", "utf-8")
    target = os.fspath(path)
    tmp = _temp_path(target)
    fh = open(tmp, mode.replace("w", "x"), **open_kwargs)  # noqa: SIM115 - closed below
    try:
        yield fh
        fh.flush()
        if durable:
            os.fsync(fh.fileno())
        fh.close()
        _replace(tmp, target)
    except BaseException:
        # On a full disk the close fails too (it flushes the rest): it must neither
        # replace the error being raised nor keep the temp file on disk (R7). The
        # file is closed even when its final flush fails.
        try:
            fh.close()
        except Exception:  # noqa: BLE001 - the original error is the one to raise
            pass
        _remove_quietly(tmp)
        raise


def atomic_write_bytes(
    path: str | os.PathLike[str], data: bytes | bytearray | memoryview, *, durable: bool = True
) -> None:
    """Atomically write ``data`` to ``path`` (see :func:`atomic_open`)."""
    with atomic_open(path, "wb", durable=durable) as fh:
        fh.write(data)


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
    durable: bool = True,
) -> None:
    """Atomically write ``text`` to ``path``; ``newline`` works as in :func:`open`."""
    with atomic_open(path, "w", durable=durable, encoding=encoding, newline=newline) as fh:
        fh.write(text)


def sweep_stale_temp_files(
    folder: str | os.PathLike[str],
    *,
    older_than_s: float = 3600.0,
    also: Callable[[str], bool] | None = None,
) -> list[str]:
    """Delete the temp files killed writes left in ``folder``; return their paths (R8).

    A process killed between :func:`atomic_open` creating its temp file and the swap
    leaves the hidden ``.<name>.<8 hex>.tmp`` behind for good. This deletes the
    regular files named exactly so (or accepted by ``also``, e.g. a retired writer's
    own temp names) whose mtime is more than ``older_than_s`` seconds old: a younger
    one may belong to a write in progress. One ``os.scandir``, never recursive.

    Never raises for I/O: a missing or unreadable folder gives ``[]``, and a file
    that cannot be deleted is logged and skipped.
    """
    cutoff = time.time() - older_than_s
    removed: list[str] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not (_is_temp_name(entry.name) or (also is not None and also(entry.name))):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                        continue  # young: may be a write in progress
                    os.remove(entry.path)
                except FileNotFoundError:
                    continue  # already gone
                except OSError as exc:
                    logger.warning("Could not delete stale temp file %s: %s", entry.path, exc)
                    continue
                removed.append(entry.path)
    except (FileNotFoundError, NotADirectoryError):
        logger.debug("No folder %s to sweep for stale temp files", folder)
    except OSError as exc:
        logger.warning("Could not sweep %s for stale temp files: %s", folder, exc)
    return removed


def imread_unicode(path: str | os.PathLike[str], flags: int | None = None) -> np.ndarray | None:
    """``cv2.imread`` that also works for non-ASCII paths on Windows.

    ``flags`` defaults to ``cv2.IMREAD_COLOR``. Returns ``None`` if the file is
    missing, empty, not a file, or cannot be decoded, matching ``cv2.imread``.
    Pixels are identical to ``cv2.imread`` (same decoder).
    """
    import cv2
    import numpy as np

    try:
        data = np.fromfile(os.fspath(path), dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if data.size == 0:
        return None
    try:
        return cv2.imdecode(data, cv2.IMREAD_COLOR if flags is None else flags)
    except cv2.error:
        return None


def imwrite_unicode(
    path: str | os.PathLike[str],
    img: np.ndarray,
    params: list[int] | None = None,
    *,
    durable: bool = True,
) -> None:
    """Encode ``img`` by the extension of ``path`` and write it atomically.

    Works for non-ASCII paths. ``params`` are ``cv2.imencode`` flags (e.g.
    ``[cv2.IMWRITE_PNG_COMPRESSION, 1]``); ``durable`` as in :func:`atomic_open`.

    Raises:
        ValueError: the image cannot be encoded for that extension.
        FileLockedError: ``path`` is held open by another program.
        OSError: the file cannot be written.
    """
    import cv2

    target = os.fspath(path)
    ext = os.path.splitext(target)[1] or ".png"
    try:
        ok, buf = cv2.imencode(ext, img, params or [])
    except cv2.error as exc:
        raise ValueError(f"Cannot encode image as {ext} for {target}: {exc}") from exc
    if not ok:
        raise ValueError(f"Cannot encode image as {ext} for {target}")
    atomic_write_bytes(target, buf, durable=durable)
