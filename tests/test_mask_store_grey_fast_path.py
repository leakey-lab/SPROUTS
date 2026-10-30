"""``MaskStore.read`` of model masks (8-bit grey PNG) and of broken PNGs.

A model mask takes a Pillow fast path, like an editor mask (8-bit RGBA, R == G == B).
Both must read exactly as the old read did: ``imread_unicode(path, IMREAD_GRAYSCALE)``
thresholded at ``MASK_THRESHOLD``, ``MaskFileError`` where that returned None. The file
is now read once and decoded from its bytes.

Pillow and libpng (the reference decode) disagree on some broken files: Pillow accepts a
zlib stream that ends early or never ends, and ignores parts of the IHDR. The fast path
must leave those to the reference decode, so they are tested here for both kinds.
"""

from __future__ import annotations

import io
import os
import sys
import zlib

import cv2
import numpy as np
import pytest
from PIL import Image

import app.masks.store as store_module
from app.io_utils import imread_unicode
from app.masks import MASK_THRESHOLD, MaskFileError, MaskStore

SIG = b"\x89PNG\r\n\x1a\n"


def _old_read(store: MaskStore, stem: str) -> np.ndarray | None:
    """``MaskStore.read`` before any fast path, copied as the reference."""
    path = store.find(stem)
    if path is None:
        return None
    gray = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise MaskFileError(f"cannot decode {path}")
    return gray > MASK_THRESHOLD


def _outcome(read, store: MaskStore, stem: str):
    """The array a read returns, or the text of the MaskFileError it raises."""
    try:
        return read(store, stem)
    except MaskFileError as exc:
        return f"MaskFileError: {exc}"


def _same(a, b) -> bool:
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        return a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b)
    return type(a) is type(b) and a == b


# ----------------------------------------------------------------------------- PNG builders
def _chunk(kind: bytes, body: bytes, *, bad_crc: bool = False) -> bytes:
    crc = zlib.crc32(kind + body) ^ (1 if bad_crc else 0)
    return len(body).to_bytes(4, "big") + kind + body + crc.to_bytes(4, "big")


def _ihdr(w, h, colour, *, depth=8, comp=0, filt=0, inter=0, extra=b"", bad_crc=False):
    body = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([depth, colour, comp, filt, inter])
    return _chunk(b"IHDR", body + extra, bad_crc=bad_crc)


def _png(*chunks: bytes) -> bytes:
    return SIG + b"".join(chunks)


IEND = _chunk(b"IEND", b"")


def _rows(pixels: np.ndarray, filter_byte: int = 0) -> bytes:
    """Raw scanlines (filter type ``filter_byte``) of an (h, w) or (h, w, 4) array."""
    flat = pixels.reshape(pixels.shape[0], -1)
    return b"".join(bytes([filter_byte]) + row.tobytes() for row in flat)


def _cv2_png(img: np.ndarray, *params: int) -> bytes:
    ok, buf = cv2.imencode(".png", img, list(params))
    assert ok
    return buf.tobytes()


def _pil_png(img: Image.Image, **kw) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", **kw)
    return out.getvalue()


def _with_chunks(data: bytes, *extra: tuple[bytes, bytes]) -> bytes:
    """``data`` with extra chunks right after IHDR."""
    return data[:33] + b"".join(_chunk(k, v) for k, v in extra) + data[33:]


def _adam7(pixels: np.ndarray, colour: int) -> bytes:
    """An Adam7-interlaced PNG (neither OpenCV nor Pillow writes one)."""
    passes = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2))
    raw = b""
    for x0, y0, dx, dy in (*passes, (0, 1, 1, 2)):
        sub = pixels[y0::dy, x0::dx]
        if sub.size:
            raw += _rows(sub)
    h, w = pixels.shape[:2]
    return _png(_ihdr(w, h, colour, inter=1), _chunk(b"IDAT", zlib.compress(raw)), IEND)


def _with_window(stream: bytes, cinfo: int) -> bytes:
    """A zlib stream whose header declares a ``1 << (cinfo + 8)`` byte window."""
    cmf = (cinfo << 4) | 8
    flg = stream[1] & 0xC0
    flg += 31 - (cmf * 256 + flg) % 31 if (cmf * 256 + flg) % 31 else 0
    return bytes([cmf, flg]) + stream[2:]


