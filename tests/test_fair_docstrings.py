"""Docstring ratchet (FAIR4RS R1: software is described with accurate attributes; FR-16).

Measured statically with ``ast`` (nothing is imported):

* Packages listed in ``_FULLY_DOCUMENTED`` document every public module, class,
  function and method.
* The whole ``app`` package keeps at least ``_APP_FLOOR`` of its public
  functions and methods documented. Raise the floor as coverage improves; never
  lower it to make unrelated work pass.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_FULLY_DOCUMENTED = ("app/gt",)
_APP_FLOOR = 0.45  # measured 0.489 (542 public functions) on 2026-09-28


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _public_defs(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Public classes, functions and methods at module or class level."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
            yield node.name, node
        elif isinstance(node, ast.ClassDef) and _is_public(node.name):
            yield node.name, node
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(
                    member.name
                ):
                    yield f"{node.name}.{member.name}", member


def _modules(package: str) -> list[Path]:
    return sorted(p for p in (ROOT / package).rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_fully_documented_packages_document_every_public_api():
    missing = []
    for package in _FULLY_DOCUMENTED:
        for path in _modules(package):
            tree = _parse(path)
            rel = path.relative_to(ROOT).as_posix()
            if ast.get_docstring(tree) is None:
                missing.append(f"{rel}: module")
            missing += [
                f"{rel}:{node.lineno} {name}"
                for name, node in _public_defs(tree)
                if ast.get_docstring(node) is None
            ]
    assert missing == [], "public API without a docstring:\n" + "\n".join(missing)


def test_app_public_function_docstring_coverage_stays_above_the_floor():
    documented = total = 0
    for path in _modules("app"):
        for _name, node in _public_defs(_parse(path)):
            if isinstance(node, ast.ClassDef):
                continue
            total += 1
            documented += ast.get_docstring(node) is not None
    coverage = documented / total
    assert (
        coverage >= _APP_FLOOR
    ), f"docstring coverage fell to {coverage:.1%} ({documented}/{total})"
