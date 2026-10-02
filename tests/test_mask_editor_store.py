"""AR-11 / PE-07 / RB-11: Qt-free storage of hand-traced masks (``mask/<stem>.png``)."""

import os

import cv2
import numpy as np
import pytest

from app.masks import MaskFileError, MaskStore, encode_mask_png


def _mask(h=60, w=80):
    on = np.zeros((h, w), bool)
    on[10:14, 5:60] = True
    on[30:50, 40:45] = True
    return on


def _qt_reference_png(path, on):
    """What the editor wrote before (QImage ARGB32, quality 100)."""
    from PyQt6.QtGui import QImage

    out = np.zeros((*on.shape, 4), np.uint8)
    out[on] = 255
    h, w = on.shape
    assert QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32).save(str(path), "PNG", 100)


def test_paths_follow_the_project_layout(tmp_path):
    store = MaskStore.for_image(str(tmp_path / "plot_001.jpg"))
    assert store.path_for("plot_001") == os.path.normpath(str(tmp_path / "mask" / "plot_001.png"))
    assert store.find("plot_001") is None
    assert store.read("plot_001") is None


def test_write_is_pixel_identical_to_the_qt_writer_and_far_smaller(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    on = np.zeros((480, 640), bool)
    on[100:110, 50:600] = True
    store = MaskStore.for_image(str(tmp_path / "a.jpg"))
    path = store.write("a", on)
    ref = tmp_path / "ref.png"
    _qt_reference_png(ref, on)

    new = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    old = cv2.imread(str(ref), cv2.IMREAD_UNCHANGED)
    assert new.shape == old.shape == (480, 640, 4)
    assert np.array_equal(new, old)  # white on transparent, alpha only 0/255
    assert os.path.getsize(path) < 64 * 1024 < os.path.getsize(ref)
    assert [p for p in os.listdir(tmp_path / "mask") if p != "a.png"] == []  # no temp left


def test_round_trip_and_grayscale_readers_see_the_same_mask(tmp_path):
    on = _mask()
    store = MaskStore.for_image(str(tmp_path / "plot_001.png"))
    store.write("plot_001", on)
    assert np.array_equal(store.read("plot_001"), on)
    gray = cv2.imread(store.path_for("plot_001"), cv2.IMREAD_GRAYSCALE)
    assert set(np.unique(gray)) == {0, 255}
    assert np.array_equal(gray > 200, on)


def test_png_bytes_are_deterministic():
    assert encode_mask_png(_mask()) == encode_mask_png(_mask())


def test_failed_replace_keeps_the_previous_file(tmp_path, monkeypatch):
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    path = store.write("a", _mask())
    before = open(path, "rb").read()

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        store.write("a", np.zeros((60, 80), bool) | True)
    assert open(path, "rb").read() == before
    assert sorted(os.listdir(tmp_path / "mask")) == ["a.png"]


def test_remove_deletes_every_saved_variant(tmp_path):
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    store.write("a", _mask())
    jpg = os.path.join(store.directory, "a.jpg")
    cv2.imwrite(jpg, np.zeros((60, 80), np.uint8))
    assert store.remove("a") is True
    assert store.find("a") is None and not os.path.exists(jpg)
    assert store.remove("a") is False


def test_unreadable_file_raises(tmp_path):
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.directory)
    with open(store.path_for("a"), "wb") as fh:
        fh.write(b"not a png")
    with pytest.raises(MaskFileError):
        store.read("a")


# ------------------------------------------------------------- what read() sees per format
# read() is a grayscale decode + ``> MASK_THRESHOLD`` for every file kind. These pin that
# contract for each kind a project can hold, so a faster decode path must keep it exactly:
# the alpha channel is ignored, colours go through the luma weights, 200 itself is off.
def _grid_bgr(colours) -> np.ndarray:
    """One 4x4 block per colour, side by side."""
    return np.concatenate([np.full((4, 4, len(c)), c, np.uint8) for c in colours], axis=1)


def _read_png(tmp_path, img) -> np.ndarray:
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.directory, exist_ok=True)
    assert cv2.imwrite(store.path_for("a"), img)
    return store.read("a")


def _blocks(on: np.ndarray) -> list[bool]:
    """The value of each 4x4 block (asserting the block is uniform)."""
    values = []
    for x in range(0, on.shape[1], 4):
        block = on[:, x : x + 4]
        assert block.all() or not block.any()
        values.append(bool(block[0, 0]))
    return values


def test_read_of_a_grey_model_mask_thresholds_at_200(tmp_path):
    grey = np.array([[0, 199, 200, 201, 255]], np.uint8).repeat(4, 0).repeat(4, 1)
    assert _blocks(_read_png(tmp_path, grey)) == [False, False, False, True, True]


def test_read_of_an_editor_rgba_mask_is_the_saved_plane(tmp_path):
    on = _mask()
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    store.write("a", on)
    assert np.array_equal(store.read("a"), on)


