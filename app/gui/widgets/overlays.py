"""In-window floating overlays for the SPROUTS editors and feedback layer.

These are parented to a host widget (the interface/canvas), not placed in a
layout. Hand them to an :class:`~app.gui.widgets.overlay_layout.OverlayLayout`,
which places them together without collisions and follows host resizes; a
panel without one places itself when its host calls ``reposition()`` (and
must be ``raise_()``-d above the QGraphicsView). QSS has no backdrop-filter,
so the translucent design panels are approximated with a near-opaque
``--bg-1`` fill plus a drop shadow (:class:`~app.gui.widgets.effects.CachedDropShadow`, the
look of a QGraphicsDropShadowEffect blitted from a cache).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..key_text import platform_keys
from . import tokens
from .controls import DockSlider, IconButton
from .effects import pop_shadow
from .focus import keep_canvas_focus
from .icons import load_pixmap
from .text_fit import fit_forms, label_advance, wrap_label


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

    The − / + buttons step along the view's zoom ladder (``zoom_step``). The readout
    is a flat button: a click shows the image at 100 % (actual pixels), or fits it
    again when it already is at 100 %; a right-click opens :meth:`preset_menu` (Fit and
    fixed zooms). :meth:`bind` wires the cluster to a canvas view with
    ``zoom_changed(float)``, ``zoom_step``, ``fit_to_view`` and ``set_zoom``.
    """

    PRESETS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)  # readout menu, after "Fit"

    def __init__(self, parent=None, *, corner: str = "br", margin: int = 14, v_margin: int = 18):
        super().__init__(parent, corner=corner, margin=margin, v_margin=v_margin)
        self.zoom_out_button = IconButton("zoomOut", platform_keys("Zoom out (Ctrl+-)"))
        self.zoom_pct_label = QToolButton()
        self.zoom_pct_label.setObjectName("zoomReadout")
        self.zoom_pct_label.setStyleSheet(_ZOOM_READOUT_QSS)
        self.zoom_pct_label.setFixedSize(50, 30)
        self.zoom_pct_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_in_button = IconButton("zoomIn", platform_keys("Zoom in (Ctrl+=)"))
        self.fit_button = IconButton("fit", platform_keys("Fit to window (Ctrl+0)"))
        self.add_widget(self.zoom_out_button)
        self.add_widget(self.zoom_pct_label)
        self.add_widget(self.zoom_in_button)
        self.add_separator()
        self.add_widget(self.fit_button)

        self._view = None
        self._fit: Callable[[], None] | None = None
        self._factor = 1.0
        self.zoom_in_button.clicked.connect(lambda: self._ask("zoom_step", 1))
        self.zoom_out_button.clicked.connect(lambda: self._ask("zoom_step", -1))
        self.fit_button.clicked.connect(self._fit_view)
        self.zoom_pct_label.clicked.connect(self._toggle_actual_pixels)
        self.zoom_pct_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.zoom_pct_label.customContextMenuRequested.connect(self._show_presets)
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
        tip = platform_keys(
            "Fit to window (Ctrl+0)" if self._at_actual_pixels() else "Actual pixels (Ctrl+1)"
        )
        tip += " · right-click: zoom presets"
        self.zoom_pct_label.setText(f"{percent}%")
        self.zoom_pct_label.setToolTip(tip)
        self.zoom_pct_label.setAccessibleName(f"Zoom {percent}%")
        self.zoom_pct_label.setAccessibleDescription(tip)

    def preset_menu(self) -> QMenu:
        """The readout's right-click menu: "Fit", then the ``PRESETS`` zooms.

        Presets are checkable; the one equal to the current zoom is checked. Styled by
        the app's ``QMenu`` rules like every other menu.
        """
        menu = QMenu(self)
        menu.addAction("Fit").triggered.connect(self._fit_view)
        menu.addSeparator()
        for preset in self.PRESETS:
            action = menu.addAction(f"{round(preset * 100)}%")
            action.setCheckable(True)
            action.setChecked(self._at(preset))
            action.triggered.connect(lambda _checked=False, z=preset: self._ask("set_zoom", z))
        return menu

    def _show_presets(self, pos: QPoint) -> None:
        menu = self.preset_menu()
        menu.exec(self.zoom_pct_label.mapToGlobal(pos))
        menu.deleteLater()

    def _at(self, factor: float) -> bool:
        return abs(self._factor - factor) < 0.005 * factor

    def _at_actual_pixels(self) -> bool:
        return self._at(1.0)

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


