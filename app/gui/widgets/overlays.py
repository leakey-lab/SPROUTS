"""In-window floating overlays for the SPROUTS editors and feedback layer.

These are parented to a host widget (the interface/canvas), not placed in a
layout. Hand them to an :class:`~app.gui.widgets.overlay_layout.OverlayLayout`,
which places them together without collisions and follows host resizes; a
panel without one places itself when its host calls ``reposition()`` (and
must be ``raise_()``-d above the QGraphicsView). QSS has no backdrop-filter,
so the translucent design panels are approximated with a near-opaque
``--bg-1`` fill plus a QGraphicsDropShadowEffect.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import tokens
from .controls import DockSlider, IconButton
from .effects import pop_shadow
from .focus import keep_canvas_focus
from .icons import load_pixmap
from .text_fit import wrap_label
from .toasts import Toast, ToastManager  # noqa: F401  (moved; re-exported for old imports)


# --------------------------------------------------------------------------- #
#  Floating panels: rail, dock, corner panels (zoom, status chip), popover
# --------------------------------------------------------------------------- #
class _FloatingPanel(QFrame):
    """Rounded near-opaque panel with a pop shadow, for in-window overlays.

    Styled through the ``sproutsPanel`` dynamic property rather than the object name, so a
    subclass may set any objectName (the GT class panel is ``classPanel``) and keep its fill.
    """

    def __init__(self, parent=None, *, horizontal=False):
        super().__init__(parent)
        self.setObjectName("floatPanel")
        self.setProperty("sproutsPanel", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QFrame[sproutsPanel="true"] {{
                background-color: {tokens.BG_1};
                border: 1px solid {tokens.BORDER_STRONG};
                border-radius: 13px;
            }}
        """)
        box = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(3)
        self._box = box
        self._overlay_layout = None
        pop_shadow(self)

    def add_widget(self, w: QWidget):
        """Add ``w``; its controls stay Tab-reachable but a click leaves the canvas focus."""
        self._box.addWidget(w)
        keep_canvas_focus(w)

    def add_separator(self):
        sep = QFrame(self)
        if isinstance(self._box, QVBoxLayout):
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFixedHeight(1)
        else:
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setFixedWidth(1)
        sep.setStyleSheet(f"background-color: {tokens.BORDER}; border: none;")
        self._box.addWidget(sep)

    def set_overlay_layout(self, layout) -> None:
        """Let ``layout`` (an OverlayLayout) place this panel; ``None``: it places itself."""
        self._overlay_layout = layout

    def reposition(self) -> None:
        """Place the panel: through its OverlayLayout, else on its own (:meth:`_place`)."""
        if self._overlay_layout is not None:
            self._overlay_layout.relayout()
        elif self.parent() is not None:
            self._box.activate()  # fresh size hints: nested layouts cache theirs until then
            self._place()

    def _place(self) -> None:
        """Standalone placement inside the parent; each panel type overrides it."""
        self.adjustSize()


