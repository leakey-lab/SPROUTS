"""Warm-ups: the heavy imports start when the user heads for the stage that needs them.

Waves 1-2 loaded torch 250 ms after start-up (to name the device) and dash/plotly/pandas
2 s after the first project load, in every session. Measured (X-RUNTIME): +0.4-0.6 GB
per session even when nothing is generated or charted, GUI stalls at start-up and project
load (the imports hold the interpreter lock, and the full garbage collections they set
off ran 60-120 ms each), and quitting took 0.7-1.6 s instead of 0.13 s. Now:

* the device chip is named without torch (:mod:`app.inference.device_probe`);
* ``torch`` (:data:`TORCH`: torch, then the runtime is resolved and the chip shows the
  full line, ~1.3 s) and ``models`` (:data:`MODELS`: torchvision and both models' code,
  ~1 s) load, in that order, when the user enters Generate or rests the pointer on one of
  its run buttons (two jobs: a quit waits for at most one of them);
* ``dashboards`` (:data:`DASHBOARDS`: dash, plotly, pandas) load when the user enters
  Measure (Visualize comes next) or rests on Measure's run buttons or on the Visualize
  stage button. Measure itself never needs torch (its thinning is scikit-image, its CSV
  cv2 and numpy), so entering it does not load torch;
* ``skimage`` (:data:`SKIMAGE`, the skeleton tools' scikit-image) still loads at start-up,
  as before these waves: it is the one warm-up that does not wait for a request.

:data:`STAGE_WARMUPS` and :data:`BUTTON_WARMUPS` are the whole policy.

:class:`WarmupQueue` runs one warm-up at a time, each at most once per session, and only
when ``blocked()`` (the window's rules) has nothing against it: no folder scan, no project
load settling, no Measure CSV parse, no batch job, no other pool job. A warm-up that has not
started when the window closes is dropped (:meth:`WarmupQueue.shutdown`); one that runs
cannot be interrupted (an import), so the quit waits for it.

Each warm-up runs on a thread of its own, never on a QThreadPool thread that another job
reuses later: once torch's C++ code has run on a pool thread, the next job on that thread
that enters it (importing torchvision, say) waited forever for the interpreter lock it
already held (a stale thread state; reproduced 4 of 4 times, never with a fresh thread).
Only the keys the caller names ``pooled`` (the start-up scikit-image import) use the pool.

Garbage collection (:mod:`app.gc_policy`): while a warm-up imports, automatic full
collections are held (they rescanned the growing heap again and again); when it is done,
on the GUI thread, the objects alive then are frozen (``gc.freeze()``) if the warm-up
itself imported its package, so later full collections skip the new module objects.
The first freeze follows the start-up warm-up (``skimage``): start-up has settled then.
When a job imported a heavy package itself (a Generate run that started before its
warm-up), :meth:`WarmupQueue.freeze_new_imports` freezes it once the job has ended.
Only when the queue is built with ``freeze=True`` (the real app; not headless tests).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable, Collection, Mapping
from typing import Any

from PyQt6.QtCore import QEvent, QObject, Qt, QThreadPool, QTimer, pyqtSignal

from app import gc_policy

logger = logging.getLogger(__name__)

__all__ = [
    "BUTTON_WARMUPS",
    "DASHBOARDS",
    "HOVER_DWELL_MS",
    "MODELS",
    "RETRY_MS",
    "SKIMAGE",
    "STAGE_WARMUPS",
    "TORCH",
    "HoverIntent",
    "WarmupQueue",
    "warm_dashboards",
    "warm_inference",
    "warm_models",
]

SKIMAGE = "skimage"
TORCH = "torch"
MODELS = "models"
DASHBOARDS = "dashboards"

GENERATE = (TORCH, MODELS)
VISUALIZE = (DASHBOARDS,)
#: The warm-ups a stage asks for when it is entered.
STAGE_WARMUPS: dict[str, tuple[str, ...]] = {"Generate": GENERATE, "Measure": VISUALIZE}
#: The warm-ups resting on a window button asks for (attribute name -> warm-ups); the
#: Visualize stage button asks for :data:`VISUALIZE` too (see MainWindow).
BUTTON_WARMUPS: dict[str, tuple[str, ...]] = {
    "generate_all_button": GENERATE,
    "generate_button": GENERATE,
    "calculate_length_button": VISUALIZE,
    "calculate_area_button": VISUALIZE,
}
#: The package whose import a warm-up does: a freeze follows only when the warm-up itself
#: imported it (not a job or a dashboard that got there first).
MARKERS: dict[str, str] = {
    SKIMAGE: "skimage",
    TORCH: "torch",
    MODELS: "torchvision",
    DASHBOARDS: "dash",
}

#: The pointer must rest this long on a watched button to count as intent: passing over
#: it on the way elsewhere (to the close button, say) starts nothing.
HOVER_DWELL_MS = 200
#: A blocked warm-up asks again this often (a zero-cost timer while nothing is queued).
RETRY_MS = 250

_KEYBOARD_FOCUS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


# --------------------------------------------------------------------------- #
#  The jobs (pool threads; they never raise)
# --------------------------------------------------------------------------- #
def _warm_up_runtime(models: bool) -> str:
    try:
        from app.inference import runtime

        runtime.warm_up(models=models)
    except Exception as exc:  # noqa: BLE001 - the first Generate reports it again
        logger.exception("Could not load the inference runtime ahead of Generate")
        return str(exc) or type(exc).__name__
    return ""


def warm_inference() -> str:
    """The :data:`TORCH` warm-up: torch and the resolved runtime (~1.3 s). "" or why not."""
    return _warm_up_runtime(models=False)


def warm_models() -> str:
    """The :data:`MODELS` warm-up: torchvision and both models' code (~1 s). "" or why not."""
    return _warm_up_runtime(models=True)


