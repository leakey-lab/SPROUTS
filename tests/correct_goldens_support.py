"""Pixel-golden support for the Correct editor tests (Qt-free at import).

``tests/data/correct_goldens.json`` holds pixel goldens captured from the pre-merge
editors (``MaskTracingInterface`` and ``SkeletonCorrectionInterface``) by the contracts
scout's ``capture_goldens.py``, reduced to the two cases whose inputs
:func:`synth_inputs` redraws exactly (cv2 primitives, PNG, no RNG, no dataset):

- ``synth_model_mask_and_skeleton``: a model mask (8-bit grey PNG, colour type 0, as the
  mask generator writes it) and a 341x256 generated skeleton;
- ``synth_blank_png``: the photo only.

Per case the JSON records, for each op script (``ops``: ``mask``, ``mask_clear``,
``skeleton``, ``skeleton_clear``), the ``saved`` file (the contract: pixel sha256,
shape, PNG colour type, the value Measure would write) and the in-memory plane or
raster after every op (``per_op_plane_sha256`` / ``per_op_raster_sha256``: diagnostics
that name the first diverging op); skeleton records also hold the ``start`` state.
``mask_clear`` and ``skeleton_clear`` run on a fresh copy of the project.

Op semantics (every driver; positions are image pixels of the 640x480 photo):

- ``["brush", n]`` / ``["eraser", n]``: select that mask tool and set the size to ``n``;
- ``["fill"]``: select the mask Fill tool;
- ``["stroke", pts]``: press at the first point, a dragging move per further point,
  release (at the last point);
- ``["click", p]``: press + release at ``p``;
- ``["tool", t]``: select skeleton tool ``t`` (``select``, ``eraser``, ``polyline``,
  ``connect``);
- ``["smooth", b]``: set the polyline Smooth toggle to ``b``;
- ``["eraser_size", n]``: set the skeleton eraser diameter;
- ``["drag", pts]``: like ``stroke``, for the skeleton tools;
- ``["connect_endpoints", i, j]``: drag from endpoint ``i`` to endpoint ``j`` of
  ``sorted(model.endpoints())`` (skipped when there are too few endpoints);
- ``["double_click", p]``: a double-click at ``p``;
- ``["key", "Delete"]``: the Delete key;
- ``["finish"]``: finish the polyline;
- ``["clear"]``: Clear the layer;
- ``["undo"]``: pop a polyline draft point if there is one, else undo one action;
- ``["redo"]``: redo one action;
- ``["save"]``: save the layer.

Import it as ``from tests.correct_goldens_support import ...``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil

import cv2
import numpy as np

from app.io_utils import imread_unicode, imwrite_unicode

__all__ = [
    "GOLDENS",
    "SYNTH_CASES",
    "SYNTH_STEM",
    "describe_mask_file",
    "describe_skeleton_file",
    "file_sha",
    "load_goldens",
    "make_synth_project",
    "png_colour_type",
    "sha",
    "synth_inputs",
]

#: The committed golden data (``json.dump(..., indent=1, sort_keys=True)``).
GOLDENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "correct_goldens.json")

#: Stem of the synthetic image (a valid SPROUTS name, so Measure can parse it).
SYNTH_STEM = "SYNTH_T001_L001_2024.01.01_000000_001_SMM"

#: Committable cases -> (with a model mask, with a generated skeleton).
SYNTH_CASES: dict[str, tuple[bool, bool]] = {
    "synth_model_mask_and_skeleton": (True, True),
    "synth_blank_png": (False, False),
}


def load_goldens() -> dict:
    """The committed goldens: ``{"meta": ..., "ops": ..., "cases": ...}`` (a fresh copy)."""
    with open(GOLDENS, encoding="utf-8") as fh:
        return json.load(fh)


def synth_inputs(folder: str, with_mask: bool, with_skel: bool) -> str:
    """Draw the deterministic inputs into ``folder``; returns the photo's path.

    photo: 640x480 BGR gradient PNG; model mask: 8-bit grey PNG (colour type 0, what
    the generator writes) with two filled ellipses; generated skeleton: 341x256
    3-channel PNG with grey (191) 1-3 px lines, like the Pix2Pix output. The bytes
    equal the scout's ``cv2.imwrite`` output; the unicode-safe writer keeps them.
    """
    os.makedirs(folder, exist_ok=True)
    yy, xx = np.mgrid[0:480, 0:640]
    photo = np.dstack([(xx * 255 // 639), (yy * 255 // 479), ((xx + yy) * 255 // 1118)]).astype(
        np.uint8
    )
    image_path = os.path.join(folder, f"{SYNTH_STEM}.png")
    imwrite_unicode(image_path, photo, durable=False)
    if with_mask:
        mask = np.zeros((480, 640), np.uint8)
        cv2.ellipse(mask, (200, 240), (120, 40), 30, 0, 360, 255, -1)
        cv2.ellipse(mask, (470, 150), (60, 90), -15, 0, 360, 255, -1)
        os.makedirs(os.path.join(folder, "mask"), exist_ok=True)
        imwrite_unicode(os.path.join(folder, "mask", f"{SYNTH_STEM}.png"), mask, durable=False)
    if with_skel:
        skel = np.zeros((256, 341, 3), np.uint8)
        pts = np.array([[20, 30], [120, 90], [200, 80], [320, 200]], np.int32)
        cv2.polylines(skel, [pts.reshape(-1, 1, 2)], False, (191, 191, 191), 2)
        cv2.line(skel, (60, 240), (300, 20), (191, 191, 191), 1)
        cv2.line(skel, (150, 150), (150, 250), (191, 191, 191), 3)
        os.makedirs(os.path.join(folder, "skeletons"), exist_ok=True)
        imwrite_unicode(
            os.path.join(folder, "skeletons", f"{SYNTH_STEM}_fake.png"), skel, durable=False
        )
    return image_path


def make_synth_project(root: str, case_name: str) -> tuple[str, str]:
    """A fresh project folder ``<root>/<case_name>`` holding that case's inputs.

    An existing folder of that name is removed first, so a replay never sees files an
    earlier replay saved. Returns ``(folder, image path)``; an unknown case raises
    ``KeyError``.
    """
    with_mask, with_skel = SYNTH_CASES[case_name]
    folder = os.path.join(root, case_name)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    return folder, synth_inputs(folder, with_mask, with_skel)


def sha(arr: np.ndarray) -> str:
    """sha256 over dtype, shape and C-order bytes (unambiguous array identity)."""
    arr = np.ascontiguousarray(arr)
    h = hashlib.sha256()
    h.update(f"{arr.dtype.str}|{arr.shape}|".encode())
    h.update(arr.tobytes())
    return h.hexdigest()


def file_sha(path: str) -> str:
    """sha256 of the file's bytes (informational: encoders may change the bytes)."""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def png_colour_type(path: str) -> int | None:
    """PNG colour type from the IHDR chunk (0 grey, 2 RGB, 4 grey+alpha, 6 RGBA); else None."""
    with open(path, "rb") as fh:
        head = fh.read(26)
    return head[25] if len(head) == 26 and head[12:16] == b"IHDR" else None


