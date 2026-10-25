"""The Windows bundle can be rebuilt from the repository (FAIR4RS A1/R1; FR-13).

``scripts/build_support.py`` (imported by ``scripts/build_executable.spec``) is
tested directly. The spec itself is executed against recording stand-ins for
PyInstaller's ``Analysis``/``PYZ``/``EXE``/``COLLECT``, so no bundle is built.
The build scripts are run from a throw-away copy with a stub interpreter.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


@pytest.fixture(scope="module")
def bs():
    """scripts/build_support.py loaded by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("build_support", SCRIPTS / "build_support.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_required_tree(bs, root: Path) -> None:
    """Placeholder files/folders for every required bundle source under ``root``."""
    for source, _dest in bs.REQUIRED_DATAS:
        path = root / source
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "placeholder.txt").write_bytes(b"x")


# ------------------------------------------------------------------ datas


def test_repo_datas_exist_and_include_theme_icons_and_dash_assets(bs):
    datas, _warnings = bs.resolve_datas(ROOT)
    sources = {Path(src).resolve() for src, _dest in datas}
    assert all(Path(src).exists() for src, _dest in datas)
    for folder in ("resources/themes", "resources/icons", "app/assets"):
        assert (ROOT / folder).resolve() in sources, folder


def test_missing_required_source_stops_the_build_naming_it(bs, tmp_path):
    _make_required_tree(bs, tmp_path)
    shutil.rmtree(tmp_path / "resources" / "icons")
    with pytest.raises(bs.BuildConfigError, match="resources.icons"):
        bs.resolve_datas(tmp_path)


def test_missing_model_weights_warn_and_are_left_out(bs, tmp_path):
    _make_required_tree(bs, tmp_path)
    datas, warnings = bs.resolve_datas(tmp_path)
    assert len(warnings) == len(bs.MODEL_DATAS) == 2
    assert not [src for src, _dest in datas if "checkpoints" in src]


def test_present_model_weights_are_bundled_at_their_runtime_path(bs, tmp_path):
    _make_required_tree(bs, tmp_path)
    weights = (
        "checkpoints/mask_weights/best_mask_model_V5.pth",
        "checkpoints/skeletonizer/latest_net_G.pth",
    )
    for rel in weights:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"weights")
    datas, warnings = bs.resolve_datas(tmp_path)
    assert warnings == []
    bundled = {Path(src).relative_to(tmp_path).as_posix(): dest for src, dest in datas}
    assert bundled[weights[0]] == "checkpoints/mask_weights"
    assert bundled[weights[1]] == "checkpoints/skeletonizer"


# ------------------------------------------------------------- version info


@pytest.mark.parametrize(
    "version,expected",
    [
        ("3.0.0", (3, 0, 0, 0)),
        ("3.1", (3, 1, 0, 0)),
        ("3.1.0rc1", (3, 1, 0, 0)),
        ("10.2.3.4", (10, 2, 3, 4)),
    ],
)
def test_version_tuple_is_four_integers_for_windows(bs, version, expected):
    assert bs.version_tuple(version) == expected


def test_windows_version_resource_carries_name_and_version(bs):
    pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    info = bs.windows_version_info()
    ffi = info.ffi
    assert (ffi.fileVersionMS, ffi.fileVersionLS) == (3 << 16 | 0, 0 << 16 | 0)
    strings = {s.name: s.val for table in info.kids[0].kids for s in table.kids}
    assert strings["ProductName"] == app.APP_NAME
    assert strings["ProductVersion"] == strings["FileVersion"] == app.__version__
    assert strings["OriginalFilename"] == f"{app.APP_NAME}.exe"


# --------------------------------------------------------------- build_info


def test_build_info_records_version_commit_and_zoned_time(bs, tmp_path):
    info = bs.collect_build_info(ROOT)
    assert info["name"] == app.APP_NAME and info["version"] == app.__version__
    assert dt.datetime.fromisoformat(info["built_at"]).tzinfo is not None
    assert info["python"] == ".".join(map(str, sys.version_info[:3]))
    path = bs.write_build_info(tmp_path / "nested" / "build_info.json", info)
    assert json.loads(path.read_text(encoding="utf-8")) == info


def test_frozen_app_provenance_reads_the_commit_from_build_info(bs, tmp_path):
    """A frozen app has no .git: app.provenance must find the commit in build_info.json."""
    provenance = pytest.importorskip("app.provenance")
    info = bs.collect_build_info(ROOT)
    if info["git_commit"] is None:
        pytest.skip("not a git checkout")
    frozen_root = tmp_path / "_internal"  # sys._MEIPASS of a one-folder bundle
    bs.write_build_info(frozen_root / bs.BUILD_INFO_FILENAME, info)
    assert provenance.git_commit(str(frozen_root)) == info["git_commit"]


