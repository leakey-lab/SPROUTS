"""Enhance popover content (:class:`NormalizationControls`) and the legacy
:class:`ImageNormalization` facade.

The math, settings validation, decoded-source cache and saved-defaults store live in the
Qt-free :mod:`app.enhancement`; this module is the thin Qt adapter the editors use:

* ``controls.enhancement_changed`` - re-render the image (debounced while sliders move);
* ``controls.is_active()`` / ``controls.apply_to(bgr)`` / ``controls.display_pixmap(path)``;
* ``controls.settings()`` / ``controls.set_settings(settings)``.

Enhancement is display-only: it never changes masks, skeletons or measurements.
"""

from __future__ import annotations

import logging

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.enhancement import (
    METHOD_CLAHE,
    METHOD_STRETCH,
    METHODS,
    EnhanceDefaultsStore,
    EnhanceSettings,
    SourceImageCache,
    clahe,
    contrast_stretch,
    enhance,
)

from .widgets.buttons import ghost_button, primary_button

logger = logging.getLogger(__name__)

__all__ = ["ImageNormalization", "NormalizationControls", "bgr_to_qpixmap"]

PREVIEW_DELAY_MS = 80
_SAVED_FEEDBACK_MS = 1500


class ImageNormalization:
    """Static facade kept for existing callers; see :mod:`app.enhancement`."""

    @staticmethod
    def apply_clahe(img, clip_limit=2.0, tile_size=(8, 8)):
        """CLAHE (Contrast Limited Adaptive Histogram Equalization) on the L channel."""
        return clahe(img, clip_limit, tile_size)

    @staticmethod
    def apply_contrast_stretching(img, lower_percentile=2, upper_percentile=98):
        """Per-channel percentile contrast stretch (flat channels are left unchanged)."""
        return contrast_stretch(img, lower_percentile, upper_percentile)

    @staticmethod
    def apply(img: np.ndarray, settings: EnhanceSettings) -> np.ndarray:
        """``img`` enhanced with ``settings``."""
        return enhance(img, settings)


def bgr_to_qpixmap(img: np.ndarray) -> QPixmap:
    """A QPixmap (owning its pixels) from a BGR or grayscale uint8 array."""
    img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    fmt = QImage.Format.Format_Grayscale8 if img.ndim == 2 else QImage.Format.Format_BGR888
    return QPixmap.fromImage(QImage(img.data, w, h, img.strides[0], fmt).copy())


def _slider(lo: int, hi: int, tooltip: str) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(lo, hi)
    slider.setToolTip(tooltip)
    return slider


