"""Foundation-core: app version constants and app.config (lazy, torch-free import)."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import types

import pytest

import app
from app import config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every Qt-free foundation module; none may pull in a heavy stack at import.
FOUNDATION_MODULES = (
    "app",
    "app.config",
    "app.project_layout",
    "app.naming",
    "app.io_utils",
    "app.user_paths",
    "app.provenance",
    "app.models",
    "app.calibration",
)
HEAVY_MODULES = ("torch", "torchvision", "cv2", "PyQt6", "dash", "pandas", "plotly")


def _run_python(code: str) -> str:
    """Run ``code`` in a fresh interpreter from the repo root; return stdout."""
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# ------------------------------------------------------------------ version


def test_version_is_semver_and_names_are_single_sourced():
    assert re.fullmatch(r"\d+\.\d+\.\d+", app.__version__)
    assert app.APP_NAME == "SPROUTS"
    assert app.APP_FULL_NAME == "SPROUTS – Root Mask and Skeletons"
    assert app.ORGANIZATION_NAME == "LeakeyLab"


def test_pyproject_version_matches_or_reads_app_version():
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        text = fh.read()
    static = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if static:
        assert static.group(1) == app.__version__
    else:  # dynamic = ["version"] + [tool.setuptools.dynamic] version = {attr = ...}
        assert re.search(r'version\s*=\s*\{\s*attr\s*=\s*"app\.__version__"\s*\}', text)


# ------------------------------------------------------------------ lazy imports


def test_foundation_modules_import_no_heavy_stack():
    code = (
        "import importlib, sys\n"
        f"for m in {FOUNDATION_MODULES!r}: importlib.import_module(m)\n"
        f"print(sorted(m for m in {HEAVY_MODULES!r} if m in sys.modules))\n"
    )
    assert _run_python(code) == "[]"


def test_skeleton_batch_size_import_is_lazy_but_still_available():
    # A stand-in torch (CPU only) keeps this hermetic: no GPU, no 1-2 s torch import.
    code = (
        "import sys, types\n"
        "import app.config as c\n"
        "before = 'torch' in sys.modules\n"
        "cuda = types.SimpleNamespace(is_available=lambda: False)\n"
        "sys.modules['torch'] = types.SimpleNamespace(cuda=cuda)\n"
        "from app.config import SKELETON_BATCH_SIZE\n"
        "print(before, SKELETON_BATCH_SIZE, c.SKELETON_BATCH_SIZE, c.get_skeleton_batch_size())\n"
    )
    assert _run_python(code) == "False 64 64 64"


def test_skeleton_batch_size_is_listed_by_dir():
    assert "SKELETON_BATCH_SIZE" in dir(config)


def test_unknown_config_attribute_still_raises():
    with pytest.raises(AttributeError):
        config.NOT_A_REAL_SETTING  # noqa: B018


# ------------------------------------------------------------------ batch sizing


def _fake_torch(*, available: bool, free_bytes: int = 0, raises: bool = False):
    def mem_get_info():
        if raises:
            raise RuntimeError("driver gone")
        return free_bytes, free_bytes * 2

    cuda = types.SimpleNamespace(is_available=lambda: available, mem_get_info=mem_get_info)
    return types.SimpleNamespace(cuda=cuda)


def test_auto_skeleton_batch_defaults_on_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(available=False))
    assert config._auto_skeleton_batch() == 64


def test_auto_skeleton_batch_scales_with_free_vram(monkeypatch):
    free = int(14.68 * 1024**3)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(available=True, free_bytes=free))
    expected = max(64, (int((free * 0.70) // (80 * 1024 * 1024)) // 8) * 8)
    result = config._auto_skeleton_batch()
    assert result == expected
    assert result % 8 == 0 and result > 64


def test_auto_skeleton_batch_never_below_default(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(available=True, free_bytes=10))
    assert config._auto_skeleton_batch() == 64


def test_auto_skeleton_batch_survives_query_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(available=True, raises=True))
    assert config._auto_skeleton_batch() == 64


def test_get_skeleton_batch_size_caches_and_refreshes(monkeypatch):
    calls = []

    def fake_auto(default=64):
        calls.append(default)
        return 96 + 8 * len(calls)

    monkeypatch.setattr(config, "_skeleton_batch", None)
    monkeypatch.setattr(config, "_auto_skeleton_batch", fake_auto)
    assert config.get_skeleton_batch_size() == 104
    assert config.get_skeleton_batch_size() == 104
    assert config.SKELETON_BATCH_SIZE == 104
    assert len(calls) == 1
    assert config.get_skeleton_batch_size(refresh=True) == 112
    assert config.SKELETON_BATCH_SIZE == 112
    assert len(calls) == 2


# ------------------------------------------------------------------ constants


def test_existing_contract_values_unchanged():
    assert config.CALIBRATION.fov_width_mm == 18.0
    assert config.CALIBRATION.fov_height_mm == 13.5
    assert config.LENGTH_CSV_HEADERS == [
        "Image",
        "Tube",
        "Position",
        "Date",
        "Time",
        "Length (mm)",
        "Error",
        "Source",  # WG-T8 (O1): gt / prediction, the last column
    ]
    assert config.AREA_CSV_HEADERS == [
        "Image",
        "Tube",
        "Position",
        "Date",
        "Time",
        "Area (mm²)",
        "Error",
        "Source",
    ]
    assert config.SOURCE_COLUMN == "Source"
    assert config.LENGTH_CSV_HEADERS[5] == config.LENGTH_VALUE_COLUMN
    assert config.AREA_CSV_HEADERS[5] == config.AREA_VALUE_COLUMN
    assert (config.MASK_BATCH_SIZE, config.MASK_THRESHOLD) == (16, 0.5)
    assert (config.DASH_LENGTH_PORT, config.DASH_AREA_PORT) == (8050, 8051)
    assert (config.ROOT_LENGTHS_CSV, config.ROOT_AREAS_CSV) == (
        "root_lengths.csv",
        "root_areas.csv",
    )


def test_project_layout_constants():
    assert config.MASK_DIRNAME == "mask"
    assert config.SKELETONS_DIRNAME == "skeletons"
    assert not hasattr(config, "SKELETON_CORRECTIONS_DIRNAME")  # retired (WG-T3, W5-T5)
    assert config.IMAGE_EXTENSIONS == (".png", ".jpg", ".jpeg")
    assert config.DASH_HOST == "127.0.0.1"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.png", True),
        ("B.JPG", True),
        ("c.Jpeg", True),
        ("d.tif", False),
        ("root_lengths.csv", False),
        ("x.png.provenance.json", False),
        (".x.png.1a2b.tmp", False),
        ("no_extension", False),
    ],
)
def test_is_image_file_matches_library_scanner(name, expected):
    assert config.is_image_file(name) is expected


def _module_constant(path: str, name: str):
    """Evaluate a simple module-level constant expression from ``path`` without importing it."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            expr = ast.Expression(node.value)
            return eval(compile(expr, path, "eval"), {"__builtins__": {}})  # noqa: S307
    raise AssertionError(f"{name} not found in {path}")


def test_skeleton_output_size_matches_skeletonizer_geometry():
    # skeleton_inference.save_image resizes the CROP_SIZE square output to
    # (int(h / ASPECT_RATIO), w) for ASPECT_RATIO < 1 -> (width, height).
    path = os.path.join(REPO_ROOT, "app", "inference", "skeleton_inference.py")
    crop = _module_constant(path, "CROP_SIZE")
    aspect = _module_constant(path, "ASPECT_RATIO")
    assert aspect < 1.0
    assert config.SKELETON_OUTPUT_SIZE == (int(crop / aspect), crop) == (341, 256)
