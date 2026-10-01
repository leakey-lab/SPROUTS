"""The Correct page's own controls on the shared EditorChrome: :class:`CorrectControls` (C20).

A pure view: it shows what the editor hands it (the shown layer, that layer's tool, size and
opacity, the undo history, the polyline draft) and emits requests; it never changes a
layer. What it puts on the chrome (spec sections 3.2 and 4):

* the rail: one group of checkable tool buttons per layer, built from the layer's ``tools``
  (the mask's Brush, Eraser, Fill, then the skeleton's Select, Eraser, Polyline, Connect),
  then Pan and Enhance. One exclusive group spans both, each group checks in its layer's
  colour, and the group of the layer that is not shown is dimmed yet clickable (a click asks
  for that tool; the editor switches the layer); a label both layers use names its layer in
  the tooltip ("Mask eraser", "Skeleton eraser");
* the dock: the size of the current tool (one slider per distinct ``SizeSpec``, made the
  first time a layer shows it, in a slot at the dock's start, so the mask's brush and eraser
  share one and the skeleton's eraser has its own; a disabled placeholder for a tool without
  a size), the opacity of the shown layer, Load skeleton file (enabled for the skeleton
  only), Undo, Redo, Clear and Save. Nothing is hidden, so the dock keeps its width and
  place across tools and layers;
* the polyline prompt (top centre): Finish line, Cancel and Smooth, shown while the
  skeleton is the shown layer and its Polyline draft has points;
* the topbar's ghost button "Clear mask" / "Clear skeleton" (the shell places it).

A click on any of them leaves the keyboard focus on the canvas.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

from app.gui import shortcuts
from app.gui.correct.layer import MASK, SKELETON
from app.gui.widgets import DockSlider, IconButton, keep_canvas_focus, tokens
from app.gui.widgets.buttons import ghost_button, primary_button
from app.gui.widgets.icons import load_icon
from app.gui.widgets.overlays import _FloatingPanel

if TYPE_CHECKING:  # pragma: no cover
    from app.gui.correct.layer import EditLayer, SizeSpec, ToolSpec
    from app.gui.correct.skeleton_layer import DraftState
    from app.gui.editor_chrome import EditorChrome
    from app.gui.widgets import FloatingDock

__all__ = ["CorrectControls"]

_OPACITY_FMT = "{}%"
_LOAD_TIP = "Load skeleton file…"
_LOAD_OFF_TIP = f"{_LOAD_TIP} (edit the skeleton to load one)"
_NO_SIZE_TIP = "No size for this tool"
_WIDEST_SIZE = "000 px"  # every size readout is this wide, so a new value moves nothing
_SMOOTH_TIP = "needs 3+ points"
# The topbar clear button for the shown layer: (text, tooltip).
_CLEAR_TEXTS = {
    MASK: ("Clear mask", "Clear the mask. Undo with Ctrl+Z; Save removes the file."),
    SKELETON: ("Clear skeleton", "Remove every skeleton line from this image (Ctrl+Z undoes)"),
}
# The Smooth toggle shows its word and key beside the icon, as Finish and Cancel show theirs
# (the prompt reads "... Cancel (Esc) Smooth (S)").
_SMOOTH_QSS = f"""
QToolButton#iconBtn {{ padding: 0 10px 0 6px; color: {tokens.TEXT_MUTED}; font-size: 12.5px; }}
QToolButton#iconBtn:checked {{ color: {tokens.TEXT}; }}
"""


def _tool_tooltip(spec: ToolSpec, noun: str = "") -> str:
    """``"Label (Key) · tip"``, as today's rails: a one-word label ("Brush", "Select") gets
    its tip; a label that already says what the tool does ("Fill a closed outline",
    "Connect endpoints") stands alone. ``noun`` names the layer of a label both layers use
    ("Mask eraser (E) · ...")."""
    label = f"{noun[:1].upper()}{noun[1:]} {spec.label.lower()}" if noun else spec.label
    head = f"{label} ({spec.key})"
    return f"{head} · {spec.tip}" if spec.tip and " " not in spec.label else head


def _retip(widget: QWidget, tip: str) -> None:
    if widget.toolTip() != tip:
        widget.setToolTip(tip)


def _set_quietly(control: DockSlider, value: int, fmt: str) -> None:
    """``control`` shows ``value`` without emitting anything; its readout follows."""
    slider = control.slider
    if slider.value() == value:
        return
    blocked = slider.blockSignals(True)
    try:
        slider.setValue(value)
    finally:
        slider.blockSignals(blocked)
    control.value_label.setText(fmt.format(slider.value()))


def _steady(slider: DockSlider) -> DockSlider:
    """``slider`` with a readout as wide as ``_WIDEST_SIZE`` whatever it shows."""
    label = slider.value_label
    label.ensurePolished()
    text = label.text()
    label.setText(_WIDEST_SIZE)
    label.setFixedWidth(max(label.sizeHint().width(), label.minimumWidth()))
    label.setText(text)
    return slider


def _smooth_toggle() -> IconButton:
    """The prompt's Smooth toggle: icon, word and key, checked in the skeleton colour."""
    tip = shortcuts.tooltip("Smooth the polyline", "smooth", shortcuts.CORRECT)
    button = IconButton(
        "smooth", f"{tip} · {_SMOOTH_TIP}", size=32, checkable=True, on_color=tokens.SKEL
    )
    button.setText(shortcuts.tooltip("Smooth", "smooth", shortcuts.CORRECT))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setStyleSheet(button.styleSheet() + _SMOOTH_QSS)
    button.ensurePolished()
    button.setFixedWidth(button.sizeHint().width())
    return button


