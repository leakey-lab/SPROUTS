"""The length dashboard's CSV lookup never walks the folders on the GUI thread (S08-01).

Visualize looks for ``root_lengths.csv`` on the GUI thread: on entry (the stage button,
Alt+5, the Length segment, the retry after a job end) in ``stages.missing_dashboard_data``,
and in the dashboard controller's ``_show`` and ``_is_up_to_date``. Without the CSV in
``skeletons/``, the project folder or the two fixed legacy folders, that lookup fell
through to the legacy ``test_latest`` walk on every entry: the project folder, ``mask/``,
``skeletons/``, ``gt/`` and the parents' siblings listed (about 100 ms at 46k photos). It
now goes through the project's :class:`MeasurementIndex`, whose search in the pool walked
when the project opened: the same CSV is found with a few stats. Before any search walked,
or without an index of this project, it walks as before.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, Qt, QThreadPool, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app import project_layout  # noqa: E402
from app.config import LENGTH_CSV_HEADERS, ROOT_LENGTHS_CSV  # noqa: E402
from app.data_processing.measurement_index import MeasurementIndex  # noqa: E402
from app.gui import image_manager  # noqa: E402
from app.gui import visualization_manager as vm  # noqa: E402
from app.inference.metrics import write_metric_csv  # noqa: E402
from app.project_layout import ProjectLayout  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
#: A test_latest folder that only the walk finds.
WALKED = ("results", "run1", "test_latest")


@pytest.fixture
def walks(monkeypatch, tmp_path):
    """Every legacy ``test_latest`` walk, as ``(start folder, on the GUI thread)``. What it
    finds outside ``tmp_path`` is not reported (the walk also searches the parents)."""
    seen: list[tuple[str, bool]] = []
    real = project_layout.find_test_latest_dir

    def spy(start, *args, **kwargs):
        seen.append((start, threading.current_thread() is threading.main_thread()))
        found = real(start, *args, **kwargs)
        inside = found and os.path.commonpath([found, str(tmp_path)]) == str(tmp_path)
        return found if inside else None

    monkeypatch.setattr(project_layout, "find_test_latest_dir", spy)
    return seen


def _gui_walks(walks) -> list[str]:
    return [start for start, on_gui in walks if on_gui]


def _lengths(folder) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    row = {
        "Image": STEM,
        "Tube": 1,
        "Position": 44,
        "Date": "2024.07.18",
        "Time": "11:15:30",
        LENGTH_CSV_HEADERS[5]: 9.25,
    }
    path = str(folder / ROOT_LENGTHS_CSV)
    write_metric_csv([row], path, LENGTH_CSV_HEADERS)
    return path


# ------------------------------------------------------------------------ the index
def test_the_lookup_uses_the_walk_of_the_pool_search(tmp_path, walks):
    written = _lengths(tmp_path.joinpath(*WALKED, "images"))
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()  # the pool's search when the project opens: it walks
    assert len(walks) == 1
    assert index.sources()["length"] == written
    walks.clear()

    assert index.find_lengths_csv() == written
    index.invalidate()  # every Measure job end: the walk's folder is kept
    assert index.find_lengths_csv() == written
    assert walks == []


def test_before_any_search_the_lookup_walks_once(tmp_path, walks):
    index = MeasurementIndex(str(tmp_path))

    assert index.find_lengths_csv() is None
    assert index.find_lengths_csv() is None
    assert len(walks) == 1  # its own walk is kept as well

    written = _lengths(tmp_path / "skeletons")  # what Measure writes later: found at once
    assert index.find_lengths_csv() == written
    assert len(walks) == 1


def test_a_test_latest_folder_made_later_is_found_by_the_next_pool_search(tmp_path, walks):
    index = MeasurementIndex(str(tmp_path))
    index.prewarm()  # walked: no test_latest folder
    written = _lengths(tmp_path.joinpath(*WALKED, "images"))  # copied in by hand
    assert index.find_lengths_csv() is None  # the app never makes such a folder

    index.invalidate()
    index.prewarm()  # the next search in the pool (a job end without a length CSV)

    assert index.find_lengths_csv() == written


def test_the_window_index_is_used_for_its_own_project_only(tmp_path):
    layout = ProjectLayout(str(tmp_path / "a"))
    index = MeasurementIndex(str(tmp_path / "a"))
    window = SimpleNamespace(metrics=SimpleNamespace(index=index))

    assert vm.measurement_index_of(window, layout) is index
    assert vm.measurement_index_of(window, ProjectLayout(str(tmp_path / "b"))) is None
    assert vm.measurement_index_of(window, None) is None
    assert vm.measurement_index_of(SimpleNamespace(), layout) is None  # no metrics (stand-ins)
    no_index = SimpleNamespace(metrics=SimpleNamespace(index=None))
    assert vm.measurement_index_of(no_index, layout) is None


# ------------------------------------------------------------------------ the window
class StubLength(QMainWindow):
    """A length dashboard without Dash or a browser: the real one's signals and clean-up."""

    server_closed = pyqtSignal()
    export_saved = pyqtSignal(str)
    made: list = []

    def __init__(self, csv_path, *_args):
        super().__init__()
        self.csv_path = csv_path
        self.failed = False
        type(self).made.append(self)

    def cleanup_server(self) -> None:
        pass


