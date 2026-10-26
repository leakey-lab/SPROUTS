"""The six stages: going to one, which one is highlighted, what a change of stage does, the
status line's summary of a stage, and the Visualize stage's entry.

A change of stage (any path: a stage button, Alt+1…6, an editor handing over) refreshes
Measure's freshness counts and the status line's idle text (W12), asks for the stage's
warm-up once the user has stayed in it for a second, and tells the dashboards. Visualize
opens the highlighted dashboard, or, before there is anything to chart, a page that names
the next step with a button to it (W5).

These are functions of the window, bound as :class:`~app.gui.main_window.MainWindow`
methods under their old names (``MainWindow.on_stage_changed`` is
:func:`on_stage_changed`): a stage switch costs no extra call, and code and tests keep
calling (and patching) the window.
"""

import logging
import os
from typing import TYPE_CHECKING

from app.config import ROOT_AREAS_CSV, ROOT_LENGTHS_CSV

from .. import visualization_manager
from ..image_manager import project_layout_of
from ..panels import Panel

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)


def go_to_stage(window: "MainWindow", key: str) -> None:
    """Click ``key``'s stage button, when the stages are on screen and it is enabled."""
    button = window.ribbon_buttons.get(key)
    if button is None or not button.isEnabled() or window.app_stack.currentIndex() != 1:
        return  # nothing loaded yet: the stages are not on screen
    button.click()


def current_stage(window: "MainWindow") -> str | None:
    """The key of the highlighted stage (None before the top bar exists)."""
    buttons = getattr(window, "ribbon_buttons", {}) or {}
    return next((k for k, b in buttons.items() if b.isChecked()), None)


def on_stage_changed(window: "MainWindow", key: str) -> None:
    """The highlighted stage changed (any path): the status line says what Generate or
    Measure would work on (W12), the stage's warm-up is asked for once the user has
    stayed in it for a second (torch for Generate, the dashboards' libraries for
    Measure; passing through asks for nothing: :class:`~app.gui.warmup.StageIntent`),
    and the dashboards are told, when they want to know (they are released a while
    after Visualize is left)."""
    if key == "Measure":
        window.metrics.check_freshness()
    window._refresh_idle_text()
    window._stage_intent.stage_changed(key)
    changed = getattr(visualization_manager, "stage_changed", None)
    if callable(changed):
        try:
            changed(window, key)
        except Exception:  # noqa: BLE001 - a stage switch must never fail on this
            logger.exception("The dashboards could not follow the stage %r", key)


def stage_summary(window: "MainWindow") -> str | None:
    """What a run of the stage on screen would work on: the status line's idle text on
    Generate and Measure (see :class:`~app.gui.stage_status.StageStatus`); None on the
    other stages (W12)."""
    return window.stage_status.text(window._current_stage(), window.image_manager)


def refresh_idle_text(window: "MainWindow") -> None:
    """The status line's idle text follows (what a run of the stage would work on)."""
    line = getattr(window, "statusline", None)
    if line is not None:
        line.refresh_idle()


def show_dashboard(window: "MainWindow", kind: str) -> None:
    """The Visualize stage: the ``kind`` dashboard ("length" / "area").

    Without its CSV (nothing measured yet) the stage page names the next step, with a
    button to it, instead of a message box (W5); otherwise the dashboard is built or
    brought forward (see :mod:`app.gui.visualization_manager`).
    """
    missing = window._missing_dashboard_data(kind)
    if missing is not None:
        title, body, detail, stage = missing
        if window.switch_right_panel(Panel.VISUALIZATION):
            window.visualization_widget.show_missing(
                title, body, detail, stage=stage, action=f"Go to {stage}" if stage else ""
            )
        return
    window.visualization_widget.show_opening()
    getattr(window, f"show_root_{kind}_visualization")()


def missing_dashboard_data(
    window: "MainWindow", kind: str
) -> tuple[str, str, str, str | None] | None:
    """``(title, next step, expected CSV, stage)`` when the ``kind`` dashboard cannot open
    yet (no images, a run rewriting its data, or no CSV); None when it can, or is open
    already (the dashboard controller then decides)."""
    spec = getattr(visualization_manager, "SPECS", {}).get(kind)
    if spec is None or getattr(window, spec.attr, None) is not None:
        return None
    layout = project_layout_of(window.image_manager)
    if layout is None or not window.image_manager.images:
        return "No images loaded", "Open a folder of root images first (Ctrl+O).", "", None
    noun = "root length" if kind == "length" else "root area"
    is_busy = getattr(getattr(window, "skeleton_handler", None), "is_busy", None)
    if callable(is_busy) and is_busy(kind):  # a run rewrites what it would chart
        # The job's name says what is going ("Measuring root length…", V11).
        running = window.jobs.running_name()
        title = f"{running}…" if running else "Waiting for a run to finish"
        return title, f"The {noun} dashboard opens when it has finished.", "", None
    try:
        if spec.find_csv(layout).path:
            return None
    except Exception:  # noqa: BLE001 - the dashboard controller reports it then
        logger.exception("Could not look for the %s CSV", kind)
        return None
    manager = window.image_manager
    if kind == "length":
        inputs, made = manager.skeleton_stems, "skeletons"
        button, csv_path = "Measure Length", os.path.join(layout.skeletons_dir, ROOT_LENGTHS_CSV)
    else:
        inputs, made = manager.masks, "masks"
        masks_dir = layout.resolve_masks_dir() or layout.mask_dir
        button, csv_path = "Measure Area", os.path.join(masks_dir, ROOT_AREAS_CSV)
    if inputs:
        stage, step = "Measure", f"Run Measure → {button} to chart the {noun} of this trial."
    else:
        stage, step = "Generate", f"Generate {made} first, then run Measure → {button}."
    return f"No {noun} measurements yet", step, f"Looked for {csv_path}", stage
