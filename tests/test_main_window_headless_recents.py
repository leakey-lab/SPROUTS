"""A headless window without explicit settings keeps its recent projects in memory (C5-04).

Before, ``MainWindow()`` offscreen wrote every folder it opened into the user's real
recent-projects list, unlike the window state and the layer preferences.
"""

from __future__ import annotations

from app.settings import MemorySettings
from tests.qt_teardown import delete_widgets


def test_headless_window_keeps_recent_projects_in_memory(app, tmp_path):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    try:
        # Checked first: with the old code this fails before anything is recorded, so the
        # test never writes the user's list.
        assert isinstance(window.recent_projects.settings, MemorySettings)
        window._last_loaded_dir = str(tmp_path)
        window._record_recent_project(2)
        assert [entry.path for entry in window.recent_projects.entries()] == [str(tmp_path)]
    finally:
        delete_widgets([window])
