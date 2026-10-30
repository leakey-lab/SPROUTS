"""What the skeleton document hands out (Qt-free): a read done ahead, a save's result, stamps.

:class:`SkeletonRead` is one image's start file read and thinned on a worker thread (the
read-ahead's payload); :class:`SkeletonSaveResult` is what
:meth:`~app.gui.correct.skeleton_document.SkeletonDocument.save` did; :func:`stamps_of`
stamps the pinned files of :class:`~app.gui.skeleton_document.SkeletonFiles`.
``app.gui.correct.skeleton_document`` re-exports the two records.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.gui.skeleton_document import SkeletonFiles
from app.io_utils import file_stamp

__all__ = ["NOT_SHOWN", "SkeletonRead", "SkeletonSaveResult", "stamps_of"]

#: The status tooltip of a GT skeleton found at open that could not be read (final review C1).
NOT_SHOWN = "Saved skeleton could not be read: it is not shown and is kept unless you save over it"


@dataclass(frozen=True)
class SkeletonSaveResult:
    """What :meth:`SkeletonDocument.save` did.

    ``path`` is the GT file written (None when nothing was written) and ``error`` the
    reason of a failure. ``unchanged``: nothing to write (the skeleton is as saved, or is
    the prediction with no GT file on disk); ``removed``: the skeleton is the prediction
    again, so its GT file was removed.
    """

    ok: bool
    path: str | None = None
    error: str = ""
    unchanged: bool = False
    removed: bool = False


@dataclass(frozen=True)
class SkeletonRead:
    """The start file of one image's skeleton, read and thinned ahead on a worker thread.

    ``stamps`` are the ``file_stamps()`` taken just before the read; ``path`` / ``kind`` the
    start file (None: nothing to read); ``mask`` the thinned skeleton at ``size`` and ``tips``
    its endpoints (``skeleton_tips``); ``mask`` None with a ``path``: unreadable, the open
    reads it again (and says so).
    """

    image_path: str
    size: tuple[int, int]
    stamps: tuple
    path: str | None
    kind: str | None
    save_size: tuple[int, int] | None = None
    mask: np.ndarray | None = None
    tips: np.ndarray | None = None

    @property
    def nbytes(self) -> int:
        """Bytes held (the read-ahead cache counts them)."""
        held = (self.mask, self.tips)
        return sum(int(array.nbytes) for array in held if array is not None)


def stamps_of(files: SkeletonFiles) -> tuple:
    """``(GT, prediction)`` stamps; the prediction path is the resolver's file when it finds
    one, else ``skeletons/<stem>_fake.png``."""
    generated = files.generated_path() or files.layout.skeleton_path(files.image_path)
    return (file_stamp(files.gt_path), file_stamp(generated))
