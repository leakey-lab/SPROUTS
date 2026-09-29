"""Guard: the right-panel page contract after the Correct swap (spec 3.1, plan C19).

``MainWindow.right_panel`` is a ``QStackedWidget`` whose pages are named by
``app.gui.panels.Panel``. One Correct stage replaced the mask and skeleton editors and the
pages were renumbered on purpose, with no aliases (spec D18): ``DISPLAY = 0``,
``CORRECT = 1``, ``VISUALIZATION = 2``, ``GT_TRACING = 3``. Two kinds of stale caller
would survive that silently, so both are rejected anywhere under ``app/``:

* an int literal page index: ``right_panel.setCurrentIndex(<int>)``,
  ``right_panel.widget(<int>)``, or ``right_panel.currentIndex()`` compared with ``==``,
  ``!=``, ``in`` or ``not in`` to an int literal. Name pages with ``Panel`` members.
* a legacy page key: ``switch_right_panel`` logs and ignores a key that ``Panel.coerce``
  rejects, so ``switch_right_panel("mask_tracing")`` would do nothing, without an error.
  No string constant may name ``mask_tracing`` or ``skeleton_correction`` (a whole word,
  in any case, as ``Panel.coerce`` matches keys). Docstrings (the first statement of a
  module, class or function) may, to say which keys are gone.

The scan is syntactic (AST): it cannot follow an index through a variable, so it pins the
call sites as written. The self-tests prove each rule can fail.
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

#: ``right_panel`` methods whose first argument is a page index.
PAGE_ARGUMENT = frozenset({"setCurrentIndex", "widget"})
#: ``right_panel`` methods that return a page index.
PAGE_RESULT = frozenset({"currentIndex"})
#: Comparisons that test a page index for a value.
MATCHING = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)
#: The page keys of the two editors the Correct stage replaced.
LEGACY_KEY = re.compile(r"\b(?:mask_tracing|skeleton_correction)\b", re.IGNORECASE)
#: Nodes whose first statement, when a string, is their docstring.
DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _is_right_panel(node: ast.AST) -> bool:
    """``right_panel`` as a name or as the last attribute (``self.right_panel``)."""
    return (isinstance(node, ast.Name) and node.id == "right_panel") or (
        isinstance(node, ast.Attribute) and node.attr == "right_panel"
    )


def _right_panel_call(node: ast.AST, methods: frozenset[str]) -> bool:
    """``<right_panel>.<method>(...)`` for one of ``methods``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in methods
        and _is_right_panel(node.func.value)
    )


def _is_int_literal(node: ast.AST) -> bool:
    """An int constant (``True`` / ``False`` are ints to Python, not page indices)."""
    return isinstance(node, ast.Constant) and type(node.value) is int


def _names_an_int(node: ast.AST) -> bool:
    """An int literal, or a tuple / list / set display holding one."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_is_int_literal(item) for item in node.elts)
    return _is_int_literal(node)


def _compares_index_to_int(node: ast.Compare) -> bool:
    """A ``==`` / ``!=`` / ``in`` / ``not in`` link of ``currentIndex()`` and an int literal."""
    operands = [node.left, *node.comparators]
    return any(
        isinstance(op, MATCHING)
        and (
            (_right_panel_call(left, PAGE_RESULT) and _names_an_int(right))
            or (_right_panel_call(right, PAGE_RESULT) and _names_an_int(left))
        )
        for op, left, right in zip(node.ops, operands[:-1], operands[1:], strict=True)
    )


def int_page_indices(source: str) -> list[int]:
    """Line numbers where an int literal names a right-panel page in ``source``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if _right_panel_call(node, PAGE_ARGUMENT):
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            if any(_is_int_literal(argument) for argument in arguments):
                found.append(node.lineno)
        elif isinstance(node, ast.Compare) and _compares_index_to_int(node):
            found.append(node.lineno)
    return sorted(found)


