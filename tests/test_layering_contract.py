"""Layering contract (CQ-05): lower layers never import upward.

* ``app.inference`` and ``app.visualization`` do not import ``app.gui`` / ``app.handlers``;
* the Qt-free ``UiFeedback`` port lives in ``app.feedback``;
* the worker error boundary lives in ``app.workers``;
* no compatibility re-export module is left (W5-T5), and the file stamp helpers have one home.
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


def _names_in(source: str, package: tuple[str, ...]) -> set[str]:
    """Every module ``source`` imports: ``from X import y`` also counts as ``X.y`` (``y`` may be
    a submodule), and relative imports are resolved against ``package``."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = list(package[: len(package) - node.level + 1]) if node.level else []
            base = ".".join(parts + [node.module] if node.module else parts)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _imported_names(path: Path) -> set[str]:
    return _names_in(path.read_text(encoding="utf-8"), path.relative_to(ROOT).parts[:-1])


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


REMOVED_MODULES = ("app.handlers.worker_base", "app.gui.feedback", "app.handlers.window_feedback")
STAMP_HELPERS = frozenset({"_stamp", "_file_stamp", "file_stamp", "_same_file", "same_file"})


def test_no_compat_reexport_module_is_left() -> None:
    for module in REMOVED_MODULES:
        assert not (ROOT / (module.replace(".", "/") + ".py")).exists(), module


def test_nothing_imports_the_removed_modules() -> None:
    offenders: dict[str, list[str]] = {}
    paths = [ROOT / "main.py"]
    for folder in ("app", "tests"):
        paths.extend(sorted((ROOT / folder).rglob("*.py")))
    for path in paths:
        if path == Path(__file__):
            continue
        bad = sorted(n for n in _imported_names(path) if n in REMOVED_MODULES)
        if bad:
            offenders[str(path.relative_to(ROOT))] = bad
    assert offenders == {}


def test_the_import_guard_reads_aliases_and_relative_imports() -> None:
    source = (
        "from app.gui import feedback\n"
        "from .worker_base import GuardedThread\n"
        "from .. import feedback as port\n"
    )
    assert {"app.gui.feedback", "app.handlers.worker_base", "app.feedback"} <= _names_in(
        source, ("app", "handlers")
    )


def test_nothing_under_handlers_or_inference_imports_gui_feedback() -> None:
    offenders = {
        str(path.relative_to(ROOT))
        for package in ("handlers", "inference")
        for path in (ROOT / "app" / package).rglob("*.py")
        if "app.gui.feedback" in _imported_names(path)
    }
    assert offenders == set()


def test_stamp_helpers_are_defined_once() -> None:
    home = ROOT / "app" / "io_utils.py"
    defined = {
        str(path.relative_to(ROOT)): sorted(
            node.name
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef) and node.name in STAMP_HELPERS
        )
        for path in sorted((ROOT / "app").rglob("*.py"))
        if path != home
    }
    assert {name: found for name, found in defined.items() if found} == {}
    tree = ast.parse(home.read_text(encoding="utf-8"))
    assert {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} >= {
        "file_stamp",
        "same_file",
    }


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
