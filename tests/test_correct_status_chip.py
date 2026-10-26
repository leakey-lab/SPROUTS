"""The status chip keeps what identifies an image when it is narrow, and shows the image's
place in the library ("Image 12 / 40"). KNOWN_ISSUES #6: at 1280 x 800 the polyline prompt
shares the chip's row, and middle elision dropped the L-number and the date."""

from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFontMetricsF  # noqa: E402
from PyQt6.QtWidgets import QWidget  # noqa: E402

from app.gui.widgets.overlays import StatusChip, name_forms  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402
from tests.test_correct_editor import (  # noqa: E402
    editor_factory,
    install_guards,
    new_project,
    qapp,
)

CAMERA = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg"
CORE = "T001_L044_2024.07.18"


@pytest.fixture(scope="module")
def app():
    return qapp()


@pytest.fixture
def host(app):
    widget = QWidget()
    widget.resize(1100, 700)
    yield widget
    widget.deleteLater()
    del widget
    flush_deleted()


def chip_for(host, name: str = CAMERA, state: str = "clean") -> StatusChip:
    chip = StatusChip(host)
    chip.set_text(name)
    chip.set_state(state)
    return chip


def fitted_width(chip: StatusChip, budget: int) -> int:
    chip.fit_to_width(budget)
    chip.adjustSize()
    return chip.width()


# ------------------------------------------------------------------ name forms
def test_a_camera_name_drops_its_end_then_its_time_then_its_field():
    assert name_forms(CAMERA) == (
        CAMERA,
        "24EFCAM1_T001_L044_2024.07.18_111530…",
        "24EFCAM1_T001_L044_2024.07.18…",
        "…T001_L044_2024.07.18…",
    )


def test_a_name_without_a_field_or_an_end_has_fewer_forms():
    assert name_forms("T001_L044_2024.07.18_111530.png") == (
        "T001_L044_2024.07.18_111530.png",
        "T001_L044_2024.07.18_111530…",
        "T001_L044_2024.07.18…",
    )
    assert name_forms("T1_L2_2024.07.18") == ("T1_L2_2024.07.18",)


@pytest.mark.parametrize("name", ["a.png", "No image", "EXPT2_T1.jpg", "T001_L044_notadate.jpg"])
def test_any_other_name_has_no_shorter_form(name):
    assert name_forms(name) == (name,)


# ------------------------------------------------------------------ the chip
def budget(chip: StatusChip, name_px: int, position: str = "") -> int:
    """The chip width that leaves ``name_px`` for the name (and room for ``position``)."""
    chip.ensurePolished()
    margins, frame = chip._box.contentsMargins(), chip.contentsMargins()
    chrome = margins.left() + margins.right() + frame.left() + frame.right()
    spacing = chip._name_row.spacing()
    state = chip.state_label.sizeHint().width() + spacing if chip.state_label.text() else 0
    room = chip._position_width(position) + spacing if position else 0
    return chrome + state + name_px + room


def advance(chip: StatusChip, text: str) -> int:
    """The width the label gives ``text``: its fractional advance rounded up (a font with
    fractional advances, DejaVu on Linux, makes ``horizontalAdvance`` round to the nearest
    pixel, which is below the label's width when the fraction is under .5)."""
    chip.file_label.ensurePolished()
    return math.ceil(QFontMetricsF(chip.file_label.font()).horizontalAdvance(text))


def test_a_chip_with_room_shows_the_whole_name(host):
    chip = StatusChip(host, max_name_width=10_000)
    chip.set_text(CAMERA)

    assert chip.file_label.text() == CAMERA
    assert chip.file_label.toolTip() == CAMERA


@pytest.mark.parametrize("form", range(4))
def test_a_narrow_chip_keeps_the_tube_the_l_number_and_the_date(host, form):
    chip = StatusChip(host, max_name_width=10_000)
    chip.set_text(CAMERA)
    chip.set_state("clean")
    shown = name_forms(CAMERA)[form]

    for slack in (0, 1):  # exactly as wide as the form, and one pixel narrower
        width = fitted_width(chip, budget(chip, advance(chip, shown)) - slack)
        expected = shown if slack == 0 else (name_forms(CAMERA) + ("",))[form + 1]
        if expected:
            assert chip.file_label.text() == expected
            assert width <= budget(chip, advance(chip, shown))
        assert CORE in chip.file_label.text() or not expected
        assert chip.file_label.toolTip() == CAMERA


