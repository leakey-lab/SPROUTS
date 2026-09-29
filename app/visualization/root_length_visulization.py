"""Root-length Dash dashboard window.

The module keeps its historical spelling ("visulization"): other modules and
existing scripts import it by this exact name.
"""

from __future__ import annotations

import functools

from app.config import DASH_LENGTH_PORT

from ._viz_server import _DashServerThreadBase
from .viz_window import Builder, DashVizWindow, require_rows


def build_length_dashboard(csv_path: str, save_directory: str, image_manager):
    """``(DataProcessor, DashApp)`` for a ``root_lengths.csv``; runs on the init worker.

    pandas, dash and plotly are imported here: off the GUI thread, and only
    when a dashboard is opened.
    """
    from app.data_processing.data_processor import DataProcessor

    from .dash_app import DashApp

    processor = require_rows(DataProcessor(csv_path))
    return processor, DashApp(processor, save_directory, image_manager)


class DashServerThread(_DashServerThreadBase):
    """Werkzeug server thread for the root-length Dash app."""

    def __init__(self, dash_app, port: int = DASH_LENGTH_PORT):
        super().__init__(dash_app, port, object_name="DashServerThreadLength")


class RootLengthVisualization(DashVizWindow):
    """Root-length dashboard (``root_lengths.csv``) embedded in the main window."""

    window_title = "Root Length Visualization"
    download_prefix = "root_length_plot"
    default_port = DASH_LENGTH_PORT
    server_thread_class = DashServerThread

    def __init__(self, csv_path: str, image_manager, parent=None):
        # Set before the base class starts the build, which reads it.
        self.image_manager = image_manager
        super().__init__(csv_path, parent)

    def _dashboard_builder(self) -> Builder:
        return functools.partial(
            build_length_dashboard, self.csv_path, self.save_directory, self.image_manager
        )
