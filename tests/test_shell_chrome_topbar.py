"""ShellTopbar: responsive density, current-page action stack, stage wiring.

Offscreen, against the real MainWindow. Covers UXS-04 / UI-06 (window fits
900 px, compact density with hysteresis), UXS-12 (checked stage accent icon),
PV-04 / UI-05 / RB-14 (Visualize is a selector, not a toggle), RB-01 step 5
(Skeletons-only on the Generate page) and AR-07 step 4 (ShellTopbar class).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, QSize, Qt  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from tests.qt_teardown import flush_deleted  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

STAGES = [
    "Library",
    "Trace GT",
    "Generate",
    "Correct",
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
    window.correct_editor.maybe_save_changes = lambda: None
    window.app_stack.setCurrentIndex(1)
    window.show()
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    del window
    flush_deleted()


def _settle(app):
    for _ in range(3):
        app.processEvents()


def _enter(mw, app, stage):
    mw.ribbon_buttons[stage].click()
    _settle(app)


# --------------------------------------------------------------------------- #
#  AR-07 step 4: a class, same contract names
# --------------------------------------------------------------------------- #
def test_topbar_is_a_class_with_the_legacy_contract(mw):
    from app.gui.shell_chrome import ShellTopbar

    assert isinstance(mw.ribbon, ShellTopbar)
    assert list(mw.ribbon_buttons) == STAGES
    assert mw.action_bar_stack is mw.ribbon.action_stack
    assert mw._populate_action_bar == mw.ribbon.populate
    assert mw._activate_action_stage == mw.ribbon.activate_stage


def test_stage_click_emits_stage_requested(mw, app):
    seen = []
    mw.ribbon.stage_requested.connect(seen.append)
    try:
        _enter(mw, app, "Measure")
        _enter(mw, app, "Library")
    finally:
        mw.ribbon.stage_requested.disconnect(seen.append)
    assert seen == ["Measure", "Library"]


# --------------------------------------------------------------------------- #
#  UXS-04 / UI-06: width
# --------------------------------------------------------------------------- #
def _real_fonts() -> bool:
    """Offscreen Qt on Windows has an empty font database (every glyph a box, so
    widths are meaningless) unless QT_QPA_FONTDIR points at real fonts."""
    from PyQt6.QtGui import QFontDatabase

    return bool(QFontDatabase.families())


@pytest.mark.parametrize("stage", STAGES)
def test_window_minimum_is_the_compact_topbar_not_the_full_row(mw, app, stage):
    _enter(mw, app, stage)
    try:
        assert mw.minimumSizeHint().width() < mw.ribbon.full_width()
        assert mw.ribbon.minimumSizeHint().width() == mw.ribbon.compact_width()
    finally:
        mw.switch_right_panel("display")
        _enter(mw, app, "Library")


@pytest.mark.parametrize("stage", STAGES)
def test_window_minimum_width_fits_900_px_on_every_stage(mw, app, stage):
    if not _real_fonts():  # needs the QApplication, so not a collection-time skipif
        pytest.skip("no fonts: offscreen glyph widths are boxes")
    _enter(mw, app, stage)
    try:
        assert mw.minimumSizeHint().width() <= 900
    finally:
        mw.switch_right_panel("display")
        _enter(mw, app, "Library")


def test_window_accepts_a_width_below_the_full_row(mw, app):
    _enter(mw, app, "Library")
    target = max(mw.minimumSizeHint().width(), 1000 if _real_fonts() else 0)
    mw.resize(target, 640)
    _settle(app)
    assert mw.width() == target  # not refused (was: stuck at the full row's width)
    for key, btn in mw.ribbon_buttons.items():
        rect = QRect(btn.mapTo(mw.ribbon, QPoint(0, 0)), btn.size())
        assert mw.ribbon.rect().contains(rect), key
        assert btn.width() >= btn.sizeHint().width(), key


def test_compact_density_keeps_names_and_hysteresis(mw, app):
    top = mw.ribbon
    full = top.full_width()
    mw.resize(full - 10, 640)
    _settle(app)
    assert top.density() == "compact"
    for key, btn in mw.ribbon_buttons.items():
        assert btn.text() == key and btn.accessibleName() == key
        expected = (
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if btn.isChecked()
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        assert btn.toolButtonStyle() == expected, key
    mw.resize(full + 10, 640)  # inside the 24 px hysteresis band
    _settle(app)
    assert top.density() == "compact"
    mw.resize(full + 40, 640)
    _settle(app)
    assert top.density() == "full"
    for btn in mw.ribbon_buttons.values():
        assert btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon


def test_action_stack_is_as_wide_as_the_current_page(mw, app):
    _enter(mw, app, "Generate")
    mw.resize(mw.ribbon.full_width() + 200, 700)
    _settle(app)
    btn = mw.generate_all_button
    assert btn.isVisible()
    assert btn.width() == btn.sizeHint().width()
    assert mw.action_bar_stack.width() <= mw.action_bar_stack.currentWidget().sizeHint().width()
    _enter(mw, app, "Library")


# --------------------------------------------------------------------------- #
#  Stage pages
# --------------------------------------------------------------------------- #
def test_generate_page_offers_skeletons_only(mw, app):
    _enter(mw, app, "Generate")
    page = mw.action_bar_stack.currentWidget()
    assert mw.generate_all_button.parentWidget() is page
    assert mw.generate_button.parentWidget() is page
    assert mw.generate_button.text() == "Skeletons only"
    assert mw.generate_button.isVisible()
    _enter(mw, app, "Library")


def test_the_correct_page_clear_button_clears_the_edited_layer_and_follows_it(mw, app, tmp_path):
    import numpy as np

    from tests.test_correct_editor import W, new_project

    project = new_project(tmp_path)  # a.png: a model mask and a generated skeleton
    _enter(mw, app, "Correct")
    editor = mw.correct_editor
    editor.load_image(project.a, project.folder)
    _settle(app)
    button = editor.clear_button
    page = mw.action_bar_stack.currentWidget()
    try:
        assert button.parentWidget() is page and button.isVisible()
        assert button.text() == "Clear mask"
        mask = editor.layers["mask"].document.history
        skeleton = editor.layers["skeleton"].document.model
        assert mask.plane.any() and skeleton.mask[110, 20 : W - 20].all()

        button.click()
        assert not mask.plane.any() and skeleton.mask.any()  # the edited layer only

        editor.set_active_layer("skeleton")
        assert button.text() == "Clear skeleton"
        button.click()
        assert not np.any(skeleton.mask)
    finally:
        for layer in editor.layers.values():
            layer.revert()  # back to the files, so leaving asks nothing
        editor.reset()
        _enter(mw, app, "Library")


# --------------------------------------------------------------------------- #
#  UXS-12: the checked stage shows the accent icon
# --------------------------------------------------------------------------- #
def test_checked_stage_icon_differs_from_unchecked(mw):
    icon = mw.ribbon_buttons["Library"].icon()
    size = QSize(15, 15)
    off = icon.pixmap(size, QIcon.Mode.Normal, QIcon.State.Off).toImage()
    on = icon.pixmap(size, QIcon.Mode.Normal, QIcon.State.On).toImage()
    assert off != on


# --------------------------------------------------------------------------- #
#  PV-04 / UI-05 / RB-14: Visualize selects, never toggles
# --------------------------------------------------------------------------- #
@pytest.fixture
def viz_calls(mw, monkeypatch):
    calls = []
    monkeypatch.setattr(mw, "show_root_length_visualization", lambda: calls.append("show:length"))
    monkeypatch.setattr(mw, "show_root_area_visualization", lambda: calls.append("show:area"))
    for name in (
        "toggle_root_length_visualization",
        "toggle_root_area_visualization",
        "close_root_length_visualization",
        "close_root_area_visualization",
    ):
        monkeypatch.setattr(mw, name, lambda n=name: calls.append(n), raising=False)
    # No folder is open here: let the dashboards decide instead of the "No images" page.
    monkeypatch.setattr(mw, "_missing_dashboard_data", lambda kind: None)
    mw.ribbon.reset_visualize_selection()
    return calls


def test_entering_visualize_shows_the_highlighted_dashboard(mw, app, viz_calls):
    # W5: entering opens the highlighted segment's dashboard (it used to show the
    # Library photo until the highlighted segment was clicked again).
    _enter(mw, app, "Visualize")
    assert viz_calls == ["show:length"]
    mw.action_bar_viz_seg.button("length").click()  # a re-click shows it again, never closes
    assert viz_calls == ["show:length", "show:length"]
    _enter(mw, app, "Library")


def test_segments_select_and_never_close(mw, app, viz_calls):
    _enter(mw, app, "Visualize")
    viz_calls.clear()  # entering showed the highlighted one (W5)
    seg = mw.action_bar_viz_seg
    for kind in ("area", "length", "area", "area"):
        seg.button(kind).click()
    assert viz_calls == ["show:area", "show:length", "show:area", "show:area"]
    assert seg.value() == "area"
    _enter(mw, app, "Library")


def test_reentering_visualize_shows_the_last_dashboard(mw, app, viz_calls):
    _enter(mw, app, "Visualize")
    mw.action_bar_viz_seg.button("area").click()
    _enter(mw, app, "Library")
    viz_calls.clear()
    _enter(mw, app, "Visualize")
    assert viz_calls == ["show:area"]
    _enter(mw, app, "Library")
