# Wave 3 — Science Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Read `00-master.md` sections 2, 5, 6 and 7
> first, then the shared science procedure (section S below), then your task.

**Goal:** Build the owner's science answers: a per-project field of view with 18 x 13.5 mm
as the default (O3), the corner-count length estimator (O12), fixed-shape GPU batches (O4),
predictions of another size shown stretched (O8), a 1 px brush click that paints its pixel
(O11) and file names only in `Error` cells (O38). Every change that moves a number lands in
its own commit with a measured before / after entry in `CHANGELOG.md`.

**Architecture:** One new Qt-free module, `app/calibration.py`, owns everything that turns
pixels into millimetres apart from the arithmetic: the project file `sprouts_project.json`,
the record written into each Measure sidecar, the length-method name, and the check that
marks a CSV measured with another field of view or method. `app/inference/metrics.py` keeps
the arithmetic and its public signatures (new parameters keyword-only). The batch policy
lives in `app/inference/runtime.py` and one new branch of `run_batches`; `auto` is today's
code path, untouched. W3-T4 and W3-T5 are local editor changes.

**Tech Stack:** Python 3.10, NumPy, OpenCV, scikit-image (Zhang thinning), PyTorch (W3-T3),
PyQt6 (W3-T4, W3-T5 and the display parts of W3-T2), pytest, ruff.

**Spec:** `00-master.md` (this folder: O3, O4, O8, O11, O12, O38 as built; section 4 Wave 3
rows; lanes and ownership) + `docs/superpowers/plans/2026-10-12-known-issues-resolution.md`
(section 3 Wave 3, section 2 owner answers) + the research memos
`.claude/handoff/owner-decisions-2026-10-25/research/o3-o12-calibration-length.md` (done) and
`.claude/handoff/owner-decisions-2026-10-25/research/o4-gpu-batch.md` (running when this
plan was written: Exp 1 "fixed-shape premise HOLDS", report not yet written) + audit reports
`.claude/handoff/issue-audit-2026-10-12/` (`mask-calibration.md` M1 part B, M2; `length.md`
X1; `correct-ux.md` E4; `reproducibility.md` R1). Interfaces consumed from
`w2-records-wording.md` (W2-T2, W2-T3, W2-T4, W2-T6, W2-T11) and `w1-data-loss.md` (W1-T2).
`wg-gt-unified-editor.md` did not exist when this plan was written: W3-T4 is written against
today's `MaskDocument.read` with a re-check step.

---

## Global Constraints

See `00-master.md` section 2 (venv python only, offscreen pytest, ruff on own files only, no
git by implementers, scope = listed files, byte-identical CSVs outside Sci tasks, goldens'
pixels identical, Qt-free modules, `MainWindow` pin, O21 portability). Shorthands below:
`PYTEST <files>` = `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest <files> -v`;
`RUFF <files>` = `./venv/Scripts/python.exe -m ruff check --fix <files>` then `... -m ruff format <files>`.
Wave 3 adds:

- `app/inference/metrics.py`: `root_length_mm(skeleton)`, `root_area_mm2(mask)`,
  `length_row(name, path)`, `area_row(name, path)`, `calculate_root_length`,
  `calculate_root_area` keep working when called exactly so. New parameters are
  **keyword-only** with a default of `None` (= `app.config.CALIBRATION`, looked up at call
  time). This overrides the memo's sketch, which passes `calibration` positionally; the
  memo's tests are used with `calibration=CAL`.
- Every **Sci** commit's `CHANGELOG.md` entry (under `[Unreleased]` → `### Scientific output
  changes`, with the task ID) carries the before / after numbers of section S: the
  synthetic set and the audit's 3000-pair real sample. If a measured number differs from
  the memo's by more than 0.1 percentage points, stop and report; do not commit.
- `D:\2024 EF image data` is read only, through `sample_real.py` and files opened by exact
  path. Never list it whole, never print more than counts and 5 names.
- The lead tells the owner before W3-T2 commit 2, the W3-T3 default switch and W3-T8 land
  (the RootQuant-V2 benchmark session imports `metrics.py`; it should pin a commit first).
- New Qt-free module `app/calibration.py`: standard library + `app.config` only (no `cv2`,
  `numpy`, `torch`, `PyQt6`); `app.data_processing.measurement_index` and
  `app.gui.stage_summary` import it.

## Code facts (read 2026-10-25 at HEAD `41f371e`; W2-T2 edits to `metrics.py` /
`measure_run.py` were in the working tree)

| Question | Answer |
|---|---|
| Where calibration enters | `metrics.root_length_mm` / `root_area_mm2` read the module global `CALIBRATION` (`metrics.py:96-109` at HEAD). `provenance.build_record` records `calibration_info()` = `dataclasses.asdict(CALIBRATION)` in every record (`provenance.py:96-98, 376`), generation manifests included. |
| How Measure starts | `SkeletonHandler._measure` (`skeleton_handler.py:128-143`) builds `MeasureRun(spec, images, folder, project_root=layout.root)`; W2-T11 adds `without_image=`, WG-T8 adds GT-first inputs. The project root is `layout.root`. |
| Rows | `MetricSpec.row` is the module function `length_row` / `area_row`; after W2-T2 the run calls `spec.row(name, path, data=...)`. |
| Byte-identity tests | `tests/test_measure_core.py::_legacy_row` computes both values with `app.config.CALIBRATION`, so a default change keeps them green; the length formula is inlined (Freeman), so W3-T8 breaks them by design. Its corrupt-file row puts the full path in `Error` (`f"Could not read {what}: {path}"`). |
| Pins of 18 x 13 | `tests/test_foundation_core_config.py:164`, `tests/test_foundation_core_provenance.py:58`, `tests/test_measure_core.py:297`, `tests/test_regression.py:32`, `tests/test_metrics_bar_smoke.py:83`, `tests/test_shell_info_about.py:76`, `tests/test_viz_dash_export.py:86`, plus W2-T4's new tests (they write a 13.5 sidecar to mean "differs"). `tests/data/correct_goldens.json`: `area_mm2_as_measured` 14.52 / 38.09 (asserted), `length_mm_if_measured_informational` 28.86 / 60.83 (not asserted today; WG-T8 may assert them). |
| Batch sizes | `config.MASK_BATCH_SIZE = 16` (GPU), `MASK_BATCH_SIZE_CPU = 4`, `_SKELETON_BATCH_DEFAULT = 64`, `SKELETON_BATCH_SIZE_CPU = 8`; skeletons on GPU are VRAM-sized by `config.get_skeleton_batch_size(refresh=True)` (`torch.cuda.mem_get_info`). The handlers call `generate_masks` / `run_skeleton_generation` without `batch_size`. |
| Batch engine | `run_batches` (`batch_engine.py:281-409`, W2-T3 committed): cuts `batch_size` chunks (tail shorter), zero-fills unreadable slots, halves on OOM through `OomBackoff`, fills `batch_shapes` / `oom_events`. |
| Mask kinds on disk | Generate writes 8-bit grey PNGs (colour type 0); the editor writes white-on-transparent RGBA (colour type 6). Before Wave G a grey file in `mask/` is a prediction. |
| 1 px click | `PaintCanvas._draw` first-dab branch draws `drawEllipse` of a 1 x 1 rect without antialiasing: 0 px (`paint_canvas.py:248-252`). Golden scripts have no single-point size-1 stroke (audit E4: replayed, no golden changes). |

## Review Focus

1. **A project whose CSVs were measured before this wave** (18 x 13 mm, Freeman weights).
   Expected: after the switch the metrics bar marks LENGTH / AREA outdated with the reason,
   the Measure status line says "measured with another field of view or length method:
   measure again", the CSV values stay as they are until Measure runs, dashboards still
   open. Tests: W3-T2 `test_a_csv_measured_with_another_field_of_view_is_flagged`, W3-T8
   `test_a_length_csv_measured_with_the_old_estimator_is_flagged`.
2. **A hand-written `sprouts_project.json`** with a typo, numbers as strings, a UTF-8 BOM
   (Notepad), or `13,5`. Expected: a BOM is accepted; anything else invalid measures with
   the default, one warn toast names the file and the problem, the sidecar says
   `"source": "default"`. Test: W3-T2 `test_an_invalid_project_file_falls_back_and_says_why`.
3. **A GPU with too little free memory for 16 / 64 under `fixed`.** Expected: one retry
   after freeing the cache, then the run stops with the message; outputs already written
   stay; the run record says `failed` with that error; no batch is halved. Test: W3-T3
   `test_fixed_out_of_memory_retries_once_then_stops`, `test_a_fixed_stop_is_a_failed_run_record`.
4. **A 2048 x 1536 or 16:9 photo with its 640 x 480 prediction.** Expected: the mask shows
   stretched (nearest), clean; an untouched Save writes nothing; an edited Save writes at
   the photo's size; an editor-saved mask of another size stays "not shown". Tests: W3-T4.
5. **The RootQuant-V2 benchmark importing `root_area_mm2(mask)` / `root_length_mm(skeleton)`
   positionally.** Expected: both still work and use the app default. Test: W3-T2
   `test_metric_functions_keep_their_signatures`.

## Lane placement

```
L-measure : W2-T2 → W2-T11 → WX-T9 → W3-T7 → WG-T8 → W2-T4 → W2-T6 → W3-T2 (2 commits) → W3-T8
L-runrec  : W2-T3 (committed 41f371e) → W3-T3   (also after W1-T5 and the O4 memo's report)
L-misc    : … → W3-T5   (after WG-T0)
L-correct : Phase C: W3-T4 (after WG-T2; in lane order after WX-T7)
```

W3-T7 moved from the head of L-measure: W2-T2 started without it, and both edit
`metrics.py` and `tests/test_measure_core.py`.

---

## S. Shared science procedure (W3-T2 commit 2, W3-T3, W3-T8)

Scripts go in the session scratchpad (`<scratch>`), never in the repository.

- **S1 Real sample (once per wave).** If `<scratch>/real_sample/mask` does not hold 3000
  PNGs, run
  `./venv/Scripts/python.exe .claude/handoff/issue-audit-2026-10-12/scripts/length/sample_real.py "D:\2024 EF image data" <scratch>/real_sample 3000`
  (copies every 15th mask + skeleton pair; prints counts only). The sample has no photos.
- **S2 Synthetic set (once per wave).** `<scratch>/make_synthetic.py` (repo root on
  `sys.path`): `tests.test_measure_core._make_dataset(Path(<scratch>/synthetic/skeletons), "length")`
  and `(..., "mask"), "area")` — the six named images per metric the byte-identity test uses.
- **S3 `<scratch>/measure_folder.py <folder> <out>`:** for a folder with `mask/` and
  `skeletons/`, run `MeasureRun(LENGTH, ProjectLayout(folder).scan_skeletons(<folder>/skeletons), <out>/length, project_root=folder)`
  and the same for `AREA` with `scan_masks(<folder>/mask, extensions=(".png",))`;
  `compute()`, `write()`. It calls `MeasureRun` directly, not the handler: no orphan skip
  (W2-T11) and no GT resolver (WG-T8), because the sample has no photos.
- **S4 `<scratch>/csv_diff.py <before.csv> <after.csv> <value column>`:** prints rows,
  rows whose 2-dp value changed, zero ↔ non-zero flips, totals before / after and the total
  change in %, and over rows with a before value above 1 (mm or mm²): median, 5th and 95th
  percentile, min and max of `after / before - 1` in %. Counts only, at most 5 names.
- **Procedure:** before editing any code of the task, run S3 on `<scratch>/real_sample`
  and `<scratch>/synthetic` into `<scratch>/<task>/before/`; after the change into
  `.../after/`; run S4 on each CSV pair. Those numbers go into the CHANGELOG entry.

---

### Task W3-T7: File names, not absolute paths, in `Error` cells (X3, O38)

Impl sonnet / low · Rev sonnet / low · Lane L-measure · **Sci: the `Error` text of failed
rows** · After WX-T9.

**Files:**
- Modify: `app/inference/metrics.py` — the two `raise FileNotFoundError(...)` in
  `preprocess_image` / `load_mask` (`:82`, `:94` in the W2-T2 tree) and the `except` block of
  `_metric_row` (`"Error": str(e)`, `:190`)
- Modify: `tests/test_measure_core.py::_legacy_row` (error text), `tests/test_error_column.py`
- Modify: `docs/DATA_DICTIONARY.md:81` (`Error` row), `CHANGELOG.md`

**Interfaces:**
- Produces: `metrics._error_text(exc: BaseException, path: str) -> str` — `str(exc)` with
  every occurrence of `path`, `os.path.abspath(path)`, `os.path.normpath(path)` and each of
  their `repr(...)[1:-1]` forms (doubled backslashes, as `OSError` prints them) replaced by
  `os.path.basename(path)`, longest first. The raise texts become
  `f"Could not read image: {os.path.basename(image_path)}"` / `"Could not read mask: ..."`.
  The log line keeps the full name (the log is local).

- [ ] **Step 1: Write the failing tests** in `tests/test_error_column.py`:
  - `test_an_unreadable_file_names_only_the_file`: `tmp_path / "deep" / "dir" / "x_T1_L1_fake.png"`
    with bytes `b"not a png"` → `metrics.length_row("x", p)["Error"] == "Could not read image: x_T1_L1_fake.png"`;
    `metrics.area_row(...)["Error"] == "Could not read mask: x_T1_L1_fake.png"`.
  - `test_an_os_error_loses_its_folder`: monkeypatch `metrics.preprocess_image` to raise
    `FileNotFoundError(2, "No such file or directory", str(p))` → the `Error` cell contains
    `"x_T1_L1_fake.png"` and neither `str(tmp_path)` nor `repr(str(tmp_path))[1:-1]`.
- [ ] **Step 2: Run, expect FAIL** — `PYTEST tests/test_error_column.py` (the cell holds the full path).
- [ ] **Step 3: Implement** the Produces block. In `tests/test_measure_core.py::_legacy_row`
  change the reference text to `f"Could not read {what}: {os.path.basename(path)}"` with the
  comment `# O38 (W3-T7): the file name only`.