# A camera name: [<field>_]T<tube>_L<position>_<YYYY.MM.DD>[_<HHMMSS>]<rest> (app.naming).
_CAPTURE = re.compile(
    r"^(?:(?P<field>.*?)_)?(?P<core>T\d+_L\d+_\d{4}\.\d{2}\.\d{2})(?P<time>_\d{6})?"
    r"(?P<rest>(?:[_.].*)?)$"
)


def name_forms(name: str) -> tuple[str, ...]:
    """``name`` and shorter forms of it that keep what identifies a capture, longest first.

    A camera name such as ``24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg`` drops, in
    turn, its end (suffix and extension), its time and its field:
    ``24EFCAM1_T001_L044_2024.07.18_111530…``, ``24EFCAM1_T001_L044_2024.07.18…``,
    ``…T001_L044_2024.07.18…``; the tube, the position (L-number) and the date stay. Any
    other name has no shorter form (the chip elides it in the middle).
    """
    match = _CAPTURE.match(name)
    if match is None:
        return (name,)
    field, core, time, rest = match.group("field", "core", "time", "rest")
    time = time or ""
    forms = [name]
    if rest:
        forms.append(f"{field}_{core}{time}…" if field else f"{core}{time}…")
    if time or rest:
        forms.append(f"{field}_{core}…" if field else f"{core}…")
    if field:
        forms.append(f"…{core}…" if time or rest else f"…{core}")
    return tuple(dict.fromkeys(forms))


