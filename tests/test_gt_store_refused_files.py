"""Class labels Pillow refuses are unreadable labels on the Correct page and in the export.

``read_tracing`` wrapped only ``OSError`` and ``ValueError``. Pillow refuses a PNG whose
header is over twice ``Image.MAX_IMAGE_PIXELS`` (about 179 Mpx) with
``DecompressionBombError`` and a broken chunk with ``SyntaxError``; neither is one of those,
so Correct's class-label open raised half-way through an image switch (the photo and the
labels of the previous image stayed on screen, under the unexpected-error dialog), and a
broken chunk ended the whole export. Both are now a ``GtFileError``: the page opens the
labels empty with the designed notice and keeps the file, and the export lists that image
in ``errors.txt`` and carries on.
"""

from __future__ import annotations

import os

import pytest
from PIL import Image

from app.gt.export import ExportOptions, export_dataset
from app.gt.store import gt_path
from app.gui.correct.classes_document import ClassesDocument
from app.gui.correct.layer import LayerStatus
from tests.test_gt_export import _STEMS, _build_project
from tests.test_gt_store import break_a_chunk, write_header_bomb

W, H = 64, 48
NOT_SHOWN = (
    "Saved class labels could not be read: they are not shown and are kept unless you save "
    "over them"
)


@pytest.fixture
def image(tmp_path):
    """A photo with an empty ``gt/`` next to it."""
    path = str(tmp_path / "plot_T1_L1_2024.01.01_120000.png")
    Image.new("RGB", (W, H), (10, 20, 30)).save(path)
    os.makedirs(os.path.dirname(gt_path(path)))
    return path


def _too_large(path: str) -> None:
    write_header_bomb(path)


def _broken(path: str) -> None:
    Image.new("P", (W, H)).save(path)
    break_a_chunk(path)


@pytest.mark.parametrize("make", [_too_large, _broken], ids=["too_large", "broken_chunk"])
def test_correct_opens_refused_labels_empty_with_the_notice(image, make):
    labels = gt_path(image)
    make(labels)
    before = open(labels, "rb").read()
    doc = ClassesDocument()

    assert ClassesDocument.read(image, (W, H)).error == "unreadable"  # the read-ahead's read
    notices = doc.open(image, (W, H))

    assert [(n.message, n.kind) for n in notices] == [(NOT_SHOWN, "warn")]
    assert doc.image_path == image and doc.unloaded_file() and not doc.history.plane.any()
    assert doc.status() == LayerStatus("error", "Class labels not shown", NOT_SHOWN)
    assert open(labels, "rb").read() == before  # kept unless saved over


def test_a_broken_tracing_is_that_images_export_error(tmp_path):
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    broken = _STEMS[3]
    break_a_chunk(gt_path(images[broken]))
    out_dir = tmp_path / "out"

    result = export_dataset(
        project_dir, images, registry, ExportOptions(str(out_dir), only_complete=False)
    )

    assert [stem for stem, _ in result.errors] == [broken]
    assert "broken PNG file" in result.errors[0][1]
    assert result.exported == len(_STEMS) - 1
    assert not (out_dir / "masks" / f"{broken}.png").exists()
    assert (out_dir / "errors.txt").read_text(encoding="utf-8").startswith(f"{broken}\t")