- [ ] **Step 4: Run, expect PASS** — `PYTEST tests/test_error_column.py tests/test_measure_core.py tests/test_measure_handler.py tests/test_robust_saved_notice.py tests/test_measure_inputs_record.py`
  (both byte-identity tests pass with the updated reference).
- [ ] **Step 5: Docs.** `DATA_DICTIONARY.md:81`: "...the reason the image could not be
  measured, naming the file (never its folder)". CHANGELOG `### Scientific output changes`:
  "The `Error` cell of a row that could not be measured names the file only, not its
  absolute path, so a shared CSV no longer carries user or folder names (O38, W3-T7).
  Values and every other cell are unchanged."
- [ ] **Step 6: Ruff** — `RUFF app/inference/metrics.py tests/test_error_column.py tests/test_measure_core.py`.
- [ ] **Step 7: Commit (lead)** — `git add` the Files list;
  `fix(measure): the Error cell names the file, not its absolute path (W3-T7, O38)`

---

### Task W3-T5: A 1 px click paints / erases exactly its pixel (E4, O11)

Impl sonnet / medium · Rev sonnet / medium · Lane L-misc · **Sci: +1 px per size-1 click
(0.00076 mm² at 640 x 480 under 18 x 13)** · After WG-T0 (the GT goldens must exist and stay
green).

**Files:**
- Modify: `app/gui/paint_canvas.py:223-261` (`_draw`, first-dab branch) and its module
  docstring line 6-7 ("round caps" sentence: add "a 1 px dab is exactly its pixel")
- Modify: `tests/test_canvas_zoom_quality.py:328` (comment "a 1 px click paints nothing, as
  before" → "size 2, the default of this test")
- Test: Modify `tests/test_paint_canvas.py`, `tests/test_correct_mask_layer.py`, and the GT
  editor test file: `tests/test_gt_editor_ux.py` while Trace GT exists; if WG-T5 has landed,
  the GT classes layer's test file that WG-T5 names
- Modify: `CHANGELOG.md`

**Interfaces:** none new. `PaintCanvas.begin(pos, color, size, erase=)` unchanged.

- [ ] **Step 1a: Capture today's dabs.** Before editing `paint_canvas.py`, run a scratch
  snippet using `tests.test_paint_canvas.make_canvas` / `stroke`: for `size` in 2..9, paint
  on `np.zeros((40, 40))` with `stroke(canvas, (19, 17), size=size)`, and erase on
  `np.ones((40, 40))` with `erase=True`; print `hashlib.sha256(plane.tobytes()).hexdigest()[:16]`
  for each (16 values). Paste them as `DAB_SHA = {(size, erase): "..."}` in the test.
- [ ] **Step 1b: Write the failing tests.** `tests/test_paint_canvas.py`:
  - `test_a_one_pixel_click_paints_exactly_its_pixel`: `make_canvas(np.zeros((20, 30)))`,
    `patch = stroke(canvas, (7, 5), size=1)` → `canvas.history.plane.sum() == 1`,
    `plane[5, 7] == 1`, `patch is not None`, `len(canvas.history.undo_stack) == 1`,
    `np.array_equal(shown(canvas), mask_lut()[plane])`.
  - `test_a_one_pixel_eraser_click_clears_exactly_its_pixel`: `np.ones((20, 30))`,
    `stroke(canvas, (7, 5), size=1, erase=True)` → `plane.sum() == 599` and `plane[5, 7] == 0`.
  - `test_dabs_of_sizes_2_to_9_are_unchanged` (parametrize size, erase): hash equals `DAB_SHA`.
  - `test_a_one_pixel_anti_diagonal_drag_gains_its_pressed_pixel`: `stroke(canvas, (3, 8), (8, 3), size=1)`
    → `plane[8, 3] == 1` (the pressed pixel, unpainted today, audit E4), and every pixel the
    same drag paints today is still 1 (capture that set in Step 1a as `ANTI_DIAGONAL_TODAY`;
    the audit lists it as one row below the pointer: (3,9), (4,8) … (8,4) as (x, y)).
  `tests/test_correct_mask_layer.py::test_a_one_pixel_click_paints_its_pixel_as_one_undo_step`:
  `open_layer(scene, tmp_path)`, `layer.set_size(1)`, `click(layer, (70, 50))` →
  `plane_of(layer).sum() == 1`, `plane_of(layer)[50, 70] == 1`,
  `len(layer.document.history.undo_stack) == 1`, `layer.document.is_dirty()`; then
  `layer.undo()` → `plane_of(layer).sum() == 0`.
  GT: `test_a_one_pixel_click_paints_its_class_pixel` with that file's fixtures (`made`,
  `_editor`): `editor.size_slider.setValue(1)`, `editor.begin_stroke(QPoint(30, 20))`,
  `editor.end_stroke()` → `np.count_nonzero(editor.edits.plane) == 1` and
  `editor.edits.plane[20, 30] == ` the active class id.
