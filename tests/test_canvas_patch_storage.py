"""Patch storage of the raster editors' undo history (app/gui/patch_history.py).

A large 0/1 region (every mask edit) is stored as its packed bits, deflated: 8x less data to
compress on commit and to inflate on undo / redo, and at most one bit per pixel however noisy
the region is. Other regions (Trace GT class ids) are deflated byte for byte. Every form
gives back exactly the region it stored (values, shape and dtype).
"""

import numpy as np
import pytest

from app.gui.patch_history import COMPRESS_MIN_BYTES, Patch, PatchHistory

OVERHEAD = 128 + 2 * 64  # the entry overhead plus a deflate header / trailer per side


@pytest.mark.parametrize("shape", [(400, 400), (131, 257), (1, COMPRESS_MIN_BYTES + 3)])
def test_a_large_binary_region_is_stored_in_at_most_one_bit_per_pixel(shape):
    rng = np.random.default_rng(1)
    before = (rng.random(shape) < 0.5).astype(np.uint8)  # noise: deflate alone cannot shrink it
    after = 1 - before
    assert before.nbytes >= COMPRESS_MIN_BYTES

    patch = Patch.capture(2, 3, before, after)

    assert patch.nbytes <= 2 * -(-before.size // 8) + OVERHEAD
    for got, want in ((patch.before(), before), (patch.after(), after)):
        assert got.dtype == np.uint8 and got.shape == shape
        assert np.array_equal(got, want)


def test_a_large_region_of_class_ids_round_trips_exactly():
    rng = np.random.default_rng(2)
    before = rng.integers(0, 6, (300, 211), dtype=np.uint8)
    before[::7, ::5] = 255  # Ignore pixels
    after = np.where(before == 2, 1, before).astype(np.uint8)

    patch = Patch.capture(0, 0, before, after)

    assert np.array_equal(patch.before(), before) and np.array_equal(patch.after(), after)
    assert patch.before().dtype == np.uint8


def test_a_binary_region_of_another_dtype_round_trips_exactly():
    before = np.zeros((200, 300), np.uint16)
    before[50:150, 20:280] = 1
    patch = Patch.capture(0, 0, before, np.zeros_like(before))
    assert patch.before().dtype == np.uint16 and np.array_equal(patch.before(), before)
    assert not patch.after().any()


def test_undo_and_redo_of_a_whole_plane_mask_edit_restore_every_pixel():
    rng = np.random.default_rng(3)
    plane = (rng.random((1536, 2048)) < 0.4).astype(np.uint8)
    original = plane.copy()
    history = PatchHistory()
    history.reset(plane)
    after = original.copy()
    after[100:1400, 30:2000] ^= 1
    history.commit((0, 0, 2048, 1536), after)
    edited = history.plane.copy()

    assert history.undo() is not None and np.array_equal(history.plane, original)
    assert history.redo() is not None and np.array_equal(history.plane, edited)
