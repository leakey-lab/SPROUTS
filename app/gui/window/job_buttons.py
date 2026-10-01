"""The run buttons while a batch job runs, and what the window refreshes after one.

While a job runs (generation or measurement; one at a time, ``app.handlers.job_registry``)
the buttons that start one are disabled with a tooltip that says why. When it ends they
come back, the measured values are re-read (a Measure run may have rewritten the CSVs),
the modules the run imported are frozen, and a Visualize page that waited for the run
tries again.

:func:`on_jobs_busy_changed` is a function of the window, bound as
``MainWindow._on_jobs_busy_changed`` (the slot of ``jobs.busy_changed``); the buttons'
own tooltips wait in ``window._job_button_tooltips`` while a job runs.
"""

from typing import TYPE_CHECKING

from ..panels import Panel

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

#: Buttons that start a batch job; disabled (with a tooltip saying why) while one runs.
JOB_BUTTONS = (
    "generate_all_button",
    "generate_button",
    "calculate_length_button",
    "calculate_area_button",
)


def on_jobs_busy_changed(window: "MainWindow", busy: bool) -> None:
    """Disable the job buttons while a job runs; refresh measured values after one."""
    name = window.jobs.running_name() or "A task"
    for attr in JOB_BUTTONS:
        button = getattr(window, attr, None)
        if button is None:
            continue
        if busy:
            window._job_button_tooltips.setdefault(attr, button.toolTip())
            button.setEnabled(False)
            button.setToolTip(f"{name} is running. Wait for it, or press Cancel.")
        else:
            button.setEnabled(True)
            if attr in window._job_button_tooltips:
                button.setToolTip(window._job_button_tooltips.pop(attr))
    if not busy and window.metrics.index is not None:
        # A Measure run may have rewritten the CSVs: re-locate and re-read them.
        window.metrics.index.invalidate()
        window.metrics.refresh(window.metrics.current_image_name())
    if not busy and window._current_stage() == "Measure":
        window.metrics.check_freshness()  # a Measure run rewrote a CSV
    warmups = getattr(window, "warmups", None)
    if not busy and warmups is not None:
        # A run that started before its warm-up imported torch itself: its modules are
        # frozen now, right after the run's own full collection (app.gui.warmup).
        warmups.freeze_new_imports("a run's imports")
    page = getattr(window, "visualization_widget", None)
    if not busy and page is not None and page.is_blocked():
        # Visualize said why its dashboard could not open, and a run just ended (it may
        # have written the CSV, or it was the run it waited for): try again.
        if window.right_panel.currentIndex() == Panel.VISUALIZATION:
            window.show_dashboard(window.ribbon.visualize_kind())
