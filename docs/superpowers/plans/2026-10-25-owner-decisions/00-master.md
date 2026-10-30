# Owner Decisions Build — Master Plan (2026-10-25)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. This file is the index: the step-level tasks
> live in the wave files of this folder (section 9). Read this file first, then your wave
> file, then your task.

**Goal:** Build every owner decision of the 2026-10-12 decision sheet (O1-O38) and the
follow-up answers of 2026-10-25, in an order that removes data loss first, keeps every
scientific output change behind its own reviewed commit, and leaves working, tested
software after every task.

**Architecture:** Nine waves of tasks (0, 1, X, G, 2, 3, 4, 5, 6) run as parallel *lanes*;
a lane is an ordered list of tasks over files no other running lane touches. Wave G
replaces the separate Trace GT editor with a third layer of the Correct editor and moves
every human-saved edit into `gt/` (predictions in `mask/` and `skeletons/` are never
overwritten). Wave X makes the app run on Linux and macOS. Science changes (calibration,
length estimator, GT-first measurement, dropped orphan rows) land late, one per commit,
each with a before / after CSV diff in `CHANGELOG.md`.

**Tech Stack:** Python 3.10, PyQt6, PyTorch (CUDA on Windows / Linux, CPU on macOS),
OpenCV, Pillow, scikit-image, Dash / Plotly, pytest, ruff. Windows 11 dev machine
(RTX 5070 Ti), CI on ubuntu, windows and macos.

