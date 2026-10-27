"""Hand the image the Correct page leaves back to its read-ahead (Qt-free).

Paging on (PgDn) leaves an image that PgUp opens again: it is the next image's neighbour.
Its photo and its layers' documents are in memory, and clean (the page saved or discarded
every edit before it moved on). Instead of reading them all again on the worker (photo,
mask and skeleton: about 34 ms at 2048 x 1536), the page keeps them as a finished read
(:class:`~app.gui.correct.read_ahead.AheadImage`): the photo as it was decoded, and each
layer's document as the payload its ``read_ahead`` reader would have made. The documents
let go of those arrays when the next image opens, so nothing is copied.

A layer gives its payload only while its document is as saved and its files are still the
ones it was opened from or last saved to: those stamps (the session's) travel with the
payload, and the next open adopts it only while they still match, as for any read ahead.
When one layer cannot give its payload nothing is handed back: a read without it would
leave that layer's read to the GUI thread at the next open.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.gui.correct.read_ahead import AheadImage

__all__ = ["closed_image", "read_back"]


def closed_image(
    opened: AheadImage, layers: Mapping[str, object], stamps: Mapping[str, tuple] | None
) -> AheadImage | None:
    """``opened`` (the photo of the image being left, still current) with the payload of
    every layer that reads ahead, or None.

    ``stamps`` are each layer's file stamps at open or at its last save, while they are
    still the files' (``CorrectSession.saved_stamps``); None: something is not as saved.
    """
    if stamps is None or not opened.is_current():
        return None
    payloads: dict[str, object] = {}
    for layer_id, layer in layers.items():
        if getattr(layer, "read_ahead", None) is None:
            continue  # it reads nothing ahead: it opens by itself either way
        payload = read_back(layer, stamps.get(layer_id))
        if payload is None:
            return None
        payloads[layer_id] = payload
    return AheadImage(opened.image_path, opened.stamp, opened.photo, payloads)


def read_back(layer: object, stamps: tuple | None) -> object | None:
    """``layer``'s document as its read-ahead payload: its optional ``read_back(stamps)``
    hook (the mask, the skeleton and the class labels have one); None when there is none."""
    if stamps is None:
        return None
    hook = getattr(layer, "read_back", None)
    return hook(stamps) if callable(hook) else None