def test_build_info_without_git_has_no_commit(bs, monkeypatch):
    def _no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(bs.subprocess, "run", _no_git)
    info = bs.collect_build_info(ROOT)
    assert info["git_commit"] is None and info["git_dirty"] is None


# --------------------------------------------------------------------- spec


class _Recorder:
    """Stand-in for a PyInstaller build target: records its arguments."""

    calls: dict = {}

    def __init__(self, *args, **kwargs):
        type(self).calls[type(self).__name__] = (args, kwargs)
        self.pure = self.zipped_data = self.scripts = []
        self.binaries = self.zipfiles = self.datas = []


def test_spec_wires_repo_paths_icon_version_and_build_info(bs, tmp_path, monkeypatch):
    pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    monkeypatch.setattr(sys, "path", list(sys.path))  # the spec prepends scripts/ and the root
    _Recorder.calls = {}
    namespace = {
        "__file__": str(SCRIPTS / "build_executable.spec"),
        "SPECPATH": str(SCRIPTS),
        "workpath": str(tmp_path / "work"),
        "DISTPATH": str(tmp_path / "dist"),
        **{name: type(name, (_Recorder,), {}) for name in ("Analysis", "PYZ", "EXE", "COLLECT")},
    }
    source = (SCRIPTS / "build_executable.spec").read_text(encoding="utf-8")
    try:
        exec(compile(source, "build_executable.spec", "exec"), namespace)
    finally:
        sys.modules.pop("build_support", None)

    (scripts,), analysis = _Recorder.calls["Analysis"]
    assert [Path(s) for s in scripts] == [ROOT / "main.py"]
    datas = analysis["datas"]
    assert all(Path(src).exists() for src, _dest in datas)
    assert (str(tmp_path / "work" / "build_info.json"), ".") in datas
    assert "app.gt.registry" in analysis["hiddenimports"]

    _args, exe = _Recorder.calls["EXE"]
    assert exe["name"] == app.APP_NAME
    assert Path(exe["icon"]).is_file()
    strings = {s.name: s.val for table in exe["version"].kids[0].kids for s in table.kids}
    assert strings["FileVersion"] == app.__version__
    assert _Recorder.calls["COLLECT"][1]["name"] == app.APP_NAME


def test_spec_and_helpers_are_not_git_ignored():
    """A fresh clone must contain what it takes to rebuild the bundle."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    paths = ["scripts/build_executable.spec", "scripts/build_support.py"]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *paths], cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode == 128:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")
    assert result.stdout.split() == [], "ignored by .gitignore"


# ------------------------------------------------------------ build scripts

_STUB_OK_VERSION = '@if "%2"=="PyInstaller" if "%3"=="--version" exit /b 0\r\n@exit /b 1\r\n'


@pytest.mark.skipif(sys.platform != "win32", reason="Windows build scripts")
@pytest.mark.parametrize("script", ["build.bat", "build.ps1"])
def test_build_script_exits_nonzero_when_pyinstaller_fails(bs, tmp_path, script):
    """A failed PyInstaller run must end the script with an error, never 'success'."""
    _make_required_tree(bs, tmp_path)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    shutil.copy2(SCRIPTS / script, tmp_path / "scripts" / script)
    stub = tmp_path / "fake_python.bat"  # PyInstaller present, every build fails
    stub.write_text(_STUB_OK_VERSION, encoding="ascii")
    env = {**os.environ, "SPROUTS_BUILD_PYTHON": str(stub), "SPROUTS_BUILD_NO_PAUSE": "1"}
    if script.endswith(".ps1"):
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        cmd += [str(tmp_path / "scripts" / script), "-AllowMissingModels"]
    else:
        cmd = ["cmd", "/c", str(tmp_path / "scripts" / script), "/allow-missing-models"]
    # cwd is the throw-away copy too: nothing a script does may reach the real repository.
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0
    assert "successfully" not in result.stdout.lower()
    assert "failed" in (result.stdout + result.stderr).lower()


def test_licence_files_are_bundled_at_the_bundle_root(bs):
    datas = dict(bs.REQUIRED_DATAS)
    assert datas["LICENSE"] == "." and datas["THIRD_PARTY_NOTICES.md"] == "."
    resolved, _warnings = bs.resolve_datas(ROOT)
    assert {Path(s).name for s, d in resolved if d == "."} >= {"LICENSE", "THIRD_PARTY_NOTICES.md"}
