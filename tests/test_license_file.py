"""The licence decision (O32): GPL-3.0-or-later code, CC-BY-4.0 weights, one SPDX id."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

import app
from app.models import MODELS

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
# FSF gpl-3.0.txt (674 lines, 35,149 bytes), fetched 2026-10-25.
GPL3_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"


def test_license_is_gpl3_text_under_the_projects_copyright_line():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")  # universal newlines
    assert "Copyright (c) 2026 the SPROUTS authors" in text
    marker = "                    GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007"
    body = text[text.index(marker) :]
    assert len(body.splitlines()) == 674
    assert hashlib.sha256(body.encode()).hexdigest() == GPL3_SHA256


def test_spdx_constant_is_the_one_in_every_metadata_file():
    assert app.LICENSE_SPDX == "GPL-3.0-or-later"
    assert app.COPYRIGHT_HOLDER == "the SPROUTS authors"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    cff = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    codemeta = json.loads((ROOT / "codemeta.json").read_text(encoding="utf-8"))
    assert project["license"] == app.LICENSE_SPDX
    assert cff["license"] == app.LICENSE_SPDX
    assert codemeta["license"].rsplit("/", 1)[-1] == app.LICENSE_SPDX


def test_every_registered_checkpoint_is_cc_by_4():
    assert MODELS
    assert {spec.license for spec in MODELS.values()} == {"CC-BY-4.0"}
