"""Foundation-core: one image-name parser for the library, the CSVs and the dashboards.

``_legacy_metrics`` and ``_legacy_image_manager`` are verbatim copies of the two
parsers this module replaces (app/inference/metrics.py and
app/gui/image_manager.py before consolidation). On canonical names the new
parser must agree with both; it may differ only where they were wrong (FR-15).
"""

from __future__ import annotations

import dataclasses
import os
import posixpath
import re

import pytest

from app.naming import ImageName, image_stem, parse_image_name

REAL_DATA = r"D:/2024 EF image data"


def _legacy_metrics(name: str) -> dict:
    info = {
        "original_name": name,
        "tube_number": None,
        "length_position": None,
        "date": None,
        "time": None,
    }
    if m := re.search(r"T(\d+)", name):
        info["tube_number"] = int(m.group(1))
    if m := re.search(r"L(\d+)", name):
        info["length_position"] = int(m.group(1))
    if m := re.search(r"(\d{4})\.(\d{2})\.(\d{2})", name):
        y, mo, d = m.groups()
        info["date"] = f"{y}.{mo}.{d}"
    if m := re.search(r"_(\d{6})(?:_|$)", name):
        t = m.group(1)
        info["time"] = f"{t[:2]}:{t[2:4]}:{t[4:]}"
    return info


def _legacy_image_manager_field(name: str) -> str:
    return re.sub(r"cam\d+$", "", name.split("_", 1)[0], flags=re.IGNORECASE)


def _as_legacy(parsed: ImageName) -> tuple:
    return (parsed.tube, parsed.position, parsed.date, parsed.time)


def _legacy_tuple(name: str) -> tuple:
    info = _legacy_metrics(name)
    return (info["tube_number"], info["length_position"], info["date"], info["time"])


# Canonical-style names: real camera output plus plausible variants.
CANONICAL = [
    "24EFCAM1_T001_L011_2024.07.18_111618_001_SMM",
    "24EFCAM2_T088_L100_2024.08.27_100639_001_SMM",
    "24EFCAM3_T156_L106_2024.08.08_103401_002_SCW",
    "24EFcam2_T169_L009_2024.08.27_101242_002_SMM",
    "SoyFACE_T012_L003_2023.06.01_090000_001_SMM",
    "EF_T7_L2_2022.12.31_235959",
    "Field_T0100_L0001_2024.01.02_000001_x",
    "scan_T005_L030_2024.03.15_143022_fake",
    "24EFCAM1_T001_L045_2024.07.18_111618_001_SMM_fake",
    "Plot_T3_L4_2024.05.06_070809_extra_tokens_here",
    "A_T1_L1_1999.01.01_010101_1",
    "24EFCAM10_T200_L150_2025.10.10_101010_003_SMM",
]


@pytest.mark.parametrize("name", CANONICAL)
def test_canonical_names_match_both_legacy_parsers(name):
    parsed = parse_image_name(name)
    assert _as_legacy(parsed) == _legacy_tuple(name)
    assert parsed.field == _legacy_image_manager_field(name)
    assert parsed.stem == name


def test_full_record():
    parsed = parse_image_name("24EFCAM1_T001_L045_2024.07.18_111618_001_SMM")
    assert parsed == ImageName(
        stem="24EFCAM1_T001_L045_2024.07.18_111618_001_SMM",
        field="24EF",
        tube=1,
        position=45,
        date="2024.07.18",
        time="11:16:18",
    )
    assert parsed.is_complete


def test_file_names_and_paths_are_parsed_by_stem():
    parsed = parse_image_name("D:/data/scan_T005_L030_2024.03.15_143022_fake.png")
    assert parsed.stem == "scan_T005_L030_2024.03.15_143022_fake"
    assert (parsed.tube, parsed.position, parsed.date, parsed.time) == (
        5,
        30,
        "2024.03.15",
        "14:30:22",
    )
    # The time token right before the extension is found (the legacy regex,
    # run on "..._111618.jpg", missed it).
    assert parse_image_name("EF_T1_L2_2024.01.01_111618.jpg").time == "11:16:18"


