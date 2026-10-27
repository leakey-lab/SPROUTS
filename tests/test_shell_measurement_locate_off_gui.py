"""The metrics bar never walks folders for a legacy CSV on the GUI thread (review finding).

``MeasurementIndex.lookup(load=False)`` / ``needs_load()`` are the GUI thread's calls (every
library selection). Without a ``root_lengths.csv`` in ``skeletons/`` or the project folder,
locating one fell through to the legacy ``test_latest`` search, a walk of the project
folders and their parents: about 60 ms per selection on a 46k-image project (at most every
2 s). The GUI-thread calls now look only where Measure writes (a few stats: ``skeletons/``,
the project folder and the two fixed legacy folders); the walk runs in the worker's
:meth:`MeasurementIndex.prewarm`, which the window starts when a project opens and after a
Measure run, so a legacy CSV is still found. A located prewarm asked for while another
parse runs (a project opened during the previous project's parse) starts when that parse
reports.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt, QThread, QThreadPool  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app import project_layout  # noqa: E402
from app.config import LENGTH_CSV_HEADERS  # noqa: E402
from app.data_processing.measurement_index import MeasurementIndex  # noqa: E402
from app.inference.metrics import write_metric_csv  # noqa: E402
from tests.qt_teardown import delete_widgets, flush_deleted  # noqa: E402

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"
LEGACY = ("skeletonizer", "test_latest", "images")
#: Where Measure writes for a test_latest folder that only the walk finds.
WALKED = ("results", "run1", "test_latest", "images")


@pytest.fixture
def walks(monkeypatch):
    """Every legacy ``test_latest`` search, as the folder it started from."""
    seen: list[str] = []
    real = project_layout.find_test_latest_dir

    def spy(start, *args, **kwargs):
        seen.append(start)
        return real(start, *args, **kwargs)

    monkeypatch.setattr(project_layout, "find_test_latest_dir", spy)
    return seen


def _legacy_lengths(root, parts: tuple[str, ...] = LEGACY) -> str:
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True)
    row = {
        "Image": STEM,
        "Tube": 1,
        "Position": 44,
        "Date": "2024.07.18",
        "Time": "11:15:30",
        LENGTH_CSV_HEADERS[5]: 9.25,
    }
    path = str(folder / "root_lengths.csv")
    write_metric_csv([row], path, LENGTH_CSV_HEADERS)
    return path


def test_the_gui_thread_calls_never_walk_for_a_legacy_csv(tmp_path, walks):
    now = [0.0]
    index = MeasurementIndex(str(tmp_path), clock=lambda: now[0])
    for _ in range(3):
        now[0] += 5.0  # past the relocation throttle every time
        index.lookup(STEM, load=False)
        index.needs_load()
        index.outdated(STEM)
        index.sources()
    assert walks == []


@pytest.mark.parametrize("parts", [LEGACY, WALKED], ids=["fixed folder", "walked folder"])
def test_the_worker_prewarm_still_finds_a_legacy_csv(tmp_path, walks, parts):
    _legacy_lengths(tmp_path, parts)
    index = MeasurementIndex(str(tmp_path))
    assert index.lookup(STEM, load=False).length_mm is None  # never parsed on the GUI thread
    assert walks == []

    index.prewarm()  # a worker's job

    assert index.lookup(STEM, load=False).length_mm == 9.25
    assert index.sources()["length"].endswith("root_lengths.csv")


def test_a_fixed_legacy_csv_is_found_again_after_a_job_end_without_a_walk(tmp_path, walks):
    """invalidate() (every Measure job end) forgets the located CSV: the GUI thread's search
    finds it again in a fixed legacy folder (a stat), so the bar asks for its parse."""
    written = _legacy_lengths(tmp_path)
    now = [0.0]
    index = MeasurementIndex(str(tmp_path), clock=lambda: now[0])
    index.prewarm()
    assert index.lookup(STEM, load=False).length_mm == 9.25

    index.invalidate()
    now[0] += 5.0

    assert index.needs_load() is True
    assert index.sources()["length"] == written
    index.prewarm()
    assert index.lookup(STEM, load=False).length_mm == 9.25
    assert walks == []  # a fixed folder is never walked for


def test_a_csv_measure_writes_later_is_found_without_a_walk(tmp_path, walks):
    now = [0.0]
    index = MeasurementIndex(str(tmp_path), clock=lambda: now[0])
    index.prewarm()
    walks.clear()
    folder = tmp_path / "skeletons"
    folder.mkdir()
    row = {"Image": STEM, "Tube": 1, "Position": 44, "Date": "d", "Time": "t"}
    row[LENGTH_CSV_HEADERS[5]] = 3.5
    write_metric_csv([row], str(folder / "root_lengths.csv"), LENGTH_CSV_HEADERS)
    now[0] += 5.0

    assert index.needs_load() is True
    index.prewarm()
    assert index.lookup(STEM, load=False).length_mm == 3.5


# ------------------------------------------------------------------------ the window
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_delayed_temp_sweep(monkeypatch):
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )


def test_the_bar_never_waits_for_a_csv_the_pool_is_parsing(app, tmp_path):
    """needs_load() takes the CSV lock, which the pool's parse holds (about 50 ms at 46k
    rows here): while a parse runs, a selection must not ask it (the parse's ready signal
    refreshes the bar)."""
    from app.gui.main_window import MainWindow

    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        index = MeasurementIndex(str(tmp_path))
        asked = []
        index.needs_load = lambda: asked.append(1) or False
        window._measurements = index
        window._measurements_loading = True  # a pool job is parsing

        window._refresh_metrics_bar(STEM)

        assert asked == []
    finally:
        window.close()
        window.deleteLater()
        flush_deleted()


def test_a_legacy_csv_reaches_the_metrics_bar(app, tmp_path):
    from app.gui.main_window import MainWindow

    root = tmp_path / "proj"
    root.mkdir()
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(root / f"{STEM}.jpg"))
    _legacy_lengths(root)
    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        window.open_path(str(root))
        end = time.monotonic() + 5
        while time.monotonic() < end and not window._image_leaves():
            QApplication.processEvents()
        item = window.library_index.item_for(STEM)
        window.file_list.setCurrentItem(item)
        window.on_image_selected(item)
        end = time.monotonic() + 5
        while time.monotonic() < end and window.metrics_bar._length_cell.isHidden():
            QApplication.processEvents()
        assert window.metrics_bar._length_value.text() == "9.25 mm"
    finally:
        window.close()
        window.deleteLater()
        flush_deleted()


# ------------------------------------------------------------------ after a batch job
class _Job(QThread):
    """A batch job that ends at once."""

    def run(self) -> None:
        pass


def _until(condition, timeout_s: float = 5.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if condition():
            return True
        QApplication.processEvents()
    return condition()


def _end_job(window, name: str) -> None:
    """Run job ``name`` through the window's registry until it has ended."""
    ended = []
    window.jobs.busy_changed.connect(lambda busy: ended.append(1) if not busy else None)
    thread = _Job()
    assert window.jobs.try_start(name, thread)
    assert _until(lambda: ended)
    thread.wait()


