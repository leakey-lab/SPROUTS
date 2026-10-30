"""Root-area Dash dashboard window."""

from __future__ import annotations

import functools

from app.config import DASH_AREA_PORT

from ._viz_server import _DashServerThreadBase
from .viz_window import Builder, DashVizWindow, require_rows


def build_area_dashboard(csv_path: str, save_directory: str):
    """``(DataProcessorArea, DashAppArea)`` for a ``root_areas.csv``; runs on the init worker.

    pandas, dash and plotly are imported here: off the GUI thread, and only
    when a dashboard is opened.
    """
    from app.data_processing.data_processor_area import DataProcessorArea

    from .dash_app_area import DashAppArea

    processor = require_rows(DataProcessorArea(csv_path))
    return processor, DashAppArea(processor, save_directory)


class DashServerThreadArea(_DashServerThreadBase):
    """Werkzeug server thread for the root-area Dash app."""

    def __init__(self, dash_app, port: int = DASH_AREA_PORT):
        super().__init__(dash_app, port, object_name="DashServerThreadArea")


class RootAreaVisualization(DashVizWindow):
    """Root-area dashboard (``root_areas.csv``) embedded in the main window."""

    window_title = "Root Area Visualization"
    download_prefix = "root_area_plot"
    default_port = DASH_AREA_PORT
    server_thread_class = DashServerThreadArea

    def _dashboard_builder(self) -> Builder:
        return functools.partial(build_area_dashboard, self.csv_path, self.save_directory)
