"""ShellTopbar review fixes (wf2 fixer #2).

F1 - a stage click the open editor refuses (unsaved-changes prompt answered
     Cancel, or a failed save) keeps the EDITOR's stage highlighted and its
     action page shown, for every editor, not only Trace GT.
F2 - the topbar density does not depend on which action page is current:
     switching to a stage with a wider action page never leaves the row
     squeezed below its minimum (stage names elided, "Cor...ton").
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.gui.panels import Panel  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

STAGES = [
    "Library",
    "Trace GT",
    "Generate",
    "Correct Mask",
    "Correct Skeleton",
    "Measure",
    "Visualize",
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def mw(app):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.mask_tracing_interface.maybe_save_changes = lambda: None
    window.app_stack.setCurrentIndex(1)
    window.show()
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()


def _settle(app):
    for _ in range(3):
        app.processEvents()


def _checked(mw) -> str | None:
    return next((k for k, b in mw.ribbon_buttons.items() if b.isChecked()), None)


# --------------------------------------------------------------------------- #
#  F1: a refused stage switch keeps the editor's stage highlighted
# --------------------------------------------------------------------------- #
@pytest.fixture
def refusing_editor(mw, app, monkeypatch):
    """Every editor answers the unsaved-changes prompt with Cancel.

    The real ``switch_right_panel`` runs (and refuses); the editor toggles are
    no-ops because entering an editor needs a loaded image.
    """
    for editor in mw._editors().values():
        monkeypatch.setattr(editor, "maybe_save_changes", lambda: False)
    for name in (
        "toggle_mask_tracing",
        "toggle_skeleton_correction",
        "toggle_gt_tracing",
    ):
        monkeypatch.setattr(mw, name, lambda: None)
    yield
    monkeypatch.undo()
    mw.switch_right_panel("display")
    mw.ribbon_buttons["Library"].click()
    _settle(app)


@pytest.mark.parametrize(
    ("panel", "editor_stage", "clicked"),
    [
        (Panel.MASK_TRACING, "Correct Mask", "Measure"),
        (Panel.MASK_TRACING, "Correct Mask", "Library"),
        (Panel.MASK_TRACING, "Correct Mask", "Correct Skeleton"),
        (Panel.SKELETON_CORRECTION, "Correct Skeleton", "Measure"),
        (Panel.SKELETON_CORRECTION, "Correct Skeleton", "Correct Mask"),
        (Panel.GT_TRACING, "Trace GT", "Generate"),
    ],
)
def test_refused_stage_click_keeps_the_editor_stage(
    mw, app, refusing_editor, panel, editor_stage, clicked
):
    top = mw.ribbon
    mw.right_panel.setCurrentIndex(panel)
    top.activate_stage(editor_stage)
    _settle(app)

    mw.ribbon_buttons[clicked].click()
    _settle(app)

    assert mw.right_panel.currentIndex() == panel
    assert _checked(mw) == editor_stage
    assert mw.action_bar_stack.currentIndex() == mw._action_bar_pages[editor_stage]


def test_accepted_stage_click_still_highlights_the_clicked_stage(mw, app):
    mw.ribbon_buttons["Measure"].click()
    _settle(app)
    try:
        assert mw.right_panel.currentIndex() == Panel.DISPLAY
        assert _checked(mw) == "Measure"
        assert mw.action_bar_stack.currentIndex() == mw._action_bar_pages["Measure"]
    finally:
        mw.ribbon_buttons["Library"].click()
        _settle(app)


# --------------------------------------------------------------------------- #
#  F2: density is decided from the widest action page
# --------------------------------------------------------------------------- #
def _enter(mw, app, stage):
    mw.ribbon_buttons[stage].click()
    _settle(app)


def test_full_width_is_the_same_on_every_stage(mw, app):
    widths = {}
    try:
        for stage in STAGES:
            _enter(mw, app, stage)
            widths[stage] = mw.ribbon.full_width()
            mw.switch_right_panel("display")
    finally:
        _enter(mw, app, "Library")
    assert len(set(widths.values())) == 1, widths


def _assert_row_fits(mw, context: str) -> None:
    bar = mw.ribbon
    need = bar.layout().minimumSize().width()
    assert need <= bar.width(), f"{context}: topbar needs {need}px, has {bar.width()}px"
    for key, btn in mw.ribbon_buttons.items():
        assert btn.width() >= btn.sizeHint().width(), f"{context}: '{key}' squeezed"


def test_switching_to_a_wider_action_page_never_squeezes_the_row(mw, app):
    top = mw.ribbon
    # From the narrowest-action stage to the widest, at every width in between.
    low = max(mw.minimumSizeHint().width(), top.compact_width())
    high = top.full_width() + 60
    try:
        for width in range(low, high, 12):
            for src, dst in (
                ("Correct Mask", "Generate"),
                ("Correct Skeleton", "Measure"),
            ):
                _enter(mw, app, "Library")
                _enter(mw, app, src)
                mw.resize(width, 640)
                _settle(app)
                mw.switch_right_panel("display")
                _enter(mw, app, dst)
                _assert_row_fits(mw, f"{src}->{dst} @ {mw.width()}px")
    finally:
        mw.switch_right_panel("display")
        _enter(mw, app, "Library")
