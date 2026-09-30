"""
UI Panel creation methods for the main window.
Handles left panel (controls/tree) and right panel (display area) creation.
"""

import os

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from app.gui import file_tree_manager
from app.gui.library_index import LibrarySearchController
from app.gui.widgets import tokens
from app.gui.widgets.icons import load_icon
from resources.resource_utils import project_root


def get_icon_path(icon_name: str) -> str:
    """
    Get the full path to an icon file.

    Args:
        icon_name: Name of the icon file (with extension)

    Returns:
        Full path to the icon file
    """
    # Check in resources/icons first
    base_path = str(project_root())
    resources_icons_path = os.path.join(base_path, "resources", "icons", icon_name)
    if os.path.exists(resources_icons_path):
        return resources_icons_path

    # Check in app/utils for XML icons
    app_utils_path = os.path.join(base_path, "app", "utils", icon_name)
    if os.path.exists(app_utils_path):
        return app_utils_path

    return ""


def create_icon_button(icon_path: str, tooltip: str, size: int = 100) -> QToolButton:
    """
    Create a QToolButton with an icon and tooltip.

    Args:
        icon_path: Path to the icon file (SVG or XML)
        tooltip: Tooltip text for the button
        size: Button size in pixels

    Returns:
        Configured QToolButton
    """
    button = QToolButton()
    button.setToolTip(tooltip)
    button.setFixedSize(QSize(size, size))
    # Use full button size for icon
    button.setIconSize(QSize(size, size))

    if icon_path and os.path.exists(icon_path):
        button.setIcon(QIcon(icon_path))

    return button


def create_text_button(text: str, tooltip: str) -> QPushButton:
    """
    Create a QPushButton with text and tooltip.

    Args:
        text: Button text label
        tooltip: Tooltip text for the button

    Returns:
        Configured QPushButton
    """
    button = QPushButton(text)
    button.setToolTip(tooltip)
    return button


def create_section_label(text: str, color: str | None = None) -> QLabel:
    """
    Create a styled section label for button groups.

    Args:
        text: Label text
        color: Color for the label text (default ``tokens.ACCENT``)

    Returns:
        Styled QLabel
    """
    color = color or tokens.ACCENT
    label = QLabel(text)
    label.setStyleSheet(f"""
        QLabel {{
            color: {color};
            font-size: 8pt;
            font-weight: bold;
            padding: 2px 0px;
            margin-top: 4px;
        }}
    """)
    return label


