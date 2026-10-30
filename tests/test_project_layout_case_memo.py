"""A case-variant lookup lists a case-sensitive folder once while the folder is unchanged.

Where the file system keeps case (Linux), a lookup that misses the exact name lists the
folder for a name that differs in case only (O7). The stem keys of that listing are kept per
folder while its ``(st_dev, st_ino, st_mtime_ns)`` stays the same, so the next miss in a
46k-file ``skeletons/`` costs one ``os.stat`` instead of a listing (P2-01). A folder changed in
the last 2 s is never remembered (git's racy-timestamp rule), so every test ages its folders.

Qt-free. The case-sensitive path is simulated on every disk (``_ignores_case`` False).
"""

from __future__ import annotations

import itertools
import os
import random
import time

import pytest

from app import project_layout
from app.naming import stem_key
from app.project_layout import MASK_EXTENSIONS, SKELETON_SUFFIX, ProjectLayout, find_by_stem

_AGES = itertools.count()


@pytest.fixture(autouse=True)
def case_sensitive(monkeypatch):
    project_layout._LISTED_KEYS.clear()
    monkeypatch.setattr(project_layout, "_ignores_case", lambda _directory: False)
    yield
    project_layout._LISTED_KEYS.clear()


def _age(folder: str) -> int:
    """Set ``folder``'s time a minute back, to a value no other call gives: two folder states
    never share a time, as on a disk where nothing restores an old time."""
    ns = time.time_ns() - 60_000_000_000 - next(_AGES) * 1000
    os.utime(folder, ns=(ns, ns))
    return ns


def _listings(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = os.scandir
    monkeypatch.setattr(os, "scandir", lambda path: seen.append(path) or real(path))
    return seen


def _touch(path) -> str:
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "ab").close()
    return path


def _case_variant(folder: str, stem: str, endings=MASK_EXTENSIONS) -> str | None:
    return project_layout._find_without_case(folder, stem, endings, fold_endings=True)


def _listed_today(directory, stem, endings, fold_endings):
    """``_find_without_case`` before the memo (f392a85), verbatim: the reference."""
    key = stem_key(stem)
    rank = {(e.lower() if fold_endings else e): i for i, e in enumerate(endings)}
    best = None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                for ending, r in rank.items():
                    tail = name[-len(ending) :]
                    if (tail.lower() if fold_endings else tail) != ending:
                        continue
                    if stem_key(name[: -len(ending)]) != key or not entry.is_file():
                        continue
                    if best is None or (r, name) < best[:2]:
                        best = (r, name, entry.path)
    except FileNotFoundError, NotADirectoryError:
        return None
    return best[2] if best is not None else None


def test_misses_in_an_unchanged_folder_list_it_once(tmp_path, monkeypatch):
    folder = os.path.dirname(_touch(tmp_path / "skeletons" / "other_fake.png"))
    _age(folder)
    seen = _listings(monkeypatch)

    assert find_by_stem(folder, "a", SKELETON_SUFFIX) is None
    assert find_by_stem(folder, "b", SKELETON_SUFFIX) is None
    assert find_by_stem(folder, "a", SKELETON_SUFFIX) is None

    assert len(seen) == 1


def test_the_layout_finders_share_the_memo_of_their_folder(tmp_path, monkeypatch):
    layout = ProjectLayout(str(tmp_path))
    _touch(os.path.join(layout.gt_mask_dir, "other.png"))
    _touch(os.path.join(layout.mask_dir, "other.png"))
    _age(layout.gt_mask_dir)
    _age(layout.mask_dir)
    seen = _listings(monkeypatch)

    for stem in ("a", "b", "c"):
        assert layout.find_gt_mask(stem) is None and layout.find_mask(stem) is None

    assert len(seen) == 2  # one listing per folder


def test_a_case_variant_added_after_a_miss_is_found(tmp_path):
    folder = str(tmp_path / "skeletons")
    os.makedirs(folder)
    _age(folder)
    assert find_by_stem(folder, "A", SKELETON_SUFFIX) is None

    added = _touch(os.path.join(folder, "a_fake.png"))  # the folder changed: listed again

    found = find_by_stem(folder, "A", SKELETON_SUFFIX)
    assert found is not None and os.path.normcase(found) == os.path.normcase(added)


def test_a_removed_case_variant_is_not_returned(tmp_path):
    path = _touch(tmp_path / "gt" / "mask" / "a.png")
    folder = os.path.dirname(path)
    _touch(os.path.join(folder, "other.png"))
    _age(folder)
    assert _case_variant(folder, "zz", (".png",)) is None  # remembers a, other
    assert _case_variant(folder, "A", (".png",)) == path

    os.remove(path)

    assert _case_variant(folder, "A", (".png",)) is None


def test_a_folder_changed_just_now_is_listed_on_every_miss(tmp_path, monkeypatch):
    folder = os.path.dirname(_touch(tmp_path / "skeletons" / "other_fake.png"))
    seen = _listings(monkeypatch)

    assert find_by_stem(folder, "a", SKELETON_SUFFIX) is None
    assert find_by_stem(folder, "a", SKELETON_SUFFIX) is None

    assert len(seen) == 2 and project_layout._LISTED_KEYS == {}


def test_a_name_with_the_key_is_listed_again(tmp_path, monkeypatch):
    path = _touch(tmp_path / "mask" / "a.PNG")
    folder = os.path.dirname(path)
    _age(folder)
    seen = _listings(monkeypatch)

    assert _case_variant(folder, "A") == _case_variant(folder, "A") == path

    assert len(seen) == 2  # the file test and the ranking stay those of a fresh listing