def _levels(h: int, w: int, seed: int) -> np.ndarray:
    """Random grey levels, half of them in the band around the threshold."""
    rng = np.random.default_rng(seed)
    grey = rng.integers(0, 256, (h, w), dtype=np.uint8)
    near = rng.random((h, w)) < 0.5
    grey[near] = rng.integers(MASK_THRESHOLD - 5, MASK_THRESHOLD + 6, int(near.sum()))
    return grey


# ------------------------------------------------------------------------------ model masks
_FILTERS = {
    "none": cv2.IMWRITE_PNG_FILTER_NONE,
    "sub": cv2.IMWRITE_PNG_FILTER_SUB,
    "up": cv2.IMWRITE_PNG_FILTER_UP,
    "avg": cv2.IMWRITE_PNG_FILTER_AVG,
    "paeth": cv2.IMWRITE_PNG_FILTER_PAETH,
    "all": cv2.IMWRITE_PNG_ALL_FILTERS,
}


def _grey_cases() -> dict[str, tuple[bytes, bool]]:
    """name -> (file bytes, whether the fast path should take it)."""
    grey = _levels(37, 53, 1)
    cases: dict[str, tuple[bytes, bool]] = {}
    for name, flag in _FILTERS.items():
        cases[f"filter_{name}"] = (_cv2_png(grey, cv2.IMWRITE_PNG_FILTER, flag), True)
    for level in range(10):
        cases[f"cv2_level_{level}"] = (_cv2_png(grey, cv2.IMWRITE_PNG_COMPRESSION, level), True)
        cases[f"pillow_level_{level}"] = (
            _pil_png(Image.fromarray(grey, "L"), compress_level=level),
            True,
        )
    cases["pillow_optimized"] = (_pil_png(Image.fromarray(grey, "L"), optimize=True), True)
    for strategy in ("FILTERED", "HUFFMAN_ONLY", "RLE", "FIXED"):
        flag = getattr(cv2, f"IMWRITE_PNG_STRATEGY_{strategy}")
        cases[f"strategy_{strategy.lower()}"] = (
            _cv2_png(grey, cv2.IMWRITE_PNG_STRATEGY, flag),
            True,
        )
    for h, w in ((1, 1), (1, 2), (2, 1), (1, 300), (300, 1), (3, 5), (7, 13), (255, 257)):
        cases[f"size_{h}x{w}"] = (_pil_png(Image.fromarray(_levels(h, w, h * w), "L")), True)
    every_level = np.arange(256, dtype=np.uint8).reshape(16, 16)
    cases["every_level"] = (_cv2_png(every_level), True)
    band = np.arange(MASK_THRESHOLD - 8, MASK_THRESHOLD + 8, dtype=np.uint8)
    cases["threshold_band"] = (_cv2_png(np.tile(band, (9, 4))), True)
    model = (_levels(480, 640, 5) > MASK_THRESHOLD).astype(np.uint8) * 255
    cases["model_mask_480x640"] = (_pil_png(Image.fromarray(model, "L")), True)
    # The last row equals the pattern written there before the decode: still exact.
    ramp = np.tile((np.arange(1, 41) % 256).astype(np.uint8), (6, 1))
    cases["last_row_is_the_ramp"] = (_cv2_png(ramp), False)

    # Chunks that never change the pixels.
    plain = _cv2_png(grey)
    exif = b"MM\x00*\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01\x00\x06\x00\x00"
    exif += b"\x00\x00\x00\x00"  # Orientation = 6 (rotate 90)
    profile = b"\nexif\n" + str(len(exif) + 6).encode().rjust(8) + b"\n"
    profile += (b"Exif\x00\x00" + exif).hex().encode() + b"\n"
    for name, extra in {
        "phys": (b"pHYs", b"\x00\x00\x0b\x13\x00\x00\x0b\x13\x01"),
        "time": (b"tIME", b"\x07\xea\x0a\x02\x0c\x00\x00"),
        "text": (b"tEXt", b"Software\x00model"),
        "ztxt": (b"zTXt", b"Comment\x00\x00" + zlib.compress(b"a model mask")),
        "itxt": (b"iTXt", b"Comment\x00\x00\x00en\x00\x00a model mask"),
        "exif_text_profile": (b"zTXt", b"Raw profile type exif\x00\x00" + zlib.compress(profile)),
    }.items():
        cases[f"chunk_{name}"] = (_with_chunks(plain, extra), True)
    # Chunks the fast path leaves to the reference decode.
    icc = b"\x00" * 128
    for name, extra in {
        "trns": (b"tRNS", b"\x00\xc8"),
        "gama": (b"gAMA", (45455).to_bytes(4, "big")),
        "gama_strong": (b"gAMA", (10000).to_bytes(4, "big")),
        "sbit": (b"sBIT", b"\x04"),
        "iccp": (b"iCCP", b"icc\x00\x00" + zlib.compress(icc)),
        "chrm": (b"cHRM", bytes(range(32))),
        "srgb": (b"sRGB", b"\x00"),
        "bkgd": (b"bKGD", b"\x00\x80"),
        "exif": (b"eXIf", exif),
        "unknown": (b"prVt", b"private"),
    }.items():
        cases[f"chunk_{name}"] = (_with_chunks(plain, extra), False)
    cases["interlaced_adam7"] = (_adam7(grey, 0), False)
    cases["interlaced_adam7_1x1"] = (_adam7(grey[:1, :1], 0), False)
    cases["bit_depth_16"] = (_cv2_png(grey.astype(np.uint16) * 257), False)
    cases["bit_depth_1"] = (_pil_png(Image.fromarray(grey > 127)), False)
    cases["grey_alpha"] = (_pil_png(Image.fromarray(np.dstack([grey, grey]), "LA")), False)
    return cases


