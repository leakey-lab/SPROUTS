"""Read-only metrics strip shown beneath the display canvas.

Was a 56px two-line strip whose four cells, in practice, read
``— · — · 18×13.5 mm · <status>`` — root length and area are only produced by
the batch threads and written to CSV (KNOWN_ISSUES #3), so two of the four cells
are permanently em-dashes and a third is a constant. 56px for one live value.

Now a 26px single-line strip that only shows cells it can actually fill: the
selected image name, then LENGTH / AREA *when measured values exist*, then the
fixed FOV, then STATUS. Cells with nothing to say are hidden rather than shown
as ``—``, so the strip stays quiet until it has content.

Still pure presentation: ``set_metrics`` formats what it is handed and never
computes anything. Measured values come from the Measure CSVs through
:class:`app.data_processing.measurement_index.MeasurementIndex` (read by the
caller); each value's tooltip names what it was measured from (the caller's ``source``:
the GT file or the prediction, O1, and the CSV); a failed measurement shows ``failed`` with
its message, and a value whose inputs changed after it was measured is shown in the warning
colour with a "Measure again" tooltip (plus a line saying so when the comparison was by file time:
the CSV has no record of its inputs). A value measured with another field of view than the
open project's, or (length) another length method, is shown outdated the same way, its
tooltip saying why (the caller's ``method_notes``, O3). A value that is not a number (a CSV edited to hold ``nan``
or ``inf``) shows ``—`` in the failure colour, never "nan mm". The FOV text is derived from
:data:`app.config.CALIBRATION` (the single source of the field of view) until a measured value
is shown: then :meth:`MetricsBar.set_fov` shows the field of view recorded with that value's
CSV (M2). The
value QLabels keep their ``—`` fallback text (and their ``_length_value`` /
``_area_value`` / ``_status_value`` names) so existing callers and tests are
unaffected.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPainter, QPalette
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from app import config
from app.calibration import ProjectCalibration, calibration_words
from app.gui.key_text import platform_keys
from app.gui.library_index import fit_label, stem_forms
from app.gui.widgets import tokens
from app.provenance import same_calibration

_DASH = "—"
_FAILED = "failed"
#: The input a value was measured from changed after the Measure run (``{}``: which one).
_OUTDATED_TIP = "{} changed after this measurement — run Measure again for a current value."
#: The CSV row holds no number to show (``nan``, ``inf``): ``{}`` the value, ``{}`` the source
#: (what it was measured from and its CSV).
_NO_NUMBER_TIP = "This image has no valid number ({}): {}"
_LENGTH_INPUT = "A skeleton of this image (GT or prediction)"
#: An outdated value whose CSV has no record of its inputs: compared by file time (R5).
_BY_TIME_TIP = "Compared by file time: this CSV has no record of its inputs."
_ZOOM_TIP = platform_keys(
    "Ctrl+0 fit · Ctrl+1 100 % · Ctrl+= / Ctrl+- zoom · Ctrl+wheel zooms about the cursor"
)
_AREA_INPUT = "A mask of this image (GT or prediction)"


def fov_text(calibration: Mapping[str, float] | None = None) -> str:
    """Display text of a field of view, e.g. ``18×13.5 mm``: ``calibration`` (``fov_width_mm``,
    ``fov_height_mm``, as a CSV's provenance records it), else the running one."""
    if calibration is None:
        cal = config.CALIBRATION
        return f"{cal.fov_width_mm:g}×{cal.fov_height_mm:g} mm"
    return f"{calibration['fov_width_mm']:g}×{calibration['fov_height_mm']:g} mm"


def fov_owner_tip(current: ProjectCalibration | Mapping[str, Any] | None = None) -> str:
    """The FOV cell's tooltip while no CSV's own field of view is shown: "This project's field
    of view" for a project file's calibration, else "This app's field of view"
    (:func:`app.calibration.calibration_words`)."""
    return f"{calibration_words(current).owner.capitalize()} field of view"


def differs_from_current(
    calibration: Mapping[str, float], current: Mapping[str, float] | None = None
) -> bool:
    """True when ``calibration`` (a CSV's recorded field of view) is another field of view than
    ``current`` (the open project's, O3; ``None``: the running default), compared as numbers
    (the one rule of the export, the bar and About)."""
    if current is None:
        cal = config.CALIBRATION
        current = {"fov_width_mm": cal.fov_width_mm, "fov_height_mm": cal.fov_height_mm}
    return not same_calibration(calibration, current)


class _ElidedLabel(QLabel):
    """A label that shortens its text instead of widening its layout.

    ``text()`` keeps the full string (callers and tests read it); only the painted
    text is shortened, and the full text becomes the tooltip whenever it is cut.
    ``forms(text)`` (optional) lists shorter forms, most complete first: the first
    that fits is painted, and when none fits the shortest is elided at its end.
    Without shorter forms the text is elided in the middle.
    """

    MIN_WIDTH = 72

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        forms: Callable[[str], list[str]] | None = None,
    ):
        super().__init__(text, parent)
        self._forms = forms
        self._forms_of: tuple[str, list[str]] = ("", [""])  # (text, its forms), cached
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self.MIN_WIDTH), hint.height())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        super().setText(text)
        self._sync_tooltip()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_tooltip()

    def _text_forms(self) -> list[str]:
        text = self.text()
        if self._forms_of[0] != text:
            self._forms_of = (text, (self._forms(text) if self._forms else None) or [text])
        return self._forms_of[1]

    def _elided(self) -> str:
        width = self.contentsRect().width()
        metrics = self.fontMetrics()
        forms = self._text_forms()
        if len(forms) == 1:
            return metrics.elidedText(forms[0], Qt.TextElideMode.ElideMiddle, width)
        shown = fit_label(forms, lambda form: metrics.horizontalAdvance(form) <= width)
        return metrics.elidedText(shown, Qt.TextElideMode.ElideRight, width)

    def _sync_tooltip(self) -> None:
        self.setToolTip(self.text() if self._elided() != self.text() else "")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        self.style().drawItemText(
            painter,
            self.contentsRect(),
            int(self.alignment()),
            self.palette(),
            self.isEnabled(),
            self._elided(),
            QPalette.ColorRole.WindowText,
        )