def right_panel_page_calls(source: str) -> int:
    """How many ``right_panel`` page calls (index argument or result) ``source`` makes."""
    methods = PAGE_ARGUMENT | PAGE_RESULT
    return sum(_right_panel_call(node, methods) for node in ast.walk(ast.parse(source)))


def _docstring_ids(tree: ast.Module) -> set[int]:
    """``id()`` of every docstring constant of ``tree``."""
    return {
        id(first.value)
        for node in ast.walk(tree)
        if isinstance(node, DOCSTRING_OWNERS)
        and node.body
        and isinstance(first := node.body[0], ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    }


def legacy_panel_keys(source: str) -> list[int]:
    """Line numbers of string constants (docstrings excepted) naming a legacy page key."""
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and LEGACY_KEY.search(node.value)
    )


def _app_sources() -> dict[str, str]:
    """Every module under ``app/`` by repo-relative path."""
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(APP.rglob("*.py"))
    }


def _offenders(sources: dict[str, str], scan: Callable[[str], list[int]]) -> dict[str, list[str]]:
    """``{path: ["<line>: <code>", ...]}`` for every module where ``scan`` finds lines."""
    found = {}
    for path, source in sources.items():
        if lines := scan(source):
            code = source.splitlines()
            found[path] = [f"{line}: {code[line - 1].strip()}" for line in lines]
    return found


def test_the_index_scan_flags_int_literals_and_passes_panel_members() -> None:
    source = textwrap.dedent(
        """\
        self.right_panel.setCurrentIndex(1)
        mw.right_panel.widget(3)
        right_panel.setCurrentIndex(index=2)
        if self.right_panel.currentIndex() == 4:
            pass
        if 1 != mw.right_panel.currentIndex():
            pass
        if self.right_panel.currentIndex() in (0, Panel.CORRECT):
            pass
        self.right_panel.setCurrentIndex(Panel.CORRECT)
        self.right_panel.widget(Panel.GT_TRACING)
        self.right_panel.setCurrentIndex(target)
        if self.right_panel.currentIndex() == Panel.DISPLAY:
            pass
        if self.right_panel.currentIndex() < 0:
            pass
        self.right_panel.addWidget(page, 1)
        self.stack.setCurrentIndex(1)
        self.right_panel.setCurrentIndex(True)
        """
    )
    assert int_page_indices(source) == [1, 2, 3, 4, 6, 8]
    assert right_panel_page_calls(source) == 12


def test_the_key_scan_flags_legacy_keys_but_not_docstrings() -> None:
    source = textwrap.dedent(
        '''\
        """Module docstring: "mask_tracing" and "skeleton_correction" are gone."""


        class Page:
            """Keys like 'mask_tracing' were renamed."""

            def show(self):
                """Show "skeleton_correction"."""
                self.switch_right_panel("mask_tracing")
                Panel.coerce("SKELETON_CORRECTION")
                message = f"go to {'mask_tracing'}"
                "a later bare string naming skeleton_correction is code, not a docstring"
                self.switch_right_panel("correct")
                folder = "skeleton_corrections"
                module = "app.gui.mask_tracing_interface"
        '''
    )
    assert legacy_panel_keys(source) == [9, 10, 11, 12]


def test_no_int_literal_names_a_right_panel_page() -> None:
    sources = _app_sources()
    seen = sum(right_panel_page_calls(source) for source in sources.values())
    assert seen > 0, "the scan found no right_panel page call: it would pass vacuously"
    offenders = _offenders(sources, int_page_indices)
    assert offenders == {}, "name right_panel pages with app.gui.panels.Panel members"


def test_no_legacy_page_key_is_left_in_app() -> None:
    offenders = _offenders(_app_sources(), legacy_panel_keys)
    assert offenders == {}, (
        "legacy right-panel keys (switch_right_panel ignores them): use Panel.CORRECT / 'correct'"
    )
