"""Download the SPROUTS model weights and verify them (docs/MODELS.md; owner decision O33).

    ./venv/Scripts/python.exe scripts/fetch_models.py [--key mask|skeleton ...]
                                                      [--url KEY=URL ...] [--root DIR] [--replace]

Each model is fetched from ``ModelSpec.url`` (or the ``--url KEY=URL`` override) to
``<final path>.part``, checked against the registered SHA-256 and only then moved onto its
final path. A download that does not match is discarded, and a good file that is already in
place is never replaced by a bad download. A model that already verifies is skipped. A file
that is present but is not the registered checkpoint (a lab's retrained weights) is left
alone unless ``--replace`` is given.

Exit status: 0 every requested model verifies after the run, 1 a download failed or did
not match, 2 a requested model has no URL (the weights are not published yet) or the
arguments are wrong.

Qt-free and torch-free: standard library and ``app.models`` only.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import os
import sys
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import MODELS, ModelSpec, ModelStatus, verify  # noqa: E402
from app.provenance import file_sha256  # noqa: E402

CHUNK_BYTES = 1024 * 1024
_SCHEMES = ("http", "https")


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def fetch(
    spec: ModelSpec, url: str, root: str | None = None, *, timeout: float = 60.0
) -> ModelStatus:
    """Download ``url`` to the checkpoint path of ``spec`` and keep it only if it verifies.

    Returns ``ModelStatus("ok", ...)`` after the verified file replaced the old one,
    ``"mismatch"`` when the download had another digest, and ``"missing"`` when it could
    not be fetched or written. In both failures the partial file is removed and a file
    that was already in place is left as it was. Never raises for I/O or network errors.
    """
    final = spec.path(root)
    part = final + ".part"
    if urlparse(url).scheme not in _SCHEMES:
        return ModelStatus("missing", final, None)
    try:
        os.makedirs(os.path.dirname(final), exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as response, open(part, "wb") as out:
            while chunk := response.read(CHUNK_BYTES):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        digest = file_sha256(part)
        if digest != spec.sha256:
            _remove(part)
            return ModelStatus("mismatch", final, digest)
        os.replace(part, final)
    except (OSError, http.client.HTTPException):  # URLError and HTTPError are OSErrors
        _remove(part)
        return ModelStatus("missing", final, None)
    return ModelStatus("ok", final, digest)


def _parse_overrides(parser: argparse.ArgumentParser, values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        key, sep, url = value.partition("=")
        if not sep or not url or key not in MODELS:
            parser.error(f"--url expects KEY=URL with KEY one of {sorted(MODELS)}, got {value!r}")
        overrides[key] = url
    return overrides


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download the SPROUTS model weights and verify their SHA-256."
    )
    parser.add_argument(
        "--key",
        action="append",
        choices=sorted(MODELS),
        help="model to fetch (repeatable; default: every model)",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        metavar="KEY=URL",
        help="download address for a model, replacing the registered one (repeatable)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="also replace a file that is present but is not the registered checkpoint",
    )
    parser.add_argument("--root", help="application folder to place the checkpoints under")
    parser.add_argument("--timeout", type=float, default=60.0, help="network timeout, seconds")
    args = parser.parse_args(argv)
    overrides = _parse_overrides(parser, args.url)

    no_url = failed = False
    for key in args.key or list(MODELS):
        spec = MODELS[key]
        current = verify(spec, args.root)
        if current.ok:
            print(f"{key}: ok (already present)")
            continue
        url = overrides.get(key) or spec.url
        if not url:
            print(f"no download URL registered for '{key}' yet; see docs/MODELS.md")
            no_url = True
            continue
        if current.state == "mismatch" and not args.replace:
            print(
                f"{key}: {current.path} does not match the registered checkpoint; "
                "it is not replaced (pass --replace to download over it)"
            )
            failed = True
            continue
        status = fetch(spec, url, args.root, timeout=args.timeout)
        if status.ok:
            print(f"{key}: ok (downloaded)")
        elif status.state == "mismatch":
            print(f"{key}: download had the wrong digest, discarded (existing file kept)")
            failed = True
        else:
            print(f"{key}: download failed from {url}")
            failed = True
    return 2 if no_url else 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
