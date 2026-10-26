"""Restoring a retired class (O24) and the export manifest's pixel counts and EXIF tag (O24).

A retired class keeps its id, colour and place, so ``Registry.restore`` and the class editor's
Restore button bring it back exactly; the next export gives its pixels their own id again.
``manifest.csv`` gains ``exif_orientation`` (the source image's tag 0x0112, recorded and never
applied: ``tests/test_exif_policy.py``) and one pixel count per exported class.
"""

from __future__ import annotations

import csv
import os
import struct

import numpy as np
import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QWidget  # noqa: E402

from app.config import GT_IGNORE_ID  # noqa: E402
from app.gt.export import ExportOptions, export_dataset  # noqa: E402
from app.gt.registry import RegistryError, default_registry  # noqa: E402
from app.gt.store import Tracing, gt_path, write_tracing  # noqa: E402
from app.gui.class_editor_dialog import ClassEditorDialog  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

W, H = 640, 480
NODULE_PIXELS = 59 * 15  # 885
_STEMS = ("plot_T1_L1_2024.01.01_120000", "plot_T2_L1_2024.01.01_120000")
_MADE: list[QWidget] = []


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _delete_test_widgets(app):
    yield
    delete_widgets(_MADE)


def _ids() -> np.ndarray:
    """640 x 480 ids: a root bar, 885 nodule pixels, a root tip block and an Ignore block."""
    ids = np.zeros((H, W), np.uint8)
    ids[100:110, 50:400] = 1  # root: 10 x 350
    ids[200:215, 300:359] = 2  # nodule: 15 x 59 = 885
    ids[300:304, 100:125] = 3  # root tip: 4 x 25
    ids[400:410, 500:520] = GT_IGNORE_ID  # ignore: 10 x 20
    return ids


@pytest.fixture
def gt_project_with_nodules(tmp_path):
    """Two traced 640 x 480 photos; the second is a JPEG stored with EXIF Orientation = 6.

    Returns ``(project_dir, images)`` with every class reviewed on both photos.
    """
    project = tmp_path / "project"
    project.mkdir()
    registry = default_registry()
    reviewed = frozenset(c.key for c in registry.labelled())
    images: dict[str, str] = {}
    first = project / f"{_STEMS[0]}.png"
    Image.new("RGB", (W, H), (10, 20, 30)).save(first)
    second = project / f"{_STEMS[1]}.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (W, H), (10, 20, 30)).save(second, exif=exif.tobytes())
    for stem, path in zip(_STEMS, (first, second), strict=True):
        images[stem] = str(path)
        write_tracing(gt_path(str(path)), Tracing(_ids(), reviewed), registry)
    return str(project), images


def _export(project_dir, images, registry, out_dir):
    result = export_dataset(project_dir, images, registry, ExportOptions(str(out_dir)))
    assert result.exported == 2 and not result.errors
    return result


