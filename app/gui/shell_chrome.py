"""SPROUTS guided-shell chrome (presentation-only).

ONE chrome band instead of three. ``build_topbar`` merges what used to be the
titlebar (48px, a logo and nothing else), the stage ribbon (52px) and the action
bar (52px, the left half of it a hint sentence restating the stage already
highlighted in the ribbon above it) into a single 46px row:

    [logo SPROUTS] | Library  Trace GT  Generate  Correct Mask  … | <stage actions> | [load]

152px of chrome becomes 46px. On the 1200x700 window that is ~19% of the window
height handed back to the image canvas. Every stage button keeps its name and
icon — no step numbers, nothing collapsed to a bare glyph — and the per-stage
explanation that used to occupy the action bar now lives in each button's
tooltip.

The pipeline has seven stages: Library, then Trace GT (hand-traced multi-class
ground truth for a segmentation dataset, independent of the ML pipeline), then
the measurement pipeline. Mask and skeleton generation are one button
(``mw.generate_all_button``), and the two corrections are separate stages
because they open different editors.

Wiring is unchanged. Stage buttons forward to the same MainWindow handlers,
``mw.ribbon_buttons`` keeps its seven canonical keys in order, and
``mw._populate_action_bar`` / ``mw._activate_action_stage`` keep their names and
contracts.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from app.gui.mask_tracing_interface import _GHOST_QSS
from app.gui.widgets import SegmentedControl, tokens
from app.gui.widgets.icons import load_icon, load_pixmap

TOPBAR_HEIGHT = 46
STATUSLINE_HEIGHT = 28


def _band(object_name: str, *, height: int | None = None) -> QFrame:
    band = QFrame()
    band.setObjectName(object_name)
    band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    band.setStyleSheet(f"""
        QFrame#{object_name} {{
            background-color: {tokens.BG_1};
            border: none;
            border-bottom: 1px solid {tokens.BORDER};
        }}
        QLabel {{ background: transparent; }}
    """)
    if height is not None:
        band.setFixedHeight(height)
    return band


def _vrule() -> QFrame:
    rule = QFrame()
    rule.setFixedSize(1, 18)
    rule.setStyleSheet(f"background-color: {tokens.BORDER};")
    return rule


# --------------------------------------------------------------------------- #
#  Stage table: (canonical key, displayed name, icon, tooltip blurb)
#
#  Seven stages: Trace GT (the multi-class ground-truth editor) sits right
#  after Library; mask + skeleton generation is ONE step (one button, one run),
#  and the two corrections are separate stages because they are separate
#  editors. Displayed names are full words — no step numbers, nothing
#  collapsed to a bare glyph. The tooltip carries the sentence that used to
#  live in the deleted hint band.
# --------------------------------------------------------------------------- #
_STAGES = [
    ("Library", "Library", "image", "Browse and select minirhizotron images from the tree."),
    ("Trace GT", "Trace GT", "layers", "Paint ground-truth classes for a segmentation dataset."),
    (
        "Generate",
        "Generate",
        "cpu",
        "Generate masks and skeletons for all loaded images in one pass.",
    ),
    (
        "Correct Mask",
        "Correct Mask",
        "brush",
        "Paint, erase and fill to fix the segmentation mask.",
    ),
    (
        "Correct Skeleton",
        "Correct Skeleton",
        "skeleton",
        "Move, connect and prune skeleton nodes and branches.",
    ),
    ("Measure", "Measure", "ruler", "Compute root length (mm) and area (mm²)."),
    ("Visualize", "Visualize", "chart", "Explore length & area trends across the trial."),
]

# Every stage button shows its NAME plus its icon — no step numbers, nothing
# collapsed to a glyph. The row stays inside the 1200px default window because
# the padding is tight (9px) and the stage-action labels on the right are short
# ("Length" / "Area", not "Calculate Root Length").

_STEP_QSS = f"""
    QToolButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        color: {tokens.TEXT_MUTED};
        padding: 4px 9px;
        font-size: 12px;
        font-weight: 500;
    }}
    QToolButton:hover {{
        background-color: {tokens.BG_2};
        color: {tokens.TEXT};
    }}
    QToolButton:checked {{
        background-color: {tokens.rgba(tokens.ACCENT, 0.14)};
        color: {tokens.ACCENT};
        border: 1px solid {tokens.rgba(tokens.ACCENT, 0.28)};
    }}
"""

_ICONBTN_QSS = f"""
    QToolButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 7px;
        padding: 5px;
    }}
    QToolButton:hover {{
        background-color: {tokens.BG_2};
        border: 1px solid {tokens.BORDER};
    }}
