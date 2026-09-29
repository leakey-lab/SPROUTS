"""SPROUTS guided-shell chrome: the topbar and the statusline (presentation only).

ONE chrome row on top (:class:`ShellTopbar`, 46 px) and ONE status band at the
bottom (:class:`StatusLine`, 28 px)::

    [logo SPROUTS] | Library  Trace GT  Generate  Correct Mask  … | <stage actions> | [load]
    ● <status message>                         <task progress · ETA · Cancel>   <device>

Topbar
    Seven stages: Library, then Trace GT (hand-traced multi-class ground truth,
    independent of the ML pipeline), then the measurement pipeline. Each stage
    button forwards to the same MainWindow handler as before and shows its
    per-stage actions on the right (a stack sized to the CURRENT page only).

    Density is responsive. At or above the measured full width every stage shows
    its icon and name. Below it (with a 24 px hysteresis) the unchecked stages
    become icon-only and the wordmark hides; the checked stage keeps its name,
    and every button keeps ``text()``, its accessible name and its "name —
    description" tooltip. That keeps the window's minimum width well under
    900 px instead of the ~1.2-1.9k px the full row needs.

Statusline
    The message is bound to ``mw.status_bar.messageChanged`` (every existing
    ``status_bar.showMessage`` caller keeps working); the TaskProgressWidget runs
    inline in the same row; the chip names the inference device only once the
    runtime module is loaded (it never imports torch to find out).

Contract kept: ``mw.ribbon`` (the topbar), ``mw.ribbon_buttons`` (seven keys, in
order), ``mw.action_bar_stack``, ``mw.action_bar_viz_seg``,
``mw._populate_action_bar``, ``mw._activate_action_stage``,
``mw.statusline_message`` and ``mw.statusline_info``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QWidget,
)

from app.gui.panels import Panel
from app.gui.task_progress import TaskProgressWidget
from app.gui.widgets import SegmentedControl, tokens
from app.gui.widgets.buttons import ghost_button
from app.gui.widgets.icons import load_icon, load_pixmap

TOPBAR_HEIGHT = 46
STATUSLINE_HEIGHT = 28
DENSITY_HYSTERESIS = 24  # px above the full width before leaving compact mode
INFO_MAX_WIDTH = 320  # device chip, elided beyond this

FULL = "full"
COMPACT = "compact"

_RUNTIME_MODULE = "app.inference.runtime"
_RUNTIME_UNKNOWN = "Device: auto"


def _band(object_name: str, *, border: str = "bottom") -> str:
    """QSS for a chrome band ``QFrame#object_name`` (BG_1 with a hairline edge)."""
    return f"""
        QFrame#{object_name} {{
            background-color: {tokens.BG_1};
            border: none;
            border-{border}: 1px solid {tokens.BORDER};
        }}
        QLabel {{ background: transparent; }}
    """


def _vrule() -> QFrame:
    rule = QFrame()
    rule.setFixedSize(1, 18)
    rule.setStyleSheet(f"background-color: {tokens.BORDER};")
    return rule


# --------------------------------------------------------------------------- #
#  Stage table: (canonical key, displayed name, icon, tooltip blurb)
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
    {tokens.focus_rule("QToolButton")}
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
    {tokens.focus_rule("QToolButton")}
"""


def _stage_icon(icon_name: str):
    """Muted stage icon with an ACCENT ``On`` state (UXS-12), when icons support it."""
    try:
        return load_icon(icon_name, tokens.TEXT_MUTED, 15, checked_color=tokens.ACCENT)
    except TypeError:  # an icons module without ``checked_color``
        return load_icon(icon_name, tokens.TEXT_MUTED, 15)


class _CurrentPageStack(QStackedWidget):
    """A QStackedWidget as wide as its CURRENT page.

    QStackedLayout sizes to its widest page; giving every other page an
    ``Ignored`` size policy takes them out of the size hints, so one short action
    is not stretched to the width of the longest stage's actions.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self._fit_current)

    def addWidget(self, widget: QWidget) -> int:  # noqa: N802 - Qt API
        index = super().addWidget(widget)
        self._fit_current()
        return index

    def _fit_current(self, *_args) -> None:
        current = self.currentWidget()
        for i in range(self.count()):
            page = self.widget(i)
            policy = QSizePolicy.Policy.Preferred if page is current else QSizePolicy.Policy.Ignored
            page.setSizePolicy(policy, policy)
        self.updateGeometry()


