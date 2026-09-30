"""Deleting Qt widgets in the tests so that nothing of them is left for the next test.

``deleteLater()`` followed by one ``sendPostedEvents(None, DeferredDelete)`` deletes the
C++ widgets but can leave their dead Python wrappers alive for the rest of the run:

- PyQt frees the signal proxies of a deleted sender (what holds a connected lambda or
  bound method) through queued deletions of their own; a full ``sendPostedEvents()``
  delivers those, but it skips a ``deleteLater()`` made outside an event loop, so both
  calls, three rounds (one round can queue more).
- A lambda that captures a widget (``lambda: self.save_mask()``) keeps its dead wrapper
  while the proxy lives, and a dead wrapper keeps the wrappers of the widget's C++-made
  children (scroll bars, viewports) in sip's address map. sip later handed one of those
  out for a new object at the same address (a QBoxLayout typed as a QScrollBar) and the
  run ended in an access violation.
- A wrapper in a reference cycle (page -> chrome -> layout -> page) goes only when the
  cyclic garbage collector runs: ``gc.collect()`` last.
- A reference the teardown still holds (a list of made widgets, a loop variable, a local)
  keeps the wrapper through all of that: ``delete_widgets`` empties the list it is given,
  and fixtures ``del`` their locals before they flush (not one a lambda of theirs uses: it
  would raise in a slot). pytest keeps a fixture's value until after its teardown, so
  ``conftest.py`` flushes once more after every test.
"""

from __future__ import annotations

import gc
from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent


def flush_deleted() -> None:
    """Deliver the pending ``deleteLater()`` calls and the queued deletions of the signal
    proxies they free (three rounds, as one round can queue more), then collect the garbage.
    """
    if QCoreApplication.instance() is not None:
        for _ in range(3):
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QCoreApplication.sendPostedEvents()
    gc.collect()


def _delete(widget, close: bool, prepare: Callable | None) -> None:
    """Prepare, close and ``deleteLater()`` one object (its reference ends with this call)."""
    if sip.isdeleted(widget):
        return  # a test deleted it already
    if prepare is not None:
        prepare(widget)
    if close:
        widget.close()
    widget.deleteLater()


def delete_widgets(widgets: list, *, close: bool = True, prepare: Callable | None = None) -> None:
    """``prepare(widget)`` (when given), ``close()`` (unless ``close`` is False) and
    ``deleteLater()`` every object in ``widgets`` in order, then ``flush_deleted()``.

    ``widgets`` is emptied, so that neither the list nor a loop variable holds a wrapper
    through the collection; objects a test already deleted are skipped.
    """
    while widgets:
        _delete(widgets.pop(0), close, prepare)
    flush_deleted()