class NormalizationControls(QWidget):
    """Enhance settings laid out for the 264 px Enhance popover.

    Moving any control turns the enhancement on and emits :attr:`enhancement_changed`
    once the controls have been still for ``preview_delay_ms`` (live preview). "Apply"
    turns it on at once; "Show original" shows the untouched image while checked. The
    on/off state stays across image switches, so editors re-render on every image load.
    """

    #: The displayed image should be re-rendered (``display_pixmap`` / ``apply_to``).
    enhancement_changed = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        store: EnhanceDefaultsStore | None = None,
        preview_delay_ms: int = PREVIEW_DELAY_MS,
    ):
        super().__init__(parent)
        self._store = store if store is not None else EnhanceDefaultsStore()
        self._sources = SourceImageCache()
        self._enabled = False
        self._updating = False
        self.current_image = None

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(preview_delay_ms)
        self._preview_timer.timeout.connect(self.enhancement_changed)

        self.default_values: dict = {}
        initial = self.load_defaults()
        self.init_ui()
        self.set_settings(initial)

    # ------------------------------------------------------------------ editor API
    def settings(self) -> EnhanceSettings:
        """The settings the controls currently show."""
        return EnhanceSettings(
            method=self.method_combo.currentText(),
            clip=self.clip_slider.value(),
            tile=self.tile_slider.value(),
            lower=self.lower_slider.value(),
            upper=self.upper_slider.value(),
        )

    def set_settings(self, settings: EnhanceSettings) -> None:
        """Show ``settings`` (re-renders when the enhancement is on)."""
        self._updating = True
        try:
            self.method_combo.setCurrentText(settings.method)
            self.clip_slider.setValue(settings.clip)
            self.tile_slider.setValue(settings.tile)
            self.lower_slider.setValue(settings.lower)
            self.upper_slider.setValue(settings.upper)
        finally:
            self._updating = False
        self.on_method_changed(settings.method)
        if self._enabled:
            self._preview_timer.start()

    def is_active(self) -> bool:
        """True when the displayed image should be enhanced."""
        return self._enabled and not self.original_check.isChecked()

    def apply_to(self, image: np.ndarray) -> np.ndarray:
        """``image`` enhanced when active, else ``image`` itself (never modified)."""
        return enhance(image, self.settings()) if self.is_active() else image

    def display_pixmap(self, path: str) -> QPixmap | None:
        """What the editor should show for the image at ``path`` (None if unreadable).

        The decoded file is cached, so live previews never re-read it.
        """
        source = self._sources.get(path)
        return None if source is None else bgr_to_qpixmap(self.apply_to(source))

    def set_image(self, image):
        """Remember the current image; Apply is enabled only when there is one."""
        self.current_image = image
        self.apply_button.setEnabled(image is not None)

    # ------------------------------------------------------------------ saved defaults
    def load_defaults(self) -> EnhanceSettings:
        """Read the user's saved defaults (validated; never raises)."""
        settings = self._store.load()
        self.default_values = settings.to_dict()
        return settings

    def save_current_as_defaults(self) -> bool:
        """Save the current settings as the user's defaults (per-user config file)."""
        settings = self.settings()
        ok = self._store.save(settings)
        if ok:
            self.default_values = settings.to_dict()
        self._flash_save_result(ok)
        return ok

    def reset_to_defaults(self):
        """Put the controls back to the user's saved defaults."""
        self.set_settings(self.load_defaults())

    # ------------------------------------------------------------------ layout
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.method_label = QLabel("Method")
        self.method_combo = QComboBox()
        self.method_combo.addItems(list(METHODS))
        # Size from a short minimum, not the longest item: the popover is only 264 px wide.
        self.method_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.method_combo.setMinimumContentsLength(8)
        self.method_combo.setToolTip(
            "CLAHE: local contrast (adaptive histogram equalization).\n"
            "Contrast Stretching: map the chosen percentiles to black and white."
        )
        method_block = QVBoxLayout()
        method_block.setSpacing(2)
        method_block.addWidget(self.method_label)
        method_block.addWidget(self.method_combo)
        layout.addLayout(method_block)

        self.clip_label = QLabel()
        self.clip_slider = _slider(
            *EnhanceSettings.RANGES["clip"], "CLAHE clip limit: higher = stronger contrast"
        )
        self.tile_label = QLabel()
        self.tile_slider = _slider(
            *EnhanceSettings.RANGES["tile"], "CLAHE tile grid: smaller = more local contrast"
        )
        self.clahe_group = self._group(
            (self.clip_label, self.clip_slider), (self.tile_label, self.tile_slider)
        )
        layout.addWidget(self.clahe_group)

        self.lower_label = QLabel()
        self.lower_slider = _slider(
            *EnhanceSettings.RANGES["lower"], "Percentile of each channel mapped to black"
        )
        self.upper_label = QLabel()
        self.upper_slider = _slider(
            *EnhanceSettings.RANGES["upper"], "Percentile of each channel mapped to white"
        )
        self.contrast_group = self._group(
            (self.lower_label, self.lower_slider), (self.upper_label, self.upper_slider)
        )
        layout.addWidget(self.contrast_group)

        buttons = QHBoxLayout()
        self.apply_button = primary_button(
            "Apply", tooltip="Enhance the image now (sliders also preview live)"
        )
        self.restore_defaults_button = QPushButton("Reset")
        self.restore_defaults_button.setToolTip("Reset the sliders to your saved defaults")
        buttons.addWidget(self.apply_button, 1)
        buttons.addWidget(self.restore_defaults_button, 1)
        layout.addLayout(buttons)

        self.original_check = QCheckBox("Show original")
        self.original_check.setToolTip("Show the image without enhancement (the settings are kept)")
        layout.addWidget(self.original_check)

        self.save_defaults_button = ghost_button(
            "Save as default", tooltip="Use these settings by default from now on"
        )
        layout.addWidget(self.save_defaults_button)

        self.method_combo.currentTextChanged.connect(self.on_method_changed)
        self.clip_slider.valueChanged.connect(self.on_clip_changed)
        self.tile_slider.valueChanged.connect(self.on_tile_changed)
        self.lower_slider.valueChanged.connect(self.on_percentile_changed)
        self.upper_slider.valueChanged.connect(self.on_percentile_changed)
        for signal in (
            self.method_combo.currentTextChanged,
            self.clip_slider.valueChanged,
            self.tile_slider.valueChanged,
            self.lower_slider.valueChanged,
            self.upper_slider.valueChanged,
        ):
            signal.connect(self._on_control_changed)
        self.apply_button.clicked.connect(self._on_apply)
        self.original_check.toggled.connect(self._on_original_toggled)
        self.save_defaults_button.clicked.connect(self.save_current_as_defaults)
        self._save_feedback_timer = QTimer(self)
        self._save_feedback_timer.setSingleShot(True)
        self._save_feedback_timer.setInterval(_SAVED_FEEDBACK_MS)
        self._save_feedback_timer.timeout.connect(
            lambda: self.save_defaults_button.setText("Save as default")
        )
        self.restore_defaults_button.clicked.connect(self.reset_to_defaults)

    @staticmethod
    def _group(*rows: tuple[QLabel, QSlider]) -> QWidget:
        """A frameless column of 'label over full-width slider' rows."""
        group = QWidget()
        column = QVBoxLayout(group)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        for label, slider in rows:
            column.addWidget(label)
            column.addWidget(slider)
        return group

    # ------------------------------------------------------------------ slots
    def on_method_changed(self, method):
        """Show only the selected method's sliders."""
        self.clahe_group.setVisible(method == METHOD_CLAHE)
        self.contrast_group.setVisible(method == METHOD_STRETCH)

    def on_clip_changed(self, value):
        self.clip_label.setText(f"Clip limit: {value / 10.0:.1f}")

    def on_tile_changed(self, value):
        self.tile_label.setText(f"Tile size: {value}×{value}")

    def on_percentile_changed(self, *_):
        """Keep lower < upper and refresh the percentile labels."""
        lower = self.lower_slider.value()
        upper = self.upper_slider.value()
        if lower >= upper:
            if self.sender() is self.lower_slider:
                self.lower_slider.setValue(upper - 1)
            else:
                self.upper_slider.setValue(lower + 1)
        self.lower_label.setText(f"Black point: {self.lower_slider.value()}%")
        self.upper_label.setText(f"White point: {self.upper_slider.value()}%")

    def _show_enhanced(self) -> None:
        """Turn the enhancement on and leave "Show original" without a second re-render."""
        self._enabled = True
        if self.original_check.isChecked():
            self.original_check.blockSignals(True)
            self.original_check.setChecked(False)
            self.original_check.blockSignals(False)

    def _on_control_changed(self, *_):
        if self._updating:
            return
        self._show_enhanced()
        self._preview_timer.start()  # restarts: one render once the slider rests

    def _on_apply(self):
        self._preview_timer.stop()
        self._show_enhanced()
        self.enhancement_changed.emit()

    def _on_original_toggled(self, _checked: bool):
        self._preview_timer.stop()
        self.enhancement_changed.emit()

    def _flash_save_result(self, ok: bool) -> None:
        self.save_defaults_button.setText("Saved" if ok else "Could not save")
        self._save_feedback_timer.start()