- [ ] **Step 2: Run, expect FAIL** — `PYTEST tests/test_paint_canvas.py tests/test_correct_mask_layer.py tests/test_gt_editor_ux.py`
  (size-1 tests: 0 px painted; the dab hashes pass).
- [ ] **Step 3: Implement** — in `_draw`'s first-dab branch: `if self._size <= 1:
  painter.fillRect(QRect(pos.x(), pos.y(), 1, 1), self._colour)` with the composition mode
  already set (Source paints, DestinationOut erases); else today's ellipse. The dirty box is
  unchanged. Segments (`self._last is not None`) are untouched.
- [ ] **Step 4: Run, expect PASS** — the Step 2 command plus
  `tests/test_correct_goldens.py tests/test_correct_mask_goldens.py tests/test_gt_goldens.py tests/test_canvas_zoom_quality.py tests/test_correct_editor.py`.
  `tests/data/correct_goldens.json` and `tests/data/gt_goldens.json` are not touched.
- [ ] **Step 5: CHANGELOG** `### Scientific output changes`: "A click with the 1 px brush
  paints (or erases) exactly the clicked pixel in Correct and in the GT classes; it painted
  nothing and still added an empty undo step. A 1 px stroke along an exact anti-diagonal
  also gains its pressed pixel. Sizes 2 and above, and every golden, are unchanged (O11,
  W3-T5)."
- [ ] **Step 6: Ruff** — `RUFF app/gui/paint_canvas.py` and the three test files.
- [ ] **Step 7: Commit (lead)** — `fix(paint): a click with the 1 px brush paints its pixel (W3-T5, O11)`

---

### Task W3-T3: Fixed-shape batches, `SPROUTS_BATCH_POLICY` (R1 step 2, O4)

Impl opus / high · Rev opus / high · Lane L-runrec · **Sci: generated outputs (default
switch)** · After W2-T3 (committed), W1-T5 (`mask_inference.py`, `skeleton_inference.py`
ownership), and the O4 memo's report. Not while WG-T7 has uncommitted edits in
`app/inference/mask_inference.py`.

**Files:**
- Modify: `app/inference/runtime.py` (after `CUDNN_BENCHMARK_ENV`, `:59`: the policy
  constants and two functions; module docstring "Public API" list)
- Modify: `app/inference/batch_engine.py` (`FixedBatchOutOfMemory`, `run_batches(..., fixed=)`,
  module docstring bullet "R1")
- Modify: `app/inference/mask_inference.py:111-195` (`default_mask_batch_size`,
  `generate_masks`)
- Modify: `app/inference/skeleton_inference.py:559-651` (`default_skeleton_batch_size`,
  `run_skeleton_generation`)
- Modify: `README.md:150` (one paragraph after `SPROUTS_CUDNN_BENCHMARK`),
  `docs/DATA_DICTIONARY.md:268` (generation `parameters`: `batch_policy`), `CHANGELOG.md`
- Test: Create `tests/test_generation_fixed_batches.py`; Modify the generation tests that
  pin `auto` behaviour (Step 5)
- `app/config.py` is **not** changed (L-measure owns it): the fixed sizes are named in
  `runtime.py` and pinned against `config` by a test.

**Interfaces:**
- Consumes: W2-T3's `BatchReport.batch_shapes`, `oom_events`; `OomBackoff` (auto only).
- Produces (`runtime.py`):
  - `BATCH_POLICY_ENV = "SPROUTS_BATCH_POLICY"`, `BATCH_POLICIES = ("fixed", "auto")`,
    `DEFAULT_BATCH_POLICY` (Step 0 decides `"fixed"` or `"auto"`).
  - `batch_policy() -> str` — the env value stripped and lower-cased when it is one of
    `BATCH_POLICIES`, else `DEFAULT_BATCH_POLICY` (an unknown value logs one warning).
    Read on every call (not cached in `RuntimeConfig`, which is `lru_cache`d).
  - `fixed_batch_size(model: str, device_type: str) -> int` — `("mask", "cuda")` → 16,
    `("skeleton", "cuda")` → 64, any other device → `config.MASK_BATCH_SIZE_CPU` (4) /
    `config.SKELETON_BATCH_SIZE_CPU` (8). Never queries the GPU.
