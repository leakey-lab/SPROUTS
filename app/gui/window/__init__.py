"""The parts :class:`~app.gui.main_window.MainWindow` is built from (review CQ-07).

One module per responsibility of the window: the library navigator (``navigator``), the
project loader (``project_loader``), the metrics controller (``metrics``), the editor pages
(``editors``), the stages (``stages``), the job buttons (``job_buttons``), closing the
window (``lifecycle``) and its widgets (``body``); helpers they share are in ``_common``.

Nothing is imported here: importing the package costs nothing.
"""
