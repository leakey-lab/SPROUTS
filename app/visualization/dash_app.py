"""
Root-length dashboard (Dash): one app over one root_lengths.csv.

:class:`DashApp` builds the app and holds what its parts share: the data
(:class:`.dash_data_cache.DataCache`), the figures (the
:class:`.dash_visualizations.DashVisualizations` mixin), the hover-image index and
its thumbnail server. The parts live in their own modules:

- :mod:`.dash_length_layout`: the page (control cards, chart row, stores);
- :mod:`.dash_length_views`: what each view shows for a selection;
- :mod:`.dash_length_callbacks`: the callbacks that wire them to the page.

The page chrome comes from :mod:`.dash_shell`, hover thumbnails from
:mod:`.dash_image_utils`. Component ids, callback Outputs and figure customdata
are a contract with the renderer and the embedding window: keep them stable.
"""

import logging
from typing import Any

import dash_bootstrap_components as dbc
import pandas as pd
from dash import html

from app.config import DASH_HOST, DASH_LENGTH_PORT

from . import dash_export, theme
from . import dash_length_callbacks as length_callbacks
from . import dash_length_layout as length_layout
from . import dash_length_views as length_views
from .dash_data_cache import DataCache
from .dash_image_utils import ThumbnailServer, build_available_images_map
from .dash_length_layout import LENGTH_SHELL
from .dash_shell import DashboardShell, create_dash_app, exclusions_note, tube_options
from .dash_visualizations import DashVisualizations

theme.use("sprouts")

logger = logging.getLogger(__name__)

# Defined here before the dashboard was split into modules (CQ-07); still importable.
_SHOWN = length_layout.SHOWN
_HIDDEN = length_layout.HIDDEN
_IMAGES_SHOWN = length_layout.IMAGES_SHOWN
_GRAPH_STYLE = length_layout.GRAPH_STYLE
_FACETED_GRAPH_STYLE = length_layout.FACETED_GRAPH_STYLE
_DATE_TOKEN = length_views.DATE_TOKEN
_VIEW_PANELS_JS = length_callbacks.VIEW_PANELS_JS


class DashApp(DashVisualizations):
    """The root-length dashboard: one Dash app over one root_lengths.csv.

    The underscore methods below delegate to the layout and view-model modules and
    are kept for callers of this class; the callbacks call those modules directly.
    """

    def __init__(self, data_processor: Any, save_directory: str, image_manager=None):
        self.data_processor = data_processor
        self.save_directory = save_directory
        self.image_manager = image_manager

        # (tube, position, date) -> image path, parsed on first use (PV-17).
        self.available_images = build_available_images_map(image_manager)
        self.data_cache = DataCache(data_processor)
        self._tube_options = tube_options(self.data_cache.tubes)
        self._date_options = [
            {"label": date.strftime("%Y-%m-%d"), "value": date.isoformat()}
            for date in self.data_cache.dates
        ]

        self.shell = DashboardShell(LENGTH_SHELL)
        self.app = create_dash_app(__name__, title="SPROUTS — Root Length")
        # Hover thumbnails are served by URL from this app's own server (PV-09).
        self.thumbnail_server = ThumbnailServer(self.available_images.thumbnails)
        self.thumbnail_server.register(self.app.server)

        self._setup_layout()
        self._setup_callbacks()
        # Parsed while the page loads, not by the first Depth Profile view (~95 ms).
        self.available_images.parse_in_background()

    def get_encoded_image(self, tube: int, position: int, date: pd.Timestamp) -> str:
        """Hover thumbnail as a ``data:image/jpeg`` URL (``""`` if unavailable)."""
        try:
            key = (tube, position, date.strftime("%Y.%m.%d"))
        except (AttributeError, ValueError):
            return ""
        return self.available_images.thumbnails.data_url(key)

    # ---- layout (dash_length_layout) ---------------------------------------------

    def _setup_layout(self) -> None:
        """Build the page: shell chrome around the length controls and chart."""
        self.app.layout = length_layout.page(
            self.shell,
            self._tube_options,
            self._date_options,
            notice=exclusions_note(self.data_processor),
        )

    def _controls(self) -> list:
        return length_layout.controls(self._tube_options, self._date_options)

    def _tube_selection_card(self) -> dbc.Card:
        """Tube pickers of the Growth Over Time view."""
        return length_layout.tube_selection_card(self._tube_options)

    _viz_options_card = staticmethod(length_layout.viz_options_card)

    def _faceted_options_card(self) -> dbc.Card:
        """Date rows and tube columns of the Depth Profile Grid view."""
        return length_layout.faceted_options_card(self._tube_options, self._date_options)

    _faceted_info = staticmethod(length_layout.faceted_info)

    def _availability_view(self, tubes: list, dates: list):
        """Which selected tubes were measured on which dates."""
        return length_layout.availability_view(self.get_tube_date_availability(), tubes, dates)

    def _thumbnail(self, tube: int, position: int, date_str: str) -> html.Div:
        """One hover image (served by URL) with its position label."""
        return length_layout.thumbnail(self.thumbnail_server, tube, position, date_str)

    # ---- view model (dash_length_views) ------------------------------------------

    def _lines_tube(self, selected_tube: Any) -> int | None:
        """The tube the Depth Profile view plots: the dropdown's, else the first."""
        return length_views.lines_tube(self.data_cache.tubes, selected_tube)

    def _controls_summary(self, view, tube, selected_tubes, faceted_tubes, faceted_dates) -> str:
        """One line for the collapsed control strip (PV-15)."""
        return length_views.controls_summary(
            self, view, tube, selected_tubes, faceted_tubes, faceted_dates
        )

    def _render_view(
        self, view, selected_tube, selected_tubes, viz_options, legend_visible, tubes, dates
    ) -> tuple:
        """Outputs of update_visualization for ``view`` (figure, message, styles)."""
        return length_views.render_view(
            self, view, selected_tube, selected_tubes, viz_options, legend_visible, tubes, dates
        )

    def _faceted_outputs(self, tubes, dates) -> tuple:
        """(figure, message, tube-selector style) of the Depth Profile Grid view."""
        return length_views.faceted_outputs(self, tubes, dates)

    _faceted_styles = staticmethod(length_views.faceted_styles)
    _legend_patch = staticmethod(length_views.legend_patch)

    def _export_table(
        self, view, tube, selected_tubes, viz_options, faceted_tubes, faceted_dates
    ) -> dash_export.ExportTable | None:
        """The rows behind ``view``, from the same DataCache aggregates as the chart."""
        return length_views.export_table(
            self, view, tube, selected_tubes, viz_options, faceted_tubes, faceted_dates
        )

    _hover_date = staticmethod(length_views.hover_date)

    # ---- callbacks (dash_length_callbacks) ---------------------------------------

    def _setup_callbacks(self) -> None:
        """Register every callback of the dashboard."""
        length_callbacks.register_callbacks(self)

    def run_server(self):
        """Run the Dash server (legacy entry point; the app uses DashServerThread).

        Errors are logged (with traceback) and re-raised so the caller is not
        left silently without a running server (F-008).
        """
        try:
            self.app.run(debug=False, host=DASH_HOST, port=DASH_LENGTH_PORT, threaded=True)
        except Exception:
            logger.exception("Dash length-visualization server failed to run")
            raise