def _value_qss(color: str) -> str:
    return (
        f"color: {color}; font-family: {tokens.MONO}; font-size: 9.5pt; "
        f"font-weight: 600; border: none;"
    )


class MetricsBar(QWidget):
    """A thin read-only strip of measured metrics under the display canvas."""

    HEIGHT = 26

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("metricsBar")
        self.setFixedHeight(self.HEIGHT)
        self.setStyleSheet(
            f"QWidget#metricsBar {{ background-color: {tokens.BG_1}; "
            f"border-top: 1px solid {tokens.BORDER}; }}"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        # The bar's minimum is minimumSizeHint() (without the ZOOM cell), not its layout's.
        row.setSizeConstraint(QHBoxLayout.SizeConstraint.SetNoConstraint)

        # A long image name falls back to its tube, depth, date and time (stem_forms).
        self._name_cell, self._name_value = self._add_cell(row, None, first=True, elide=True)
        self._name_value.setStyleSheet(
            f"color: {tokens.TEXT}; font-size: 11.5px; font-weight: 600; border: none;"
        )
        self._length_cell, self._length_value = self._add_cell(row, "LENGTH", accent=True)
        self._area_cell, self._area_value = self._add_cell(row, "AREA")
        self._fov_cell, self._fov_value = self._add_cell(row, "FOV")
        self.set_fov(fov_text(), fov_owner_tip())
        self._status_cell, self._status_value = self._add_cell(row, "STATUS")
        # The Library display's zoom (the editors show theirs in their zoom cluster).
        self._zoom_cell, self._zoom_value = self._add_cell(row, "ZOOM")
        self._zoom_cell.setToolTip(_ZOOM_TIP)
        self._zoom: float | None = None
        self._cells = (
            self._name_cell,
            self._length_cell,
            self._area_cell,
            self._fov_cell,
            self._status_cell,
            self._zoom_cell,
        )

        row.addStretch(1)

        self.set_metrics()

    def _add_cell(
        self,
        row: QHBoxLayout,
        label_text: str | None,
        accent: bool = False,
        first: bool = False,
        elide: bool = False,
    ) -> tuple[QFrame, QLabel]:
        """Add one inline label/value cell; return (cell, value QLabel).

        ``elide`` makes the value shorten itself (middle ellipsis) rather than set the
        bar's minimum width; used for the free-length image name.
        """
        cell = QFrame()
        cell.setObjectName("metricsCell")
        self._set_separator(cell, not first)

        line = QHBoxLayout(cell)
        line.setContentsMargins(13, 0, 13, 0)
        line.setSpacing(7)

        if label_text:
            label = QLabel(label_text)
            label.setStyleSheet(
                f"color: {tokens.TEXT_FAINT}; font-family: {tokens.MONO}; "
                f"font-size: 7.5pt; font-weight: 600; letter-spacing: 1px; border: none;"
            )
            line.addWidget(label)

        value = _ElidedLabel(_DASH, forms=stem_forms) if elide else QLabel(_DASH)
        value.setProperty("baseColor", tokens.ACCENT if accent else tokens.TEXT)
        value.setStyleSheet(_value_qss(value.property("baseColor")))
        line.addWidget(value)

        row.addWidget(cell)
        return cell, value

    def set_metrics(
        self,
        length: float | None = None,
        area: float | None = None,
        status: str | None = None,
        name: str | None = None,
        *,
        error: str | None = None,
        stale: bool = False,
        source: str | None = None,
        area_stale: bool | None = None,
        area_source: str | None = None,
        length_note: str | None = None,
        status_tip: str | None = None,
        by_time: bool = False,
        area_by_time: bool | None = None,
        area_note: str | None = None,
        method_notes: Mapping[str, str] | None = None,
    ) -> None:
        """Update the displayed values (read-only). Empty cells are hidden.

        ``error`` (a failed Measure row) is shown as ``failed`` in the first
        metric cell that has no value, with the message as its tooltip.
        ``stale`` flags a value whose input changed after it was measured and ``source``
        names what it was read from (tooltip: "Measured value from …", the measured file
        and its CSV): the LENGTH value's, and the AREA value's too unless ``area_stale`` /
        ``area_source`` give AREA its own. ``length_note`` / ``area_note`` are one more line
        for each tooltip (e.g. that a GT file exists and was not used); ``status_tip`` is
        the STATUS cell's tooltip. ``by_time`` says the LENGTH value's staleness was judged by file
        time (its CSV has no record of its inputs), and the AREA value's too unless
        ``area_by_time`` gives AREA its own; an outdated value's tooltip then says so.
        ``method_notes`` (``{"length": note, "area": note}``, O3): a value with a note was
        measured with another field of view or length method; it is shown outdated and the
        note is a line of its own in its tooltip.
        """
        method_notes = method_notes or {}
        length_error = error if error and length is None else None
        area_error = error if error and length is not None and area is None else None
        if area_stale is None:
            area_stale = stale
        if area_source is None:
            area_source = source
        if area_by_time is None:
            area_by_time = by_time
        self._show_metric(
            self._length_cell,
            self._length_value,
            length,
            "mm",
            length_error,
            stale,
            source,
            changed=_LENGTH_INPUT,
            note=length_note,
            by_time=by_time,
            method_note=method_notes.get("length", ""),
        )
        self._show_metric(
            self._area_cell,
            self._area_value,
            area,
            "mm²",
            area_error,
            area_stale,
            area_source,
            changed=_AREA_INPUT,
            note=area_note,
            by_time=area_by_time,
            method_note=method_notes.get("area", ""),
        )
        self._status_value.setText(status if status else _DASH)
        self._name_value.setText(name if name else _DASH)
        self._status_cell.setToolTip(status_tip if status and status_tip else "")
        self._status_cell.setVisible(bool(status))
        self._name_cell.setVisible(bool(name))
        self._sync_separators()
        self._fit_zoom()

    def set_fov(self, text: str, tooltip: str = "") -> None:
        """Show ``text`` in the FOV cell (the field of view of the shown measurement, or the
        app's) with ``tooltip``; read-only like the rest of the bar."""
        self._fov_value.setText(text)
        self._fov_cell.setToolTip(tooltip)

    def set_zoom(self, factor: float | None) -> None:
        """Show the display's zoom (``1.0`` = 100 %), while an image is named; ``None``
        (or a factor that is not a positive number) hides it."""
        if factor is None or not math.isfinite(factor) or factor <= 0:
            self._zoom = None
        else:
            self._zoom = float(factor)
            percent = self._zoom * 100
            self._zoom_value.setText(f"{percent:.0f} %" if percent >= 10 else f"{percent:.1f} %")
        self._fit_zoom()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """The cells' minimum without the ZOOM cell: it never widens the window."""
        hint = self.layout().minimumSize()
        if not self._zoom_cell.isHidden():
            hint.setWidth(max(0, hint.width() - self._zoom_cell.minimumSizeHint().width()))
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_zoom()

    def _fit_zoom(self) -> None:
        """Show the ZOOM cell while there is a zoom to report (an image is named) and room
        for it next to the other cells: on a narrow window it is the first to go."""
        cell = self._zoom_cell
        wanted = self._zoom is not None and not self._name_cell.isHidden()
        if wanted:
            others = self.layout().minimumSize().width()
            if not cell.isHidden():
                others -= cell.minimumSizeHint().width()
            wanted = self.width() >= others + cell.minimumSizeHint().width()
        if wanted != (not cell.isHidden()):
            cell.setVisible(wanted)
            self._sync_separators()
            self.updateGeometry()

    def _sync_separators(self) -> None:
        """A separator in front of every shown cell but the first one shown."""
        lead = True
        for cell in self._cells:
            if not cell.isHidden():
                self._set_separator(cell, not lead)
                lead = False

    @staticmethod
    def _set_separator(cell: QFrame, on: bool) -> None:
        """Give ``cell`` a left separator (restyled only when that changes)."""
        if cell.property("separator") == on:
            return
        cell.setProperty("separator", on)
        border = f"border-left: 1px solid {tokens.BORDER};" if on else "border: none;"
        cell.setStyleSheet(f"QFrame#metricsCell {{ background-color: transparent; {border} }}")

    @staticmethod
    def _show_metric(
        cell: QFrame,
        label: QLabel,
        value: float | None,
        unit: str,
        error: str | None,
        stale: bool,
        source: str | None,
        *,
        changed: str = "Its input",
        note: str | None = None,
        by_time: bool = False,
        method_note: str = "",
    ) -> None:
        """Fill one metric cell: a value, ``failed`` + message, or hidden. ``changed`` names
        the input an outdated value was measured from; ``note`` is one more tooltip line;
        ``by_time``: an outdated value was judged by file time (said in its tooltip);
        ``method_note``: the value was measured with another field of view or length method
        (outdated, the note a tooltip line of its own).
        A value that is not a finite number shows the dash in the failure colour."""
        if value is not None and not math.isfinite(value):
            label.setText(_DASH)
            outdated = False
            color = tokens.DANGER
            tips = [_NO_NUMBER_TIP.format(value, source or "the measurement CSV")]
            if note:
                tips.append(note)
        elif value is not None:
            label.setText(f"{value:.2f} {unit}")
            outdated = bool(stale) or bool(method_note)
            color = tokens.WARN if outdated else label.property("baseColor")
            tips = [f"Measured value from {source}" if source else "Measured value"]
            if stale:
                tips.append(_OUTDATED_TIP.format(changed))
                if by_time:
                    tips.append(_BY_TIME_TIP)
            if method_note:
                tips.append(method_note)
            if note:
                tips.append(note)
        elif error:
            label.setText(_FAILED)
            outdated = False
            color = tokens.DANGER
            tips = [f"Measurement failed — {error}"]
        else:
            label.setText(_DASH)
            label.setProperty("stale", False)
            cell.setToolTip("")
            cell.setVisible(False)
            return
        label.setProperty("stale", outdated)
        label.setStyleSheet(_value_qss(color))
        cell.setToolTip("\n".join(tips))
        cell.setVisible(True)
