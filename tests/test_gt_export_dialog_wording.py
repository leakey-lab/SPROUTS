"""The Export dataset dialog's count sentence agrees with its number (V11, 2026-09-30).

It read "1 of 40 images have a GT tracing" (now "have class labels"): the verb followed the folder's image count.
The noun follows the image count ("1 of 1 image", "1 of 40 images") and the verb the
traced count ("1 ... has", "2 ... have").
"""

from __future__ import annotations

import os

import pytest

from app.gt import default_registry, gt_dir
from app.gui.export_dataset_dialog import ExportDatasetDialog
from tests.qt_teardown import delete_widgets


@pytest.fixture
def made(app):
    dialogs = []
    yield dialogs
    delete_widgets(dialogs)


def _project(tmp_path, n_images: int, n_traced: int) -> dict[str, str]:
    """``n_images`` image names, the first ``n_traced`` with a ``gt/<stem>.png`` (the
    dialog only counts the names)."""
    folder = gt_dir(str(tmp_path))
    os.makedirs(folder, exist_ok=True)
    images = {}
    for i in range(n_images):
        stem = f"plot_T1_L{i + 1}_2024.01.01_120000"
        images[stem] = str(tmp_path / f"{stem}.png")
        if i < n_traced:
            open(os.path.join(folder, f"{stem}.png"), "wb").close()
    return images


@pytest.mark.parametrize(
    "n_images, n_traced, sentence",
    [
        (40, 1, "1 of 40 images has class labels."),
        (40, 2, "2 of 40 images have class labels."),
        (40, 40, "40 of 40 images have class labels."),
        (1, 1, "1 of 1 image has class labels."),
        (2, 1, "1 of 2 images has class labels."),
    ],
)
def test_the_count_sentence_agrees_with_its_number(made, tmp_path, n_images, n_traced, sentence):
    dialog = ExportDatasetDialog(
        str(tmp_path), _project(tmp_path, n_images, n_traced), default_registry()
    )
    made.append(dialog)

    assert dialog.traced_label.text() == sentence


def test_no_traced_image_still_says_why_export_is_off(made, tmp_path):
    dialog = ExportDatasetDialog(str(tmp_path), _project(tmp_path, 3, 0), default_registry())
    made.append(dialog)

    assert dialog.traced_label.text().startswith("No image in this folder has class labels")
    assert not dialog.export_button.isEnabled()
