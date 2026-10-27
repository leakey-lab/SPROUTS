"""About SPROUTS dialog (UXS-15 / FR-09): version, commit, models, calibration, citation."""

from __future__ import annotations

import time
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from app import __version__, provenance
from app.gui import about_dialog
from app.models import ModelStatus

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


def test_citation_text_names_all_three_authors():
    text = about_dialog.citation_text(str(about_dialog.PROJECT_ROOT + "/CITATION.cff"))
    assert text.startswith("Parth, K., Varela, S., & Leakey, A. D. B.")
    assert "SPROUTS (Version 3.0.0)" in text


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
    assert "18×13.5 mm" in text
    assert "ca1f92d21609" in text
    assert "Commit:" in text


class _Parent(QWidget):
    """A window with the controller shape the dialog reads (duck-typed)."""

    def __init__(self, calibrations):
        super().__init__()
        index = SimpleNamespace(
            calibrations=lambda: calibrations,
            sources=lambda: {
                "length": "D:/p/skeletons/root_lengths.csv",
                "area": "D:/p/mask/root_areas.csv",
            },
        )
        self.metrics = SimpleNamespace(index=index)


def test_about_names_a_differing_measured_calibration(app):
    differing = {
        "length": {"fov_width_mm": 18.0, "fov_height_mm": 13.0},
        "area": None,
    }
    parent = _Parent(differing)
    text = _texts(about_dialog.AboutDialog(parent))
    assert "Measured with" in text and "FOV 18×13 mm (root_lengths.csv)" in text

    same = {"length": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}, "area": None}
    parent = _Parent(same)
    assert "Measured with" not in _texts(about_dialog.AboutDialog(parent))
    assert "Measured with" not in _texts(about_dialog.AboutDialog())  # no window, no project
    parent = _Parent({"length": None, "area": None})
    assert "Measured with" not in _texts(about_dialog.AboutDialog(parent))


def test_dialog_and_details_show_the_licence(app):
    dlg = about_dialog.AboutDialog()
    assert "GPL-3.0-or-later" in _texts(dlg)
    assert "License: GPL-3.0-or-later" in about_dialog.details_text()


def test_details_and_dialog_name_the_canvas_path(app, monkeypatch):
    from app.gui import canvas_gl

    monkeypatch.setattr(canvas_gl, "canvas_line", lambda: ("OpenGL · Fake GPU", False))
    assert "Canvas: OpenGL · Fake GPU" in about_dialog.details_text(canvas="OpenGL · Fake GPU")
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


def test_copy_details_names_the_differing_measured_calibration(app, monkeypatch):
    """The 'Copy details' block holds the 'Measured with' line the dialog shows, and only then."""
    differing = {"length": {"fov_width_mm": 18.0, "fov_height_mm": 13.0}, "area": None}
    monkeypatch.setattr(about_dialog, "_verify_models", lambda: {})
    for calibrations, expected in [
        (differing, "Measured with: FOV 18×13 mm (root_lengths.csv)"),
        ({"length": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}, "area": None}, None),
    ]:
        parent = _Parent(calibrations)
        dlg = about_dialog.AboutDialog(parent)
        dlg.copy_details()
        copied = QApplication.clipboard().text()
        assert ("Measured with" in copied) == (expected is not None)
        if expected:
            assert expected in copied
    assert "Measured with" not in about_dialog.details_text()
    assert "Measured with: FOV 1×2 mm (a.csv); FOV 3×4 mm (b.csv)" in about_dialog.details_text(
        measured_with="FOV 1×2 mm (a.csv)\nFOV 3×4 mm (b.csv)"
    )


def test_one_rule_decides_that_a_recorded_field_of_view_differs(app, tmp_path):
    """The export, the metrics bar and About all compare as numbers (math.isclose)."""
    from app.data_processing.measurement_index import Measurement
    from app.gui.window.metrics import _shown_fov
    from app.visualization import dash_export

    csv_path = tmp_path / "root_lengths.csv"
    for height, differs in [(13.5 + 1e-12, False), (13.0, True)]:
        cal = {"fov_width_mm": 18.0, "fov_height_mm": height}
        provenance.write_sidecar(csv_path, {"calibration": cal})
        export = dash_export.calibration_line(str(csv_path))
        tip = _shown_fov(Measurement(1.0, None, None), {"length": cal}, {"length": str(csv_path)})[
            1
        ]
        parent = _Parent({"length": cal, "area": None})
        row = about_dialog.measured_with_text(parent)
        assert ("this app now uses" in export) is differs
        assert ("This app now uses" in tip) is differs
        assert (row is not None) is differs
