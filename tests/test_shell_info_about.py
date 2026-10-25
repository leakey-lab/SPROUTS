"""About SPROUTS dialog (UXS-15 / FR-09): version, commit, models, calibration, citation."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app import __version__  # noqa: E402
from app.gui import about_dialog  # noqa: E402
from app.models import ModelStatus  # noqa: E402

CFF = """\
cff-version: 1.2.0
title: "SPROUTS (Root-Mask-and-Skeletons)"
authors:
  - given-names: Kinjalk
    family-names: Parth
    alias: MasterKinjalk
  - given-names: Ada B.
    family-names: Lovelace
url: "https://github.com/leakey-lab/Root-Mask-and-Skeletons"
version: "3.0.0"
date-released: 2026-10-01
doi: 10.5281/zenodo.123
"""


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def _texts(dlg) -> str:
    return "\n".join(lbl.text() for lbl in dlg.findChildren(QLabel))


def _button(dlg, text):
    return next(b for b in dlg.findChildren(QPushButton) if text in b.text())


def test_citation_text_from_cff(tmp_path):
    path = tmp_path / "CITATION.cff"
    path.write_text(CFF, encoding="utf-8")
    text = about_dialog.citation_text(str(path))
    assert text == (
        "Parth, K., & Lovelace, A. B. (2026). SPROUTS (Root-Mask-and-Skeletons) "
        "(Version 3.0.0) [Computer software]. https://doi.org/10.5281/zenodo.123"
    )


def test_citation_text_without_date_or_doi_uses_nd_and_url(tmp_path):
    path = tmp_path / "CITATION.cff"
    path.write_text(
        CFF.replace("date-released: 2026-10-01\n", "").replace("doi: 10.5281/zenodo.123\n", ""),
        encoding="utf-8",
    )
    text = about_dialog.citation_text(str(path))
    assert "(n.d.)" in text
    assert text.endswith("https://github.com/leakey-lab/Root-Mask-and-Skeletons")


def test_citation_text_missing_file_is_none(tmp_path):
    assert about_dialog.citation_text(str(tmp_path / "nope.cff")) is None


def test_details_text_has_version_commit_fov_and_models():
    statuses = {"mask": ModelStatus("ok", "C:/m.pth", "ca1f92d21609" + "0" * 52)}
    text = about_dialog.details_text(statuses)
    assert f"v{__version__}" in text
    assert "18×13 mm" in text
    assert "ca1f92d21609" in text
    assert "Commit:" in text


def test_details_and_dialog_name_the_canvas_path(app, monkeypatch):
    from app.gui import canvas_gl

    monkeypatch.setattr(canvas_gl, "canvas_line", lambda: ("OpenGL · Fake GPU", False))
    assert "Canvas: OpenGL · Fake GPU" in about_dialog.details_text()
    dlg = about_dialog.AboutDialog()
    assert "OpenGL · Fake GPU" in _texts(dlg)
    dlg.close()


def test_dialog_shows_version_and_verifies_models_off_the_gui_thread(app, monkeypatch):
    import threading

    threads = []

    def fake_verify_all():
        threads.append(threading.current_thread())
        return {
            "mask": ModelStatus("ok", "C:/m.pth", "ca1f92d21609" + "0" * 52),
            "skeleton": ModelStatus("missing", "C:/s.pth", None),
        }

    monkeypatch.setattr(about_dialog, "_verify_models", fake_verify_all)
    dlg = about_dialog.AboutDialog()
    assert f"v{__version__}" in _texts(dlg)
    deadline = time.monotonic() + 5
    while "ca1f92d21609" not in _texts(dlg) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    text = _texts(dlg)
    assert "ca1f92d21609" in text and "verified" in text
    assert "missing" in text
    assert threads and threads[0] is not threading.main_thread()


def test_copy_citation_and_details_use_the_clipboard(app, monkeypatch, tmp_path):
    path = tmp_path / "CITATION.cff"
    path.write_text(CFF, encoding="utf-8")
    monkeypatch.setattr(about_dialog, "CITATION_PATH", str(path))
    monkeypatch.setattr(about_dialog, "_verify_models", lambda: {})
    dlg = about_dialog.AboutDialog()
    _button(dlg, "Copy citation").click()
    assert "Parth, K." in QApplication.clipboard().text()
    _button(dlg, "Copy details").click()
    assert f"v{__version__}" in QApplication.clipboard().text()


def test_no_citation_button_without_cff(app, monkeypatch, tmp_path):
    monkeypatch.setattr(about_dialog, "CITATION_PATH", str(tmp_path / "none.cff"))
    monkeypatch.setattr(about_dialog, "_verify_models", lambda: {})
    dlg = about_dialog.AboutDialog()
    assert not [b for b in dlg.findChildren(QPushButton) if "Copy citation" in b.text()]
