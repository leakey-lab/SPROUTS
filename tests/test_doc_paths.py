"""The documents name only repository paths that exist (W5-T7, F7).

Every back-ticked token of the README, CLAUDE.md and ``docs/*.md`` that looks like a
repository path must be a tracked file or folder (or a module path such as
``app/gui/panels.Panel``). Plans and specs under ``docs/superpowers/`` are history and are
not scanned; ``docs/KNOWN_ISSUES.md`` is a record that names deleted files by design.
A token whose first folder is not a tracked top-level entry, or a bare name that is no
tracked file's name with one of ``FILE_SUFFIXES``, is not checked: the test finds stale
paths, it does not prove that every token is a path.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# First segments that are local or untracked on purpose (weights, build output, the venv,
# tool folders), so a doc may name them without the repository holding them.
LOCAL_TOPLEVEL = {"checkpoints", "dist", "build", "venv", "graphify-out", ".claude"}
# Tokens that are names of things, not paths (memory index, bare extension).
NOT_PATHS = {"MEMORY.md", ".md"}
FILE_SUFFIXES = (".py", ".md", ".qss", ".spec", ".ps1", ".bat", ".toml", ".cff")
SKIPPED_DOCS = {"docs/KNOWN_ISSUES.md"}

_TOKEN = re.compile(r"`([^`\n]+)`")


def _tracked_files() -> set[str]:
    if shutil.which("git") is None:
        pytest.skip("git not available")
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")
    return {name for name in result.stdout.split("\0") if name}


def _exists(path: str, tracked: set[str]) -> bool:
    path = path.rstrip("/")
    if path in tracked or any(name.startswith(path + "/") for name in tracked):
        return True
    # a module path: a/b/c.Name -> a/b/c.py
    stem = path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path
    return f"{stem}.py" in tracked


def unresolved(text: str, tracked: set[str]) -> list[str]:
    """The back-ticked repository paths of ``text`` that are not in ``tracked``."""
    top = {name.split("/", 1)[0] for name in tracked}
    names = {name.rsplit("/", 1)[-1] for name in tracked}
    missing: list[str] = []
    for match in _TOKEN.finditer(text):
        token = match.group(1).split(" ", 1)[0].split("::", 1)[0]
        if not token or any(mark in token for mark in "<*{"):
            continue
        token = token.replace("\\", "/")
        if token in NOT_PATHS or token.split("/", 1)[0] in LOCAL_TOPLEVEL:
            continue
        if "/" in token:
            if token.split("/", 1)[0] in top and not _exists(token, tracked):
                missing.append(token)
        elif token.endswith(FILE_SUFFIXES) and token not in names:
            missing.append(token)
    return missing


def test_a_missing_path_is_reported():
    tracked = {"README.md", "app/gui/panels.py", "docs/MODELS.md"}
    assert unresolved("see `docs/NOPE.md` and `app/nope.py`", tracked) == [
        "docs/NOPE.md",
        "app/nope.py",
    ]
    assert unresolved("see `README.md` and `app/gui/panels.Panel`", tracked) == []


def test_docs_name_only_existing_repository_paths():
    tracked = _tracked_files()
    docs = ["README.md", "CLAUDE.md"] + sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md")
    )
    problems: list[str] = []
    for doc in docs:
        if doc in SKIPPED_DOCS:
            continue
        for number, line in enumerate((ROOT / doc).read_text(encoding="utf-8").splitlines(), 1):
            problems.extend(f"{doc}:{number}: {path}" for path in unresolved(line, tracked))
    assert not problems, "paths the docs name that the repository does not have:\n" + "\n".join(
        problems
    )