_UNSET = object()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch, walks):
    """A shown window whose length dashboard is :class:`StubLength` (set on the module itself:
    a monkeypatch would import the Dash stack to read the old value)."""
    from app.gui.main_window import MainWindow

    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox, name, lambda *a, n=name, **k: pytest.fail(f"QMessageBox.{n}: {a[2:]}")
        )
    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    saved = vm.__dict__.get("RootLengthVisualization", _UNSET)
    vm.RootLengthVisualization = StubLength
    StubLength.made = []
    made = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.resize(1280, 800)
    made.show()
    try:
        yield made
    finally:
        QThreadPool.globalInstance().waitForDone(5000)
        delete_widgets([made])
        if saved is _UNSET:
            vm.__dict__.pop("RootLengthVisualization", None)
        else:
            vm.RootLengthVisualization = saved


def _settle() -> None:
    for _ in range(3):
        QApplication.processEvents()


def _until(condition, timeout_s: float = 5.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if condition():
            return True
        QApplication.processEvents()
    return condition()


def _idle() -> None:
    QThreadPool.globalInstance().waitForDone(5000)
    _settle()


def _project(root, *, skeletons: bool):
    root.mkdir(parents=True)
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(root / f"{STEM}.jpg"))
    if skeletons:
        (root / "skeletons").mkdir()
        assert image.save(str(root / "skeletons" / f"{STEM}_fake.png"))
    return root


def _open(window, root) -> None:
    """Open ``root`` and wait until the pool's search of its CSVs has reported."""
    window.open_path(str(root))
    assert _until(lambda: window._image_leaves())
    assert _until(lambda: not window.metrics.loading)
    _idle()


def _enter(window, stage: str) -> None:
    window.ribbon_buttons[stage].click()
    _settle()


def test_visualize_without_a_length_csv_walks_in_the_pool_only(window, tmp_path, walks):
    root = _project(tmp_path / "proj", skeletons=True)
    _open(window, root)
    assert walks and _gui_walks(walks) == []  # the open's search walked, in the pool

    _enter(window, "Visualize")
    window.action_bar_viz_seg.button("length").click()
    _settle()
    page = window.visualization_widget
    texts = (page.title_label.text(), page.body_label.text(), page.detail_label.text())
    assert page.is_blocked()
    window._on_jobs_busy_changed(False)  # a job ended while the page waits: it looks again
    _idle()

    assert _gui_walks(walks) == []
    assert texts == (
        "No root length measurements yet",
        "Run Measure → Measure Length to chart the root length of this trial.",
        f"Looked for {os.path.join(str(root), 'skeletons', ROOT_LENGTHS_CSV)}",
    )
    assert StubLength.made == []


@pytest.mark.parametrize(
    "folder",
    [
        (*WALKED, "images"),
        WALKED,
        ("output", "skeletonizer", "test_latest", "images"),
        ("skeletonizer", "test_latest", "images"),
    ],
    ids=[
        "walked, where Measure writes",
        "walked, where it wrote before 2024-12",
        "fixed output folder",
        "fixed folder",
    ],
)
def test_a_legacy_csv_opens_the_dashboard(window, tmp_path, walks, folder):
    root = _project(tmp_path / "proj", skeletons=False)
    written = _lengths(root.joinpath(*folder))
    _open(window, root)

    _enter(window, "Visualize")
    _enter(window, "Library")
    _enter(window, "Visualize")  # the live dashboard is checked against its CSV
    window._on_jobs_busy_changed(False)  # a job end: the pool searches again
    _idle()
    _enter(window, "Library")
    _enter(window, "Visualize")

    assert [os.path.normcase(d.csv_path) for d in StubLength.made] == [os.path.normcase(written)]
    assert window.right_panel.currentWidget() is StubLength.made[0]
    assert _gui_walks(walks) == []


def test_a_csv_measure_writes_later_opens_the_dashboard_at_once(window, tmp_path, walks):
    root = _project(tmp_path / "proj", skeletons=True)
    _open(window, root)
    _enter(window, "Visualize")
    assert StubLength.made == []
    _enter(window, "Measure")
    written = _lengths(root / "skeletons")  # the Measure run wrote it

    _enter(window, "Visualize")

    assert [os.path.normcase(d.csv_path) for d in StubLength.made] == [os.path.normcase(written)]
    assert _gui_walks(walks) == []
