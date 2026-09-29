"""One lint/format toolchain: ruff (CQ-02).

black and isort used to be configured and documented alongside ruff, while pre-commit ran
ruff + ruff-format. The two formatters disagree on some lines (e.g. assert messages) and
isort silently skipped non-cp1252 files on Windows, so the contract is: ruff is the only
linter, import sorter ("I" rules) and formatter, and every place that tells a contributor
or agent what to run says the same thing.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, name: str) -> str:
    """Body of one TOML table (stdlib tomllib is py3.11+, CI runs py3.10)."""
    match = re.search(rf"^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert match, f"[{name}] missing from pyproject.toml"
    return match.group(1)


def test_pyproject_configures_only_ruff():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert not re.search(r"^\[tool\.black\]", text, re.M), "[tool.black] competes with ruff format"
    assert not re.search(r"^\[tool\.isort\]", text, re.M), (
        "[tool.isort] competes with ruff's I rules"
    )
    ruff = _section(text, "tool.ruff")
    assert re.search(r"^line-length\s*=\s*100$", ruff, re.M)
    assert re.search(r'^target-version\s*=\s*"py310"$', ruff, re.M)
    select = re.search(r"^select\s*=\s*\[(.*?)\]", _section(text, "tool.ruff.lint"), re.M)
    assert select and '"I"' in select.group(1), "ruff must own import sorting"


def test_pre_commit_runs_ruff_lint_and_format_only():
    text = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hook_ids = re.findall(r"-\s*id:\s*([\w-]+)", text)
    assert "ruff" in hook_ids or "ruff-check" in hook_ids
    assert "ruff-format" in hook_ids
    assert not {"black", "isort"} & set(hook_ids)
    assert "psf/black" not in text and "pycqa/isort" not in text.lower()


def test_claude_md_commands_match_pre_commit():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "-m ruff format ." in text
    assert "-m ruff check --fix ." in text
    assert not re.search(r"-m\s+(black|isort)\b", text), "CLAUDE.md still says run black/isort"
    assert "</invoke>" not in text, "stray tool-call markup at the end of CLAUDE.md"