def create_left_panel(main_window) -> QWidget:
    """
    Create the left panel with toolbar buttons, view mode selector, and file tree.

    Args:
        main_window: MainWindow instance to connect signals and store widgets

    Returns:
        QWidget: The configured left panel widget
    """
    left_widget = QWidget()
    layout = QVBoxLayout(left_widget)
    layout.setSpacing(6)
    layout.setContentsMargins(8, 8, 8, 8)

    # Style for text toolbar buttons. These now sit in the 46px chrome row, so
    # the chunky 2px-bordered Dracula style is gone (it was also the last
    # off-brand surface in the shell).
    text_button_style = f"""
        QPushButton {{
            background-color: {tokens.BG_2};
            color: {tokens.TEXT};
            border: 1px solid {tokens.BORDER_STRONG};
            border-radius: 8px;
            padding: 5px 13px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {tokens.BG_3};
            border: 1px solid {tokens.ACCENT_LINE};
        }}
        QPushButton:pressed {{ background-color: {tokens.BG_ACTIVE}; }}
        {tokens.focus_rule("QPushButton")}
    """

    # ========== LOAD IMAGES BUTTON (Full width, prominent) ==========
    load_icon_path = get_icon_path("folder-open.svg")
    main_window.load_images_button = QPushButton("  Load Images")
    if load_icon_path:
        main_window.load_images_button.setIcon(QIcon(load_icon_path))
        main_window.load_images_button.setIconSize(QSize(24, 24))
    main_window.load_images_button.setStyleSheet(text_button_style)
    main_window.load_images_button.setToolTip("Load images from a directory")
    main_window.load_images_button.clicked.connect(main_window.load_images)
    # FIX1: Welcome screen is the sole load entry. The attr/connect stay alive
    # for any code referencing them, but the button is not shown in the shell.
    main_window.load_images_button.setVisible(False)

    # ====================================================================== #
    # FIX4: The stage action buttons are constructed here (so their mw.<attr>
    # names and .clicked.connect() targets stay intact) but are NOT added to
    # the left layout. build_action_bar() reparents the real button objects
    # into the stage-aware action bar. Construction-only here.
    # ====================================================================== #

    # Generate ML Masks button (kept wired + reparented; the Generate stage now
    # uses the combined generate_all_button below as its single entry point)
    main_window.generate_mask_button = create_text_button(
        "Generate Masks",
        "Generate Masks\n\nUse AI machine learning to automatically generate segmentation masks for root images. This will process all loaded images using a trained neural network model.",
    )
    main_window.generate_mask_button.setStyleSheet(text_button_style)
    main_window.generate_mask_button.clicked.connect(
        main_window.mask_generation_handler.generate_masks
    )

    # Generate Skeleton button (kept wired + reparented)
    main_window.generate_button = create_text_button(
        "Generate Skeleton",
        "Generate Skeleton\n\nPredict root skeletons directly from the original images "
        "(Pix2Pix skeletonizer) for length measurement. Independent of masks: mask "
        "corrections change root area only.",
    )
    main_window.generate_button.setStyleSheet(text_button_style)
    main_window.generate_button.clicked.connect(main_window.skeleton_handler.generate_skeleton)

    # Single entry point for generation — masks, then skeletons, in one run.
    main_window.generate_all_button = create_text_button(
        "Generate Mask + Skeleton",
        "Generate Mask + Skeleton\n\nRun the ResNet segmentation model over all "
        "loaded images (masks -> root area), then predict skeletons from the same "
        "original images (Pix2Pix -> root length). One click runs both.",
    )
    main_window.generate_all_button.setStyleSheet(text_button_style)
    main_window.generate_all_button.clicked.connect(main_window.generate_all)

    # Calculate Root Length button (Measure stage)
    main_window.calculate_length_button = create_text_button(
        "Root Length",
        "Calculate Root Length\n\nMeasure total root length from skeleton data. Processes all skeleton files and calculates cumulative length measurements for each image.",
    )
    main_window.calculate_length_button.setStyleSheet(text_button_style)
    main_window.calculate_length_button.clicked.connect(main_window.calculate_root_length)

    # Calculate Root Area button (Measure stage)
    main_window.calculate_area_button = create_text_button(
        "Root Area",
        "Calculate Root Area\n\nMeasure root surface area from segmentation masks. Processes all mask files and calculates pixel-based area measurements for each image.",
    )
    main_window.calculate_area_button.setStyleSheet(text_button_style)
    main_window.calculate_area_button.clicked.connect(main_window.calculate_root_area)

    # Visualize Root Length button (kept for .setText callers; Visualize stage
    # uses the SegmentedControl in the action bar)
    main_window.visualize_root_length_button = create_text_button(
        "Visualize Root Length",
        "Visualize Root Length\n\nOpen interactive dashboard with line charts showing root length analysis. Compare length measurements across different images, tubes, and dates.",
    )
    main_window.visualize_root_length_button.setStyleSheet(text_button_style)
    main_window.visualize_root_length_button.clicked.connect(
        main_window.toggle_root_length_visualization
    )

    # Visualize Root Area button (kept for .setText callers)
    main_window.visualize_root_area_button = create_text_button(
        "Visualize Root Area",
        "Visualize Root Area\n\nOpen interactive dashboard with bar charts showing root area analysis. Compare area measurements across different images, tubes, and dates.",
    )
    main_window.visualize_root_area_button.setStyleSheet(text_button_style)
    main_window.visualize_root_area_button.clicked.connect(
        main_window.toggle_root_area_visualization
    )

    # ========== VIEW MODE COMBO (hidden live driver) ==========
    # The combo stays the single source of truth for the display mode (indices
    # 0/1/2 -> display_controller.update_display_mode). It is hidden; a cosmetic
    # SegmentedControl in the action bar forwards into it (see build_action_bar).
    main_window.view_mode_combo = QComboBox()
    main_window.view_mode_combo.addItems(["Single Image", "Overlay", "Side by Side"])
    main_window.view_mode_combo.currentIndexChanged.connect(
        main_window.display_controller.update_display_mode
    )
    main_window.view_mode_combo.setVisible(False)
    # An invisible widget must not hold a layout slot (and its spacing); park it
    # on the panel without adding it to the column.
    main_window.view_mode_combo.setParent(left_widget)

    # ========== SEARCH FIELD ==========
    main_window.library_search = QLineEdit()
    main_window.library_search.setPlaceholderText("Search images…")
    main_window.library_search.setAccessibleName("Search images")
    main_window.library_search.setClearButtonEnabled(True)
    main_window.library_search.setStyleSheet(f"""
        QLineEdit {{
            background-color: {tokens.BG_3};
            color: {tokens.TEXT};
            border: 1px solid {tokens.BORDER};
            border-radius: 7px;
            padding: 6px 10px;
            font-size: 12px;
        }}
        QLineEdit:focus {{ border: 1px solid {tokens.ACCENT_LINE}; }}
    """)
    # Debounced (one filter per typing burst) with a match count under the field.
    main_window.library_search_status = QLabel()
    main_window.library_search_status.setStyleSheet(
        f"color: {tokens.TEXT_FAINT}; font-size: 11px; padding: 0 2px;"
    )
    main_window.library_search_controller = LibrarySearchController(
        main_window.library_search,
        lambda text: filter_file_list(main_window, text),
        main_window.library_search_status,
    )

    # ========== SEARCH + TREE CONTROLS (one row) ==========
    # Search field takes the slack; the expand/collapse controls are icon-only
    # ("Expand" / "Collapse" are self-evident from the glyphs) and sit inline
    # instead of owning a row of their own.
    tree_controls_layout = QHBoxLayout()
    tree_controls_layout.setSpacing(5)
    tree_controls_layout.addWidget(main_window.library_search, 1)

    tree_button_style = f"""
        QPushButton {{
            background-color: {tokens.BG_2};
            color: {tokens.TEXT_MUTED};
            border: 1px solid {tokens.BORDER};
            border-radius: 7px;
            padding: 0px;
        }}
        QPushButton:hover {{
            background-color: {tokens.BG_3};
            color: {tokens.TEXT};
            border: 1px solid {tokens.BORDER_STRONG};
        }}
        {tokens.focus_rule("QPushButton")}
    """

    # Icon-only: Qt names a button after its text, so a screen reader needs the name set.
    main_window.expand_all_button = QPushButton()
    main_window.expand_all_button.setIcon(load_icon("expand", tokens.TEXT_MUTED, 15))
    main_window.expand_all_button.setToolTip("Expand all groups")
    main_window.expand_all_button.setAccessibleName("Expand all groups")
    main_window.expand_all_button.setStyleSheet(tree_button_style)
    main_window.expand_all_button.setFixedSize(30, 30)
    main_window.expand_all_button.clicked.connect(lambda: main_window.file_list.expandAll())
    tree_controls_layout.addWidget(main_window.expand_all_button)

    main_window.collapse_all_button = QPushButton()
    main_window.collapse_all_button.setIcon(load_icon("collapse", tokens.TEXT_MUTED, 15))
    main_window.collapse_all_button.setToolTip("Collapse all groups")
    main_window.collapse_all_button.setAccessibleName("Collapse all groups")
    main_window.collapse_all_button.setStyleSheet(tree_button_style)
    main_window.collapse_all_button.setFixedSize(30, 30)
    main_window.collapse_all_button.clicked.connect(lambda: main_window.file_list.collapseAll())
    tree_controls_layout.addWidget(main_window.collapse_all_button)

    layout.addLayout(tree_controls_layout)
    layout.addWidget(main_window.library_search_status)

    # The tree's own header (Field → Tube → Date → Images) names the panel, and
    # it is the only left panel in the app — no redundant "Image Library" label.

    # ========== FILE TREE ==========
    main_window.file_list = QTreeWidget()
    main_window.file_list.setAccessibleName("Image library")
    main_window.file_list.setHeaderLabels(["Field → Tube → Date → Images"])
    main_window.file_list.itemClicked.connect(main_window.on_tree_item_clicked)
    # Keyboard browsing (arrows, Home/End) loads the image after a short debounce.
    main_window.file_list.currentItemChanged.connect(main_window.on_tree_current_changed)
    main_window.file_list.setAnimated(True)
    main_window.file_list.setIndentation(20)

    # Names follow the splitter: the name column takes the slack, other columns
    # size to contents. Image rows are short ("L44 · 11:15:30"; the full stem is
    # in the tooltip and the metrics bar), so a narrow panel cuts only the end.
    main_window.file_list.setTextElideMode(Qt.TextElideMode.ElideRight)
    main_window.file_list.setUniformRowHeights(True)
    _hdr = main_window.file_list.header()
    _hdr.setStretchLastSection(False)
    _hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for _c in range(1, main_window.file_list.columnCount()):
        _hdr.setSectionResizeMode(_c, QHeaderView.ResizeMode.ResizeToContents)

    # Token-styled tree: zebra striping is dropped (hover + selection + stripes
    # is three competing row treatments in a ~240px panel).
    main_window.file_list.setStyleSheet(f"""
        QTreeWidget {{
            background-color: {tokens.BG_1};
            color: {tokens.TEXT};
            border: 1px solid {tokens.BORDER};
            border-radius: 8px;
            padding: 4px;
            font-size: 9pt;
            outline: none;
        }}
        QTreeWidget::item {{ padding: 4px; border-radius: 5px; }}
        QTreeWidget::item:hover {{ background-color: {tokens.BG_2}; }}
        QTreeWidget::item:selected {{
            background-color: {tokens.SEL_SOFT};
            color: {tokens.TEXT};
        }}
        QHeaderView::section {{
            background-color: {tokens.BG_1};
            color: {tokens.TEXT_FAINT};
            border: none;
            border-bottom: 1px solid {tokens.BORDER};
            padding: 5px 6px;
            font-size: 8pt;
            font-weight: 600;
        }}
        {tokens.focus_rule("QTreeWidget")}
    """)
    # Per-window index: O(1) lookup/navigation, fast filter, status badges.
    file_tree_manager.library_index_for(main_window)

    layout.addWidget(main_window.file_list, 1)

    return left_widget


