"""``io_utils.imdecode_bytes``: the one decoder of file bytes (Measure, the GT check, masks).

Measure (``metrics``), the GT validity check (``measure_sources``) and the mask readers
(``masks.store``, Correct's ``mask_files``) must see the pixels ``imread_unicode`` sees (O10).
They decode the bytes they read once through ``imdecode_bytes``, which ``imread_unicode``
calls too, so the agreement is structural. The reference below is the decode as it was
written before, with its guards: empty -> None, ``cv2.error`` -> None.
"""

from __future__ import annotations

import ast
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.inference import measure_sources, metrics
from app.io_utils import imdecode_bytes, imread_unicode
from app.masks import store

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"
FLAGS = {
    "default": None,
    "color": cv2.IMREAD_COLOR,
    "grayscale": cv2.IMREAD_GRAYSCALE,
    "unchanged": cv2.IMREAD_UNCHANGED,
    "anydepth": cv2.IMREAD_ANYDEPTH,
}


def _reference(data: bytes, flags: int) -> np.ndarray | None:
    """The decode before the shared helper, verbatim."""
    if not data:
        return None
    try:
        return cv2.imdecode(np.frombuffer(data, np.uint8), flags)
    except cv2.error:
        return None


def _encoded(img: np.ndarray, ext: str) -> bytes:
    ok, buf = cv2.imencode(ext, img)
    assert ok
    return buf.tobytes()


def _cases() -> dict[str, bytes]:
    rng = np.random.default_rng(11)
    grey = rng.integers(0, 256, (37, 53), dtype=np.uint8)
    colour = rng.integers(0, 256, (29, 41, 3), dtype=np.uint8)
    rgba = rng.integers(0, 256, (23, 31, 4), dtype=np.uint8)
    grey16 = rng.integers(0, 1 << 16, (17, 19), dtype=np.uint16)
    png = _encoded(colour, ".png")
    cases = {
        "grey_png": _encoded(grey, ".png"),
        "colour_png": png,
        "rgba_png": _encoded(rgba, ".png"),
        "grey16_png": _encoded(grey16, ".png"),
        "jpeg": _encoded(colour, ".jpg"),
        "bmp": _encoded(colour, ".bmp"),
        "editor_mask": store.encode_mask_png(grey > 127),
        "empty": b"",
        "one_byte": b"\x89",
        "png_signature_only": png[:8],
        "truncated_png": png[: len(png) // 2],
        "garbage": rng.integers(0, 256, 300, dtype=np.uint8).tobytes(),
    }
    for path in sorted(EXAMPLES.rglob("*.jpg")) + sorted(EXAMPLES.rglob("*.png")):
        cases[f"example_{path.parent.name}_{path.name}"] = path.read_bytes()
    return cases


CASES = _cases()


def _same(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b)


@pytest.mark.parametrize("flag_name", sorted(FLAGS))
@pytest.mark.parametrize("name", sorted(CASES))
def test_every_reader_decodes_as_the_reference(tmp_path, name, flag_name):
    data = CASES[name]
    flags = FLAGS[flag_name]
    expected = _reference(data, cv2.IMREAD_COLOR if flags is None else flags)
    path = tmp_path / "f.bin"
    path.write_bytes(data)

    assert _same(imread_unicode(str(path), flags), expected)
    if flags is not None:
        as_array = np.fromfile(str(path), np.uint8)
        for given in (data, bytearray(data), memoryview(data), as_array):
            assert _same(imdecode_bytes(given, flags), expected)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_grey_readers_of_measure_and_the_masks_decode_alike(tmp_path, name):
    data = CASES[name]
    expected = _reference(data, cv2.IMREAD_GRAYSCALE)
    path = tmp_path / "f.png"
    path.write_bytes(data)

    assert _same(store._decode_reference(data), expected)
    assert _same(metrics._read_grey(str(path), data), expected)
    assert _same(metrics._read_grey(str(path), None), expected)
    on = store.read_mask_file(str(path))
    assert _same(on, None if expected is None else expected > store.MASK_THRESHOLD)


def test_grayscale_is_flag_zero_and_is_not_taken_for_no_flag():
    data = CASES["colour_png"]
    assert cv2.IMREAD_GRAYSCALE == 0
    grey = imdecode_bytes(data, cv2.IMREAD_GRAYSCALE)
    assert grey is not None and grey.ndim == 2


def test_empty_or_undecodable_data_gives_none_and_never_raises():
    for data in (b"", bytearray(), memoryview(b""), np.zeros(0, np.uint8), b"\x00" * 64):
        assert imdecode_bytes(data, cv2.IMREAD_GRAYSCALE) is None


def test_a_missing_path_or_a_folder_reads_as_none(tmp_path):
    assert imread_unicode(str(tmp_path / "missing.png")) is None
    assert imread_unicode(str(tmp_path)) is None


def test_the_flags_have_no_default():
    with pytest.raises(TypeError):
        imdecode_bytes(CASES["grey_png"])  # type: ignore[call-arg]


def _calls_cv2_imdecode(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "imdecode"
        and isinstance(node.value, ast.Name)
        and node.value.id == "cv2"
    ]


def test_the_measure_and_mask_readers_have_no_decoder_of_their_own():
    readers = (
        ROOT / "app" / "inference" / "metrics.py",
        ROOT / "app" / "inference" / "measure_sources.py",
        ROOT / "app" / "masks" / "store.py",
        ROOT / "app" / "gui" / "correct" / "mask_files.py",
    )
    assert {str(p.relative_to(ROOT)): _calls_cv2_imdecode(p) for p in readers} == {
        str(p.relative_to(ROOT)): [] for p in readers
    }
    assert _calls_cv2_imdecode(ROOT / "app" / "io_utils.py") != []  # the one home


@pytest.mark.parametrize("name", ["grey_png", "editor_mask", "truncated_png", "empty"])
def test_a_gt_file_is_judged_on_the_reference_decode(tmp_path, name):
    data = CASES[name]
    path = tmp_path / "a.png"
    path.write_bytes(data)
    expected = _reference(data, cv2.IMREAD_GRAYSCALE)
    photo = tmp_path / "photo.png"
    shape = (37, 53) if expected is None else expected.shape
    photo.write_bytes(_encoded(np.zeros(shape, np.uint8), ".png"))

    decision = measure_sources.decide_area_source("a", None, str(path), str(photo))

    assert decision.reason == ("unreadable" if expected is None else "")
    assert (decision.data is None) == (expected is None)
