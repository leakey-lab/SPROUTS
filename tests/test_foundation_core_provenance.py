"""Foundation-core: provenance records and sidecars (FAIR4RS R1.2)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import types

import pytest

import app
from app import config, provenance
from app.provenance import (
    SCHEMA,
    app_info,
    build_record,
    calibration_info,
    file_sha256,
    git_commit,
    portable_path,
    runtime_info,
    sidecar_path,
    write_sidecar,
)

SHA_A = "a" * 40
SHA_B = "b" * 40


# ------------------------------------------------------------------ record


def test_build_record_shape():
    record = build_record(
        "root_length",
        inputs={"folder": "skeletons", "n_images": 3},
        parameters={"batch_size": 64},
        outputs={"csv": "root_lengths.csv", "n_rows": 3},
    )
    assert record["schema"] == SCHEMA == "sprouts-provenance/1"
    assert record["kind"] == "root_length"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["created_utc"])
    created = dt.datetime.strptime(record["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    assert abs((now - created).total_seconds()) < 120
    assert record["app"] == app_info()
    assert record["app"]["version"] == app.__version__
    assert record["app"]["name"] == "SPROUTS"
    assert "git_commit" in record
    assert (
        record["calibration"]
        == calibration_info()
        == {"fov_width_mm": 18.0, "fov_height_mm": 13.5, "source": "default"}
    )
    assert record["runtime"]["python"]
    assert record["inputs"] == {"folder": "skeletons", "n_images": 3}
    assert record["parameters"] == {"batch_size": 64}
    assert record["outputs"] == {"csv": "root_lengths.csv", "n_rows": 3}
    assert record["models"] == {}
    json.dumps(record)  # JSON-ready


def test_models_are_identified_by_hash(tmp_path, monkeypatch):
    ckpt = tmp_path / "weights.pth"
    ckpt.write_bytes(b"weights")
    monkeypatch.setattr(provenance, "PROJECT_ROOT", str(tmp_path))
    record = build_record("mask", models={"mask": str(ckpt), "gone": str(tmp_path / "no.pth")})
    assert record["models"]["mask"] == {
        "path": "weights.pth",
        "sha256": hashlib.sha256(b"weights").hexdigest(),
        "size": 7,
    }
    assert record["models"]["gone"] == {"path": "no.pth", "sha256": None, "size": None}


# ------------------------------------------------------------------ hashing


def test_file_sha256_streams_and_memoizes(tmp_path, monkeypatch):
    path = tmp_path / "big.bin"
    data = os.urandom(3 * 1024 * 1024 + 17)
    path.write_bytes(data)
    calls = []
    real = provenance._hash_file

    def counting(p):
        calls.append(p)
        return real(p)

    monkeypatch.setattr(provenance, "_hash_file", counting)
    provenance._sha256_cache.clear()
    assert file_sha256(path) == hashlib.sha256(data).hexdigest()
    assert file_sha256(str(path)) == hashlib.sha256(data).hexdigest()
    assert len(calls) == 1
    path.write_bytes(data + b"!")  # size changes -> re-hash
    assert file_sha256(path) == hashlib.sha256(data + b"!").hexdigest()
    assert len(calls) == 2


def test_file_sha256_of_missing_file_is_none(tmp_path):
    assert file_sha256(tmp_path / "missing.pth") is None
    assert file_sha256(tmp_path) is None


# ------------------------------------------------------------------ git


def _fake_repo(root: pathlib.Path) -> pathlib.Path:
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    return git


def test_git_commit_from_loose_ref(tmp_path):
    git = _fake_repo(tmp_path)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(SHA_A + "\n")
    assert git_commit(str(tmp_path)) == SHA_A


def test_git_commit_from_packed_refs(tmp_path):
    git = _fake_repo(tmp_path)
    (git / "HEAD").write_text("ref: refs/heads/feat/x\n")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{SHA_B} refs/heads/feat/x\n^{SHA_A}\n{SHA_A} refs/tags/v1\n"
    )
    assert git_commit(str(tmp_path)) == SHA_B


def test_git_commit_detached_head(tmp_path):
    git = _fake_repo(tmp_path)
    (git / "HEAD").write_text(SHA_A + "\n")
    assert git_commit(str(tmp_path)) == SHA_A


def test_git_commit_in_a_worktree(tmp_path):
    main_git = _fake_repo(tmp_path / "main")
    (main_git / "refs" / "heads" / "topic").write_text(SHA_B + "\n")
    wt_git = main_git / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/topic\n")
    (wt_git / "commondir").write_text("../..\n")
    checkout = tmp_path / "wt"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {wt_git}\n")
    assert git_commit(str(checkout)) == SHA_B


def test_git_commit_without_repo_uses_build_info_or_none(tmp_path):
    assert git_commit(str(tmp_path)) is None
    (tmp_path / "build_info.json").write_text(json.dumps({"git_commit": SHA_A}))
    assert git_commit(str(tmp_path)) == SHA_A


def test_git_commit_of_unborn_branch_is_none(tmp_path):
    git = _fake_repo(tmp_path)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    assert git_commit(str(tmp_path)) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_commit_matches_git_rev_parse_for_this_checkout():
    proc = subprocess.run(
        ["git", "-C", config.PROJECT_ROOT, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    assert git_commit(config.PROJECT_ROOT) == proc.stdout.strip()


# ------------------------------------------------------------------ runtime


def test_runtime_info_reports_only_already_imported_packages(monkeypatch):
    monkeypatch.delitem(sys.modules, "dash", raising=False)
    monkeypatch.setitem(sys.modules, "pandas", types.SimpleNamespace(__version__="9.8.7"))
    info = runtime_info()
    assert info["python"].count(".") == 2
    assert info["platform"]
    assert "dash" not in info["packages"]
    assert info["packages"]["pandas"] == "9.8.7"


def test_runtime_info_records_torch_precision_flags(monkeypatch):
    fake_torch = types.SimpleNamespace(
        __version__="2.9.0+cu128",
        version=types.SimpleNamespace(cuda="12.8"),
        backends=types.SimpleNamespace(
            cudnn=types.SimpleNamespace(
                version=lambda: 91002, allow_tf32=True, benchmark=True, deterministic=False
            ),
            cuda=types.SimpleNamespace(matmul=types.SimpleNamespace(allow_tf32=True)),
        ),
        get_float32_matmul_precision=lambda: "high",
        cuda=types.SimpleNamespace(is_initialized=lambda: False),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.delitem(sys.modules, "app.inference.runtime", raising=False)
    torch_info = runtime_info()["torch"]
    assert torch_info == {
        "version": "2.9.0+cu128",
        "cuda": "12.8",
        "cudnn": 91002,
        "cudnn_allow_tf32": True,
        "matmul_allow_tf32": True,
        "float32_matmul_precision": "high",
        "cudnn_benchmark": True,
        "cudnn_deterministic": False,
    }


def test_runtime_info_survives_a_partial_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(__version__="0.0"))
    assert runtime_info()["torch"]["version"] == "0.0"


def test_runtime_info_reads_a_resolved_inference_runtime(monkeypatch):
    import dataclasses
    import functools

    @dataclasses.dataclass(frozen=True)
    class FakeRuntime:
        device: str = "cuda"
        device_name: str = "Fake GPU"
        amp_dtype: object = None
        compute_capability: tuple = (12, 0)

    @functools.lru_cache(maxsize=1)
    def get_runtime():
        return FakeRuntime()

    fake_module = types.SimpleNamespace(get_runtime=get_runtime)
    monkeypatch.setitem(sys.modules, "app.inference.runtime", fake_module)
    assert "inference" not in runtime_info()  # never resolves the runtime itself
    get_runtime()
    assert runtime_info()["inference"] == {
        "device": "cuda",
        "device_name": "Fake GPU",
        "amp_dtype": None,
        "compute_capability": [12, 0],
    }


# ------------------------------------------------------------------ sidecars


def test_sidecar_round_trip_is_atomic_and_scanner_safe(tmp_path):
    output = tmp_path / "root_lengths.csv"
    output.write_text("Image\n", encoding="utf-8")
    before = output.read_bytes()
    record = build_record(
        "root_length",
        inputs={"folder": pathlib.Path("skeletons"), "stems": {"b", "a"}, "size": (341, 256)},
    )
    written = write_sidecar(output, record)
    assert written == sidecar_path(output) == str(output) + ".provenance.json"
    assert output.read_bytes() == before
    loaded = json.loads(pathlib.Path(written).read_text(encoding="utf-8"))
    assert loaded["inputs"] == {"folder": "skeletons", "stems": ["a", "b"], "size": [341, 256]}
    assert not config.is_image_file(os.path.basename(written))
    assert sorted(os.listdir(tmp_path)) == ["root_lengths.csv", "root_lengths.csv.provenance.json"]


def test_portable_path_never_leaks_folders_outside_root(tmp_path):
    root = tmp_path / "project"
    inside = root / "mask" / "a.png"
    assert portable_path(inside, root) == "mask/a.png"
    assert portable_path(tmp_path / "Users" / "someone" / "x.png", root) == "x.png"
    assert portable_path("relative/name.png", root) == "name.png"


def test_run_manifest_lives_inside_the_output_folder(tmp_path):
    record = build_record("mask_generation", parameters={"mask_threshold": 0.5})
    path = provenance.write_run_manifest(tmp_path, record)
    assert path == provenance.run_manifest_path(tmp_path) == str(tmp_path / "_sprouts_run.json")
    assert json.loads(pathlib.Path(path).read_text(encoding="utf-8"))["kind"] == "mask_generation"
    assert not config.is_image_file(os.path.basename(path))


def test_names_digest_identifies_an_input_set_regardless_of_order():
    a = provenance.names_digest(["b.jpg", "a.jpg", "c.jpg"])
    assert a == provenance.names_digest(("c.jpg", "a.jpg", "b.jpg"))
    assert a == hashlib.sha256(b"a.jpg\nb.jpg\nc.jpg").hexdigest()
    assert a != provenance.names_digest(["a.jpg", "b.jpg"])
