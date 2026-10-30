"""Generate and Measure say what a run would work on (W12).

The status line's idle text names, on the Generate stage, the images without a mask or a
skeleton and, on the Measure stage, the skeletons and masks to measure and whether each
metric's CSV is current (inputs written after it are counted off the GUI thread).
"""

from __future__ import annotations

import os
import time

import pytest
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from app import config
from app.gui.stage_summary import (
    Freshness,
    generate_text,
    input_freshness,
    measure_text,
)
from tests.qt_teardown import delete_widgets
from tests.window_support import build_window, open_folder, quiet_window

STEMS = [f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM" for d in (11, 12, 13, 14)]

pytestmark = pytest.mark.usefixtures("no_temp_sweep")


# ------------------------------------------------------------------------ the phrases
def test_generate_text_counts_what_is_missing():
    images = ["a", "b", "c", "d"]
    assert generate_text(images, {"a"}, {"a", "b"}) == (
        "4 images · 3 without a mask · 2 without a skeleton"
    )
    assert generate_text(["a"], {"a"}, {"a"}) == "1 image · every image has a mask and a skeleton"


def test_measure_text_before_and_after_the_folders_are_read():
    assert measure_text(20, 12) == "20 skeletons · 12 masks"
    assert measure_text(0, 1) == "no skeletons to measure · 1 mask"
    length = Freshness(inputs=20, measured=True, changed=0)
    area = Freshness(inputs=12, measured=True, changed=3)
    assert measure_text(20, 12, length, area) == (
        "20 skeletons: measured, current · 12 masks: 3 changed since measured"
    )
    assert measure_text(1, 0, Freshness(1, False), Freshness(0, False)) == (
        "1 skeleton: not measured yet · no masks to measure"
    )


def _shift(path, seconds: float) -> None:
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + int(seconds * 1e9)))


def test_input_freshness_counts_inputs_written_after_the_csv(tmp_path):
    folder = tmp_path / "mask"
    folder.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        (folder / name).write_bytes(b"x")
        _shift(folder / name, -60)
    (folder / ".a.png.0123abcd.tmp").write_bytes(b"x")  # a write in progress: not an input
    (folder / "notes.txt").write_bytes(b"x")
    csv = folder / "root_areas.csv"
    csv.write_text("Image\n")
    _shift(folder / "b.png", 120)  # saved after the Measure run

    assert input_freshness(str(folder), ".png", str(csv)) == Freshness(3, True, 1)
    assert input_freshness(str(folder), ".png", None) == Freshness(3, False, 0)
    assert input_freshness(str(tmp_path / "gone"), ".png", None) == Freshness(0, False, 0)


# ------------------------------------------------------------------------ the window
@pytest.fixture
def window(app, tmp_path):
    made = [build_window(tmp_path / "s.ini", show=False)]
    made[0].show()
    yield made[0]
    delete_widgets(made, prepare=quiet_window)
    QApplication.processEvents()


def _project(root):
    """Four images; two masks, three skeletons; root_areas.csv older than one mask."""
    root.mkdir()
    (root / "mask").mkdir()
    (root / "skeletons").mkdir()
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    for stem in STEMS:
        assert image.save(str(root / f"{stem}.jpg"))
    for stem in STEMS[:2]:
        assert image.save(str(root / "mask" / f"{stem}.png"))
    for stem in STEMS[:3]:
        assert image.save(str(root / "skeletons" / f"{stem}_fake.png"))
    for path in [*(root / "mask").iterdir(), *(root / "skeletons").iterdir()]:
        _shift(path, -60)
    areas = root / "mask" / config.ROOT_AREAS_CSV
    areas.write_text(",".join(config.AREA_CSV_HEADERS) + "\n", encoding="utf-8")
    _shift(root / "mask" / f"{STEMS[1]}.png", 120)
    return root


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _enter(window, stage: str) -> None:
    window.ribbon_buttons[stage].click()
    QApplication.processEvents()


def test_the_generate_stage_says_what_is_missing(window, tmp_path):
    open_folder(window, _project(tmp_path / "p"), len(STEMS))

    _enter(window, "Generate")

    assert window.statusline_message.text() == (
        "Ready · 4 images · 2 without a mask · 1 without a skeleton"
    )
    _enter(window, "Library")
    assert window.statusline_message.text() == "Ready · 4 images"


def test_the_measure_stage_says_whether_each_csv_is_current(window, tmp_path):
    open_folder(window, _project(tmp_path / "p"), len(STEMS))

    _enter(window, "Measure")

    assert _wait(
        lambda: (
            window.statusline_message.text()
            == "Ready · 3 skeletons: not measured yet · 2 masks: 1 changed since measured"
        )
    ), window.statusline_message.text()


def test_a_method_note_says_measure_again():
    """O3: a CSV measured with another field of view or length method asks to measure again."""
    length = Freshness(inputs=20, measured=True, method_note="Measured with field of view …")
    area = Freshness(inputs=12, measured=True)
    assert measure_text(20, 12, length, area) == (
        "20 skeletons: measured with another field of view or length method: measure again"
        " · 12 masks: measured, current"
    )
    # Not measured yet: nothing was measured with another field of view.
    assert measure_text(1, 0, Freshness(1, False, method_note="x"), None) == (
        "1 skeleton: not measured yet · no masks to measure"
    )