def _legacy_window(made: list, tmp_path, *, csv: bool = True) -> None:
    """A window on a project whose length CSV is (or will be) in a legacy folder only, its
    image selected; with ``csv`` it waits until the bar shows the CSV's value."""
    from app.gui.main_window import MainWindow

    root = tmp_path / "proj"
    root.mkdir()
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(root / f"{STEM}.jpg"))
    if csv:
        _legacy_lengths(root)
    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.append(window)
    window.open_path(str(root))
    assert _until(lambda: window._image_leaves())
    item = window.library_index.item_for(STEM)
    window.file_list.setCurrentItem(item)
    window.on_image_selected(item)
    assert _until(lambda: not window.metrics.loading)
    if csv:
        assert _until(lambda: window.metrics_bar._length_value.text() == "9.25 mm")


@pytest.fixture
def made():
    widgets: list = []
    yield widgets
    QThreadPool.globalInstance().waitForDone(5000)
    delete_widgets(widgets)


def _length_text(window) -> str:
    bar = window.metrics_bar
    return "" if bar._length_cell.isHidden() else bar._length_value.text()


@pytest.mark.parametrize("job", ["Measuring root length", None])
def test_a_legacy_csv_stays_on_the_bar_after_a_measure_run(app, tmp_path, made, job):
    _legacy_window(made, tmp_path)
    window = made[0]

    if job is None:
        window.jobs.busy_changed.emit(False)  # a job end of no known name
    else:
        _end_job(window, job)

    assert _until(lambda: _length_text(window) == "9.25 mm" and not window.metrics.loading)
    assert window.metrics.index.sources()["length"].endswith("root_lengths.csv")


def test_a_legacy_csv_stays_on_the_bar_after_a_generation_pass(app, tmp_path, made):
    _legacy_window(made, tmp_path)
    window = made[0]

    _end_job(window, window.mask_generation_handler.job_label)

    assert _length_text(window) == "9.25 mm"  # never hidden: nothing was re-read


@pytest.mark.parametrize("parts", [LEGACY, WALKED], ids=["fixed folder", "walked folder"])
def test_a_measure_run_into_a_legacy_folder_reaches_the_bar(app, tmp_path, made, parts):
    _legacy_window(made, tmp_path, csv=False)
    window = made[0]
    assert _length_text(window) == ""  # nothing measured yet

    _legacy_lengths(tmp_path / "proj", parts)  # where Measure Length writes without skeletons/
    _end_job(window, "Measuring root length")

    assert _until(lambda: _length_text(window) == "9.25 mm")


