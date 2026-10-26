"""The pre-Generate scan of ``mask/`` and the overwrite policy (RB-01 / PI-04 / FR-04).

``mask/`` holds predictions only (since WG-T7 every human correction is saved under
``gt/``), so Generate's prompt needs only how many of the folder's images already have a
mask. :func:`scan_existing_masks` lists ``mask/`` once and reads no file: the number of
masks is the number of names. The earlier version also read the PNG header of every mask
to tell hand-saved masks from model output; nothing read that split any more, and W5-T5
removed it with the header reads (a thread pool for 46k masks).

Qt-free and torch-free, so the pre-flight scan is cheap and unit-testable.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.project_layout import ProjectLayout, key_to_stems

__all__ = [
    "ExistingMasks",
    "OverwritePolicy",
    "scan_existing_masks",
]


@dataclass(frozen=True)
class ExistingMasks:
    """Stems that already have a mask in ``mask/``."""

    stems: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> ExistingMasks:
        return cls()

    @property
    def count(self) -> int:
        return len(self.stems)

    def __bool__(self) -> bool:
        return self.count > 0


def scan_existing_masks(layout: ProjectLayout, stems: Iterable[str] | None = None) -> ExistingMasks:
    """The stems that have a mask in ``layout.mask_dir``.

    ``stems`` limits the result to masks of those images (orphan masks of images
    that are no longer in the folder are irrelevant to a generation run), and a mask
    whose name differs from its image's only in case is reported under the image's
    stem (O7: ``mask/a.png`` is the mask of ``A.png``).
    """
    found = layout.scan_masks()  # {} when mask/ does not exist
    if stems is not None:
        wanted = set(stems)
        found = {s: p for s, p in key_to_stems(found, wanted).items() if s in wanted}
    return ExistingMasks(stems=frozenset(found))


class OverwritePolicy(enum.Enum):
    """What a generation run does with predictions that already exist."""

    KEEP_EXISTING = "keep_existing"  # predict the missing masks only (the safe default)
    OVERWRITE_ALL = "overwrite_all"  # replace every prediction (gt/ is never touched)

    def skip_stems(self, existing: ExistingMasks) -> frozenset[str]:
        """Stems the generator must not write under this policy."""
        if self is OverwritePolicy.KEEP_EXISTING:
            return existing.stems
        return frozenset()
