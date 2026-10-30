"""Citation and release metadata stay in step with the code (FAIR4RS F1.2, F4, R1.1; FR-10, FR-01).

``CITATION.cff`` (GitHub "Cite this repository", Zenodo), ``codemeta.json`` and
``pyproject.toml`` describe the same software; ``CHANGELOG.md`` records every
release. Each copy of a fact is checked against its single source so a version
bump, a new author or a license decision cannot leave one file behind.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

import app

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")


@pytest.fixture(scope="module")
def cff() -> dict:
    return yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def codemeta() -> dict:
    return json.loads((ROOT / "codemeta.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_citation_cff_has_the_fields_github_needs_to_cite_it(cff):
    assert cff["cff-version"] == "1.2.0"
    assert cff["type"] == "software"
    assert cff["message"].strip() and cff["title"].strip()
    assert cff["authors"], "at least one author"
    for author in cff["authors"]:
        assert author.get("family-names") or author.get("name"), author


def test_the_three_authors_are_in_the_agreed_order(cff):
    assert [a["family-names"] for a in cff["authors"]] == ["Parth", "Varela", "Leakey"]
    assert cff["title"] == "SPROUTS"


def test_readme_cites_the_current_version_and_every_author(cff):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## How to cite", 1)[1].split("\n## ", 1)[0]
    match = re.search(r"version (\d+\.\d+\.\d+)", section)
    assert match and match.group(1) == app.__version__
    for author in cff["authors"]:
        assert author["family-names"] in section


def test_orcids_have_the_registry_form_when_present(cff):
    for author in cff["authors"]:
        if "orcid" in author:
            assert re.fullmatch(r"https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]", author["orcid"])


def test_version_is_the_package_version_everywhere(cff, codemeta):
    assert cff["version"] == codemeta["version"] == app.__version__


def test_repository_and_name_agree_across_metadata_files(cff, codemeta, project):
    repository = project["urls"]["Repository"]
    assert cff["repository-code"] == codemeta["codeRepository"] == repository
    assert codemeta["name"] == cff["title"]
    assert codemeta["issueTracker"] == project["urls"]["Issues"]


def test_authors_and_keywords_agree_across_metadata_files(cff, codemeta, project):
    cff_names = [f"{a['given-names']} {a['family-names']}" for a in cff["authors"]]
    codemeta_names = [f"{a['givenName']} {a['familyName']}" for a in codemeta["author"]]
    assert cff_names == codemeta_names == [a["name"] for a in project["authors"]]
    assert cff["keywords"] == codemeta["keywords"] == project["keywords"]


def test_license_is_declared_everywhere_or_nowhere(cff, codemeta, project):
    """The license is pending (README "License"). When a LICENSE file lands, every
    metadata file must carry the same SPDX id; until then none may claim one."""
    has_license_file = any((ROOT / name).is_file() for name in _LICENSE_FILES)
    declared = {
        "CITATION.cff": cff.get("license"),
        "codemeta.json": codemeta.get("license", "").rsplit("/", 1)[-1] or None,
        "pyproject.toml": project.get("license"),
    }
    if has_license_file:
        assert len(set(declared.values())) == 1 and None not in declared.values(), declared
    else:
        assert set(declared.values()) == {None}, declared


def test_changelog_top_release_is_the_package_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = re.findall(r"^## \[([^\]]+)\]", text, flags=re.MULTILINE)
    assert sections[:2] == ["Unreleased", app.__version__]


def test_the_pix2pix_bsd_notice_is_reproduced():
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for needle in (
        "Copyright (c) 2017, Jun-Yan Zhu and Taesung Park",
        "Copyright (c) 2016, Phillip Isola and Jun-Yan Zhu",
        "Copyright (c) 2015, Facebook, Inc.",
        "Redistributions of source code must retain the above copyright notice",
    ):
        assert needle in text
    # The derived module names its origin and points at the notice.
    source = (ROOT / "app" / "inference" / "skeleton_inference.py").read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    assert "THIRD_PARTY_NOTICES.md" in docstring and "pix2pix" in docstring
