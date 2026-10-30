"""Keep Python's full garbage collections off the GUI thread's back (X-RUNTIME F1).

The cyclic collector runs a *full* pass over every tracked object once enough objects
have survived since the last one. Such a pass holds the interpreter lock, so whichever
thread runs it stalls the GUI thread too, and it costs time in proportion to the heap:
about 2.5 ms for the app alone, 60 ms once torch is imported and 110 ms with torch and
dash/plotly/pandas (tracked objects: 44k, 192k, 304k). A heavy import adds 100-250k
objects in a second or two, and full passes then run over and over during it (torch +
torchvision: 4 passes, 148 ms, up to 69 ms each; measured in a fresh process).

Two tools, used around the warm-up imports (:mod:`app.gui.warmup`):

* :func:`hold_full_collections` / :func:`release_full_collections`: no automatic full
  pass while a warm-up import runs (the young generations are still collected, so short
  lived garbage keeps being freed). Holds nest and are thread-safe.
* :func:`freeze`: a young collection, then ``gc.freeze()``: every object alive now moves to
  the permanent generation, which no later collection examines. The modules just imported
  are process-lifetime state, so later full passes skip ~300k objects that could never be
  freed anyway (110 ms -> the size of the rest of the heap).

Trade-off of freezing: a frozen object is never looked at by the cyclic collector again.
Reference counting still frees it once nothing points at it, but if it later ends up in an
unreachable reference cycle, that cycle (and all it holds) stays in memory for the rest
of the session, and its finalizers do not run at exit either. For a Qt wrapper that also
keeps the wrappers of its C++ children registered with sip (see ``tests/qt_teardown.py``
for why that matters). So the app freezes only when the live objects are almost all
permanent: once start-up has settled, and right after a warm-up import (not mid-way
through a project load or an edit), after a young collection so fresh garbage is not
frozen with them. What a freeze keeps for good is the old generation's garbage of that
moment; measured, a full collection then found nothing at start-up (3 of 3 runs) and 30
objects after importing torch + torchvision in a fresh process. Nothing in the app may rely
on a destructor at exit (the settings stores sync explicitly).
"""

from __future__ import annotations

import gc
import logging
import threading
import time

logger = logging.getLogger(__name__)

__all__ = ["freeze", "full_collections_held", "hold_full_collections", "release_full_collections"]

#: The oldest generation's threshold while held: never reached (it counts young passes).
_NEVER = 1 << 30

_lock = threading.Lock()
_holds = 0
_saved_threshold: int | None = None


def hold_full_collections() -> None:
    """No automatic full collection until the matching :func:`release_full_collections`."""
    global _holds, _saved_threshold
    with _lock:
        if _holds == 0:
            young, middle, oldest = gc.get_threshold()
            _saved_threshold = oldest
            gc.set_threshold(young, middle, _NEVER)
        _holds += 1


def release_full_collections() -> None:
    """End one :func:`hold_full_collections`; the last one restores the threshold."""
    global _holds, _saved_threshold
    with _lock:
        if _holds == 0:
            return
        _holds -= 1
        if _holds == 0 and _saved_threshold is not None:
            young, middle, _oldest = gc.get_threshold()
            gc.set_threshold(young, middle, _saved_threshold)
            _saved_threshold = None


def full_collections_held() -> bool:
    """True while a hold is in place."""
    return _holds > 0


def freeze(reason: str, *, full: bool = False) -> None:
    """Collect, then freeze every object alive (see the module doc).

    The collection covers the young generations (only the objects created since the
    last one: about 1 ms), or with ``full`` every generation, so that no garbage at all is
    frozen (a whole pass over the heap; the app does not need it). ``gc.freeze()`` itself
    only moves lists. Nothing is counted at the INFO level: ``gc.get_freeze_count()``
    walks every frozen object (about 30 ns each), and the three counts this logged cost
    the GUI thread 10-16 ms after the torch warm-up and 29-47 ms after the models one.
    """
    started = time.perf_counter()
    gc.collect(2 if full else 1)
    gc.freeze()
    logger.info(
        "Froze the long-lived objects after %s (%.1f ms)",
        reason,
        (time.perf_counter() - started) * 1000.0,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("%d objects frozen in all", gc.get_freeze_count())
