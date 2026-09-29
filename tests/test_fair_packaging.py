"""Packaging metadata (FAIR4RS F1.2/I2/R1; FR-09, FR-11).

``pyproject.toml`` must describe an installable, runnable distribution whose
version, dependencies and bundled files come from their single sources:

* the version is read from ``app.__version__`` (never duplicated);
* runtime dependencies mirror the tested pins in ``requirements.txt`` without
  torch/torchvision (installed per CUDA build, see README);
* every asset the app loads at run time is shipped as package data;
* the ``sprouts`` command points at a real ``main.main`` function.

The pyproject is expanded by setuptools itself (no wheel build); CI builds and
inspects the real wheel.
"""

from __future__ import annotations

import ast
import glob
import os
import sys
import warnings
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import app

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
# Installed per CUDA/CPU build (README "Installation"); pip must never pull them in.
_TORCH = {"torch", "torchvision"}
# Files the app opens at run time relative to the repo root (resources/resource_utils.py,
# app/gui/widgets/icons.py, app/gui/ui_panels.py, main.py) and the folder Dash serves
# (app/visualization/dash_app*.py -> app/assets, including vendored fonts and licences).
_RUNTIME_ASSET_GLOBS = (
    "resources/themes/**/*",
    "resources/icons/**/*",
    "resources/app_icon.*",
    "app/assets/**/*",
)


def _files(pattern: str) -> set[str]:
    """Files matching ``pattern`` with setuptools' package-data semantics (``**`` recurses)."""
    return {
        os.path.normcase(os.path.normpath(p))
        for p in glob.glob(pattern, recursive=True)
        if os.path.isfile(p) and "__pycache__" not in p
    }


def _raw_pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expanded() -> dict:
    """pyproject.toml as setuptools resolves it (dynamic version, package discovery)."""
    pyprojecttoml = pytest.importorskip("setuptools.config.pyprojecttoml")
    with warnings.catch_warnings():  # older setuptools flags [tool.setuptools] as "beta"
        warnings.filterwarnings("ignore", message=r".*\[tool\.setuptools\].*beta")
        return pyprojecttoml.read_configuration(str(ROOT / "pyproject.toml"), expand=True)


def _requirements(path: Path) -> list[Requirement]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Requirement(line.split("#", 1)[0]) for line in lines if line.split("#", 1)[0].strip()]


def test_version_is_read_from_app_package_not_duplicated(expanded):
    project = _raw_pyproject()["project"]
    assert "version" not in project, "static version would drift from app.__version__"
    assert "version" in project["dynamic"]
    assert expanded["project"]["version"] == app.__version__


def test_runtime_dependencies_mirror_requirements_txt_without_torch():
    declared = {
        canonicalize_name(req.name): req
        for req in (Requirement(d) for d in _raw_pyproject()["project"]["dependencies"])
    }
    pinned = {canonicalize_name(r.name): r for r in _requirements(ROOT / "requirements.txt")}

    assert not _TORCH & declared.keys(), "torch must stay a separate, CUDA-matched install"
    assert declared.keys() == pinned.keys()
    for name, pin in pinned.items():
        (spec,) = pin.specifier
        assert spec.operator == "==", f"requirements.txt must pin {name} exactly"
        assert declared[name].specifier.contains(
            spec.version
        ), f"{name} pin outside pyproject range"
        assert str(declared[name].marker) == str(pin.marker), f"{name} platform marker differs"


def test_packages_found_are_the_app_and_its_resources_only(expanded):
    packages = set(expanded["tool"]["setuptools"]["packages"])
    assert {"app", "app.gt", "app.gui", "app.inference", "app.mask_model", "resources"} <= packages
    assert not {p.split(".")[0] for p in packages} - {"app", "resources"}
    assert expanded["tool"]["setuptools"]["py-modules"] == ["main"]


def test_every_runtime_asset_is_shipped_as_package_data(expanded):
    package_data = expanded["tool"]["setuptools"]["package-data"]
    shipped = set().union(
        *(
            _files(str(ROOT / package.replace(".", "/") / pattern))
            for package, patterns in package_data.items()
            for pattern in patterns
        )
    )
    assets = sorted(set().union(*(_files(str(ROOT / p)) for p in _RUNTIME_ASSET_GLOBS)))
    assert assets, "asset globs matched nothing: update _RUNTIME_ASSET_GLOBS"
    missing = [os.path.relpath(a, ROOT) for a in assets if a not in shipped]
    assert missing == [], f"not in the wheel: {missing}"


def test_sprouts_command_points_at_a_main_function():
    scripts = _raw_pyproject()["project"]["scripts"]
    module, _, func = scripts["sprouts"].partition(":")
    tree = ast.parse((ROOT / f"{module}.py").read_text(encoding="utf-8"))
    top_level_functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert func in top_level_functions
