"""scripts/fetch_models.py downloads a checkpoint and keeps only a verified file (O33).

No internet: a local ``http.server`` serves the bytes. The real checkpoints are never
downloaded and ``checkpoints/`` is never touched (every test works under ``tmp_path``).
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import importlib.util
import threading
from pathlib import Path

import pytest

from app.models import ModelSpec

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_models.py"
GOOD = b"good weights" * 1000
BAD = b"corrupted weights" * 1000


@pytest.fixture(scope="module")
def fm():
    """scripts/fetch_models.py loaded by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("fetch_models", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Handler(http.server.SimpleHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802 - http.server API
        type(self).hits += 1
        super().do_GET()

    def log_message(self, *args):  # keep the test output pristine
        pass


@pytest.fixture
def server(tmp_path):
    """Serve ``tmp_path/srv`` on 127.0.0.1; yields (base_url, srv_dir, hit_counter)."""
    srv = tmp_path / "srv"
    srv.mkdir()

    class Handler(_Handler):
        hits = 0

    handler = functools.partial(Handler, directory=str(srv))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", srv, Handler
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _spec(data: bytes = GOOD) -> ModelSpec:
    return ModelSpec(
        key="t",
        relpath="checkpoints/t.pth",
        sha256=hashlib.sha256(data).hexdigest(),
        url=None,
    )


def _partials(root: Path) -> list[Path]:
    return list(root.rglob("*.part"))


def test_a_good_download_verifies_and_leaves_no_partial_file(fm, server, tmp_path):
    base, srv, _ = server
    (srv / "t.pth").write_bytes(GOOD)
    root = tmp_path / "root"
    status = fm.fetch(_spec(), base + "/t.pth", str(root))
    assert status.ok
    assert (root / "checkpoints" / "t.pth").read_bytes() == GOOD
    assert _partials(root) == []


def test_a_corrupted_download_is_refused_removed_and_keeps_an_existing_good_file(
    fm, server, tmp_path
):
    base, srv, _ = server
    (srv / "t.pth").write_bytes(BAD)
    root = tmp_path / "root"
    final = root / "checkpoints" / "t.pth"
    final.parent.mkdir(parents=True)

    status = fm.fetch(_spec(), base + "/t.pth", str(root))
    assert status.state == "mismatch"
    assert not final.exists()
    assert _partials(root) == []

    final.write_bytes(GOOD)  # an existing good file survives a later bad download
    status = fm.fetch(_spec(), base + "/t.pth", str(root))
    assert status.state == "mismatch"
    assert final.read_bytes() == GOOD
    assert _partials(root) == []


def test_a_missing_url_is_a_download_failure_not_a_crash(fm, server, tmp_path):
    base, _srv, _ = server
    root = tmp_path / "root"
    status = fm.fetch(_spec(), base + "/absent.pth", str(root))
    assert status.state == "missing"
    assert _partials(root) == []


def test_an_unreachable_server_is_a_download_failure(fm, tmp_path):
    root = tmp_path / "root"
    status = fm.fetch(_spec(), "http://127.0.0.1:9/t.pth", str(root), timeout=2.0)
    assert status.state == "missing"
    assert _partials(root) == []


def test_a_file_url_is_refused_and_changes_nothing(fm, tmp_path):
    source = tmp_path / "elsewhere.pth"
    source.write_bytes(GOOD)
    root = tmp_path / "root"
    final = root / "checkpoints" / "t.pth"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"existing")
    status = fm.fetch(_spec(), source.as_uri(), str(root))
    assert status.state == "missing"
    assert final.read_bytes() == b"existing"
    assert _partials(root) == []


def test_main_exit_codes(fm, server, tmp_path, monkeypatch, capsys):
    base, srv, handler = server
    root = tmp_path / "root"
    monkeypatch.setattr(fm, "MODELS", {"mask": _spec()})

    # no URL registered: exit 2 and a pointer to the docs
    assert fm.main(["--root", str(root), "--key", "mask"]) == 2
    assert (
        "no download URL registered for 'mask' yet; see docs/MODELS.md" in capsys.readouterr().out
    )

    # good bytes: exit 0, then the file is present
    (srv / "t.pth").write_bytes(GOOD)
    assert fm.main(["--root", str(root), "--key", "mask", "--url", f"mask={base}/t.pth"]) == 0
    assert "mask: ok (downloaded)" in capsys.readouterr().out
    assert (root / "checkpoints" / "t.pth").read_bytes() == GOOD

    # already verifies: exit 0 without a request
    handler.hits = 0
    assert fm.main(["--root", str(root), "--key", "mask", "--url", f"mask={base}/t.pth"]) == 0
    assert "mask: ok (already present)" in capsys.readouterr().out
    assert handler.hits == 0

    # corrupt bytes: exit 1
    (root / "checkpoints" / "t.pth").unlink()
    (srv / "t.pth").write_bytes(BAD)
    assert fm.main(["--root", str(root), "--key", "mask", "--url", f"mask={base}/t.pth"]) == 1
    out = capsys.readouterr().out
    assert "mask: download had the wrong digest, discarded (existing file kept)" in out
    assert _partials(root) == []


def test_main_does_not_replace_a_present_file_that_is_not_the_checkpoint(
    fm, server, tmp_path, monkeypatch, capsys
):
    base, srv, handler = server
    (srv / "t.pth").write_bytes(GOOD)
    root = tmp_path / "root"
    final = root / "checkpoints" / "t.pth"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"a lab's retrained weights")
    monkeypatch.setattr(fm, "MODELS", {"mask": _spec()})
    argv = ["--root", str(root), "--key", "mask", "--url", f"mask={base}/t.pth"]

    handler.hits = 0
    assert fm.main(argv) == 1
    out = capsys.readouterr().out
    assert str(final) in out
    assert "--replace" in out
    assert final.read_bytes() == b"a lab's retrained weights"
    assert handler.hits == 0

    assert fm.main([*argv, "--replace"]) == 0
    assert final.read_bytes() == GOOD
    assert _partials(root) == []


def test_main_uses_the_registered_url_and_defaults_to_every_model(
    fm, server, tmp_path, monkeypatch
):
    base, srv, _ = server
    (srv / "t.pth").write_bytes(GOOD)
    spec = ModelSpec(
        key="mask", relpath="checkpoints/t.pth", sha256=_spec().sha256, url=base + "/t.pth"
    )
    monkeypatch.setattr(fm, "MODELS", {"mask": spec})
    root = tmp_path / "root"
    assert fm.main(["--root", str(root)]) == 0
    assert (root / "checkpoints" / "t.pth").read_bytes() == GOOD


def test_a_malformed_url_override_is_a_usage_error(fm, tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "MODELS", {"mask": _spec()})
    with pytest.raises(SystemExit) as excinfo:
        fm.main(["--root", str(tmp_path), "--url", "no-equals-sign"])
    assert excinfo.value.code == 2
