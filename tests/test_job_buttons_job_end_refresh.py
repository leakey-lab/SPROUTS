"""What the end of a batch job re-reads for the metrics bar (``job_buttons``).

Generate writes predictions only (``mask/``, ``skeletons/``), never a measurement CSV: the
end of a generation pass keeps the parsed rows, so LENGTH and AREA stay on screen and
neither CSV is parsed again (each pass end hid both for about 0.2 s at 46k rows while the
pool parsed them again, twice per Generate). A Measure run, or a job of no known name, may
have rewritten the CSVs: they are re-located and re-read, as before. The job-end slot learns
which job ended from ``JobRegistry.ended_name()``.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", "0")

from PyQt6.QtCore import QSettings, Qt, QThread, QThreadPool  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

from app.config import AREA_CSV_HEADERS, LENGTH_CSV_HEADERS  # noqa: E402
from app.data_processing import measurement_index  # noqa: E402
from app.handlers.job_registry import JobRegistry  # noqa: E402
from app.inference.metrics import write_metric_csv  # noqa: E402
from tests.qt_teardown import delete_widgets  # noqa: E402

STEM = "24EFCAM1_T001_L044_2024.07.18_111530_001_SMM"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Job(QThread):
    """A batch job that ends at once."""

    def run(self) -> None:
        pass


def until(condition, timeout_s: float = 5.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if condition():
            return True
        QApplication.processEvents()
    return condition()


def run_job(jobs: JobRegistry, name: str) -> None:
    """Start ``name`` through the registry and wait until it has ended (busy False)."""
    ended = []
    jobs.busy_changed.connect(lambda busy: ended.append(busy) if not busy else None)
    thread = Job()
    assert jobs.try_start(name, thread)
    assert until(lambda: ended)
    thread.wait()


def test_the_job_end_slot_can_tell_which_job_ended(app):
    jobs = JobRegistry()
    seen = []
    jobs.busy_changed.connect(
        lambda busy: seen.append((busy, jobs.running_name(), jobs.ended_name()))
    )
    assert jobs.ended_name() is None

    run_job(jobs, "Generating masks")

    assert seen == [(True, "Generating masks", None), (False, None, "Generating masks")]


def _csv(path, headers, value) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"Image": STEM, "Tube": 1, "Position": 44, "Date": "2024.07.18", "Time": "11:15:30"}
    row[headers[5]] = value
    write_metric_csv([row], str(path), headers)


def _open_selected(made: list, tmp_path) -> None:
    """A window on a project with both CSVs, its image selected and its values shown."""
    from app.gui.main_window import MainWindow

    root = tmp_path / "proj"
    root.mkdir()
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(root / f"{STEM}.jpg"))
    _csv(root / "skeletons" / "root_lengths.csv", LENGTH_CSV_HEADERS, 9.25)
    _csv(root / "mask" / "root_areas.csv", AREA_CSV_HEADERS, 4.5)
    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    made.append(window)
    window.open_path(str(root))
    assert until(lambda: window._image_leaves())
    item = window.library_index.item_for(STEM)
    window.file_list.setCurrentItem(item)
    window.on_image_selected(item)
    assert until(lambda: shown(window) and not window.metrics.loading)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from app.gui import image_manager

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    made: list = []
    _open_selected(made, tmp_path)
    yield made[0]
    QThreadPool.globalInstance().waitForDone(5000)
    delete_widgets(made)


def shown(window) -> bool:
    bar = window.metrics_bar
    return not bar._length_cell.isHidden() and not bar._area_cell.isHidden()


def watch(window, monkeypatch) -> dict:
    """What the next job end does: CSV parses, invalidations, and whether LENGTH / AREA
    were on screen right after the window's job-end slot."""
    seen = {"parsed": [], "invalidated": 0, "shown_after_slot": []}
    real_read = measurement_index._read_metric_csv
    real_invalidate = measurement_index.MeasurementIndex.invalidate

    def read(path, column):
        seen["parsed"].append(os.path.basename(path))
        return real_read(path, column)

    def invalidate(index):
        seen["invalidated"] += 1
        real_invalidate(index)

    monkeypatch.setattr(measurement_index, "_read_metric_csv", read)
    monkeypatch.setattr(measurement_index.MeasurementIndex, "invalidate", invalidate)
    # Connected after the window's own slot: runs right after it.
    window.jobs.busy_changed.connect(
        lambda busy: seen["shown_after_slot"].append(shown(window)) if not busy else None
    )
    return seen


@pytest.mark.parametrize("label", ["mask_generation_handler", "skeleton_handler.skeleton_handler"])
def test_a_generation_run_end_keeps_the_parsed_values(window, monkeypatch, label):
    handler = window
    for attr in label.split("."):
        handler = getattr(handler, attr)
    seen = watch(window, monkeypatch)

    run_job(window.jobs, handler.job_label)  # "Generating masks" / "Generating skeletons"
    QThreadPool.globalInstance().waitForDone(5000)
    QApplication.processEvents()

    assert seen["shown_after_slot"] == [True]  # never hidden
    assert seen["parsed"] == [] and seen["invalidated"] == 0
    assert window.metrics_bar._length_value.text() == "9.25 mm"
    assert window.metrics_bar._area_value.text().startswith("4.50 mm")


