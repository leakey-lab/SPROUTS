"""Foundation-core: model-weights registry and identity check (FAIR4RS F1.1 / R1)."""

from __future__ import annotations

import hashlib
import logging
import os
import re

import pytest

from app import config, models, provenance
from app.models import MODELS, ModelSpec, ModelStatus, verify, verify_all

MASK_SHA = "ca1f92d21609f9905bd407bd0562f8456cfff3987d1493c701a5585ee3a3a43f"
SKELETON_SHA = "a312586ce086aa913b6823f1c99c064f9189d71f77bd575a6529ee9f6a0d2daa"


def _spec(tmp_path, data: bytes | None, sha: str) -> ModelSpec:
    if data is not None:
        (tmp_path / "w.pth").write_bytes(data)
    return ModelSpec(key="test", relpath="w.pth", sha256=sha, description="test weights")


def test_registry_matches_the_paths_the_loaders_use():
    assert set(MODELS) == {"mask", "skeleton"}
    assert MODELS["mask"].path() == os.path.normpath(config.MASK_WEIGHTS_PATH)
    assert MODELS["skeleton"].path() == os.path.normpath(
        os.path.join(config.SKELETONIZER_DIR, "latest_net_G.pth")
    )
    assert config.SKELETON_WEIGHTS_PATH == os.path.join(config.SKELETONIZER_DIR, "latest_net_G.pth")
    assert (MODELS["mask"].sha256, MODELS["skeleton"].sha256) == (MASK_SHA, SKELETON_SHA)
    for spec in MODELS.values():
        assert spec.key and spec.description
        assert "\\" not in spec.relpath


def test_verify_ok(tmp_path):
    spec = _spec(tmp_path, b"weights", hashlib.sha256(b"weights").hexdigest())
    status = verify(spec, root=str(tmp_path))
    assert status == ModelStatus(
        state="ok", path=str(tmp_path / "w.pth"), sha256=hashlib.sha256(b"weights").hexdigest()
    )
    assert status.ok


def test_verify_mismatch_is_a_warning_not_an_error(tmp_path, caplog):
    spec = _spec(tmp_path, b"other weights", "0" * 64)
    with caplog.at_level(logging.WARNING, logger="app.models"):
        status = verify(spec, root=str(tmp_path))
        verify(spec, root=str(tmp_path))
    assert status.state == "mismatch" and not status.ok
    assert status.sha256 == hashlib.sha256(b"other weights").hexdigest()
    assert len([r for r in caplog.records if "do not match" in r.getMessage()]) == 1


def test_verify_missing(tmp_path):
    status = verify(_spec(tmp_path, None, "0" * 64), root=str(tmp_path))
    assert status == ModelStatus(state="missing", path=str(tmp_path / "w.pth"), sha256=None)


def test_verify_never_raises(tmp_path, monkeypatch):
    spec = _spec(tmp_path, b"x", "0" * 64)

    def boom(path):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(models, "file_sha256", boom)
    monkeypatch.setattr(models, "_status_cache", {})
    assert verify(spec, root=str(tmp_path)).state == "missing"


def test_verify_is_cached_by_size_and_mtime(tmp_path, monkeypatch):
    spec = _spec(tmp_path, b"weights", hashlib.sha256(b"weights").hexdigest())
    calls = []
    real = provenance._hash_file

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(provenance, "_hash_file", counting)
    provenance._sha256_cache.clear()
    assert verify(spec, root=str(tmp_path)).ok
    assert verify(spec, root=str(tmp_path)).ok
    assert len(calls) == 1
    (tmp_path / "w.pth").write_bytes(b"weights, retrained")
    assert verify(spec, root=str(tmp_path)).state == "mismatch"
    assert len(calls) == 2


def test_verify_all_covers_the_registry(monkeypatch, tmp_path):
    statuses = verify_all(root=str(tmp_path))
    assert set(statuses) == set(MODELS)
    assert all(s.state == "missing" for s in statuses.values())


@pytest.mark.skipif(
    not (os.path.isfile(config.MASK_WEIGHTS_PATH) and os.path.isfile(config.SKELETON_WEIGHTS_PATH)),
    reason="model checkpoints not present (they are not committed)",
)
def test_shipped_checkpoints_verify():
    statuses = verify_all()
    assert statuses["mask"].state == "ok"
    assert statuses["skeleton"].state == "ok"


def test_model_url_and_doi_are_well_formed_when_set():
    for spec in MODELS.values():
        assert spec.doi is None or re.fullmatch(r"10\.\d{4,9}/\S+", spec.doi), spec.key
        assert spec.url is None or spec.url.startswith("https://"), spec.key