"""


def build_topbar(mw) -> QWidget:
    """The single chrome row: brand, stage stepper, stage actions, load."""
    bar = _band("shellTopbar", height=TOPBAR_HEIGHT)
    row = QHBoxLayout(bar)
    row.setContentsMargins(14, 0, 12, 0)
    row.setSpacing(8)

    logo = QLabel()
    logo.setPixmap(load_pixmap("sprouts_logo", tokens.ACCENT, 20))
    row.addWidget(logo)

    wordmark = QLabel(
        f'<span style="color:{tokens.TEXT}">SPR</span>'
        f'<span style="color:{tokens.ACCENT}">OU</span>'
        f'<span style="color:{tokens.TEXT}">TS</span>'
    )
    wordmark.setTextFormat(Qt.TextFormat.RichText)
    wordmark.setStyleSheet("font-size: 14px; font-weight: 800; letter-spacing: 1px;")
    row.addWidget(wordmark)

    row.addSpacing(4)
    row.addWidget(_vrule())
    row.addSpacing(4)

    # ---- stage stepper ---------------------------------------------------- #
    group = QButtonGroup(bar)
    group.setExclusive(True)
    mw.ribbon = bar  # legacy attr name kept for any external reference
    mw.ribbon_buttons = {}

    base_handlers = {
        "Library": lambda: mw.switch_right_panel("display"),
        # Enter-only: clicking the active stage again must not toggle it off.
        "Trace GT": lambda: mw.toggle_gt_tracing() if mw.right_panel.currentIndex() != 4 else None,
        "Generate": lambda: mw.switch_right_panel("display"),
        "Correct Mask": lambda: (
            mw.toggle_mask_tracing() if mw.right_panel.currentIndex() != 1 else None
        ),
        "Correct Skeleton": lambda: (
            mw.toggle_skeleton_correction() if mw.right_panel.currentIndex() != 3 else None
        ),
        "Measure": lambda: mw.switch_right_panel("display"),
        "Visualize": lambda: mw.switch_right_panel("display"),
    }

    def _make_handler(stage_key, base):
        def _h():
            base()
            activate = getattr(mw, "_activate_action_stage", None)
            if callable(activate):
                # Still on Trace GT after another stage was asked for: the GT
                # editor kept unsaved edits (its save failed), so keep its stage.
                stuck = stage_key != "Trace GT" and mw.right_panel.currentIndex() == 4
                activate("Trace GT" if stuck else stage_key)

        return _h

    steps = QWidget()
    steps_row = QHBoxLayout(steps)
    steps_row.setContentsMargins(0, 0, 0, 0)
    steps_row.setSpacing(2)

    for key, label, icon_name, tip in _STAGES:
        btn = QToolButton(steps)
        btn.setText(label)
        btn.setIcon(load_icon(icon_name, tokens.TEXT_MUTED, 15))
        btn.setIconSize(QSize(15, 15))
        btn.setToolTip(f"{label} — {tip}")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setStyleSheet(_STEP_QSS)
        btn.clicked.connect(lambda _=False, h=_make_handler(key, base_handlers[key]): h())
        group.addButton(btn)
        steps_row.addWidget(btn)
        mw.ribbon_buttons[key] = btn

    mw.ribbon_buttons["Library"].setChecked(True)
    row.addWidget(steps)

    row.addStretch(1)

    # ---- stage-aware actions (right) -------------------------------------- #
    viz_seg = SegmentedControl([("length", "Length"), ("area", "Area")], value="length")

    def _on_viz(value: str):
        if value == "length":
            mw.toggle_root_length_visualization()
        elif value == "area":
            mw.toggle_root_area_visualization()

    viz_seg.valueChanged.connect(_on_viz)
    mw.action_bar_viz_seg = viz_seg

    stack = QStackedWidget()
    stack.setFixedHeight(32)
    mw.action_bar_stack = stack
    row.addWidget(stack)

    row.addWidget(_vrule())

    load_btn = QToolButton(bar)
    load_btn.setIcon(load_icon("load", tokens.TEXT_MUTED, 17))
    load_btn.setIconSize(QSize(17, 17))
    load_btn.setToolTip("Load images from another directory")
    load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    load_btn.setStyleSheet(_ICONBTN_QSS)
    load_btn.clicked.connect(mw.load_images)
    mw.topbar_load_button = load_btn
    row.addWidget(load_btn)

    def _page(*widgets) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for w in widgets:
            if w is not None:
                w.setParent(page)
                lay.addWidget(w)
        return page

    def _populate():
        # Cosmetic display-mode control; forwards into the hidden live
        # view_mode_combo (still the single source of truth) and reverse-syncs.
        view_seg = SegmentedControl(
            [("single", "Single"), ("overlay", "Overlay"), ("split", "Side by side")],
            value="single",
        )
        mw.action_bar_view_seg = view_seg
        _seg_index = {"single": 0, "overlay": 1, "split": 2}
        _index_seg = {0: "single", 1: "overlay", 2: "split"}

        def _on_view(value: str):
            idx = _seg_index.get(value)
            if idx is not None and mw.view_mode_combo.currentIndex() != idx:
                mw.view_mode_combo.setCurrentIndex(idx)

        view_seg.valueChanged.connect(_on_view)

        def _on_combo(idx: int):
            val = _index_seg.get(idx)
            if val is not None and view_seg.value() != val:
                view_seg.setValue(val, emit=False)

        mw.view_mode_combo.currentIndexChanged.connect(_on_combo)

        trace_clear = getattr(getattr(mw, "mask_tracing_interface", None), "clear_button", None)
        skel_reset = getattr(
            getattr(mw, "skeleton_correction_interface", None), "reset_button", None
        )

        # Trace GT actions: ghost buttons styled like the Correct Mask "Clear".
        # Save stays in the editor dock ("Save GT"), as in Correct Mask.
        mw.gt_classes_button = _ghost_button(
            "Classes…", "settings", "Add, rename, recolour or retire GT classes"
        )
        mw.gt_classes_button.clicked.connect(mw.gt_tracing_interface.edit_classes)
        mw.gt_export_button = _ghost_button(
            "Export dataset…", "export", "Export images + class-id masks as a dataset"
        )
        mw.gt_export_button.clicked.connect(mw.export_gt_dataset)

        pages = {
            "Library": _page(view_seg),
            "Trace GT": _page(mw.gt_classes_button, mw.gt_export_button),
            # ONE generate button — masks then skeletons, single run.
            "Generate": _page(mw.generate_all_button),
            "Correct Mask": _page(trace_clear),
            "Correct Skeleton": _page(skel_reset),
            "Measure": _page(mw.calculate_length_button, mw.calculate_area_button),
            "Visualize": _page(viz_seg),
        }
        mw._action_bar_pages = {}
        for key, page in pages.items():
            mw._action_bar_pages[key] = stack.addWidget(page)

    def _activate_action_stage(key: str):
        btn = mw.ribbon_buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        idx = getattr(mw, "_action_bar_pages", {}).get(key)
        if idx is not None:
            stack.setCurrentIndex(idx)

    mw._populate_action_bar = _populate
    mw._activate_action_stage = _activate_action_stage

    return bar


def _ghost_button(text: str, icon_name: str, tooltip: str) -> QPushButton:
    """A topbar action button in the editors' ghost style (see ``trace_clear``)."""
    btn = QPushButton(text)
    btn.setObjectName("ghostBtn")
    btn.setStyleSheet(_GHOST_QSS)
    btn.setIcon(load_icon(icon_name, tokens.TEXT_MUTED, 15))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


