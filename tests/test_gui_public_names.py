"""Guard: the public names of ``app.gui`` after the Correct swap (spec 12.4, 13 and D18).

``app.gui`` re-exports its public classes lazily (PEP 562, ``app/gui/_lazy.py``), so an
export whose module was deleted or renamed breaks only when something touches it. This
pins the surface: every name in ``app.gui.__all__`` resolves to the object defined under
that name (no aliases), ``CorrectEditor`` (the editor of the Correct stage) is public, and
the names of the mask editor it replaced are gone (a clean break, spec D18).
"""

from __future__ import annotations

from types import ModuleType

import pytest

import app.gui as gui

#: The mask editor's public names, deleted with it when the Correct stage replaced it.
REMOVED = (
    "MaskTracingInterface",
    "MaskTracingGraphicsView",
    "MaskDrawingMixin",
    "create_brush_cursor",
    "create_panning_cursor",
)


def _own_name(value: object) -> str | None:
    """The name ``value`` was defined under (for a module, the last dotted part)."""
    name = getattr(value, "__name__", None)
    if isinstance(value, ModuleType) and name:
        return name.rpartition(".")[2]
    return name


@pytest.mark.parametrize("name", sorted(gui.__all__))
def test_every_public_name_resolves_to_the_object_of_that_name(name: str) -> None:
    assert _own_name(getattr(gui, name)) == name


def test_the_correct_editor_is_public() -> None:
    assert "CorrectEditor" in gui.__all__
    from app.gui.correct.editor import CorrectEditor

    assert gui.CorrectEditor is CorrectEditor


@pytest.mark.parametrize("name", REMOVED)
def test_the_deleted_mask_editor_names_are_gone(name: str) -> None:
    assert name not in gui.__all__
    with pytest.raises(AttributeError):
        getattr(gui, name)
