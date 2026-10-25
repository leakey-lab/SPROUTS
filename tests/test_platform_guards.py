"""Ratchets that keep Windows-only code behind a platform check (O21: Linux and macOS).

Source scans over ``main.py``, ``app/`` and ``scripts/``: they fail when a Windows-only
module is imported, a Windows API is reached outside a ``sys.platform == "win32"`` branch,
or a process pool appears without a deliberate start-method choice.
"""

from __future__ import annotations

import ast
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAN = [
    ROOT / "main.py",
    *sorted((ROOT / "app").rglob("*.py")),
    *sorted((ROOT / "scripts").rglob("*.py")),
]

WINDOWS_ONLY_MODULES = ("win32", "pywintypes", "msvcrt", "winreg")
WINDOWS_ONLY_ATTRIBUTES = ("WinDLL", "windll")
#: Threads only (O21 report 8): a future pool must choose its start method on purpose.
POOL_NAMES = ("multiprocessing", "ProcessPoolExecutor")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _with_parents(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _imported_modules(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node, node.module


def test_the_scan_sees_the_sources():
    names = {_rel(path) for path in SCAN}
    assert "main.py" in names and "app/io_utils.py" in names and len(names) > 100


def test_no_windows_only_module_is_imported():
    found = [
        f"{_rel(path)}:{node.lineno} imports {module}"
        for path in SCAN
        for node, module in _imported_modules(_parse(path))
        if module.split(".")[0].startswith(WINDOWS_ONLY_MODULES)
    ]
    assert not found, found


def _under_win32_branch(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when an ancestor ``if`` tests ``"win32"`` and ``node`` is in its body."""
    child, parent = node, parents.get(node)
    while parent is not None:
        if isinstance(parent, ast.If) and child in parent.body:
            if any(
                isinstance(n, ast.Constant) and n.value == "win32" for n in ast.walk(parent.test)
            ):
                return True
        child, parent = parent, parents.get(parent)
    return False


def test_windows_dll_loading_is_only_inside_a_win32_branch():
    found = []
    for path in SCAN:
        tree = _parse(path)
        parents = _with_parents(tree)
        for node in ast.walk(tree):
            name = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.id
                if isinstance(node, ast.Name)
                else None
            )
            if name in WINDOWS_ONLY_ATTRIBUTES and not _under_win32_branch(node, parents):
                found.append(f"{_rel(path)}:{node.lineno} uses {name}")
    assert not found, found


def test_the_win32_branch_detector_tells_the_branches_apart():
    source = (
        "import sys, ctypes\n"
        "if sys.platform == 'win32':\n    a = ctypes.WinDLL\n"
        "else:\n    b = ctypes.WinDLL\n"
        "c = ctypes.windll\n"
    )
    tree = ast.parse(source)
    parents = _with_parents(tree)
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr != "platform"]
    inside = [_under_win32_branch(n, parents) for n in sorted(uses, key=lambda n: n.lineno)]
    assert inside == [True, False, False]


def _code_text(path: Path) -> str:
    """The source with its comments left out (a comment may name the platform's API)."""
    with path.open("rb") as fh:
        tokens = tokenize.tokenize(fh.readline)
        return " ".join(t.string for t in tokens if t.type != tokenize.COMMENT)


def test_d3d11_is_named_only_in_qt_environment():
    found = [
        _rel(path)
        for path in SCAN
        if _rel(path) != "app/qt_environment.py" and "d3d11" in _code_text(path).lower()
    ]
    assert not found, found


def test_nothing_calls_os_startfile():
    found = [
        f"{_rel(path)}:{node.lineno}"
        for path in SCAN
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Attribute) and node.attr == "startfile"
    ]
    assert not found, found


def test_app_uses_threads_only():
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = _parse(path)
        found += [
            f"{_rel(path)}:{node.lineno} imports {module}"
            for node, module in _imported_modules(tree)
            if module.split(".")[0] == "multiprocessing"
        ]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name in POOL_NAMES for alias in node.names
            ):
                found.append(f"{_rel(path)}:{node.lineno} imports a process pool")
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name in POOL_NAMES:
                    found.append(f"{_rel(path)}:{node.lineno} uses {name}")
            if isinstance(node, ast.keyword) and node.arg == "num_workers":
                found.append(f"{_rel(path)}:{node.value.lineno} passes num_workers=")
    assert not found, found


@pytest.mark.parametrize("name", ["win32con", "pywintypes", "msvcrt", "winreg"])
def test_the_import_scan_flags_a_windows_only_module(name):
    tree = ast.parse(f"import {name}\n")
    assert [m for _, m in _imported_modules(tree) if m.startswith(WINDOWS_ONLY_MODULES)]