def _manifest(out_dir) -> list[dict[str, str]]:
    with open(out_dir / "manifest.csv", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _exported_mask(out_dir, stem) -> np.ndarray:
    with Image.open(out_dir / "masks" / f"{stem}.png") as mask:
        return np.asarray(mask)


def test_restore_puts_a_retired_class_back_with_its_id_colour_and_place():
    reg = default_registry().retire("nodule")
    back = reg.restore("nodule")
    assert back.by_key("nodule") == default_registry().by_key("nodule")
    assert [c.key for c in back.classes] == [c.key for c in default_registry().classes]
    with pytest.raises(RegistryError):
        reg.restore("root")
    with pytest.raises(RegistryError):
        reg.restore("nothing")


def test_restore_keeps_a_class_added_later_in_its_place():
    reg = default_registry().add(key="dead_root", name="Dead root", color="#111111")
    retired = reg.retire("nodule").retire("dead_root")
    assert retired.restore("dead_root").restore("nodule") == reg


def test_the_class_editor_lists_retired_classes_with_a_restore_button(app):
    dialog = ClassEditorDialog(default_registry().retire("nodule"))
    _MADE.append(dialog)
    assert list(dialog.restore_buttons) == ["nodule"]
    dialog.restore_buttons["nodule"].click()
    dialog._on_accept()  # what OK runs: accept() alone would not record the result
    assert not dialog.result_registry().by_key("nodule").retired


def test_the_class_editor_has_no_restore_button_without_retired_classes(app):
    dialog = ClassEditorDialog(default_registry())
    _MADE.append(dialog)
    assert dialog.restore_buttons == {}


def test_a_restored_class_returns_to_the_active_rows(app):
    dialog = ClassEditorDialog(default_registry().retire("nodule"))
    _MADE.append(dialog)
    assert "nodule" not in dialog._rows
    dialog.restore_buttons["nodule"].click()
    assert "nodule" in dialog._rows
    assert dialog.restore_buttons == {}


def test_export_after_restore_gives_the_class_its_own_id_again(gt_project_with_nodules, tmp_path):
    project_dir, images = gt_project_with_nodules
    retired = default_registry().retire("nodule")
    nodule = _ids() == 2

    _export(project_dir, images, retired, tmp_path / "retired")
    assert np.all(_exported_mask(tmp_path / "retired", _STEMS[0])[nodule] == GT_IGNORE_ID)

    _export(project_dir, images, retired.restore("nodule"), tmp_path / "restored")
    assert np.all(_exported_mask(tmp_path / "restored", _STEMS[0])[nodule] == 2)


def test_the_manifest_has_per_class_pixel_counts_and_exif_orientation(
    gt_project_with_nodules, tmp_path
):
    project_dir, images = gt_project_with_nodules
    _export(project_dir, images, default_registry(), tmp_path / "out")
    rows = _manifest(tmp_path / "out")
    assert list(rows[0]) == [
        "stem",
        "source_image",
        "split",
        "tube",
        "position",
        "date",
        "classes_present",
        "exif_orientation",
        "px_background",
        "px_root",
        "px_nodule",
        "px_root_tip",
        "px_ignore",
    ]
    assert int(rows[0]["px_nodule"]) == NODULE_PIXELS
    assert sum(int(rows[0][c]) for c in rows[0] if c.startswith("px_")) == 640 * 480
    assert rows[0]["exif_orientation"] == "" and rows[1]["exif_orientation"] == "6"


def test_the_counts_follow_the_exported_ids(gt_project_with_nodules, tmp_path):
    """A retired class counts as ignore, the rest move up: the counts are of the remapped ids."""
    project_dir, images = gt_project_with_nodules
    _export(project_dir, images, default_registry().retire("nodule"), tmp_path / "out")
    row = _manifest(tmp_path / "out")[0]
    assert list(row)[8:] == ["px_background", "px_root", "px_root_tip", "px_ignore"]
    assert int(row["px_root"]) == 10 * 350
    assert int(row["px_root_tip"]) == 4 * 25
    assert int(row["px_ignore"]) == NODULE_PIXELS + 10 * 20
    assert int(row["px_background"]) + 3500 + 100 + NODULE_PIXELS + 200 == 640 * 480


def test_the_exif_tag_is_recorded_and_never_applied(gt_project_with_nodules, tmp_path):
    project_dir, images = gt_project_with_nodules
    _export(project_dir, images, default_registry(), tmp_path / "out")
    exported = tmp_path / "out" / "images" / f"{_STEMS[1]}.jpg"
    with Image.open(exported) as image:
        assert image.size == (W, H)  # the stored grid
    assert _exported_mask(tmp_path / "out", _STEMS[1]).shape == (H, W)


def _corrupt_exif_jpeg(path) -> None:
    """A 640 x 480 JPEG whose APP1 (EXIF) segment is garbage."""
    Image.new("RGB", (W, H), (10, 20, 30)).save(path)
    data = path.read_bytes()
    garbage = b"Exif\x00\x00" + b"II*\x00\xff\xff\xff\x7f" + b"\xde\xad\xbe\xef" * 8
    segment = b"\xff\xe1" + (len(garbage) + 2).to_bytes(2, "big") + garbage
    path.write_bytes(data[:2] + segment + data[2:])  # right after SOI


@pytest.mark.filterwarnings("ignore:Corrupt EXIF data:UserWarning")
def test_a_corrupt_exif_block_leaves_the_column_blank_and_the_export_completes(
    gt_project_with_nodules, tmp_path, monkeypatch
):
    project_dir, images = gt_project_with_nodules
    broken = tmp_path / "project" / f"{_STEMS[1]}.jpg"
    _corrupt_exif_jpeg(broken)
    _export(project_dir, images, default_registry(), tmp_path / "out")
    assert _manifest(tmp_path / "out")[1]["exif_orientation"] == ""

    # Whatever Pillow raises while reading the tag (here a struct.error) is a blank, too.
    def boom(self):
        raise struct.error("bad EXIF")

    monkeypatch.setattr(Image.Image, "getexif", boom)
    _export(project_dir, images, default_registry(), tmp_path / "out2")
    assert [row["exif_orientation"] for row in _manifest(tmp_path / "out2")] == ["", ""]


def test_a_refused_restore_is_a_transient_error_that_never_blocks_ok(app):
    dialog = ClassEditorDialog(default_registry())
    _MADE.append(dialog)
    dialog._on_restore_clicked("root")  # not retired: the registry refuses
    assert "not retired" in dialog._error_label.text()
    assert dialog._error is None
    assert dialog._button_box.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