# --------------------------------------------------------------------------- #
def build_statusline(mw) -> QWidget:
    band = QFrame()
    band.setObjectName("shellStatusline")
    band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    band.setFixedHeight(STATUSLINE_HEIGHT)
    band.setStyleSheet(f"""
        QFrame#shellStatusline {{
            background-color: {tokens.BG_1};
            border: none;
            border-top: 1px solid {tokens.BORDER};
        }}
        QLabel {{ background: transparent; }}
    """)
    row = QHBoxLayout(band)
    row.setContentsMargins(14, 0, 14, 0)
    row.setSpacing(8)

    dot = QLabel()
    dot.setPixmap(load_pixmap("info", tokens.OK, 8))
    dot.setFixedWidth(10)
    row.addWidget(dot)

    msg = QLabel("Ready")
    msg.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 11.5px;")
    mw.statusline_message = msg
    row.addWidget(msg)

    row.addStretch(1)

    info = QLabel("GPU · CUDA 12.8")
    info.setStyleSheet(f"color: {tokens.TEXT_FAINT}; font-family: {tokens.MONO}; font-size: 11px;")
    mw.statusline_info = info
    row.addWidget(info)

    return band


# --------------------------------------------------------------------------- #
# Back-compat shims. ``build_titlebar`` / ``build_ribbon`` / ``build_action_bar``
# collapsed into build_topbar; these keep any stale caller alive. Prefer
# build_topbar — _build_shell now calls it directly.
# --------------------------------------------------------------------------- #
def build_ribbon(mw) -> QWidget:  # pragma: no cover - compat
    return build_topbar(mw)
