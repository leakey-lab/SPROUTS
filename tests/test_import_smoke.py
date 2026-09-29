"""
Import-smoke test: every app module must import cleanly.

This is the primary guard for dependency upgrades (dash, PyQt6, numpy, pandas,
plotly, etc.) where the regression suite's pure-function coverage does not reach
GUI / visualization wiring. A broken import after an upgrade fails here.

GUI modules only need a QApplication for *instantiation*, not import, so importing
is safe in a headless/offscreen environment.
"""

import importlib
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_MODULES = [
    "app.mask_model.model",
    "app.inference.skeleton_inference",
    "app.inference.root_length_inference_handler",
    "app.inference.root_area_inference_handler",
    "app.handlers.mask_generation_handler",
    "app.handlers.generate_skeleton_handler",
    "app.handlers.skeleton_handler",
    "app.handlers.mask_handler",
    "app.data_processing.data_processor",
    "app.data_processing.data_processor_area",
    "app.gui.skeleton_graph_model",
    "app.gui.skeleton_correction_graphics_view",
    "app.gui.mask_graphics_view",
    "app.gui.mask_drawing_tools",
    "app.gui.mask_cursor_utils",
    "app.gui.image_manager",
    "app.gui.image_normalization_interface",
    "app.gui.file_tree_manager",
    "app.gui.ui_panels",
    "app.gui.task_progress",
    "app.gui.empty_state",
    "app.gui.display_controller",
    "app.gui.mask_tracing_interface",
    "app.gui.skeleton_correction_interface",
    "app.gui.visualization_manager",
    "app.gui.main_window",
    "app.visualization.dash_image_utils",
    "app.visualization.dash_data_cache",
    "app.visualization.dash_visualizations",
    "app.visualization.dash_app",
    "app.visualization.dash_app_area",
    "app.visualization.root_length_visulization",
    "app.visualization.root_area_visualization",
    # Modules added by the UI/perf refinement packages (and older ones never listed).
    "app.config",
    "app.data_processing.measurement_index",
    "app.design.tokens",
    "app.editing.geometry",
    "app.editing.history",
    "app.enhancement",
    "app.gt.export",
    "app.gt.registry",
    "app.gt.store",
    "app.gui._lazy",
    "app.gui.about_dialog",
    "app.gui.brush_preview",
    "app.gui.canvas_background",
    "app.gui.canvas_gl",
    "app.gui.canvas_item",
    "app.gui.canvas_view",
    "app.gui.class_editor_dialog",
    "app.gui.class_panel",
    "app.gui.error_hooks",
    "app.gui.export_dataset_dialog",
    "app.gui.feedback",
    "app.gui.gt_tracing_interface",
    "app.gui.library_index",
    "app.gui.loading_overlay",
    "app.gui.metrics_bar",
    "app.gui.panels",
    "app.gui.patch_history",
    "app.gui.recent_projects",
    "app.gui.shell_chrome",
    "app.gui.shortcuts",
    "app.gui.skeleton_document",
    "app.gui.skeleton_overlay",
    "app.gui.skeleton_tools",
    "app.gui.ui_feedback",
    "app.gui.welcome_screen",
    "app.gui.widgets.buttons",
    "app.gui.widgets.controls",
    "app.gui.widgets.effects",
    "app.gui.widgets.focus",
    "app.gui.widgets.icons",
    "app.gui.widgets.overlay_geometry",
    "app.gui.widgets.overlay_layout",
    "app.gui.widgets.overlays",
    "app.gui.widgets.text_fit",
    "app.gui.widgets.toasts",
    "app.gui.widgets.tokens",
    "app.gui.window_state",
    "app.handlers.generation_base",
    "app.handlers.job_registry",
    "app.handlers.window_feedback",
    "app.handlers.worker_base",
    "app.inference.batch_engine",
    "app.inference.batch_pipeline",
    "app.inference.mask_inference",
    "app.inference.mask_origin",
    "app.inference.measure_run",
    "app.inference.metric_thread",
    "app.inference.metrics",
    "app.inference.run_record",
    "app.inference.runtime",
    "app.io_utils",
    "app.logging_config",
    "app.masks.store",
    "app.models",
    "app.naming",
    "app.project_layout",
    "app.provenance",
    "app.user_paths",
    "app.visualization._viz_server",
    "app.visualization.dash_export",
    "app.visualization.dash_shell",
    "app.visualization.faceted_profile",
    "app.visualization.figure_memo",
    "app.visualization.theme",
    "app.visualization.viz_window",
]


@pytest.mark.parametrize("modname", APP_MODULES)
def test_module_imports(modname):
    # The mask_handler -> mask_tracing_interface -> main_window -> mask_handler
    # cycle was broken in R5 (handlers imported lazily in MainWindow.__init__),
    # so every module now imports cleanly standalone.
    importlib.import_module(modname)
