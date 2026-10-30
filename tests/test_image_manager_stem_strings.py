"""The Library's output sets hold the Library's own stem strings, not a second copy.

A folder scan makes a new string for every file name. ``ImageManager.masks`` and
``skeleton_stems`` are keyed by the Library's stems (O7), and their keys are the very
strings of ``ImageManager.images``: the scans' copies are freed, about 8 MiB for the masks
and skeletons of 46k images. Only a scan of a few stems (under an eighth of the images, as
GT usually is) keeps its own strings. The answers (and the order of a remapped dict) are
those of the code before (f392a85), the pre-Generate mask count's too.
"""

from __future__ import annotations

import os
import random

import pytest

from app.gui import image_manager as im
from app.inference.mask_origin import scan_existing_masks
from app.naming import stem_key
from app.project_layout import (
    SKELETON_SUFFIX,
    ProjectLayout,
    few_keys,
    key_to_stems,
)

# Long names: CPython shares one object for each one-letter string, so those prove nothing.
STEMS = [
    f"24EFCAM1_T{tube:03d}_L{depth:03d}_2024.06.04_100001_001_SMM"
    for tube in (1, 2)
    for depth in range(1, 13)
]
ORPHAN = "24EFCAM1_T009_L001_2024.06.04_100001_001_SMM"  # an output of a deleted photo


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()


def _project(root, *, skeletons=tuple(STEMS)) -> ProjectLayout:
    """``STEMS`` as photos with a mask each; a skeleton for each of ``skeletons``."""
    layout = ProjectLayout(str(root))
    for stem in STEMS:
        _touch(os.path.join(layout.root, stem + ".jpg"))
        _touch(os.path.join(layout.mask_dir, stem + ".png"))
    for stem in skeletons:
        _touch(os.path.join(layout.skeletons_dir, stem + SKELETON_SUFFIX))
    return layout


def _ids(strings) -> set[int]:
    return {id(s) for s in strings}


def _fresh(text: str) -> str:
    """An equal string that is another object, as a second scan makes it."""
    return "".join(list(text))


def test_a_load_keys_masks_and_skeletons_by_the_librarys_strings(app, tmp_path):
    _project(tmp_path, skeletons=STEMS[:-3])
    thread = im.ImageLoaderThread(str(tmp_path))
    delivered = []
    thread.finished.connect(lambda *args: delivered.append(args))

    thread.run()  # the loader's body, on this thread

    [(images, _fake_images, masks, _pairs)] = delivered
    assert sorted(images) == sorted(STEMS)
    assert set(masks) == set(STEMS)
    assert thread.skeleton_stems == set(STEMS[:-3])
    library = _ids(images)
    assert _ids(masks) <= library
    assert _ids(thread.skeleton_stems) <= library


def test_refresh_outputs_keys_them_by_the_librarys_strings(app, tmp_path):
    # A skeleton named in another case belongs to its photo (O7); an orphan keeps its name.
    layout = _project(tmp_path, skeletons=[STEMS[0].lower(), *STEMS[1:], ORPHAN])
    manager = im.ImageManager(None)
    manager.original_folder = layout.root
    manager.images = layout.scan_images()

    manager.refresh_outputs()

    library = _ids(manager.images)
    assert set(manager.masks) == set(STEMS) and _ids(manager.masks) <= library
    assert manager.skeleton_stems == {*STEMS, ORPHAN}
    assert {s for s in manager.skeleton_stems if id(s) not in library} == {ORPHAN}


@pytest.mark.parametrize("shape", [dict.fromkeys, set])
def test_key_to_stems_gives_a_large_scan_the_strings_of_stems_in_its_own_order(shape):
    stems = shape(map(_fresh, STEMS))
    found = {_fresh(stem): f"mask/{stem}.png" for stem in reversed(STEMS[2:])}

    out = key_to_stems(found, stems)

    assert list(out.items()) == list(found.items())
    assert _ids(out) <= _ids(stems)


@pytest.mark.parametrize("n, few", [(2, True), (3, False)])  # 24 images: an eighth is 3
def test_both_remaps_keep_their_own_strings_below_the_same_size(n, few):
    images = dict.fromkeys(map(_fresh, STEMS))
    found = [_fresh(stem) for stem in STEMS[:n]]

    remapped = key_to_stems(dict.fromkeys(found), images)
    stems = im._stems_of(set(found), images)

    assert few_keys(found, images) is few
    for out in (remapped, stems):
        assert set(out) == set(found)
        assert _ids(out) <= (_ids(found) if few else _ids(images))


def _key_to_stems_before(found, stems):
    """``key_to_stems`` of f392a85, verbatim: the reference."""
    by_key = {stem_key(stem): stem for stem in stems}
    return {by_key.get(stem_key(key), key): value for key, value in found.items()}


class _MaskScan:
    """The one layout method :func:`scan_existing_masks` calls."""

    def __init__(self, masks: dict) -> None:
        self._masks = masks

    def scan_masks(self) -> dict:
        return dict(self._masks)


_ALPHABET = ("a", "A", "b", "ß", "ẞ", "Σ", "σ", "ς", "k", "K", "K", "_", "1")


@pytest.mark.parametrize("seed", range(3))
def test_the_answers_are_those_of_before_for_few_and_many_outputs(seed):
    """Random Libraries (one stem per stem key, as a scan lists them) of up to 40 stems and
    scans of a few or of most of them: exact stems, other cases, orphans."""
    rng = random.Random(seed)
    for _ in range(600):
        pool = ["".join(rng.choices(_ALPHABET, k=rng.randint(1, 4))) for _ in range(40)]
        by_key: dict[str, str] = {}
        for name in pool[: rng.randint(0, 40)]:
            by_key.setdefault(stem_key(name), name)
        stems = list(by_key.values())
        exact = rng.random() < 0.5  # every output named as its image (nearly always)
        found = {}
        for _ in range(rng.choice((rng.randint(0, 3), rng.randint(0, len(stems) + 3)))):
            if stems and (exact or rng.random() < 0.7):
                key = rng.choice(stems)
            else:
                key = rng.choice([*pool, "orphan"])
            if not exact:
                key = rng.choice((key, key.swapcase(), key.upper(), key.lower()))
            found[key] = rng.random()
        images = dict.fromkeys(stems)
        before = _key_to_stems_before(found, stems)

        assert list(key_to_stems(found, images).items()) == list(before.items())
        for kind in (set, frozenset):
            assert im._stems_of(kind(found), images) == set(
                _key_to_stems_before(dict.fromkeys(kind(found)), stems)
            )
        wanted = set(stems)
        assert scan_existing_masks(_MaskScan(found), stems).stems == frozenset(
            s for s in _key_to_stems_before(found, wanted) if s in wanted
        )