**Spec:** `docs/superpowers/plans/2026-10-12-known-issues-resolution.md` (task details,
section 2 = the owner's answers, committed `19f3761`) + the 2026-10-25 chat answers
(section 1 below) + the research memos in
`.claude/handoff/owner-decisions-2026-10-25/research/` (git-ignored, owner's machine) +
the audit reports in `.claude/handoff/issue-audit-2026-10-12/`.

---

## 1. Decisions as built

Every owner answer, read once, with the task that builds it. "Chat" = answered on
2026-10-25 in this session. Where the sheet's text was ambiguous, the reading below is the
one the owner confirmed in chat or the plain reading of the answer.

| No. | Owner answer (short) | What we build | Task |
|---|---|---|---|
| O1 | Measure corrected skeletons, `Source` column | Generalised by the GT design: Measure reads GT first for **both** length and area; last column `Source` = `gt` or `prediction` in both CSVs | WG-T8 |
| O2 | Unedited Save is not acceptance | A Save writes only a layer whose pixels differ from what it would otherwise read (the prediction, or the saved GT); a skeleton equal to the generated one removes a stale correction | W1-T3, WG-T2, WG-T3 |
| O3 | Same Bartz BTC-100X camera; 18 x 13.5 mm; one constant per project file | Per-project calibration file with default 18 x 13.5 mm (square 0.028125 mm pixels; confirmed by Rajurkar et al. 2022, Plant Methods 18:39), recorded in every sidecar. Default switch is its own science commit. Chat 2026-10-25: no grid photos (keep 18 x 13.5); CSVs measured with another field of view or length method are flagged outdated, not hidden | W3-T2 |
| O4 | Fixed-shape batches; stop on OOM; default on after a GPU session | GPU session running now (chat: "now, in background"); the mode is built with default from the session's verdict | W2-T3, W3-T3 |
| O5 | Deterministic cuDNN default | Docs only | W0-T2 |
| O6 | PNG and JPEG only (any case) | Every reader uses `config.IMAGE_EXTENSIONS` (`.png .jpg .jpeg`, case-insensitive); other files are reported, not listed | W1-T5 |
| O7 | Stems compare without case, every platform | `stem_key = stem.lower()` everywhere a stem meets a file | W1-T5 |
| O8 | Generate keeps the model size; nearest on display | A prediction of another size opens stretched with nearest | W3-T4 |
| O9 | Cleared mask stays; "Replace model masks only" | GT design: clearing and saving writes an **empty GT mask** (Measure writes 0.0, `Source` = `gt`); Generate only ever replaces predictions, so its prompt becomes "Keep / Replace predictions" and never offers to touch GT | WG-T2, WG-T7 |
| O10 | One threshold, 127 | `MASK_THRESHOLD = 127` shared by the editor and Measure; non-binary masks counted | W2-T6 |
| O11 | 1 px click paints | One pixel per click, no golden change | W3-T5 |
| O12 | Keep an 8-connected arc length; pixels are square; fix the bias | Vossepoel-Smeulders corner-count estimator (memo `research/o3-o12-calibration-length.md`; bias -0.1 % vs +5.3 %; axis-aligned lines read 2.0 % short), accepted by the owner in chat 2026-10-25; own science commit | W3-T8 |
| O13 | Old 341 x 256 corrections never measured, flagged | Legacy GT skeletons flagged in the Library and Correct; Measure falls back to the prediction with a recorded reason | WG-T3 (flag, was W2-T7), WG-T8 |
| O14 | Say mask → area, skeleton → length, independent | Generate page sentence (W2-T8a); the three Layers-row tooltips (WG-T10) | W2-T8a, WG-T10 |
| O15 | Refuse a folder change during a job | As planned | W1-T1 |
| O16 | No-op run keeps the record; run history | As planned | W2-T1 |
| O17 | "Measure Length" / "Measure Area"; no incremental | As planned | W2-T8a |
| O18 | One Clear in the dock; Save accent only while unsaved | Applied to the one unified editor after Wave G | W4-T1 |
| O19 | Discard reloads a changed file; Save asks | Correct layers in W1-T4; the GT classes layer inherits it in WG-T4 | W1-T4, WG-T4 |
| O20 | Toast when a smoothed pick is dropped | As planned | W4-T2 |
| O21 | Linux + macOS; GPU optional on macOS; no Windows-only packages | Wave X from `research/o21-cross-platform.md`: drop `pywin32`, per-OS Qt flags, trackpad pinch (native gesture), CI on macOS, install docs | WX-* |
| O22 | Deblock switch yes; a tiny real-time upscaler only if one exists | `research/o22-display-upscaler.md` (done): no upscaler meets the bar (ESPCN / FSRCNN keep the blocks; Real-ESRGAN-compact 42 ms GPU, 1-1.5 s CPU, invents edges). Build only the deblock switch: `deblock(img, threshold=14)`, default off, `.jpg` / `.jpeg`, 5-6 ms CPU, blockiness 1.95 -> 1.14 | W4-T5 |
| O23 | One unified editor | Trace GT becomes the third layer ("GT classes") of Correct; the Trace GT stage, page and panel are removed (top bar: Library · Generate · Correct · Measure · Visualize) | WG-T4, WG-T5 |
| O24 | Restore retired classes; Remove GT; GT in the Library; per-class pixel counts; no COCO | As planned, on the merged editor | WG-T6, WG-T9 |
| O25 | Accept compact top bar | Close G3 | W0-T1 |
| O26 | Prediction vs GT; keep the word GT; third Library mark | Vocabulary "prediction" (model output) and "GT" (saved after a human edit); the Library badge gets a third mark | WG-T6, WG-T10 |
| O27 | View names + one blue date ramp | As planned | W4-T6 |
| O28 | D2 warm-up only with other Generate work | Optional, after WG-T7 | W6-T3 |
| O29 | Canvas indicator: toast + About | As planned | W2-T9 |
| O30 | Do not defer editor pages | Close P1 | W0-T1 |
| O31 | Uninstall `pipreqs`, `ipython` (owner runs it) | README line; owner command in the hand-over | W6-T4 |
| O32 | GPL-3.0-or-later code, CC-BY-4.0 weights, no sample data; chat: copyright "the SPROUTS authors" | `LICENSE` + metadata | W5-T1 |
| O33 | Only weights on Zenodo, no data | Untrack `checkpoints.zip`, fetch script, DOI fields empty until the owner deposits | W5-T2 |
| O34 | Cite as "SPROUTS"; tag at merge; chat: authors as in RootQuantV2's `CITATION.cff` | Authors Kinjalk Parth, Sebastian Varela, Andrew D. B. Leakey; tag `v3.0.0` is the owner's | W5-T3 |
| O35 | Only a few example images with mask, skeleton, result graphs; chat: the audit's three | `docs/examples/` (T001 L044, T001 L050, T012 L047 of 2024-07-18) + the real-image gate reads them | W5-T4 |
| O36 | Delete `docs/Architecture_2 useful.md` + PDF, `docs/BUILD_EXECUTABLE.md` | As planned | W5-T7 |
| O37 | Orphan masks not shown; chat: skip and record, never delete | Measure skips masks, skeletons and GT files whose image is gone, for both metrics; provenance + end notice list them | W2-T11 |
| O38 | File name only in `Error` cells | As planned | W3-T7 |
| Chat | GT design = **GT folder** | Human-saved mask, skeleton and class labels live under `gt/`; predictions untouched; existing hand-corrected files copied into `gt/` once (originals kept) | Wave G |
| Chat | Commits on this branch, none pushed | One commit per reviewed task on `feat/unified-correct-editor` | all |
| Chat | Third Library mark = "class labels exist"; layer keys M / L / G (+ X), digits pick classes | Badge: mask and skeleton marks show prediction (circle) vs GT (diamond); the purple third mark shows class labels | WG-T5, WG-T6 |
| Chat | "with this app it will never be used on old data ... I will regenerate everything" | No migration of old projects, no legacy-format UX (old 341 x 256 corrections, "older save" masks, origin-by-colour-type); only generic input validation stays (an unreadable or wrong-size GT file is not used, with a recorded reason). O13's flag UX is dropped | Wave G (re-cut by the fable plan revision) |
| Chat | O4 GPU session result (lead ruling) | Fixed shapes: masks N = 4 (16 needs about 12 GiB free; cuDNN silently changes kernel under memory pressure), skeletons N = 64; pre-flight free-VRAM check; no retry after OOM in fixed mode | W3-T3 |

### 1.1 Tasks of the 2026-10-12 plan that are superseded

| Old task | Now | Why |
|---|---|---|
| W1-T6 (mask saved during a run) | dropped | Generate never writes `gt/`; a mask saved during a run is GT |
| W2-T5 (origin chunk, third prompt answer) | dropped; the O9 parts move to WG-T2 / WG-T7 | `mask/` holds predictions only after the migration |
| W3-T1 (measure corrections) | WG-T8 | GT-first resolver for both metrics |
| W3-T6 (formats) | W1-T5 | as before |
| W4-T3 (GT goldens, chip, opacity) | WG-T0 (goldens); chip and opacity come with the merge | the GT layer lives in Correct |
| W4-T4 (badge, Restore, Remove, counts) | WG-T6, WG-T9 | built on the merged editor |
| W4-T7 (exact windowed pick) | not built | 11 ms per pick on real data |
| W4-T8 (GT noun) | WG-T10 | owner keeps "GT" |
| W6-T1, W6-T2 | not built now | only for images larger than 640 x 480 |
| W1-T4 Trace GT part | WG-T4 | the GT editor is replaced |
| W0-T2 rows inside code files | moved to the code task that owns the file (section 6) | avoids two lanes in one file |

---

## 2. Global Constraints

Every task's requirements implicitly include this section.

- Python: always `./venv/Scripts/python.exe` (implementers in other cwd: the absolute
  `D:/Root-Mask-and-Skeletons/venv/Scripts/python.exe`). Never bare `python`. No `pip
  install` into the venv without the lead's approval.
- Tests: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest <files>
  -v`. For counts use `--junitxml` (`-q` hides the summary). Full suite only by the lead
  at wave ends.
- Lint and format: ruff only (line length 100, py310). **Implementers run ruff on their
  own changed files only** (`ruff check --fix <files>`, `ruff format <files>`), never on
  `.`, because other lanes have uncommitted work in the same tree.
- Git: implementers make no git state change (no add, commit, stash, checkout, reset,
  branch). The lead commits each task with `git add <the task's files>` (never `-A`), one
  conventional commit per task, no attribution lines.
- Scope: an implementer edits only the files its task lists. A needed change elsewhere is
  reported to the lead in the final report, not made. `docs/KNOWN_ISSUES.md` and
  `CHANGELOG.md` change only where a task says so.
- Never touch `docs/superpowers/plans/2026-10-25-rqv2-sprouts-benchmark.md` (another
  session's untracked file) or anything under `E:/`.
- Scientific output: a change that can alter a value in `root_lengths.csv` /
  `root_areas.csv`, a row's presence, a saved mask / skeleton / GT pixel, or a generated
  output happens only in a task marked **Sci** in section 4, and gets a `CHANGELOG.md`
  entry under "Scientific output changes" with the before / after diff on the synthetic
  test set and the 3000-pair real sample (audit script
  `.claude/handoff/issue-audit-2026-10-12/scripts/length/sample_real.py`, read-only on
  `D:\2024 EF image data`; never list that folder whole).
- `tests/data/correct_goldens.json`: saved mask and skeleton **pixels** stay identical.
  Wave G may change where a file is written; if a golden encodes a path, only the path
  entry changes, in WG-T2 / WG-T3, with the reason in the commit message. W1-T3 changes
  the `synth_blank_png/skeleton_clear` block only (O2: a no-op save writes no file; pixel
  hashes unchanged).
- Measure CSVs stay byte-identical except in Sci tasks
  (`tests/test_measure_core.py::test_measure_csv_is_byte_identical_to_legacy_pipeline`,
  `::test_real_sample_is_byte_identical_to_legacy_pipeline`).
- `app/inference/metrics.py`: public functions keep backward-compatible signatures (new
  parameters are keyword-only with defaults). A parallel benchmark session imports them.
- Every `app/gui/correct/*.py` stays at 450 lines or fewer (`tests/test_correct_layering.py`).
- Qt-free core modules import neither PyQt6 nor torch (`tests/test_layering_contract.py`,
  the import smoke test). New Qt-free modules: `app/gt/*`, `app/calibration.py`,
  `app/inference/length_inputs.py` (or the name WG-T8 gives the resolver),
  `app/gui/correct/save_gate.py` (W1-T2), `app/editing/thinning.py` (W1-T3),
  `app/inference/measure_inputs.py` (W2-T2), `app/qt_environment.py` (WX-T1).
- `MainWindow`'s API surface is pinned by `tests/test_shell_window_split_surface.py`; only
  WG-T5 (stage removal) and WG-T8 (skeleton_saved wiring) may change the pin, each saying
  which names and why. New window logic goes into a part under `app/gui/window/`.
- Panel indices only through `app/gui/panels.Panel` members (`tests/test_right_panel_contract.py`).
- The metrics bar shows measured values only. Never fabricate a value.
- O21: no Windows-only package; new code uses `os.path` / `pathlib`, `QDesktopServices`
  for opening folders, no `os.startfile`, no `ctypes.windll`, no drive-letter literals.
- GPU jobs (benchmarks, experiments) take the shared lock
  `E:/RootQuantV2/benchmark_sprouts/work/gpu.lock` (O_CREAT|O_EXCL, "name pid iso-time",
  removed in `finally`) when that folder exists.
- Every implementer starts with superpowers:test-driven-development: failing test first,
  watch it fail, minimal code, watch it pass.
- After each wave the lead runs the full suite (junitxml), `ruff check .`, the wave's
  reproducing scripts from `.claude/handoff/issue-audit-2026-10-12/scripts/` on a scratch
  copy, `python -m graphify update .` from the uv tool venv (memory
  `graphify-cli-invocation`), and updates `STATUS.md`.

## 3. Review Focus

The five inputs most likely to bite a user, each pinned by a test in the named task:

1. (Void: the owner never opens old data; see section 1.) **A project saved before this build**: hand-corrected masks in `mask/` (white on
   transparent) and corrections in `skeleton_corrections/`. Expected: after the one-time
   copy into `gt/`, Correct shows them, Measure gives the same numbers for them (now
   `Source` = `gt`), Generate never replaces them, the originals are untouched. Tests in
   WG-T1 (migration), WG-T7 (Generate), WG-T8 (values equal).
2. **A photo that is not 640 x 480.** Expected: its prediction is shown (nearest), no Save
   removes or replaces a file the editor did not load. Tests in W1-T2 and W3-T4.
3. **A second folder opened while a job runs.** Expected: refused; the job's outputs go to
   the folder it started in. Tests in W1-T1.
4. **Case-variant stems, or a `.tif` next to a `.png`.** Expected: one Library entry, one
   set of outputs, the same on every run, unsupported files reported. Tests in W1-T5.
5. **A file rewritten on disk while open with unsaved edits** (another program, or a
   Generate run rewriting the prediction under an unedited layer). Expected: Save asks
   before it overwrites GT; an unedited layer follows the new prediction; Discard reloads.
   Tests in W1-T4 and WG-T4.

---

## 4. Task catalogue

Columns: **Impl** = implementer model / effort; **Rev** = reviewer model / effort; **Sci**
= may change a scientific output (section 2). Effort words are the Agent tool's levels.
Routing rule from the owner: sonnet / opus by difficulty; fable only for decisions and
plan writing, never code.

### Wave 0: documents true (plan file `w0-w5-docs-repo.md`)

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| W0-T1 | Restructure `docs/KNOWN_ISSUES.md`: open decisions table now "decided → task", engineering table, by design, details, resolved history; close G3, P1, D1, D3, D4, D6, E6 (bicubic part), L4, M3, R2, P4, P5, P7 per old section 4 | sonnet / high | sonnet / medium | no | — |
| W0-T2 | Stale statements in docs only (README, CHANGELOG, DATA_DICTIONARY, MODELS, specs, Architecture_2, CLAUDE.md); code-comment rows go to their code tasks (section 6) | sonnet / medium | sonnet / low | no | — |

### Wave 1: stop data loss (plan file `w1-data-loss.md`)

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| W1-T1 | A job belongs to the folder it started in; refuse folder change while a job runs; no job while a folder loads (R7, O15) | opus / high | opus / medium | no | — |
| W1-T2 | A mask the editor did not load is never removed or replaced silently (M1-A) | opus / high | opus / medium | no | — |
| W1-T3 | Skeleton Save writes only when it differs from the generated one; `is_generated_as_opened` helper (L2, O2) | opus / high | opus / medium | no | W1-T2 |
| W1-T4 | Files changed on disk: Discard reloads, Save asks, no false alarm, open image follows a finished Generate (E2, O19) — Correct layers only | opus / xhigh | opus / high | no | W1-T3, W1-T1 (`main_window.py`) |
| W1-T5 | `stem_key` everywhere; PNG/JPEG only in every reader; load toast names hidden and unsupported files (R4, R3, O6, O7) | opus / high | opus / medium | yes (such folders) | W1-T1 for `project_loader.py` |
| W1-T7 | `THIRD_PARTY_NOTICES.md` with the pix2pix BSD text (F1 step 1) | sonnet / low | sonnet / low | no | W1-T5 (`skeleton_inference.py`) |

### Wave X: Linux and macOS (plan file `wx-cross-platform.md`, from `research/o21-cross-platform.md`)

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| WX-T1 | Start-up environment per OS: new Qt-free `app/qt_environment.py` (D3D11 / ANGLE on Windows only; Linux Qt defaults, sandbox off only as root; macOS `QT_FONT_DPI=96`); Fusion off Windows; desktop file name | opus / high | opus / medium | no | — |
| WX-T2 | Dependencies and metadata: drop `pywin32`; `opencv-python-headless`; OS classifiers, codemeta | sonnet / medium | sonnet / low | no | WX-T1 |
| WX-T3 | Trackpad pinch zoom (`QNativeGestureEvent`) on `CanvasViewMixin` | opus / high | opus / medium | no | WX-T2 |
| WX-T4 | `io_utils.path_key` / `same_file` (case-insensitive volumes), temp names in bytes, macOS device note, platform-guard tests | sonnet / high | opus / medium | no | WX-T3 |
| WX-T5 | Font fallback chains (tokens + QSS) | sonnet / medium | sonnet / low | no | WX-T4 |
| WX-T6 | CI on macOS (continue-on-error until the first green run), OS-neutral suite (the 12 Linux failures), README Linux / macOS install | sonnet / high | opus / medium | no | WX-T5 |
| WX-T7 | Platform key text (Cmd / Option), macOS stage keys, Pinch row in F1 | sonnet / high | opus / medium | no | W2-T8a |
| WX-T8 | macOS: raster canvas viewport by default (OpenGL opt-in) | sonnet / medium | sonnet / medium | no | W2-T9 |
| WX-T9 | Deterministic CSV / Library tie order (name tie-break) | sonnet / high | opus / high | **yes** (row order) | W2-T11 |
| WX-T10 | Backslash separators in `image_stem` | sonnet / low | sonnet / low | no | W1-T5 |
| WX-T11 | Optional: bundle IBM Plex TTF (SIL OFL) | sonnet / medium | sonnet / low | no | WX-T6 |

### Wave G: GT folder and one editor (plan file `wg-gt-unified-editor.md`, written by fable, revised after the owner's chat answers)

Storage: human-saved GT in `gt/mask/<stem>.png` and `gt/skeletons/<stem>.png`; class labels stay
at `gt/<stem>.png` + `gt/classes.json`; predictions in `mask/` and `skeletons/` never written by
the editor; no migration of old data (owner: old data is never opened; everything is
regenerated); `skeleton_corrections/` is no longer read. Resolver module:
`app/inference/measure_sources.py` (W2-T2's `measure_inputs.py` is the inputs record).

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| WG-T0 | GT editor goldens captured from today's Trace GT | sonnet / high | opus / medium | no | — |
| WG-T1 | GT paths and scans in `ProjectLayout` (`gt_mask_path`, `gt_skeleton_path`, `scan_gt_stems() -> GtStems`), Qt-free `app/gt/files.py`, `ImageManager` GT sets | sonnet / high | opus / medium | no | W1-T5 |
| WG-T2 | Mask layer reads GT else prediction, writes GT only; Clear + Save = empty GT (O9); a mask equal to its prediction removes the GT file; "Load prediction"; session fallback removed | opus / high | opus / high | no | WG-T1, W1-T4 |
| WG-T3 | Skeleton layer reads / writes `gt/skeletons/`; `skeleton_corrections/` retired from `ProjectLayout` / `ImageManager` / display / metrics names | opus / high | opus / high | no | WG-T2 |
| WG-T4 | `RasterLayer` lifted from `MaskLayer`; Qt-free `ClassesDocument`; `ClassesLayer` (also a `DiskGuardedLayer`) | opus / xhigh | opus / high | no | WG-T0, WG-T3 |
| WG-T5 | The classes layer inside Correct (class panel, keys M / L / G / X and digits for classes, Classes… / Export dataset… / Remove GT… on the Correct page); `editor.py` ends <= 440 lines | opus / xhigh | opus / high | no | WG-T4 |
| WG-T11 | Trace GT stage / panel / scope removed (Alt+1…Alt+5), `raster_editor_base.py` + `gt_tracing_interface.py` deleted, tests ported, `skeleton_saved` wiring | opus / high | opus / high | no | WG-T5 |
| WG-T6 | Library badge: three marks (mask / skeleton: circle prediction, diamond GT; purple third mark: class labels exist), tooltips, follows saves | sonnet / high | sonnet / medium | no | WG-T3, WX-T9 |
| WG-T7 | Generate writes predictions only; "Keep / Replace predictions"; never touches `gt/` | sonnet / high | opus / medium | no | WG-T2 |
| WG-T8 | Measure GT-first for both metrics (`measure_sources.py`); `Source` column (`gt` / `prediction`); reasons `size` / `unedited` / `unreadable`; provenance `inputs.gt`; freshness on top of W2-T2's record; two commits | opus / xhigh | opus / high | **yes** | WG-T3, WG-T6, W2-T11, WX-T9 |
| WG-T9 | Restore retired classes; per-class pixel counts and `exif_orientation` in the export manifest | sonnet / high | sonnet / medium | export manifest only | WG-T11 |
| WG-T10 | Vocabulary (prediction / GT), README "Which layer?" table, wording guards, Layers-row tooltips (O14) | sonnet / medium | sonnet / medium | no | WG-T11 |

### Wave 2: records and wording (plan file `w2-records-wording.md`)

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| W2-T1 | No-op run keeps `_sprouts_run.json`; `_sprouts_runs.jsonl` history (R6, O16) | sonnet / high | sonnet / medium | no | W1-T5 |
| W2-T2 | Measure records its inputs (`<csv>.inputs.json`); freshness compares content (R5) | opus / high | opus / medium | no | W3-T7 |
| W2-T3 | Run record: `batch_shapes`, `oom_events` (R1 step 1) | sonnet / medium | sonnet / low | no | W2-T1 |
| W2-T4 | Provenance `inputs.sizes_px`; mixed-size warning; export header / metrics bar / About show the CSV's own calibration (M2 step 1) | sonnet / high | opus / medium | no | WG-T8 |
| W2-T6 | `MASK_THRESHOLD = 127` shared; `outputs.n_non_binary` (M5, O10) | sonnet / medium | sonnet / medium | no | W2-T4 |
| W2-T8a | Measure Length / Measure Area (+ tooltips, "Measure again?"), the Generate page sentence (O14), `_fake` constant + sentence (L4), "Select an image in the Library" empty state (X9) | sonnet / medium | sonnet / low | no | W2-T2, WG-T5, WG-T8 (also needs L-measure / L-runrec idle: it edits `skeleton_handler.py`, `metric_thread.py`, `measure_run.py`, `skeleton_inference.py:57`) |
| W2-T9 | About row + device-chip line + warn toast for the canvas path (P6, O29); edits `shell_chrome.py` (chip tooltip) and `window/body.py`, so it lands before WG-T5 | sonnet / medium | sonnet / low | no | — |
| W2-T10 | EXIF policy written down and pinned by tests (M3); the export column lives in WG-T9 | sonnet / medium | sonnet / low | no | — |
| W2-T11 | Skip and record masks, skeletons and GT files without an image, both metrics (X2, O37) | sonnet / high | opus / medium | **yes** (rows dropped) | W2-T2 |

### Wave 3: science (plan file `w3-science.md`, written after the O3 / O12 / O4 memos)

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| W3-T2 | `app/calibration.py` per-project file `sprouts_project.json`; recorded in sidecars; commit 1 keeps 18 x 13, commit 2 switches the default to 18 x 13.5 (O3) | opus / high | opus / high | **yes** (commit 2) | W2-T6 |
| W3-T3 | Fixed-shape batch mode `SPROUTS_BATCH_POLICY` (`fixed` / `auto`), padded tail, stop on OOM, default from the GPU memo (O4); fixed sizes named in `runtime.py`, `config.py` untouched | opus / high | opus / high | **yes** (default on, only if the premise holds) | W2-T3, W1-T5, the O4 memo; not while WG-T7 edits `mask_inference.py` |
| W3-T4 | A prediction mask of another size opens stretched with nearest (O8) | sonnet / high | opus / medium | saved-from-Correct only | WG-T2 |
| W3-T5 | A 1 px click paints / erases its pixel (O11) | sonnet / medium | sonnet / medium | 1 px | WG-T0 (the GT goldens are part of its green run) |
| W3-T7 | `Error` cells hold the file name only (O38) | sonnet / low | sonnet / low | Error text | — |
| W3-T8 | Corrected 8-connected length estimator from the research memo (O12) | opus / high | opus / max | **yes** (every length) | W3-T2 |

### Wave 4: editors (plan file `w4-editors.md`, W4-T5 after the O22 memo)

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| W4-T1 | Save accent only while unsaved, disabled otherwise; one Clear, in the dock (O18) | opus / medium | sonnet / high | no | WG-T5 |
| W4-T2 | Toast when a smoothed, uncommitted pick is dropped (O20) | sonnet / medium | sonnet / low | no | W4-T1 |
| W4-T5 | Display-only "Smooth JPEG blocks" switch (deblock only; the memo found no upscaler that fits) (O22) | sonnet / high | opus / medium | no | — |
| W4-T6 | Dashboard view names; one blue date ramp (O27) | sonnet / high | sonnet / medium | no | — |

### Wave 5: repository and FAIR (plan file `w0-w5-docs-repo.md`)

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| W5-T1 | `LICENSE` GPL-3.0-or-later, "Copyright (c) 2026 the SPROUTS authors"; weights CC-BY-4.0 in `docs/MODELS.md`; metadata, About row, bundle datas (O32) | sonnet / medium | sonnet / medium | no | WX-T2, W1-T7 |
| W5-T2 | `git rm --cached checkpoints.zip` (lead runs it); `ModelSpec.url` / `.doi` empty until deposit; `scripts/fetch_models.py`; Zenodo deposit notes for the owner (O33) | sonnet / high | opus / medium | no | W5-T1 |
| W5-T3 | Authors (Parth, Varela, Leakey) in `CITATION.cff`, `codemeta.json`, `pyproject.toml`; cite as "SPROUTS"; ship `CITATION.cff`; release notes for the owner's tag (O34) | sonnet / medium | sonnet / low | no | W5-T2 |
| W5-T4 | `docs/examples/` (3 images + mask + skeleton + a result graph) and the real-image golden gate reading them (F4, O35) | sonnet / high | opus / medium | no | WG-T8, W3-T8 (the example CSVs follow calibration and estimator) |
| W5-T5 | Delete compat shims; one `file_stamp` / `same_file` in `app/io_utils.py` (F5, X8); adopt WX-T4's `path_key` / `same_file` at every remaining path-identity site, first the GT export protected-folder check | sonnet / high | sonnet / medium | no | WG-T5, W1-T4, WX-T4 |
| W5-T6 | CI on `feat/**`, `fix/**`; test log dir; README `fault.log` note; start-up Qt warnings (X5, X6, X7) | sonnet / medium | sonnet / low | no | WX-T6 |
| W5-T7 | Delete `docs/Architecture_2 useful.md` + PDF, `docs/BUILD_EXECUTABLE.md`; trim `Architecture_2.md`; doc-path test; teardown note (F6, F7, O36) | sonnet / medium | sonnet / low | no | WG-T10 |

### Wave 6: optional

| ID | Task | Impl | Rev | Sci | After |
|---|---|---|---|---|---|
| W6-T3 | Pre-Generate scan as the first Generate warm-up (D2, O28) — only if time remains | sonnet / high | sonnet / medium | no | WG-T7 |
| W6-T4 | README: the runtime venv needs neither `pipreqs` nor `ipython`; owner command in the hand-over (D5, O31) | sonnet / low | — | no | — |

### Close-out (lead)

After every wave: `docs/KNOWN_ISSUES.md` rows of the wave's IDs move to "Resolved history"
(with commit hashes), `CHANGELOG.md` `[Unreleased]` entries checked, graph refreshed,
`STATUS.md` updated, memory updated.

---

## 5. Order and lanes

A lane runs its tasks in order; lanes run in parallel. A task starts when every task in its
**After** column is committed. File ownership (section 6) is what keeps lanes apart.

```
Phase A (start once the wave plans are reviewed)
  L-docs     : W0-T2 → W0-T1 → W1-T7 (after W1-T5)
  L-job      : W1-T1
  L-correct  : W1-T2 → W1-T3 → W1-T4
  L-stems    : W1-T5 (its project_loader.py edit after W1-T1) → WX-T10
  L-runrec   : W2-T1 (after W1-T5) → W2-T3
  L-viz      : W4-T6
  L-gold     : WG-T0
  L-xplat    : WX-T1 → WX-T2 → WX-T3 → WX-T4 → WX-T5 → WX-T6 → WX-T11 (optional)
  L-measure  : W2-T2 → W2-T11 → WX-T9 → W3-T7 (then WG-T8 in Phase B)
  L-misc     : W2-T9 → WX-T8 → W2-T10 → W3-T5 → W4-T5

Phase B (Wave G; after L-correct and L-stems)
  L-correct  : WG-T1 → WG-T2 → WG-T3 → WG-T4 → WG-T5 → WG-T11 → WG-T9 → WG-T10 → W2-T8a → WX-T7
  L-library  : WG-T6 (after WG-T3 and WX-T9: both edit app/gui/library_index.py)
  L-generate : WG-T7 (after WG-T2) → W6-T3 (optional)
  L-measure  : WG-T8 (after WG-T3, WG-T6, W2-T11, WX-T9) → W2-T4 → W2-T6 → W3-T2 → W3-T8
  L-runrec   : W3-T3 (after W2-T3 and the O4 memo)
  L-repo     : W5-T1 (after WX-T2) → W5-T2 → W5-T3

Phase C
  L-correct  : W3-T4 → W4-T1 → W4-T2 → W5-T5
  L-repo     : W5-T4 (after WG-T8) → W5-T6 (after WX-T6) → W5-T7 (after WG-T10) → W6-T4
```

Critical path: L-correct (W1-T2 … WG-T5 … W5-T5). Science commits (WG-T8, W2-T11,
W3-T2 commit 2, W3-T3 default, W3-T8) land in that order on L-measure / L-runrec, each
with its own CHANGELOG entry, and the owner is told before W3-T2 commit 2 and W3-T8 land
(the benchmark session reads `metrics.py`).

## 6. File ownership

A file belongs to one lane at a time. The lanes above already respect this list; a task
that needs a file owned by a running lane waits.

| File(s) | Lane, in order |
|---|---|
| `app/gui/correct/*`, `app/gui/editor_prompts.py`, `app/gui/raster_editor_base.py`, `app/gui/gt_tracing_interface.py`, `app/gui/class_panel.py`, `app/gui/class_editor_dialog.py`, `app/gui/export_dataset_dialog.py`, `app/gui/shell_chrome.py`, `app/gui/shortcuts.py`, `app/gui/panels.py`, `app/gui/window/editors.py`, `app/gui/skeleton_tools.py`, `app/gui/skeleton_graph_model.py` | L-correct |
| `app/gui/main_window.py`, `app/gui/window/project_loader.py`, `app/handlers/generation_base.py`, `app/handlers/skeleton_handler.py` (job parts), `app/handlers/generate_skeleton_handler.py` | L-job (W1-T1), then L-stems (toast), then L-generate / L-measure as their tasks say |
| `app/naming.py`, `app/project_layout.py`, `app/inference/mask_origin.py`, `app/inference/mask_inference.py` (`list_inputs`), `app/gui/image_manager.py`, `app/data_processing/measurement_index.py` | L-stems (W1-T5), then L-correct (WG-T1) / L-library (WG-T6) / L-measure (WG-T8) |
| `app/inference/metrics.py`, `app/inference/measure_run.py`, `app/provenance.py`, `app/handlers/skeleton_handler.py` (measure parts), `app/gui/stage_summary.py`, `app/gui/stage_status.py`, `app/gui/metrics_bar.py`, `app/gui/window/metrics.py`, `app/config.py` (calibration, CSV headers) | L-measure |
| `app/inference/run_record.py`, `app/inference/batch_engine.py`, `app/inference/runtime.py`, `app/inference/skeleton_inference.py` (batching) | L-runrec (after W1-T5 for `skeleton_inference.py`) |
| `app/visualization/*`, `docs/SPROUTS_DASH_THEME.md` | L-viz |
| `main.py`, `app/qt_environment.py`, `requirements*.txt`, `pyproject.toml`, `codemeta.json`, `.github/workflows/ci.yml`, `app/gui/canvas_view.py`, `app/user_paths.py`, `app/logging_config.py`, `app/inference/device_probe.py`, `app/io_utils.py`, `app/gui/display_read_ahead.py`, `app/gui/recent_projects.py`, `app/design/tokens.py`, `resources/themes/dark_theme.qss` | L-xplat, then L-repo (`pyproject.toml`, `codemeta.json`, `ci.yml`) and W5-T5 (`app/io_utils.py`) |
| `app/gui/canvas_gl.py`, `app/gui/about_dialog.py`, `app/enhancement.py`, `app/gui/image_normalization_interface.py`, `app/gui/paint_canvas.py`, `app/gui/canvas_layers/photo.py` | L-misc |
| `README.md`, `docs/*.md`, `CLAUDE.md`, `CHANGELOG.md` | per task; the lead merges doc edits at commit time if two tasks touch one doc (each task edits only its own section) |

Code-comment rows of the old W0-T2, now owned by code tasks: `app/config.py:61-62` and
`app/inference/metrics.py:17-18` ("near-isotropic") → W3-T2; `app/gui/shell_chrome.py:21-22`
→ WG-T5; `app/gui/main_window.py:209` (scikit-image import time) → W1-T1;
`app/handlers/mask_generation_handler.py:122` + `tests/test_robust_generation_handler_ux.py:4`
→ WG-T7; `app/gui/window/project_loader.py:170` toast → W1-T5; `app/models.py:10`,
`scripts/build_support.py:64` → W5-T2.

## 7. Execution protocol (per task)

1. **Dispatch.** The lead sends the implementer (model / effort from section 4) the task
   text from its wave file, section 2 of this file, the task's file list, and: "Use
   superpowers:test-driven-development. Edit only these files. No git commands. Run the
   task's tests and ruff on your files. End with a report: files changed, tests run (pass /
   fail counts), anything you could not do, any file outside your list you think needs a
   change."
2. **Review.** A fresh reviewer (model / effort from section 4) gets the task text and the
   diff (`git diff -- <files>` plus new files) and checks: spec coverage, the tests prove
   the behaviour (and fail without the change), section 2 constraints, scope creep, the
   Review Focus line of the task if any. Findings come back to the implementer through
   SendMessage (same agent, context kept) until the reviewer passes it.
3. **Commit.** The lead re-runs the task's tests and `ruff check <files>`, reads the diff,
   and commits only the task's files with the task's commit message.
4. **Record.** `STATUS.md` line (task, commit, notes). Science tasks: the CHANGELOG diff
   summary is part of the commit.
5. **Wave end.** Full suite with `--junitxml`, read counts from the XML; `ruff check .`;
   reproducing scripts; graph refresh; KNOWN_ISSUES close-out; memory note.

Workflows (the Workflow tool) are used for the wave-end reviews (one reviewer per
dimension: correctness, science, data safety, cross-platform) and may be used to run a
lane's implement → review → fix loop; each workflow stays under 10 agents.

## 8. Owner actions (cannot be done by an agent)

- Deposit the two weight files on Zenodo (CC-BY-4.0) and send the DOI; W5-T2 leaves the
  fields empty and a checklist in `docs/MODELS.md`.
- Add ORCIDs / affiliations / contact / funding to `CITATION.cff` when wanted (W5-T3 keeps
  the RootQuantV2 format: names only).
- Tag `v3.0.0` at the merge and push (W5-T3 prepares the notes).
- Run `./venv/Scripts/python.exe -m pip uninstall -y pipreqs ipython` (W6-T4).
- Approve the two calibration-level science commits (W3-T2 commit 2, W3-T8) before they
  land; the parallel RootQuant-V2 benchmark should pin a commit before then.
- Approve swapping the venv's `opencv-python` for `opencv-python-headless` (WX-T2 step 5).
- Push once so CI runs on ubuntu, windows and macos; then WX-T6's `continue-on-error` on
  the macOS job comes off.
- One session on a Mac (Apple silicon, macOS 13+) with the WX-T6 step 8 check list.

## 9. Wave plan files

| File | Waves | Written by (model / effort) | When |
|---|---|---|---|
| `w0-w5-docs-repo.md` | 0, 5 | sonnet / high | now |
| `w1-data-loss.md` | 1 | opus / high | now |
| `wg-gt-unified-editor.md` | G (design + plan) | fable / max | now |
| `w2-records-wording.md` | 2 | opus / high | now |
| `wx-cross-platform.md` | X | opus / medium | after `research/o21-cross-platform.md` |
| `w3-science.md` | 3 | opus / high | after the O3 / O12 and O4 memos |
| `w4-editors.md` | 4 | sonnet / high | now (the O22 memo is in) |

Each wave file follows superpowers:writing-plans: header, Global Constraints (pointing
here), Review Focus, then tasks with Files / Interfaces / steps (failing test with its
assertions, run and see it fail, the exact signature to implement, run and see it pass,
commit message). The lead reviews each wave file against this master before dispatch.