class ToolRail(_FloatingPanel):
    """Vertical floating rail, vertically centred at the host's left edge."""

    def __init__(self, parent=None, *, margin: int = 14):
        super().__init__(parent, horizontal=False)
        self._margin = margin

    def _place(self) -> None:
        self.adjustSize()
        ph = self.parent().height()
        y = max(self._margin, (ph - self.height()) // 2)
        self.move(self._margin, y)


class FloatingDock(_FloatingPanel):
    """Horizontal floating dock, bottom-centred over the host.

    On a narrow canvas the dock can turn compact (:meth:`set_compact`): its
    :class:`DockSlider` controls get shorter and hide their readouts.
    """

    def __init__(self, parent=None, *, margin: int = 18):
        super().__init__(parent, horizontal=True)
        self._margin = margin
        self._compact = False

    def add_widget(self, w: QWidget):
        super().add_widget(w)
        for slider in self._sliders(w):
            slider.set_compact(self._compact)

    def set_compact(self, compact: bool) -> None:
        """Compact (shorter sliders, no readouts) or full width."""
        self._compact = bool(compact)
        for slider in self._sliders(self):
            slider.set_compact(self._compact)

    def is_compact(self) -> bool:
        return self._compact

    def natural_width(self, compact: bool) -> int:
        """Width the dock needs in either mode, predicted without switching modes."""
        width = self.sizeHint().expandedTo(self.minimumSize()).boundedTo(self.maximumSize()).width()
        if bool(compact) == self._compact:
            return width
        savings = sum(s.compact_savings() for s in self._sliders(self) if s.isVisibleTo(self))
        return width - savings if compact else width + savings

    @staticmethod
    def _sliders(root: QWidget) -> list[DockSlider]:
        found = root.findChildren(DockSlider)
        return [root, *found] if isinstance(root, DockSlider) else found

    def _place(self) -> None:
        self.adjustSize()
        pw, ph = self.parent().width(), self.parent().height()
        x = max(self._margin, (pw - self.width()) // 2)
        y = ph - self.height() - self._margin
        self.move(x, y)


class CornerPanel(_FloatingPanel):
    """Small horizontal floating panel pinned to a corner of the host.

    ``corner`` is one of ``"tl"``, ``"tr"``, ``"bl"``, ``"br"``. Used for the
    canvas filename/status chip and the zoom cluster.
    """

    def __init__(self, parent=None, *, corner: str = "br", margin: int = 14, v_margin=None):
        super().__init__(parent, horizontal=True)
        self._corner = corner
        self._margin = margin
        self._v_margin = margin if v_margin is None else v_margin
        self._box.setContentsMargins(5, 4, 5, 4)

    def _place(self) -> None:
        self.adjustSize()
        pw, ph = self.parent().width(), self.parent().height()
        x = self._margin if self._corner[1] == "l" else pw - self.width() - self._margin
        y = self._v_margin if self._corner[0] == "t" else ph - self.height() - self._v_margin
        self.move(x, y)


_ZOOM_READOUT_QSS = f"""
QToolButton#zoomReadout {{
    background: transparent; border: 1px solid transparent; border-radius: 7px;
    color: {tokens.TEXT}; font-family: {tokens.MONO}; font-size: 12px; padding: 0;
}}
QToolButton#zoomReadout:hover {{ background-color: {tokens.BG_3}; }}
{tokens.focus_rule("QToolButton#zoomReadout")}
"""


class ZoomCluster(CornerPanel):
    """Zoom controls shared by the canvas editors: out, readout, in | fit.

    The readout is a flat button: a click shows the image at 100 % (actual
    pixels), or fits it again when it already is at 100 %. :meth:`bind` wires
    the cluster to a canvas view with ``zoom_changed(float)``, ``zoom_by``,
    ``fit_to_view`` and ``set_zoom``.
    """

    ZOOM_IN_STEP = 1.25
    ZOOM_OUT_STEP = 0.8

    def __init__(self, parent=None, *, corner: str = "br", margin: int = 14, v_margin: int = 18):
        super().__init__(parent, corner=corner, margin=margin, v_margin=v_margin)
        self.zoom_out_button = IconButton("zoomOut", "Zoom out (Ctrl+-)")
        self.zoom_pct_label = QToolButton()
        self.zoom_pct_label.setObjectName("zoomReadout")
        self.zoom_pct_label.setStyleSheet(_ZOOM_READOUT_QSS)
        self.zoom_pct_label.setFixedSize(50, 30)
        self.zoom_pct_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_in_button = IconButton("zoomIn", "Zoom in (Ctrl+=)")
        self.fit_button = IconButton("fit", "Fit to window (Ctrl+0)")
        self.add_widget(self.zoom_out_button)
        self.add_widget(self.zoom_pct_label)
        self.add_widget(self.zoom_in_button)
        self.add_separator()
        self.add_widget(self.fit_button)

        self._view = None
        self._fit: Callable[[], None] | None = None
        self._factor = 1.0
        self.zoom_in_button.clicked.connect(lambda: self._ask("zoom_by", self.ZOOM_IN_STEP))
        self.zoom_out_button.clicked.connect(lambda: self._ask("zoom_by", self.ZOOM_OUT_STEP))
        self.fit_button.clicked.connect(self._fit_view)
        self.zoom_pct_label.clicked.connect(self._toggle_actual_pixels)
        self.set_zoom_factor(1.0)

    def bind(self, view, *, fit: Callable[[], None] | None = None) -> None:
        """Drive ``view`` from the buttons and follow its ``zoom_changed`` signal.

        ``fit`` replaces ``view.fit_to_view()`` for the fit button and the readout toggle,
        e.g. ``lambda: view.fit_to_view(insets=overlay_layout.insets())``.
        """
        if self._view is not None:
            try:
                self._view.zoom_changed.disconnect(self.set_zoom_factor)
            except (TypeError, RuntimeError):  # already gone
                pass
        self._view = view
        self._fit = fit
        view.zoom_changed.connect(self.set_zoom_factor)
        self.set_zoom_factor(getattr(view, "zoom_factor", self._factor))

    def set_zoom_factor(self, factor: float) -> None:
        """Show ``factor`` (1.0 = 100 %) in the readout."""
        self._factor = float(factor)
        percent = round(self._factor * 100)
        tip = "Fit to window (Ctrl+0)" if self._at_actual_pixels() else "Actual pixels (Ctrl+1)"
        self.zoom_pct_label.setText(f"{percent}%")
        self.zoom_pct_label.setToolTip(tip)
        self.zoom_pct_label.setAccessibleName(f"Zoom {percent}%")
        self.zoom_pct_label.setAccessibleDescription(tip)

    def _at_actual_pixels(self) -> bool:
        return abs(self._factor - 1.0) < 0.005

    def _toggle_actual_pixels(self) -> None:
        if self._at_actual_pixels():
            self._fit_view()
        else:
            self._ask("set_zoom", 1.0)

    def _fit_view(self) -> None:
        if self._fit is not None:
            self._fit()
        else:
            self._ask("fit_to_view")

    def _ask(self, method: str, *args) -> None:
        if self._view is not None:
            getattr(self._view, method)(*args)


class StatusChip(CornerPanel):
    """Canvas chip: mono file name and a coloured save-state dot, plus an optional
    muted hint line below them.

    ``set_state`` takes ``"none"`` (faint; no text unless ``label`` is given),
    ``"clean"`` (Saved), ``"dirty"`` (Unsaved), ``"saving"`` or ``"error"``;
    ``label`` replaces the default text, e.g. ``set_state("none", "No mask")`` or
    ``set_state("clean", "Saved · 2/3 checked")``. Long names are elided in the
    middle (full name in the tooltip); an :class:`OverlayLayout` narrows the chip
    further with :meth:`fit_to_width`.
    """

    STATES = {
        "none": (tokens.TEXT_FAINT, ""),
        "clean": (tokens.OK, "Saved"),
        "dirty": (tokens.WARN, "Unsaved"),
        "saving": (tokens.INFO, "Saving…"),
        "error": (tokens.DANGER, "Error"),
    }
    MIN_NAME_WIDTH = 80  # about a dozen mono characters: "24EF…SMM.jpg"
    MIN_HINT_WIDTH = 160  # narrowest useful hint column

    def __init__(
        self,
        parent=None,
        *,
        corner: str = "tl",
        margin: int = 14,
        max_name_width: int = 360,
        max_hint_width: int = 460,
    ):
        super().__init__(parent, corner=corner, margin=margin)
        self._box.setContentsMargins(11, 5, 11, 5)
        self._max_name_width = max_name_width
        self._max_hint_width = max_hint_width
        self._max_width: int | None = None
        self._name = ""
        self._hint = ""
        self._state = "none"

        self.file_label = QLabel()
        self.file_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-family: {tokens.MONO}; font-size: 12px;"
            "background: transparent;"
        )
        self.state_label = QLabel()
        self.state_label.setTextFormat(Qt.TextFormat.RichText)
        self.hint_label = QLabel()
        self.hint_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hint_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: 12px; background: transparent;"
        )
        name_row = QHBoxLayout()
        name_row.setSpacing(9)
        name_row.addWidget(self.file_label)
        name_row.addWidget(self.state_label)
        name_row.addStretch(1)
        column = QVBoxLayout()
        column.setSpacing(3)
        column.addLayout(name_row)
        column.addWidget(self.hint_label)
        self._name_row = name_row
        self._box.addLayout(column)
        self.set_state("none")
        self.set_hint("")

    # ------------------------------------------------------------------ API
    def set_text(self, name: str) -> None:
        """Show ``name`` (a file name), elided in the middle when it is long."""
        self._name = name
        self._refresh()
        self.reposition()

    def text(self) -> str:
        return self._name

    def set_state(self, state: str, label: str | None = None, *, tooltip: str = "") -> None:
        """Show the save state; see the class docstring for ``state`` and ``label``."""
        if state not in self.STATES:
            raise ValueError(f"unknown status chip state {state!r}")
        color, default = self.STATES[state]
        text = default if label is None else label
        self._state = state
        self.state_label.setText(
            f'<span style="color:{color}; font-size:11.5px;">● {text}</span>' if text else ""
        )
        self.state_label.setToolTip(tooltip)
        self.state_label.setVisible(bool(text))
        self._refresh()
        self.reposition()

    def state(self) -> str:
        return self._state

    def set_hint(self, text: str, tooltip: str = "") -> None:
        """Show ``text`` on a muted line under the name (hidden while empty)."""
        self._hint = text
        self.hint_label.setText(text)
        self.hint_label.setToolTip(tooltip or text)
        self.hint_label.setVisible(bool(text))
        self._refresh()
        self.reposition()

    def fit_to_width(self, max_width: int | None) -> None:
        """Narrow the chip to ``max_width`` px (``None``: natural width); the layout hook.

        The name is elided and the hint wraps, down to MIN_NAME_WIDTH / MIN_HINT_WIDTH;
        a chip that cannot get that narrow stays wider than ``max_width``. Does not move
        the chip (its OverlayLayout does).
        """
        self._max_width = max_width
        self._refresh()

    # ------------------------------------------------------------- internals
    def _refresh(self) -> None:
        """Re-elide the name and re-wrap the hint for the current width budget.

        Every call below is a no-op when nothing changes, so a relayout that re-applies
        the same budget does not invalidate the chip's layout again.
        """
        self.ensurePolished()
        inner = None
        if self._max_width is not None:
            margins, frame = self._box.contentsMargins(), self.contentsMargins()
            chrome = margins.left() + margins.right() + frame.left() + frame.right()
            inner = self._max_width - chrome

        name_width = self._max_name_width
        if inner is not None:
            state = self.state_label
            used = state.sizeHint().width() + self._name_row.spacing() if state.text() else 0
            name_width = max(self.MIN_NAME_WIDTH, min(name_width, inner - used))
        self.file_label.ensurePolished()
        metrics = self.file_label.fontMetrics()
        self.file_label.setText(
            metrics.elidedText(self._name, Qt.TextElideMode.ElideMiddle, name_width)
        )
        self.file_label.setToolTip(self._name)

        hint_width = self._max_hint_width
        if inner is not None:
            hint_width = max(self.MIN_HINT_WIDTH, min(hint_width, inner))
        wrap_label(self.hint_label, hint_width)
        self._box.activate()  # the name row / column layouts cache hints until activated


