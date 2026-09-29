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
