"""
Crash-safe, Unicode-safe file writing and image I/O (Qt-free).

Every file the app produces (metric CSVs, masks, skeletons, sidecars) should be
written through this module so that:

* a crash, disk-full or network drop mid-write never destroys the previous
  complete file: data goes to a temp file in the same folder, is flushed and
  (by default) fsync'ed, then atomically swapped in with ``os.replace`` (the
  pattern the GT store already uses);
* a target held open by another program (Excel keeps a CSV locked) raises
  :class:`FileLockedError`, a ``PermissionError`` whose message tells the user
  what to do, instead of an opaque ``[WinError 5]``;
* image paths with non-ASCII characters work on Windows, where ``cv2.imread`` /
  ``cv2.imwrite`` silently fail (``Données``, ``José``, CJK folder names).

``durable=True`` (the default, for hand-made data and CSVs) also survives a
power cut; bulk writers of regenerable outputs (46k generated masks) may pass
``durable=False``: still never half-written, but skips the fsync, which costs
0.6-4 ms per file on Windows.

``cv2`` and ``numpy`` are imported lazily so importing this module stays cheap.
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

__all__ = [
    "REPLACE_RETRY_DELAYS",
    "FileLockedError",
    "atomic_open",
    "atomic_write_bytes",
    "atomic_write_text",
    "imread_unicode",
    "imwrite_unicode",
]

#: Windows errors meaning "another process has the file open":
#: ERROR_ACCESS_DENIED (os.replace over an open file), ERROR_SHARING_VIOLATION,
#: ERROR_LOCK_VIOLATION.
_LOCK_WINERRORS = frozenset({5, 32, 33})

#: Pauses (seconds) before retrying a replace that hit a lock. Antivirus and
#: search indexers hold fresh files for a few ms; Excel holds them until closed.
REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.25)

_WRITE_MODES = frozenset({"w", "wb", "wt"})


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


def _temp_path(target: str) -> str:
    """A unique sibling of ``target`` that no image/CSV scanner will pick up."""
    folder, name = os.path.split(target)
    return os.path.join(folder, f".{name}.{secrets.token_hex(4)}.tmp")


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
        fh.close()
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