class EnhancePopover(_FloatingPanel):
    """Top-right popover that hosts an arbitrary content widget.

    It is ``width`` px wide, or wider when the content needs more (UI-07: the
    Enhance buttons used to be clipped). ``toggled(bool)`` reports open/close.
    The content keeps normal keyboard focus (its sliders and combos need it).
    """

    toggled = pyqtSignal(bool)

    def __init__(self, parent=None, *, margin: int = 14, width: int = 264):
        super().__init__(parent, horizontal=False)
        self._margin = margin
        self._min_width = width
        self.setFixedWidth(width)
        self.hide()

    def set_content(self, w: QWidget):
        self._box.addWidget(w)
        self._fit_width()

    def _fit_width(self) -> None:
        """Never narrower than the content's minimum size (plus margins and frame)."""
        self.setFixedWidth(max(self._min_width, self._box.totalMinimumSize().width()))

    def event(self, event):
        if event.type() == QEvent.Type.LayoutRequest:  # the content changed size
            self._fit_width()
        return super().event(event)

    def _place(self) -> None:
        self._fit_width()
        self.adjustSize()
        pw = self.parent().width()
        self.move(pw - self.width() - self._margin, self._margin)

    def toggle(self):
        # isHidden() tracks explicit show/hide state even when the host widget
        # is not itself on-screen (isVisible() would always be False then).
        opening = self.isHidden()
        self.setVisible(opening)
        self.reposition()  # with an OverlayLayout: the others move for / back after it
        if opening:
            self.raise_()
        self.toggled.emit(opening)

    def keyPressEvent(self, event):
        """Esc closes the open popover (keys its sliders / combos leave unhandled land here)."""
        if event.key() == Qt.Key.Key_Escape and not self.isHidden():
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)