- Produces (`batch_engine.py`):
  - `FIXED_OOM_MESSAGE = "Out of GPU memory at the fixed batch size ({n}). Close other programs that use the GPU and run again, or set SPROUTS_BATCH_POLICY=auto to let SPROUTS use smaller batches (outputs may then differ slightly)."`
    (Step 0 may replace the wording with the memo's).
  - `class FixedBatchOutOfMemory(RuntimeError)`: `__init__(self, batch_size: int)`,
    attribute `batch_size`, `str(exc) == FIXED_OOM_MESSAGE.format(n=batch_size)`.
  - `run_batches(..., fixed: bool = False)`. `fixed=False` is today's code, line for line.
    With `fixed=True`: every forward gets a tensor of exactly `batch_size` rows (a short
    last batch is padded with `torch.zeros_like(template)` rows; unreadable inputs keep
    their zero-filled slot as today); outputs of padding rows are never saved; on CUDA OOM
    the step is left (outside the `except`, as `OomBackoff` does), `_free_cuda_cache()` is
    called (module attribute, looked up at call time), the same tensor runs once more, and
    a second OOM raises `FixedBatchOutOfMemory(batch_size)` from it. The retry appends
    `{"at": <first input of the batch>, "from": batch_size, "to": batch_size}` to
    `report.oom_events`; `batch_shapes` counts real slots only (inputs and their zero-filled
    unreadable slots, not padding): 7 inputs at 3 → `{3: 7}`;
    `effective_batch_size == batch_size`.
- Produces (callers): `default_mask_batch_size(*, policy: str | None = None) -> int`;
  `generate_masks(..., batch_policy: str | None = None)`;
  `default_skeleton_batch_size(device_type: str, *, policy: str | None = None) -> int`
  (`fixed` never calls `config.get_skeleton_batch_size`);
  `run_skeleton_generation(..., batch_policy: str | None = None)`. `None` →
  `runtime.batch_policy()`. A caller's explicit `batch_size` is used as the fixed size.
  Both manifests' `parameters` gain `"batch_policy": "fixed" | "auto"`.

- [ ] **Step 0: Read the memo's verdict.** Open `research/o4-gpu-batch.md`. If its
  PROGRESS still has "Report written" unticked, stop and tell the lead (this task waits).
  Then:
  - **Premise holds** (padded tail = full batch, 0 files differ, both models, as Exp 1
    reported): `DEFAULT_BATCH_POLICY = "fixed"`; this task is Sci.
  - **Premise fails** (any padded-tail or permutation difference): `DEFAULT_BATCH_POLICY = "auto"`;
    `fixed` is opt-in, nothing changes by default, the commit is not Sci (CHANGELOG
    `### Added` only), and the README paragraph says what the memo found.
  - Use the memo's OOM wording if it gives one (Exp 1c memory pressure), and its
    recommended sizes if they differ from 16 / 64 (then the tests' 16 / 64 change too).
  Write the branch taken at the top of your report.
- [ ] **Step 1: Write the failing tests** in `tests/test_generation_fixed_batches.py`
  (reuse `NAMES`, `_make_images`, `_spec`, `_step`, `_outputs` from
  `tests.test_generation_batch_engine`; stub models from `tests.test_generation_inference`
  as `tests/test_generation_failed_run_manifest.py` does):
  - `test_a_padded_tail_runs_every_forward_at_the_fixed_size`: 7 images, `batch_size=3`,
    `fixed=True`, a step recording `x.shape[0]` → sizes `[3, 3, 3]`; `len(report.written) == 7`;
    `_outputs(out) == _outputs(ref)` where `ref` is the same run with `fixed=False`;
    `report.batch_shapes == {3: 7}`; `report.effective_batch_size == 3`.
  - `test_padding_rows_are_never_saved`: a `save` spy → saved input names `== NAMES`.
  - `test_an_unreadable_input_keeps_its_slot_and_the_tail_is_padded`: `NAMES + ["bad.jpg"]`
    (corrupt), `batch_size=3`, fixed → sizes `[3, 3, 3]`, `failed_names == ["bad.jpg"]`,
    `batch_shapes == {3: 8}`.
  - `test_fixed_out_of_memory_retries_once_then_stops`: a step that always raises
    `torch.cuda.OutOfMemoryError("CUDA out of memory (stub)")`, `_free_cuda_cache`
    monkeypatched to a counter → `pytest.raises(FixedBatchOutOfMemory)` with
    `str(exc) == FIXED_OOM_MESSAGE.format(n=3)`; sizes `[3, 3]` (no halving); frees `== 1`;
    the passed-in `report.oom_events == [{"at": "img00.png", "from": 3, "to": 3}]`,
    `report.written == []`.
  - `test_a_fixed_retry_that_succeeds_goes_on`: OOM on the first call only → 7 written,
    sizes `[3, 3, 3, 3]`, one `oom_events` entry, `effective_batch_size == 3`.
  - `test_a_fixed_stop_is_a_failed_run_record`: `generate_masks(src, out, model=<stub that
    succeeds on forward 1 and raises OutOfMemoryError from forward 2 on>, batch_size=2, batch_policy="fixed")`
    on 5 images → raises `FixedBatchOutOfMemory`; `_sprouts_run.json` `outputs.status == "failed"`,
    `outputs.error == "FixedBatchOutOfMemory: " + FIXED_OOM_MESSAGE.format(n=2)`,
    `parameters.batch_policy == "fixed"`, 2 PNGs on disk.
  - `test_batch_policy_reads_the_environment` (monkeypatch env): unset →
    `DEFAULT_BATCH_POLICY`; `"AUTO"` → `"auto"`; `" fixed "` → `"fixed"`; `"bogus"` → default.
  - `test_fixed_sizes_are_the_owner_sizes`: `fixed_batch_size("mask", "cuda") == config.MASK_BATCH_SIZE == 16`,
    `("skeleton", "cuda") == 64`, `("mask", "cpu") == 4`, `("skeleton", "cpu") == 8`.
  - `test_fixed_skeleton_batch_never_asks_the_gpu`: `config.get_skeleton_batch_size`
    monkeypatched to `pytest.fail` → `default_skeleton_batch_size("cuda", policy="fixed") == 64`;
    with it returning 104, `default_skeleton_batch_size("cuda", policy="auto") == 104`.
  - `test_the_manifest_records_the_batch_policy` (parametrize mask / skeleton, CPU runtime
    fixture as in `test_generation_failed_run_manifest.py`): `batch_policy="auto"` →
    `parameters.batch_policy == "auto"`.
- [ ] **Step 2: Run, expect FAIL** — `PYTEST tests/test_generation_fixed_batches.py`
  (`ImportError: FixedBatchOutOfMemory`).
- [ ] **Step 3: Implement** the Produces blocks. The fixed branch is a small helper
  `_run_fixed(step, x, n_real) -> list[tuple[slice, Any]]` returning
  `[(slice(0, n_real), result)]`; the save loop already indexes `result[offset]` by input.
  Padding happens in `load_batch` when `fixed` and `len(batch_paths) < batch_size`.
- [ ] **Step 4: Run, expect PASS** — the Step 2 command.
- [ ] **Step 5: Existing tests.** Run `PYTEST tests/test_generation_batch_engine.py tests/test_generation_oom_backoff.py tests/test_generation_inference.py tests/test_generation_failed_run_manifest.py tests/test_generation_all_failed.py tests/test_generation_handlers.py tests/test_jobs_cancel_cpu_fallback.py tests/test_robust_run_manifest_fallback.py tests/test_run_record_history.py tests/test_foundation_core_config.py tests/test_exif_policy.py tests/test_robust_generation_cleanup.py tests/test_robust_generation_stem_clash.py`.
  With the default `fixed`, a test that pins *auto* behaviour through `generate_masks` /
  `run_skeleton_generation` (OOM halving, every image out of memory recorded as failed,
  `effective_batch_size` below the requested size) gets `batch_policy="auto"` (handler-level
  tests: `monkeypatch.setenv("SPROUTS_BATCH_POLICY", "auto")`) and nothing else. List each
  such test by name in your report. Any other failure is a bug in this task.
- [ ] **Step 6: Docs.** README after the `SPROUTS_CUDNN_BENCHMARK` paragraph: "Batches run
  at one fixed size (masks 16, skeletons 64 on a GPU; 4 and 8 on the CPU), the last one
  padded, so a mask or skeleton does not depend on how many images were in the folder or
  how much GPU memory was free. If the GPU runs out of memory at that size the run stops
  with a message; `SPROUTS_BATCH_POLICY=auto` restores the earlier behaviour (skeleton batch
  sized to free memory, batches halved on out of memory)." (premise-fails branch: say `auto`
  is the default and `fixed` is opt-in.) DATA_DICTIONARY: `parameters` now include
  `batch_policy`; under `fixed`, `oom_events` holds the one retry (`from` = `to`).
- [ ] **Step 7: CHANGELOG** (premise holds) `### Scientific output changes`: "Generation
  runs every batch at one fixed size, the last batch padded with zeros whose outputs are
  dropped (O4, W3-T3). Measured on 300 real 640 x 480 images (RTX 5070 Ti,
  `research/o4-gpu-batch.md`): padded tail against a full batch of real images 0 files
  differ (both models); today's unpadded mask tail (12 images) against the padded one: 4
  files / 5 pixels differ; skeletons identical. Mask outputs no longer change with the
  number of images, free memory or out-of-memory halving (16 vs 8 / 4 / 1: 95 / 96 / 59 of
  300 files differ today); the skeleton batch is no longer sized to free memory (64 vs 128
  identical, 64 vs 1: 105 files). `SPROUTS_BATCH_POLICY=auto` keeps the former behaviour."
  Copy the numbers from the memo's final tables (the ones above are its interim results);
  no GPU run is needed in this task.
- [ ] **Step 8: Ruff** — `RUFF` on the four app files and the changed test files.
- [ ] **Step 9: Commit (lead)** — `feat(inference): fixed batch shapes for reproducible outputs, SPROUTS_BATCH_POLICY (W3-T3, O4)`

---

### Task W3-T4: A prediction of another size opens stretched with nearest (M1 part B, O8)

Impl sonnet / high · Rev opus / medium · Lane L-correct (Phase C) · **Sci: only a mask
saved from Correct after such an open** · After WG-T2.

**Files:**
- Modify: `app/gui/correct/mask_document.py` (`MaskRead`: field `resized_from`; `read`;
  `open`: the notice; texts at the top) — re-locate by name, W1-T2 / WG-T2 move lines
- Modify: `app/gui/correct/read_back.py` (its `MaskRead(...)` construction keeps
  `resized_from=None`; check every other `MaskRead(` with `rg "MaskRead\("`)
- Test: Modify `tests/test_correct_mask_document.py`, `tests/test_correct_mask_layer.py`
  (`test_open_hands_back_the_documents_notices`), `tests/test_correct_unloaded_mask.py` (W1-T2)
- Modify: `README.md` (the W1-T2 "Saving" bullet: one sentence), `CHANGELOG.md`

**Interfaces:**
- Consumes: W1-T2's `MaskRead.not_loaded` (`""`, `"size"`, `"unreadable"`) and its
  "not shown" rule; WG-T2's mask source (GT else prediction).
- Produces: `MaskRead.resized_from: tuple[int, int] | None = None` — the file's `(w, h)`
  when a prediction was stretched to the photo. `RESIZED_TEXT = "Mask is {fw} x {fh}; shown at the photo's {w} x {h}. Saving stores it at the photo's size."`
  (`Notice(..., "info")`).
- Rule: the file that opens is a **prediction** and its size differs → plane =
  `cv2.resize(_zero_one_plane(on), (w, h), interpolation=cv2.INTER_NEAREST)`,
  `has_saved_file=True`, `not_loaded=""`, `resized_from=(fw, fh)`; the document opens clean
  with that plane as its baseline (an untouched Save writes nothing, O2). A **GT /
  editor-saved** mask of another size stays under W1-T2 (`not_loaded="size"`).

- [ ] **Step 0: Re-check against WG-T2.** Read WG-T2's committed `mask_document.py` (and
  `wg-gt-unified-editor.md` if it exists). Decide "prediction" by WG-T2's source (the file
  came from `mask/` because no GT exists). If WG-T2 kept a single `mask/` reader instead,
  use the PNG colour type: grey (0) = prediction, RGBA (6) = editor-saved
  (`tests.correct_goldens_support.png_colour_type` shows the check). Write which rule you
  used in the report.
