"""The class-label goldens of the Trace GT editor (plan WG-T0): the file itself.

``tests/data/gt_goldens.json`` holds what ``GtTracingInterface`` did for the scripted sessions
of ``tests/gt_goldens_support.GT_OPS``: the class-id plane after every op, the class counts,
the ``reviewed`` ticks, and the saved ``gt/<stem>.png`` (pixels, palette, chunk). They were
captured once, before any GT code changed (``b8bb6e4``), and are frozen: the old editor was
deleted in WG-T11, so nothing can capture them again. The Correct page and its
``ClassesLayer`` reproduce them through the same ``replay``
(``tests/test_correct_classes_goldens.py``); this file pins what the goldens hold.
"""

from __future__ import annotations

import json

from tests.gt_goldens_support import GT_CASES, GT_OPS, load_goldens


def test_the_golden_file_has_the_cases_and_pins_ids_ticks_and_the_file():
    data = load_goldens()
    assert set(data) == {"meta", "ops", "cases"} and set(data["cases"]) == set(GT_CASES)
    assert data["ops"] == json.loads(json.dumps(GT_OPS))
    assert data["meta"]["deterministic_across_two_runs"] is True
    case = data["cases"]["two_classes_crossing"]
    assert set(case) >= {
        "per_op_ids_sha256",
        "per_op_reviewed",
        "per_op_dirty",
        "ids_sha256",
        "counts",
        "reviewed",
        "dirty_before_save",
        "dirty_after_save",
        "save_returned",
        "warnings",
        "file",
    }
    assert len(case["per_op_ids_sha256"]) == len(GT_OPS["two_classes_crossing"])
    assert case["reviewed"] == ["nodule", "root", "root_tip"] and case["file"]["mode"] == "P"
    assert case["file"]["transparency"] == 0
    # root painted, nodule painted, root_tip ticked only
    assert case["counts"]["2"] > 0 and case["counts"]["1"] > case["counts"].get("3", 0) == 0
    empty = data["cases"]["empty_save"]
    assert empty["file"]["exists"] is True and empty["counts"] == {"0": 640 * 480}
    lossy = data["cases"]["lossy_remap_on_load"]
    assert lossy["warnings"] == 1 and lossy["dirty_before_save"] is True
    fill = data["cases"]["fill_active_class_only"]
    per_op = fill["per_op_ids_sha256"]
    ops = GT_OPS["fill_active_class_only"]
    misses = [i for i, op in enumerate(ops) if op[0] == "click" and per_op[i] == per_op[i - 1]]
    fills = [i for i, op in enumerate(ops) if op[0] == "click" and per_op[i] != per_op[i - 1]]
    assert len(misses) == 4 and len(fills) == 1  # only the fill inside the active class's outline
    assert fill["reviewed"] == ["nodule", "root"] and set(fill["counts"]) == {"0", "1", "2"}
    assert fill["counts"]["1"] > fill["counts"]["2"] > 0  # the root fill overwrote half the nodule
    for name, record in data["cases"].items():  # one entry per op; Save leaves nothing unsaved
        assert len(record["per_op_dirty"]) == len(record["per_op_reviewed"]) == len(GT_OPS[name])
        assert record["save_returned"] is True and record["dirty_after_save"] is False, name
    history = data["cases"]["dirty_and_history"]
    dirty = history["per_op_dirty"]
    saves = [i for i, op in enumerate(GT_OPS["dirty_and_history"]) if op[0] == "save"]
    assert [dirty[i] for i in saves] == [False, False] and history["dirty_before_save"] is True
    # tick (4) and its takeback (5); the locked box of a painted class (6) is no change
    assert (dirty[4], dirty[5], dirty[6]) == (True, False, False)
    # stroke (9), undo (10), redo (11), undo (12), undo past the save (13), redo (14), redo (15)
    assert dirty[9:16] == [True, False, True, False, True, False, True]
    # a saved tick unticked (18) and ticked again (19); Clear (20) and its Undo (21)
    assert dirty[18:22] == [True, False, True, False]
    assert history["per_op_reviewed"][6] == ["root"]  # the painted class stays checked
    ignore = data["cases"]["ignore_interplay"]
    assert set(ignore["counts"]) == {"0", "1", "255"} and ignore["reviewed"] == ["root"]
    assert ignore["file"]["classes"]["255"] == "ignore"