class StatusChip(CornerPanel):
    """Canvas chip: mono file name and a coloured save-state dot, the image's place in
    the library (optional), plus an optional muted hint line below them.

    ``set_state`` takes ``"none"`` (faint; no text unless ``label`` is given),
    ``"clean"`` (Saved), ``"dirty"`` (Unsaved), ``"saving"`` or ``"error"``;
    ``label`` replaces the default text, e.g. ``set_state("none", "No mask")`` or
    ``set_state("clean", "Saved · 2/3 checked")``. ``set_position(12, 40)`` shows
    "Image 12 / 40". An :class:`OverlayLayout` narrows the chip with
    :meth:`fit_to_width`: a camera name then drops its least telling parts first
    (:func:`name_forms`: tube, L-number and date stay), the position shortens to
    "12 / 40" and then hides before the name loses those; any other long name is elided
    in the middle (the full name is always the tooltip). The hint line can start with a
    coloured dot (``set_hint(..., dot=tokens.MASK)``), e.g. the active layer of the
    Correct editor.
    """

    STATES = {
        "none": (tokens.TEXT_FAINT, ""),
        "clean": (tokens.OK, "Saved"),
        "dirty": (tokens.WARN, "Unsaved"),
        "saving": (tokens.INFO, "Saving…"),
        "error": (tokens.DANGER, "Error"),
    }
    MIN_NAME_WIDTH = 80  # about a dozen mono characters: "…T001_L044…"
    MIN_HINT_WIDTH = 160  # narrowest useful hint column
    HINT_DOT = "●"
    _HINT_QSS = "color: {color}; font-size: 12px; background: transparent;"

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
        self._forms: tuple[str, ...] = ("",)
        self._positions: tuple[str, ...] = ()  # "Image 12 / 40", "12 / 40"; () hides it
        self._dot: str | None = None
        self._state = "none"

        self.file_label = QLabel()
        self.file_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-family: {tokens.MONO}; font-size: 12px;"
            "background: transparent;"
        )
        self.state_label = QLabel()
        self.state_label.setTextFormat(Qt.TextFormat.RichText)
        self.position_label = QLabel()
        self.position_label.setTextFormat(Qt.TextFormat.PlainText)
        self.position_label.setStyleSheet(
            f"color: {tokens.TEXT_FAINT}; font-size: 11.5px; background: transparent;"
        )
        self.position_label.hide()
        self.hint_label = QLabel()
        self.hint_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hint_label.setStyleSheet(self._HINT_QSS.format(color=tokens.TEXT_MUTED))
        # The hint's leading dot: a separate label so the hint text stays plain text.
        self.hint_dot = QLabel(self.HINT_DOT)
        self.hint_dot.setTextFormat(Qt.TextFormat.PlainText)
        self._dot_color = tokens.TEXT_MUTED  # the colour its style sheet carries
        self.hint_dot.setStyleSheet(self._HINT_QSS.format(color=self._dot_color))
        self.hint_dot.hide()
        name_row = QHBoxLayout()
        name_row.setSpacing(9)
        name_row.addWidget(self.file_label)
        name_row.addWidget(self.state_label)
        name_row.addStretch(1)
        name_row.addWidget(self.position_label)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(6)
        hint_row.addWidget(self.hint_dot, 0, Qt.AlignmentFlag.AlignTop)
        hint_row.addWidget(self.hint_label, 1)
        column = QVBoxLayout()
        column.setSpacing(3)
        column.addLayout(name_row)
        column.addLayout(hint_row)
        self._name_row = name_row
        self._hint_row = hint_row
        self._box.addLayout(column)
        self.set_state("none")
        self.set_hint("")

    # ------------------------------------------------------------------ API
    def set_text(self, name: str) -> None:
        """Show ``name`` (a file name), shortened when it is long (see the class docstring)."""
        self._name = name
        self._forms = name_forms(name)
        self._refresh()
        self.reposition()

    def text(self) -> str:
        return self._name

    def set_position(self, index: int | None, count: int | None = None) -> None:
        """Show "Image ``index`` / ``count``" (1-based) on the name row; None hides it."""
        if index is None or count is None:
            self._positions = ()
            self.position_label.setToolTip("")
        else:
            self._positions = (f"Image {index} / {count}", f"{index} / {count}")
            self.position_label.setToolTip(f"Image {index} of {count} in the library order")
        self._refresh()
        self.reposition()

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

    def set_hint(self, text: str, tooltip: str = "", *, dot: str | None = None) -> None:
        """Show ``text`` on a muted line under the name (hidden while empty).

        ``dot`` is a colour from ``tokens`` (e.g. a layer's ``tokens.MASK``): a "●" in
        that colour then leads the hint; ``None`` hides it. The hint stays plain text.
        """
        self.hint_label.setText(text)
        self.hint_label.setToolTip(tooltip or text)
        self.hint_label.setVisible(bool(text))
        dot = dot if text else None
        if dot is not None and dot != self._dot_color:  # restyle only on a new colour
            self._dot_color = dot
            self.hint_dot.setStyleSheet(self._HINT_QSS.format(color=dot))
        self._dot = dot
        self.hint_dot.setToolTip(tooltip or text)
        self.hint_dot.setVisible(dot is not None)
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

        self.file_label.ensurePolished()
        self.position_label.ensurePolished()
        metrics, spacing = self.file_label.fontMetrics(), self._name_row.spacing()
        name_width = self._max_name_width
        position = self._positions[0] if self._positions else ""
        if inner is not None:
            state = self.state_label
            free = inner - (state.sizeHint().width() + spacing if state.text() else 0)
            # The name keeps its shortest telling form; the position takes what is left.
            kept = min(label_advance(metrics, self._forms[-1]), self._max_name_width)
            position = self._fit_position(free - max(kept, self.MIN_NAME_WIDTH) - spacing)
            used = self._position_width(position) + spacing if position else 0
            name_width = max(self.MIN_NAME_WIDTH, min(name_width, free - used))
        # The longest form that fits, else the shortest elided (a lone form in the middle).
        text = fit_forms(self._forms, metrics, name_width)
        if self.file_label.text() != text:
            self.file_label.setText(text)
        self.file_label.setToolTip(self._name)
        if self.position_label.text() != position:
            self.position_label.setText(position)
        if self.position_label.isHidden() == bool(position):
            self.position_label.setVisible(bool(position))

        hint_width = self._max_hint_width
        if inner is not None:
            hint_width = max(self.MIN_HINT_WIDTH, min(hint_width, inner))
        if self._dot is not None:  # the dot and its spacing share the hint line's width
            self.hint_dot.ensurePolished()
            hint_width -= self.hint_dot.sizeHint().width() + self._hint_row.spacing()
        wrap_label(self.hint_label, hint_width)
        self._box.activate()  # the name row / column layouts cache hints until activated

    def _position_width(self, text: str) -> int:
        return self.position_label.fontMetrics().horizontalAdvance(text) + 1

    def _fit_position(self, width: int) -> str:
        """The longest position text that fits ``width`` px ("" when none does)."""
        return next((text for text in self._positions if self._position_width(text) <= width), "")


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
        pop_shadow(frame, radius=12)  # the card's border-radius

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        col = self._column = QVBoxLayout(frame)  # a subclass may add its own lines below
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
