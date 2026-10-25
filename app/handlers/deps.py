"""Dependencies every batch handler needs: a ``UiFeedback`` and the ``JobRegistry``.

:class:`HandlerDependencies` is the one place that resolves them, in this order:
the value injected into the constructor (tests, tools), then the window's own
``main_window.feedback`` / ``main_window.jobs``, then a cached local stand-in
(``NullFeedback`` / a private ``JobRegistry``) so a partial window still works.
"""

from __future__ import annotations

import os
from typing import Any

from app.gui.feedback import NullFeedback, UiFeedback
from app.handlers.job_registry import JobRegistry

#: How long the status line keeps "Saved <full path>" after a toast named only the
#: file and its folder: long enough to hover it for the tooltip with the whole path (W13).
SAVED_PATH_STATUS_MS = 15_000

#: Every batch job's refusal while a folder loads (``refuse_while_loading``).
LOADING_REFUSED = "A folder is loading. Try again when it has loaded."


def folder_label(folder: str, root: str | None = None) -> str:
    """``folder`` as a toast names it: relative to the project ``root`` when inside it
    (``skeletons``, ``output\\mask``), else the folder's own name (W13).

    A toast wraps at spaces, so a full path is one word it can only elide in the
    middle; the full path goes to the status line instead.
    """
    folder = os.path.normpath(folder)
    if root:
        try:
            rel = os.path.relpath(folder, os.path.normpath(root))
        except ValueError:  # another drive (Windows)
            rel = ""
        if rel and rel != os.curdir and not rel.startswith(os.pardir):
            return rel
    return os.path.basename(folder) or folder


class HandlerDependencies:
    """Mixin: ``feedback`` and ``jobs`` resolved from injection, the window, or a stand-in;
    ``refuse_while_loading`` keeps every job from starting while a folder loads."""

    def __init__(
        self,
        main_window: Any,
        *,
        feedback: UiFeedback | None = None,
        jobs: JobRegistry | None = None,
    ) -> None:
        self.main_window = main_window
        self._feedback = feedback
        self._jobs = jobs
        self._fallback_feedback: UiFeedback | None = None
        self._fallback_jobs: JobRegistry | None = None

    @property
    def feedback(self) -> UiFeedback:
        found = self._feedback or getattr(self.main_window, "feedback", None)
        if found is not None:
            return found
        if self._fallback_feedback is None:
            self._fallback_feedback = NullFeedback()
        return self._fallback_feedback

    @property
    def jobs(self) -> JobRegistry:
        found = self._jobs or getattr(self.main_window, "jobs", None)
        if found is not None:
            return found
        if self._fallback_jobs is None:  # serializes at least this handler's jobs
            self._fallback_jobs = JobRegistry()
        return self._fallback_jobs

    def refuse_while_loading(self) -> bool:
        """True (and a warning toast) while the window loads a folder (R7, O15).

        A job started then would bind to the project being left, or to a half-scanned
        one: no job starts until the folder has loaded.
        """
        if getattr(self.main_window, "_loading_project", False) is True:
            self.feedback.notify(LOADING_REFUSED, "warn")
            return True
        return False


__all__ = ["HandlerDependencies"]
