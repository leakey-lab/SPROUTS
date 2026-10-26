"""Toasts keep clear of an editor's side panel as well as its bottom chrome (R-SHELL-B).

Wave 1 lifted the toast stack above an editor's dock and zoom cluster, which pushed it up
the right edge into the side panel: in Trace GT the third toast covered the Classes panel
at 1280 x 800 and every toast did at 1024 x 640; in Correct two toasts covered the Layers
panel at 1024 x 640 (danger toasts stay until closed). Now the stack keeps its corner while
it fits below the side panel, else it moves to the panel's left.

Unit level: a host widget with stand-in chrome at the 1024 x 640 Trace GT geometry.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.gui.toast_clearance import FLOATING_GAP, ToastClearance  # noqa: E402
from app.gui.widgets.toasts import ToastManager  # noqa: E402
from tests.qt_teardown import flush_deleted  # noqa: E402

LONG = "Could not save mask for 24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg: disk full"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Scene:
    """1024 x 640 host: status line, bottom dock, zoom cluster, side panel on the right."""

    def __init__(self) -> None:
        self.host = QWidget()
        self.host.resize(1024, 640)
        self.status = self._band(QRect(0, 612, 1024, 28))
        self.dock = self._band(QRect(330, 540, 612, 46))
        self.zoom = self._band(QRect(838, 490, 162, 38))
        self.panel = self._band(QRect(846, 245, 154, 167))
        self.toasts = ToastManager(self.host)
        self.clearance = ToastClearance(
            self.host,
            self.toasts,
            lambda: [(self.status, 0), (self.dock, FLOATING_GAP), (self.zoom, FLOATING_GAP)],
            side=lambda: [(self.panel, FLOATING_GAP)],
        )
        self.clearance.watch(self.status, self.dock, self.zoom, self.panel)
        self.host.show()
        settle()

    def _band(self, rect: QRect) -> QWidget:
        widget = QWidget(self.host)
        widget.setGeometry(rect)
        return widget

    def rect(self, widget: QWidget) -> QRect:
        return QRect(widget.mapTo(self.host, QPoint(0, 0)), widget.size())

    def close(self) -> None:
        self.host.close()
        self.host.deleteLater()
        flush_deleted()


def settle() -> None:
    for _ in range(3):
        QApplication.processEvents()


@pytest.fixture
def scene(app):
    made = Scene()
    yield made
    made.close()


def assert_clear(scene: Scene) -> None:
    toasts = scene.toasts.toasts()
    assert toasts
    for toast in toasts:
        box = toast.geometry()
        assert scene.host.rect().contains(box), box
        for name in ("status", "dock", "zoom", "panel"):
            widget = getattr(scene, name)
            if widget.isVisible():
                assert not box.intersects(scene.rect(widget)), (toast.message(), name, box)


def test_one_toast_keeps_its_corner_below_the_side_panel(scene):
    scene.toasts.show("GT saved for L44.jpg")
    settle()
    assert_clear(scene)
    (toast,) = scene.toasts.toasts()
    assert toast.geometry().right() == scene.host.width() - 16 - 1  # the corner, as before


def test_a_stack_too_tall_for_the_corner_moves_left_of_the_side_panel(scene):
    scene.toasts.show("Loaded 12 images")
    scene.toasts.show("Saved D:/data/project/root_lengths.csv")
    scene.toasts.show(LONG, kind="danger")
    settle()
    assert_clear(scene)
    for toast in scene.toasts.toasts():
        assert toast.geometry().right() < scene.panel.x()


def test_the_stack_returns_to_its_corner_when_the_side_panel_hides(scene):
    for text in ("one", "two", LONG):
        scene.toasts.show(text)
    settle()
    scene.panel.hide()
    settle()
    assert_clear(scene)
    assert all(t.geometry().right() == scene.host.width() - 17 for t in scene.toasts.toasts())


def test_the_stack_follows_the_side_panel_when_it_grows(scene):
    scene.toasts.show("GT saved for L44.jpg")
    settle()
    assert_clear(scene)
    scene.panel.setGeometry(QRect(846, 245, 154, 300))  # more classes: a taller panel
    settle()
    assert_clear(scene)


def test_without_side_chrome_nothing_changes(app):
    host = QWidget()
    host.resize(1024, 640)
    try:
        toasts = ToastManager(host)
        ToastClearance(host, toasts, lambda: [])
        toasts.show("Saved")
        settle()
        (toast,) = toasts.toasts()
        assert toast.geometry().right() == 1024 - 16 - 1
        assert toast.geometry().bottom() == 640 - 16 - 1
    finally:
        host.deleteLater()
        flush_deleted()


# ------------------------------------------------------ the main window (once wired)
@pytest.mark.parametrize("layer", ["mask", "classes"])
def test_the_main_window_keeps_toasts_off_the_editor_side_panels(app, tmp_path, monkeypatch, layer):
    """Runs once MainWindow passes ``side=`` (see the R-SHELL-B cross-package request).
    On the Correct page the side card holds the Layers panel, and the class panel below it
    while the class labels are edited (WG-T5): the toasts keep clear of both."""
    import time

    from PyQt6.QtCore import QSettings
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QMessageBox

    from app.gui import image_manager
    from app.gui.main_window import MainWindow

    if not hasattr(MainWindow, "_side_chrome"):
        pytest.skip("MainWindow does not pass its editors' side panels to ToastClearance yet")
    monkeypatch.setattr(QMessageBox, "exec", lambda box: pytest.fail(f"prompt: {box.text()}"))
    monkeypatch.setenv("SPROUTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    folder = tmp_path / "project"
    folder.mkdir()
    img = QImage(640, 480, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 60, 30))
    for d in (11, 12, 13):
        assert img.save(str(folder / f"24EFCAM1_T001_L0{d}_2024.07.18_1115{d}_001_SMM.jpg"))
    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        window.resize(1024, 640)
        window.show()
        window.open_path(str(folder))
        end = time.monotonic() + 5
        while len(window.library_index.leaves()) < 3 and time.monotonic() < end:
            QTest.qWait(10)
        item = window.library_index.leaves()[0]
        window.file_list.setCurrentItem(item)
        window.on_tree_item_clicked(item, 0)
        window.ribbon_buttons["Correct"].click()
        settle()
        editor = window.correct_editor
        editor.set_active_layer(layer)
        settle()
        names = ("layer_panel", "class_panel")
        panels = [getattr(editor, name) for name in names if getattr(editor, name).isVisible()]
        assert panels and (layer != "classes" or len(panels) == 2)
        for text in ("Loaded 3 images", "Saved D:/data/project/root_lengths.csv", LONG):
            window.notify(text, "danger" if text == LONG else "success")
        QTest.qWait(50)
        settle()
        assert all(panel.isVisible() for panel in panels)
        boxes = [QRect(panel.mapTo(window, QPoint(0, 0)), panel.size()) for panel in panels]
        for toast in window.toasts.toasts():
            assert window.rect().contains(toast.geometry())
            for panel_box in boxes:
                assert not toast.geometry().intersects(panel_box), toast.message()
    finally:
        for editor in window._editors().values():
            editor.maybe_save_changes = lambda: True
        window.close()
        window.deleteLater()
        flush_deleted()
        QApplication.processEvents()
