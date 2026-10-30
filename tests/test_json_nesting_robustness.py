"""Deeply nested JSON is just another malformed file (R4).

``json.load`` / ``json.loads`` raise ``RecursionError`` (a ``RuntimeError``, not a
``ValueError``) for input nested deeper than the recursion limit. Every reader that
already turns malformed JSON into its own error must treat it the same way: a
hand-broken ``gt/classes.json`` once escaped ``load_registry`` and reached the crash
hook instead of the "invalid registry" notice (Trace GT then; the Correct page's class
labels now).

Since Python 3.14 the ``RecursionError`` comes when the C stack runs low, not at a fixed
depth: on a big main-thread stack (the Ubuntu CI runner's; here from 64 MB) a 100,000-deep
file parses, and the readers then reject or accept it by its type, without a crash. So every
test here parses on a thread with a small stack (:func:`_json_on_a_small_stack`), which makes
the ``RecursionError`` path the readers must handle the same on every OS.
"""

from __future__ import annotations

import json
import logging
import os
import threading

import cv2
import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from app import enhancement as enh
from app.config import GT_META_CHUNK
from app.gt.registry import RegistryError, load_registry
from app.gt.store import GtFileError, gt_path, read_tracing
from app.provenance import BUILD_INFO_FILENAME, git_commit

_DEPTH = 100_000
NESTED = {
    "array": "[" * _DEPTH + "]" * _DEPTH,
    "object": '{"a":' * _DEPTH + "1" + "}" * _DEPTH,
}
#: Stack of the thread the JSON is parsed on: 100,000 levels overflow it on every OS.
_PARSE_STACK = 2 << 20


@pytest.fixture(autouse=True)
def _json_on_a_small_stack(monkeypatch):
    """``json.loads`` (and ``json.load``, which calls it) runs on a thread with a 2 MB stack.

    The real parser, only on a known stack: the readers call ``json.load`` / ``json.loads``
    through the module, so they all parse there.
    """
    real_loads = json.loads

    def loads(*args, **kwargs):
        outcome = {}

        def parse():
            try:
                outcome["value"] = real_loads(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
                outcome["error"] = exc

        previous = threading.stack_size(_PARSE_STACK)
        try:
            worker = threading.Thread(target=parse, name="json-small-stack")
            worker.start()
        finally:
            threading.stack_size(previous)
        worker.join()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    monkeypatch.setattr(json, "loads", loads)


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


# ------------------------------------------------------------------ the class labels
@pytest.fixture
def page(app, no_prompts, gl_off):
    """The Correct page with its notices recorded; no modal box, no GL."""
    from app.gui.correct.editor import CorrectEditor
    from tests.qt_teardown import delete_widgets

    editor = CorrectEditor()
    editor.notices = []
    editor.notice.connect(lambda msg, kind: editor.notices.append((msg, kind)))
    yield editor
    delete_widgets([editor])


def _image(folder) -> str:
    path = folder / "plot_T1_L1_2024.01.01_120000.png"
    cv2.imwrite(str(path), np.full((30, 40, 3), 90, np.uint8))
    return str(path)


def test_the_class_labels_report_a_deeply_nested_classes_json_instead_of_crashing(page, tmp_path):
    from app.gui.correct.layer import CLASSES
    from tests.correct_support import drag

    image = _image(tmp_path)
    _write_classes_json(tmp_path, NESTED["array"])

    page.load_image(image, images_base_folder=str(tmp_path))  # RecursionError used to escape
    page.set_active_layer(CLASSES)
    drag(page, (5, 5), (20, 5))  # the first edit is refused with the notice users see

    assert page.current_image_path == image
    assert any(kind == "danger" and "classes.json" in msg for msg, kind in page.notices)
    assert "Read-only" in page.status_chip.state_label.text()
    assert page.save() is False  # the invalid-registry path: saving is off
    assert not os.path.exists(gt_path(image))


def test_the_class_labels_open_empty_for_a_deeply_nested_meta_chunk(page, tmp_path):
    from app.gui.correct.layer import CLASSES

    image = _image(tmp_path)
    path = gt_path(image)
    _write_png_with_meta(path, NESTED["object"])
    with open(path, "rb") as fh:
        before = fh.read()

    page.load_image(image, images_base_folder=str(tmp_path))
    page.set_active_layer(CLASSES)

    assert page.current_image_path == image
    assert any(kind == "warn" and "could not be read" in msg for msg, kind in page.notices)
    assert "Class labels not shown" in page.status_chip.state_label.text()
    assert not page.layers[CLASSES].document.history.plane.any()
    with open(path, "rb") as fh:
        assert fh.read() == before  # left as is unless the user saves
