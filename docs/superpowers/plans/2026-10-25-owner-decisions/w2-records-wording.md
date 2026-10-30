# Wave 2 — Records and Wording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Read `00-master.md` first (sections 2, 6, 7).

**Goal:** Make every run and every measurement leave a record that says what it read and
how, make "changed since measured" compare content, skip (and name) inputs whose photo is
gone, share one mask threshold, and say on screen what each button, layer and canvas does.

**Architecture:** Three Qt-free record changes (`app/inference/run_record.py` +
`app/provenance.py` for generation runs; a new `app/inference/measure_inputs.py` for the
per-CSV input record; `app/inference/measure_run.py` for Measure provenance) feed the
existing GUI readers (`stage_summary` / `stage_status` / `MeasurementIndex` /
`MetricsBar`). Wording changes are small, pinned by `tests/test_wording_guards.py`. One
task (W2-T11) drops CSV rows and is a science commit.

**Tech Stack:** Python 3.10, PyQt6, OpenCV, NumPy, pytest, ruff. Windows 11 dev box.

**Spec:** `docs/superpowers/plans/2026-10-25-owner-decisions/00-master.md` (decisions O10,
O14, O16, O17, O29, O37, task catalogue, lanes, file ownership) and
`docs/superpowers/plans/2026-10-12-known-issues-resolution.md` (section 3 "Wave 2",
section 2 owner answers). Evidence: `.claude/handoff/issue-audit-2026-10-12/`
(`reproducibility.md` R1, R5, R6, R8; `mask-calibration.md` M2, M3, M5 and "Outside my
package" X2; `perf-editors.md` P6; `length.md` L3, L4; `trace-gt.md` "Outside" X9).

---

## Global Constraints

See `00-master.md` section 2 (venv python only, offscreen pytest, ruff on own files only,
no git by implementers, scope = listed files, CHANGELOG / KNOWN_ISSUES only where a task
says so, byte-identical CSVs outside Sci tasks, `metrics.py` signatures backward
compatible, Qt-free modules, `MainWindow` pin, panel indices, O21 portability). Wave 2
adds:

- `app/inference/measure_inputs.py` (new) imports only the standard library,
  `app.io_utils` and `app.naming`: no `cv2`, `numpy`, `torch` or `PyQt6`
  (`app/data_processing/measurement_index.py` and `app/gui/stage_summary.py` import it).
- `app/provenance.py` stays light (`tests/test_foundation_core_config.py`
  `FOUNDATION_MODULES` / `HEAVY_MODULES`): no `cv2`, `numpy`, `torch`, `PyQt6`.
- New keys in records are additive. Nothing that reads `_sprouts_run.json`,
  `<csv>.provenance.json` or a CSV may depend on column or key order; read by name
  (WG-T8 appends a last CSV column `Source`).
- Measure rows may carry private keys starting with `_` (`_size_px`, `_non_binary`).
  `metrics.write_metric_csv` writes only `headers`, so they never reach a CSV; the
  byte-identity tests pin that.
- `app.config.MASK_THRESHOLD = 0.5` is the **sigmoid** threshold of the mask model and is
  not touched. The shared grey threshold of W2-T6 is `app.masks.store.MASK_THRESHOLD`.
- Stems compare through `app.naming.stem_key(stem: str) -> str` (W1-T5, `stem.lower()`).
  W2-T11 starts after W1-T5 is committed (W2-T2 → W2-T11 on L-measure; W1-T5 is Phase A).
- Real data: `D:\2024 EF image data` is read-only and never listed whole; scripts print
  counts and at most 5 names.

## Code facts settled for this wave (read 2026-10-25, HEAD `19f3761`)

| Question | Answer | Where |
|---|---|---|
| Who writes `_sprouts_run.json` | `run_record.write_run_manifest` (builds `provenance.build_record`, falls back to `minimal_record`, else removes the stale file); called by `mask_inference.generate_masks` (`write_manifest()` closure, line 150; final call line 189) and `skeleton_inference.run_skeleton_generation` (closure line 598; final call after the `try`). A run that raises calls `record_failed_run`, which writes only when `report.written`. | `app/inference/run_record.py:37-92` |
| Why a no-op run replaces it | both call sites call `write_manifest()` after every run that returns, whatever `report.written` is. The no-op rule therefore goes **inside** `run_record.write_run_manifest`: no call-site change. | same |
| Where batch shapes are set | `run_batches` cuts fixed `batch_size` chunks (`batch_engine.py:297`, the tail is shorter); `OomBackoff._run_slice` halves on CUDA OOM (`:195-219`, the halving decision at `:215-219`); `report.effective_batch_size = backoff.size` (`:375`). The forward that actually ran is each `(chunk, result)` from `backoff.run` (`:359`) whose result is not an exception. | `app/inference/batch_engine.py` |
| Freshness paths today | (1) status line: `stage_summary.input_freshness` (`stage_summary.py:85-117`, `mtime > csv mtime`), run in the pool by `StageStatus.check` (`stage_status.py:102-138`); (2) metrics bar: `MeasurementIndex.outdated` / `_table_outdated` (`measurement_index.py:268-295`, same rule; a deleted input returns False), shown by `MetricsBar._show_metric` with `_OUTDATED_TIP` (`metrics_bar.py:42, 331-376`) via `MetricsController.refresh` (`window/metrics.py:101-154`). Triggers: `stages.py:54`, `job_buttons.py:50`, `window/metrics.py:196`. | — |
| The thresholds | Measure: `cv2.threshold(img, 127, 255, THRESH_BINARY)` = grey > 127 (`metrics.py:77`). Correct: `MASK_THRESHOLD = 200`, `gray > MASK_THRESHOLD` (`masks/store.py:33, 253`), docstring `correct/mask_document.py:179` ("grey above 200"). `metrics._mostly_white` (`:48`) and `skeleton_graph_model.py:287` use 127 as the Otsu inversion rule, not a mask threshold: unchanged. | — |
| Canvas path | `canvas_gl.opengl_viewports_allowed()` decides (env opt-out, then a cached GL probe); `canvas_view.py:205` applies it. Nothing on screen says which path is active: the device chip tooltip is built in `StatusLine.refresh_runtime` (`shell_chrome.py:840-859`), About has no row (`about_dialog.py:201-215, 286-291`). The probe is `functools.cache`d (`canvas_gl.py:140-148`). | — |
| What "the image is in the project" means | `ProjectLayout.scan_images()` (`project_layout.py:204-211`; the Library's scan, `image_manager.py:87-98`), compared through `stem_key`. The Library tree is built from images only (`index_names(images)`), so an orphan mask is never listed; only counts and Measure see it. | — |
| How Measure lists its inputs | `SkeletonHandler._measure` (`skeleton_handler.py:128-143`) calls `layout.scan_skeletons(folder)` (length) or `layout.scan_masks(folder, extensions=(".png",))` (area) and passes the whole mapping to `MeasureRun`; no image lookup. | — |

## Review Focus

1. **A project measured before this build** (a CSV with no `<csv>.inputs.json`).
   Expected: the status line and the metrics bar keep the file-time rule, the bar's
   tooltip says the comparison is by file time, and Measure never asks "Measure again?".
   Tests: W2-T2 `test_a_csv_without_a_record_keeps_the_file_time_rule`, W2-T8a
   `test_a_csv_without_a_record_measures_without_asking`.
2. **A copied, synced or unzipped project whose files got new times.** Expected:
   "measured, current" when the content is the same; a repainted file put back with an
   older time is "changed". Tests: W2-T2 `test_restamped_inputs_with_equal_content_are_current`,
   `test_a_repainted_mask_with_an_older_time_is_changed`.
3. **A mask or skeleton whose photo is gone, next to a case-variant stem**
   (`mask/A_T1.png` with photo `a_t1.JPG`). Expected: the case variant is measured; only
   the real orphan is skipped, counted in the end notice and named in the provenance;
   no file is deleted. Test: W2-T11 `test_a_case_variant_stem_is_not_skipped`.
4. **A "Keep existing" Generate run with nothing missing, or a cancel before the first
   batch.** Expected: `_sprouts_run.json` stays byte-identical and no history line is
   added. Tests: W2-T1 `test_a_keep_existing_run_with_nothing_missing_leaves_the_record_byte_identical`,
   `test_a_cancel_before_the_first_batch_leaves_the_record_alone`.
5. **A remote or GPU-less session (no hardware OpenGL).** Expected: one warn toast after
   start-up, the reason in About and in the device chip's tooltip; nothing in offscreen
   test runs. Tests: W2-T9 `test_slow_path_notice_only_off_offscreen`,
   `test_the_canvas_notice_is_one_warn_toast`.

---

## Lane order (from `00-master.md` section 5)

```
L-runrec  : W2-T1 (after W1-T5) → W2-T3
L-measure : W3-T7 → W2-T2 → W2-T11 → WG-T8 → W2-T4 → W2-T6
L-misc    : W2-T9 → W2-T10
L-correct : … WG-T10 → W2-T8a  (also needs W2-T2 and WG-T8; see its section)
```

---

## L-runrec

### Task W2-T1: No-op run keeps `_sprouts_run.json`; runs have a history (R6, O16)

Impl sonnet / high · Rev sonnet / medium · Lane L-runrec · Sci no · After W1-T5.

**Files:**
- Modify: `app/provenance.py:42-67` (`__all__`, constants), add after `:438-440`
  (`run_history_path`, `append_run_history`)
- Modify: `app/inference/run_record.py:1-16` (docstring), `:37-69` (`write_run_manifest`),
  `:116` (`__all__`)
- Modify: `docs/DATA_DICTIONARY.md:228-262` (Provenance records: the no-op rule, the
  history file), `README.md:124-136` (Outputs table: one row)
- Modify: `CHANGELOG.md` `[Unreleased]` → `### Changed` (one line)
- Test: Create `tests/test_run_record_history.py`; Modify
  `tests/test_generation_handlers.py:201-213` (`test_no_existing_masks_means_no_prompt`:
  add `"_sprouts_runs.jsonl"` to the expected listing)

**Interfaces:**
- Consumes: `BatchReport` (`written`, `failed`, `error`, `summary()`), `provenance.build_record`.
- Produces:
  - `provenance.RUN_HISTORY_FILENAME = "_sprouts_runs.jsonl"`
  - `provenance.run_history_path(output_dir: str | os.PathLike[str]) -> str`
  - `provenance.append_run_history(output_dir: str | os.PathLike[str], entry: Mapping[str, Any]) -> str`
    — one compact JSON line (`separators=(",", ":")`, `ensure_ascii=False`,
    `default=_json_default`) + `"\n"`, opened in append mode; returns the path.
  - `run_record.HISTORY_NAMES_LIMIT = 1000`
  - `run_record.history_entry(record: Mapping[str, Any], report: BatchReport) -> dict[str, Any]`
    — keys `created_utc`, `kind`, `app_version`, `git_commit`, `models`
    (`{name: sha256}`), `status`, `n_written`, `n_failed`, `n_skipped`, and either
    `written` (sorted names, when `len(report.written) <= HISTORY_NAMES_LIMIT`) or
    `written_count` + `written_sha256` (`provenance.names_digest(report.written)`).
  - `run_record.write_run_manifest(...)` unchanged signature; returns `None` and writes
    nothing when the run is a no-op: `not report.written and not report.failed and
    report.error is None`. Otherwise it writes the manifest as today, then appends
    `history_entry(record_written, report)`; a history failure is logged and never fails
    the run (same rule as the manifest).

- [ ] **Step 1: Write the failing tests** in `tests/test_run_record_history.py`, reusing
  `_StubMaskModel`, `_images` and the `cpu_runtime` fixture from
  `tests/test_generation_inference.py` (import them; `# noqa: F401` for the fixture).
  Mask runs through `mask_inference.generate_masks(src, out, model=..., batch_size=2, ...)`.
  - `test_a_keep_existing_run_with_nothing_missing_leaves_the_record_byte_identical`:
    run on `a.jpg`, `b.jpg`; read `_sprouts_run.json` bytes; run again with
    `skip_stems=frozenset({"a", "b"})` → bytes equal; `_sprouts_runs.jsonl` has 1 line.
  - `test_a_cancel_before_the_first_batch_leaves_the_record_alone`: second run with
    `should_stop=lambda: True` → manifest bytes equal; history still 1 line.
  - `test_a_partial_run_appends_a_line_and_replaces_the_latest`: run on 3 images;
    delete `mask/c.png`; run with `skip_stems={"a","b"}` → manifest `outputs.n_written == 1`;
    history 2 lines; `json.loads(lines[-1])["written"] == ["c.jpg"]`.
  - `test_an_all_failed_run_still_writes`: inputs `x.jpg`, `y.jpg` both corrupt (`_images(..., corrupt=...)`)
    → manifest `outputs.n_failed == 2`, `status == "completed"`; history 1 line with
    `n_failed == 2`.
  - `test_more_than_the_limit_written_names_are_a_count_and_a_digest`:
    `history_entry(record, BatchReport(written=[f"{i}.jpg" for i in range(1001)]))` has no
    `written`, `written_count == 1001`, `written_sha256 == provenance.names_digest(...)`;
    with 1000 names it has `written` of length 1000.
  - `test_a_history_failure_never_fails_the_run`: monkeypatch
    `provenance.append_run_history` to raise `OSError` → `write_run_manifest` returns the
    manifest path; the warning is logged (`caplog`).

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_run_record_history.py -v`
  Expected: FAIL (`ImportError: history_entry` / byte comparison differs).

- [ ] **Step 3: Implement** the Produces block in `app/provenance.py` and
  `app/inference/run_record.py`. Rewrite the `run_record.py` docstring sentence "The file
  describes the latest run … even with no outputs" to: the file describes the latest run
  that wrote or failed an input; a run that did neither leaves it alone; every such run is
  also one line of `_sprouts_runs.jsonl`. Docs: DATA_DICTIONARY provenance paragraph
  (`:233-249`: remove "completed or cancelled, even with no outputs, for example a *Keep
  existing masks* run with nothing missing"; add the history file and its keys), README
  Outputs row `mask/_sprouts_runs.jsonl`, `skeletons/_sprouts_runs.jsonl` — "one line per
  generation run that wrote or failed an input (the run file holds the latest)".
  CHANGELOG `### Changed`: "A generation run that writes and fails nothing leaves
  `_sprouts_run.json` alone; every other run is appended to `_sprouts_runs.jsonl` (R6, O16)."

- [ ] **Step 4: Run, expect PASS**, plus the tests that must stay green:

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_run_record_history.py tests/test_generation_handlers.py tests/test_generation_failed_run_manifest.py tests/test_robust_run_manifest_fallback.py tests/test_generation_inference.py tests/test_generation_all_failed.py tests/test_foundation_core_provenance.py tests/test_foundation_core_config.py -v`
  Expected: PASS (`test_a_cancelled_run_is_cancelled` still writes: 2 outputs).

- [ ] **Step 5: Ruff** `./venv/Scripts/python.exe -m ruff check --fix app/provenance.py app/inference/run_record.py tests/test_run_record_history.py tests/test_generation_handlers.py` and `ruff format` on the same files.

- [ ] **Step 6: Commit (lead)**
  `git add app/provenance.py app/inference/run_record.py tests/test_run_record_history.py tests/test_generation_handlers.py docs/DATA_DICTIONARY.md README.md CHANGELOG.md`
  `git commit -m "feat(provenance): keep the run record on a no-op run; append runs to a history"`

### Task W2-T3: Run records say which batch shapes ran (R1 step 1)

Impl sonnet / medium · Rev sonnet / low · Lane L-runrec · Sci no · After W2-T1.

**Files:**
- Modify: `app/inference/batch_engine.py:50-102` (`BatchReport`), `:157-227`
  (`OomBackoff`: an `events` list), `:344-377` (`run_batches`: fill both fields)
- Modify: `docs/DATA_DICTIONARY.md:261` (generation `outputs` keys)
- Test: Modify `tests/test_generation_batch_engine.py`
- `app/inference/run_record.py` needs no change: `outputs = report.summary()`.

**Interfaces:**
- Produces:
  - `BatchReport.batch_shapes: dict[int, int]` (default `{}`): forward size (rows of the
    stacked tensor, zero-filled slots included) → number of input slots that ran at that
    size. Only forwards that returned a result count (an `ItemOutOfMemory` slice does not).
  - `BatchReport.oom_events: list[dict[str, Any]]` (default `[]`):
    `{"at": "<input file name of the slice's first slot>", "from": <slice length>, "to": <(length + 1) // 2>}`,
    in the order they happened. A single image that runs out of memory alone is not an
    event (it is a recorded failure, as today).
  - `OomBackoff.events: list[tuple[int, int, int]]` — `(start index in the batch, from, to)`,
    appended where `_run_slice` logs "Out of memory at batch … retrying at …" (`:215-218`).
  - `summary()` gains `"batch_shapes": {str(size): count}` (sizes descending) and
    `"oom_events": [...]`.
- Consumed by: W3-T3 (fixed-shape mode), DATA_DICTIONARY readers.

- [ ] **Step 1: Write the failing tests** in `tests/test_generation_batch_engine.py`:
  - `test_the_report_records_the_batch_shapes`: 7 images, `batch_size=3`, plain `_step`
    → `report.batch_shapes == {3: 6, 1: 1}`, `report.oom_events == []`,
    `report.summary()["batch_shapes"] == {"3": 6, "1": 1}`.
  - `test_the_report_records_out_of_memory_events`: the `oom_step` of
    `test_out_of_memory_halves_the_batch_and_writes_everything` (OOM above 2), 7 images,
    `batch_size=4` → `batch_shapes == {2: 6, 1: 1}`,
    `oom_events == [{"at": "img00.png", "from": 4, "to": 2}]`.
  - `test_zero_filled_slots_count_in_the_forward_shape`: `NAMES + ["bad.jpg"]` with
    `bad.jpg` corrupt, `batch_size=3` (sorted paths put `bad.jpg` first: batches
    `[bad, img00, img01]`, `[img02..img04]`, `[img05, img06]`) →
    `report.batch_shapes == {3: 6, 2: 2}` (the zero-filled `bad.jpg` slot counts in the
    first forward's 3).
  - `test_an_empty_report_has_empty_shapes`: `BatchReport().summary()` has
    `"batch_shapes": {}` and `"oom_events": []`.

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_generation_batch_engine.py -v`
  Expected: FAIL (`AttributeError: 'BatchReport' object has no attribute 'batch_shapes'`).

- [ ] **Step 3: Implement** the Produces block. In `run_batches`, count each
  `(chunk, result)` of `backoff.run(...)` whose `result` is not a `BaseException`, and after
  each `backoff.run` convert the new `backoff.events` with `batch_paths[start]`'s basename.
  Fill the report fields in the `finally` block like `effective_batch_size` (`:375`) so a
  run that raises still records them.

- [ ] **Step 4: Run, expect PASS** with the tests that must stay green:

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_generation_batch_engine.py tests/test_generation_oom_backoff.py tests/test_generation_failed_run_manifest.py tests/test_generation_inference.py tests/test_robust_run_manifest_fallback.py -v`
  Expected: PASS.

- [ ] **Step 5: Ruff** on `app/inference/batch_engine.py tests/test_generation_batch_engine.py`.

- [ ] **Step 6: Commit (lead)**
  `git add app/inference/batch_engine.py tests/test_generation_batch_engine.py docs/DATA_DICTIONARY.md`
  `git commit -m "feat(provenance): record the batch shapes and out-of-memory events of a run"`

---

## L-measure

### Task W2-T2: Measure records its inputs; freshness compares content (R5)

Impl opus / high · Rev opus / medium · Lane L-measure · Sci no · After W3-T7.

**Files:**
- Create: `app/inference/measure_inputs.py`
- Modify: `app/inference/metrics.py:51-78` (`preprocess_image`, `load_mask`: keyword
  `data`), `:176-185` (`length_row`, `area_row`: keyword `data`)
- Modify: `app/inference/measure_run.py:33-44` (`MetricSpec.row` type), `:76-156`
  (`MeasureRun`: fingerprints, `write()` writes the record, `unchanged()`)
- Modify: `app/gui/stage_summary.py:23-30` (`Freshness`), `:54-66` (`_metric_text`),
  `:85-117` (`input_freshness`)
- Modify: `app/gui/stage_status.py:102-138` (`check`: load the record in the pool)
- Modify: `app/data_processing/measurement_index.py:100-200` (`MetricTable`: record,
  signature), `:264-295` (`outdated`, new `by_content`)
- Modify: `app/gui/metrics_bar.py:40-47` (tooltip texts), `:215-274` (`set_metrics`),
  `:331-376` (`_show_metric`)
- Modify: `app/gui/window/metrics.py:101-154` (`refresh`: pass `by_time`)
- Modify: `tests/test_import_smoke.py` (`APP_MODULES`: add `"app.inference.measure_inputs"`)
- Modify: `docs/DATA_DICTIONARY.md` (Metric CSVs: the `.inputs.json` file),
  `README.md:124-136` (Outputs row), `CHANGELOG.md` `[Unreleased]` `### Changed` (one line)
- Test: Create `tests/test_measure_inputs_record.py`; Modify `tests/test_measure_core.py`

**Interfaces:**
- Consumes: `provenance.sidecar_path` pattern, `app.io_utils.atomic_write_text`.
- Produces (`app/inference/measure_inputs.py`):
  - `INPUTS_SUFFIX = ".inputs.json"`
  - `Fingerprint = tuple[int, int, str]` — `(size, mtime_ns, sha256 hex)`
  - `Stamp = tuple[str, int, int]` — `(absolute path, size, mtime_ns)`
  - `inputs_record_path(csv_path: str) -> str` → `csv_path + INPUTS_SUFFIX`
  - `input_key(path: str, project_root: str) -> str` — path relative to `project_root`
    with `/` separators (e.g. `mask/a.png`, `skeletons/a_fake.png`, later `gt/...`).
    **Decision:** keys are project-relative paths, not stems, so the record says which
    file was measured and WG-T8 can add GT inputs without a schema change.
  - `remember_sha256(path: str, size: int, mtime_ns: int, sha256: str) -> None` and
    `content_sha256(path: str, size: int, mtime_ns: int) -> str | None` — session cache
    keyed by `(abspath, size, mtime_ns)`, thread-safe; `content_sha256` hashes on a miss,
    `None` when unreadable.
  - `write_inputs_record(csv_path: str, inputs: Mapping[str, Fingerprint]) -> str` —
    `{"csv_sha256": <sha256 of the CSV bytes on disk>, "inputs": {key: [size, mtime_ns, sha256]}}`,
    written with `atomic_write_text`, keys sorted.
  - `@dataclass(frozen=True) class InputsRecord: inputs: Mapping[str, Fingerprint]`
  - `load_inputs_record(csv_path: str) -> InputsRecord | None` — `None` when the file is
    missing, unreadable, malformed, or its `csv_sha256` differs from the CSV on disk.
  - `scan_stamps(folder: str, suffix: str, project_root: str, *, keep: Callable[[str], bool] | None = None) -> dict[str, Stamp]`
    — one `os.scandir` (entry stat, free on Windows); skips names starting with `.`;
    matches `suffix` case-insensitively as `input_freshness` does; `keep(stem)` filters
    (W2-T11). The stem is the name without `suffix`.
  - `@dataclass(frozen=True) class InputsDiff: inputs: int; changed: int; new: int; removed: int`
  - `diff_inputs(record: InputsRecord, current: Mapping[str, Stamp], *, prefix: str | None = None) -> InputsDiff`
    — key in both: current when `(size, mtime_ns)` equal, else when `content_sha256`
    equals the recorded hash, else changed; key only in `current`: new; key only in
    `record` (restricted to keys starting with `prefix`, e.g. `"mask/"`, when given): removed.
  - `is_unchanged(record: InputsRecord | None, current: Mapping[str, Stamp]) -> bool` —
    True only when `record` is not None, the key sets are equal and no key is changed;
    stops at the first difference.
- Produces (`app/inference/metrics.py`, backward compatible, keyword-only):
  `preprocess_image(image_path: str, *, data: bytes | None = None) -> np.ndarray`,
  `load_mask(image_path: str, *, data: bytes | None = None) -> np.ndarray`,
  `length_row(name: str, path: str, *, data: bytes | None = None) -> dict`,
  `area_row(name: str, path: str, *, data: bytes | None = None) -> dict`. With `data`, the
  grey decode is `cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)` (empty
  `data` → `None` → the same `FileNotFoundError` text), i.e. pixel-identical to
  `imread_unicode`.
- Produces (`app/inference/measure_run.py`):
  - `MetricSpec.row: Callable[..., dict]` (called as `row(name, path, data=...)`)
  - `MeasureRun.fingerprints: dict[str, Fingerprint]` — filled during `compute()`: the
    worker opens the file once, `os.fstat` for size / mtime, reads the bytes, hashes
    them, calls `remember_sha256`, then `spec.row(name, path, data=data)`. An `OSError`
    on open / read calls `spec.row(name, path)` as today (same error row) and records no
    fingerprint.
  - `MeasureRun.write()` order: CSV, provenance sidecar, then `write_inputs_record`
    (best effort: logged, never fails the run).
  - `MeasureRun.unchanged() -> bool` — `is_unchanged(load_inputs_record(self.csv_path),
    stamps of self.images)` with stamps from one `scan_stamps` per distinct parent folder
    of the inputs. Consumed by W2-T8a.
- Produces (GUI side):
  - `Freshness` gains `new: int = 0`, `removed: int = 0`, `by_content: bool = False`
    (appended after `changed`; positional use unchanged).
  - `input_freshness(folder, suffix, csv_path, *, record: InputsRecord | None = None, project_root: str | None = None) -> Freshness`
    — with a record: `scan_stamps` + `diff_inputs(prefix=input_key(folder)+"/")`,
    `by_content=True`; without: today's rule, byte for byte.
  - `measure_text`: by-content phrase `"{n} masks: 36 changed, 2 new, 3 removed since measured"`
    (only the non-zero parts, in that order); none → `"measured, current"`. The
    file-time phrase is unchanged.
  - `MetricTable.record -> InputsRecord | None` (loaded with the CSV in `_current(load=True)`;
    the table's signature becomes the CSV's `(mtime_ns, size)` plus the record file's, so a
    record written after the CSV is picked up).
  - `MeasurementIndex.by_content() -> dict[str, bool]` (`{"length": bool, "area": bool}`).
  - `MeasurementIndex.outdated(stem)` with a record: the input's entry vs its file (stat;
    hash only when the stamp differs); a row whose input file is gone → `True`; no entry
    for an existing file → `True`. Without a record: today's rule.
  - `MetricsBar.set_metrics(..., by_time: bool = False, area_by_time: bool | None = None)`;
    when a value is outdated and `by_time`, its tooltip gets the line
    `"Compared by file time: this CSV has no record of its inputs."`

- [ ] **Step 1: Write the failing tests** in `tests/test_measure_inputs_record.py`.
  Helper `_measured(tmp_path, n=3)`: images `x_T{i+1}_L2_2024.01.02_01020{i}_001.jpg` in the
  root, masks `mask/<stem>.png` (a `cv2.line` on 64 x 80, as `tests/test_measure_handler.py::_project`),
  then `MeasureRun(AREA, layout.scan_masks(extensions=(".png",)), layout.mask_dir, project_root=root)`
  `.compute(); .write()`. Tests:
  - `test_measure_writes_an_inputs_record_beside_the_csv`: `mask/root_areas.csv.inputs.json`
    exists; top-level keys `{"csv_sha256", "inputs"}`; `csv_sha256 == sha256(csv bytes)`;
    `inputs` keys `== {f"mask/{stem}.png", ...}`; each value `== [st.st_size, st.st_mtime_ns, sha256(file bytes)]`.
  - `test_rows_from_bytes_equal_rows_from_the_path` (parametrize length / area):
    `row(n, p) == row(n, p, data=open(p, "rb").read())`; `row(n, missing, data=b"")`
    equals `row(n, missing)` (same `Error`).
  - `test_restamped_inputs_with_equal_content_are_current`: `os.utime` every mask to
    CSV mtime + 100 s → `input_freshness(mask_dir, ".png", csv, record=load_inputs_record(csv), project_root=root)`
    `== Freshness(3, True, 0, new=0, removed=0, by_content=True)`.
  - `test_regenerated_identical_files_are_current`: rewrite each mask with its own bytes
    → `changed == 0`.
  - `test_a_repainted_mask_with_an_older_time_is_changed`: change one mask's pixels, set
    its mtime to CSV mtime − 3600 s → `changed == 1`; `MeasurementIndex(root)` after
    `prewarm()`: `outdated(stem)["area"] is True`, other stems `False`.
  - `test_a_deleted_input_is_reported_removed`: delete one mask → `inputs == 2`,
    `removed == 1`; `outdated(deleted_stem)["area"] is True`.
  - `test_a_new_input_is_reported_new`: add `mask/new.png` → `new == 1`.
  - `test_a_record_for_another_csv_is_ignored`: append `"\n"` to the CSV →
    `load_inputs_record(csv) is None`; `input_freshness(...)` with that `None` has
    `by_content is False`.
  - `test_a_csv_without_a_record_keeps_the_file_time_rule`: delete the `.inputs.json`;
    touch one mask newer than the CSV → `Freshness(3, True, 1)`; `by_content()["area"] is False`.
  - `test_hashes_are_cached_for_the_session`: restamp all masks; count calls of
    `measure_inputs._hash_file` (monkeypatch a counter) over two `input_freshness` passes
    → `3` (not 6).
  - `test_measure_text_names_changed_new_and_removed`:
    `measure_text(0, 0, area=Freshness(46175, True, 36, removed=3, by_content=True))`
    endswith `"46,175 masks: 36 changed, 3 removed since measured"`.
  - `test_the_metrics_bar_says_when_the_comparison_is_by_file_time` (QApplication
    fixture as `tests/test_metrics_bar_smoke.py`): `set_metrics(length=1.0, stale=True, by_time=True, source="x.csv")`
    → the LENGTH cell tooltip contains `"Compared by file time"`; with `by_time=False` it does not.
  - `test_unchanged_is_true_only_when_every_input_matches`: right after `write()`,
    `run.unchanged() is True`; after repainting one mask → `False`; after adding a mask
    (and passing a new `MeasureRun` with that input) → `False`; with the record deleted →
    `False`.
  In `tests/test_measure_core.py` add `test_the_record_does_not_touch_the_csv`: the CSV
  bytes with the record step equal the bytes with `measure_inputs.write_inputs_record`
  monkeypatched to raise (and the run does not fail).

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_measure_inputs_record.py tests/test_measure_core.py -v`
  Expected: FAIL (`ModuleNotFoundError: app.inference.measure_inputs`).

- [ ] **Step 3: Implement** the Produces blocks. `StageStatus.check.work()` loads
  `load_inputs_record(csv)` per metric in the pool and passes `record=`, `project_root=layout.root`.
  `MetricsController.refresh` passes `by_time=not by_content["length"]`,
  `area_by_time=not by_content["area"]` (only when the index exists). Docs: DATA_DICTIONARY
  "Metric CSVs" one paragraph (the file, its keys, that a check compares size and time
  first and content only when they differ, that a record whose `csv_sha256` does not match
  is ignored), README Outputs row `<csv>.inputs.json`, update the R5 sentence of the
  `MeasurementIndex.outdated` docstring. CHANGELOG `### Changed`: "Measure records each
  input's size, time and SHA-256 in `<csv>.inputs.json`; 'changed since measured' compares
  content when the times differ and reports new and removed inputs (R5)."

- [ ] **Step 4: Run, expect PASS** with the tests that must stay green:

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_measure_inputs_record.py tests/test_measure_core.py tests/test_measure_handler.py tests/test_measure_threads.py tests/test_shell_stage_summary.py tests/test_shell_metrics_per_metric_staleness.py tests/test_shell_info_measurement_index.py tests/test_stage_status_cache.py tests/test_metrics_bar_smoke.py tests/test_robust_measure_speed.py tests/test_error_column.py tests/test_import_smoke.py tests/test_foundation_core_config.py tests/test_layering_contract.py -v`
  Expected: PASS, including `test_measure_csv_is_byte_identical_to_legacy_pipeline[length|area]`
  and `test_real_sample_is_byte_identical_to_legacy_pipeline[length|area]`.

- [ ] **Step 5: Ruff** on every file of the Files list (`.py` only).

- [ ] **Step 6: Commit (lead)**
  `git add app/inference/measure_inputs.py app/inference/metrics.py app/inference/measure_run.py app/gui/stage_summary.py app/gui/stage_status.py app/data_processing/measurement_index.py app/gui/metrics_bar.py app/gui/window/metrics.py tests/test_measure_inputs_record.py tests/test_measure_core.py tests/test_import_smoke.py docs/DATA_DICTIONARY.md README.md CHANGELOG.md`
  `git commit -m "feat(measure): record the measured inputs; freshness compares content, not only times"`

### Task W2-T11: Skip and record inputs without an image, both metrics (X2, O37) — **Sci**

Impl sonnet / high · Rev opus / medium · Lane L-measure · **Sci yes (rows dropped)** ·
After W2-T2 (and W1-T5 for `stem_key`).

**Files:**
- Modify: `app/inference/measure_run.py:76-99` (`MeasureRun.__init__`: `without_image`),
  `:133-156` (`_write_provenance`), new module function `split_by_image`
- Modify: `app/handlers/skeleton_handler.py:128-143` (`_measure`: split, all-orphan
  warning, pass `without_image`), `:165-182` (`_on_finished`: the skipped notice)
- Modify: `app/gui/stage_summary.py` (`Freshness.without_image`, `input_freshness(images=)`,
  `_metric_text`), `app/gui/stage_status.py:124-136` (`work()`: `layout.scan_images()` once)
- Modify: `docs/DATA_DICTIONARY.md` (Metric CSVs: rows exist only for inputs whose image is
  in the project), `README.md` (Outputs paragraph after the table: one sentence),
  `CHANGELOG.md` `### Scientific output changes`
- Test: Create `tests/test_measure_without_image.py`; Modify
  `tests/test_robust_saved_notice.py:98-108`
  (`test_a_measure_with_unreadable_images_still_posts_the_full_path`: also write
  `x_T9_L2_2024.01.02_010209_001.jpg` in the root, so the corrupt skeleton is still
  measured and the test keeps testing an Error row)

**Interfaces:**
- Consumes: `app.naming.stem_key(stem: str) -> str` (W1-T5), `ProjectLayout.scan_images()`,
  W2-T2's `MeasureRun` and `scan_stamps(keep=...)`.
- Produces:
  - `measure_run.WITHOUT_IMAGE_NAMES_LIMIT = 100`
  - `measure_run.split_by_image(inputs: Mapping[str, str], image_stems: Iterable[str]) -> tuple[dict[str, str], list[str]]`
    — keeps `name → path` whose `stem_key(name)` is in `{stem_key(s) for s in image_stems}`;
    the second item is the sorted file names (`os.path.basename(path)`) of the rest.
  - `MeasureRun(spec, images, output_dir, *, project_root=None, without_image: Sequence[str] = ())`
    — provenance `inputs.n_without_image = len(without_image)` and
    `inputs.without_image = sorted names[:100]` (both always present; `0` / `[]`).
  - `Freshness.without_image: int = 0`; `input_freshness(..., images: Collection[str] | None = None)`
    — files whose stem has no image are not inputs and are counted in `without_image`;
    `_metric_text` appends `" ({n:,} without an image: skipped)"` when non-zero.
  - Handler texts (exact):
    - end notice, `"warn"`: `"{n:,} {noun} skipped: {its|their} image is no longer in the project (named in {csv}.provenance.json)"`,
      `noun` = `"mask"` / `"masks"` (area), `"skeleton"` / `"skeletons"` (length).
    - nothing left to measure: `"No images to measure in {folder}: its {n:,} {noun} have no image in the project."`
      (`"has"` for one), no job started.
  - WG-T8 consumes `split_by_image` for GT files (`gt/`) too.

- [ ] **Step 1: Write the failing tests** in `tests/test_measure_without_image.py` (reuse
  `_project`, `_handler`, `_wait_idle`, `qapp` from `tests/test_measure_handler.py`):
  - `test_split_by_image_keeps_inputs_with_an_image`:
    `split_by_image({"a": "m/a.png", "B": "m/B.png", "z": "m/z.png"}, ["A", "b"])`
    `== ({"a": "m/a.png", "B": "m/B.png"}, ["z.png"])`.
  - `test_area_skips_a_mask_without_an_image` : `_project(tmp_path, n=4, skeletons=False)`,
    plus `mask/orphan_T9_L2_2024.01.02_010209_001.png`; `calculate_root_area()` → CSV has
    4 data rows; the orphan file still exists with its bytes; sidecar
    `inputs.n_without_image == 1`, `inputs.without_image == ["orphan_T9_L2_2024.01.02_010209_001.png"]`;
    a warn notify equals `"1 mask skipped: its image is no longer in the project (named in root_areas.csv.provenance.json)"`.
  - `test_length_skips_a_skeleton_without_an_image`: same for
    `skeletons/orphan_…_fake.png` → 4 rows, notice with `"1 skeleton skipped"`.
  - `test_a_case_variant_stem_is_not_skipped`: image `x_T1_L2_2024.01.02_010200_001.jpg`,
    mask `mask/X_T1_L2_2024.01.02_010200_001.png` → 1 row, `n_without_image == 0`, no
    skipped notice.
  - `test_only_orphans_starts_no_job`: masks only, no images →
    `mw.jobs.running_name() is None`, warn `"No images to measure in … its 2 masks have no image in the project."`
  - `test_the_measure_status_counts_only_inputs_with_an_image`: `input_freshness(mask_dir, ".png", None, images=["a"])`
    over `a.png`, `b.png` → `Freshness(1, False, without_image=1)`;
    `measure_text(0, 0, area=that)` ends with `"1 mask: not measured yet (1 without an image: skipped)"`.

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_measure_without_image.py -v`
  Expected: FAIL (`ImportError: split_by_image`).

- [ ] **Step 3: Implement** the Produces block. In `_measure`, after `scan(folder)`:
  `kept, orphans = split_by_image(images, layout.scan_images())` (one more scandir of the
  root, ~60 ms at 46k, as the scan itself). Pass `without_image=orphans`; the all-orphan
  case warns and returns. `StageStatus.check.work()` computes `layout.scan_images()` once and
  passes `images=` to both `input_freshness` calls. Files are never deleted or moved.

- [ ] **Step 4: Run, expect PASS** with the tests that must stay green:

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_measure_without_image.py tests/test_measure_handler.py tests/test_robust_measure_legacy_notice.py tests/test_robust_saved_notice.py tests/test_measure_core.py tests/test_measure_inputs_record.py tests/test_shell_stage_summary.py tests/test_stage_status_cache.py tests/test_measure_threads.py -v`
  Expected: PASS.

- [ ] **Step 5: Before / after counts for the CHANGELOG.** Write
  `<scratchpad>/count_without_image.py` (outside the repo): argv[1] = project folder;
  prints `images N`, `masks N, without an image M`, `skeletons N, without an image M` and
  at most 5 names each, using `ProjectLayout.scan_images / scan_masks(extensions=(".png",)) / scan_skeletons`
  and `stem_key`. Run it read-only on `D:\2024 EF image data` and on the synthetic fixture
  of `test_area_skips_a_mask_without_an_image` (before 5 rows, after 4). The audit's
  3000-pair sample (`scripts/length/sample_real.py`) copies no photos, so every row of it
  would be skipped: use the real project's counts instead, and say so in the entry.
  CHANGELOG `### Scientific output changes`: "Measure skips masks and skeletons whose image
  is no longer in the project (O37): no row is written for them; the provenance lists them
  (`inputs.n_without_image`, up to 100 names) and the end notice counts them; the files stay
  on disk. Real project (46,178 photos): masks M of 46,175, skeletons M of 46,178 rows
  dropped. Synthetic: 5 → 4 rows." with the measured M values.

- [ ] **Step 6: Ruff** on the `.py` files of the Files list.

- [ ] **Step 7: Commit (lead)**
  `git add app/inference/measure_run.py app/handlers/skeleton_handler.py app/gui/stage_summary.py app/gui/stage_status.py tests/test_measure_without_image.py tests/test_robust_saved_notice.py docs/DATA_DICTIONARY.md README.md CHANGELOG.md`
  `git commit -m "feat(measure): skip and record masks and skeletons whose image is no longer in the project"`

### Task W2-T4: Record measured pixel sizes; warn on mixed sizes; show the CSV's own calibration (M2 step 1)

Impl sonnet / high · Rev opus / medium · Lane L-measure · Sci no · After WG-T8.

Runs after WG-T8, which appends the CSV column `Source` and may add GT inputs: read rows,
records and CSV columns by name only.

**Files:**
- Modify: `app/inference/metrics.py:144-185` (`_metric_row` and the two row functions:
  private key `_size_px`)
- Modify: `app/inference/measure_run.py` (`_write_provenance`: `inputs.sizes_px`; new
  `sizes_px`, `csv_calibration`)
- Modify: `app/provenance.py` (new `read_sidecar`)
- Modify: `app/handlers/skeleton_handler.py:165-182` (`_on_finished`: mixed-size warning)
- Modify: `app/visualization/dash_export.py:165-198` (`provenance_lines`: calibration line)
- Modify: `app/gui/metrics_bar.py:50-53` (`fov_text`), `:135-160` (FOV cell: a setter)
- Modify: `app/data_processing/measurement_index.py` (`MetricTable`: the CSV's calibration,
  loaded with the CSV in `_current(load=True)`; `MeasurementIndex.calibrations()`)
- Modify: `app/gui/window/metrics.py:101-154` (`refresh`: show the CSV's FOV)
- Modify: `app/gui/about_dialog.py:120-123, 201-215, 286-288` ("Measured with" row)
- Modify: `README.md:154-166` (Calibration notes: one sentence), `docs/DATA_DICTIONARY.md`
  (provenance `inputs.sizes_px`)
- Test: Modify `tests/test_measure_core.py`, `tests/test_viz_dash_export.py:86`,
  `tests/test_metrics_bar_smoke.py`, `tests/test_shell_info_about.py`,
  `tests/test_measure_handler.py`

**Interfaces:**
- Produces:
  - Rows carry `_size_px: "WxH"` (the decoded image; absent on an Error row).
  - `measure_run.sizes_px(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]` —
    `{"640x480": 2880, ...}`, most common first.
  - provenance `inputs.sizes_px` (that dict).
  - `provenance.read_sidecar(output_path: str | os.PathLike[str]) -> dict[str, Any] | None`
    — the parsed `<output>.provenance.json`, `None` when missing or malformed.
  - `measure_run.csv_calibration(csv_path: str) -> dict[str, float] | None` — the
    sidecar's `calibration` (`fov_width_mm`, `fov_height_mm`), `None` without one.
  - `metrics_bar.fov_text(calibration: Mapping[str, float] | None = None) -> str` —
    default the running `CALIBRATION` (today's text, `"18×13 mm"`).
  - `MetricsBar.set_fov(text: str, tooltip: str = "") -> None`.
  - `MeasurementIndex.calibrations() -> dict[str, dict[str, float] | None]` (`length`, `area`).
  - Mixed-size warning (exact, `"warn"`): `"The measured files have {k} sizes ({size} ×{n:,}, …): one {fov} field of view was applied to each."`
    with at most 3 sizes listed, most common first; `{fov}` = `fov_text()`.
  - Export calibration line: with a sidecar
    `"field of view 18.00 x 13.00 mm, as recorded with root_lengths.csv (mm per pixel = field of view / image resolution)"`,
    plus `"; this app now uses W x H mm"` when it differs from the running calibration;
    without one `"field of view 18.00 x 13.00 mm, this app's: no record next to root_lengths.csv (mm per pixel = field of view / image resolution)"`.
  - Metrics bar FOV cell: the shown value's CSV calibration (length's, else area's), tooltip
    `"Field of view recorded with <csv name>"` and, when it differs, a second line
    `"This app now uses <fov_text()>"`; no CSV → the running one, tooltip `"This app's field of view"`.
  - About: a `"Measured with"` row under `"Calibration"` only when an open project's CSV
    calibration differs from the running one (`FOV <fov_text(cal)> (<csv name>)`), read
    from the parent window duck-typed (`getattr(parent, "metrics", None)` → `.index` →
    `calibrations()`); no `main_window.py` change.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_measure_core.py::test_the_sidecar_records_the_measured_sizes`: two masks
    64 x 80 and one 48 x 96 → sidecar `inputs.sizes_px == {"80x64": 2, "96x48": 1}`; CSV
    bytes unchanged against a run with `_size_px` stripped (byte-identity tests stay).
  - `tests/test_measure_handler.py::test_mixed_sizes_warn_once`: `_project(n=2)` + one
    mask of another size → exactly one warn notify starting `"The measured files have 2 sizes"`;
    same-size project → none.
  - `tests/test_viz_dash_export.py:86`: assert
    `header["calibration"].startswith("field of view 18.00 x 13.00 mm")`; new
    `test_the_export_takes_the_calibration_from_the_csv_record`: write a sidecar with
    `fov_height_mm: 13.5` next to the source CSV → the line contains `"18.00 x 13.50 mm, as recorded with"`
    and `"; this app now uses 18.00 x 13.00 mm"`.
  - `tests/test_metrics_bar_smoke.py::test_fov_text_formats_a_given_calibration`:
    `fov_text({"fov_width_mm": 18.0, "fov_height_mm": 13.5}) == "18×13.5 mm"`; `set_fov("18×13.5 mm", "x")`
    sets `_fov_value.text()` and the cell tooltip.
  - `tests/test_shell_info_about.py::test_about_names_a_differing_measured_calibration`:
    parent stub with `metrics.index.calibrations()` → `{"length": {"fov_width_mm": 18.0, "fov_height_mm": 13.5}, "area": None}`
    → `_texts(dlg)` contains `"Measured with"` and `"18×13.5 mm"`; equal calibration → no row.

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_measure_core.py tests/test_measure_handler.py tests/test_viz_dash_export.py tests/test_metrics_bar_smoke.py tests/test_shell_info_about.py -v`
  Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** the Produces block. README Calibration notes: "Each CSV's
  `.provenance.json` records the field of view it was measured with (`calibration`) and
  the pixel sizes of the measured files (`inputs.sizes_px`); the dashboards' export, the
  metrics bar and About show that recorded value."

- [ ] **Step 4: Run, expect PASS** (the command of Step 2 plus
  `tests/test_measure_inputs_record.py tests/test_measure_without_image.py tests/test_shell_info_measurement_index.py tests/test_about_runtime_provenance.py tests/test_small_texts_2026_10_metrics_bar.py tests/test_foundation_core_config.py`).

- [ ] **Step 5: Ruff** on the `.py` files of the Files list.

- [ ] **Step 6: Commit (lead)** — `git add` the Files list;
  `git commit -m "feat(measure): record pixel sizes, warn on mixed sizes, show the CSV's own calibration"`

### Task W2-T6: One mask threshold, 127; count non-binary masks (M5, O10)

Impl sonnet / medium · Rev sonnet / medium · Lane L-measure · Sci: no CSV value; the
editor now reads grey pixels 128-200 of a non-binary mask as root (see Step 5) · After W2-T4.

**Files:**
- Modify: `app/masks/store.py:15-17` (docstring), `:32-33` (`MASK_THRESHOLD = 127`)
- Modify: `app/inference/metrics.py:72-78` (`load_mask` uses `MASK_THRESHOLD`;
  `area_row` sets `_non_binary`)
- Modify: `app/inference/measure_run.py` (`_write_provenance`: `outputs.n_non_binary`;
  `AREA.parameters["binarization"]` built from the constant, value unchanged `"gray > 127"`)
- Modify: `app/handlers/skeleton_handler.py:165-182` (one warning)
- Modify: `app/gui/correct/mask_document.py:179` (docstring "grey above 200" →
  "grey above `MASK_THRESHOLD` (127)") — **L-correct file**: the lead dispatches this task
  only when L-correct has no uncommitted edit in it.
- Any GT binary-mask reader added by WG-T1 / WG-T2 (`app/gt/*`) that thresholds grey:
  import the same constant (the implementer greps `> 200`, `200`, `MASK_THRESHOLD` under
  `app/` and lists each hit in the report).
- Modify: `docs/DATA_DICTIONARY.md:95-140` (masks: the one threshold, the alpha rule:
  alpha is ignored, the grey value decides), `CHANGELOG.md` `### Changed` (Step 5)
- Test: Modify `tests/test_mask_editor_store.py:130-163`, `tests/test_measure_core.py`,
  `tests/test_measure_handler.py`

**Interfaces:**
- Produces: `app.masks.store.MASK_THRESHOLD == 127` (re-exported by `app.masks`), imported
  by `app.inference.metrics`; rows of `area_row` carry `_non_binary: True` when the grey
  decode holds a value other than 0 and 255; provenance `outputs.n_non_binary: int` (area
  CSVs; absent for length); warning text (exact, `"warn"`):
  `"{n:,} {mask|masks} {has|have} grey levels: pixels above 127 were counted as root."`

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_mask_editor_store.py`: rename `test_read_of_a_grey_model_mask_thresholds_at_200`
    → `test_read_of_a_grey_model_mask_thresholds_at_127`, grey `[0, 127, 128, 200, 255]` →
    `[False, False, True, True, True]`; in `test_read_of_coloured_pixels_uses_their_luma`
    the expected list becomes `[True, True, True, True, True, False, False]` (green luma
    about 150 and grey 200 are now root; update the comments).
  - `tests/test_mask_editor_store.py::test_measure_and_the_mask_store_agree_on_a_grey_mask`:
    a 16 x 16 grey PNG holding 0..255 once each, as `mask/a.png` →
    `np.count_nonzero(metrics.load_mask(p) == 255) == np.count_nonzero(MaskStore.for_image(img).read("a")) == 128`.
  - `tests/test_measure_core.py::test_the_non_binary_count_is_recorded`: two masks, one
    0/255, one with a pixel of 100 → area sidecar `outputs.n_non_binary == 1`; the CSV
    bytes equal a run without the key.
  - `tests/test_measure_handler.py::test_grey_masks_warn_once`: one grey mask in
    `_project` → one warn notify `"1 mask has grey levels: pixels above 127 were counted as root."`

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_mask_editor_store.py tests/test_measure_core.py tests/test_measure_handler.py -v`

- [ ] **Step 3: Implement** the Produces block.

- [ ] **Step 4: Run, expect PASS** with: `tests/test_mask_editor_store.py tests/test_mask_store_grey_fast_path.py tests/test_measure_core.py tests/test_measure_handler.py tests/test_correct_goldens.py tests/test_correct_mask_goldens.py tests/test_correct_skeleton_goldens.py tests/test_correct_goldens_data.py tests/test_generation_inference.py tests/test_foundation_core_config.py`.
  The goldens must pass unchanged (`tests/data/correct_goldens.json` untouched): app-written
  masks are 0/255.

- [ ] **Step 5: CHANGELOG** `### Changed` (not under Scientific output: no CSV value and no
  app-written mask changes): "Correct reads a mask at Measure's threshold (grey > 127,
  was > 200), so the editor and Measure see the same root pixels. Only masks with grey
  levels (legacy JPEG masks, files from other programs) open differently; saving one in
  Correct then writes those pixels as root. Measure records `outputs.n_non_binary` and
  warns when masks have grey levels (M5, O10)."

- [ ] **Step 6: Ruff** on the `.py` files.

- [ ] **Step 7: Commit (lead)** — `git add` the Files list;
  `git commit -m "fix(masks): Correct reads masks at Measure's threshold; Measure counts non-binary masks"`

---

## L-misc

### Task W2-T9: Say which canvas path is active: About row, chip line, warn toast (P6, O29)

Impl sonnet / medium · Rev sonnet / low · Lane L-misc · Sci no · After —.

Touches `app/gui/shell_chrome.py` (L-correct, `refresh_runtime` only) and
`app/gui/window/body.py`: the lead dispatches it while L-correct has no uncommitted edit in
`shell_chrome.py` (Phase A, before WG-T5).

**Files:**
- Modify: `app/gui/canvas_gl.py:140-156` (`canvas_line`, `slow_path_notice`; the "off"
  log at WARNING when the user did not opt out)
- Modify: `app/gui/about_dialog.py:201-215` (`details_text`: `Canvas:` line),
  `:286-288` (`"Canvas"` row under `"Runtime"`)
- Modify: `app/gui/shell_chrome.py:853-858` (`refresh_runtime`: tooltip line)
- Modify: `app/gui/window/body.py:106-113` (end of `init_ui`: schedule the notice) + new
  function `notify_canvas_path`
- Modify: `README.md` (Running: one sentence on `ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT` and
  the About row), `CHANGELOG.md` `### Added`
- Test: Modify `tests/test_canvas_gl.py`, `tests/test_shell_info_about.py`,
  `tests/test_shell_device_check.py:219-230`

**Interfaces:**
- Produces:
  - `canvas_gl.canvas_line() -> tuple[str, bool]` — `(text, slow)`, from the cached
    `opengl_support()` (no probe when the env opts out):
    `("OpenGL · <renderer or reason>", False)`;
    `("Software drawing · turned off by ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", False)`;
    `("Software drawing (slower) · <reason>", True)`.
  - `canvas_gl.slow_path_notice(platform: str) -> str | None` —
    `"Canvases draw without OpenGL (slower): <reason>. See About."` when `slow` and
    `platform not in {"offscreen", "minimal"}`, else `None`.
  - `body.notify_canvas_path(window) -> bool` — calls
    `slow_path_notice(QGuiApplication.platformName())`; when a text comes back,
    `window.notify(text, "warn")` and returns True. Scheduled once with
    `QTimer.singleShot(0, ...)` at the end of `init_ui` (after `window.feedback`). Not
    attached to `MainWindow` (the API pin is unchanged).
  - Device chip tooltip: `"Inference runtime: <text>"` + optional note lines + a last
    line `"Canvas: <canvas_line()[0]>"`.
  - About: row `"Canvas"` = `canvas_line()[0]`; `details_text` line `"Canvas: <text>"`
    after `"Runtime:"`.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_canvas_gl.py` (patch `canvas_gl.opengl_support` and the env var, as the
    existing tests do):
    `test_canvas_line_names_the_renderer` → `("OpenGL · NVIDIA GeForce RTX 5070 Ti", False)` for
    `GLSupport(True, "hardware OpenGL", "NVIDIA GeForce RTX 5070 Ti")`;
    `test_canvas_line_says_the_user_turned_opengl_off` (env `"0"`, `opengl_support`
    patched to fail the test if called) → `("Software drawing · turned off by ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT", False)`;
    `test_canvas_line_flags_an_unchosen_slow_path` → `("Software drawing (slower) · software renderer 'llvmpipe'", True)`;
    `test_slow_path_notice_only_off_offscreen` → `slow_path_notice("windows") == "Canvases draw without OpenGL (slower): software renderer 'llvmpipe'. See About."`,
    `slow_path_notice("offscreen") is None`, and `None` for the two non-slow states;
    `test_the_canvas_notice_is_one_warn_toast`: `body.notify_canvas_path(SimpleNamespace(notify=record))`
    with `slow_path_notice` patched → one call `(text, "warn")`, returns True; patched to
    `None` → no call, False.
  - `tests/test_shell_info_about.py::test_details_and_dialog_name_the_canvas_path`
    (`canvas_line` patched to `("OpenGL · Fake GPU", False)`) → `"Canvas: OpenGL · Fake GPU"`
    in `details_text()`; `"OpenGL · Fake GPU"` in `_texts(AboutDialog())`.
  - `tests/test_shell_device_check.py::test_the_statusline_state_api`: patch
    `canvas_gl.canvas_line` to `("OpenGL · Fake GPU", False)`; the tooltip assertion
    becomes `== "Inference runtime: GPU · Fake RTX · CUDA 12.8 · TF32\nCanvas: OpenGL · Fake GPU"`.

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_canvas_gl.py tests/test_shell_info_about.py tests/test_shell_device_check.py -v`

- [ ] **Step 3: Implement** the Produces block. CHANGELOG `### Added`: "About, the
  device chip's tooltip and (when OpenGL was not turned off by the user) one warning say
  whether the canvases draw with OpenGL (P6, O29)."

- [ ] **Step 4: Run, expect PASS** with: `tests/test_canvas_gl.py tests/test_canvas_views.py tests/test_shell_info_about.py tests/test_about_runtime_provenance.py tests/test_shell_device_check.py tests/test_shell_device_check_idle.py tests/test_device_chip_fit.py tests/test_warmup_device_chip.py tests/test_integration_main_window_wiring.py tests/test_shell_window_split_surface.py`.

- [ ] **Step 5: Ruff** on the `.py` files.

- [ ] **Step 6: Commit (lead)** — `git add app/gui/canvas_gl.py app/gui/about_dialog.py app/gui/shell_chrome.py app/gui/window/body.py tests/test_canvas_gl.py tests/test_shell_info_about.py tests/test_shell_device_check.py README.md CHANGELOG.md`;
  `git commit -m "feat(shell): About and the device chip say whether the canvases use OpenGL"`

### Task W2-T10: Write down the EXIF policy and pin it (M3)

Impl sonnet / medium · Rev sonnet / low · Lane L-misc · Sci no · After W2-T9.
The GT export `exif_orientation` column is WG-T9's, not this task's.

**Files:**
- Modify: `README.md:154-166` (Calibration & measurement notes: the policy paragraph)
- Modify: `docs/DATA_DICTIONARY.md:8-35` (Project folder: one line)
- Test: Create `tests/test_exif_policy.py`

**Interfaces:** none produced. Consumes `mask_inference.generate_masks`,
`skeleton_inference.run_skeleton_generation(input_dir, output_dir, *, batch_size=None, model=None, ...)`,
`app.enhancement.DECODE_FLAGS`, `app.io_utils.imread_unicode`, the `cpu_runtime` fixture of
`tests/test_generation_inference.py`.

- [ ] **Step 1: Write the tests** in `tests/test_exif_policy.py`. Helper
  `_tagged_jpeg(path)`: 640 x 480 RGB, value 60, a 160 x 120 block of 235 in the stored
  top-left, saved by Pillow with EXIF `0x0112 = 6` (`quality=95`; the
  `tests/test_enhance_core.py::exif_rotated_jpeg` recipe plus a block, since generation
  resizes to fixed sizes and the shape alone proves nothing). Helper `_corner(gray)`: the
  brightest quarter-corner.
  - `test_the_tag_is_real`: `cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)`
    has shape `(640, 480, 3)` (OpenCV's default applies it).
  - `test_mask_generation_keeps_the_stored_grid`: `generate_masks(src, out, model=Stub())`
    with `Stub.forward = lambda x: (x[:, 0:1] > 1.0).float()` → `mask/<stem>.png` shape
    `(480, 640)`, `_corner == "top-left"`.
  - `test_skeleton_generation_keeps_the_stored_grid`: `run_skeleton_generation(src, out, model=SkelStub(), batch_size=1)`
    with `run_batch = lambda x: x` → `<stem>_fake.png` shape `(256, 341)`, `_corner == "top-left"`.
  - `test_the_library_and_correct_decode_keep_the_stored_grid`:
    `imread_unicode(p, DECODE_FLAGS).shape == (480, 640, 3)` and top-left corner.

- [ ] **Step 2: Run** `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_exif_policy.py -v`.
  Expected: PASS (the policy already holds; these tests pin it). If one fails, stop and
  report: a reader applies EXIF, which contradicts the audit.

- [ ] **Step 3: Docs.** README Calibration notes, new paragraph: "**Orientation.** Every
  stage uses the image's stored pixel grid; EXIF orientation is never applied. A photo
  with an orientation tag is shown as stored (not as a photo viewer shows it), and its
  mask, skeleton and GT line up with it. The field of view belongs to the sensor's stored
  axes, so this is also the calibrated orientation." DATA_DICTIONARY Project folder: "Images
  are read in their stored orientation (EXIF orientation ignored)."

- [ ] **Step 4: Ruff** on `tests/test_exif_policy.py`.

- [ ] **Step 5: Commit (lead)** — `git add tests/test_exif_policy.py README.md docs/DATA_DICTIONARY.md`;
  `git commit -m "test(exif): pin the stored-grid policy and write it down"`

---

## L-correct (end)

### Task W2-T8a: Measure Length / Measure Area, "Measure again?", the Generate sentence, one `_fake` constant, the Correct empty state (R8, O17, O14, L4, X9)

Impl sonnet / medium · Rev sonnet / low · Lane L-correct (end) · Sci no ·
After W2-T2, WG-T5 **and WG-T8** (the tooltips name the GT-first inputs; see the report
to the lead). Kept away from Trace GT strings and from `app/gui/correct/layer_panel.py`
(WG-T10 owns them).

Cross-lane files: `app/handlers/skeleton_handler.py`, `app/inference/metric_thread.py`,
`app/inference/measure_run.py` (L-measure) and `app/inference/skeleton_inference.py:57`
(L-runrec). The lead dispatches this task when L-measure and L-runrec have no uncommitted
edit in those files. Line numbers below are HEAD `19f3761`; Wave G moves some: find by the
quoted text.

**Files:**
- Modify: `app/gui/ui_panels.py:214-221` (`generate_button`: text "Skeletons only" and
  its tooltip moved here from `shell_chrome.py:363-369`), `:224-231` (`generate_all_button`
  tooltip), `:234-248` (the two Measure buttons)
- Modify: `app/gui/shell_chrome.py:113-118` (Generate stage blurb), `:363-369` (remove the
  `generate_button.setText` / `setToolTip` override, now in `ui_panels.py`)
- Modify: `app/gui/window/stages.py:126,130` (button names), `app/gui/visualization_manager.py:120,133,142`
- Modify: `app/inference/metric_thread.py:16-56` (`unchanged` signal), `app/inference/measure_run.py`
  (`MeasureRun(..., ask_if_unchanged: bool = False)`), `app/handlers/skeleton_handler.py:145-162`
  (`_start`: connect), new `_on_unchanged` / `_ask_measure_again`, `:128-143` (first run
  asks)
- Modify (L4): `app/inference/skeleton_inference.py:57` (`from app.project_layout import SKELETON_SUFFIX`;
  the name stays importable), `app/gui/image_manager.py:389`, `app/gui/window/editors.py:104`,
  `app/inference/measure_run.py:56` (`f"generated skeletons (*{SKELETON_SUFFIX})"`, same value)
- Modify (X9): `app/gui/correct/chip.py:42-43` (`chip_content`)
- Modify: `README.md` (Stages table Generate and Measure rows; Outputs: the `_fake`
  sentence), `docs/DATA_DICTIONARY.md` (Skeletons: the `_fake` sentence), `CHANGELOG.md`
  `### Changed`
- Test: Create `tests/test_wording_guards.py`; Modify `tests/test_measure_handler.py`,
  `tests/test_correct_status_chip.py`

**Interfaces:**
- Consumes: `MeasureRun.unchanged()` (W2-T2), `SKELETON_SUFFIX` (`app/project_layout.py:53`).
- Produces / exact texts:
  - Buttons: `calculate_length_button.text() == "Measure Length"`,
    `calculate_area_button.text() == "Measure Area"`.
  - Length tooltip: `"Measure Length\n\nRoot length (mm) of every image, from its skeleton: the GT skeleton saved in Correct where there is one, else the predicted skeleton in skeletons/. Writes skeletons/root_lengths.csv. Skeletons whose image is no longer in the project are skipped."`
  - Area tooltip: `"Measure Area\n\nRoot area (mm²) of every image, from its mask: the GT mask saved in Correct where there is one, else the predicted mask in mask/. Writes mask/root_areas.csv. Masks whose image is no longer in the project are skipped."`
  - Quoting texts: `stages.py` `button = "Measure Length"` / `"Measure Area"` (the
    sentences become "Run Measure → Measure Length to chart …");
    `visualization_manager.py`: `"Run Measure → Measure Length first (after generating skeletons)."`,
    `"Generate masks first, then run Measure → Measure Area."`,
    `"Run Measure → Measure Area first (after generating masks)."`
  - Generate stage blurb (and README Generate row): `"Predict masks (root area) and skeletons (root length) for all loaded images in one pass. Both come from the photo; neither follows the other."`
  - `generate_all_button` tooltip ends: `"One click runs both. They are independent: correcting a mask changes root area only, correcting a skeleton root length only."`
  - `MetricCalculatorThread.unchanged = pyqtSignal()` — emitted instead of measuring when
    `job.ask_if_unchanged and job.rows is None and job.unchanged()`; no CSV is written.
  - `MeasureRun(..., ask_if_unchanged: bool = False)`: the handler's first click passes
    `True`; the retry path and "Measure again" pass `False`; the legacy thread wrappers
    keep `False`.
  - Prompt (non-modal `QMessageBox`, `WA_DeleteOnClose`, `open()`, held in
    `self._again_box` like `_retry_box`): title `"Measure"`, text
    `"Nothing changed since the last measurement. Measure again?"`, informative
    `"{csv name} already holds the values of these {n:,} {nouns}."`, buttons Yes ("Measure
    again") / Cancel, default Cancel. Yes → `_start(MeasureRun(spec, job.images, job.output_dir, project_root=job.project_root, without_image=job.without_image))`;
    Cancel → `feedback.task_finished("Nothing changed since the last measurement: {csv name} kept")`.
    The progress task of the skipped run ends with the same text either way (no dangling
    task).
  - L4 docs sentence (README Outputs, DATA_DICTIONARY Skeletons): "`_fake` is the Pix2Pix
    name for a generated image: `<stem>_fake.png` is the predicted skeleton."
  - X9: `chip_content("", statuses, layer, None)[2] == ("Select an image in the Library", "", None)`;
    with a path the hint is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_wording_guards.py`:
    - `test_no_fake_png_literal_outside_project_layout`: `ast` walk of every
      `app/**/*.py` except `app/project_layout.py`; collect `ast.Constant` str values
      (f-string parts included) that are not docstrings (first statement of a module /
      class / function); assert none contains `"_fake.png"`.
    - `test_skeleton_inference_uses_the_layout_suffix`:
      `skeleton_inference.SKELETON_SUFFIX is project_layout.SKELETON_SUFFIX`.
    - `test_the_measure_buttons_say_what_they_do` (the `mw` fixture of
      `tests/test_integration_main_window_wiring.py`, imported with its `app`, `settings`,
      `make_window`, `no_delayed_temp_sweep`): texts `"Measure Length"` / `"Measure Area"`;
      tooltips contain `"root_lengths.csv"` + `"GT skeleton"` / `"root_areas.csv"` + `"GT mask"`.
    - `test_no_text_quotes_the_old_button_names`: no string constant under `app/` equals or
      contains `"Measure → Root Length"`, `"Measure → Root Area"`; `ui_panels.py` has no
      `"Root Length"` / `"Root Area"` button text.
    - `test_the_generate_stage_says_masks_and_skeletons_are_independent`:
      `dict((k, tip) for k, _, _, tip in shell_chrome.STAGES)["Generate"]` contains
      `"neither follows the other"`.
  - `tests/test_correct_status_chip.py::test_correct_with_no_image_says_select_an_image`
    (import `make_editor` from `tests/test_correct_editor.py` beside the names the file
    already imports; it cannot share `tests/test_wording_guards.py`, whose `app` fixture
    comes from the main-window module): fresh editor →
    `editor.status_chip.hint_label.text() == "Select an image in the Library"`.
  - `tests/test_measure_handler.py`:
    - `test_measuring_again_with_nothing_changed_asks_first`: measure area once; note the
      CSV `st_mtime_ns`; `calculate_root_area()` again, `_wait_idle` → `handler._again_box`
      is visible with the exact text; CSV mtime unchanged; `box.done(QMessageBox.StandardButton.Yes.value)`
      → a job runs; after `_wait_idle` the CSV mtime grew.
    - `test_a_changed_input_measures_without_asking`: repaint one mask between the runs →
      no `_again_box`; the CSV is rewritten.
    - `test_a_csv_without_a_record_measures_without_asking`: delete
      `root_areas.csv.inputs.json` between the runs → no prompt; rewritten.
    - `test_cancel_keeps_the_csv`: answer Cancel → no job; a `task_finished` call with
      `"Nothing changed since the last measurement: root_areas.csv kept"`.

- [ ] **Step 2: Run, expect FAIL**

  Run: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_wording_guards.py tests/test_measure_handler.py tests/test_correct_status_chip.py -v`

- [ ] **Step 3: Implement** the Produces block. CHANGELOG `### Changed`: "The Measure
  buttons read *Measure Length* / *Measure Area* and say what they read and write;
  measuring again with nothing changed since the CSV asks first (R8, O17). The Generate
  stage says masks give area and skeletons length, independently (O14). Correct with no
  image open says 'Select an image in the Library' (X9)."

- [ ] **Step 4: Run, expect PASS** with: `tests/test_wording_guards.py tests/test_measure_handler.py tests/test_measure_threads.py tests/test_measure_core.py tests/test_robust_saved_notice.py tests/test_robust_measure_legacy_notice.py tests/test_integration_main_window_wiring.py tests/test_shell_chrome_smoke.py tests/test_shell_focus_order.py tests/test_warmup_main_window.py tests/test_shell_visualize_entry.py tests/test_viz_embed_manager.py tests/test_correct_editor.py tests/test_correct_status_chip.py tests/test_correct_layering.py tests/test_generation_inference.py tests/test_shell_window_split_surface.py tests/test_import_smoke.py`.
  `tests/test_correct_layering.py`: `chip.py` stays ≤ 450 lines.

- [ ] **Step 5: Ruff** on the `.py` files of the Files list.

- [ ] **Step 6: Commit (lead)** — `git add` the Files list;
  `git commit -m "docs(ui): Measure Length / Measure Area, ask before re-measuring, say what each layer gives"`

---

## Self-review (done 2026-10-25)

1. **Spec coverage.** W2-T1 (O16 both parts), W2-T2 (R5 record, content compare, new /
   removed, session cache, fallback + tooltip), W2-T3 (R1 step 1), W2-T4 (M2 step 1:
   `sizes_px`, mixed-size toast, export / bar / About read the CSV's calibration), W2-T6
   (O10 + `n_non_binary`), W2-T8a (O17 rename + quoting texts + tooltips + "Measure
   again?", O14 Generate sentence, L4 constant + sentence, X9), W2-T9 (O29 toast + About +
   chip), W2-T10 (M3 README + generation tests), W2-T11 (O37 skip + provenance + notice +
   files kept + Sci entry). Not here, by the master: W2-T5, W2-T7, Layers-row tooltips,
   the GT export manifest column.
2. **Step scan.** Every test names its setup and exact values; code steps give
   signatures and the one decision a test cannot fix (key = project-relative path; the
   no-op rule inside `run_record`; the probe-free `canvas_line`).
3. **Type consistency.** `Freshness(inputs, measured, changed, new, removed, by_content, without_image)`
   used the same in W2-T2 / W2-T11; `MeasureRun(spec, images, output_dir, *, project_root, without_image, ask_if_unchanged)`
   grows in W2-T2 → W2-T11 → W2-T8a; `input_freshness(folder, suffix, csv_path, *, record, project_root, images)`.
4. **Review Focus.** Five lines, each with its test in the owning task.
5. **Proportion.** No function bodies; the plan lists signatures, texts and assertions.