def test_a_folder_named_like_a_match_is_listed_again_and_never_found(tmp_path, monkeypatch):
    folder = str(tmp_path / "skeletons")
    os.makedirs(os.path.join(folder, "a_fake.png"))  # a folder, not a skeleton
    _age(folder)
    seen = _listings(monkeypatch)

    assert find_by_stem(folder, "A", SKELETON_SUFFIX) is None
    assert find_by_stem(folder, "A", SKELETON_SUFFIX) is None

    assert len(seen) == 2


def test_each_ending_shape_has_its_own_memo(tmp_path):
    path = _touch(tmp_path / "f" / "a.png")
    folder = os.path.dirname(path)
    _age(folder)

    assert find_by_stem(folder, "A_x", SKELETON_SUFFIX) is None  # remembers no "a" there

    assert _case_variant(folder, "A", (".png",)) == path
    assert _case_variant(folder, "A") == path


def test_a_stem_ending_in_capital_sigma_is_found_as_today(tmp_path):
    path = _touch(tmp_path / "gt" / "ΟΔΟΣ.PNG")
    folder = os.path.dirname(path)
    _age(folder)

    assert _case_variant(folder, "zz", (".png",)) is None  # the memo is built
    assert _case_variant(folder, "οδοΣ", (".png",)) == path
    assert _case_variant(folder, "ΟΔΟΣ", (".png",)) == path


def test_a_missing_folder_or_a_file_is_not_remembered(tmp_path):
    a_file = _touch(tmp_path / "skeletons")
    _age(a_file)

    assert find_by_stem(str(tmp_path / "missing"), "a", SKELETON_SUFFIX) is None
    assert find_by_stem(a_file, "a", SKELETON_SUFFIX) is None

    assert project_layout._LISTED_KEYS == {}


def test_a_folder_replaced_by_a_copy_with_its_time_is_listed_again(tmp_path):
    folder = str(tmp_path / "gt")
    _touch(os.path.join(folder, "other.png"))
    old = _age(folder)
    assert _case_variant(folder, "A", (".png",)) is None

    os.rename(folder, str(tmp_path / "gt_old"))  # e.g. restored with ``cp -a``
    copied = _touch(os.path.join(folder, "a.png"))
    os.utime(folder, ns=(old, old))

    assert _case_variant(folder, "A", (".png",)) == copied


def test_a_case_insensitive_folder_is_neither_stated_nor_listed(tmp_path, monkeypatch):
    folder = str(tmp_path / "skeletons")
    os.makedirs(folder)
    monkeypatch.setattr(project_layout, "_ignores_case", lambda _directory: True)

    def refused(*_args, **_kwargs):
        raise AssertionError("a case-insensitive folder was read")

    with monkeypatch.context() as patched:
        patched.setattr(os, "stat", refused)
        patched.setattr(os, "scandir", refused)
        found = project_layout._find_without_case(folder, "a", (".png",), fold_endings=True)

    assert found is None


def test_the_memo_holds_at_most_eight_folders(tmp_path):
    for i in range(20):
        folder = str(tmp_path / f"f{i}")
        os.makedirs(folder)
        _age(folder)
        assert find_by_stem(folder, "a", SKELETON_SUFFIX) is None
        assert 0 < len(project_layout._LISTED_KEYS) <= project_layout._LISTED_MAX


_SHAPES = (((SKELETON_SUFFIX,), False), (MASK_EXTENSIONS, True), ((".png",), True))
_STEMS = ("a", "A", "b", "B1", "b1", "Σa", "σA", "ΟΔΟΣ", "οδος", "x.png", "X.PNG", "")
_ENDINGS = ("_fake.png", "_FAKE.png", ".png", ".PNG", ".jpg", ".JPEG", ".jpeg", ".tif", "")


def _random_file(rng: random.Random, folder: str) -> str:
    name = ""
    while not name:
        name = rng.choice(_STEMS) + rng.choice(_ENDINGS)
    return _touch(os.path.join(folder, name))


@pytest.mark.parametrize("seed", range(3))
def test_answers_equal_a_fresh_listing(tmp_path, seed):
    """Random lookups, files added and removed, the folder settled or just changed: the
    remembered answer is always the one a fresh listing gives."""
    rng = random.Random(seed)
    folder = str(tmp_path / "f")
    os.makedirs(os.path.join(folder, "d_fake.png"))  # a folder posing as a match of "D"
    for _ in range(12):
        _random_file(rng, folder)
    _age(folder)
    compared = 0
    for _ in range(300):
        roll = rng.random()
        if roll < 0.08:
            _random_file(rng, folder)
        elif roll < 0.14:
            names = [e.name for e in os.scandir(folder) if e.is_file()]
            if names:
                os.remove(os.path.join(folder, rng.choice(names)))
        if roll < 0.14 and rng.random() < 0.7:
            _age(folder)  # settled again; else it stays "changed just now"
        stem = rng.choice((*_STEMS, "D"))
        endings, fold = rng.choice(_SHAPES)
        expected = _listed_today(folder, stem, endings, fold)
        found = project_layout._find_without_case(folder, stem, endings, fold_endings=fold)
        assert found == expected, (stem, endings, fold, sorted(os.listdir(folder)))
        compared += 1
    assert compared == 300 and project_layout._LISTED_KEYS  # the memo was in use
