"""R7: Measure says so when it measures an older mask folder instead of ``mask/``.

In a project that still has the older ``output/mask`` folder, Measure (area) reads
that folder first, as before (``ProjectLayout.mask_dir_candidates``), while Generate
and Correct save masks to ``mask/``: new or corrected masks were silently left out of
``root_areas.csv``, and the only trace was a log line. The folder measured does not
change (same CSV); the user now gets a warning naming both folders.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

from tests.test_measure_handler import _handler, _project, _wait_idle


def test_measuring_an_older_mask_folder_next_to_mask_warns(tmp_path, qapp):
    _project(tmp_path, skeletons=False)  # mask/: where Generate and Correct save
    legacy = tmp_path / "output" / "mask"
    legacy.mkdir(parents=True)
    cv2.imwrite(str(legacy / "x_T1_L2_2024.01.02_010200_001.png"), np.zeros((64, 80), np.uint8))
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    warnings = [c.args[0] for c in mw.feedback.calls if c.name == "notify" and c.args[1] == "warn"]
    assert len(warnings) == 1
    message = warnings[0]
    assert os.path.join("output", "mask") in message  # the folder measured
    assert "not measured" in message  # and that the masks in mask/ are left out
    assert os.path.isfile(legacy / "root_areas.csv")  # the same folder is measured as before
    assert not os.path.exists(tmp_path / "mask" / "root_areas.csv")


def test_a_project_with_only_mask_gets_no_warning(tmp_path, qapp):
    _project(tmp_path, skeletons=False)
    handler, mw = _handler(tmp_path, qapp)

    handler.calculate_root_area()
    _wait_idle(handler, qapp)

    assert [c for c in mw.feedback.calls if c.name == "notify" and c.args[1] == "warn"] == []
