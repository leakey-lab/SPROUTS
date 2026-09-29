"""Layering contract (CQ-05): lower layers never import upward.

* ``app.inference`` and ``app.visualization`` do not import ``app.gui`` / ``app.handlers``;
* the Qt-free ``UiFeedback`` port lives in ``app.feedback`` (``app.gui.feedback`` re-exports);
* the worker error boundary lives in ``app.workers`` (``app.handlers.worker_base`` re-exports).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPWARD = ("app.gui", "app.handlers")


def _app_imports(path: Path) -> list[str]:
    """Absolute ``app.*`` modules imported anywhere in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return [name for name in found if name == "app" or name.startswith("app.")]


def _upward(names: list[str]) -> list[str]:
    return [n for n in names if any(n == u or n.startswith(u + ".") for u in UPWARD)]


@pytest.mark.parametrize("package", ["inference", "visualization"])
def test_lower_layers_do_not_import_gui_or_handlers(package: str) -> None:
    offenders = {
        str(path.relative_to(ROOT)): bad
        for path in sorted((ROOT / "app" / package).rglob("*.py"))
        if (bad := _upward(_app_imports(path)))
    }
    assert offenders == {}


@pytest.mark.parametrize("module", ["feedback.py", "workers.py"])
def test_neutral_modules_do_not_import_gui_or_handlers(module: str) -> None:
    assert _upward(_app_imports(ROOT / "app" / module)) == []


def test_feedback_port_is_neutral_and_reexported() -> None:
    import app.feedback as neutral
    import app.gui.feedback as compat

    assert set(compat.__all__) == set(neutral.__all__)
    for name in neutral.__all__:
        assert getattr(compat, name) is getattr(neutral, name), name


def test_worker_boundary_is_neutral_and_reexported() -> None:
    import app.handlers.worker_base as compat
    import app.workers as neutral

    for name in (
        "GuardedThread",
        "JobCancelled",
        "describe_error",
        "is_user_condition",
    ):
        assert getattr(compat, name) is getattr(neutral, name), name


def _loaded_after_import(module: str) -> set[str]:
    code = (
        f"import sys, importlib; importlib.import_module({module!r}); "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return set(out.split())


def test_feedback_port_stays_qt_free() -> None:
    loaded = _loaded_after_import("app.feedback")
    assert not any(m == "PyQt6" or m.startswith("PyQt6.") for m in loaded)
    assert not _upward(sorted(m for m in loaded if m.startswith("app")))


def test_metric_thread_does_not_load_handlers_or_gui() -> None:
    loaded = _loaded_after_import("app.inference.metric_thread")
    assert not _upward(sorted(m for m in loaded if m.startswith("app")))
