"""Start-up import budget: the heavy scientific stack stays out of the app's start-up.

``import app.gui.main_window`` is what ``main.py`` does before the window shows, and
``MainWindow.__init__`` then imports the skeleton and mask-generation handlers. Neither
may import scikit-image (with scipy behind it, about 0.2 s of a 0.4 s start-up): the
skeleton code imports it on first use, and ``MainWindow`` prewarms it off the GUI
thread once the window shows (``skeleton_graph_model.prewarm_skeletonize``). torch,
dash, pandas, plotly and flask load on demand too; cv2 and PIL are allowed.

Each check runs in a fresh interpreter, so modules other tests imported do not count.
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


def _heavy_packages_loaded_by(statement: str) -> list[str]:
    """Top-level HEAVY packages a fresh interpreter has loaded after ``statement``."""
    code = textwrap.dedent(
        f"""
        import sys
        {statement}
        print(" ".join(sorted({{name.split(".")[0] for name in sys.modules}})))
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
    loaded = set(result.stdout.split())
    return [name for name in HEAVY if name in loaded]


def test_importing_the_main_window_loads_no_heavy_package():
    assert _heavy_packages_loaded_by("import app.gui.main_window") == []


@pytest.mark.parametrize(
    "module", ["app.handlers.skeleton_handler", "app.handlers.mask_generation_handler"]
)
def test_the_handlers_main_window_builds_load_no_heavy_package(module):
    assert _heavy_packages_loaded_by(f"import {module}") == []