class _SelectorSegments(SegmentedControl):
    """SegmentedControl that also reports re-clicks of the selected segment.

    ``valueChanged`` (base) only fires on a change; ``activated`` fires on every
    click or keyboard selection, which is what a "show this view" selector needs.
    """

    activated = pyqtSignal(str)

    def _on_click(self, value: str) -> None:
        super()._on_click(value)
        self.activated.emit(value)


# --------------------------------------------------------------------------- #
#  Topbar
# --------------------------------------------------------------------------- #
class ShellTopbar(QFrame):
    """The single chrome row: brand, stage stepper, stage actions, load.

    Signals:
        stage_requested(str): a stage button was clicked (canonical stage key).
    """

    stage_requested = pyqtSignal(str)

    def __init__(self, mw, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mw = mw
        self._density = FULL
        self._stage_widths: dict[str, tuple[int, int]] = {}  # key -> (text, icon-only)
        self._pages: dict[str, int] = {}
        self._viz_kind: str | None = None  # the dashboard the user chose last

        self.setObjectName("shellTopbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_band("shellTopbar"))
        self.setFixedHeight(TOPBAR_HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(8)
        self._row = row

        logo = QLabel()
        logo.setPixmap(load_pixmap("sprouts_logo", tokens.ACCENT, 20))
        row.addWidget(logo)

        self.wordmark = QLabel(
            f'<span style="color:{tokens.TEXT}">SPR</span>'
            f'<span style="color:{tokens.ACCENT}">OU</span>'
            f'<span style="color:{tokens.TEXT}">TS</span>'
        )
        self.wordmark.setTextFormat(Qt.TextFormat.RichText)
        self.wordmark.setStyleSheet("font-size: 14px; font-weight: 800; letter-spacing: 1px;")
        row.addWidget(self.wordmark)

        row.addSpacing(4)
        row.addWidget(_vrule())
        row.addSpacing(4)

        row.addWidget(self._build_stages())
        row.addStretch(1)

        self.viz_seg = _SelectorSegments([("length", "Length"), ("area", "Area")], value="length")
        self.viz_seg.activated.connect(self._show_visualization)

        self.action_stack = _CurrentPageStack()
        self.action_stack.setFixedHeight(32)
        row.addWidget(self.action_stack)

        row.addWidget(_vrule())

        self.load_button = QToolButton(self)
        self.load_button.setIcon(load_icon("load", tokens.TEXT_MUTED, 17))
        self.load_button.setIconSize(QSize(17, 17))
        self.load_button.setToolTip("Load images from another directory")
        self.load_button.setAccessibleName("Load images")
        self.load_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_button.setStyleSheet(_ICONBTN_QSS)
        self.load_button.clicked.connect(mw.load_images)
        row.addWidget(self.load_button)

    # ------------------------------------------------------------ stages
    def _build_stages(self) -> QWidget:
        steps = QWidget()
        steps_row = QHBoxLayout(steps)
        steps_row.setContentsMargins(0, 0, 0, 0)
        steps_row.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.stage_buttons: dict[str, QToolButton] = {}
        for key, label, icon_name, tip in _STAGES:
            btn = QToolButton(steps)
            btn.setText(label)
            btn.setAccessibleName(label)
            btn.setIcon(_stage_icon(icon_name))
            btn.setIconSize(QSize(15, 15))
            btn.setToolTip(f"{label} — {tip}")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setStyleSheet(_STEP_QSS)
            btn.clicked.connect(lambda _=False, k=key: self._on_stage_clicked(k))
            self._group.addButton(btn)
            steps_row.addWidget(btn)
            self.stage_buttons[key] = btn
        self.stage_buttons["Library"].setChecked(True)
        self._group.buttonToggled.connect(self._on_stage_toggled)
        return steps

    def _enter_stage(self, key: str) -> None:
        """Run the MainWindow handler that opens ``key``'s page."""
        mw = self._mw
        index = mw.right_panel.currentIndex()
        if key == "Trace GT":
            if index != Panel.GT_TRACING:  # enter-only: never toggles the editor off
                mw.toggle_gt_tracing()
        elif key == "Correct Mask":
            if index != Panel.MASK_TRACING:
                mw.toggle_mask_tracing()
        elif key == "Correct Skeleton":
            if index != Panel.SKELETON_CORRECTION:
                mw.toggle_skeleton_correction()
        elif key == "Visualize" and self._viz_kind is not None:
            self._show_visualization(self._viz_kind)
        else:
            mw.switch_right_panel("display")

    def _on_stage_clicked(self, key: str) -> None:
        self._enter_stage(key)
        self.stage_requested.emit(key)
        # Still on Trace GT after another stage was asked for: the GT editor kept
        # unsaved edits (its save failed), so keep its stage highlighted.
        stuck = key != "Trace GT" and self._mw.right_panel.currentIndex() == Panel.GT_TRACING
        self.activate_stage("Trace GT" if stuck else key)

    # ----------------------------------------------------------- actions
    def populate(self) -> None:
        """Build the per-stage action pages (the MainWindow buttons must exist)."""
        mw = self._mw
        mw.action_bar_view_seg = self._view_mode_control()

        mw.gt_classes_button = ghost_button(
            "Classes…",
            "settings",
            tooltip="Add, rename, recolour or retire GT classes",
            compact=True,
        )
        mw.gt_classes_button.clicked.connect(mw.gt_tracing_interface.edit_classes)
        mw.gt_export_button = ghost_button(
            "Export dataset…",
            "export",
            tooltip="Export images + class-id masks as a dataset",
            compact=True,
        )
        mw.gt_export_button.clicked.connect(mw.export_gt_dataset)

        # Skeleton-only regeneration never touches masks (RB-01): the wired
        # legacy button, next to the one-pass masks-then-skeletons run.
        mw.generate_button.setText("Skeletons only")
        mw.generate_button.setToolTip(
            "Skeletons only — extract skeletons from the masks in mask/ (hand-corrected "
            "masks included) without regenerating any mask."
        )
        mw.skeleton_clear_button = ghost_button(
            "Clear skeleton",
            "trash",
            tooltip="Remove every skeleton line from this image (Ctrl+Z undoes)",
            compact=True,
        )
        mw.skeleton_clear_button.clicked.connect(
            lambda: mw.skeleton_correction_interface.clear_skeleton()
        )
        trace_clear = getattr(getattr(mw, "mask_tracing_interface", None), "clear_button", None)

        pages = {
            "Library": (mw.action_bar_view_seg,),
            "Trace GT": (mw.gt_classes_button, mw.gt_export_button),
            "Generate": (mw.generate_all_button, mw.generate_button),
            "Correct Mask": (trace_clear,),
            "Correct Skeleton": (mw.skeleton_clear_button,),
            "Measure": (mw.calculate_length_button, mw.calculate_area_button),
            "Visualize": (self.viz_seg,),
        }
        self._pages = {key: self.action_stack.addWidget(_page(*ws)) for key, ws in pages.items()}
        mw._action_bar_pages = dict(self._pages)

    def _view_mode_control(self) -> SegmentedControl:
        """Display-mode control that forwards to the hidden ``view_mode_combo``
        (still the single source of truth) and follows it back."""
        mw = self._mw
        view_seg = SegmentedControl(
            [("single", "Single"), ("overlay", "Overlay"), ("split", "Side by side")],
            value="single",
        )
        modes = ["single", "overlay", "split"]

        def _on_view(value: str) -> None:
            idx = modes.index(value) if value in modes else -1
            if idx >= 0 and mw.view_mode_combo.currentIndex() != idx:
                mw.view_mode_combo.setCurrentIndex(idx)

        def _on_combo(idx: int) -> None:
            if 0 <= idx < len(modes) and view_seg.value() != modes[idx]:
                view_seg.setValue(modes[idx], emit=False)

        view_seg.valueChanged.connect(_on_view)
        mw.view_mode_combo.currentIndexChanged.connect(_on_combo)
        return view_seg

    def activate_stage(self, key: str) -> None:
        """Highlight ``key`` and show its action page (no handler runs)."""
        btn = self.stage_buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        idx = self._pages.get(key)
        if idx is not None:
            self.action_stack.setCurrentIndex(idx)

    # ------------------------------------------------------- visualization
    def _show_visualization(self, kind: str) -> None:
        """Show (build or bring forward) the ``kind`` dashboard; never toggles."""
        self._viz_kind = kind
        if self.viz_seg.value() != kind:
            self.viz_seg.setValue(kind, emit=False)
        getattr(self._mw, f"show_root_{kind}_visualization")()

    def reset_visualize_selection(self) -> None:
        """Forget the chosen dashboard (e.g. after another folder is opened)."""
        self._viz_kind = None
        self.viz_seg.setValue("length", emit=False)

    # ------------------------------------------------------------ density
    def density(self) -> str:
        """``"full"`` (every stage named) or ``"compact"`` (unchecked stages icon-only)."""
        return self._density

    def _widths(self, key: str) -> tuple[int, int]:
        """(text-beside-icon, icon-only) size-hint widths of a stage button (cached)."""
        cached = self._stage_widths.get(key)
        if cached is None:
            btn = self.stage_buttons[key]
            style = btn.toolButtonStyle()
            widths = []
            for probe in (
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
                Qt.ToolButtonStyle.ToolButtonIconOnly,
            ):
                btn.setToolButtonStyle(probe)
                widths.append(btn.sizeHint().width())
            btn.setToolButtonStyle(style)
            cached = self._stage_widths[key] = (widths[0], widths[1])
        return cached

    def _wordmark_width(self) -> int:
        return self.wordmark.sizeHint().width() + self._row.spacing()

    def full_width(self) -> int:
        """Minimum width at which every stage fits with its name (current stage page)."""
        width = self._row.minimumSize().width()
        for key, btn in self.stage_buttons.items():
            if btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
                text_w, icon_w = self._widths(key)
                width += text_w - icon_w
        if self.wordmark.isHidden():
            width += self._wordmark_width()
        return width

    def compact_width(self) -> int:
        """Minimum width in compact density (the topbar's minimum)."""
        width = self._row.minimumSize().width()
        for key, btn in self.stage_buttons.items():
            text_mode = btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if text_mode and not btn.isChecked():
                text_w, icon_w = self._widths(key)
                width -= text_w - icon_w
        if not self.wordmark.isHidden():
            width -= self._wordmark_width()
        return width

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self.compact_width(), TOPBAR_HEIGHT)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._update_density()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.type() in (event.Type.StyleChange, event.Type.FontChange):
            self._stage_widths.clear()
        super().changeEvent(event)

    def _update_density(self) -> None:
        width = self.width()
        if self._density == FULL and width < self.full_width():
            self._set_density(COMPACT)
        elif self._density == COMPACT and width >= self.full_width() + DENSITY_HYSTERESIS:
            self._set_density(FULL)

    def _set_density(self, density: str) -> None:
        self._density = density
        self._apply_density()

    def _apply_density(self) -> None:
        compact = self._density == COMPACT
        for btn in self.stage_buttons.values():
            style = (
                Qt.ToolButtonStyle.ToolButtonIconOnly
                if compact and not btn.isChecked()
                else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            )
            if btn.toolButtonStyle() != style:
                btn.setToolButtonStyle(style)
        self.wordmark.setVisible(not compact)
        self.updateGeometry()

    def _on_stage_toggled(self, _button, checked: bool) -> None:
        if checked:
            self._apply_density()  # the checked stage keeps its name in compact mode
            self._update_density()


def _page(*widgets: QWidget | None) -> QWidget:
    """An action page: ``widgets`` (reparented) in a row; ``None`` entries skipped."""
    page = QWidget()
    lay = QHBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for w in widgets:
        if w is not None:
            w.setParent(page)
            lay.addWidget(w)
    return page


def build_topbar(mw) -> ShellTopbar:
    """Create the topbar and publish its contract names on ``mw``."""
    bar = ShellTopbar(mw)
    mw.ribbon = bar  # legacy attr name kept for any external reference
    mw.ribbon_buttons = bar.stage_buttons
    mw.action_bar_stack = bar.action_stack
    mw.action_bar_viz_seg = bar.viz_seg
    mw.topbar_load_button = bar.load_button
    mw._populate_action_bar = bar.populate
    mw._activate_action_stage = bar.activate_stage
    return bar


# --------------------------------------------------------------------------- #
#  Statusline
# --------------------------------------------------------------------------- #
def runtime_status() -> tuple[str, bool]:
    """(label, models_ok) for the device chip, WITHOUT importing torch.

    ``"Device: auto"`` until ``app.inference.runtime`` has been imported by a
    handler. Then its ``describe_runtime()`` summary (``.text`` / ``.models_ok``,
    never resolving the device itself) when the module offers one, else
    ``"GPU · <name>"`` / ``"CPU"`` once ``get_runtime()`` has been resolved.
    """
    module = sys.modules.get(_RUNTIME_MODULE)
    if module is None:
        return _RUNTIME_UNKNOWN, True
    describe = getattr(module, "describe_runtime", None)
    if callable(describe):
        try:
            summary = describe()
        except Exception:  # noqa: BLE001 - a status chip must never break the UI
            return _RUNTIME_UNKNOWN, True
        return str(getattr(summary, "text", summary)), bool(getattr(summary, "models_ok", True))
    get_runtime = getattr(module, "get_runtime", None)
    cache_info = getattr(get_runtime, "cache_info", None)
    if not callable(get_runtime) or cache_info is None or cache_info().currsize == 0:
        return _RUNTIME_UNKNOWN, True  # never force device detection just to label a chip
    config = get_runtime()
    return (f"GPU · {config.device_name}" if config.is_cuda else "CPU"), True


def runtime_label() -> str:
    """The device chip's text (see :func:`runtime_status`)."""
    return runtime_status()[0]


_STATE_COLORS = {
    "idle": tokens.OK,
    "working": tokens.ACCENT,
    "warn": tokens.WARN,
    "error": tokens.DANGER,
}


class StatusLine(QFrame):
    """The single bottom status band: state dot, message, inline task progress, device chip.

    ``adopt(status_bar, task_progress)`` makes it THE status surface: messages
    posted with ``status_bar.showMessage`` appear here, the progress widget moves
    into this row and the QStatusBar itself is hidden (its API stays usable).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        idle_text: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._idle_text = idle_text
        self._state = "idle"
        self._status_bar: QStatusBar | None = None
        self._progress: TaskProgressWidget | None = None

        self.setObjectName("shellStatusline")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(STATUSLINE_HEIGHT)
        self.setStyleSheet(_band("shellStatusline", border="top"))

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(8)
        self._row = row

        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        row.addWidget(self.dot)

        self.message = QLabel()
        self.message.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 11.5px;")
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.message, 1)

        self.info = QLabel(_RUNTIME_UNKNOWN)
        self.info.setProperty("models_ok", True)
        row.addWidget(self.info)

        self._show_idle()
        self.set_state("idle")
        self.refresh_runtime()

    # ------------------------------------------------------------ binding
    def adopt(self, status_bar: QStatusBar, progress: TaskProgressWidget) -> None:
        """Mirror ``status_bar`` messages, host ``progress`` inline, hide the bar."""
        if self._status_bar is not status_bar:
            if self._status_bar is not None:
                self._status_bar.messageChanged.disconnect(self._on_message)
            self._status_bar = status_bar
            status_bar.messageChanged.connect(self._on_message)
        if self._progress is not progress:
            self._progress = progress
            hidden = progress.isHidden()
            self._row.insertWidget(self._row.indexOf(self.info), progress)
            progress.setHidden(hidden)
            progress.state_changed.connect(self.set_state)
        status_bar.hide()
        self._on_message(status_bar.currentMessage())

    # ------------------------------------------------------------- state
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        """Colour the dot: idle / working / warn / error."""
        self._state = state if state in _STATE_COLORS else "idle"
        self.dot.setStyleSheet(
            f"background-color: {_STATE_COLORS[self._state]}; border-radius: 4px;"
        )

    def refresh_runtime(self) -> None:
        """Re-read the device chip (cheap; never imports torch).

        The chip is elided to ``INFO_MAX_WIDTH`` (full text in the tooltip) and
        turns WARN when a model's weights are missing.
        """
        text, models_ok = runtime_status()
        metrics = QFontMetrics(self.info.font())
        self.info.setText(metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, INFO_MAX_WIDTH))
        self.info.setToolTip(f"Inference runtime: {text}")
        self.info.setProperty("models_ok", models_ok)
        color = tokens.TEXT_FAINT if models_ok else tokens.WARN
        self.info.setStyleSheet(f"color: {color}; font-family: {tokens.MONO}; font-size: 11px;")

    # --------------------------------------------------------- internals
    def _on_message(self, text: str) -> None:
        if text:
            self.message.setText(text)
            self.message.setToolTip(text)
        else:
            self._show_idle()

    def _show_idle(self) -> None:
        text = "Ready"
        if self._idle_text is not None:
            try:
                text = self._idle_text() or text
            except Exception:  # noqa: BLE001 - never let a label provider break the UI
                pass
        self.message.setText(text)
        self.message.setToolTip("")


def _idle_text_for(mw) -> Callable[[], str]:
    def _text() -> str:
        images = getattr(getattr(mw, "image_manager", None), "images", None) or {}
        return f"Ready · {len(images):,} images" if images else "Ready"

    return _text


def build_statusline(mw) -> StatusLine:
    """Create the statusline and publish ``mw.statusline`` / ``statusline_*``."""
    line = StatusLine(idle_text=_idle_text_for(mw))
    mw.statusline = line
    mw.statusline_message = line.message
    mw.statusline_info = line.info
    return line


def install_status_surface(mw) -> None:
    """Make the statusline the window's one status surface (idempotent).

    Call once ``mw.status_bar`` and ``mw.task_progress`` exist.
    """
    mw.statusline.adopt(mw.status_bar, mw.task_progress)


# --------------------------------------------------------------------------- #
# Back-compat shim: build_titlebar / build_ribbon / build_action_bar collapsed
# into build_topbar; _build_shell calls build_topbar directly.
# --------------------------------------------------------------------------- #
def build_ribbon(mw) -> ShellTopbar:  # pragma: no cover - compat
    return build_topbar(mw)