def test_a_measure_run_that_ends_during_an_older_parse_reaches_the_bar(
    app, tmp_path, made, monkeypatch
):
    """A parse in flight at a Measure end searched for root_lengths.csv before the run wrote
    it into a walked legacy folder: a located search follows that parse (S06-01), so the
    value reaches the bar."""
    _legacy_window(made, tmp_path, csv=False)
    window = made[0]
    searched, gate, calls = threading.Event(), threading.Event(), []
    real_prewarm = MeasurementIndex.prewarm

    def prewarm(index):
        calls.append(1)
        if len(calls) > 1:
            return real_prewarm(index)
        index._length.load()  # its search predates the run's CSV
        searched.set()
        gate.wait(5)
        index._area.load()

    monkeypatch.setattr(MeasurementIndex, "prewarm", prewarm)
    window.metrics.prewarm(locate=True)  # the older parse
    assert searched.wait(5)
    _legacy_lengths(tmp_path / "proj", WALKED)  # where the run writes

    _end_job(window, "Measuring root length")
    gate.set()

    assert _until(lambda: _length_text(window) == "9.25 mm")
    assert len(calls) == 2


def test_a_project_opened_during_the_previous_parse_gets_its_own_search(app, tmp_path):
    """One parse at a time: the located prewarm of a project opened while the previous
    project's parse runs is refused, and starts when that parse reports."""
    from app.gui.window.metrics import MetricsController

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _legacy_lengths(tmp_path / "b")
    first, second = MeasurementIndex(str(tmp_path / "a")), MeasurementIndex(str(tmp_path / "b"))
    gate = threading.Event()
    real = first.prewarm
    first.prewarm = lambda: (gate.wait(5), real())  # the previous project's parse in flight
    window = SimpleNamespace(
        image_manager=None,
        display_controller=SimpleNamespace(current_name=None),
        stage_status=SimpleNamespace(check=lambda *a, **k: None),
        _current_stage=lambda: None,
    )
    controller = MetricsController(window)
    controller.signals.ready.connect(controller.on_ready)
    controller.index = first
    controller.prewarm(locate=True)  # project A opens
    controller.index = second
    controller.prewarm(locate=True)  # project B opens while A's parse runs: refused

    gate.set()

    assert _until(lambda: second.sources()["length"] is not None and not controller.loading)
    assert second.lookup(STEM, load=False).length_mm == 9.25


class _Pool:
    """The thread pool, recording the jobs it is given (run by hand)."""

    def __init__(self) -> None:
        self.jobs: list = []

    def globalInstance(self):  # noqa: N802 - Qt API name
        return self

    def start(self, job) -> None:
        self.jobs.append(job)


def _controller(monkeypatch, refreshed: list, checks: list):
    """A metrics controller on the Measure stage whose bar refreshes and Measure counts are
    recorded, over a stub pool (its jobs run by hand) and a stand-in index."""
    from app.gui.window import metrics

    pool = _Pool()
    monkeypatch.setattr(metrics, "QThreadPool", pool)
    window = SimpleNamespace(
        image_manager=None,
        display_controller=SimpleNamespace(current_name=None),
        stage_status=SimpleNamespace(check=lambda *a, **k: checks.append(1)),
        _current_stage=lambda: "Measure",
    )
    controller = metrics.MetricsController(window)
    controller.refresh = lambda name=None: refreshed.append(name)
    controller.index = SimpleNamespace(prewarm=lambda: None, needs_load=lambda: False)
    return controller, pool


def test_a_located_prewarm_asked_during_a_parse_runs_after_it(monkeypatch):
    """A Measure run ended while an older parse ran (its search may predate the run's CSV):
    the located prewarm runs once that parse reports, and the bar and Measure's counts
    follow its own report."""
    refreshed, checks = [], []
    controller, pool = _controller(monkeypatch, refreshed, checks)
    controller.prewarm()  # nothing to parse: refused
    controller.loading = True  # an older parse in flight
    controller.prewarm(locate=True)  # refused for now
    assert pool.jobs == []

    controller.on_ready(controller.index)  # the older parse reports

    assert len(pool.jobs) == 1 and controller.loading
    assert refreshed == [] and checks == []  # the located parse reports next
    controller.on_ready(controller.index)
    assert refreshed == [None] and checks == [1] and not controller.loading


def test_a_plain_prewarm_refused_during_a_parse_waits_for_nothing(monkeypatch):
    refreshed, checks = [], []
    controller, pool = _controller(monkeypatch, refreshed, checks)
    controller.loading = True
    controller.prewarm()  # a selection's parse: the report's refresh asks again

    controller.on_ready(controller.index)

    assert pool.jobs == [] and refreshed == [None] and checks == [1]