def test_windows_paths_split_on_every_os(monkeypatch):
    # Force POSIX path semantics so this fails on Windows too if the parser leans on os.path.
    monkeypatch.setattr(os.path, "basename", posixpath.basename)
    monkeypatch.setattr(os.path, "normpath", posixpath.normpath)
    assert image_stem("C:\\data\\run 1\\24EFCAM1_T001_L044.png") == "24EFCAM1_T001_L044"
    assert image_stem("/data/T001_L044.png") == "T001_L044"
    assert image_stem("C:\\data\\run 1\\a.jpg\\") == "a"


def test_image_name_is_frozen():
    parsed = parse_image_name("EF_T1_L2_2024.01.01_111618")
    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.tube = 3


@pytest.mark.parametrize(
    ("name", "tube", "position"),
    [
        # FR-15: the legacy parsers took the first T/L digits anywhere.
        ("PLOT12_T001_L005_2024.07.18_111618_001_SMM", 1, 5),  # legacy tube 12
        ("EXPT2_T014_L033_2024.07.18_111618_001_SMM", 14, 33),  # legacy tube 2
        ("CAL1_T001_L011_2024.07.18_111618_001_SMM", 1, 11),  # legacy position 1
        ("X_T2EXP_T014_L033_2024.07.18_111618", 14, 33),  # T2EXP is a word, not a token
        ("EF-T003-L004-2024.07.18", 3, 4),  # other delimiters still delimit tokens
    ],
)
def test_fr15_tokens_must_stand_alone(name, tube, position):
    parsed = parse_image_name(name)
    assert (parsed.tube, parsed.position) == (tube, position)


@pytest.mark.parametrize("name", ["IMG_0001", "nothing_here.png", "", "T001L045", "PLOT12"])
def test_unparseable_names_give_none_not_guesses(name):
    parsed = parse_image_name(name)
    assert parsed.tube is None and parsed.position is None
    assert not parsed.is_complete


def test_missing_parts_are_none():
    parsed = parse_image_name("nothing_here.png")
    assert (parsed.date, parsed.time) == (None, None)
    assert parsed.field == "nothing"
    assert parse_image_name("").field is None


def test_field_keeps_image_manager_rules():
    assert parse_image_name("xyzCAM12_T1_L1").field == "xyz"
    assert parse_image_name("nocam_T1").field == "nocam"
    assert parse_image_name("camera").field == "camera"
    assert parse_image_name("single").field == "single"


@pytest.mark.skipif(not os.path.isdir(REAL_DATA), reason="real dataset not available")
def test_real_dataset_names_parse_like_legacy():
    with os.scandir(REAL_DATA) as entries:
        stems = [
            os.path.splitext(e.name)[0]
            for e in entries
            if e.is_file() and e.name.lower().endswith((".png", ".jpg", ".jpeg"))
        ]
    assert len(stems) > 40_000
    diffs = [
        s
        for s in stems
        if _as_legacy(p := parse_image_name(s)) != _legacy_tuple(s)
        or p.field != _legacy_image_manager_field(s)
    ]
    assert diffs == []


TRICKY = [
    "T5_T001_L045_2024.07.18_111618_001",  # field is itself a tube token
    "L5_T001_L045_2024.07.18_111618_001",  # field is itself a position token
    "T5cam1_T001_L045_2024.07.18_111618",  # camera tag after a T-looking field
    "camcam1_T001_L045_2024.07.18_111618",
    "cam1_T001_L045_2024.07.18_111618",  # field is only a camera tag
    "EF-2_T001_L045_2024.07.18_111618",  # non-alphanumeric field
    "EF_T001_L045_12024.07.18_111618",  # date inside a longer number
    "EF_T001_L045_2024.07.18_1116180_x",  # 7-digit time token
    "EF_T001_L045_2024.07.18_111618",  # time at the end
    "EF_T001_L045_2024.07.18",  # no time
    "EF_T001_L045",
]


@pytest.mark.parametrize("name", CANONICAL + TRICKY)
def test_fast_path_agrees_with_general_parser(name):
    from app.naming import _parse_tokens, image_stem

    assert parse_image_name(name) == _parse_tokens(image_stem(name))