- [ ] **Step 1: Write the failing tests** in `tests/test_correct_mask_document.py`
  (`_write`, `_mask_path`, `_image` exist):
  - `test_a_half_size_prediction_opens_stretched_exactly`: grey 40 x 30 prediction with
    `grey[5:10, 8:20] = 255`; `doc.open(_image(tmp_path), (80, 60))` ==
    `[Notice("Mask is 40 x 30; shown at the photo's 80 x 60. Saving stores it at the photo's size.", "info")]`;
    plane `== np.kron(src > 0, np.ones((2, 2)))`; `not doc.is_dirty()`;
    `doc.status() == LayerStatus("clean")`; `metrics.root_area_mm2(plane * 255) == pytest.approx(metrics.root_area_mm2(src))` (x2 is exact).
  - `test_a_prediction_opens_stretched_at_any_ratio` (parametrize `(2048, 1536)`,
    `(1920, 1080)`, `(800, 600)`): a 640 x 480 grey prediction with a filled
    `cv2.circle((320, 240), 90)` and a 9 px `cv2.line((0, 0), (639, 479))` → plane shape
    `(h, w)`, values in `{0, 1}`, clean, `MaskDocument.read(...).resized_from == (640, 480)`,
    area within 1 % of the file's area (`root_area_mm2`; the audit's totals are under 0.07 %).
  - `test_an_edited_save_of_a_stretched_prediction_writes_the_photo_size`: `_paint`, save →
    the written file (GT after Wave G) is `(1536, 2048)`.
  - `test_an_editor_saved_mask_of_another_size_is_still_not_shown`: an RGBA mask written
    with `app.masks.write_mask_file` (or a GT file after Wave G) at 80 x 60, opened at
    `(100, 50)` → `[Notice(NOT_SHOWN_SIZE, "warn")]`, `read.not_loaded == "size"`, `read.resized_from is None`.
  Rewrite W1-T2's tests that used a grey mask to mean "another size" so they use an
  editor-saved / GT mask: `test_a_mask_of_another_size_is_not_shown_and_is_kept`,
  `test_painting_over_an_unshown_mask_then_saving_replaces_it`,
  `test_a_failed_save_over_an_unshown_mask_says_so_and_keeps_it_flagged`,
  `test_closing_forgets_the_unshown_file`, `test_a_read_says_why_a_file_was_not_loaded`
  (and in `tests/test_correct_unloaded_mask.py`, `tests/test_correct_mask_layer.py` any test
  whose other-size mask is a grey prediction). Add to `tests/test_correct_unloaded_mask.py`:
  `test_a_2048_photo_shows_its_640_prediction` — editor on a 2048 x 1536 photo with a
  640 x 480 prediction → mask layer status `clean`, plane shape `(1536, 2048)`,
  `editor.save() is True` and no file written or changed (the prediction's bytes equal,
  no GT file created): an untouched Save of a stretched prediction writes nothing.
- [ ] **Step 2: Run, expect FAIL** — `PYTEST tests/test_correct_mask_document.py tests/test_correct_mask_layer.py tests/test_correct_unloaded_mask.py`.
- [ ] **Step 3: Implement** the Interfaces block. Keep `mask_document.py` ≤ 450 lines.
- [ ] **Step 4: Run, expect PASS** — Step 2 command plus `tests/test_correct_goldens.py tests/test_correct_mask_goldens.py tests/test_correct_read_ahead.py tests/test_correct_read_ahead_handback.py tests/test_correct_layering.py tests/test_correct_session.py tests/test_correct_editor.py`.
  Goldens unchanged (both cases are 640 x 480 with a 640 x 480 mask or none).
