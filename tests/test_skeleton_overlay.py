"""Skeleton overlay helpers that outlive the old skeleton editor.

``overlay_indices`` (the line plus its one-pixel halo, UXE-13) feeds the skeleton layer
item, and ``SkeletonDecorations`` draws the endpoint markers of the Correct workspace.
Copied from ``tests/test_skeleton_editor_tools.py``, which goes with that editor.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_overlay_halo_surrounds_the_line_only():
    from app.gui.skeleton_overlay import HALO_INDEX, LINE_INDEX, overlay_indices

    mask = np.zeros((9, 9), np.uint8)
    mask[4, 2:7] = 255
    idx = overlay_indices(mask)
    assert (idx == LINE_INDEX).sum() == 5 and np.array_equal(idx == LINE_INDEX, mask > 0)
    halo = idx == HALO_INDEX
    ring = cv2.dilate(mask, np.ones((3, 3), np.uint8)) > 0
    assert np.array_equal(halo, ring & (mask == 0))
    assert mask[4, 2] == 255  # input untouched


def test_touching_endpoint_markers_stay_filled(app):
    from PyQt6.QtWidgets import QGraphicsScene

    from app.gui.skeleton_overlay import SkeletonDecorations

    deco = SkeletonDecorations(QGraphicsScene())
    deco.set_endpoints([(10, 10), (12, 10)])
    assert deco.endpoints_item.path().contains(QPointF(11, 10))
