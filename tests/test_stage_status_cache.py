"""Generate's idle text is counted once per change of the outputs, not on every refresh (V8).

The status line asks :meth:`StageStatus.text` for its idle text on every refresh while
Generate is on screen (the stage switch, every status message that clears, every job state
change), and each call walked all image names twice: 21.7 ms to enter Generate on a 46k
project in the walkthrough, against 2.8 ms before W12. The phrase is now kept until the
outputs change: another folder or a re-read after a run (new collections), or a mask saved
or cleared in an editor (the mask dict changes size in place).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui import stage_status  # noqa: E402
from app.gui.stage_status import StageStatus  # noqa: E402
from app.gui.stage_summary import generate_text  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

NAMES = [
    f"24EFCAM1_T{t:03d}_L{p:03d}_2024.07.18_111530_001_SMM" for t in range(1, 5) for p in (1, 2)
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def counted(monkeypatch):
    """Every :func:`generate_text` the status asks for, as its arguments."""
    calls = []

    def counting(images, masks, skeletons):
        calls.append((images, masks, skeletons))
        return generate_text(images, masks, skeletons)

    monkeypatch.setattr(stage_status, "generate_text", counting)
    return calls


@pytest.fixture
def status(app):
    made = StageStatus()
    yield made
    made.deleteLater()
    del made
    flush_deleted()


def library(masked: int = 3, skeletons: int = 5) -> SimpleNamespace:
    """An ImageManager stand-in: 8 images, ``masked`` with a mask, ``skeletons`` with one."""
    return SimpleNamespace(
        images={name: f"/p/{name}.jpg" for name in NAMES},
        masks={name: f"/p/mask/{name}.png" for name in NAMES[:masked]},
        skeleton_stems=set(NAMES[:skeletons]),
    )


def test_repeated_refreshes_count_once(status, counted):
    manager = library()
    first = status.text("Generate", manager)
    for _ in range(20):
        assert status.text("Generate", manager) == first
    assert first == "8 images · 5 without a mask · 3 without a skeleton"
    assert len(counted) == 1


def test_a_mask_saved_or_cleared_in_place_is_counted_again(status, counted):
    manager = library()
    status.text("Generate", manager)

    manager.masks[NAMES[5]] = "/p/mask/new.png"  # ImageManager.set_mask(stem, path)
    assert status.text("Generate", manager) == "8 images · 4 without a mask · 3 without a skeleton"
    manager.masks.pop(NAMES[0])  # ImageManager.set_mask(stem, None)
    manager.masks.pop(NAMES[1])
    assert status.text("Generate", manager) == "8 images · 6 without a mask · 3 without a skeleton"
    assert len(counted) == 3


def test_outputs_read_again_are_counted_again_even_at_the_same_sizes(status, counted):
    """A run's outputs are re-read into new collections (``refresh_outputs``): same sizes,
    other images."""
    manager = library(masked=3)
    status.text("Generate", manager)
    manager.masks = {name: f"/p/mask/{name}.png" for name in NAMES[:3]}
    manager.skeleton_stems = set(NAMES[3:8])
    status.text("Generate", manager)
    manager.images = dict(manager.images)
    status.text("Generate", manager)
    assert [call[1] is manager.masks for call in counted] == [False, True, True]
    assert len(counted) == 3


def test_another_project_is_counted_afresh(status, counted):
    manager = library()
    status.text("Generate", manager)
    status.reset()
    assert status.text("Generate", manager) == generate_text(
        manager.images, manager.masks, manager.skeleton_stems
    )
    assert len(counted) == 2
    other = library(masked=8, skeletons=8)
    assert status.text("Generate", other) == "8 images · every image has a mask and a skeleton"
    assert len(counted) == 3


def test_other_stages_and_an_empty_library_never_count(status, counted):
    manager = library()
    assert status.text("Measure", manager) == "5 skeletons · 3 masks"
    assert status.text("Library", manager) is None
    assert status.text("Generate", SimpleNamespace(images={})) is None
    assert counted == []


# ------------------------------------------------------------------------ the window
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from app.gui import image_manager
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    made = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.resize(1280, 800)
    made.show()
    QApplication.processEvents()
    yield made
    made.close()
    made.deleteLater()
    del made
    flush_deleted()


def test_the_generate_text_follows_a_mask_saved_in_an_editor(window, tmp_path, counted):
    import time

    project = tmp_path / "project"
    (project / "mask").mkdir(parents=True)
    photo = QImage(32, 24, QImage.Format.Format_RGB32)
    photo.fill(QColor(90, 60, 30))
    for name in NAMES[:4]:
        assert photo.save(str(project / f"{name}.jpg"))
    assert photo.save(str(project / "mask" / f"{NAMES[0]}.png"))
    window.open_path(str(project))
    end = time.monotonic() + 10
    while time.monotonic() < end and len(window._image_leaves()) < 4:
        QApplication.processEvents()
        time.sleep(0.01)
    window.ribbon_buttons["Generate"].click()
    QApplication.processEvents()
    line = window.statusline_message
    assert line.text() == "Ready · 4 images · 3 without a mask · 4 without a skeleton"
    for _ in range(10):
        window.statusline.refresh_idle()
    assert len(counted) == 1

    window.highlight_saved_mask(str(project / f"{NAMES[1]}.jpg"))  # an editor saved a mask
    window.statusline.refresh_idle()
    assert line.text() == "Ready · 4 images · 2 without a mask · 4 without a skeleton"
    window.unhighlight_cleared_mask(str(project / f"{NAMES[0]}.jpg"))  # and cleared one
    window.statusline.refresh_idle()
    assert line.text() == "Ready · 4 images · 3 without a mask · 4 without a skeleton"
