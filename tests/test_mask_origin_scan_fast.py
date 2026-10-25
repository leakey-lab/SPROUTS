"""The pre-Generate mask-origin scan reads its headers on a few threads, same answers.

``scan_existing_masks`` reads the 26-byte PNG header of every mask in ``mask/`` on the
GUI thread before a Generate run (about 1.4 s for 46k masks). Most of it is opening
the files (about 18 us each on NTFS), not reading them: the scan reads the header
only, never pixel data. ``os.open`` and ``os.read`` release the GIL, so large
folders now read their headers on a small thread pool (about 0.6 s for 46k). The
call still returns only when every header is read, so the prompt and the job see
the whole result, as before.

The reference is the scan before this change, with the pre-R6 header reader, both
verbatim, on every kind of file a mask folder can hold.

The overwrite prompt (WG-T7) shows only how many masks exist, never a hand-corrected
count: "Replace all predictions" is the same button whatever the scan classified.
"""

from __future__ import annotations

import os
import threading

import cv2
import numpy as np
import pytest
from PIL import Image

from app.inference import mask_origin
from app.inference.mask_origin import ExistingMasks, MaskOrigin, scan_existing_masks
from app.project_layout import ProjectLayout

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- reference
def _reference_colour_type(path):
    """``mask_origin.png_colour_type`` before R6, verbatim."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(26)
    except OSError:
        return None
    if len(head) <= 25 or not head.startswith(_PNG_SIGNATURE):
        return None
    if head[12:16] != b"IHDR":
        return None
    return head[25]


def _reference_scan(layout, stems=None):
    """``scan_existing_masks`` before the threaded header reads, verbatim."""
    found = layout.scan_masks()
    if stems is not None:
        wanted = set(stems)
        found = {stem: path for stem, path in found.items() if stem in wanted}
    groups = {origin: set() for origin in MaskOrigin}
    for stem, path in found.items():
        colour_type = _reference_colour_type(path)
        if colour_type in (4, 6):
            origin = MaskOrigin.MANUAL
        elif colour_type == 0:
            origin = MaskOrigin.MODEL
        else:
            origin = MaskOrigin.UNKNOWN
        groups[origin].add(stem)
    return ExistingMasks(
        manual=frozenset(groups[MaskOrigin.MANUAL]),
        model=frozenset(groups[MaskOrigin.MODEL]),
        unknown=frozenset(groups[MaskOrigin.UNKNOWN]),
    )


# --------------------------------------------------------------------------- odd files
def _pil(mode):
    def write(path):
        if mode == "I;16":
            Image.fromarray(np.arange(48, dtype=np.uint16).reshape(6, 8)).save(path, "PNG")
        else:
            Image.new(mode, (8, 6)).save(path, "PNG")

    return write


def _cv2(array):
    def write(path):
        ok, data = cv2.imencode(".png", array)
        assert ok
        with open(path, "wb") as fh:  # cv2.imwrite cannot write non-ASCII paths on Windows
            fh.write(data.tobytes())

    return write


def _raw(data):
    def write(path):
        with open(path, "wb") as fh:
            fh.write(data)

    return write


_GREY = np.zeros((6, 8), np.uint8)
_IHDR = _PNG_SIGNATURE + b"\0\0\0\rIHDR"
_JPEG = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
ODD_MASKS = {
    "pil_grey.png": _pil("L"),
    "pil_grey_alpha.png": _pil("LA"),
    "pil_rgb.png": _pil("RGB"),
    "pil_rgba.png": _pil("RGBA"),
    "pil_palette.png": _pil("P"),
    "pil_grey16.png": _pil("I;16"),
    "cv2_grey.png": _cv2(_GREY),
    "cv2_bgra.png": _cv2(np.zeros((6, 8, 4), np.uint8)),
    "cv2_bgr.png": _cv2(np.zeros((6, 8, 3), np.uint8)),
    "cv2_grey16.png": _cv2(np.zeros((6, 8), np.uint16)),
    "cv2_bgra16.png": _cv2(np.zeros((6, 8, 4), np.uint16)),
    "jpeg_bytes.png": _raw(_JPEG),
    "legacy.jpg": _raw(_JPEG),
    "legacy_upper.JPEG": _raw(_JPEG),
    "grey_png_bytes.jpeg": _pil("L"),  # the header decides, not the extension
    "upper_case.PNG": _pil("RGBA"),
    "both.png": _pil("L"),
    "both.jpg": _raw(_JPEG),  # .png wins the stem clash
    "empty.png": _raw(b""),
    "signature_only.png": _raw(_PNG_SIGNATURE),
    "25_bytes.png": _raw(_IHDR + b"\0" * 9),
    "26_bytes_type_6.png": _raw(_IHDR + b"\0" * 9 + b"\6"),
    "26_bytes_type_0.png": _raw(_IHDR + b"\0" * 10),
    "26_bytes_type_4.png": _raw(_IHDR + b"\0" * 9 + b"\4"),
    "no_ihdr.png": _raw(_PNG_SIGNATURE + b"\0\0\0\rIDAT" + b"\6" * 20),
    "text.png": _raw(b"not a png at all, just text padding....."),
    "Données_根.png": _pil("RGBA"),
    "orphan.png": _pil("RGBA"),  # no image of this stem
    "notes.txt": _raw(b"not a mask"),
}


def _odd_project(tmp_path):
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    for name, write in ODD_MASKS.items():
        write(str(mask_dir / name))
    (mask_dir / "a_folder.png").mkdir()  # not a file: never a mask
    return ProjectLayout(str(tmp_path))


def _stems_with_images():
    stems = {ProjectLayout.stem(name) for name in ODD_MASKS}
    return (stems | {"a_folder", "no_mask_yet"}) - {"orphan"}


@pytest.fixture
def parallel_spy(monkeypatch):
    """Counts the thread pools the scan starts."""
    pools = []
    real_pool = mask_origin.ThreadPoolExecutor

    def spy(*args, **kwargs):
        pools.append(args or kwargs)
        return real_pool(*args, **kwargs)

    monkeypatch.setattr(mask_origin, "ThreadPoolExecutor", spy)
    return pools


@pytest.mark.parametrize("chunk", [None, 1, 2, 5])
@pytest.mark.parametrize("limit_to_images", [False, True])
def test_the_scan_matches_the_reference_on_every_kind_of_file(
    tmp_path, monkeypatch, parallel_spy, chunk, limit_to_images
):
    layout = _odd_project(tmp_path)
    if chunk is not None:  # small chunks: the odd files are read on the thread pool
        monkeypatch.setattr(mask_origin, "_SCAN_CHUNK", chunk)
    stems = _stems_with_images() if limit_to_images else None

    found = scan_existing_masks(layout, stems)

    assert found == _reference_scan(layout, stems)
    assert bool(parallel_spy) == (chunk is not None)
    expected_manual = {
        "pil_grey_alpha",
        "pil_rgba",
        "cv2_bgra",
        "cv2_bgra16",
        "upper_case",
        "26_bytes_type_6",
        "26_bytes_type_4",
        "Données_根",
    }
    assert found.manual == expected_manual | (set() if limit_to_images else {"orphan"})
    # Greyscale is colour type 0 at any bit depth; the .jpg loses the stem clash.
    assert found.model == {
        "pil_grey",
        "pil_grey16",
        "cv2_grey",
        "cv2_grey16",
        "grey_png_bytes",
        "both",
        "26_bytes_type_0",
    }
    assert {"legacy", "legacy_upper", "jpeg_bytes", "text", "empty"} <= found.unknown


def test_a_large_folder_is_read_on_the_thread_pool_with_the_same_answers(tmp_path, parallel_spy):
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    kinds = [
        cv2.imencode(".png", _GREY)[1].tobytes(),  # model
        cv2.imencode(".png", np.zeros((6, 8, 4), np.uint8))[1].tobytes(),  # hand-corrected
        cv2.imencode(".png", np.zeros((6, 8, 3), np.uint8))[1].tobytes(),  # unknown
        b"\x89PNG",  # truncated
    ]
    rng = np.random.default_rng(7)
    count = 3 * mask_origin._SCAN_CHUNK + 17
    for i, kind in enumerate(rng.integers(0, len(kinds), count)):
        (mask_dir / f"T{i:05d}.png").write_bytes(kinds[kind])
    layout = ProjectLayout(str(tmp_path))
    wanted = {f"T{i:05d}" for i in range(0, count, 3)}

    assert scan_existing_masks(layout) == _reference_scan(layout)
    assert scan_existing_masks(layout, wanted) == _reference_scan(layout, wanted)
    assert len(parallel_spy) == 2


def test_a_small_folder_needs_no_thread_pool(tmp_path, parallel_spy):
    layout = _odd_project(tmp_path)
    assert scan_existing_masks(layout) == _reference_scan(layout)
    assert parallel_spy == []


def test_the_scan_reads_only_the_png_header(tmp_path, monkeypatch):
    layout = _odd_project(tmp_path)
    monkeypatch.setattr(mask_origin, "_SCAN_CHUNK", 3)  # on the thread pool too
    asked = []
    real_read = os.read

    def spy_read(fd, n):
        asked.append(n)
        return real_read(fd, n)

    monkeypatch.setattr(mask_origin.os, "read", spy_read)
    found = scan_existing_masks(layout)

    assert asked and max(asked) <= 26
    # One read per mask; a file shorter than the header (but not empty) asks once
    # more for the rest and gets end of file.
    short = {"signature_only", "25_bytes"}
    assert len(asked) == found.count + len(short)


def test_an_unreadable_mask_is_unknown_and_the_rest_are_read(tmp_path, monkeypatch):
    layout = _odd_project(tmp_path)
    monkeypatch.setattr(mask_origin, "_SCAN_CHUNK", 2)
    real_open = os.open
    locked = os.path.normcase(str(tmp_path / "mask" / "pil_rgba.png"))
    opened_on = set()

    def open_or_deny(path, flags, *args):
        opened_on.add(threading.get_ident())
        if os.path.normcase(path) == locked:
            raise PermissionError(13, "Access is denied (simulated)")
        return real_open(path, flags, *args)

    monkeypatch.setattr(mask_origin.os, "open", open_or_deny)
    found = scan_existing_masks(layout)
    reference = _reference_scan(layout)

    assert "pil_rgba" in found.unknown
    assert found.manual == reference.manual - {"pil_rgba"}
    assert found.model == reference.model
    assert found.unknown == reference.unknown | {"pil_rgba"}
    assert threading.get_ident() not in opened_on  # the headers were read on the pool


def test_a_missing_mask_folder_is_empty(tmp_path, parallel_spy):
    assert scan_existing_masks(ProjectLayout(str(tmp_path))) == ExistingMasks.empty()
    assert parallel_spy == []


# --------------------------------------------------------------------------- prompt label
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _replace_button_text(existing_count):
    """The text of the prompt's destructive button (the prompt is cancelled)."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from app.handlers import mask_generation_handler as mgh

    texts = []

    def read_and_cancel():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(10, read_and_cancel)
            return
        role = QMessageBox.ButtonRole.DestructiveRole
        texts.extend(b.text() for b in box.buttons() if box.buttonRole(b) == role)
        box.escapeButton().click()

    QTimer.singleShot(0, read_and_cancel)
    assert mgh.ask_overwrite_policy(None, existing_count, 5000) is None
    [text] = texts
    return text


def test_the_replace_button_never_counts_hand_corrected_masks(qapp):
    # mask/ holds predictions only (WG-T7): the scan's manual / model split is not shown.
    none_by_hand = ExistingMasks(model=frozenset({"a", "b"}), unknown=frozenset({"c"}))
    assert _replace_button_text(none_by_hand.count) == "Replace all predictions"

    two_by_hand = ExistingMasks(manual=frozenset({"a", "b"}), model=frozenset({"c"}))
    assert _replace_button_text(two_by_hand.count) == "Replace all predictions"

    many = ExistingMasks(manual=frozenset(f"m{i}" for i in range(1234)))
    assert _replace_button_text(many.count) == "Replace all predictions"