def test_read_of_rgba_ignores_the_alpha_channel(tmp_path):
    bgra = _grid_bgr(
        [(255, 255, 255, 255), (255, 255, 255, 128), (255, 255, 255, 0), (0, 0, 0, 255)]
    )
    assert _blocks(_read_png(tmp_path, bgra)) == [True, True, True, False]


@pytest.mark.parametrize("alpha", [None, 255, 77])
def test_read_of_coloured_pixels_uses_their_luma(tmp_path, alpha):
    colours = [
        (255, 255, 255),  # white: on
        (0, 255, 0),  # pure green (BGR), luma about 150: off
        (255, 255, 180),  # pale cyan-ish, luma about 223: on
        (201, 201, 201),  # grey just over the threshold: on
        (200, 200, 200),  # exactly the threshold: off
        (0, 0, 255),  # pure red, luma about 76: off
        (255, 0, 0),  # pure blue, luma about 29: off (its first channel alone would be on)
    ]
    if alpha is not None:
        colours = [(*c, alpha) for c in colours]
    got = _blocks(_read_png(tmp_path, _grid_bgr(colours)))
    assert got == [True, False, True, True, False, False, False]


def test_read_of_a_legacy_jpg_mask(tmp_path):
    on = np.zeros((48, 64), bool)
    on[8:40, 16:48] = True
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.directory)
    path = os.path.join(store.directory, "a.jpg")
    assert cv2.imwrite(path, on.astype(np.uint8) * 255, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert store.find("a") == path
    assert np.array_equal(store.read("a"), cv2.imread(path, cv2.IMREAD_GRAYSCALE) > 200)
    assert np.array_equal(store.read("a")[12:36, 20:44], on[12:36, 20:44])  # inside the edges


# ------------------------------------------------------------ the fast path for saved masks
# An editor-saved mask (8-bit RGBA, R == G == B) and a model mask (8-bit grey) skip the
# reference decode. Every file must still read exactly as the reference decode reads it:
# ``imread(IMREAD_GRAYSCALE) > MASK_THRESHOLD``, or MaskFileError where that returns None.
def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    chunks, pos = [], 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        chunks.append((data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]))
        pos += 12 + length
    return chunks


def _png_from_chunks(chunks, *, bad_crc: bytes | None = None) -> bytes:
    import zlib

    out = bytearray(b"\x89PNG\r\n\x1a\n")
    for kind, body in chunks:
        crc = zlib.crc32(kind + body) ^ (0xFFFF if kind == bad_crc else 0)
        out += len(body).to_bytes(4, "big") + kind + body + crc.to_bytes(4, "big")
    return bytes(out)


def _with_chunk(data: bytes, kind: bytes, body: bytes) -> bytes:
    """``data`` with an extra chunk right after IHDR."""
    chunks = _png_chunks(data)
    return _png_from_chunks([chunks[0], (kind, body), *chunks[1:]])


def _grey_rgba(h=48, w=64, *, alpha=None) -> np.ndarray:
    """Every grey level 0..255 (R == G == B) with a random alpha."""
    rng = np.random.default_rng(7)
    grey = (np.arange(h * w) % 256).astype(np.uint8).reshape(h, w)
    out = np.dstack([grey, grey, grey, rng.integers(0, 256, (h, w), dtype=np.uint8)])
    if alpha is not None:
        out[:, :, 3] = alpha
    return out


def _encoded(img, ext=".png", params=()) -> bytes:
    ok, buf = cv2.imencode(ext, img, list(params))
    assert ok
    return buf.tobytes()


def _reference(path: str):
    gray = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
    return None if gray is None else gray > 200


