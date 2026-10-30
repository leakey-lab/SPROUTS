"""Binary mask files, Qt-free: the predictions (``<project>/mask/<stem>.png``, read only) and
the GT masks the Correct page saves (``gt/mask/<stem>.png``)."""

from .store import (
    MASK_THRESHOLD,
    MaskFileError,
    MaskStore,
    encode_mask_png,
    read_mask_file,
    write_mask_file,
)

__all__ = [
    "MASK_THRESHOLD",
    "MaskFileError",
    "MaskStore",
    "encode_mask_png",
    "read_mask_file",
    "write_mask_file",
]
