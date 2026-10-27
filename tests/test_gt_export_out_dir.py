"""The GT export resolves its output folder once, to an absolute path.

On Windows ``D:out`` is drive-relative: the folder ``out`` in drive D's current folder.
The export dialog joins what is typed to the project folder, which keeps a path on another
drive as it is, so ``D:out`` with the project on ``C:`` stayed relative: the dialog showed
it, and the export wrote each file wherever that path pointed at the moment. Now the
dialog spells the folder out, and the export resolves it once when it starts and uses that
folder for every file, the checks and its result.
"""

from __future__ import annotations

import os

import pytest

from app.gt.export import ExportError, ExportOptions, export_dataset
from app.gui.export_dataset_dialog import ExportDatasetDialog
from tests.test_gt_export import _STEMS, _build_project

windows_only = pytest.mark.skipif(os.name != "nt", reason="drive letters are Windows paths")


def _drive(path: str) -> str:
    """``"C:"`` on Windows, ``""`` elsewhere."""
    return os.path.splitdrive(path)[0]


def test_a_drive_relative_folder_is_resolved_once_when_the_export_starts(tmp_path, monkeypatch):
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    start, later = tmp_path / "start", tmp_path / "later"
    start.mkdir()
    later.mkdir()
    monkeypatch.chdir(start)
    moved: list[str] = []

    def progress(_percent: int) -> None:
        if not moved:  # the working folder changes while the export runs
            moved.append(str(later))
            os.chdir(later)

    options = ExportOptions(out_dir=f"{_drive(str(tmp_path))}out", only_complete=False)
    result = export_dataset(project_dir, images, registry, options, progress)

    out = start / "out"
    assert moved and os.path.isabs(result.out_dir) and os.path.samefile(result.out_dir, out)
    assert result.errors == [] and result.exported == len(_STEMS)
    assert len(os.listdir(out / "images")) == len(os.listdir(out / "masks")) == len(_STEMS)
    for name in ("splits/train.txt", "splits/val.txt", "classes.json", "manifest.csv"):
        assert (out / name).is_file()
    assert (out / "manifest.csv.provenance.json").is_file()
    assert not (later / "out").exists()


def test_a_drive_relative_folder_inside_the_project_data_is_refused(tmp_path, monkeypatch):
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    monkeypatch.chdir(project_dir)
    options = ExportOptions(out_dir=f"{_drive(project_dir)}mask")

    with pytest.raises(ExportError, match="mask/") as caught:
        export_dataset(project_dir, images, registry, options)

    assert str(caught.value).endswith(os.path.join(project_dir, "mask"))  # named in full
    assert not os.path.exists(os.path.join(project_dir, "mask"))


@windows_only
def test_a_folder_on_another_drive_is_named_in_full(tmp_path, monkeypatch):
    """``D:`` with the project and the working folder on ``C:`` is drive D's current
    folder. It is not empty, so nothing is written; the refusal names it in full."""
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    other = _drive(os.getcwd())
    if not other or other.upper() == _drive(project_dir).upper():
        pytest.skip("the tests do not run from a drive other than the temporary folder's")
    monkeypatch.chdir(tmp_path)
    folder = os.path.abspath(other)
    if not os.path.isdir(folder) or not os.listdir(folder):
        pytest.skip(f"{folder} is not a folder with something in it")

    with pytest.raises(ExportError) as caught:
        export_dataset(project_dir, images, registry, ExportOptions(out_dir=other))

    assert os.path.isabs(folder) and _drive(folder).upper() == other.upper()
    assert str(caught.value) == f"Output directory is not empty: {folder}"


@windows_only
def test_the_dialog_spells_out_a_folder_on_another_drive(app, tmp_path):
    project_dir, images, _write_registry, registry = _build_project(tmp_path)
    other = "E:" if _drive(project_dir).upper() == "D:" else "D:"
    dialog = ExportDatasetDialog(project_dir, images, registry)
    try:
        dialog.output_edit.setText(f"{other}out")

        folder = dialog._output_folder()  # resolved by name only: nothing is written

        assert os.path.isabs(folder) and _drive(folder).upper() == other
        assert os.path.basename(folder) == "out" and folder == os.path.abspath(f"{other}out")
    finally:
        dialog.deleteLater()