def _mask_file_cases() -> dict[str, tuple[bytes, str]]:
    from PIL import Image

    rng = np.random.default_rng(3)
    editor = encode_mask_png(_mask())
    coloured = rng.integers(0, 256, (40, 50, 4), dtype=np.uint8)
    nearly = _grey_rgba()
    nearly[5, 7, 1] ^= 1  # one pixel with G != R: not a grey image
    blue = _grey_rgba()
    blue[30, 2, 0] ^= 4  # (BGR order) one pixel whose blue alone differs
    rgb_grey = _grey_rgba()[:, :, :3]

    def pil_png(mode, arr, **kw) -> bytes:
        import io

        out = io.BytesIO()
        Image.fromarray(arr, mode).save(out, "PNG", **kw)
        return out.getvalue()

    grey_alpha = pil_png("LA", _grey_rgba()[:, :, [0, 3]])
    palette = Image.fromarray(_grey_rgba()[:, :, :3]).quantize(64)
    import io

    pal_buf = io.BytesIO()
    palette.save(pal_buf, "PNG")
    rgba16 = (_grey_rgba().astype(np.uint16) * 257).astype(np.uint16)
    truncated = editor[: len(editor) // 2]
    idat = [c for c in _png_chunks(editor) if c[0] == b"IDAT"]
    split_idat = _png_from_chunks(
        [c for c in _png_chunks(editor) if c[0] == b"IHDR"]
        + [(b"IDAT", idat[0][1][:9]), (b"IDAT", idat[0][1][9:])]
        + [(b"IEND", b"")]
    )
    extra_idat = _png_from_chunks(
        [c for c in _png_chunks(editor) if c[0] != b"IEND"]
        + [(b"IDAT", b"\x00" * 16), (b"IEND", b"")]
    )
    garbage_idat = _png_from_chunks(
        [c for c in _png_chunks(editor) if c[0] != b"IEND"]
        + [(b"IDAT", rng.integers(0, 256, 64, dtype=np.uint8).tobytes()), (b"IEND", b"")]
    )
    exif = (
        b"MM\x00*\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01\x00\x06\x00\x00"
        b"\x00\x00\x00\x00"
    )  # Orientation = 6 (rotate 90)
    return {
        "editor_rgba": (editor, "fast"),
        "editor_rgba_every_grey_level_random_alpha": (_encoded(_grey_rgba()), "fast"),
        "semi_transparent_white": (
            _encoded(np.full((40, 50, 4), (255, 255, 255, 128), np.uint8)),
            "fast",
        ),
        "fully_transparent_grey": (_encoded(_grey_rgba(alpha=0)), "fast"),
        "text_chunk": (_with_chunk(editor, b"tEXt", b"k\x00v"), "fast"),
        "split_idat_chunks": (split_idat, "fast"),
        "coloured_rgba": (_encoded(coloured), "reference"),
        "one_coloured_pixel": (_encoded(nearly), "reference"),
        "one_bluish_pixel": (_encoded(blue), "reference"),
        "rgb_grey": (_encoded(rgb_grey), "reference"),
        "grey_model_mask": (_encoded(_grey_rgba()[:, :, 0]), "fast"),
        "grey_alpha": (grey_alpha, "reference"),
        "palette": (pal_buf.getvalue(), "reference"),
        "rgba_16_bit": (_encoded(rgba16), "reference"),
        "interlaced_rgba": (_interlaced(), "reference"),
        "gamma_chunk": (_with_chunk(editor, b"gAMA", (45455).to_bytes(4, "big")), "reference"),
        "srgb_chunk": (_with_chunk(editor, b"sRGB", b"\x00"), "reference"),
        "exif_rotation_chunk": (_with_chunk(editor, b"eXIf", exif), "reference"),
        "bad_idat_crc": (_png_from_chunks(_png_chunks(editor), bad_crc=b"IDAT"), "reference"),
        "bad_text_crc": (
            _png_from_chunks(_png_chunks(_with_chunk(editor, b"tEXt", b"k\x00v")), bad_crc=b"tEXt"),
            "reference",
        ),
        # libpng only warns about data after the zlib stream; Pillow ignores it too
        "extra_idat_after_the_stream": (extra_idat, "fast"),
        "garbage_idat_after_the_stream": (garbage_idat, "fast"),
        "truncated": (truncated, "reference"),
        "legacy_jpg": (_encoded(_grey_rgba()[:, :, 0], ".jpg"), "reference"),
    }


def _interlaced() -> bytes:
    """An Adam7-interlaced 8-bit RGBA PNG of grey pixels (Pillow cannot write one)."""
    import zlib

    img = _grey_rgba(16, 16)
    passes = [
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    ]
    raw = bytearray()
    for x0, y0, dx, dy in passes:
        sub = img[y0::dy, x0::dx]
        for row in sub:
            raw += b"\x00" + row.tobytes()
    ihdr = (16).to_bytes(4, "big") * 2 + bytes([8, 6, 0, 0, 1])
    return _png_from_chunks([(b"IHDR", ihdr), (b"IDAT", zlib.compress(bytes(raw))), (b"IEND", b"")])


CASES = _mask_file_cases()


@pytest.mark.parametrize("name", sorted(CASES))
def test_read_returns_exactly_what_the_reference_decode_returns(tmp_path, name):
    data, _path = CASES[name]
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.directory)
    ext = ".jpg" if name == "legacy_jpg" else ".png"
    path = os.path.join(store.directory, "a" + ext)
    with open(path, "wb") as fh:
        fh.write(data)
    expected = _reference(path)
    if expected is None:
        with pytest.raises(MaskFileError):
            store.read("a")
    else:
        got = store.read("a")
        assert got.dtype == bool and got.shape == expected.shape
        assert np.array_equal(got, expected)


@pytest.mark.parametrize("name", sorted(CASES))
def test_only_plain_grey_rgba_pngs_skip_the_reference_decode(tmp_path, monkeypatch, name):
    import app.masks.store as store_module

    data, path_kind = CASES[name]
    store = MaskStore.for_image(str(tmp_path / "a.png"))
    os.makedirs(store.directory)
    ext = ".jpg" if name == "legacy_jpg" else ".png"
    with open(os.path.join(store.directory, "a" + ext), "wb") as fh:
        fh.write(data)
    calls = []
    real = store_module._decode_reference
    monkeypatch.setattr(
        store_module, "_decode_reference", lambda *a, **k: calls.append(a) or real(*a, **k)
    )
    try:
        store.read("a")
    except MaskFileError:
        pass
    assert (calls == []) == (path_kind == "fast")