- [ ] **Step 5: Docs.** README Saving bullet: "A prediction of another size than the photo
  (Generate keeps the model's 640 x 480) opens stretched to the photo with nearest
  neighbour; saving an edit stores it at the photo's size." CHANGELOG
  `### Scientific output changes`: "A prediction mask of another size than its photo opens
  stretched to the photo (nearest neighbour) instead of not at all (O8, W3-T4). Nothing
  changes until such a mask is edited and saved; the saved mask is then at the photo's
  size: area identical for integer factors (x2); on the audit's 17 masks x3.2 total
  +0.013 % (worst image 1.45 %), 16:9 +0.026 % (0.70 %), x1.25 +0.069 % (3.11 %). All
  640 x 480 photos: no change."
- [ ] **Step 6: Ruff** — `RUFF app/gui/correct/mask_document.py app/gui/correct/read_back.py` and the test files.
- [ ] **Step 7: Commit (lead)** — `feat(correct): open a prediction of another size stretched to the photo (W3-T4, O8)`

---

### Task W3-T2: Per-project field of view; default 18 x 13.5 mm (M2 step 2, O3)

Impl opus / high · Rev opus / high · Lane L-measure · **Sci: commit 2** · After W2-T6.
Two commits: **commit 1** (mechanism, default still 18 x 13, no CSV value changes) and
**commit 2** (default 18 x 13.5). The lead announces commit 2 to the owner before it lands.

**Decision on memo Q3 (old values), for the owner to confirm:** a CSV whose sidecar records
another field of view than the open project's, or (length) another length method, is
**flagged, not hidden**: the metrics bar marks the value outdated with the reason, the
Measure status line says "measured with another field of view or length method: measure
again", and the values and dashboards stay usable. Reason: mixing old and new values in
one analysis is the real risk (memo Q3), hiding or deleting results would lose data, and a
flag reuses the "outdated" path users already know. A CSV without a sidecar, or whose
sidecar has no `calibration`, is never flagged (unknown is not "different").

**Files (commit 1):**
- Create: `app/calibration.py`
- Modify: `app/inference/metrics.py` (`root_length_mm`, `root_area_mm2`, `length_row`,
  `area_row`, `_metric_row` call sites: keyword `calibration`)
- Modify: `app/inference/measure_run.py` (`MeasureRun(..., calibration=)`, the record's
  `calibration`, `LENGTH.parameters["estimator"] = calibration.LENGTH_METHOD`)
- Modify: `app/provenance.py` (`calibration_info()` adds `"source": "default"`;
  `build_record(..., calibration: Mapping[str, Any] | None = None)`)
- Modify: `app/handlers/skeleton_handler.py` (`_measure`: load the project file, warn on a
  problem, pass it; `_on_finished`: the shape warning)
- Modify: `app/data_processing/measurement_index.py` (keep each table's sidecar
  `calibration` and `parameters.estimator` with W2-T4's calibration;
  `MeasurementIndex.method_notes(current)`)
- Modify: `app/gui/window/metrics.py` (`MetricsController.current_calibration()`; `refresh`
  passes the notes and compares W2-T4's FOV cell against the project's calibration)
- Modify: `app/gui/metrics_bar.py` (`set_metrics(..., method_notes=)`)
- Modify: `app/gui/stage_summary.py` (`Freshness.method_note`, `_metric_text`),
  `app/gui/stage_status.py` (`check.work()`: the note per metric)
- Modify: `app/visualization/dash_export.py` (`provenance_lines`: "this app now uses" →
  compared with `calibration.load_for_output(source_csv)`)
- Modify: `app/gui/about_dialog.py` (the Calibration row shows the open project's
  calibration, read duck-typed from `parent.metrics.current_calibration()`)
- Modify: `tests/test_foundation_core_config.py` (`FOUNDATION_MODULES` + `"app.calibration"`),
  `tests/test_import_smoke.py` (`APP_MODULES` + `"app.calibration"`),
  `tests/test_foundation_core_provenance.py:58`, `tests/test_measure_core.py:297`
  (the record gains `"source": "default"`)
- Modify: `README.md` (Calibration notes: the project file), `docs/DATA_DICTIONARY.md`
  (provenance `calibration` keys; the project file), `CHANGELOG.md` `### Added`
- Test: Create `tests/test_project_calibration.py`; Modify `tests/test_measure_handler.py`,
  `tests/test_metrics_bar_smoke.py`, `tests/test_shell_stage_summary.py`,
  `tests/test_viz_dash_export.py`
- Not changed: `app/project_layout.py` (the file path lives in `app/calibration.py`, so
  L-correct / L-library keep that file) and `app/config.py` (commit 2 only).
- Wait for: no uncommitted edit by another lane in `about_dialog.py` (L-misc) or
  `dash_export.py` (L-viz).

**Interfaces:**
- Consumes: W2-T2 `spec.row(name, path, data=...)`; W2-T11 `MeasureRun(..., without_image=)`;
  WG-T8's resolver and `Source` column (read by name); W2-T4 `measure_run.sizes_px(rows)`,
  rows' `_size_px`, `provenance.read_sidecar(path)`, `measure_run.csv_calibration(csv)`,
  `metrics_bar.fov_text(calibration=None)`, `MetricsBar.set_fov`,
  `MeasurementIndex.calibrations()`, `dash_export.provenance_lines`; W2-T6 `MASK_THRESHOLD`.
- Produces (`app/calibration.py`):
  - `PROJECT_FILENAME = "sprouts_project.json"`, `PROJECT_SCHEMA = 1`,
    `LENGTH_METHOD = "8-connected arc length with per-axis pixel pitch"` (today's text;
    W3-T8 replaces it).
  - `@dataclass(frozen=True) class ProjectCalibration`: `calibration: Calibration`,
    `source: str` (`"project"` | `"default"`), `reference_size_px: tuple[int, int] | None = None`,
    `evidence: str = ""` (the file's `calibration.source` text), `problem: str = ""` (why an
    existing project file was not used).
  - `default_calibration() -> ProjectCalibration` — `config.CALIBRATION` at call time,
    `source="default"`.
  - `project_file_path(project_root: str | os.PathLike[str]) -> str`.
  - `load_project_calibration(project_root: str | os.PathLike[str] | None) -> ProjectCalibration`
    — `None` or no file → default, `problem=""`. The file is read as `utf-8-sig`. Cached by
    `(path, st_mtime_ns, st_size)`. Problems (exact, default used): `"it is not valid JSON"`,
    `'"schema" must be 1'`, `'"calibration" is missing'`,
    `'"calibration.fov_width_mm" must be a number above 0'` (same for `fov_height_mm`; a
    bool, a string, `nan`, `inf`, 0 or below all fail), `'"calibration.reference_size_px" must be two whole numbers above 0'`,
    `'"calibration.source" must be text'`, and for an unreadable file `"it could not be read"`.
    Unknown keys are ignored.
  - `load_for_output(output_path: str) -> ProjectCalibration` — the project file of the
    folder two levels above `output_path` (`<root>/skeletons/root_lengths.csv`), else three
    levels (`<root>/output/mask/...`), else default.
  - `calibration_record(pc: ProjectCalibration) -> dict[str, Any]` —
    `{"fov_width_mm", "fov_height_mm", "source"}`; for `"project"` also
    `"file": "sprouts_project.json"`, `"evidence"` and `"reference_size_px": [w, h]` when set.
  - `fov_label(calibration: Calibration | Mapping[str, float]) -> str` — `"18×13 mm"`,
    `"18×13.5 mm"` (`:g` format, as `metrics_bar.fov_text`).
  - `method_note(sidecar: Mapping[str, Any] | None, *, kind: str, current: ProjectCalibration) -> str`
    — `""` when the sidecar or its `calibration` is missing. Otherwise the calibration
    sentence when width or height differ (`math.isclose`, `rel_tol=1e-9`):
    `"Measured with field of view {fov_label(recorded)}; this project uses {fov_label(current)}. Measure again before comparing with new values."`,
    then for `kind == "length"` when `parameters.estimator` is present and differs from
    `LENGTH_METHOD`: `"Measured with an earlier length method. Measure again before comparing with new values."`;
    both joined by one space.
  - `aspect_mismatches(sizes_px: Mapping[str, int], reference: tuple[int, int] | None, *, tolerance: float = 0.01) -> int`
    — the number of files (counts of W2-T4's `"WxH"` keys) whose `w / h` differs from the
    reference's by more than 1 %; 0 without a reference. 341 x 256 against 640 x 480 is
    0.06 %: not a mismatch.
- Produces (elsewhere):
  - `metrics.root_length_mm(skeleton, *, calibration: Calibration | None = None) -> float`,
    `root_area_mm2(mask, *, calibration=None)`, `length_row(name, path, *, data=None, calibration=None)`,
    `area_row(...)` likewise.
  - `MeasureRun(spec, images, output_dir, *, project_root=None, without_image=(), calibration: ProjectCalibration | None = None)`
    — `None` → `default_calibration()` (the run never reads files itself); rows get
    `calibration=self.calibration.calibration`; the sidecar's `calibration` is
    `calibration_record(self.calibration)`.
  - `provenance.build_record(..., calibration=None)` — `None` → `calibration_info()`
    (`{"fov_width_mm", "fov_height_mm", "source": "default"}`; generation manifests keep
    the app default, since generation does not use a field of view).
  - Handler texts (exact, `"warn"`): problem → `f"{PROJECT_FILENAME} was not used ({problem}): measured with the default field of view {fov_label(default)}."`;
    shape → `"{n:,} measured {file|files} {has|have} another shape than the {w}×{h} reference in sprouts_project.json: the field of view was applied to each whole image."`
  - `MeasurementIndex.method_notes(current: ProjectCalibration) -> dict[str, str]`
    (`{"length": ..., "area": ...}`, `""` = none).
  - `MetricsController.current_calibration() -> ProjectCalibration` (the open project's,
    default without one).
  - `MetricsBar.set_metrics(..., method_notes: Mapping[str, str] | None = None)` — a metric
    with a note is shown outdated and its tooltip gets the note as a line of its own.
  - `stage_summary.Freshness.method_note: str = ""` (appended last); `_metric_text` for a
    measured metric with a note: `"{n:,} {noun}: measured with another field of view or length method: measure again"`.

**Commit 1**

- [ ] **Step 1: Write the failing tests** in `tests/test_project_calibration.py`:
  - `test_no_project_file_gives_the_default`: `load_project_calibration(tmp_path)` →
    `calibration == config.CALIBRATION`, `source == "default"`, `problem == ""`; same for `None`.
  - `test_a_project_file_sets_the_field_of_view`: file
    `{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5, "reference_size_px": [640, 480], "source": "Rajurkar et al. 2022"}}`
    → `Calibration(18.0, 13.5)`, `"project"`, `(640, 480)`, `evidence == "Rajurkar et al. 2022"`;
    the same file written with a UTF-8 BOM gives the same.
  - `test_an_invalid_project_file_falls_back_and_says_why` (parametrize: `"{"`, schema 2,
    no `calibration`, `"fov_width_mm": "18"`, `0`, `-1`, `true`, `NaN`,
    `"reference_size_px": [640]`, `"source": 3`) → `source == "default"` and `problem` equals
    the matching exact text.
  - `test_the_file_is_read_again_when_it_changes`: load, rewrite with 13.5 and a new mtime →
    the new value.
  - `test_the_record_names_its_source`: default → `{"fov_width_mm": 18.0, "fov_height_mm": 13.0, "source": "default"}`;
    the project file above → that plus `"file": "sprouts_project.json"`,
    `"evidence": "Rajurkar et al. 2022"`, `"reference_size_px": [640, 480]`.
  - `test_metric_functions_keep_their_signatures`: for `root_length_mm`, `root_area_mm2`
    the first parameter is positional and `calibration` is `KEYWORD_ONLY` with default
    `None`; `root_area_mm2(np.full((480, 640), 255, np.uint8)) == pytest.approx(18.0 * 13.0)`;
    `root_area_mm2(same, calibration=Calibration(18.0, 13.5)) == pytest.approx(243.0)`.
  - `test_measure_uses_and_records_the_project_calibration`: two masks 80 x 64 →
    `MeasureRun(AREA, images, out, project_root=root, calibration=<13.5 project>)` → each
    value `== round(n_px * 18.0 / 80 * 13.5 / 64, 2)`; sidecar `calibration ==
    calibration_record(pc)`.
  - `test_method_note_texts`: recorded 18 x 13, current 18 x 13.5 → the calibration
    sentence with `"18×13 mm"` and `"18×13.5 mm"`; same calibration and length
    `estimator` `"old"` → the method sentence; both → both, one space apart; sidecar
    `None` or without `calibration` → `""`; area ignores `estimator`.
  - `test_aspect_mismatches`: `{"640x480": 3, "341x256": 2, "1920x1080": 1}` with `(640, 480)` → `1`; reference `None` → `0`.
  - `test_load_for_output_finds_the_project_two_or_three_levels_up`.
  `tests/test_measure_handler.py` (its `_project`, `_handler`, `_wait_idle`):
  - `test_measure_reads_the_project_file`: a 13.5 project file → the area sidecar's
    `calibration.source == "project"` and its values use 13.5.
  - `test_an_invalid_project_file_warns_once_and_measures_with_the_default`: file `"{"` →
    exactly one warn notify
    `"sprouts_project.json was not used (it is not valid JSON): measured with the default field of view 18×13 mm."`;
    sidecar `source == "default"`.
  - `test_files_of_another_shape_warn_once`: project file with reference `[640, 480]`, one
    mask 96 x 48 among 80 x 60 ones → one warn starting `"1 measured file has another shape"`.
  `tests/test_metrics_bar_smoke.py::test_a_method_note_marks_the_value_outdated` and
  `tests/test_shell_stage_summary.py::test_a_method_note_says_measure_again`; in a new
  `tests/test_project_calibration.py::test_a_csv_measured_with_another_field_of_view_is_flagged`:
  measure with the default, then write a 13.5 project file →
  `MeasurementIndex(root).method_notes(load_project_calibration(root))["area"]` starts with
  `"Measured with field of view 18×13 mm; this project uses 18×13.5 mm."`, `["length"] == ""`
  when no length CSV exists. `tests/test_viz_dash_export.py::test_the_export_compares_with_the_projects_calibration`:
  a 13.5 project file next to a CSV recorded at 13.5 → no `"this app now uses"` part.
- [ ] **Step 2: Run, expect FAIL** — `PYTEST tests/test_project_calibration.py tests/test_measure_handler.py tests/test_metrics_bar_smoke.py tests/test_shell_stage_summary.py tests/test_viz_dash_export.py`
  (`ModuleNotFoundError: app.calibration`).
- [ ] **Step 3: Implement** the Interfaces block. Update the two record pins
  (`test_foundation_core_provenance.py:58`, `test_measure_core.py:297`) to include
  `"source": "default"`. README Calibration notes, one paragraph: "A project can state its
  own field of view in `sprouts_project.json` next to the images:
  `{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5, "reference_size_px": [640, 480], "source": "how it was measured"}}`.
  Measure uses it and records it (with `"source": "project"`) in each CSV's
  `.provenance.json`; a missing or invalid file means the default, and Measure says why. A
  CSV measured with another field of view or length method is marked outdated until
  measured again." DATA_DICTIONARY: the provenance `calibration` keys and the file's
  schema. CHANGELOG `### Added`: "Per-project field of view in `sprouts_project.json`,
  recorded with every measurement; CSVs measured with another field of view or length
  method are marked outdated (O3 mechanism, memo Q3, W3-T2)."
- [ ] **Step 4: Run, expect PASS** — Step 2 command plus `tests/test_measure_core.py tests/test_regression.py tests/test_foundation_core_config.py tests/test_foundation_core_provenance.py tests/test_import_smoke.py tests/test_layering_contract.py tests/test_shell_info_about.py tests/test_shell_info_measurement_index.py tests/test_shell_metrics_per_metric_staleness.py tests/test_measure_inputs_record.py tests/test_measure_without_image.py tests/test_correct_goldens.py`.
  Both byte-identity tests pass: no CSV byte changes in commit 1.
- [ ] **Step 5: Ruff** on every `.py` of the commit-1 Files list.
- [ ] **Step 6: Commit 1 (lead)** — `feat(calibration): per-project field of view, recorded with every measurement (W3-T2, O3)`

**Commit 2** (after the owner has been told; memo Q1 — grid images — answered or waived)

- [ ] **Step 7: Baseline** — section S on the real sample and the synthetic set into
  `<scratch>/w3-t2/before/`.
- [ ] **Step 8: Write the failing test** `tests/test_project_calibration.py::test_the_default_field_of_view_is_18_by_13_5`:
  `config.CALIBRATION == Calibration(18.0, 13.5)`;
  `config.CALIBRATION.mm_per_px(640, 480) == (0.028125, 0.028125)`;
  `calibration_info() == {"fov_width_mm": 18.0, "fov_height_mm": 13.5, "source": "default"}`.
  Run it: FAIL (13.0).
- [ ] **Step 9: Implement** — `app/config.py:49` `fov_height_mm: float = 13.5`; the
  `Calibration` docstring names the source (Bartz BTC-100X / I-CAP frame, 640 x 480 px,
  Rajurkar et al. 2022, Plant Methods 18:39); `mean_mm_per_px` docstring (`:60-62`):
  "Mean mm-per-pixel. The default pixels are square (0.028125 mm at 640 x 480); a project
  file may set another field of view."; `metrics.py` module docstring (`:13-18`): drop
  "near-isotropic (<4%)", say the per-axis pitch keeps a non-square project calibration
  exact. Text examples `"18×13 mm"` in `app/gui/metrics_bar.py:4, 51` → `"18×13.5 mm"`.
- [ ] **Step 10: Update the pins**, each by name: `test_foundation_core_config.py::test_existing_contract_values_unchanged`
  (13.5), `test_foundation_core_provenance.py:58`, `test_measure_core.py:297`,
  `test_regression.py:32` (`FOV_H_MM = 13.5`), `test_metrics_bar_smoke.py:83` (`"18×13.5 mm"`),
  `test_shell_info_about.py:76`, `test_viz_dash_export.py:86` (`"18.00 x 13.50 mm"`), this
  task's own commit-1 pins (`test_metric_functions_keep_their_signatures`: `18.0 * 13.5`;
  `test_the_record_names_its_source`: 13.5; the invalid-file warn text: `"18×13.5 mm"`), and
  W2-T4's / commit 1's tests that wrote a 13.5 sidecar or project file to mean "differs
  from the app": they now write 13.0 (grep `13.5`, `13.0`, `18×13`, `18.00 x 13` under
  `tests/`; list each change). `tests/data/correct_goldens.json`: `area_mm2_as_measured`
  14.52 → the recomputed value (expected 15.08) and 38.09 → (expected 39.55 or 39.56; take
  `describe_mask_file`'s value), pixels untouched. If WG-T8 made the golden lengths
  asserted, recompute them too.
- [ ] **Step 11: Run, expect PASS** — the Step 4 command plus `tests/test_correct_mask_goldens.py tests/test_correct_goldens_data.py tests/test_shell_metrics_bar_separators.py`.
- [ ] **Step 12: After** — section S into `.../after/`; S4 for both metrics. Expected (memo):
  real sample lengths +2.02 % total (616 rows changed of 3,000, median +2.01 %, 5th…95th
  +0.43…+3.59 %, max +3.80 %, no zero ↔ non-zero flip); every non-zero area × 1.038462
  (+3.846 %).
- [ ] **Step 13: Docs + CHANGELOG.** README Calibration notes default **18 mm × 13.5 mm**
  and "A fully-root mask measures the whole FOV (243 mm²)"; DATA_DICTIONARY and MODELS lines
  that state 18 x 13 (memo: `DATA_DICTIONARY.md:97`, `MODELS.md:66`; re-grep). CHANGELOG
  `### Scientific output changes` (memo draft, with your S4 numbers): "Field of view
  18 × 13 mm → 18 × 13.5 mm, the Bartz BTC-100X / I-CAP frame at 640 × 480 px (Rajurkar et
  al. 2022, Plant Methods 18:39); pixels are now square (0.028125 mm at 640 × 480). Every
  root area × 1.0385 (+3.85 %); root lengths +0 to +3.85 % by direction (vertical steps
  +3.85 %, diagonal +1.87 %, horizontal 0). 3,000 real skeletons: +2.02 % in total, N rows
  changed; synthetic set: … (O3, W3-T2). CSVs measured before are marked outdated until
  measured again."
- [ ] **Step 14: Ruff** — `RUFF app/config.py app/gui/metrics_bar.py app/inference/metrics.py` and the changed tests.
- [ ] **Step 15: Commit 2 (lead)** — `feat(calibration)!: default field of view 18 x 13.5 mm, square pixels (W3-T2, O3)`

---

### Task W3-T8: The corner-count length estimator (X1, O12)

Impl opus / high · Rev opus / max · Lane L-measure · **Sci: every length** · After W3-T2
commit 2. The lead announces it to the owner before it lands (memo Q2: axis-parallel lines
read 2.0 % short).

**Files:**
- Modify: `app/inference/metrics.py` — constants `VS_ORTHOGONAL = 0.980`, `VS_DIAGONAL = 1.406`,
  `VS_CORNER = 0.091`, `_NEIGHBOUR_CODE`, `_corner_lut()` / `_CORNER_LUT`, `SkeletonSteps`,
  `skeleton_arc_length_px`, `root_length_mm`; module docstring (the "Length" bullet)
- Modify: `app/calibration.py` — `LENGTH_METHOD` text (the only place; `measure_run`'s
  `parameters.estimator` and the method check read it)
- Test: Create `tests/test_length_estimator.py`, `tests/length_reference.py`; Modify
  `tests/test_regression.py:44-79`, `tests/test_measure_core.py` (`_legacy_row` length,
  module docstring), `tests/test_skeleton_model_editing.py:621-625` (`_arc_px`),
  `tests/data/correct_goldens.json` (`length_mm_if_measured_informational`, or the
  asserted lengths if WG-T8 made them so), `tests/test_project_calibration.py` (one test)
- Modify: `README.md` (Calibration notes, Length bullet), `docs/DATA_DICTIONARY.md`
  (length definition, memo lines 103-105), `docs/MODELS.md` (memo line 78), `CHANGELOG.md`

**Interfaces:**
- Consumes: W3-T2 `root_length_mm(skeleton, *, calibration=None)`, `calibration.LENGTH_METHOD`,
  `method_note`.
- Produces:
  - `class SkeletonSteps(NamedTuple)`: `horizontal: int`, `vertical: int`, `diagonal: int`
    (m-adjacent diagonal pairs), `corners: int`. `skeleton_arc_length_px(skeleton) -> SkeletonSteps`
    (was a 3-tuple; its only other caller is `tests/test_skeleton_model_editing.py::_arc_px`).
  - `root_length_mm(skeleton, *, calibration=None)` =
    `VS_ORTHOGONAL * (h * mx + v * my) + VS_DIAGONAL / sqrt(2) * d * hypot(mx, my) - VS_CORNER * c * sqrt(mx * my)`
    with `(mx, my) = calibration.mm_per_px(w, h)`.
  - `LENGTH_METHOD = "corner-count length (Vossepoel & Smeulders 1982): 0.980 n_e + 1.406 n_o - 0.091 n_c on the m-adjacent 8-connected skeleton, per-axis pixel pitch"`.
  - `tests/length_reference.py::vs_length_mm(skeleton, mx: float, my: float) -> float` — an
    independent pure-Python version (loops over the set pixels): n_h, n_v as 4-adjacent
    pairs; a diagonal pair counts when the two pixels share no set 4-neighbour; a corner is
    a pixel with exactly two m-adjacent neighbours whose 8-directions are not opposite;
    the same expression, in the same order, as `root_length_mm`.
- Algorithm: the memo's sketch, section "Implementation sketch", verbatim except the
  keyword-only `calibration` (default `None` → `CALIBRATION`) and `Calibration` imported
  from `app.config`. Do not change the LUT, the `filter2D` neighbour code or the bounding-box
  crop: the memo verified this exact code on 680 real files.

- [ ] **Step 1: Baseline** — section S into `<scratch>/w3-t8/before/` (W3-T2 commit 2 state).
- [ ] **Step 2: Write the failing tests.** `tests/test_length_estimator.py` = the memo's
  test block ("Test cases (exact values)") with every `metrics.root_length_mm(x, CAL)`
  written `metrics.root_length_mm(x, calibration=CAL)`; its 8 cases and values unchanged
  (`(100, 0, 0, 0)`, `98.0 * P`, `140.6 * P`; the 10° / 22.5° / 30° lines with steps
  `(165, 0, 35, 70)` 204.540 px, `(117, 0, 83, 165)` 216.343 px, `(85, 0, 115, 169)`
  229.611 px; the junction `(20, 10, 0, 0)`, `29.4 * P`; the ring within 0.5 %; empty and
  single pixel 0.0; the 341 x 256 file `0.98 * 100 * 18 / 341` and `0.98 * 100 * 13.5 / 256`).
  Add:
  - `test_the_constants_are_vossepoel_smeulders`: `(VS_ORTHOGONAL, VS_DIAGONAL, VS_CORNER) == (0.980, 1.406, 0.091)`.
  - `test_the_vectorised_counts_equal_the_reference` (parametrize seeds 0-19): a random
    640 x 480 image of 6 `cv2.line`s and 2 `cv2.circle`s, widths 1-9, thinned with
    `skimage.morphology.skeletonize` → `root_length_mm(sk, calibration=CAL) == vs_length_mm(sk, *CAL.mm_per_px(640, 480))`
    exactly (`==`, same expression order).
  - `test_the_memo_cv2_cases`: `cv2.circle(r=50, 1 px)` steps `(80, 80, 120, 148)`, 312.052 px;
    `cv2.line((100, 100), (300, 183))` steps `(117, 0, 83, 166)`, 216.252 px (rel 1e-6).
  - `tests/test_project_calibration.py::test_a_length_csv_measured_with_the_old_estimator_is_flagged`:
    a length sidecar whose `parameters.estimator == "8-connected arc length with per-axis pixel pitch"`
    and the current calibration → `method_note(...) == "Measured with an earlier length method. Measure again before comparing with new values."`.
- [ ] **Step 3: Run, expect FAIL** — `PYTEST tests/test_length_estimator.py tests/test_project_calibration.py`
  (3-tuple has no `horizontal`; values are Freeman's).
- [ ] **Step 4: Implement** the Produces block.
- [ ] **Step 5: Run, expect PASS** — the Step 3 command.
- [ ] **Step 6: Tests that change**, each by name:
  - `tests/test_regression.py`: `test_horizontal_length_uses_x_pitch` → `(n_px - 1) * 0.98 * FOV_W_MM / w`;
    `test_diagonal_uses_euclidean_step_and_exceeds_orthogonal` → `(n - 1) * 1.406 / math.sqrt(2) * diag_step`,
    still `ld > lh`; `test_length_resolution_invariant_full_width_line` → `0.98 * FOV_W_MM * (w - 1) / w`;
    module docstring: the O12 change and why.
  - `tests/test_measure_core.py`: `_legacy_row`'s length value becomes
    `length_reference.vs_length_mm(s, mx, my)`; the module docstring says the CSVs are
    byte-identical to the legacy pipeline except the length estimator (O12, W3-T8), whose
    reference is independent of `metrics.py`. Both byte-identity tests (synthetic and real
    sample) pass with it.
  - `tests/test_skeleton_model_editing.py::_arc_px`: `st = skeleton_arc_length_px(mask > 0)`;
    `return st.horizontal + st.vertical + st.diagonal * float(np.sqrt(2.0))`. If
    `test_a_small_file_opens_upscaled_smoothly_as_one_line` then misses its 1.5 %, report
    it; do not widen the tolerance.
  - `tests/data/correct_goldens.json`: the two lengths (28.86, 60.83) → the values
    `describe_skeleton_file` now gives; pixels untouched.
- [ ] **Step 7: Run** — `PYTEST tests/test_length_estimator.py tests/test_regression.py tests/test_measure_core.py tests/test_skeleton_model_editing.py tests/test_correct_goldens.py tests/test_correct_skeleton_goldens.py tests/test_correct_goldens_data.py tests/test_robust_measure_speed.py tests/test_measure_handler.py tests/test_project_calibration.py tests/test_error_column.py`. Expected: PASS.
- [ ] **Step 8: After** — section S into `.../after/`; S4 on the length CSVs. Expected
  (memo E5, O12 alone at 18 x 13.5): total −5.24 %, 654 rows changed, median −5.12 %, 5th…95th
  −7.25…−2.92 %, extremes −11.51 % and −1.88 %; areas byte-identical. Synthetic set: report
  its total change. Time `measure_folder.py`'s length run before and after (memo: +0.015 ms
  per skeleton).
- [ ] **Step 9: Docs + CHANGELOG.** README Length bullet: "Length uses the corner-count
  estimator of Vossepoel & Smeulders (1982): 0.980 per orthogonal step, 1.406 per diagonal
  step and −0.091 per change of direction, on the 8-connected skeleton with a junction's
  diagonal not counted twice; per-axis pixel pitch. It is unbiased over all directions
  (−1.9 % to +1.0 % by direction; a perfectly horizontal or vertical line reads 2.0 %
  short)." DATA_DICTIONARY and MODELS: the same definition in one sentence each. CHANGELOG
  `### Scientific output changes` = the memo's draft "Length estimator (O12)" with your S4
  numbers and the synthetic line, plus "Together with the field-of-view change (W3-T2):
  −3.33 % in total on the same sample." and "CSVs measured before are marked outdated
  (earlier length method)." Ask the lead to update `docs/KNOWN_ISSUES.md` (X1, memo lines
  542 / 563) at the wave close-out.