def filter_file_list(main_window, query: str) -> int:
    """Filter the image-library tree (case-insensitive contains); returns the match count.

    Images match on their row text or full stem; a group whose own label matches keeps
    all of its images; an empty query shows everything. Parents of matches stay visible
    and small result sets are expanded (see :meth:`LibraryIndex.apply_filter`).
    """
    if getattr(main_window, "file_list", None) is None:
        return 0
    count = file_tree_manager.library_index_for(main_window).apply_filter(query or "")
    filtered = getattr(main_window, "on_library_filtered", None)
    if callable(filtered):
        filtered()  # positions ("Image i / n") count the images the search shows
    return count


def create_right_panel(main_window) -> QWidget:
    """
    Create the right panel with the display area.

    Args:
        main_window: MainWindow instance to connect display controller

    Returns:
        QWidget: The configured right panel widget
    """
    from .empty_state import EmptyStateWidget

    container = QWidget()
    outer = QVBoxLayout(container)
    outer.setContentsMargins(0, 0, 0, 0)

    # Page stack: index 0 = real display area, index 1 = empty-state placeholder.
    page_stack = QStackedWidget()

    display_page = QWidget()
    main_window.display_area = main_window.display_controller.setup_display_area(display_page)

    # Read-only metrics strip mounted as a sibling *below* the display canvas,
    # inside the display page itself. This leaves the QStackedWidget page
    # indices (0 = display, 1 = empty state) untouched (Track M2).
    from .metrics_bar import MetricsBar

    main_window.metrics_bar = MetricsBar()
    page_layout = display_page.layout()
    if page_layout is not None:
        page_layout.addWidget(main_window.metrics_bar)

    page_stack.addWidget(display_page)

    empty_state = EmptyStateWidget(on_load=main_window.load_images)
    page_stack.addWidget(empty_state)

    # Start on the empty state until images are loaded; MainWindow flips to the
    # display page in on_loading_finished.
    page_stack.setCurrentIndex(1)

    main_window.display_page_stack = page_stack
    main_window.empty_state = empty_state

    outer.addWidget(page_stack)
    return container
