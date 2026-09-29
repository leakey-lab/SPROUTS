"""Dependencies every batch handler needs: a ``UiFeedback`` and the ``JobRegistry``.

:class:`HandlerDependencies` is the one place that resolves them, in this order:
the value injected into the constructor (tests, tools), then the window's own
``main_window.feedback`` / ``main_window.jobs``, then a cached local stand-in
(``NullFeedback`` / a private ``JobRegistry``) so a partial window still works.
"""

from __future__ import annotations

from typing import Any

from app.gui.feedback import NullFeedback, UiFeedback
from app.handlers.job_registry import JobRegistry


class HandlerDependencies:
    """Mixin: ``feedback`` and ``jobs`` resolved from injection, the window, or a stand-in."""

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


__all__ = ["HandlerDependencies"]
