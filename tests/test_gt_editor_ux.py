"""Trace GT stage UX at 1280 x 800 (G2, 2026-09-30): what read as broken or unfinished.

- Class panel: long class names pushed the "checked" boxes out of the panel behind a
  horizontal scroll bar (12 classes, names like "Extra class number 0"). Names now elide
  (full name in the tooltip) and the boxes always show. A box locked because the class has
  pixels says so in its tooltip.
- No image open: the canvas was blank with every control live (Save GT did nothing). The
  chip's hint line says what to do, like the Correct editor's, and Save / Clear are off.
- An invalid gt/classes.json: the chip said "No GT" while every edit was refused, each
  refused stroke stacked another long danger toast, and the toasts spelled out the absolute
  path. The chip now says "Read-only" with a hint; one refusal notice per opened image.
- Class editor: Enter in the "new class" form closed the dialog and dropped the class; OK
  with a typed, not yet added class dropped it too. Both add it now (an invalid one keeps the
  dialog open with its error). A blank name says "A class needs a name". It opens in the
  new-class field (it opened in the rows' scroll area).
- Export dialog: Enter in the folder field opened the folder picker (the first auto-default
  button); it starts the export now. The dialog counts the traced images, and with none
  explains it and keeps Export off. A relative folder goes next to the images (it went to
  the app's working folder) and a pasted "quoted" path works.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget  # noqa: E402

from app.gt import Tracing, default_registry, gt_path, save_registry, write_tracing  # noqa: E402
from app.gui import canvas_gl  # noqa: E402
from app.gui.class_editor_dialog import ClassEditorDialog  # noqa: E402
from app.gui.class_panel import ClassPanel  # noqa: E402
from app.gui.export_dataset_dialog import ExportDatasetDialog  # noqa: E402
from app.gui.gt_tracing_interface import GtTracingInterface  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

H, W = 60, 80


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _guards(monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv(canvas_gl.ENV_VAR, "0")
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture
def made(app):
    """Widgets closed and deleted deterministically after the test."""
    widgets = []
    yield widgets
    delete_widgets(widgets)


def _photo(folder, name="plot_001.png"):
    path = str(folder / name)
    cv2.imwrite(path, np.full((H, W, 3), 90, np.uint8))
    return path


def _editor(made, image_path=None):
    iface = GtTracingInterface()
    made.append(iface)
    iface.resize(1280, 800)
    if image_path:
        iface.load_image(image_path)
    return iface


def _long_named_registry(n=12):
    reg = default_registry()
    for i in range(n - len(reg.labelled())):
        reg = reg.add(
            key=f"extra_{i}", name=f"Extra class number {i}", color=f"#{0x40A0C0 + 7 * i:06x}"
        )
    return reg


# ----------------------------------------------------------------- class panel
@pytest.mark.parametrize("n", [4, 12, 25])
def test_long_class_names_elide_and_every_box_shows(app, made, n):
    host = QWidget()
    made.append(host)
    host.resize(1000, 700)
    panel = ClassPanel(host)
    reg = _long_named_registry(n)
    panel.set_registry(reg)
    host.show()
    QTest.qWaitForWindowExposed(host)
    panel.reposition()
    QApplication.processEvents()

    scroll = panel._scroll
    assert not scroll.horizontalScrollBar().isVisible()
    viewport = scroll.viewport()
    for key, row in panel._rows.items():
        name = row._name_label
        assert name.text() == reg.by_key(key).name  # the full name stays readable
        if row.checkbox is None:
            continue
        box = row.checkbox.geometry()
        right = row.mapTo(viewport, box.topRight()).x()
        assert right < viewport.width(), f"{key}: box at x={right}, viewport {viewport.width()}"
    assert panel.width() <= 280  # the panel does not grow without bound either
    for row in panel._rows.values():
        label = row._name_label
        assert label.toolTip() == (label.text() if label.is_elided() else "")


def test_a_forty_character_name_elides_with_the_full_name_as_tooltip(app, made):
    host = QWidget()
    made.append(host)
    host.resize(1000, 700)
    panel = ClassPanel(host)
    name = "A very long class name that goes on and"  # 40 characters, the most allowed
    panel.set_registry(default_registry().add(key="long_one", name=name, color="#123456"))
    host.show()
    QTest.qWaitForWindowExposed(host)
    panel.reposition()
    QApplication.processEvents()
    label = panel._rows["long_one"]._name_label
    assert label.is_elided() and label.toolTip() == name and label.text() == name
    box = panel._rows["long_one"].checkbox
    viewport = panel._scroll.viewport()
    assert panel._rows["long_one"].mapTo(viewport, box.geometry().topRight()).x() < viewport.width()


def test_the_panel_widens_for_longer_names_after_a_registry_change(app, made):
    """QScrollArea keeps its first measurement: the panel stayed as narrow as the default
    classes made it and squeezed longer names to a few letters ("Extr...")."""
    host = QWidget()
    made.append(host)
    host.resize(1000, 700)
    panel = ClassPanel(host)
    panel.set_registry(default_registry())
    host.show()
    QTest.qWaitForWindowExposed(host)
    panel.reposition()
    QApplication.processEvents()
    narrow = panel.width()

    def _swap(registry):
        # As GtTracingInterface._set_registry: now, and once the new rows are shown.
        panel.set_registry(registry)
        panel.reposition()
        QApplication.processEvents()
        panel.reposition()
        QApplication.processEvents()

    _swap(_long_named_registry(12))
    assert panel.width() > narrow
    label = panel._rows["extra_0"]._name_label
    assert label.width() == label.sizeHint().width()  # its full width, not squeezed

    _swap(default_registry())  # and back
    assert panel.width() == narrow


def test_a_locked_box_says_why_it_is_locked(app, made):
    host = QWidget()
    made.append(host)
    panel = ClassPanel(host)
    panel.set_registry(default_registry())
    panel.set_reviewed({"nodule"}, {"root"})
    root, nodule = panel._rows["root"].checkbox, panel._rows["nodule"].checkbox
    assert not root.isEnabled() and root.isChecked()
    assert nodule.isEnabled() and nodule.isChecked()
    assert "pixels" in root.toolTip() and root.toolTip() != nodule.toolTip()
    panel.set_reviewed(set(), set())
    assert root.isEnabled() and root.toolTip() == nodule.toolTip()


# ---------------------------------------------------------------- empty state
def test_no_image_hint_and_controls_off_until_an_image_opens(app, made, tmp_path):
    iface = _editor(made)
    chip = iface.status_chip
    assert "Select an image" in chip.hint_label.text()
    assert not iface.save_button.isEnabled() and not iface.clear_icon_button.isEnabled()
    assert not iface.clear_button.isEnabled()

    iface.load_image(_photo(tmp_path))
    assert chip.hint_label.text() == ""
    assert iface.save_button.isEnabled() and iface.clear_icon_button.isEnabled()

    assert iface.reset()
    assert "Select an image" in chip.hint_label.text() and not iface.save_button.isEnabled()


# ---------------------------------------------------------- invalid classes.json
def test_an_invalid_registry_reads_read_only_and_refuses_quietly(app, made, tmp_path):
    (tmp_path / "gt").mkdir()
    (tmp_path / "gt" / "classes.json").write_text("{", encoding="utf-8")
    notices = []
    iface = GtTracingInterface()
    made.append(iface)
    iface.notice.connect(lambda msg, kind: notices.append((msg, kind)))
    iface.load_image(_photo(tmp_path))

    assert iface.status_chip.state() == "error"
    assert "Read-only" in iface.state_label.text()
    assert "classes.json" in iface.status_chip.hint_label.text()
    assert "Expecting property name" in iface.state_label.toolTip()
    assert [k for _, k in notices] == ["danger"]
    assert str(tmp_path) not in notices[0][0]  # no absolute path in the toast

    for x in (10, 20, 30):
        iface.begin_stroke(QPoint(x, 10))
        iface.end_stroke()
    iface.clear_mask()
    refusals = [m for m, _ in notices if m.startswith("Editing is off")]
    assert len(refusals) == 1  # once per opened image, the chip says the rest
    assert not iface.has_unsaved_changes() and not iface.edits.plane.any()

    other = _photo(tmp_path, "plot_002.png")
    iface.load_image(other)  # still invalid: a new image may refuse once again
    iface.begin_stroke(QPoint(10, 10))
    iface.end_stroke()
    assert len([m for m, _ in notices if m.startswith("Editing is off")]) == 2

    save_registry(str(tmp_path), default_registry())  # fixed
    iface.load_image(_photo(tmp_path, "plot_003.png"))
    assert iface.status_chip.state() == "none" and "No GT" in iface.state_label.text()
    assert iface.status_chip.hint_label.text() == ""


# ---------------------------------------------------------------- class editor
def test_enter_in_the_add_form_adds_the_class_and_keeps_the_dialog_open(app, made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg.show()
    QTest.qWaitForWindowExposed(dlg)
    dlg._add_name_edit.setText("Dead root")
    dlg._add_name_edit.setFocus()
    QTest.keyClick(dlg._add_name_edit, Qt.Key.Key_Return)
    assert dlg.isVisible() and dlg.result() == 0  # not accepted
    assert "dead_root" in dlg._rows and dlg._add_name_edit.text() == ""

    dlg._add_name_edit.setText("Hypha")
    dlg._add_key_edit.setFocus()
    QTest.keyClick(dlg._add_key_edit, Qt.Key.Key_Enter)
    assert "hypha" in dlg._rows and dlg.isVisible()

    dlg._button_box.button(dlg._button_box.StandardButton.Ok).click()
    assert not dlg.isVisible()
    assert {"dead_root", "hypha"} <= {c.key for c in dlg.result_registry().classes}


def test_class_editor_keyboard_flow(app, made):
    """It opens in the new-class field (it opened in the rows' scroll area, a dead Tab
    stop); Tab cycles through the rows, the form, OK and Cancel in drawing order; Enter
    with nothing typed is OK."""
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg.show()
    dlg.activateWindow()
    QTest.qWaitForWindowActive(dlg, 2000)
    assert dlg.focusWidget() is dlg._add_name_edit

    rows = [dlg._rows[k] for k in ("root", "nodule", "root_tip", "ignore")]
    expected = []
    for row in rows:
        expected += [row.swatch_button, row.name_edit]
        if row.retire_button is not None:
            expected.append(row.retire_button)
    ok = dlg._button_box.button(dlg._button_box.StandardButton.Ok)
    cancel = dlg._button_box.button(dlg._button_box.StandardButton.Cancel)
    expected += [
        dlg._add_name_edit,
        dlg._add_key_edit,
        dlg._add_swatch,
        dlg._add_button,
        ok,
        cancel,
    ]
    chain, w = [], expected[0]
    while True:
        if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.isVisible() and w.isEnabled():
            chain.append(w)
        w = w.nextInFocusChain()
        if w is expected[0] or len(chain) > 40:
            break
    assert chain == expected

    QTest.keyClick(dlg._add_name_edit, Qt.Key.Key_Return)  # nothing typed: Enter = OK
    assert not dlg.isVisible() and dlg.result() == 1


def test_ok_adds_a_typed_class_that_was_not_added_yet(app, made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg._add_name_edit.setText("Dead root")
    dlg._on_accept()
    assert dlg.result_registry() is not None
    assert "dead_root" in {c.key for c in dlg.result_registry().classes}


def test_ok_with_an_invalid_typed_class_stays_open_with_its_error(app, made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg.show()
    dlg._add_name_edit.setText("Second root")
    dlg._add_key_edit.setText("root")  # key taken
    dlg._on_accept()
    assert dlg.isVisible() and dlg.result_registry() is None
    assert dlg._error_label.isVisible() and "Duplicate" in dlg._error_label.text()


def test_a_blank_name_says_a_class_needs_a_name(app, made):
    dlg = ClassEditorDialog(default_registry())
    made.append(dlg)
    dlg._rows["root"].name_edit.setText("   ")
    dlg._on_name_committed("root")
    assert dlg._error_label.text() == "A class needs a name."
    dlg._rows["root"].name_edit.setText("Root")
    dlg._on_name_committed("root")
    dlg._add_name_edit.setText("  ")
    dlg._on_add_clicked()
    assert dlg._error_label.text() == "A class needs a name."


# ---------------------------------------------------------------- export dialog
def _traced_project(tmp_path, n_images=3, n_traced=2):
    images = {}
    for i in range(n_images):
        path = _photo(tmp_path, f"plot_{i:03d}.png")
        images[f"plot_{i:03d}"] = path
        if i < n_traced:
            ids = np.zeros((H, W), np.uint8)
            ids[10:20, 10:20] = 1
            checked = frozenset({"root", "nodule", "root_tip"})  # fully checked
            write_tracing(gt_path(path), Tracing(ids, checked), default_registry())
    return images


def test_enter_in_the_folder_field_exports_instead_of_browsing(app, made, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    images = _traced_project(tmp_path)
    browsed = []
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: browsed.append(1) or ""
    )
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    dlg.show()
    QTest.qWaitForWindowExposed(dlg)
    defaults = [b.text() for b in dlg.findChildren(QPushButton) if b.isDefault()]
    assert defaults == ["Export"]
    assert not dlg.browse_button.autoDefault() and not dlg.cancel_button.autoDefault()

    dlg.output_edit.setText(str(tmp_path / "out"))
    dlg.output_edit.setFocus()
    QTest.keyClick(dlg.output_edit, Qt.Key.Key_Return)
    assert browsed == []
    assert dlg.export_thread is not None
    dlg.export_thread.wait(20000)
    QApplication.processEvents()
    assert dlg.result_summary is not None and dlg.result_summary.exported == 2


def test_export_counts_the_traced_images(app, made, tmp_path):
    images = _traced_project(tmp_path, n_images=5, n_traced=2)
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    assert "2 of 5 images" in dlg.traced_label.text()
    dlg.output_edit.setText(str(tmp_path / "out"))
    assert dlg.export_button.isEnabled()


def test_export_with_no_traced_image_explains_and_stays_off(app, made, tmp_path):
    images = _traced_project(tmp_path, n_images=3, n_traced=0)
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    assert "No image" in dlg.traced_label.text()
    dlg.output_edit.setText(str(tmp_path / "out"))
    assert not dlg.export_button.isEnabled()
    dlg.start_export()
    assert dlg.export_thread is None


def _run_export(dlg):
    dlg.start_export()
    assert dlg.export_thread is not None, dlg.status_label.text()
    dlg.export_thread.wait(20000)
    QApplication.processEvents()
    return dlg.result_summary


def test_a_relative_output_folder_goes_next_to_the_images(app, made, tmp_path, monkeypatch):
    """G4: a typed "out" was resolved against the app's working folder (wherever the app
    was started), not the project; it goes next to the images now."""
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    images = _traced_project(project)
    dlg = ExportDatasetDialog(str(project), images, default_registry())
    made.append(dlg)
    dlg.output_edit.setText("out")
    result = _run_export(dlg)
    assert result is not None and result.exported == 2
    assert os.path.isdir(project / "out" / "images")
    assert not os.path.exists(elsewhere / "out")
    assert dlg.output_edit.text() == str(project / "out")  # the field says where it went


def test_a_pasted_quoted_path_is_unquoted(app, made, tmp_path):
    """Explorer's "Copy as path" puts the path in double quotes."""
    images = _traced_project(tmp_path)
    dlg = ExportDatasetDialog(str(tmp_path), images, default_registry())
    made.append(dlg)
    dlg.output_edit.setText(f'  "{tmp_path / "quoted_out"}"  ')
    result = _run_export(dlg)
    assert result is not None and result.exported == 2
    assert os.path.isdir(tmp_path / "quoted_out" / "masks")
