"""Enhance popover content (:class:`NormalizationControls`).

The math, settings validation, decoded-source cache and saved-defaults store live in the
Qt-free :mod:`app.enhancement`; this module is the thin Qt adapter the editors use:

* ``controls.enhancement_changed`` - re-render the image (debounced while sliders move);
* ``controls.is_active()`` / ``controls.apply_to(bgr)``;
* ``controls.source_image(path)`` - the one cached, read-only decode of the photo;
  ``controls.display_image(path)`` - that decode as shown (enhanced when active);
  ``controls.display_pixmap(path)`` - the same as a ``QPixmap``;
* ``controls.settings()`` / ``controls.set_settings(settings)``;
* ``controls.set_source_path(path)`` - names the photo on screen: the "Smooth JPEG blocks"
  box (``deblock_check``) is offered, and applied, only for a ``.jpg`` / ``.jpeg`` source. It
  works without the enhancement being on.

Enhancement is display-only: it never changes masks, skeletons or measurements.
"""

from __future__ import annotations

import logging
from dataclasses import replace

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
    enhance,
    is_jpeg_path,
)

from .widgets.buttons import ghost_button, primary_button

logger = logging.getLogger(__name__)

__all__ = ["NormalizationControls", "bgr_to_qpixmap"]

PREVIEW_DELAY_MS = 80
_SAVED_FEEDBACK_MS = 1500


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
    "Smooth JPEG blocks" is a separate switch: it needs a JPEG source (:meth:`set_source_path`)
    and does not turn the enhancement on.
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
        self._jpeg_source = False
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
            deblock=self.deblock_check.isChecked(),
        )

    def set_settings(self, settings: EnhanceSettings) -> None:
        """Show ``settings`` (re-renders when the enhancement is on, or the JPEG box took effect)."""
        deblock_before = self._deblock_effective()
        self._updating = True
        try:
            self.method_combo.setCurrentText(settings.method)
            self.clip_slider.setValue(settings.clip)
            self.tile_slider.setValue(settings.tile)
            self.lower_slider.setValue(settings.lower)
            self.upper_slider.setValue(settings.upper)
            self.deblock_check.setChecked(settings.deblock)
        finally:
            self._updating = False
        self.on_method_changed(settings.method)
        if self._enabled or self._deblock_effective() != deblock_before:
            self._preview_timer.start()

    def set_source_path(self, path: str | None) -> None:
        """Name the photo on screen: "Smooth JPEG blocks" is offered only for a JPEG file."""
        self._jpeg_source = is_jpeg_path(path)
        self.deblock_check.setVisible(self._jpeg_source)

    def _deblock_effective(self) -> bool:
        return self.deblock_check.isChecked() and self._jpeg_source

    def is_active(self) -> bool:
        """True when the displayed image should be enhanced or its JPEG blocks smoothed."""
        return (self._enabled or self._deblock_effective()) and not self.original_check.isChecked()

    def apply_to(self, image: np.ndarray) -> np.ndarray:
        """``image`` enhanced when active, else ``image`` itself (never modified)."""
        if not self.is_active():
            return image
        settings = replace(self.settings(), deblock=self._deblock_effective())
        return enhance(image, settings, tone=self._enabled)

    def source_image(self, path: str) -> np.ndarray | None:
        """The decoded BGR image at ``path`` (None if unreadable): the editor's one decode.

        Cached and read-only: repeat calls return the same array until the file changes. The
        geometry is Qt's (EXIF orientation ignored, :data:`app.enhancement.DECODE_FLAGS`).
        """
        return self._sources.get(path)

    def display_image(self, path: str) -> np.ndarray | None:
        """What the editor should show for ``path`` as a BGR array (None if unreadable).

        The cached :meth:`source_image` itself while the enhancement is off, else a new
        enhanced array; the source is never modified. Names ``path`` as the source first
        (:meth:`set_source_path`).
        """
        self.set_source_path(path)
        source = self.source_image(path)
        return None if source is None else self.apply_to(source)

    def display_pixmap(self, path: str) -> QPixmap | None:
        """What the editor should show for the image at ``path`` (None if unreadable).

        The decoded file is cached, so live previews never re-read it.
        """
        image = self.display_image(path)
        return None if image is None else bgr_to_qpixmap(image)

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
        """Build the controls (method, sliders, Apply / Reset, Show original, Save as default)."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.deblock_check = QCheckBox("Smooth JPEG blocks")
        self.deblock_check.setToolTip(
            "Soften the 8 x 8 blocks of a JPEG photo when zoomed in.\n"
            "Display only: nothing saved changes."
        )
        self.deblock_check.setVisible(False)  # offered once a JPEG source is named
        layout.addWidget(self.deblock_check)

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
        self.deblock_check.toggled.connect(self._on_deblock_toggled)
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
        """Show the CLAHE clip limit of slider ``value`` (tenths)."""
        self.clip_label.setText(f"Clip limit: {value / 10.0:.1f}")

    def on_tile_changed(self, value):
        """Show the CLAHE tile grid of slider ``value``."""
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

    def _show_enhanced(self, *, tone: bool = True) -> None:
        """Leave "Show original" without a second re-render, and turn the enhancement on
        unless ``tone`` is false (the JPEG switch works without it)."""
        if tone:
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

    def _on_deblock_toggled(self, _checked: bool):
        if self._updating:
            return
        self._show_enhanced(tone=False)
        self._preview_timer.start()

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
