"""Length dashboard: the names other code uses survive the CQ-07 split, and nothing heavier loads.

``dash_app.py`` was split into ``dash_length_layout``, ``dash_length_views`` and
``dash_length_callbacks``. ``DashApp`` keeps every method with the same kind (method
or static method) and parameters, the module keeps the constants it defined, and the
new modules import nothing the dashboard did not import before.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.viz_dash_helpers import build_length_app

REPO_ROOT = Path(__file__).resolve().parents[1]
SPLIT_MODULES = (
    "app.visualization.dash_length_layout",
    "app.visualization.dash_length_views",
    "app.visualization.dash_length_callbacks",
)

# Attributes DashApp defines itself: name -> (kind, parameters), as before the split.
DASH_APP_OWN = {
    "_availability_view": ("function", ("self", "tubes", "dates")),
    "_controls": ("function", ("self",)),
    "_controls_summary": ("function", ("self", "view", "tube", "selected_tubes", "faceted_tubes",
                                       "faceted_dates")),
    "_export_table": ("function", ("self", "view", "tube", "selected_tubes", "viz_options",
                                   "faceted_tubes", "faceted_dates")),
    "_faceted_info": ("staticmethod", ("tubes", "dates")),
    "_faceted_options_card": ("function", ("self",)),
    "_faceted_outputs": ("function", ("self", "tubes", "dates")),
    "_faceted_styles": ("staticmethod", ("tubes",)),
    "_hover_date": ("staticmethod", ("point",)),
    "_legend_patch": ("staticmethod", ("view", "visible")),
    "_lines_tube": ("function", ("self", "selected_tube")),
    "_render_view": ("function", ("self", "view", "selected_tube", "selected_tubes",
                                  "viz_options", "legend_visible", "tubes", "dates")),
    "_setup_callbacks": ("function", ("self",)),
    "_setup_layout": ("function", ("self",)),
    "_thumbnail": ("function", ("self", "tube", "position", "date_str")),
    "_tube_selection_card": ("function", ("self",)),
    "_viz_options_card": ("staticmethod", ()),
}  # fmt: skip
# Inherited from DashVisualizations (the figures); unchanged by the split.
DASH_APP_INHERITED = {
    "_faceted_figure", "_field_average_traces", "_growth_lines_figure",
    "_growth_over_time_figure", "_interval_hover", "_memoized", "_metric_text",
    "_stacked_bar_figure", "create_faceted_depth_profile", "create_stacked_bar_chart",
    "get_tube_date_availability", "show_growth_lines", "show_growth_over_time",
}  # fmt: skip
DASH_APP_INSTANCE_ATTRS = {
    "_date_options", "_tube_options", "app", "available_images", "data_cache",
    "data_processor", "image_manager", "save_directory", "shell", "thumbnail_server",
}  # fmt: skip


def test_dash_app_keeps_every_method_with_its_kind_and_parameters():
    from app.visualization.dash_app import DashApp

    for name, (kind, parameters) in DASH_APP_OWN.items():
        attribute = inspect.getattr_static(DashApp, name)
        assert type(attribute).__name__ == kind, name
        assert tuple(inspect.signature(getattr(DashApp, name)).parameters) == parameters, name
    missing = sorted(name for name in DASH_APP_INHERITED if not hasattr(DashApp, name))
    assert not missing, f"DashApp lost {missing}"


def test_dash_app_instance_keeps_its_attributes(tmp_path):
    dashboard = build_length_app(tmp_path)
    missing = sorted(name for name in DASH_APP_INSTANCE_ATTRS if not hasattr(dashboard, name))
    assert not missing, f"DashApp instances lost {missing}"


def test_module_constants_are_still_importable_from_dash_app():
    import app.visualization.dash_app as dash_app
    from app.visualization import dash_length_callbacks, dash_length_layout, dash_length_views
    from app.visualization.dash_app import LENGTH_SHELL

    assert LENGTH_SHELL is dash_length_layout.LENGTH_SHELL
    assert dash_app._SHOWN is dash_length_layout.SHOWN
    assert dash_app._HIDDEN is dash_length_layout.HIDDEN
    assert dash_app._IMAGES_SHOWN is dash_length_layout.IMAGES_SHOWN
    assert dash_app._GRAPH_STYLE is dash_length_layout.GRAPH_STYLE
    assert dash_app._FACETED_GRAPH_STYLE is dash_length_layout.FACETED_GRAPH_STYLE
    assert dash_app._DATE_TOKEN is dash_length_views.DATE_TOKEN
    assert dash_app._VIEW_PANELS_JS is dash_length_callbacks.VIEW_PANELS_JS


@pytest.mark.parametrize("module", SPLIT_MODULES)
def test_split_module_imports(module):
    __import__(module)


def _run(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_split_modules_load_no_package_the_dashboard_did_not_load():
    # First what dash_app imported before the split (what the moved code already paid
    # for), then the split modules and dash_app: no new top-level module may appear,
    # and none of the heavy stacks the dashboard never needed. dash_app imports flask
    # itself; since Dash 4.2 ``import dash`` no longer does.
    result = _run(
        """
        import sys

        import dash, dash.exceptions, dash_bootstrap_components, flask, pandas
        import app.config
        from app.visualization import (
            dash_data_cache, dash_export, dash_image_utils, dash_shell,
            dash_visualizations, faceted_profile, theme,
        )

        def top():
            return {name.split(".")[0] for name in sys.modules}

        before = top()
        import app.visualization.dash_length_layout
        import app.visualization.dash_length_views
        import app.visualization.dash_length_callbacks
        import app.visualization.dash_app
        new = sorted(top() - before)
        heavy = sorted(top() & {"torch", "torchvision", "cv2", "skimage", "scipy", "PyQt6"})
        print("new=" + ",".join(new))
        print("heavy=" + ",".join(heavy))
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    lines = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert lines["new"] == "", f"the split modules import new packages: {lines['new']}"
    assert lines["heavy"] == "", f"the dashboard loads {lines['heavy']}"
