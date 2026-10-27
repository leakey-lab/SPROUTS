"""Opening a project's ``gt/`` folder as a project is refused (final review D4).

``ProjectLayout(<project>/gt).mask_dir`` is the real ``<project>/gt/mask``: Generate's
"Replace all predictions" on that "project" would overwrite the human GT masks. So a folder
named ``gt`` that holds ``classes.json`` or a ``mask/`` or ``skeletons/`` sub-folder is not
opened; a warning names the folder to open instead. An ordinary project that merely contains
a ``gt/`` sub-folder, a plain folder of photos that happens to be named ``gt`` and a project
named ``gt`` that Generate has given ``mask/`` and ``skeletons/`` all open: a ``mask/`` or
``skeletons/`` marks a GT folder only when the folder shows no sign of being a project.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import (
    GT_CLASSES_FILENAME,
    GT_DIRNAME,
    MASK_DIRNAME,
    SKELETONS_DIRNAME,
)
from app.gui.panels import Panel
from app.gui.window import project_loader


def loader_window(calls: list) -> SimpleNamespace:
    """A window that records what an open does: the toasts, the page switch and the scan."""
    editor = SimpleNamespace(maybe_save_changes=lambda: True, reset=lambda: None)
    window = SimpleNamespace(
        notify=lambda message, kind="success", timeout=3200, key=None: calls.append(
            (message, kind)
        ),
        _refresh_recents=lambda: calls.append("recents"),
        app_stack=SimpleNamespace(setCurrentIndex=lambda i: calls.append(("page", i))),
        image_manager=SimpleNamespace(load_images=lambda d: calls.append("scan"), images={}),
        loading_overlay=SimpleNamespace(set_filename=lambda *a, **k: None, start=lambda *a: None),
        empty_state=SimpleNamespace(set_loading=lambda name: None),
        task_progress=SimpleNamespace(start=lambda *a: None),
        status_bar=SimpleNamespace(clearMessage=lambda: None),
        ribbon=SimpleNamespace(),
        _persist_window_state=False,
    )
    window._editors = lambda: {Panel.CORRECT: editor}
    return window


def _gt_folder(tmp_path, marker: str, name: str = GT_DIRNAME) -> str:
    """``<tmp>/trial/<name>`` holding ``marker`` (a file name, or a sub-folder name)."""
    folder = tmp_path / "trial" / name
    folder.mkdir(parents=True)
    if marker == GT_CLASSES_FILENAME:
        (folder / marker).write_text("{}", encoding="utf-8")
    else:
        (folder / marker).mkdir()
    return str(folder)


@pytest.mark.parametrize("marker", [GT_CLASSES_FILENAME, MASK_DIRNAME, SKELETONS_DIRNAME])
def test_a_projects_gt_folder_is_refused_and_names_the_folder_to_open(tmp_path, marker):
    calls: list = []
    folder = _gt_folder(tmp_path, marker)

    project_loader.open_path(loader_window(calls), folder)

    parent = str(tmp_path / "trial")
    assert calls == [(project_loader.GT_FOLDER_OPEN.format(name="gt", parent=parent), "warn")]
    assert parent in calls[0][0]  # the toast names the folder to open instead
    # nothing changed: no page switch, no scan, the library stays
    assert "scan" not in calls and ("page", 1) not in calls


def test_the_gt_folder_name_is_compared_without_case(tmp_path):
    calls: list = []
    folder = _gt_folder(tmp_path, MASK_DIRNAME, name="GT")

    project_loader.open_path(loader_window(calls), folder)

    assert calls and calls[0][1] == "warn" and "scan" not in calls


def test_an_ordinary_project_with_a_gt_subfolder_opens(tmp_path):
    calls: list = []
    project = tmp_path / "trial"
    (project / GT_DIRNAME / MASK_DIRNAME).mkdir(parents=True)
    (project / GT_DIRNAME / GT_CLASSES_FILENAME).write_text("{}", encoding="utf-8")

    project_loader.open_path(loader_window(calls), str(project))

    assert "scan" in calls and not any(c[-1] == "warn" for c in calls if isinstance(c, tuple))


def test_a_plain_folder_named_gt_opens(tmp_path):
    calls: list = []
    folder = tmp_path / "gt"  # photos only: no classes.json, no mask/ or skeletons/ inside
    folder.mkdir()

    project_loader.open_path(loader_window(calls), str(folder))

    assert "scan" in calls


def test_a_folder_with_classes_json_but_another_name_opens(tmp_path):
    calls: list = []
    folder = _gt_folder(tmp_path, GT_CLASSES_FILENAME, name="other")

    project_loader.open_path(loader_window(calls), folder)

    assert "scan" in calls


def _generated_project_named_gt(tmp_path, name: str = GT_DIRNAME) -> Path:
    """A project folder named ``gt`` (photos) that Generate has given ``mask/`` and
    ``skeletons/`` (the layers a GT folder has too)."""
    folder = tmp_path / "trial" / name
    (folder / MASK_DIRNAME).mkdir(parents=True)
    (folder / SKELETONS_DIRNAME).mkdir()
    (folder / "a.jpg").write_bytes(b"x")
    return folder


def test_a_generated_project_named_gt_opens_with_a_run_record(tmp_path):
    calls: list = []
    folder = _generated_project_named_gt(tmp_path)
    (folder / MASK_DIRNAME / "_sprouts_run.json").write_text("{}", encoding="utf-8")
    (folder / MASK_DIRNAME / "a.png").write_bytes(b"x")

    project_loader.open_path(loader_window(calls), str(folder))

    assert "scan" in calls and not any(c[-1] == "warn" for c in calls if isinstance(c, tuple))


def test_a_generated_project_named_gt_opens_with_a_prediction(tmp_path):
    calls: list = []
    folder = _generated_project_named_gt(tmp_path, name="GT")
    (folder / SKELETONS_DIRNAME / "a_fake.png").write_bytes(b"x")

    project_loader.open_path(loader_window(calls), str(folder))

    assert "scan" in calls and not any(c[-1] == "warn" for c in calls if isinstance(c, tuple))


@pytest.mark.parametrize(
    "mark",
    [
        "sprouts_project.json",
        "mask/root_areas.csv",
        "skeletons/root_lengths.csv",
        "gt/.keep",
    ],
)
def test_the_other_project_markers_keep_a_folder_named_gt_open(tmp_path, mark):
    folder = _generated_project_named_gt(tmp_path)
    path = folder / mark
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    assert project_loader.gt_folder_parent(str(folder)) is None


def test_a_gt_folder_of_plain_layers_is_still_refused(tmp_path):
    calls: list = []
    folder = _generated_project_named_gt(tmp_path)  # mask/ + skeletons/ ...
    (folder / "a.jpg").unlink()
    (folder / MASK_DIRNAME / "a.png").write_bytes(b"x")  # ... holding plain <stem>.png files
    (folder / SKELETONS_DIRNAME / "a.png").write_bytes(b"x")

    project_loader.open_path(loader_window(calls), str(folder))

    assert calls and calls[0][1] == "warn" and "scan" not in calls
