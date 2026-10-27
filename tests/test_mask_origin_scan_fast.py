"""The pre-Generate scan lists ``mask/`` once and opens no mask file.

Before W5-T5 it read the 26-byte PNG header of every mask (about 1.4 s for 46k, on a thread
pool) to split hand-saved from model masks; nothing read the split after WG-T7, so the
scan is the folder listing and ``count`` is the number of masks.

The overwrite prompt (WG-T7) shows only how many masks exist: "Replace all predictions" is
the same button whatever the folder holds.
"""

from __future__ import annotations

import builtins
import os

import pytest
from PIL import Image

from app.inference import mask_origin
from app.inference.mask_origin import ExistingMasks, scan_existing_masks
from app.project_layout import ProjectLayout


def test_the_scan_opens_no_mask_file(tmp_path, monkeypatch):
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    for name in ("a.png", "B.PNG", "c.jpg", "notes.txt"):
        Image.new("L", (8, 6)).save(mask_dir / name, "PNG")
    (mask_dir / "a_folder.png").mkdir()  # not a file: never a mask
    opened = []

    def spy(real):
        # os.open and the builtin open (PIL, plain reads) both record what they open
        return lambda p, *a, **k: opened.append(p) or real(p, *a, **k)

    monkeypatch.setattr(os, "open", spy(os.open))
    monkeypatch.setattr(builtins, "open", spy(builtins.open))

    found = scan_existing_masks(ProjectLayout(str(tmp_path)))

    assert found.stems == frozenset({"a", "B", "c"}) and found.count == 3
    assert opened == []


@pytest.mark.parametrize("limit_to_images", [False, True])
def test_a_large_folder_is_counted_by_name(tmp_path, limit_to_images):
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    for i in range(3000):
        (mask_dir / f"T{i:05d}.png").write_bytes(b"")  # unreadable as a PNG: still a mask
    layout = ProjectLayout(str(tmp_path))
    wanted = {f"T{i:05d}" for i in range(0, 3000, 3)}

    found = scan_existing_masks(layout, wanted if limit_to_images else None)

    assert found.count == (len(wanted) if limit_to_images else 3000)


def test_masks_named_as_their_images_are_not_remapped(tmp_path, monkeypatch):
    """Every mask named as one of the images (nearly always): the count needs no remap."""
    mask_dir = tmp_path / "mask"
    mask_dir.mkdir()
    for i in range(300):
        (tmp_path / f"T{i:05d}.jpg").write_bytes(b"")
        if i % 3:
            (mask_dir / f"T{i:05d}.png").write_bytes(b"")
    layout = ProjectLayout(str(tmp_path))
    remaps = []
    real = mask_origin.key_to_stems
    monkeypatch.setattr(mask_origin, "key_to_stems", lambda *a: remaps.append(a) or real(*a))

    found = scan_existing_masks(layout, layout.scan_images())

    assert found.stems == frozenset(f"T{i:05d}" for i in range(300) if i % 3)
    assert remaps == []
    # A mask of no image: remapped, then left out of the count.
    (mask_dir / "orphan.png").write_bytes(b"")
    assert scan_existing_masks(layout, layout.scan_images()) == found and len(remaps) == 1


# --------------------------------------------------------------------------- prompt label
def _replace_button_text(existing_count):
    """The text of the prompt's destructive button (the prompt is cancelled)."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from app.handlers import mask_generation_handler as mgh

    texts = []

    def read_and_cancel():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(10, read_and_cancel)
            return
        role = QMessageBox.ButtonRole.DestructiveRole
        texts.extend(b.text() for b in box.buttons() if box.buttonRole(b) == role)
        box.escapeButton().click()

    QTimer.singleShot(0, read_and_cancel)
    assert mgh.ask_overwrite_policy(None, existing_count, 5000) is None
    [text] = texts
    return text


def test_the_replace_button_never_counts_hand_corrected_masks(qapp):
    # mask/ holds predictions only (WG-T7): the prompt shows how many exist, nothing more.
    few = ExistingMasks(stems=frozenset({"a", "b", "c"}))
    assert _replace_button_text(few.count) == "Replace all predictions"

    many = ExistingMasks(stems=frozenset(f"m{i}" for i in range(1234)))
    assert _replace_button_text(many.count) == "Replace all predictions"
