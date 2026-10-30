"""An independent reference of the corner-count length estimator (O12, W3-T8).

Vossepoel & Smeulders (1982), CGIP 20:347-364: ``L = 0.980 n_e + 1.406 n_o - 0.091 n_c`` on
the m-adjacent (Gonzalez & Woods) graph of an 8-connected skeleton, with the per-axis pixel
pitch ``(mx, my)``. Written as plain Python loops over the set pixels, sharing no code with
:mod:`app.inference.metrics` (no LUT, no ``filter2D``, no array shifts), so a test can compare
the two exactly. The final expression is the one of ``metrics.root_length_mm``, in the same
order, so equal counts give the same float.
"""

from __future__ import annotations

import math

import numpy as np

ORTHOGONAL = 0.980
DIAGONAL = 1.406
CORNER = 0.091

#: The 8 directions as (row, column) offsets.
_DIRECTIONS = ((0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1))


def _m_neighbours(pixels: set[tuple[int, int]], r: int, c: int) -> list[tuple[int, int]]:
    """The directions of the m-adjacent neighbours of the set pixel ``(r, c)``: its set
    4-neighbours, and each set diagonal neighbour that shares no set 4-neighbour with it."""
    found = []
    for dr, dc in _DIRECTIONS:
        if (r + dr, c + dc) not in pixels:
            continue
        if dr and dc and ((r + dr, c) in pixels or (r, c + dc) in pixels):
            continue  # a diagonal with a shared 4-neighbour: reached through that pixel
        found.append((dr, dc))
    return found


def vs_counts(skeleton: np.ndarray) -> tuple[int, int, int, int]:
    """``(n_h, n_v, n_o, n_c)`` of the skeleton's non-zero pixels."""
    pixels = {(int(r), int(c)) for r, c in np.argwhere(np.asarray(skeleton) > 0)}
    n_h = n_v = n_o = n_c = 0
    for r, c in pixels:
        if (r, c + 1) in pixels:
            n_h += 1
        if (r + 1, c) in pixels:
            n_v += 1
        # Diagonal pairs, each counted once from its upper pixel.
        for dc in (1, -1):
            if (r + 1, c + dc) in pixels and (r, c + dc) not in pixels and (r + 1, c) not in pixels:
                n_o += 1
        steps = _m_neighbours(pixels, r, c)
        if len(steps) == 2:
            (r1, c1), (r2, c2) = steps
            if (r1 + r2, c1 + c2) != (0, 0):  # not on opposite sides: the chain turns here
                n_c += 1
    return n_h, n_v, n_o, n_c


def vs_length_mm(skeleton: np.ndarray, mx: float, my: float) -> float:
    """The corner-count length in mm of ``skeleton`` with pixel pitch ``mx`` x ``my`` mm."""
    n_h, n_v, n_o, n_c = vs_counts(skeleton)
    return (
        ORTHOGONAL * (n_h * mx + n_v * my)
        + DIAGONAL / math.sqrt(2.0) * n_o * math.hypot(mx, my)
        - CORNER * n_c * math.sqrt(mx * my)
    )
