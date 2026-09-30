"""Deeply nested JSON is just another malformed file (R4).

``json.load`` / ``json.loads`` raise ``RecursionError`` (a ``RuntimeError``, not a
``ValueError``) for input nested deeper than the recursion limit. Every reader that
already turns malformed JSON into its own error must treat it the same way: a
hand-broken ``gt/classes.json`` once escaped ``load_registry`` and reached the crash
hook instead of the Trace GT "invalid registry" notice.
"""

from __future__ import annotations

import json
import logging
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, PngImagePlugin  # noqa: E402

from app import enhancement as enh  # noqa: E402
from app.config import GT_META_CHUNK  # noqa: E402
from app.gt.registry import RegistryError, load_registry  # noqa: E402
from app.gt.store import GtFileError, gt_path, read_tracing  # noqa: E402
from app.provenance import BUILD_INFO_FILENAME, git_commit  # noqa: E402

_DEPTH = 100_000
NESTED = {
    "array": "[" * _DEPTH + "]" * _DEPTH,
    "object": '{"a":' * _DEPTH + "1" + "}" * _DEPTH,
}


def test_nested_json_really_raises_recursion_error():
    # The premise of R4: not a ValueError, so ``except ValueError`` misses it.
    for text in NESTED.values():
        with pytest.raises(RecursionError):
            json.loads(text)


def _write_classes_json(project_dir, text: str) -> None:
    (project_dir / "gt").mkdir(exist_ok=True)
    (project_dir / "gt" / "classes.json").write_text(text, encoding="utf-8")


def _write_png_with_meta(path, meta_text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = Image.fromarray(np.zeros((6, 8), np.uint8), mode="P")
    image.putpalette([0, 0, 0] * 256)
    info = PngImagePlugin.PngInfo()
    info.add_text(GT_META_CHUNK, meta_text)
    image.save(path, format="PNG", pnginfo=info)


# ------------------------------------------------------------------ core readers


@pytest.mark.parametrize("kind", sorted(NESTED))
def test_load_registry_reports_deeply_nested_json_as_registry_error(tmp_path, kind):
    _write_classes_json(tmp_path, NESTED[kind])
    with pytest.raises(RegistryError, match="classes.json"):
        load_registry(str(tmp_path))


@pytest.mark.parametrize("kind", sorted(NESTED))
def test_read_tracing_reports_deeply_nested_meta_chunk_as_gt_file_error(tmp_path, kind):
    path = str(tmp_path / "gt" / "a.png")
    _write_png_with_meta(path, NESTED[kind])
    with pytest.raises(GtFileError, match="metadata"):
        read_tracing(path)


def test_enhance_read_json_treats_deeply_nested_file_as_unreadable(tmp_path, caplog):
    path = tmp_path / "normalization_defaults.json"
    path.write_text(NESTED["array"], encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=enh.__name__):
        assert enh._read_json(str(path)) is None
    assert "unreadable normalization defaults" in caplog.text.lower()


def test_enhance_store_falls_back_to_factory_for_deeply_nested_user_file(tmp_path):
    factory = tmp_path / "factory.json"
    factory.write_text(json.dumps({"clahe_clip": 12}), encoding="utf-8")
    user = tmp_path / "user" / "normalization_defaults.json"
    user.parent.mkdir()
    user.write_text(NESTED["object"], encoding="utf-8")
    store = enh.EnhanceDefaultsStore(user_path=str(user), factory_path=str(factory))
    assert store.load().clip == 12


def test_git_commit_ignores_a_deeply_nested_build_info(tmp_path):
    # A frozen build (no .git) reads build_info.json; a broken one means "unknown".
    (tmp_path / BUILD_INFO_FILENAME).write_text(NESTED["object"], encoding="utf-8")
    assert git_commit(str(tmp_path)) is None


@pytest.mark.parametrize("kind", sorted(NESTED))
def test_recent_projects_ignore_a_deeply_nested_list(kind):
    # The Welcome screen reads this list at startup: it must not crash the app.
    from app.gui.recent_projects import RecentProjectsStore

    class _Settings:
        def value(self, key, default=None):
            return NESTED[kind]

        def setValue(self, key, value):  # noqa: N802 - Qt API name
            pass

    assert RecentProjectsStore(_Settings()).entries() == []


# ------------------------------------------------------------------ Trace GT


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def gt_editor(app, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from app.gui.gt_tracing_interface import GtTracingInterface

    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("unexpected modal dialog"))
    editor = GtTracingInterface()
    editor.notices = []
    editor.notice.connect(lambda msg, kind: editor.notices.append((msg, kind)))
    yield editor
    editor.deleteLater()


def _image(folder) -> str:
    path = folder / "plot_T1_L1_2024.01.01_120000.png"
    cv2.imwrite(str(path), np.full((30, 40, 3), 90, np.uint8))
    return str(path)


def test_trace_gt_reports_a_deeply_nested_classes_json_instead_of_crashing(gt_editor, tmp_path):
    image = _image(tmp_path)
    _write_classes_json(tmp_path, NESTED["array"])

    gt_editor.load_image(image)  # RecursionError used to escape to the crash hook

    assert gt_editor.current_image_path == image
    assert any(kind == "danger" and "classes.json" in msg for msg, kind in gt_editor.notices)
    assert gt_editor.save_mask() is False  # the invalid-registry path: saving is off
    assert not os.path.exists(gt_path(image))


def test_trace_gt_opens_empty_for_a_deeply_nested_meta_chunk(gt_editor, tmp_path):
    image = _image(tmp_path)
    path = gt_path(image)
    _write_png_with_meta(path, NESTED["object"])
    with open(path, "rb") as fh:
        before = fh.read()

    gt_editor.load_image(image)

    assert gt_editor.current_image_path == image
    assert any(kind == "danger" and "Could not read GT" in m for m, kind in gt_editor.notices)
    assert "GT unreadable" in gt_editor.state_label.text()
    with open(path, "rb") as fh:
        assert fh.read() == before  # left as is unless the user saves