def _metric(kind: str, path: str) -> float | None:
    """The value the Measure stage would write for this file (root_areas / root_lengths)."""
    from app.inference import metrics

    stem = os.path.splitext(os.path.basename(path))[0]
    row = (metrics.area_row if kind == "area" else metrics.length_row)(stem, path)
    return row.get(metrics.AREA_VALUE_COLUMN if kind == "area" else metrics.LENGTH_VALUE_COLUMN)


def describe_mask_file(path: str) -> dict:
    """What a golden records for a saved mask (compare with ``cases[...]["mask"]["saved"]``)."""
    if not os.path.exists(path):
        return {"exists": False}
    rgba = imread_unicode(path, cv2.IMREAD_UNCHANGED)
    return {
        "area_mm2_as_measured": _metric("area", path),
        "exists": True,
        "shape": list(rgba.shape),
        "dtype": str(rgba.dtype),
        "colour_type": png_colour_type(path),
        "pixels_sha256": sha(rgba),
        "on_sha256": sha(rgba[:, :, 3] == 255) if rgba.ndim == 3 else None,
        "on_count": int((rgba[:, :, 3] == 255).sum()) if rgba.ndim == 3 else None,
        "file_sha256_informational": file_sha(path),
    }


def describe_skeleton_file(path: str) -> dict:
    """What a golden records for a saved skeleton (``cases[...]["skeleton"]["saved"]``)."""
    if not os.path.exists(path):
        return {"exists": False}
    img = imread_unicode(path, cv2.IMREAD_UNCHANGED)
    return {
        # FR-03: corrections are NOT measured today; recorded in case the owner flips it.
        "length_mm_if_measured_informational": _metric("length", path),
        "exists": True,
        "shape": list(img.shape),
        "dtype": str(img.dtype),
        "colour_type": png_colour_type(path),
        "pixels_sha256": sha(img),
        "on_count": int((img > 0).sum()),
        "values": sorted(int(v) for v in np.unique(img)),
        "file_sha256_informational": file_sha(path),
    }