def test_the_narrowest_chip_still_starts_with_the_tube_and_the_l_number(host):
    chip = chip_for(host)

    fitted_width(chip, budget(chip, advance(chip, "…T001_L044…") + 3))  # Qt elides with slack

    assert chip.file_label.text().startswith("…T001_L044")


def test_other_names_are_still_elided_in_the_middle(host):
    name = "project_scan_" + "x" * 80 + "_final.jpg"
    chip = StatusChip(host, max_name_width=200)
    chip.set_text(name)

    shown = chip.file_label.text()
    assert "…" in shown and shown.startswith("proj") and shown.endswith(".jpg")


# ------------------------------------------------------------------ position
def test_the_position_in_the_library_follows_the_state(host):
    chip = chip_for(host)

    chip.set_position(12, 40)

    assert chip.position_label.text() == "Image 12 / 40" and not chip.position_label.isHidden()
    assert chip.position_label.toolTip() == "Image 12 of 40 in the library order"
    chip.set_position(None)
    assert chip.position_label.isHidden()


def test_a_narrow_chip_shortens_then_hides_the_position_before_the_name_loses_its_core(host):
    chip = StatusChip(host, max_name_width=10_000)
    chip.set_text(CAMERA)
    chip.set_state("clean")
    chip.set_position(12, 40)
    core = advance(chip, name_forms(CAMERA)[-1])
    cases = {
        "roomy": budget(chip, advance(chip, CAMERA), "Image 12 / 40"),
        "long position": budget(chip, core, "Image 12 / 40"),
        "short position": budget(chip, core, "12 / 40"),
        "no position": budget(chip, core),
    }
    shown = {}
    for case, width in cases.items():
        fitted_width(chip, width)
        position = "" if chip.position_label.isHidden() else chip.position_label.text()
        shown[case] = (position, chip.file_label.text())

    assert shown == {
        "roomy": ("Image 12 / 40", CAMERA),
        "long position": ("Image 12 / 40", "…T001_L044_2024.07.18…"),
        "short position": ("12 / 40", "…T001_L044_2024.07.18…"),
        "no position": ("", "…T001_L044_2024.07.18…"),
    }


# ------------------------------------------------------------------ the page
@pytest.fixture
def make_editor(app):
    yield from editor_factory()


@pytest.fixture(autouse=True)
def guards(monkeypatch, tmp_path):
    yield from install_guards(monkeypatch, tmp_path)


def test_the_page_shows_the_library_position_its_provider_gives(make_editor, tmp_path):
    project = new_project(tmp_path)
    editor = make_editor()
    order = [project.a, project.b]

    editor.set_position_provider(lambda path: (order.index(path) + 1, len(order)))
    editor.load_image(project.b, project.folder)

    chip = editor.status_chip
    assert chip.position_label.text() == "Image 2 / 2" and not chip.position_label.isHidden()
    editor.load_image(project.a, project.folder)
    assert chip.position_label.text() == "Image 1 / 2"


def test_without_a_provider_or_a_place_the_page_shows_no_position(make_editor, tmp_path, caplog):
    project = new_project(tmp_path)
    editor = make_editor(project.a, project.folder)
    assert editor.status_chip.position_label.isHidden()

    editor.set_position_provider(lambda path: None)  # not in the library
    assert editor.status_chip.position_label.isHidden()

    def broken(path):
        raise KeyError(path)

    editor.set_position_provider(broken)
    editor.load_image(project.b, project.folder)
    assert editor.status_chip.position_label.isHidden()
    assert "library position" in caplog.text


def test_correct_with_no_image_says_select_an_image(make_editor):
    editor = make_editor()
    assert editor.status_chip.hint_label.text() == "Select an image in the Library"
