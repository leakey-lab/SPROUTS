"""NormalizationControls (Enhance popover content): layout fits the 264 px popover (UI-07),
live debounced preview + "Show original" + sticky state (PE-08, UXE-14), per-user validated
defaults (RB-13, UI-19, PE-14) and the editor API apply_to / is_active / enhancement_changed
(AR-13)."""

from __future__ import annotations

import json
import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from app.enhancement import (  # noqa: E402
    METHOD_CLAHE,
    METHOD_STRETCH,
    EnhanceDefaultsStore,
    EnhanceSettings,
    enhance,
)
from app.gui.image_normalization_interface import NormalizationControls  # noqa: E402
from app.gui.widgets import EnhancePopover  # noqa: E402
from resources.resource_utils import resource_path  # noqa: E402

FACTORY = resource_path("config", "normalization_defaults.json")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def user_config(tmp_path, monkeypatch):
    """Every control built here stores its defaults under tmp_path, never in the repo."""
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


def _store(tmp_path, user=None, factory=None):
    user_path = tmp_path / "u.json"
    factory_path = tmp_path / "f.json"
    if user is not None:
        user_path.write_text(json.dumps(user), encoding="utf-8")
    if factory is not None:
        factory_path.write_text(json.dumps(factory), encoding="utf-8")
    return EnhanceDefaultsStore(user_path=str(user_path), factory_path=str(factory_path))


def _image():
    rng = np.random.default_rng(3)
    return rng.normal(90, 10, (60, 80, 3)).clip(0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- defaults (RB-13)
@pytest.mark.parametrize(
    "payload",
    [
        {"clahe_clip": 8.5},
        {"clahe_tile": "14"},
        {"clahe_clip": 10**12},
        {"contrast_lower": [1]},
        {"contrast_upper": None},
        {"last_method": 3},
        ["not", "a", "dict"],
    ],
)
def test_bad_saved_defaults_never_break_construction(app, tmp_path, payload):
    controls = NormalizationControls(store=_store(tmp_path, user=payload))
    for slider in (
        controls.clip_slider,
        controls.tile_slider,
        controls.lower_slider,
        controls.upper_slider,
    ):
        assert slider.minimum() <= slider.value() <= slider.maximum()
    assert controls.method_combo.currentText() in (METHOD_CLAHE, METHOD_STRETCH)


def test_save_as_default_writes_the_user_config_and_not_the_repo(app, user_config):
    before = open(FACTORY, "rb").read()
    mtime = os.stat(FACTORY).st_mtime_ns
    controls = NormalizationControls()
    controls.method_combo.setCurrentText(METHOD_STRETCH)
    controls.clip_slider.setValue(31)
    controls.lower_slider.setValue(7)
    controls.save_current_as_defaults()
    saved = json.loads((user_config / "normalization_defaults.json").read_text("utf-8"))
    assert saved["clahe_clip"] == 31 and saved["contrast_lower"] == 7
    assert saved["last_method"] == METHOD_STRETCH
    assert open(FACTORY, "rb").read() == before and os.stat(FACTORY).st_mtime_ns == mtime
    again = NormalizationControls()
    assert again.settings() == controls.settings()


def test_reset_restores_the_saved_defaults_not_hardcoded_ones(app, tmp_path):
    store = _store(tmp_path, user={"clahe_clip": 12, "clahe_tile": 4, "last_method": "CLAHE"})
    controls = NormalizationControls(store=store)
    controls.clip_slider.setValue(40)
    controls.tile_slider.setValue(15)
    controls.reset_to_defaults()
    assert (controls.clip_slider.value(), controls.tile_slider.value()) == (12, 4)
    assert controls.clip_label.text().endswith("1.2")


# --------------------------------------------------------------------------- layout (UI-07)
def test_every_button_fits_inside_the_264px_popover_with_the_app_theme(app):
    previous = app.styleSheet()
    with open(resource_path("resources", "themes", "dark_theme.qss"), encoding="utf-8") as fh:
        app.setStyleSheet(fh.read())
    host = QWidget()
    host.resize(1280, 800)
    try:
        popover = EnhancePopover(host)
        controls = NormalizationControls()
        popover.set_content(controls)
        host.show()
        QTest.qWaitForWindowExposed(host)
        popover.toggle()
        for method in (METHOD_CLAHE, METHOD_STRETCH):
            controls.method_combo.setCurrentText(method)
            QApplication.processEvents()
            assert popover.width() == 264
            for button in controls.findChildren(QPushButton):
                if button.isVisible():
                    assert button.width() >= button.sizeHint().width(), button.text()
                    assert button.toolTip(), button.text()
    finally:
        host.close()
        app.setStyleSheet(previous)


# --------------------------------------------------------------------------- live preview
def test_rapid_slider_changes_emit_one_debounced_update(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path), preview_delay_ms=40)
    hits = []
    controls.enhancement_changed.connect(lambda: hits.append(1))
    for value in (11, 12, 13, 14, 15):
        controls.clip_slider.setValue(value)
    assert hits == []  # nothing rendered on the per-tick path
    QTest.qWait(150)
    assert hits == [1]
    assert controls.is_active()


def test_apply_activates_immediately_and_original_toggles_back(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path))
    hits = []
    controls.enhancement_changed.connect(lambda: hits.append(1))
    assert not controls.is_active()
    controls.apply_button.click()
    assert controls.is_active() and hits == [1]
    controls.original_check.setChecked(True)
    assert not controls.is_active() and hits == [1, 1]
    controls.tile_slider.setValue(controls.tile_slider.value() + 1)
    assert not controls.original_check.isChecked()  # tweaking a slider shows the result again
    assert controls.is_active()


def test_apply_to_is_identity_until_active_then_enhances(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path))
    img = _image()
    assert controls.apply_to(img) is img
    controls.method_combo.setCurrentText(METHOD_STRETCH)
    controls.apply_button.click()
    assert np.array_equal(controls.apply_to(img), enhance(img, controls.settings()))
    assert controls.settings().method == METHOD_STRETCH


def test_set_settings_updates_every_control(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path))
    wanted = EnhanceSettings(method=METHOD_STRETCH, clip=9, tile=3, lower=10, upper=90)
    controls.set_settings(wanted)
    assert controls.settings() == wanted
    assert controls.contrast_group.isVisibleTo(controls)
    assert not controls.clahe_group.isVisibleTo(controls)


def test_display_pixmap_raw_then_enhanced_and_none_for_missing(app, tmp_path):
    path = tmp_path / "img.png"
    img = _image()
    cv2.imencode(".png", img)[1].tofile(str(path))
    controls = NormalizationControls(store=_store(tmp_path))
    raw = controls.display_pixmap(str(path))
    assert raw is not None and (raw.width(), raw.height()) == (80, 60)
    controls.apply_button.click()
    enhanced = controls.display_pixmap(str(path))
    assert enhanced.toImage() != raw.toImage()
    controls.original_check.setChecked(True)
    assert controls.display_pixmap(str(path)).toImage() == raw.toImage()
    assert controls.display_pixmap(str(tmp_path / "missing.png")) is None


def test_legacy_public_surface_is_kept(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path))
    for name in (
        "method_combo",
        "clip_slider",
        "tile_slider",
        "lower_slider",
        "upper_slider",
        "apply_button",
        "save_defaults_button",
        "restore_defaults_button",
        "default_values",
        "clahe_group",
        "contrast_group",
    ):
        assert hasattr(controls, name), name
    controls.set_image(None)
    assert not controls.apply_button.isEnabled()
    controls.set_image(_image())
    assert controls.apply_button.isEnabled()