GREY = _grey_cases()


# ---------------------------------------------------------------------------- broken files
def _broken_cases(colour: int) -> dict[str, tuple[bytes, bool]]:
    """Broken or odd files of colour type ``colour`` (0 grey, 6 RGBA), all CRCs right."""
    rng = np.random.default_rng(colour + 1)
    h, w = 6, 40
    grey = rng.integers(0, 256, (h, w), dtype=np.uint8)
    pixels = grey if colour == 0 else np.dstack([grey, grey, grey, grey[::-1]])
    raw = _rows(pixels)
    one_row = len(raw) // h
    hdr = _ihdr(w, h, colour)
    stream = zlib.compress(raw)

    def file(z: bytes, *, header: bytes = hdr) -> bytes:
        return _png(header, _chunk(b"IDAT", z), IEND)

    co = zlib.compressobj()
    unfinished = co.compress(raw) + co.flush(zlib.Z_SYNC_FLUSH)
    # One row too many, all zeros, and no end: only random spare rows tell it apart.
    co = zlib.compressobj()
    zero_row_unfinished = co.compress(raw + bytes(one_row)) + co.flush(zlib.Z_SYNC_FLUSH)
    # Two equal rows of 300 bytes behind a header that declares a 256-byte window: the
    # second row copies the first from 301 bytes back, which libpng refuses.
    size = 1 if colour == 0 else 4
    g = rng.integers(0, 256, 300 // size, dtype=np.uint8)
    row_bytes = (g if colour == 0 else np.dstack([g, g, g, g])[0]).tobytes()
    far_raw = (b"\x00" + row_bytes) * 2
    near_raw = (b"\x00" + row_bytes[:40]) * 2  # 82 bytes: a 256-byte window covers it
    flg = 0x20  # a preset dictionary, which neither decoder has
    flg += (31 - (0x78 * 256 + flg) % 31) % 31
    dict_stream = bytes([0x78, flg]) + b"\x00\x00\x00\x01" + stream[2:]
    return {
        "good": (file(stream), True),
        "good_split_in_three_idat": (
            _png(
                hdr,
                _chunk(b"IDAT", stream[:3]),
                _chunk(b"IDAT", b""),
                _chunk(b"IDAT", stream[3:]),
                IEND,
            ),
            True,
        ),
        "stream_one_row_short": (file(zlib.compress(raw[:-one_row])), False),
        "stream_half_a_row_short": (file(zlib.compress(raw[: -one_row // 2])), False),
        "stream_never_ends": (file(unfinished), False),
        "stream_one_zero_row_long_never_ends": (file(zero_row_unfinished), False),
        "stream_one_row_long": (file(zlib.compress(raw + raw[:one_row])), False),
        "stream_half_a_row_long": (file(zlib.compress(raw + raw[: one_row // 2])), False),
        "garbage_after_the_stream": (file(stream + b"garbage!"), True),
        "bad_checksum": (file(stream[:-4] + bytes(b ^ 0xFF for b in stream[-4:])), False),
        "bad_checksum_in_a_later_idat": (
            _png(
                hdr,
                _chunk(b"IDAT", stream[:-4]),
                _chunk(b"IDAT", bytes(b ^ 0xFF for b in stream[-4:])),
                IEND,
            ),
            False,
        ),
        "bad_filter_type": (file(zlib.compress(b"\x07" + raw[1:])), False),
        "preset_dictionary": (file(dict_stream), False),
        "compression_method_1": (file(stream, header=_ihdr(w, h, colour, comp=1)), False),
        "filter_method_1": (file(stream, header=_ihdr(w, h, colour, filt=1)), False),
        "interlace_method_2": (file(stream, header=_ihdr(w, h, colour, inter=2)), False),
        "ihdr_of_14_bytes": (file(stream, header=_ihdr(w, h, colour, extra=b"\x00")), False),
        "ihdr_bad_crc": (file(stream, header=_ihdr(w, h, colour, bad_crc=True)), False),
        "two_ihdr": (_png(hdr, hdr, _chunk(b"IDAT", stream), IEND), False),
        "width_0": (file(stream, header=_ihdr(0, h, colour)), False),
        "height_0": (file(stream, header=_ihdr(w, 0, colour)), False),
        "huge_declared_size": (file(stream, header=_ihdr(1 << 16, 1 << 16, colour)), False),
        "text_inside_the_stream": (
            _png(
                hdr,
                _chunk(b"IDAT", stream[:5]),
                _chunk(b"tEXt", b"k\x00v"),
                _chunk(b"IDAT", stream[5:]),
                IEND,
            ),
            False,
        ),
        "second_idat_run_after_the_stream": (
            _png(
                hdr,
                _chunk(b"IDAT", stream),
                _chunk(b"tEXt", b"k\x00v"),
                _chunk(b"IDAT", b"more"),
                IEND,
            ),
            True,
        ),
        "small_window_too_far_back": (
            _png(
                _ihdr(300 // size, 2, colour),
                _chunk(b"IDAT", _with_window(zlib.compress(far_raw, 9), 0)),
                IEND,
            ),
            False,
        ),
        "small_window_covering_the_image": (
            _png(
                _ihdr(40 // size, 2, colour),
                _chunk(b"IDAT", _with_window(zlib.compress(near_raw, 9), 0)),
                IEND,
            ),
            True,
        ),
        "no_idat": (_png(hdr, IEND), False),
        "empty_idat": (_png(hdr, _chunk(b"IDAT", b""), IEND), False),
        "no_iend": (_png(hdr, _chunk(b"IDAT", stream)), False),
        "iend_with_data": (_png(hdr, _chunk(b"IDAT", stream), _chunk(b"IEND", b"xx")), True),
        "bytes_after_iend": (file(stream) + b"trailing", True),
        "idat_after_iend": (file(stream) + _chunk(b"IDAT", stream), True),
        "phys_of_3_bytes": (
            _png(hdr, _chunk(b"pHYs", b"\x00\x00\x01"), _chunk(b"IDAT", stream), IEND),
            True,
        ),
        "phys_after_idat": (
            _png(hdr, _chunk(b"IDAT", stream), _chunk(b"pHYs", b"\x00" * 9), IEND),
            True,
        ),
        "text_without_separator": (
            _png(hdr, _chunk(b"tEXt", b"novalue"), _chunk(b"IDAT", stream), IEND),
            True,
        ),
        "broken_ztxt": (
            _png(hdr, _chunk(b"zTXt", b"k\x00\x00broken"), _chunk(b"IDAT", stream), IEND),
            True,
        ),
        "chunk_past_the_end": (file(stream)[:-6], False),
        "not_a_png": (b"\x89PNG\r\n\x1a\nnot a png at all, but long enough to look at", False),
    }


BROKEN = {
    f"broken_{kind}_{name}": case
    for kind, colour in (("grey", 0), ("rgba", 6))
    for name, case in _broken_cases(colour).items()
}
ALL = {**{f"grey_{name}": case for name, case in GREY.items()}, **BROKEN}


def _write(tmp_path, data: bytes) -> MaskStore:
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.directory, exist_ok=True)
    with open(store.path_for("a"), "wb") as fh:
        fh.write(data)
    return store


@pytest.mark.parametrize("name", sorted(ALL))
def test_read_is_the_old_read(tmp_path, name):
    store = _write(tmp_path, ALL[name][0])
    old = _outcome(_old_read, store, "a")
    assert _same(_outcome(MaskStore.read, store, "a"), old)
    assert _same(_outcome(MaskStore.read, store, "a"), old)  # the same again


@pytest.mark.parametrize("name", sorted(ALL))
def test_only_the_expected_files_take_the_fast_path(name):
    data, fast = ALL[name]
    plane = store_module._decode_plain_png(data)
    assert (plane is not None) == fast
    if plane is not None:  # the plane itself, before the threshold, is the reference one
        reference = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        assert plane.dtype == np.uint8 and np.array_equal(plane, reference)


@pytest.mark.parametrize("name", sorted(ALL))
def test_the_bytes_decode_like_the_file_decode(tmp_path, name):
    store = _write(tmp_path, ALL[name][0])
    from_file = imread_unicode(store.path_for("a"), cv2.IMREAD_GRAYSCALE)
    from_bytes = store_module._decode_reference(ALL[name][0])
    assert (from_file is None) == (from_bytes is None)
    if from_file is not None:
        assert np.array_equal(from_file, from_bytes)


def test_random_model_masks_read_back_exactly(tmp_path):
    rng = np.random.default_rng(17)
    for _ in range(40):
        h, w = (int(v) for v in rng.integers(1, 300, 2))
        grey = (rng.random((h, w)) < 0.3).astype(np.uint8) * 255
        grey[rng.random((h, w)) < 0.05] = rng.integers(190, 211)
        data = _pil_png(Image.fromarray(grey, "L"), compress_level=int(rng.integers(0, 10)))
        store = _write(tmp_path, data)
        assert store_module._decode_plain_png(data) is not None
        assert np.array_equal(store.read("a"), grey > MASK_THRESHOLD)
        assert np.array_equal(store.read("a"), _old_read(store, "a"))


# ------------------------------------------------------------------- files that cannot be read
def _missing(tmp_path, monkeypatch) -> MaskStore:
    """A store whose mask vanishes between find() and the read."""
    store = _write(tmp_path, GREY["filter_none"][0])
    path = store.path_for("a")
    os.remove(path)
    monkeypatch.setattr(MaskStore, "find", lambda self, stem: path)
    return store


def test_a_vanished_file_raises_as_before(tmp_path, monkeypatch):
    store = _missing(tmp_path, monkeypatch)
    old = _outcome(_old_read, store, "a")
    assert old.startswith("MaskFileError")
    assert _outcome(MaskStore.read, store, "a") == old


def test_a_folder_in_place_of_the_file_raises_as_before(tmp_path, monkeypatch):
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.path_for("a"))
    monkeypatch.setattr(MaskStore, "find", lambda self, stem: self.path_for(stem))
    old = _outcome(_old_read, store, "a")
    assert old.startswith("MaskFileError")
    assert _outcome(MaskStore.read, store, "a") == old


@pytest.mark.skipif(sys.platform != "win32", reason="an exclusive lock is a Windows thing")
def test_a_locked_file_raises_as_before(tmp_path):
    import ctypes
    from ctypes import wintypes

    store = _write(tmp_path, GREY["filter_none"][0])
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    generic_read, no_sharing, open_existing = 0x80000000, 0, 3
    handle = kernel32.CreateFileW(
        store.path_for("a"), generic_read, no_sharing, None, open_existing, 0, None
    )
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        old = _outcome(_old_read, store, "a")
        assert old.startswith("MaskFileError")
        assert _outcome(MaskStore.read, store, "a") == old
    finally:
        kernel32.CloseHandle(handle)
    assert isinstance(store.read("a"), np.ndarray)  # readable again once released