def warm_dashboards() -> str:
    """The :data:`DASHBOARDS` warm-up: dash, plotly and pandas (~1 s). "" or why not."""
    from . import visualization_manager

    return visualization_manager.warm_dashboard_imports()


def _imported_packages() -> set[str]:
    """The :data:`MARKERS` packages imported so far (by anyone)."""
    return {name for name in MARKERS.values() if name in sys.modules}


class _Signals(QObject):
    """Carries a finished warm-up from its thread to the GUI thread.

    Unparented and kept alive by the job's closure, so a job that ends after the window
    was destroyed emits into nothing instead of a dead object.
    """

    done = pyqtSignal(str, str, bool, bool)  # key, error, held the GC, imported afresh


# --------------------------------------------------------------------------- #
#  The queue
# --------------------------------------------------------------------------- #
class WarmupQueue(QObject):
    """Runs the requested warm-ups one at a time, off the GUI thread (see the module doc).

    ``jobs``: warm-up key -> a callable run off the GUI thread, returning "" or an error
    (``None`` counts as ""); it should not raise (one that does, even with ``SystemExit``,
    ends as a failure with that reason). ``blocked``: why a warm-up must wait now,
    or None; asked again every :data:`RETRY_MS` while one waits. ``ungated``: keys that
    never wait for ``blocked`` (the start-up warm-up). ``pooled``: keys run in ``pool``
    (the global QThreadPool by default); every other key gets a fresh thread (see the
    module doc). ``freeze``: hold full collections during each job and freeze afterwards
    (:mod:`app.gc_policy`).

    Signals:
        finished(str, str): a warm-up ended (key, "" or why it failed), on the GUI thread.
    """

    finished = pyqtSignal(str, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        jobs: Mapping[str, Callable[[], str | None]],
        blocked: Callable[[], str | None] | None = None,
        ungated: Collection[str] = (),
        pooled: Collection[str] = (),
        freeze: bool = False,
        retry_ms: int = RETRY_MS,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._jobs = dict(jobs)
        self._blocked = blocked or (lambda: None)
        self._ungated = frozenset(ungated)
        self._pooled = frozenset(pooled)
        self._freeze = bool(freeze)
        self._retry_ms = max(1, int(retry_ms))
        self._pool = pool
        self._queued: list[str] = []
        self._running: str | None = None
        self._thread: threading.Thread | None = None  # the running warm-up's own thread
        self._done: dict[str, str] = {}
        self._closed = False
        self._started_at = 0.0
        self._frozen_packages: set[str] = set()  # the MARKERS packages the last freeze held
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        # Every start goes through the event loop: never inside the event that asked
        # (a hover, a stage click) nor inside the constructor.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._pump)

    # ------------------------------------------------------------------ queries
    def state(self, key: str) -> str:
        """``"idle"``, ``"queued"``, ``"running"`` or ``"done"``."""
        if key in self._done:
            return "done"
        if key == self._running:
            return "running"
        return "queued" if key in self._queued else "idle"

    def result(self, key: str) -> str | None:
        """How ``key`` ended: an empty string when it worked, why it failed, or None."""
        return self._done.get(key)

    def pending(self) -> tuple[str, ...]:
        """The queued (not started) warm-ups, in order."""
        return tuple(self._queued)

    def running(self) -> str | None:
        """The warm-up under way now, if any."""
        return self._running

    def wait(self, timeout: float | None = None) -> bool:
        """Join the thread of the warm-up under way (tests); True when none is left running.
        A pooled one is not waited for (``QThreadPool.waitForDone`` does that)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    # ------------------------------------------------------------------ commands
    def request(self, key: str | None) -> bool:
        """Queue ``key`` (once per session); True when this call queued it."""
        if self._closed or not key or key not in self._jobs:
            return False
        if key in self._done or key == self._running or key in self._queued:
            return False
        self._queued.append(key)
        if not self._timer.isActive():
            self._timer.start(0)
        return True

    def freeze_new_imports(self, reason: str) -> bool:
        """Freeze the heap when a heavy package was imported since the last freeze by
        something other than a warm-up (a Generate run started before its warm-up imports
        torch itself). Call it at a quiet moment: the main window does when a job ends (on
        CUDA right after the run's own full collection, ``release_cuda_cache``). False when
        nothing is new, freezing is off, or a warm-up is under way."""
        if not self._freeze or self._running is not None or gc_policy.full_collections_held():
            return False
        imported = _imported_packages()
        if imported <= self._frozen_packages:
            return False
        try:
            gc_policy.freeze(reason)
        except Exception:  # noqa: BLE001 - only the optimisation is lost
            logger.exception("Could not freeze the heap after %s", reason)
            return False
        self._frozen_packages |= imported
        return True

    def shutdown(self) -> None:
        """Drop every queued warm-up (the window closes); a running one ends by itself."""
        if self._queued:
            logger.info("Dropping warm-ups that had not started: %s", ", ".join(self._queued))
        self._closed = True
        self._queued.clear()
        self._timer.stop()

    # ------------------------------------------------------------------ internals
    def _pump(self) -> None:
        if self._closed or self._running is not None or not self._queued:
            return
        key = self._queued[0]
        if key not in self._ungated:
            reason = self._blocked()
            if reason:
                logger.debug("Warm-up %s waits: %s", key, reason)
                self._timer.start(self._retry_ms)
                return
        self._queued.pop(0)
        self._start(key)

    def _start(self, key: str) -> None:
        self._running = key
        self._started_at = time.perf_counter()
        job = self._jobs[key]
        marker = MARKERS.get(key)
        hold = self._freeze
        signals = self._signals

        def work() -> None:
            fresh = marker is not None and marker not in sys.modules
            if hold:
                gc_policy.hold_full_collections()  # released on the GUI thread (_on_done)
            try:
                error = job() or ""
            except BaseException as exc:  # noqa: BLE001 - SystemExit too: it must report back
                # Unreported, the warm-up would stay "running" (none after it would start)
                # and the hold on full collections would last the whole session.
                logger.exception("Warm-up %s failed", key)
                error = str(exc) or type(exc).__name__
            fresh = fresh and marker in sys.modules
            try:
                signals.done.emit(key, error, hold, fresh)
            except RuntimeError:
                pass  # the window is gone

        if key in self._pooled:
            (self._pool or QThreadPool.globalInstance()).start(work)
            return
        # Not a daemon: the process never exits under an import that is half done.
        thread = threading.Thread(target=work, name=f"warm-up {key}")
        try:
            thread.start()
        except RuntimeError as exc:  # no thread to be had: the first real use imports it
            logger.warning("Could not start the %s warm-up: %s", key, exc)
            self._on_done(key, str(exc), False, False)
            return
        self._thread = thread

    def _on_done(self, key: str, error: str, held: bool, fresh: bool) -> None:
        """A job ended: freeze what it imported, end its GC hold, start the next one."""
        try:
            if held and fresh:
                # A young collection only: at the start-up freeze a full one found nothing
                # unreachable in the old generation (3 of 3 runs) and cost 20-30 ms.
                gc_policy.freeze(f"the {key} warm-up")
                self._frozen_packages |= _imported_packages()
        except Exception:  # noqa: BLE001 - only the optimisation is lost
            logger.exception("Could not freeze the heap after the %s warm-up", key)
        finally:
            if held:
                gc_policy.release_full_collections()
        logger.info(
            "Warm-up %s %s in %.2f s",
            key,
            f"failed ({error})" if error else "done",
            time.perf_counter() - self._started_at,
        )
        self._running = None
        self._thread = None
        self._done[key] = error
        self.finished.emit(key, error)
        if self._queued and not self._closed:
            self._timer.start(0)


# --------------------------------------------------------------------------- #
#  Intent
# --------------------------------------------------------------------------- #
class HoverIntent(QObject):
    """Calls ``request(key)`` when the pointer rests on a watched widget for
    :data:`HOVER_DWELL_MS`, or the keyboard moves the focus onto it (Tab, a shortcut).

    ``key`` is whatever the widget was watched with (the main window passes a tuple of
    warm-up keys)."""

    def __init__(
        self,
        parent: QObject | None,
        request: Callable[[Any], object],
        *,
        dwell_ms: int = HOVER_DWELL_MS,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._keys: dict[QObject, Any] = {}
        self._hovered: QObject | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(dwell_ms)))
        self._timer.timeout.connect(self._on_dwell)

    def watch(self, widget: QObject | None, key: Any) -> None:
        """Ask for ``key`` when the user rests on ``widget`` (None is skipped)."""
        if widget is None or widget in self._keys:
            return
        self._keys[widget] = key
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        kind = event.type()
        if kind == QEvent.Type.Enter and obj in self._keys:
            self._hovered = obj
            self._timer.start()
        elif kind in (QEvent.Type.Leave, QEvent.Type.Hide) and obj is self._hovered:
            self._timer.stop()
            self._hovered = None
        elif kind == QEvent.Type.FocusIn and event.reason() in _KEYBOARD_FOCUS:
            key = self._keys.get(obj)
            if key:
                self._request(key)
        return False

    def _on_dwell(self) -> None:
        key = self._keys.get(self._hovered) if self._hovered is not None else None
        if key:
            self._request(key)
