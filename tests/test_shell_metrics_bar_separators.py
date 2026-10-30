"""The metrics bar draws separators only between cells it shows (H6).

Before: each cell's left border was fixed when it was built (only the name cell had
none), so with no image selected the bar read "| FOV 18×13 mm": a stray separator
before its first visible cell.
"""

from __future__ import annotations

from app.gui.metrics_bar import MetricsBar


def _separated(bar: MetricsBar) -> list[str]:
    """Names of the shown cells, each with ``|`` in front when it draws a separator."""
    cells = [
        ("name", bar._name_cell),
        ("length", bar._length_cell),
        ("area", bar._area_cell),
        ("fov", bar._fov_cell),
        ("status", bar._status_cell),
    ]
    return [
        ("|" if "border-left: 1px" in cell.styleSheet() else "") + name
        for name, cell in cells
        if not cell.isHidden()
    ]


def test_without_a_selection_the_first_cell_has_no_separator(app):
    bar = MetricsBar()
    bar.set_metrics()
    assert _separated(bar) == ["fov"]
    bar.deleteLater()


def test_separators_sit_between_the_shown_cells(app):
    bar = MetricsBar()
    bar.set_metrics(length=1.5, area=2.5, status="Mask ready", name="img")
    assert _separated(bar) == ["name", "|length", "|area", "|fov", "|status"]
    bar.set_metrics(status="No mask", name="img")
    assert _separated(bar) == ["name", "|fov", "|status"]
    bar.set_metrics()
    assert _separated(bar) == ["fov"]
    bar.deleteLater()
