"""
Image utilities for the Growth-Lines hover panel of the length dashboard.

* :class:`ImageIndex` maps ``(tube, position, "YYYY.MM.DD")`` to an image path. It
  snapshots the image manager's names when the dashboard opens but parses them
  (with the shared :func:`app.naming.parse_image_name`) only on first use, so
  opening a dashboard no longer pays for 46k name parses (PV-17).
* :class:`ThumbnailCache` encodes each hovered image once as a small JPEG and keeps
  a bounded LRU of the results (PV-09: base64 PNGs of ~75 KiB each were resent on
  every hover and never evicted).
* :class:`ThumbnailServer` serves those JPEGs by URL from the dashboard's own Flask
  server, so a hover response carries ten short URLs instead of ~0.75 MB of base64
  and Chromium caches and fetches the images in parallel.

Qt-free: the Flask route runs on the werkzeug server thread(s); the caches are
thread-safe.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import secrets
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.naming import parse_image_name

logger = logging.getLogger(__name__)

#: ``(tube, position, "YYYY.MM.DD")`` -- the key of one captured image.
ImageKey = tuple[int, int, str]

#: Bounding box of a hover thumbnail. Sized so each image is a meaningful
#: >= 100x100 preview in the ~300 px wide Growth-Lines side panel.
THUMBNAIL_SIZE = (256, 256)
#: JPEG quality of a thumbnail: no visible artifacts at 256 px, ~10 KB each.
THUMBNAIL_QUALITY = 82
#: Thumbnails kept in memory per dashboard (~10 KB each, so ~5 MB at most).
THUMBNAIL_CACHE_CAPACITY = 512

_DATE_TOKEN = re.compile(r"\d{4}\.\d{2}\.\d{2}")


def encode_thumbnail_jpeg(
    path: str, size: tuple[int, int] = THUMBNAIL_SIZE, quality: int = THUMBNAIL_QUALITY
) -> bytes:
    """Read an image and return a JPEG thumbnail that fits in ``size``.

    JPEG sources are downscaled while decoding (``Image.draft``), which skips most
    of the decode work. Returns ``b""`` if the file is missing, unreadable or not
    an image.
    """
    try:
        with Image.open(path) as img:
            scale = min(size[0] / img.width, size[1] / img.height, 1.0)
            target = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
            img.draft("RGB", target)  # no-op for non-JPEG sources
            rgb = img.convert("RGB")
        rgb.thumbnail(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        logger.warning("Failed to encode thumbnail for %s: %s", path, exc)
        return b""


def jpeg_data_url(data: bytes) -> str:
    """``data:`` URL for JPEG bytes (``""`` for no data)."""
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}" if data else ""


class ThumbnailCache:
    """Thread-safe LRU of encoded JPEG thumbnails keyed by :data:`ImageKey`.

    Failures are cached too (as ``b""``) so a broken file is not re-read on every
    hover. Encoding runs outside the lock: concurrent image requests from the
    browser are decoded in parallel.
    """

    def __init__(self, paths: Mapping[ImageKey, str], capacity: int = THUMBNAIL_CACHE_CAPACITY):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._paths = paths
        self._capacity = capacity
        self._jpegs: OrderedDict[ImageKey, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def jpeg(self, key: ImageKey) -> bytes:
        """JPEG bytes of the image at ``key`` (``b""`` if unknown or unreadable)."""
        with self._lock:
            cached = self._jpegs.get(key)
            if cached is not None:
                self._jpegs.move_to_end(key)
                return cached
        path = self._paths.get(key)
        if path is None:
            return b""
        data = encode_thumbnail_jpeg(path)
        with self._lock:
            self._jpegs[key] = data
            self._jpegs.move_to_end(key)
            while len(self._jpegs) > self._capacity:
                self._jpegs.popitem(last=False)
        return data

    def data_url(self, key: ImageKey) -> str:
        """The thumbnail as a ``data:image/jpeg`` URL (``""`` if unavailable)."""
        return jpeg_data_url(self.jpeg(key))

    def cached_keys(self) -> list[ImageKey]:
        """Keys currently held, least recently used first."""
        with self._lock:
            return list(self._jpegs)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jpegs)


class ImageIndex(Mapping[ImageKey, str]):
    """``(tube, position, "YYYY.MM.DD")`` -> image path, parsed on first use.

    Names without a stand-alone tube, position and date token are skipped. The
    index also owns the dashboard's :class:`ThumbnailCache` (``.thumbnails``).
    """

    def __init__(
        self,
        images: Mapping[str, str] | None = None,
        *,
        thumbnail_capacity: int = THUMBNAIL_CACHE_CAPACITY,
    ):
        self._names: dict[str, str] = dict(images or {})  # snapshot: name -> path
        self._paths: dict[ImageKey, str] | None = None
        self._lock = threading.Lock()
        self.thumbnails = ThumbnailCache(self, thumbnail_capacity)

    def _index(self) -> dict[ImageKey, str]:
        paths = self._paths
        if paths is None:
            with self._lock:
                if self._paths is None:
                    self._paths = self._parse(self._names)
                paths = self._paths
        return paths

    @staticmethod
    def _parse(names: Mapping[str, str]) -> dict[ImageKey, str]:
        paths: dict[ImageKey, str] = {}
        for name, path in names.items():
            parsed = parse_image_name(name)
            if parsed.is_complete:
                paths[(parsed.tube, parsed.position, parsed.date)] = path
        return paths

    def __getitem__(self, key: ImageKey) -> str:
        return self._index()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._index()

    def __iter__(self) -> Iterator[ImageKey]:
        return iter(self._index())

    def __len__(self) -> int:
        return len(self._index())


class ThumbnailServer:
    """Serves a :class:`ThumbnailCache` over HTTP from a dashboard's Flask app.

    URLs look like ``/sprouts-thumb/<token>/<tube>/<position>/<YYYY.MM.DD>.jpg``.
    The random per-instance token keeps browser-cached thumbnails from leaking
    into a later dashboard session on the same port (another project).
    """

    def __init__(self, cache: ThumbnailCache, *, url_prefix: str = "/sprouts-thumb"):
        self._cache = cache
        self._base = f"{url_prefix.rstrip('/')}/{secrets.token_hex(6)}"

    def url(self, tube: int, position: int, date_str: str) -> str:
        """URL of one thumbnail (``date_str`` is ``"YYYY.MM.DD"``)."""
        return f"{self._base}/{int(tube)}/{int(position)}/{date_str}.jpg"

    def register(self, server: Any) -> None:
        """Add the thumbnail route to a Flask app (before it serves requests)."""
        from flask import Response, abort

        def serve(tube: int, position: int, date_str: str):
            if not _DATE_TOKEN.fullmatch(date_str):
                abort(404)
            data = self._cache.jpeg((tube, position, date_str))
            if not data:
                abort(404)
            response = Response(data, mimetype="image/jpeg")
            response.headers["Cache-Control"] = "private, max-age=86400, immutable"
            return response

        server.add_url_rule(
            f"{self._base}/<int:tube>/<int:position>/<date_str>.jpg",
            endpoint=f"sprouts_thumbnail_{self._base.rsplit('/', 1)[-1]}",
            view_func=serve,
        )


def build_available_images_map(image_manager) -> ImageIndex:
    """Index the image manager's images for hover display (parsed lazily).

    Args:
        image_manager: an ImageManager-like object whose ``images`` maps image
            name -> path, or None.

    Returns:
        An :class:`ImageIndex` (a read-only mapping ``(tube, position,
        "YYYY.MM.DD") -> path``) whose ``thumbnails`` cache serves the images.
    """
    return ImageIndex(getattr(image_manager, "images", None))


def get_encoded_image(available_images: Mapping, tube: int, position: int, date) -> str:
    """Hover thumbnail of one image as a ``data:image/jpeg`` URL.

    Args:
        available_images: the map returned by :func:`build_available_images_map`.
        tube: tube number.
        position: position (depth) number.
        date: pandas Timestamp or date object.

    Returns:
        The data URL, or ``""`` if the image is unknown or unreadable.
    """
    try:
        key = (tube, position, date.strftime("%Y.%m.%d"))
    except (AttributeError, ValueError) as exc:
        logger.debug("Invalid date passed to get_encoded_image: %s (%s)", date, exc)
        return ""
    cache = getattr(available_images, "thumbnails", None)
    if cache is not None:
        return cache.data_url(key)
    path = available_images.get(key)
    return jpeg_data_url(encode_thumbnail_jpeg(path)) if path else ""