class CorrectControls(QObject):
    """The Correct page's own controls on the shared EditorChrome: two rail groups, the
    dock controls and the polyline prompt. A pure view: it emits requests and shows
    state; it never changes a layer.

    It starts showing the first layer of ``layers`` (``show_layer`` + ``sync``) with Undo
    and Redo disabled, and registers :attr:`prompt` with the chrome.
    """

    tool_requested = pyqtSignal(str, str)  # (layer_id, tool_id): a rail tool button was clicked
    size_changed = pyqtSignal(int)  # the visible size slider moved (px)
    opacity_changed = pyqtSignal(float)  # the opacity slider moved (0..1, the active layer)
    load_requested = pyqtSignal()  # "Load skeleton file…"
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    clear_requested = pyqtSignal()  # the dock Clear or the topbar clear button
    save_requested = pyqtSignal()
    finish_line_requested = pyqtSignal()  # prompt "Finish line (Enter)"
    cancel_line_requested = pyqtSignal()  # prompt "Cancel (Esc)"
    smooth_toggled = pyqtSignal(bool)  # prompt Smooth toggle

    def __init__(
        self, parent: QWidget, chrome: EditorChrome, layers: Mapping[str, EditLayer]
    ) -> None:
        super().__init__(parent)
        self._chrome = chrome
        self._layers = dict(layers)
        self._shown: str | None = None
        self._draft: DraftState | None = None
        self.tool_buttons: dict[tuple[str, str], IconButton] = {}
        self.size_sliders: dict[SizeSpec, DockSlider] = {}
        self._build_rail(chrome)
        self._build_dock(chrome.dock)
        self.prompt = self._build_prompt(parent)
        chrome.register(prompt=self.prompt)
        # Text variant of Clear for the topbar (shell_chrome reparents it); show_layer names it.
        self.clear_button = ghost_button("", "trash", compact=True)
        keep_canvas_focus(self.clear_button)
        self.clear_button.clicked.connect(self.clear_requested)

        first = next(iter(self._layers))
        self.show_layer(first)
        self.sync(self._layers[first])
        self.set_history(False, False)

    # ------------------------------------------------------------------ building
    def _build_rail(self, chrome: EditorChrome) -> None:
        """Each layer's tools as a group (then a separator), then Pan | Enhance."""
        rail = chrome.rail
        self._tool_group = QButtonGroup(self)  # one exclusive group across both layers
        self._tool_keys: list[tuple[str, str]] = []
        self._groups: dict[str, tuple[IconButton, ...]] = {}
        self._group_colours: dict[str, str] = {}
        labels = [spec.label for layer in self._layers.values() for spec in layer.tools]
        for layer_id, layer in self._layers.items():
            group = []
            for spec in layer.tools:
                noun = layer.noun if labels.count(spec.label) > 1 else ""
                button = IconButton(
                    spec.icon, _tool_tooltip(spec, noun), checkable=True, on_color=layer.color
                )
                button.setAccessibleName(f"{spec.label} ({layer.noun})")  # two Erasers
                self._tool_group.addButton(button, len(self._tool_keys))
                self._tool_keys.append((layer_id, spec.tool_id))
                self.tool_buttons[(layer_id, spec.tool_id)] = button
                rail.add_widget(button)
                group.append(button)
            self._groups[layer_id] = tuple(group)
            self._group_colours[layer_id] = tokens.TEXT_MUTED  # IconButton's default colour
            rail.add_separator()
        rail.add_widget(chrome.pan_button)
        rail.add_separator()
        rail.add_widget(chrome.enhance_button)
        self._tool_group.idClicked.connect(self._tool_clicked)

    def _build_dock(self, dock: FloatingDock) -> None:
        """Size slot, opacity | Load, Undo, Redo, Clear | Save."""
        self._dock = dock
        self._slot = QWidget()  # the size sliders, made on first sight
        self._slot_row = QHBoxLayout(self._slot)
        self._slot_row.setContentsMargins(0, 0, 0, 0)
        self._slot_row.setSpacing(0)
        self.no_size_slider = _steady(DockSlider("ruler", _NO_SIZE_TIP, 0, 1, 0, fmt="–"))
        self.no_size_slider.setEnabled(False)
        self._slot_row.addWidget(self.no_size_slider)
        dock.add_widget(self._slot)
        self.opacity_slider = DockSlider("overlay", "", 0, 100, 100, fmt=_OPACITY_FMT)
        self.opacity_slider.slider.valueChanged.connect(self._opacity_moved)
        dock.add_widget(self.opacity_slider)
        dock.add_separator()

        scope = shortcuts.CORRECT
        redo_keys = " / ".join(shortcuts.keys_for("redo", scope))
        self.load_button = IconButton("load", _LOAD_TIP)
        self.undo_button = IconButton("undo", shortcuts.tooltip("Undo", "undo", scope))
        self.redo_button = IconButton("redo", f"Redo ({redo_keys})" if redo_keys else "Redo")
        self.clear_icon_button = IconButton("trash")  # show_layer names the layer it clears
        self.save_button: QPushButton = primary_button(
            "Save",
            "save",
            tooltip=shortcuts.tooltip("Save the unsaved layers", "save", scope),
            compact=True,
        )
        actions = (
            (self.load_button, self.load_requested),
            (self.undo_button, self.undo_requested),
            (self.redo_button, self.redo_requested),
            (self.clear_icon_button, self.clear_requested),
        )
        for button, request in actions:
            button.clicked.connect(request)
            dock.add_widget(button)
        dock.add_separator()
        self.save_button.clicked.connect(self.save_requested)
        dock.add_widget(self.save_button)

    def _build_prompt(self, host: QWidget) -> QFrame:
        """Finish line (Enter) · Cancel (Esc) · Smooth, hidden until a draft has points."""
        prompt = _FloatingPanel(host, horizontal=True)
        prompt.setObjectName("polylinePrompt")
        self.finish_button = primary_button("Finish line (Enter)", "check", compact=True)
        self.cancel_button = ghost_button("Cancel (Esc)", compact=True)
        self.cancel_button.setIcon(load_icon("xmark", tokens.DANGER, 16))
        self.smooth_button = _smooth_toggle()
        self.finish_button.clicked.connect(self.finish_line_requested)
        self.cancel_button.clicked.connect(self.cancel_line_requested)
        self.smooth_button.clicked.connect(self.smooth_toggled)
        for button in (self.finish_button, self.cancel_button, self.smooth_button):
            prompt.add_widget(button)  # a click keeps the canvas focus
        prompt.hide()
        return prompt

    # ---------------------------------------------------------------------- state
    def show_layer(self, layer_id: str) -> None:
        """Show ``layer_id`` as the edited layer: its tool group in full colour and the
        others dimmed (still clickable), Load enabled for the skeleton only, the clear
        buttons and the opacity slider named for it, the polyline prompt for the skeleton."""
        layer = self._layers[layer_id]
        for group_id, buttons in self._groups.items():
            colour = tokens.TEXT_MUTED if group_id == layer_id else tokens.TEXT_DISABLED
            if self._group_colours[group_id] != colour:  # recolour only on a real switch
                self._group_colours[group_id] = colour
                for button in buttons:
                    button.set_colors(color=colour)
        self.load_button.setEnabled(layer_id == SKELETON)
        _retip(self.load_button, _LOAD_TIP if layer_id == SKELETON else _LOAD_OFF_TIP)
        noun = layer.noun
        text, tip = _CLEAR_TEXTS.get(
            layer_id, (f"Clear {noun}", f"Clear the {noun} (Ctrl+Z undoes)")
        )
        if self.clear_button.text() != text:
            self.clear_button.setText(text)
        _retip(self.clear_button, tip)
        _retip(self.clear_icon_button, f"Clear {noun} (undoable)")
        _retip(self.opacity_slider, f"{noun[:1].upper()}{noun[1:]} opacity")
        self._shown = layer_id
        self._show_prompt()

    def sync(self, layer: EditLayer) -> None:
        """Show ``layer``'s state (the shown layer's): its current tool checked (the rail's
        Tab stop), the size slider of its tool's ``SizeSpec`` at ``layer.size()`` (made on
        first sight; the disabled placeholder for a tool without a size) and its opacity in
        percent. The sliders' signals are blocked meanwhile: it emits nothing."""
        button = self.tool_buttons.get((layer.layer_id, layer.current_tool))
        if button is not None:
            if not button.isChecked():
                button.setChecked(True)
            self._tab_stop(button)
        spec = layer.size_spec()
        shown = None if spec is None else self._size_slider(spec)
        if shown is not None:
            _set_quietly(shown, layer.size(), spec.fmt)
        for slider in self.size_sliders.values():
            slider.setVisible(slider is shown)
        self.no_size_slider.setVisible(shown is None)
        _set_quietly(self.opacity_slider, round(layer.opacity() * 100), _OPACITY_FMT)

    def set_history(self, can_undo: bool, can_redo: bool) -> None:
        """Enable Undo and Redo as the undo timeline allows."""
        self.undo_button.setEnabled(bool(can_undo))
        self.redo_button.setEnabled(bool(can_redo))

    def set_draft(self, state: DraftState | None) -> None:
        """Show the polyline draft: the prompt appears for a Polyline draft with points
        (while the skeleton is the shown layer), Finish from 2 points, Smooth as the draft
        has it; ``None`` hides it. Emits nothing."""
        self._draft = state
        if state is not None:
            self.finish_button.setEnabled(state.points >= 2)
            self.smooth_button.setChecked(state.smooth)
        self._show_prompt()

    # ------------------------------------------------------------------ internals
    def _show_prompt(self) -> None:
        state = self._draft
        wanted = (
            self._shown == SKELETON and state is not None and state.in_polyline and state.points > 0
        )
        if self.prompt.isHidden() == wanted:
            self.prompt.setVisible(wanted)

    def _size_slider(self, spec: SizeSpec) -> DockSlider:
        """The dock slider of ``spec``; the first sight makes it, in the slot at the start."""
        slider = self.size_sliders.get(spec)
        if slider is None:
            slider = DockSlider(
                spec.icon, spec.tooltip, spec.minimum, spec.maximum, spec.minimum, fmt=spec.fmt
            )
            slider.slider.valueChanged.connect(self.size_changed)
            slider.set_compact(self._dock.is_compact())
            _steady(slider)
            self._slot_row.addWidget(slider)
            keep_canvas_focus(slider)
            self.size_sliders[spec] = slider
            self._chrome.order_tab_focus()  # in the dock's place, not after everything
        return slider

    def _tab_stop(self, checked: IconButton) -> None:
        """Tab reaches the tools at the checked one, as a radio group (Qt's own rule is the
        tool focused last, else the first one)."""
        for button in self.tool_buttons.values():
            policy = Qt.FocusPolicy.TabFocus if button is checked else Qt.FocusPolicy.NoFocus
            if button.focusPolicy() != policy:
                button.setFocusPolicy(policy)

    def _tool_clicked(self, index: int) -> None:
        self.tool_requested.emit(*self._tool_keys[index])

    def _opacity_moved(self, value: int) -> None:
        self.opacity_changed.emit(value / 100)
