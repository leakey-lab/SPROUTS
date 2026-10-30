"""The Library's scans name their files by the Library's stems without rebuilding tables.

``key_to_stems`` renames the keys of a scan (masks, skeletons, GT) to the Library's stems
(O7). It ran five times per folder load and per ``refresh_outputs`` (GUI thread) and built a
46k-entry stem-key table each time, also for an empty GT set; nearly every output is named
exactly as its image, so a key that is a stem now stays as it is and the table is built only
when some key is not (S16-01, S06-08). ``scan_image_folder`` maps the listed files' names only
when some file lost its stem to another (S06-08). The answers are those of the code before.

Qt-free.
"""

from __future__ import annotations

import os
import random

import pytest

from app import project_layout
from app.config import IMAGE_EXTENSIONS
from app.naming import stem_key
from app.project_layout import ImageScan, ProjectLayout, key_to_stems


def _key_to_stems_today(found, stems):
    """``key_to_stems`` before the short-cut (f392a85), verbatim: the reference."""
    by_key = {stem_key(stem): stem for stem in stems}
    return {by_key.get(stem_key(key), key): value for key, value in found.items()}


def _scan_image_folder_today(layout: ProjectLayout) -> ImageScan:
    """``ProjectLayout.scan_image_folder`` before the short-cut (f392a85), verbatim."""
    skips = project_layout._Skips()
    images = project_layout._scan_ranked(layout.root, IMAGE_EXTENSIONS, skips)
    winners = {stem_key(stem): os.path.basename(path) for stem, path in images.items()}
    return ImageScan(
        images=images,
        hidden=tuple(sorted((name, winners[key]) for key, name in skips.losers)),
        unsupported=tuple(sorted(skips.unsupported)),
    )


def test_keys_that_are_stems_are_copied_in_their_order():
    found = {"c": "x", "A": "y"}

    out = key_to_stems(found, {"B": 1, "A": 2, "c": 3})

    assert list(out.items()) == [("c", "x"), ("A", "y")]
    assert out is not found  # a new dict: the Library edits its masks in place


def test_a_case_variant_key_among_exact_ones_is_mapped():
    found = {"b": 1, "a": 2, "orphan": 3}

    assert list(key_to_stems(found, ["A", "b"]).items()) == [("b", 1), ("A", 2), ("orphan", 3)]
    assert list(key_to_stems(found, {"A", "b"}).items()) == [("b", 1), ("A", 2), ("orphan", 3)]


def test_an_empty_scan_gives_a_new_empty_dict():
    first = key_to_stems({}, {"a": 1})

    assert first == {} and key_to_stems({}, {"a": 1}) is not first


def test_keys_named_as_their_images_build_no_stem_key_table(monkeypatch):
    stems = {f"T{i:03d}_L{i % 7}": i for i in range(1000)}
    found = {stem: f"mask/{stem}.png" for stem in list(stems)[::7]}
    calls: list[str] = []
    real = project_layout.stem_key
    monkeypatch.setattr(project_layout, "stem_key", lambda s: calls.append(s) or real(s))

    assert key_to_stems(found, stems) == found
    assert key_to_stems(dict.fromkeys(found), set(stems)) == dict.fromkeys(found)
    assert key_to_stems({}, stems) == {}

    assert calls == []


_ALPHABET = ("a", "A", "b", "B", "ß", "ẞ", "Σ", "σ", "ς", "k", "K", "K", ".png", "_", "1")
_SHAPES = (dict.fromkeys, set, frozenset, list, tuple, iter)


@pytest.mark.parametrize("seed", range(4))
def test_the_remap_equals_todays_for_any_stems_shape(seed):
    """Random stems (one per stem key, as a scan lists them) and keys: exact stems, other
    cases, orphans; the result, its order too, is today's."""
    rng = random.Random(seed)
    for _ in range(1500):
        pool = ["".join(rng.choices(_ALPHABET, k=rng.randint(1, 3))) for _ in range(8)]
        by_key: dict[str, str] = {}
        for name in pool[: rng.randint(0, 8)]:
            by_key.setdefault(stem_key(name), name)
        stems = list(by_key.values())
        found = {}
        for _ in range(rng.randint(0, 6)):
            key = rng.choice([*stems, *pool, "orphan"])
            found[rng.choice((key, key.swapcase(), key.upper(), key.lower()))] = rng.random()
        shape = rng.choice(_SHAPES)

        got = key_to_stems(found, shape(stems))

        assert list(got.items()) == list(_key_to_stems_today(found, stems).items())


def test_a_folder_without_shared_stems_maps_no_file_name(tmp_path, monkeypatch):
    for name in ("a.png", "b.jpg", "c.JPEG", "t.tif"):
        (tmp_path / name).write_bytes(b"")
    layout = ProjectLayout(str(tmp_path))
    calls: list[str] = []
    real = os.path.basename

    with monkeypatch.context() as patched:
        patched.setattr(os.path, "basename", lambda path: calls.append(path) or real(path))
        scan = layout.scan_image_folder()

    assert sorted(scan.images) == ["a", "b", "c"]
    assert (scan.hidden, scan.unsupported) == ((), ("t.tif",))
    assert calls == []


def test_the_hidden_files_name_the_file_listed_instead(tmp_path):
    for name in ("a.png", "a.jpg", "a.jpeg", "b.JPG", "b.jpeg", "c.png", "d.TIF", "e.bmp"):
        (tmp_path / name).write_bytes(b"")
    layout = ProjectLayout(str(tmp_path))

    scan = layout.scan_image_folder()

    assert scan.hidden == (("a.jpeg", "a.png"), ("a.jpg", "a.png"), ("b.jpeg", "b.JPG"))
    assert scan == _scan_image_folder_today(layout)
    assert scan.unsupported == ("d.TIF", "e.bmp")