def test_a_measure_run_end_re_reads_the_csvs(window, monkeypatch, tmp_path):
    seen = watch(window, monkeypatch)
    jobs = []
    real_prewarm = measurement_index.MeasurementIndex.prewarm
    monkeypatch.setattr(
        measurement_index.MeasurementIndex,
        "prewarm",
        lambda index: jobs.append(1) or real_prewarm(index),
    )
    csv_path = tmp_path / "proj" / "skeletons" / "root_lengths.csv"
    _csv(csv_path, LENGTH_CSV_HEADERS, 7.5)  # the run rewrote it

    run_job(window.jobs, "Measuring root length")

    assert until(lambda: window.metrics_bar._length_value.text() == "7.50 mm")
    assert until(lambda: not window.metrics.loading)
    assert seen["invalidated"] == 1
    assert sorted(seen["parsed"]) == ["root_areas.csv", "root_lengths.csv"]
    assert jobs == [1]  # the CSV is where Measure writes it: no search of the legacy folders


def test_a_job_end_of_no_known_name_re_reads_the_csvs(window, monkeypatch):
    seen = watch(window, monkeypatch)

    window.jobs.busy_changed.emit(False)  # as a direct call: no job name

    assert seen["invalidated"] == 1
    assert until(lambda: shown(window) and not window.metrics.loading)
    assert window.metrics_bar._length_value.text() == "9.25 mm"


def _settle() -> None:
    QThreadPool.globalInstance().waitForDone(5000)
    QApplication.processEvents()


def test_a_measure_run_end_counts_measure_once_while_its_csv_is_parsed(window, monkeypatch):
    """On the Measure stage with an image selected, the job end's refresh starts the parse
    of the rewritten CSVs: Measure's inputs are counted once that parse is in (on_ready),
    not a second time before it."""
    window._go_to_stage("Measure")
    _settle()
    checks = []
    real_check = window.stage_status.check
    monkeypatch.setattr(
        window.stage_status,
        "check",
        lambda *args, **kwargs: checks.append(kwargs.get("locate")) or real_check(*args, **kwargs),
    )

    run_job(window.jobs, "Measuring root length")
    assert until(lambda: not window.metrics.loading)
    _settle()

    assert checks == [True]  # one count, after the parse: it may look for the CSVs


def test_a_measure_run_end_parses_once_when_its_parse_also_searches(app, tmp_path, monkeypatch):
    """A project measured for area only: after a Measure Area run the bar's refresh starts a
    parse, which looks for root_lengths.csv in the legacy folders too; no second, located
    parse follows it."""
    from app.gui import image_manager
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    root = tmp_path / "proj"
    root.mkdir()
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(root / f"{STEM}.jpg"))
    _csv(root / "mask" / "root_areas.csv", AREA_CSV_HEADERS, 4.5)
    made = [MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))]
    try:
        window = made[0]
        window.open_path(str(root))
        assert until(lambda: window._image_leaves())
        item = window.library_index.item_for(STEM)
        window.file_list.setCurrentItem(item)
        window.on_image_selected(item)
        assert until(lambda: not window.metrics_bar._area_cell.isHidden())
        assert until(lambda: not window.metrics.loading)
        _settle()
        parses = []
        real_prewarm = measurement_index.MeasurementIndex.prewarm
        monkeypatch.setattr(
            measurement_index.MeasurementIndex,
            "prewarm",
            lambda index: parses.append(1) or real_prewarm(index),
        )
        _csv(root / "mask" / "root_areas.csv", AREA_CSV_HEADERS, 5.5)  # the run rewrote it

        run_job(window.jobs, "Measuring root area")
        assert until(lambda: window.metrics_bar._area_value.text().startswith("5.50 mm"))
        assert until(lambda: not window.metrics.loading)
        _settle()

        assert parses == [1]
    finally:
        QThreadPool.globalInstance().waitForDone(5000)
        delete_widgets(made)


def test_a_measure_run_end_counts_against_the_csvs_found_again(app, tmp_path, monkeypatch):
    """On the Measure stage with no image selected, the job end's count of Measure's inputs
    looks for the CSVs it re-locates first: never "not measured yet" for a measured project
    while a slow parse (46k rows) runs."""
    from app.gui import image_manager
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(
        image_manager.ImageManager, "_start_temp_sweep", staticmethod(lambda _folder: None)
    )
    root = tmp_path / "proj"
    (root / "skeletons").mkdir(parents=True)
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 60, 30))
    assert image.save(str(root / f"{STEM}.jpg"))
    assert image.save(str(root / "skeletons" / f"{STEM}_fake.png"))
    time.sleep(0.05)  # the CSV after its input
    _csv(root / "skeletons" / "root_lengths.csv", LENGTH_CSV_HEADERS, 3.0)
    made = [MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))]
    try:
        texts = _measure_texts_after_a_job_end(made[0], root, monkeypatch)
    finally:
        QThreadPool.globalInstance().waitForDone(5000)
        delete_widgets(made)
    assert texts, "no Measure count came in"
    assert not [text for text in texts if "not measured yet" in text], texts


def _measure_texts_after_a_job_end(window, root, monkeypatch) -> list[str]:
    """The Measure idle texts the status line gets after a job end of no known name, with
    no image selected and each CSV parse slowed down as at 46k rows."""
    window.open_path(str(root))
    assert until(lambda: window._image_leaves())
    assert until(lambda: not window.metrics.loading)
    window.file_list.setCurrentItem(None)
    window._go_to_stage("Measure")
    QThreadPool.globalInstance().waitForDone(5000)
    real = measurement_index.MeasurementIndex.prewarm
    monkeypatch.setattr(
        measurement_index.MeasurementIndex,
        "prewarm",
        lambda index: (time.sleep(0.3), real(index))[1],
    )
    texts: list[str] = []
    status, manager = window.stage_status, window.image_manager
    status.changed.connect(lambda: texts.append(status.text("Measure", manager)))

    window.jobs.busy_changed.emit(False)

    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        QThreadPool.globalInstance().waitForDone(20)
        QApplication.processEvents()
    QThreadPool.globalInstance().waitForDone(5000)
    QApplication.processEvents()
    return texts
