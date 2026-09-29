"""Read-only metrics strip shown beneath the display canvas.

Was a 56px two-line strip whose four cells, in practice, read
``— · — · 18×13 mm · Mask ready`` — root length and area are only produced by
the batch threads and written to CSV (KNOWN_ISSUES #3), so two of the four cells
are permanently em-dashes and a third is a constant. 56px for one live value.

Now a 26px single-line strip that only shows cells it can actually fill: the
selected image name, then LENGTH / AREA *when measured values exist*, then the
fixed FOV, then STATUS. Cells with nothing to say are hidden rather than shown
as ``—``, so the strip stays quiet until it has content.

Still pure presentation: ``set_metrics`` formats what it is handed and never
computes anything. Measured values come from the Measure CSVs through
:class:`app.data_processing.measurement_index.MeasurementIndex` (read by the
caller); a failed measurement shows ``failed`` with its message, and a value
whose inputs changed after it was measured is shown in the warning colour with a
"Measure again" tooltip. The FOV text is derived from
:data:`app.config.CALIBRATION` (the single source of the field of view). The
value QLabels keep their ``—`` fallback text (and their ``_length_value`` /
``_area_value`` / ``_status_value`` names) so existing callers and tests are
unaffected.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from app import config
from app.gui.widgets import tokens

_DASH = "—"
_FAILED = "failed"
_OUTDATED_TIP = "Edited after this measurement — run Measure again for a current value."


def fov_text() -> str:
    """Display text of the calibrated field of view, e.g. ``18×13 mm``."""
    cal = config.CALIBRATION
    return f"{cal.fov_width_mm:g}×{cal.fov_height_mm:g} mm"


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

        self._name_cell, self._name_value = self._add_cell(row, None, first=True)
        self._name_value.setStyleSheet(
            f"color: {tokens.TEXT}; font-size: 11.5px; font-weight: 600; border: none;"
        )
        self._length_cell, self._length_value = self._add_cell(row, "LENGTH", accent=True)
        self._area_cell, self._area_value = self._add_cell(row, "AREA")
        self._fov_cell, self._fov_value = self._add_cell(row, "FOV")
        self._fov_value.setText(fov_text())
        self._fov_cell.setToolTip("Calibrated field of view (app.config.CALIBRATION)")
        self._status_cell, self._status_value = self._add_cell(row, "STATUS")

        row.addStretch(1)

        self.set_metrics()

    def _add_cell(
        self,
        row: QHBoxLayout,
        label_text: str | None,
        accent: bool = False,
        first: bool = False,
    ) -> tuple[QFrame, QLabel]:
        """Add one inline label/value cell; return (cell, value QLabel)."""
        cell = QFrame()
        cell.setObjectName("metricsCell")
        border = "none" if first else f"border-left: 1px solid {tokens.BORDER};"
        cell.setStyleSheet(f"QFrame#metricsCell {{ background-color: transparent; {border} }}")

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

        value = QLabel(_DASH)
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
    ) -> None:
        """Update the displayed values (read-only). Empty cells are hidden.

        ``error`` (a failed Measure row) is shown as ``failed`` in the first
        metric cell that has no value, with the message as its tooltip.
        ``stale`` flags values whose inputs changed after they were measured;
        ``source`` names the CSV the values were read from (tooltip).
        """
        length_error = error if error and length is None else None
        area_error = error if error and length is not None and area is None else None
        self._show_metric(
            self._length_cell, self._length_value, length, "mm", length_error, stale, source
        )
        self._show_metric(self._area_cell, self._area_value, area, "mm²", area_error, stale, source)
        self._status_value.setText(status if status else _DASH)
        self._name_value.setText(name if name else _DASH)
        self._status_cell.setVisible(bool(status))
        self._name_cell.setVisible(bool(name))

    @staticmethod
    def _show_metric(
        cell: QFrame,
        label: QLabel,
        value: float | None,
        unit: str,
        error: str | None,
        stale: bool,
        source: str | None,
    ) -> None:
        """Fill one metric cell: a value, ``failed`` + message, or hidden."""
        if value is not None:
            label.setText(f"{value:.2f} {unit}")
            outdated = bool(stale)
            color = tokens.WARN if outdated else label.property("baseColor")
            tips = [f"Measured value from {source}" if source else "Measured value"]
            if outdated:
                tips.append(_OUTDATED_TIP)
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
