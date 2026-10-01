"""Importing the main window's parts costs nothing new (review CQ-07).

``app/gui/window/__init__.py`` imports nothing, and each part imports only what the
unsplit ``main_window.py`` already imported: no part loads a heavy package (torch, dash,
pandas, plotly, scipy, scikit-image, flask; see ``test_startup_import_budget.py``), and the
package itself loads nothing but itself. Each check runs in a fresh interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HEAVY = ("dash", "flask", "pandas", "plotly", "scipy", "skimage", "torch")
PARTS = sorted(
    path.stem
    for path in (REPO_ROOT / "app" / "gui" / "window").glob("*.py")
    if path.stem != "__init__"
)


def _loaded_after(statement: str, *, first: str = "") -> set[str]:
    """Modules a fresh interpreter loads for ``statement`` (after running ``first``)."""
    code = textwrap.dedent(
        f"""
        import sys
        {first}
        before = set(sys.modules)
        {statement}
        print(" ".join(sorted(set(sys.modules) - before)))
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


def test_the_parts_are_the_modules_listed():
    assert PARTS == [
        "_common",
        "body",
        "editors",
        "job_buttons",
        "lifecycle",
        "metrics",
        "navigator",
        "project_loader",
        "stages",
    ]


def test_the_package_alone_loads_nothing():
    assert _loaded_after("import app.gui.window", first="import app.gui") == {"app.gui.window"}


@pytest.mark.parametrize("part", PARTS)
def test_no_part_loads_a_heavy_package(part):
    loaded = _loaded_after(f"import app.gui.window.{part}")
    assert sorted({name.split(".")[0] for name in loaded} & set(HEAVY)) == []


def test_the_main_window_loads_every_part():
    loaded = _loaded_after("import app.gui.main_window")
    assert {f"app.gui.window.{part}" for part in PARTS} <= loaded