- [ ] **Step 10: Ruff** — `RUFF app/inference/metrics.py app/calibration.py tests/test_length_estimator.py tests/length_reference.py tests/test_regression.py tests/test_measure_core.py tests/test_skeleton_model_editing.py`.
- [ ] **Step 11: Commit (lead)** — `feat(measure)!: corner-count length estimator, Vossepoel & Smeulders 1982 (W3-T8, O12)`

---

## Self-review (done 2026-10-25)

1. **Spec coverage.** O3 → W3-T2 (file, sidecar record with `source`, two commits, near-
   isotropic comment rows in commit 2, memo Q3 decided and flagged for the owner); O12 →
   W3-T8 (memo formula, m-adjacency, corner definition, sketch, 8 exact cases, CHANGELOG
   draft); O4 → W3-T3 (env var, 16 / 64, padded tail, one retry then stop, default from the
   memo with both branches, `auto` = today's path); O8 → W3-T4 (nearest, notice text,
   W1-T2 rule for editor-saved / GT); O11 → W3-T5 (canvas, mask layer, GT layer, sizes 2-9
   pinned, goldens untouched); O38 → W3-T7. The memo's "tests that change" list is covered
   in W3-T2 Step 10 and W3-T8 Step 6. Gap kept open on purpose: the memo's Q1 (grid images)
   and Q6 (the Zenodo record's 18 x 13) are owner actions, not tasks.
2. **Step scan.** Every test step names its assertions and values; the two places that
   cannot know a value in advance (W3-T5's dab hashes, the goldens' recomputed mm values)
   say how to capture it and what is expected. No step writes a body the signature fixes,
   except the memo's estimator, which is referenced, not copied.
3. **Type consistency.** `ProjectCalibration`, `load_project_calibration`,
   `calibration_record`, `method_note(sidecar, *, kind, current)`, `LENGTH_METHOD`,
   `SkeletonSteps` fields, `FixedBatchOutOfMemory(batch_size)`, `run_batches(..., fixed=)`,
   `batch_policy=` (not `policy=`, which `generate_masks` already uses for the overwrite
   choice), `MaskRead.resized_from` are used with the same names in every task.
4. **Review Focus.** Each of the five lines has a named test in its owning task.
5. **Proportion.** About 850 lines for six tasks, one of them two commits plus the memo Q3
   check; there are no code blocks, only exact texts, signatures and test assertions; the
   estimator's code stays in the memo.

## Changes to the master

1. **W3-T7 placement:** L-measure becomes `W2-T2 → W2-T11 → WX-T9 → W3-T7 → WG-T8 → W2-T4 → W2-T6 → W3-T2 → W3-T8`;
   W2-T2's **After** becomes "—" (it started without W3-T7). Phase A's L-measure line drops
   W3-T7 at its head.
2. **W3-T5 After:** WG-T0 (the GT goldens must exist and are part of its green run).
3. **W3-T3 After:** W2-T3 (done, `41f371e`), W1-T5 and the O4 memo's report; not while
   WG-T7 edits `mask_inference.py`. It does not edit `app/config.py`: the fixed sizes are
   named in `app/inference/runtime.py` and pinned to `config` by a test. Its Sci flag holds
   only on the "premise holds" branch.
4. **W3-T2:** two commits (commit 1 not Sci); it does not edit `app/project_layout.py` (the
   project file path lives in `app/calibration.py`); it adds `app/gui/stage_summary.py`,
   `app/gui/stage_status.py`, `app/data_processing/measurement_index.py`,
   `app/gui/window/metrics.py`, `app/gui/metrics_bar.py`, `app/visualization/dash_export.py`
   and `app/gui/about_dialog.py` to its files (memo Q3 and W2-T4's displays), so it waits for
   L-viz / L-misc to have no uncommitted edit in the last two.
5. **Section 1, O3 row:** add "memo Q3: CSVs measured with another field of view or length
   method are flagged outdated, not hidden (lead's default, owner to confirm)".
6. **Section 2, `metrics.py` line:** the memo's positional `calibration` parameter is built
   keyword-only (`calibration=None`); `app/calibration.py` is the single home of the
   length-method name.
7. **Section 5, science order:** W3-T7 (Error text) now lands before WG-T8; the owner is
   told before W3-T2 commit 2, the W3-T3 default switch and W3-T8.
8. **Old W3-T8** ("no code change, document the bias") is superseded by the memo's
   estimator, as section 1 already says.