# --------------------------------------------------------------------------- #
#  Progress overlay (generate / loading feedback)
# --------------------------------------------------------------------------- #
class ProgressOverlay(QWidget):
    """Floating top-centre progress card: live dot + label + percent + bar."""

    def __init__(self, parent=None, *, margin: int = 18, width: int = 340):
        super().__init__(parent)
        self._margin = margin
        self.setFixedWidth(width)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        frame = QFrame(self)
        frame.setObjectName("progFrame")
        frame.setStyleSheet(f"""
            QFrame#progFrame {{
                background-color: {tokens.BG_1};
                border: 1px solid {tokens.BORDER_STRONG};
                border-radius: 12px;
            }}
            QLabel#progLabel {{ color: {tokens.TEXT}; background: transparent;
                font-size: 12.5px; font-weight: 600; }}
            QLabel#progPct {{ color: {tokens.ACCENT}; background: transparent;
                font-family: {tokens.MONO}; font-size: 12px; }}
            QProgressBar {{ background-color: {tokens.BG_3}; border: none;
                border-radius: 2px; height: 4px; }}
            QProgressBar::chunk {{ background-color: {tokens.ACCENT}; border-radius: 2px; }}
        """)
        pop_shadow(frame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        col = QVBoxLayout(frame)
        col.setContentsMargins(16, 12, 16, 14)
        col.setSpacing(9)
        top = QHBoxLayout()
        top.setSpacing(9)
        self._dot = QLabel()
        self._dot.setPixmap(load_pixmap("info", tokens.ACCENT, 8))
        self._dot.setFixedWidth(10)
        self._label = QLabel("Working…")
        self._label.setObjectName("progLabel")
        self._pct = QLabel("0%")
        self._pct.setObjectName("progPct")
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(self._dot)
        top.addWidget(self._label, 1)
        top.addWidget(self._pct)
        col.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        col.addWidget(self._bar)
        self.hide()

    def set_label(self, text: str):
        self._label.setText(text)

    def set_progress(self, pct: int):
        pct = max(0, min(100, int(pct)))
        self._bar.setValue(pct)
        self._pct.setText(f"{pct}%")

    def start(self, label: str = "Working…"):
        self.set_label(label)
        self.set_progress(0)
        self.reposition()
        self.raise_()
        self.show()

    def finish(self):
        self.set_progress(100)
        self.hide()

    def reposition(self):
        if self.parent() is None:
            return
        self.adjustSize()
        pw = self.parent().width()
        x = max(self._margin, (pw - self.width()) // 2)
        self.move(x, self._margin)
