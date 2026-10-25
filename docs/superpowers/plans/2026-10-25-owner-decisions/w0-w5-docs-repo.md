# Waves 0 and 5: Documents True, Repository and FAIR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Read `00-master.md` first (this folder), then
> this file, then your task.

**Goal:** Make every document of the repository true to the code and to the owner's
decisions of 2026-10-12 / 2026-10-25 (Wave 0), then give the repository its licence, a
weights policy, a citation record, example data with a real-image gate, one set of helpers,
working CI and a trimmed set of documents (Wave 5).

**Architecture:** Wave 0 is two docs-only tasks that run first (W0-T2, then W0-T1). Wave 5
is one lane (`L-repo`) of seven tasks plus W6-T4, each touching its own files; the metadata
files overlap, so W5-T1, W5-T2 and W5-T3 run in that order. Every new rule gets a test:
metadata files agree (`tests/test_fair_*.py`), docs name no missing path
(`tests/test_doc_paths.py`), examples reproduce (`tests/test_examples.py`).

**Tech Stack:** Python 3.10, pytest, ruff, setuptools (PEP 639 licence fields),
Citation File Format 1.2.0, GitHub Actions.

**Spec:** `00-master.md` in this folder (decisions as built, task catalogue, lanes, file
ownership) and `docs/superpowers/plans/2026-10-12-known-issues-resolution.md` (section 3
Waves 0, 5 and W6-T4; section 4 "Closed as accepted"; section 2 owner answers). Evidence:
`.claude/handoff/issue-audit-2026-10-12/repo-docs.md` (git-ignored, owner's machine).

## Global Constraints

See `00-master.md` section 2. Wave-specific lines:

- Edit only the files a task lists. `docs/KNOWN_ISSUES.md` changes only in W0-T1;
  `CHANGELOG.md` only where a task says so (each W5 task adds its own bullet under
  `[Unreleased]`; the lead merges them).
- Wave 0 changes no code and no test. Code-comment rows of the old W0-T2 belong to the
  code tasks of master section 6.
- W0-T1: every sentence of `git show HEAD:docs/KNOWN_ISSUES.md` stays in the new file
  (headings may change). The checker is in Task W0-T1.
- A doc sentence is exact copy: use the sentences given here. A fact you cannot verify in
  the tree at dispatch is reported, not guessed.
- New code uses `pathlib` / `os.path`, no drive-letter literal, no `os.startfile` (O21).
  The one allowed literal is the owner's default data folder in `tests/real_data.py`
  (W5-T4).
- The venv's setuptools is 65.5.0 and its `packaging` is 24.1 (no `packaging.licenses`).
  Do not `pip install` into the venv; see W5-T1 for what that means.
- No GPU run is needed by any task of this file. The GPU lock of the master is not taken.

## Review Focus

Inputs most likely to bite, each pinned by a test in the named task:

1. **A frozen bundle or a wheel without `LICENSE` / `CITATION.cff`.** Expected: the build
   stops when a required file is missing; the About dialog shows its licence row and its
   citation from the bundle. Tests in W5-T1 and W5-T3.
2. **The venv's old setuptools reading the new `pyproject.toml`.** Expected: the packaging
   tests skip with a reason below setuptools 77 instead of erroring. Test in W5-T1.
3. **A weights download that is cut off or corrupted.** Expected: `fetch_models` refuses it,
   removes the partial file, exits non-zero, and never leaves a file that `verify` rejects
   under the registered name. Tests in W5-T2.
4. **Examples that go stale after a science commit** (calibration, estimator): Expected: a
   test fails and names `scripts/make_examples.py`. Test in W5-T4.
5. **A doc that names a file that no longer exists** (the failure that left
   `CUDA_DISTRIBUTION_NOTES.md` in a doc). Expected: a test fails with the doc, line and
   path. Test in W5-T7.
6. **A KNOWN_ISSUES restructure that drops a sentence.** Expected: the checker lists it.
   W0-T1.

---

# Wave 0

Lane `L-docs`: W0-T2, then W0-T1, then W1-T7 (not in this file). Both tasks edit documents
only; nothing here has a test. Verification is by command, given per task.

## Task W0-T2: Correct the stale statements in the documents

**Model:** implementer sonnet / medium, reviewer sonnet / low. **Lane:** L-docs, first.

**Files (modify, one commit per file, in this order):**
`CHANGELOG.md`, `docs/MODELS.md`, `docs/DATA_DICTIONARY.md`,
`docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`, `README.md`,
`CLAUDE.md`.

**Rows of the old W0-T2 table that this task does NOT apply** (each is rewritten or made
true by the task named, so editing it now would be undone; the lead hands the task the row):

| Row | Why skipped | Owner of the row |
|---|---|---|
| `README.md:232` Discard sentence | W1-T4 makes "go back to what is on disk" true | W1-T4 |
| `docs/DATA_DICTIONARY.md:132` "Saving an empty mask deletes the file" | O9: an empty save writes an empty GT mask | WG-T2 (text), WG-T10 (vocabulary) |
| `docs/DATA_DICTIONARY.md:30` case-variant entry | W1-T5 rewrites it (stems without case) | W1-T5 |
| `docs/Architecture_2.md:54,78,773` | in sections W5-T7 deletes | W5-T7 |
| `CLAUDE.md:51` ("runs on push") and `:53` (pywin32 gate) | CI branches and pywin32 change in WX-T1 / WX-T5 / W5-T6 | W5-T6 owns the whole CI bullet (`CLAUDE.md:49-54`) |
| `CLAUDE.md:55` ("not committed") | true again after W5-T2 untracks the zip | W5-T2 |
| `CLAUDE.md:71` "hand-traced masks" | Generate only replaces predictions after Wave G | WG-T7 (lead: add `CLAUDE.md:71` to its files) |
| `docs/MODELS.md:30-43` "Where to get them" | W5-T2 rewrites it | W5-T2 |
| `docs/SPROUTS_DASH_THEME.md` banner + four lines (audit F7) | file belongs to lane L-viz | W4-T6 (lead: add the four corrections of `repo-docs.md` F7 to W4-T6) |
| Code-comment rows (`app/config.py:61-62`, `metrics.py:17-18`, `shell_chrome.py:21-22`, `main_window.py:209`, `mask_generation_handler.py:122`, `test_robust_generation_handler_ux.py:4`, `project_loader.py:170`, `models.py:10`, `build_support.py:64`) | master section 6 | W3-T2, WG-T5, W1-T1, WG-T7, W1-T5, W5-T2 |
| `docs/KNOWN_ISSUES.md` CQ-07 "figure JSON" | the sentence already carries the audit note; W0-T1 keeps every sentence | none |

- [ ] **Step 1: `CHANGELOG.md`** (3 edits, `Edit` tool, exact strings).

  a. Replace the line `  their length (within 0.6-2.3 % of the generated file on the real data, was 2.5-4.2 %).`
  (`CHANGELOG.md:563`) with:
  ```
    their length (a median 0.8 % longer than the generated file's on the real data, 90 % of
    285 files over 5 mm between -1.0 % and +3.9 %, extremes -3.4 % and +11.3 %, audit
    2026-10-12; was 2.5-4.2 %).
  ```
  b. In the batch-size correction (`CHANGELOG.md:546`) replace `On 300 images, masks made` with
  `On 300 synthetic images, masks made` (keep the line wrap; the next words move down).
  c. Replace `CPU runs were not measured. A fixed batch per model is an owner decision` (`:553`)
  with
  ```
  On the CPU, 8 real images gave identical masks at batch 4, 2 and 1; skeletons at batch 8
  and 1 differed in one value. A fixed batch per model is an owner decision
  ```
  d. At `CHANGELOG.md:424-425` replace the words `the dashboard's served layout, callbacks and`
  (end of line 424) and `figure JSON.` (start of line 425) with
  `the hash of the dashboard's served layout, its callback` and `wiring and the hash of its clientside JS.`
  (keep the indentation and re-wrap if a line passes 92 columns).

- [ ] **Step 2: `docs/MODELS.md`.**

  a. Line 49: replace ``run by `app/handlers/mask_generation_handler.py` `` with
  ``run by `app/inference/mask_inference.py` (started by `app/handlers/mask_generation_handler.py`) ``.
  b. Line 52: replace ``constants in `mask_generation_handler.py` `` with
  ```MASK_MEAN` and `MASK_STD` in `app/inference/mask_inference.py` ``.
  c. Line 54 (Precision row of the mask model): replace
  `On CUDA the runtime (`app/inference/runtime.py`) may enable cuDNN autotuning, TF32 and the channels-last memory format; outputs can then differ by a few pixels between runs and from CPU (see Known limits).` with
  ```
  On CUDA the runtime (`app/inference/runtime.py`) uses TF32 and the channels-last memory format; cuDNN autotuning is off and deterministic algorithms are on by default (since `abc34fa`; `SPROUTS_CUDNN_BENCHMARK=1` opts out). Outputs can still differ by a few pixels from CPU and between batch sizes (see Known limits).
  ```
  d. Lines 82-85 (the "GPU reproducibility" bullet): replace the whole bullet with
  ```
  - **GPU reproducibility.** cuDNN autotuning is off by default, so repeated GPU runs at the
    same batch size reproduce; a different batch size can flip a few pixels (the batch
    shape decides which kernel runs), and GPU and CPU differ slightly.
    `SPROUTS_CUDNN_BENCHMARK=1` brings back the run-to-run noise measured in a 2026-09-28
    audit of 12 images: 18 of 24 output files differed between two identical runs and one
    image's CSV values moved (length 60.75 vs 60.77 mm, area 126.62 vs 126.63 mm²).
  ```
  e. The skeleton model's Precision row (`Same CUDA runtime caveats as the mask model.`) stays.
  f. Run `./venv/Scripts/python.exe -m pytest tests/test_fair_docs.py -v`; expect 2 passed
  (paths and digests untouched).

- [ ] **Step 3: `docs/DATA_DICTIONARY.md:148`.** Replace
  `(corrections saved before 2026-09-29 are 341 x 256)` with
  `(builds before commit `d1645d2` of 2026-09-29, the published `main` among them, save 341 x 256)`.
  Verified: `d1645d2` is in `origin/feat/unified-correct-editor`, not in `origin/main`.

- [ ] **Step 4: the unified-editor spec** (a record: add parentheses, do not rewrite).

  a. `:714-716` Q4 bullet: append after `Track it?` the sentence
  ` Closed 2026-10-12 as not an issue: no reader applies the EXIF orientation (`docs/KNOWN_ISSUES.md` Q4).`
  b. `:686` (D23): append inside the last cell, after `follow-up.`, the text
  ` Audit 2026-10-12: the first click after an open or an edit costs about 1.1 s on the dense 2048 px skeleton, later clicks 70 ms, and 11 ms on a real 640 x 480 one; the nearest-pixel pick differs in 2.4-3.4 % of clicks within 3 px of the skeleton.`
  c. `:725-726` (section 20): after `with the semantics decision)` add
  ` (audit 2026-10-12: see D23)`.

- [ ] **Step 5: `README.md:156-166`** (calibration). Keep the existing text. After the
  bullet list (the `Length` bullet ends at `:163`) and before `If your imaging system has a
  different FOV` (`:165`), insert this paragraph with a blank line on each side:
  ```
  Every image is taken to cover the whole field of view, whatever its size, aspect or crop:
  a quarter-covered mask of a 640 × 480 frame, of a 320 × 240 crop and of a 1920 × 1080
  frame all measure the same area. The pixel is not square (0.028125 × 0.027083 mm for
  640 × 480).
  ```
  (W3-T2 rewrites the whole calibration section when it switches the default to 18 × 13.5 mm.)

- [ ] **Step 6: `CLAUDE.md`** (the owner's change is committed in `ec6c5f9`; check
  `git status --short CLAUDE.md` is empty first). Edit these, line numbers of today:

  | Line | Old text | New text |
  |---|---|---|
  | 70 | `(device/autocast/channels_last)` | `(device / TF32 precision / channels_last; no autocast, cuDNN deterministic by default)` |
  | 74 | `pins the served layout, callbacks and figure JSON.` | `pins the layout hash, the callback wiring and the clientside JS hash.` |
  | 75 | `ResNet segmentation (`EnhancedResnetGenerator`, SE/`DualAttention` blocks).` | ``ResNet-18 segmentation network `ResNetSkeleton` (the name is historical: it is the mask network) with `DualAttention` blocks; `EnhancedResnetGenerator` and the SE layer are the skeleton generator in `app/inference/skeleton_inference.py`.`` |
  | 84-85 (the text `` `resources/themes/dark_theme.qss` + `app/gui/widgets/tokens.py` (SPROUTS design`` at the end of line 84 and `tokens;` at the start of 85) | ``resources/themes/dark_theme.qss` + `app/gui/widgets/tokens.py` (SPROUTS design tokens;`` | ``app/design/tokens.py` (SPROUTS design tokens, Qt-free; Qt code imports them as `app.gui.widgets.tokens`, a re-export) + `resources/themes/dark_theme.qss` (every colour in it is one of those values, `tests/test_design_system_qss.py`;`` |
  | 94 (Docs list) | after the `docs/Architecture_2.md` line | add two lines: ``- `docs/MODELS.md` — model card: checkpoints, SHA-256, inputs and outputs.`` and ``- `docs/DATA_DICTIONARY.md` — project folder, file formats, CSV columns, provenance.`` |

  Verified today: `ResNetSkeleton` is `app/mask_model/model.py:54`; `EnhancedResnetGenerator`
  is `app/inference/skeleton_inference.py`; `tests/test_design_system_qss.py` exists.

- [ ] **Step 7: verify.**
  `./venv/Scripts/python.exe -m pytest tests/test_tooling_ruff_only.py tests/test_fair_docs.py -v`
  (the first reads `CLAUDE.md`); expect all pass. `git diff --stat` shows only the six files.

- [ ] **Step 8: commits (lead), one per file.**
  `docs: CHANGELOG.md matches the code at 19f3761`, `docs: MODELS.md matches the code at 19f3761`,
  `docs: DATA_DICTIONARY.md matches the code at 19f3761`,
  `docs: unified-editor spec carries the 2026-10-12 audit numbers`,
  `docs: README.md matches the code at 19f3761`, `docs: CLAUDE.md matches the code at 19f3761`.

## Task W0-T1: Restructure `docs/KNOWN_ISSUES.md`

**Model:** implementer sonnet / high, reviewer sonnet / medium. **Lane:** L-docs, after W0-T2.

**Files:** Modify `docs/KNOWN_ISSUES.md` only (593 lines today; HEAD `19f3761`).

**Design.** The file becomes: a status line; three tables that carry the live state; the
old "by design" section; then the old text kept word for word, newest pass first. No
sentence is rewritten, so the restructure cannot lose one.

New order (headings are free to change, body text is not):

1. `# SPROUTS — known issues and open decisions` and a status paragraph (text below).
2. `## Decisions: asked on 2026-10-12, answered 2026-10-12 and 2026-10-25` (table 1).
3. `## Open: engineering` (table 2).
4. `## Closed on 2026-10-25 (accepted, nothing to build)` (table 3).
5. `## By design (scope limits)`: old lines 32-49 (old section 4) verbatim.
6. `## Details and history`, newest pass first, each old section verbatim under a new
   `###` heading, in this order: old section 8 (lines 496-593), old 7 (264-494), old 6
   (146-262), old 5 (51-144), old 1-3 (5-30). Before them, one paragraph that keeps the
   old intro (lines 1-3) and maps old section numbers.

Old line ranges are those of `git show HEAD:docs/KNOWN_ISSUES.md`; cut with
`git show HEAD:docs/KNOWN_ISSUES.md | sed -n 'A,Bp'` so nothing is retyped.

Status paragraph (exact):

```
Status of 2026-10-25 at HEAD `19f3761` on `feat/unified-correct-editor`. Every owner
question of the 2026-10-12 audit is answered (table 1); each answer names the task that
builds it in `docs/superpowers/plans/2026-10-25-owner-decisions/00-master.md`. Table 2 lists
every engineering item with its ID, the old names it was known by, and the task. Table 3
lists what was closed as accepted. The text below the tables is the record: the entries keep
their original wording and their "Open" / "Resolved" labels as of the pass that wrote them,
so read the tables for the present state. A task that lands moves its rows to the history
with the commit hash.
```

Paragraph that opens "Details and history" (exact; the first sentence keeps the old
intro, `Tracked during ... work.` and `These are open items, ... unless noted.`):

```
Introduction of the file until 2026-10-25: Tracked during the `feat/sprouts-redesign` work. These are open items, not regressions introduced by the redesign unless noted. Old section numbers used inside the entries below: sections 1 to 3 are "Early passes", section 4 is "By design (scope limits)", section 5 is the 2026-09-28 optimization pass, section 6 the 2026-09-29 unified Correct editor, section 7 the 2026-09-30 UX and speed pass, section 8 the 2026-10-12 audit.
```

Headings for the moved sections: `### Audit of 2026-10-12 (old section 8)`,
`### UX and speed pass, 2026-09-30 (old section 7)`,
`### Unified Correct editor, 2026-09-29 (old section 6)`,
`### Optimization pass, 2026-09-28 (old section 5)`,
`### Early passes, 2026-06 (old sections 1 to 3)` (the three old `##` headings become
`####` under it).

**Table 1** (exact; columns `O`, decision, built by; every row's state is "decided, not built"):

```
| O | Decision | Built by |
|---|---|---|
| O1 | Measure reads GT first for both length and area; last column `Source` (`gt` or `prediction`) | WG-T8 |
| O2 | An unedited Save is not acceptance; a layer is written only when it differs from what it would otherwise read | W1-T3, WG-T2, WG-T3 |
| O3 | Same camera model; 18 x 13.5 mm; one calibration per project file | W3-T2 |
| O4 | Fixed-shape batches; stop on out-of-memory; default on after a GPU session | W2-T3, W3-T3 |
| O5 | Deterministic cuDNN stays the default | W0-T2 (docs only) |
| O6 | PNG and JPEG only, any case | W1-T5 |
| O7 | Stems compare without case on every platform | W1-T5 |
| O8 | Generate keeps the model size; nearest on display | W3-T4 |
| O9 | A cleared mask stays as an empty GT mask; Generate never touches GT | WG-T2, WG-T7 |
| O10 | One mask threshold, 127 | W2-T6 |
| O11 | A click with the 1 px brush paints one pixel | W3-T5 |
| O12 | Keep the 8-connected arc length; pixels are square; fix the 4.4 % bias | W3-T8 |
| O13 | Old 341 x 256 corrections are never measured and are flagged | WG-T3, WG-T8 |
| O14 | Say that the mask gives area, the skeleton gives length, independently | W2-T8a, WG-T10 |
| O15 | Refuse a folder change while a job runs | W1-T1 |
| O16 | A run that writes nothing keeps the record; keep a run history | W2-T1 |
| O17 | Buttons "Measure Length" / "Measure Area"; no incremental Measure | W2-T8a |
| O18 | One Clear, in the dock; Save accent only while something is unsaved | W4-T1 |
| O19 | Discard reloads a changed file; Save asks first | W1-T4, WG-T4 |
| O20 | Toast when a smoothed, uncommitted pick is dropped | W4-T2 |
| O21 | Linux and macOS; GPU optional on macOS; no Windows-only packages | WX-T1 to WX-T5 |
| O22 | "Smooth JPEG blocks" switch only; no display upscaler meets the bar | W4-T5 |
| O23 | One unified editor: Trace GT becomes a layer of Correct | WG-T4, WG-T5 |
| O24 | Restore retired classes; Remove GT; GT in the Library; per-class pixel counts; no COCO | WG-T6, WG-T9 |
| O25 | Accept the compact top bar | closed (table 3) |
| O26 | "Prediction" for model output, "GT" for a human-saved result; a third Library mark | WG-T6, WG-T10 |
| O27 | One naming scheme for dashboard views; one blue date ramp | W4-T6 |
| O28 | Pre-Generate scan as a warm-up only together with other Generate work | W6-T3 (optional) |
| O29 | Canvas indicator: warn toast and About row | W2-T9 |
| O30 | Do not build the editor pages after the first paint | closed (table 3) |
| O31 | Uninstall `pipreqs` and `ipython` from the runtime venv (the owner runs it) | W6-T4 |
| O32 | GPL-3.0-or-later code, CC-BY-4.0 weights; copyright "the SPROUTS authors" | W5-T1 |
| O33 | Only the weights go to Zenodo; `checkpoints.zip` is untracked | W5-T2 |
| O34 | Cite as "SPROUTS"; authors Parth, Varela, Leakey; the tag is the owner's | W5-T3 |
| O35 | A few example images with mask, skeleton and a result graph | W5-T4 |
| O36 | Delete `docs/Architecture_2 useful.*` and `docs/BUILD_EXECUTABLE.md` | W5-T7 |
| O37 | Masks, skeletons and GT files whose image is gone are skipped and recorded, never deleted | W2-T11 |
| O38 | Only the file name in `Error` cells | W3-T7 |
```

**Table 2** (exact; columns `ID`, `Also known as`, item, decision / task; state: not built,
unless the last cell says otherwise):

```
| ID | Also known as | Item | Decision, built by |
|---|---|---|---|
| L1 | FR-03 | Corrected skeletons are not measured | O1, WG-T8 |
| L2 | V1 | Save with nothing unsaved writes a correction | O2, W1-T3 |
| L3 | | Mask and skeleton are independent and the pages do not say so | O14, W2-T8a, WG-T10 |
| L5 | Q1 leftover | Two thinning paths; old 341 x 256 corrections | O13, WG-T3, WG-T8 |
| M1 | | A model mask on a photo that is not 640 x 480 | O8, W1-T2, W3-T4 |
| M2 | | One field of view for every image | O3, W2-T4, W3-T2 |
| M4 | | Mask origin by PNG colour type; a cleared mask is regenerated | O9, WG-T2, WG-T7 |
| M5 | | Area of editor masks; two thresholds | O10, W2-T6 |
| R1 | batch-size item of section 7 | Batch size changes GPU outputs | O4, W2-T3, W3-T3 |
| R3 | supported formats | Formats differ per reader | O6, W1-T5 |
| R4 | case-variant stems | Case-variant stems | O7, W1-T5 |
| R5 | "Changed since measured" | Compares file times | W2-T2 |
| R6 | | A run that writes nothing replaces `_sprouts_run.json` | O16, W2-T1 |
| R7 | | A job ends after another folder was opened | O15, W1-T1 |
| R8 | W12 | Measure buttons are nouns | O17, W2-T8a (incremental Measure closed, table 3) |
| E1 | W16 | Save placement and states | O18, W4-T1 |
| E2 | | Discard / Save after an external change | O19, W1-T4, WG-T4 |
| E3 | J1 | Smooth on a picked branch | O20, W4-T2 |
| E4 | | A click with brush size 1 paints nothing | O11, W3-T5 |
| E6 | | Pinch zoom (bicubic refine closed, table 3) | O21, WX-T3 |
| E7 | Q2 | JPEG deblock | O22, W4-T5 |
| G1 | | Two tracing interfaces | O23, WG-T4, WG-T5 |
| G2 | | Trace GT v1 limits; retired classes cannot return | O24, WG-T6, WG-T9 |
| G4 | | Naming: trace, correct, GT, mask | O26, WG-T6, WG-T10 |
| P2 | | First image read on the GUI thread | not built now (only for images larger than 640 x 480): W6-T1 |
| P3 | | Deflate in the skeleton history | not built now (only for large images): W6-T2 |
| P6 | | Hardware OpenGL check; no indicator | O29, W2-T9 |
| D2 | | Pre-Generate scan as a warm-up | O28, W6-T3 (optional) |
| D5 | | IPython in the venv | O31, W6-T4 |
| D7 | W19 | Dashboard view names and date colours | O27, W4-T6 |
| F1 | FR-01 | No licence; Pix2Pix notice missing | O32, W1-T7, W5-T1 |
| F2 | FR-07 | `checkpoints.zip` tracked; no DOI; no mask model | O33, W5-T2 |
| F3 | | Metadata placeholders; citation not shipped | O34, W5-T3 |
| F4 | Q3 | Real images as test data; stale gate | O35, W5-T4 |
| F5 | CQ-10 | Compat shims; stamp helpers | W5-T5 |
| F6 | | Script teardown note | W5-T7 (note moves to `CLAUDE.md`) |
| F7 | | Documents consistent; this file | W0-T1, W0-T2, W5-T7, O36 |
| X1 | | The length estimator reads about 4.4 % long | O12, W3-T8 |
| X2 | | Measure measures masks whose image is gone | O37, W2-T11 |
| X3 | | `Error` cells hold absolute paths | O38, W3-T7 |
| X5 | | pytest writes into the user's log | W5-T6 |
| X6 | | `fault.log` wording; start-up Qt warnings | W5-T6 |
| X7 | | CI has not run since 2026-07-25 | W5-T6, WX-T5 |
| X8 | | Stamp helpers defined six times | W5-T5 |
| X9 | | Correct with no image shows a tool hint | W2-T8a |
| XP1 | | Linux and macOS support (new) | O21, WX-T1 to WX-T5 |
```

**Table 3** (exact; closed because the cost outweighs the gain, or the item is by design):

```
| ID | Closed as | Reason |
|---|---|---|
| P1 (O30) | Do not defer the editor pages | 124 ms of `MainWindow()`; a deferred build gives the same first paint and then GUI blocks of 83 ms and 39 ms; on the real platform it is 130 ms of 0.87 s |
| P4 | Leave the halo layer | raster only, 6.7 ms at 4K and 800 %; the OpenGL path, which every logged launch uses, is not affected |
| P5 | Leave the first skeleton frame | 3.75 ms once on the stress skeleton, 0.98 ms on a real one |
| P7 | Leave the warm-up leftovers | the window is gone 15 ms after close; the 1.25 s wait is invisible, and a daemon thread risks a crash at exit |
| D1 | Leave `refresh_outputs` on the GUI thread | 94 ms for 23k + 23k once per pass; an asynchronous scan races with saves |
| D3 | Keep raster viewports while a dashboard lives | one graphics backend per top-level window; a separate window is a UX regression |
| D4 | Leave the first dashboard open | the 234 ms is the QtWebEngine start, bound to the GUI thread |
| D6 | Keep the 30-bar label limit | a zoom-following callback costs a second full draw on the largest trials |
| E6 (bicubic) | Bicubic refine not planned | bicubic sharpens the JPEG blocks; pinch is built in WX-T3 |
| G3 (O25) | Compact top bar accepted | built and tested; the alternative is a 1040 px minimum window |
| L4 | Do not rename `<stem>_fake.png` | cosmetic gain; a reader for two names for good (W2-T8a adds one constant and one sentence) |
| M3 | No reader change for EXIF | every reader already uses the stored grid (W2-T10 pins the policy with tests) |
| R2 (O5) | Deterministic cuDNN stays the default | already in the code and in every run record |
| R8 (O17, incremental part) | No incremental Measure | a safe reuse has to verify content, which costs as much as measuring (17 to 21 s for 46k) |
| E5 | Exact windowed pick not built | 11 ms per pick on a real 640 x 480 skeleton (master section 1.1) |
| X4 | Generate overwriting a mask saved during a run: dropped | Generate never writes `gt/`; a mask saved during a run is GT (master section 1.1) |
```

The last two rows are not in the old plan's section 4; they come from master section 1.1
(the lead confirms them at review).

- [ ] **Step 1: save the checker** to the scratchpad as `keep.py` (exact code, the only
  algorithm in this task):

```python
"""Every sentence of the old docs/KNOWN_ISSUES.md must still be in the new one."""
import re, subprocess, sys
from pathlib import Path

START = re.compile(r"^\s*([-*]|\d+\.)\s")


def blocks(text: str) -> list[str]:
    """Bullet / paragraph blocks as one normalised line each; headings are dropped."""
    out: list[list[str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            out.append([])
        elif START.match(line):
            out.append([START.sub("", line, count=1)])
        else:
            if not out:
                out.append([])
            out[-1].append(line)
    return [re.sub(r"\s+", " ", " ".join(b)).strip() for b in out if b]


def sentences(text: str) -> list[str]:
    return [s for b in blocks(text) for s in re.split(r"(?<=[.!?])\s+", b) if len(s) > 3]


old = subprocess.run(["git", "show", "HEAD:docs/KNOWN_ISSUES.md"], capture_output=True,
                     text=True, encoding="utf-8", check=True).stdout
new = "\n".join(blocks(Path(sys.argv[1]).read_text(encoding="utf-8")))
missing = [s for s in sentences(old) if s not in new]
print(f"{len(sentences(old))} old sentences, {len(missing)} missing")
for s in missing[:20]:
    print("  MISSING:", s[:140])
sys.exit(1 if missing else 0)
```

  Run it on the unmodified file: `./venv/Scripts/python.exe -I <scratchpad>/keep.py docs/KNOWN_ISSUES.md`.
  Expected: `293 old sentences, 0 missing`, exit 0 (verified today).

- [ ] **Step 2: build the new file** from the design above. Use a small throwaway script
  (scratchpad) that writes the new heading blocks and appends the `sed -n` ranges, then
  review it by eye. Do not retype old text.

- [ ] **Step 3: run the checker** on `docs/KNOWN_ISSUES.md`. Expected: `0 missing`, exit 0.
  A missing sentence means a range was cut wrong: fix the range, not the sentence.

- [ ] **Step 4: every ID is in a table.** Run (scratchpad):

```python
import re, sys
from pathlib import Path
text = Path("docs/KNOWN_ISSUES.md").read_text(encoding="utf-8")
ids = [f"{p}{n}" for p, rng in (("L", (1, 2, 3, 5)), ("M", (1, 2, 4, 5)), ("R", range(1, 9)),
       ("E", (1, 2, 3, 4, 6, 7)), ("G", (1, 2, 4)), ("P", (2, 3, 6)), ("D", (2, 5, 7)),
       ("F", range(1, 8)), ("X", (1, 2, 3, 5, 6, 7, 8, 9))) for n in rng]
ids += ["XP1"] + [f"O{n}" for n in range(1, 39)]
rows = {m.group(1).split()[0] for m in re.finditer(r"^\| ([A-Z]+\d+[^|]*) \|", text, re.M)}
missing = [i for i in ids if i not in rows]
closed = ["P1", "P4", "P5", "P7", "D1", "D3", "D4", "D6", "G3", "L4", "M3", "R2", "E5", "X4"]
missing += [i for i in closed if i not in rows]
print("missing rows:", missing)
sys.exit(1 if missing else 0)
```
  Expected: `missing rows: []`.

- [ ] **Step 5: read the file once top to bottom** (rendering of the tables: each row has the
  same number of cells; no stray `|`). `git diff --stat` shows one file.

- [ ] **Step 6: commit (lead):**
  `docs(known-issues): decisions and open items first, with IDs; every old sentence kept below`

---

# Wave 5

Lane `L-repo`: W5-T1 (after WX-T1 and W1-T7) → W5-T2 → W5-T3; W5-T4 (after WG-T8; see its
note on W3-T8) → W5-T6 (after WX-T5) → W5-T7 (after WG-T10) → W6-T4. W5-T5 runs on L-correct
after WG-T5 and W1-T4. The master's After column for W5-T1 lacks W1-T7 (it creates
`THIRD_PARTY_NOTICES.md`, which W5-T1 bundles); add it.

## Task W5-T1: Licence (O32)

**Model:** implementer sonnet / medium, reviewer sonnet / medium. **Lane:** L-repo.

**Files:**
- Create: `LICENSE`.
- Modify: `pyproject.toml:2,14-15`, `requirements-dev.txt`, `CITATION.cff`, `codemeta.json`,
  `README.md:335-339` (and the build section `:303-314`), `docs/MODELS.md`, `app/__init__.py`,
  `app/models.py` (`ModelSpec.license`), `app/gui/about_dialog.py` (`AboutDialog.__init__`,
  `details_text`), `scripts/build_support.py` (`REQUIRED_DATAS`), `CHANGELOG.md`.
- Test: modify `tests/test_fair_metadata.py`, `tests/test_fair_build.py`,
  `tests/test_fair_docs.py`, `tests/test_fair_packaging.py` (fixture skip),
  `tests/test_shell_info_about.py`; create `tests/test_license_file.py`.

**Interfaces:**
- Produces `app.LICENSE_SPDX: str = "GPL-3.0-or-later"` and
  `app.COPYRIGHT_HOLDER: str = "the SPROUTS authors"` (in `app/__init__.py`, added to
  `__all__`). `ModelSpec.license: str = "CC-BY-4.0"` (new last field, defaulted).
  `about_dialog.details_text()` gains a line `License: GPL-3.0-or-later (weights CC-BY-4.0)`
  after `Commit:`.
- Consumes W1-T7's `THIRD_PARTY_NOTICES.md` (repository root).

**Text, exact.**

`LICENSE` = this header, a blank line, then the FSF text of GPL-3.0 byte for byte from
`https://www.gnu.org/licenses/gpl-3.0.txt` (674 lines, 35,149 bytes, SHA-256
`3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986` on 2026-10-25, LF line ends):

```
SPROUTS - plant-root phenotyping from minirhizotron images
Copyright (c) 2026 the SPROUTS authors

This program is free software: you can redistribute it and/or modify it under the terms of
the GNU General Public License as published by the Free Software Foundation, either version
3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Public License below for more details.

The model weights are not part of this program's source and are licensed separately under
CC-BY-4.0 (docs/MODELS.md). Third-party notices: THIRD_PARTY_NOTICES.md and
app/assets/THIRD_PARTY.md.

================================================================================

```

`pyproject.toml`: `requires = ["setuptools>=77"]`; replace the two `TODO(owner)` comment
lines (`:14-15`) with
```
license = "GPL-3.0-or-later"
license-files = ["LICENSE", "THIRD_PARTY_NOTICES.md", "app/assets/licenses/*"]
```
No `License ::` classifier (setuptools rejects both). `requirements-dev.txt`: add
`setuptools>=77  # reads the PEP 639 licence fields (tests/test_fair_packaging.py)`.

`CITATION.cff`: add `license: GPL-3.0-or-later` after `type: software`; drop the TODO line
`add `license:` ...`. `codemeta.json`: add `"license": "https://spdx.org/licenses/GPL-3.0-or-later",`
after `"version"`.

`README.md` licence section (replace `:335-339`; heading stays `## License`):

```
SPROUTS is free software under the **GNU General Public License v3.0 or later**
(`GPL-3.0-or-later`, see [`LICENSE`](LICENSE)); PyQt6, which it imports, is GPL-3.0 too.
Copyright (c) 2026 the SPROUTS authors. Third-party code and assets keep their licences:
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and
[`app/assets/THIRD_PARTY.md`](app/assets/THIRD_PARTY.md). The **model weights** are not
part of the source and are licensed separately under **CC-BY-4.0**
([`docs/MODELS.md`](docs/MODELS.md)). The three example images in `docs/examples/` are
described in their own README.
```
(The last sentence is added by W5-T4's commit, not here: leave it out in this task.)

README build section, append one paragraph after the `_internal\build_info.json` sentence:
```
The bundle is distributed under the GPL: ship `LICENSE` and `THIRD_PARTY_NOTICES.md` (the
build copies both into the bundle root) and, on request, the source of the commit recorded
in `_internal\build_info.json`. A CPU-only PyTorch (README "Installation") makes a much
smaller bundle.
```

`docs/MODELS.md`: in "Where to get them" nothing changes (W5-T2 rewrites it); add a new
section after "Identity and verification":
```
## Licence of the weights

Both checkpoints are released under **CC-BY-4.0** (https://creativecommons.org/licenses/by/4.0/):
you may share and adapt them if you give credit (cite SPROUTS, see the README). The licence
of the weights is separate from the GPL-3.0-or-later licence of the code. `ModelSpec.license`
in `app/models.py` records it for each checkpoint.
```

`app/gui/about_dialog.py`: `_add_fact("License", f"{LICENSE_SPDX} · weights CC-BY-4.0")`
after the `Commit` fact; `details_text` line as in Interfaces; both import the constant from
`app`.

`scripts/build_support.py::REQUIRED_DATAS` gains `("LICENSE", ".")` and
`("THIRD_PARTY_NOTICES.md", ".")`.

- [ ] **Step 1: write the failing tests.**

  `tests/test_license_file.py`:
  ```python
  def test_license_is_gpl3_text_under_the_projects_copyright_line():
      text = (ROOT / "LICENSE").read_text(encoding="utf-8")   # universal newlines
      assert "Copyright (c) 2026 the SPROUTS authors" in text
      marker = "                    GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007"
      body = text[text.index(marker):]
      assert len(body.splitlines()) == 674
      assert hashlib.sha256(body.encode()).hexdigest() == GPL3_SHA256   # the value above
  def test_spdx_constant_is_the_one_in_every_metadata_file(): ...
  ```
  The second asserts `app.LICENSE_SPDX == "GPL-3.0-or-later"` and equals the `pyproject.toml`
  `license` string (via `tomllib`/`tomli`), `CITATION.cff` `license` (yaml) and the last
  path segment of `codemeta.json` `license`. (`packaging.licenses` does not exist in the
  venv; do not use it.) Also assert `ModelSpec.license == "CC-BY-4.0"` for every key of
  `app.models.MODELS`.

  `tests/test_fair_build.py`: add
  ```python
  def test_licence_files_are_bundled_at_the_bundle_root(bs):
      datas = dict(bs.REQUIRED_DATAS)
      assert datas["LICENSE"] == "." and datas["THIRD_PARTY_NOTICES.md"] == "."
      resolved, _warnings = bs.resolve_datas(ROOT)
      assert {Path(s).name for s, d in resolved if d == "."} >= {"LICENSE", "THIRD_PARTY_NOTICES.md"}
  ```

  `tests/test_fair_docs.py`: add `test_model_card_states_the_weights_licence`: `"CC-BY-4.0"`
  is in `docs/MODELS.md`.

  `tests/test_shell_info_about.py`: add `test_dialog_and_details_show_the_licence`: the
  dialog's label texts (helper `_texts(dlg)`) contain `GPL-3.0-or-later`, and
  `about_dialog.details_text()` contains `License: GPL-3.0-or-later`.

  `tests/test_fair_packaging.py` fixture `expanded`: before `read_configuration`, add
  ```python
  setuptools = pytest.importorskip("setuptools")
  if Version(setuptools.__version__) < Version("77"):
      pytest.skip("setuptools < 77 cannot read the PEP 639 licence fields of pyproject.toml")
  ```
  (`Version` from `packaging.version`, already used by `Requirement`'s package.)

- [ ] **Step 2: run to see failures.**
  `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_license_file.py tests/test_fair_build.py tests/test_fair_docs.py tests/test_fair_metadata.py tests/test_fair_packaging.py tests/test_shell_info_about.py -v`
  Expected: the new tests fail (no `LICENSE`, no constants); the other tests pass.

- [ ] **Step 3: create `LICENSE`.** `curl -sSL https://www.gnu.org/licenses/gpl-3.0.txt`
  into the scratchpad, check `sha256sum` equals the value above (if it differs, stop and
  report: the FSF changed the file), then write header + text with the `Write` tool or a
  script. Keep LF line ends.

- [ ] **Step 4: implement** the constants, `ModelSpec.license`, the About fact and details
  line, `REQUIRED_DATAS`, `pyproject.toml`, `requirements-dev.txt`, `CITATION.cff`,
  `codemeta.json`, `README.md`, `docs/MODELS.md` with the exact text above.

- [ ] **Step 5: run the same pytest command.** Expected: all pass. In this venv
  `tests/test_fair_packaging.py` reports its tests as skipped (setuptools 65.5.0); that is
  the intended result. The lead may upgrade the venv's setuptools to run them locally
  (`./venv/Scripts/python.exe -m pip install "setuptools>=77"`, needs the lead's approval);
  CI always runs them.
  `test_license_is_declared_everywhere_or_nowhere` now takes its "has file" branch and passes.

- [ ] **Step 6: ruff** on `app/__init__.py app/models.py app/gui/about_dialog.py scripts/build_support.py tests/test_license_file.py tests/test_fair_build.py tests/test_fair_docs.py tests/test_fair_packaging.py tests/test_shell_info_about.py`:
  `./venv/Scripts/python.exe -m ruff check --fix <files>; ./venv/Scripts/python.exe -m ruff format <files>`.

- [ ] **Step 7: CHANGELOG** under `[Unreleased]` `### Added`: one bullet "Licence:
  GPL-3.0-or-later for the code (`LICENSE`), CC-BY-4.0 for the model weights, copyright
  'the SPROUTS authors'; the bundle ships both licence files; About shows the licence."

- [ ] **Step 8: commit (lead):** `feat(repo): GPL-3.0-or-later licence, CC-BY-4.0 weights, licence files in the bundle`
  Files: the list above. The lead also runs, once, a wheel check if setuptools >= 77 is
  available: `./venv/Scripts/python.exe -m pip wheel --no-deps --no-build-isolation -w <scratch> .`
  and confirms `LICENSE` is in `*.dist-info/licenses/`.

## Task W5-T2: Weights policy: untrack the zip, fetch script, Zenodo checklist (O33)

**Model:** implementer sonnet / high, reviewer opus / medium. **Lane:** L-repo, after W5-T1.

**Files:**
- Create: `scripts/fetch_models.py`, `tests/test_fetch_models.py`.
- Modify: `app/models.py` (module docstring `:10-11`; `ModelSpec.description` of `mask`,
  `:80-81`), `scripts/build_support.py:64`, `README.md:58-71` (and `:303-314`),
  `docs/MODELS.md` ("Where to get them", new "Zenodo deposit checklist"),
  `CLAUDE.md:55`, `tests/test_foundation_core_models.py` (add the url/doi test; line 99
  stays: "not committed" is true again), `CHANGELOG.md`.
- **Lead step, not the implementer's:** `git rm --cached checkpoints.zip` (the file stays on
  disk; `.gitignore:4` then applies). No history rewrite: it would change 228 commit hashes
  and break the `git_commit` of every provenance record. Commit it together with this task.

**Interfaces:**
- Produces in `scripts/fetch_models.py` (Qt-free, torch-free, stdlib + `app.models`):
  `fetch(spec: ModelSpec, url: str, root: str | None = None, *, timeout: float = 60.0) -> ModelStatus`
  (downloads to `<final path>.part` beside the final file, flushes, verifies the `.part`
  file's SHA-256 against `spec.sha256`, then `os.replace`s it onto `spec.path(root)`; on a
  mismatch or any `OSError` / `URLError` removes the `.part` file, leaves any existing good
  file alone, and returns `ModelStatus("mismatch" | "missing", ...)`), and
  `main(argv: list[str] | None = None) -> int` (`--key mask|skeleton` repeatable, default
  both; `--url KEY=URL` overrides `ModelSpec.url`; `--root DIR`; exit 0 when every requested
  model verifies after the run, 1 on a failed download or mismatch, 2 when a requested model
  has no URL: message `no download URL registered for 'mask' yet; see docs/MODELS.md`).
  A model that already verifies is skipped without a download.
- Consumes `app.models.MODELS`, `ModelSpec.path(root)`, `verify(spec, root)`.
- `ModelSpec.url` and `.doi` stay `None` for both models until the owner deposits.

**Text, exact.**

`app/models.py` docstring sentence (replace `:10-11`): `The checkpoints are not in git; ``url``/``doi`` stay ``None`` until the weights are published (docs/MODELS.md, "Zenodo deposit checklist"); scripts/fetch_models.py downloads and verifies them once they are.`
`mask` description (replace): `Root mask segmentation, ResNet-18 encoder with DualAttention blocks (app.mask_model, class ResNetSkeleton); sigmoid output binarised at MASK_THRESHOLD 0.5.`
`scripts/build_support.py:64` comment: `# Model weights are not in git (docs/MODELS.md; scripts/fetch_models.py): bundled at their registered path`

`README.md` "Model weights" (replace the paragraph `:67-71`, keep the code block and the
`SPROUTS_CHECKPOINTS_DIR` paragraph):
```
The weights are not stored in this repository. They are published as a Zenodo record
(licence CC-BY-4.0); the link and DOI are in [`docs/MODELS.md`](docs/MODELS.md) once the
record exists, and until then you can get them from the maintainers. With the record's
address registered, `./venv/Scripts/python.exe scripts/fetch_models.py` downloads both files
into `checkpoints/` and checks their SHA-256 (the app also warns in its log when a
checkpoint does not match). The skeleton model of earlier versions is still in the git
history: `git show 2075568:checkpoints.zip > checkpoints.zip`.
```
README build section: add the sentence `Fetch the weights first (the build stops without them).` before the sentence that starts `The scripts work from any folder`... keep that sentence's own mention; do not duplicate: if the existing text already says "stop if the model weights are missing", add only `Run scripts/fetch_models.py first.`

`docs/MODELS.md` "Where to get them" (replace the three bullets with):
```
- **Both checkpoints** are released as one Zenodo record under CC-BY-4.0. The record does
  not exist yet (`url` and `doi` are `None` in `app/models.py`); the checklist below is the
  owner's to complete. Until then ask the maintainers.
- **Download and check:** `./venv/Scripts/python.exe scripts/fetch_models.py` fetches what
  is missing and verifies each file against the digests above; it refuses a file whose
  digest differs and removes it.
- **Skeleton model from git history:** `checkpoints.zip` (47,935,331 bytes, SHA-256
  `6db9555891102e2eff148d22216b141589a28558ed8a6eb85248e4040510e037`) was tracked until
  this release; `git show 2075568:checkpoints.zip > checkpoints.zip` restores it. It holds
  `checkpoints/skeletonizer/latest_net_G.pth` (the digest above) and a stale
  `checkpoints/experiment_name/test_opt.txt`.
- **Mask model:** not distributed in git. Without it, *Generate* cannot produce masks and
  root area cannot be measured from model masks (corrected masks still work).

### Zenodo deposit checklist (owner)

1. Create a new upload on zenodo.org; resource type Software (or Dataset); title "SPROUTS
   model weights (root mask and skeleton models)"; creators Kinjalk Parth, Sebastian
   Varela, Andrew D. B. Leakey; licence **CC-BY-4.0**.
2. Upload exactly `best_mask_model_V5.pth` (338,502,139 bytes) and `latest_net_G.pth`
   (51,749,020 bytes). Check them against the table above before uploading.
3. Description: what each model does (this card), the training data of each (the two
   `TODO(owner)` rows below are still open), the software version that runs them, and a
   related identifier "is supplement to" `https://github.com/leakey-lab/Root-Mask-and-Skeletons`.
4. Publish. Copy the DOI and the two file URLs (`https://zenodo.org/records/<id>/files/<name>`).
5. In `app/models.py` set `url` and `doi` of both `ModelSpec`s; in this file fill the
   table of "Identity and verification" with the DOI; add the DOI as `references` or
   `identifiers` in `CITATION.cff` if wanted.
6. In a clean folder run `scripts/fetch_models.py --root <folder>`; both models must report ok.
```

`CLAUDE.md:55-56` (replace): ``- Model checkpoints are **not** committed (they are published as a Zenodo record, `docs/MODELS.md`; `scripts/fetch_models.py` downloads and verifies them): place them under `checkpoints/` (`mask_weights/best_mask_model_V5.pth`, `skeletonizer/latest_net_G.pth`).``

- [ ] **Step 1: write the failing tests** in `tests/test_fetch_models.py`. A fixture
  starts `http.server.ThreadingHTTPServer` on `127.0.0.1:0` in a thread serving `tmp_path/srv`
  and yields `base_url`; a helper makes a `ModelSpec(key="t", relpath="checkpoints/t.pth", sha256=<of the bytes>, url=None)`.
  - `test_a_good_download_verifies_and_leaves_no_partial_file`: `fetch(spec, base_url + "/t.pth", str(root))` returns `.ok`, the file bytes equal the served bytes, no `*.part` in the folder.
  - `test_a_corrupted_download_is_refused_removed_and_keeps_an_existing_good_file`: serve different bytes; result `state == "mismatch"`, no `.part`, and when a good file already existed it is unchanged.
  - `test_a_missing_url_is_a_download_failure_not_a_crash`: a 404 returns `state == "missing"`, no partial file.
  - `test_main_exit_codes`: `main(["--root", root, "--key", "mask"])` returns 2 and prints the "no download URL registered" message (capsys); with `--url mask=<good url>` and a monkeypatched `MODELS` entry returns 0; with corrupt bytes returns 1; a model that already verifies returns 0 without a request (server counts 0 hits).
  - `test_model_url_and_doi_are_well_formed_when_set` in `tests/test_foundation_core_models.py`: for every `spec` in `MODELS`, `spec.doi is None or re.fullmatch(r"10\.\d{4,9}/\S+", spec.doi)` and `spec.url is None or spec.url.startswith("https://")`.
  The `main` tests monkeypatch `fetch_models.MODELS` (looked up at call time) with
  the test spec. The script is imported by path (`importlib.util.spec_from_file_location`),
  as `tests/test_fair_build.py` does for `build_support`.

- [ ] **Step 2: run** `./venv/Scripts/python.exe -m pytest tests/test_fetch_models.py tests/test_foundation_core_models.py -v`. Expected: `test_fetch_models.py` fails (no script); the url/doi test passes already (vacuous today).

- [ ] **Step 3: implement `scripts/fetch_models.py`** per Interfaces. It inserts the
  repository root into `sys.path` first (as `scripts/build_executable.spec` does) so
  `python scripts/fetch_models.py` finds `app`. Use `urllib.request.urlopen(url, timeout=...)`
  and `app.provenance.file_sha256` for the digest; stream in 1 MiB chunks; print one line
  per model (`mask: ok (downloaded)`, `skeleton: ok (already present)`, `mask: digest
  mismatch, file removed`).

- [ ] **Step 4: edit the docs and comments** with the exact text above.

- [ ] **Step 5: run** the same pytest command plus `tests/test_fair_docs.py tests/test_fair_build.py`. Expected: all pass.

- [ ] **Step 6: ruff** on `scripts/fetch_models.py scripts/build_support.py app/models.py tests/test_fetch_models.py tests/test_foundation_core_models.py`.

- [ ] **Step 7: CHANGELOG** `[Unreleased]` `### Changed`: "Model weights are published
  separately (Zenodo record, CC-BY-4.0) and `checkpoints.zip` is no longer tracked
  (`scripts/fetch_models.py` downloads and verifies them; the zip stays in the history)."

- [ ] **Step 8: commit (lead).** First `git rm --cached checkpoints.zip`, then
  `git add` the task's files, commit `feat(repo): weights are published separately; fetch script; checkpoints.zip untracked`.
  Check `git status --short` shows no `checkpoints.zip` and `ls checkpoints.zip` still finds the file.

## Task W5-T3: Authors, citation as "SPROUTS", shipped citation, release notes (O34)

**Model:** implementer sonnet / medium, reviewer sonnet / low. **Lane:** L-repo, after W5-T2.

**Files:**
- Modify: `CITATION.cff`, `codemeta.json`, `pyproject.toml:12-13`, `README.md:327-333`,
  `scripts/build_support.py::REQUIRED_DATAS`, `CHANGELOG.md`.
- Test: modify `tests/test_fair_metadata.py`, `tests/test_fair_build.py`; modify
  `tests/test_shell_info_about.py` only if the fixture CFF there names the old title.

**Decisions.** Authors, in this order, no ORCIDs (the format of
`E:/RootQuantV2-release/rootquant-v2/CITATION.cff`, read-only): Kinjalk Parth, Sebastian
Varela, Andrew D. B. Leakey. Cited name: "SPROUTS" (`title`); `APP_FULL_NAME` stays the
About heading. The CITATION.cff TODO about ORCID / affiliation / funding stays as an owner
action. The wheel cannot carry a root-level file next to `app/`; wheel installs show no
citation block (the About dialog already tolerates this). Only the bundle ships it. The tag
and the push are the owner's.

**Text, exact.**

`CITATION.cff`: `title: "SPROUTS"`; `authors:` replaced by
```
authors:
  - given-names: Kinjalk
    family-names: Parth
  - given-names: Sebastian
    family-names: Varela
  - given-names: Andrew D. B.
    family-names: Leakey
```
(the `alias: MasterKinjalk` line is dropped, as RootQuantV2's file has none). Header TODO
becomes
```
# TODO(owner) before the first release:
#   - add ORCIDs, affiliations, a contact e-mail and the funding statement (the author list
#     follows RootQuantV2's CITATION.cff: names only);
#   - add `date-released:` and the Zenodo DOI (`doi:` / `identifiers:`) when v3.0.0 is tagged.
```
`codemeta.json`: `"name": "SPROUTS"`; `author` = three `Person` objects with `givenName` /
`familyName` as above (`"Andrew D. B."` / `"Leakey"`). `pyproject.toml`:
```
# TODO(owner): ORCIDs and maintainers (kept in sync with CITATION.cff).
authors = [{ name = "Kinjalk Parth" }, { name = "Sebastian Varela" }, { name = "Andrew D. B. Leakey" }]
```
`README.md` "How to cite" paragraph (replace `:329-333`):
```
Use GitHub's **Cite this repository** button (generated from
[`CITATION.cff`](CITATION.cff)), or cite: Parth, K., Varela, S., & Leakey, A. D. B.
*SPROUTS*, version 3.0.0, https://github.com/leakey-lab/Root-Mask-and-Skeletons. Please
also state the model checkpoints you used (their SHA-256 is in
[`docs/MODELS.md`](docs/MODELS.md)). A DOI will be added when the first release is archived.
```
`REQUIRED_DATAS` gains `("CITATION.cff", ".")`.
`CHANGELOG.md` `[Unreleased]` `### Changed`: "Citation record: authors Parth, Varela and
Leakey; cited as 'SPROUTS'; `CITATION.cff` ships in the Windows bundle so About can show
how to cite."

- [ ] **Step 1: write the failing tests.**
  - `tests/test_fair_metadata.py::test_the_three_authors_are_in_the_agreed_order`:
    `[a["family-names"] for a in cff["authors"]] == ["Parth", "Varela", "Leakey"]`;
    `cff["title"] == "SPROUTS"`.
  - `::test_readme_cites_the_current_version_and_every_author`: take the text after
    `## How to cite` up to the next `## `; `re.search(r"version (\d+\.\d+\.\d+)", ...)`
    group equals `app.__version__`; every `family-names` of `cff["authors"]` is in it.
  - `::test_orcids_have_the_registry_form_when_present`: every author's optional `orcid`
    matches `^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$` (vacuous today).
  - `tests/test_fair_build.py::test_citation_is_bundled_at_the_bundle_root`:
    `dict(bs.REQUIRED_DATAS)["CITATION.cff"] == "."` and `resolve_datas(ROOT)` has a pair
    whose source name is `CITATION.cff`.
  - `tests/test_shell_info_about.py::test_citation_text_names_all_three_authors`: with the
    repository's real `CITATION.cff`, `about_dialog.citation_text()` starts with
    `Parth, K., Varela, S., & Leakey, A. D. B.` and contains `SPROUTS (Version 3.0.0)`.
- [ ] **Step 2: run** `./venv/Scripts/python.exe -m pytest tests/test_fair_metadata.py tests/test_fair_build.py tests/test_shell_info_about.py -v`. Expected: the five new tests fail; the rest pass.
- [ ] **Step 3: make the edits** above. Keep `[Unreleased]` and `## [3.0.0] - not yet tagged`
  as they are: `test_changelog_top_release_is_the_package_version` needs those two headings.
- [ ] **Step 4: run** the same command. Expected: all pass (the existing
  `test_authors_and_keywords_agree_across_metadata_files` now compares three names).
- [ ] **Step 5: ruff** on the changed test files and `scripts/build_support.py`.
- [ ] **Step 6: commit (lead):** `feat(repo): authors Parth, Varela and Leakey; cite as SPROUTS; CITATION.cff ships in the bundle`

**Release notes for the owner (nothing here is done by an agent).** After the merge of this
branch:
1. Fill what `CITATION.cff` still lacks (ORCIDs, affiliations, contact, funding,
   `date-released: <YYYY-MM-DD>`, the Zenodo DOI as `doi:` once the software record exists).
2. In `CHANGELOG.md` rename the heading `## [Unreleased]` to `## [3.0.0] - <date>` merged
   with the old `[3.0.0]` section, and put a new empty `## [Unreleased]` above it
   (`tests/test_fair_metadata.py` needs `Unreleased` first, then the package version).
3. Tag and push: `git tag -a v3.0.0 -m "SPROUTS 3.0.0: GPL-3.0-or-later; one Correct editor with GT layer; Linux and macOS; weights on Zenodo"` then `git push origin v3.0.0`.
4. After the tag, set `app.__version__` to `3.1.0.dev0` and `version:` in `CITATION.cff` and
   `codemeta.json` to the same string (the metadata test needs the three to agree), so
   untagged builds no longer share the released number.

## Task W5-T4: Examples and the real-image golden gate (O35, F4)

**Model:** implementer sonnet / high, reviewer opus / medium. **Lane:** L-repo, after WG-T8.
**Soft dependency (master gap):** run it after W3-T8 if the lane allows. If it runs earlier,
W3-T2 (commit 2) and W3-T8 must run `scripts/make_examples.py` and commit the regenerated
CSVs and graph, because `tests/test_examples.py` fails until they do.

**What the three examples are (found, read-only).** The audit's images exist in
`D:\2024 EF image data` (46,186 entries; found by a `os.scandir` name filter):
- `24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg` (21,877 bytes)
- `24EFCAM1_T001_L050_2024.07.18_111522_001_SMM.jpg` (13,955 bytes)
- `24EFCAM1_T012_L047_2024.07.18_101909_001_SMM.jpg` (15,970 bytes)

Their masks (`mask/<stem>.png`, 3,422 / 437 / 1,752 bytes) and skeletons
(`skeletons/<stem>_fake.png`, 4,476 / 334 / 1,154 bytes) are the production outputs of the
46k project and are byte-identical (SHA-256 checked today) to the copies in
`.claude/handoff/ui-perf-2026-09-28/scratchpad/ui-harness/dataset/`. **Decision: copy these
nine files; do not regenerate.** A new GPU run would give the same model but could flip
pixels with the batch shape (R1) and adds nothing; so no GPU lock is taken. The L050
skeleton is all black (the empty case); T012 L047 has no usable model mask in the gate
(its case seeds a correction).

**Files:**
- Create: `docs/examples/README.md`; the three photos; `docs/examples/mask/*.png` (3);
  `docs/examples/skeletons/*_fake.png` (3); `docs/examples/skeletons/root_lengths.csv`;
  `docs/examples/mask/root_areas.csv`; `docs/examples/results/length_and_area.svg`;
  `scripts/make_examples.py`; `tests/real_data.py`; `tests/test_examples.py`;
  `tests/test_correct_goldens_real.py`; `tests/data/correct_goldens_real.json`.
- Modify: `tests/test_correct_goldens.py` (`files()` takes a stem), `tests/test_enhance_core.py:61`,
  `tests/test_foundation_core_naming.py:19`, `tests/test_measure_core.py:175`,
  `tests/test_skeleton_model_topology.py:354` (use `tests/real_data.py`),
  `README.md` (licence section, one sentence), `CHANGELOG.md:572` (`four more cases from the real dataset` becomes
  `three more cases from the real dataset`), the unified-editor spec `:580` (same wording),
  `CLAUDE.md` (the golden sentence).

**Interfaces.**
- `tests/real_data.py`: `real_data_dir() -> str | None` returns `$SPROUTS_REAL_DATA` if it
  names a folder, else the owner's default `D:/2024 EF image data` if that exists, else
  `None`. The default is the only drive-letter literal in the tree (documented in the
  docstring); the four tests above `pytest.skip` on `None`.
- `scripts/make_examples.py` (stdlib + app; no Qt, no torch): `copy_inputs(source: str, dest: str) -> list[str]`
  (reads the nine files by direct path from `source`, never lists it; refuses if a file is
  missing), `measure(dest: str) -> None` (runs the Measure core on the example folder, the
  same entry `tests/test_measure_core.py` uses at dispatch, writing the two CSVs; delete the
  `*.provenance.json` sidecars: they hold clock time and host details), `build_svg(length_rows: list[dict], area_rows: list[dict]) -> str`
  (deterministic: no clock, no random, fixed 720 x 360 canvas, two horizontal bar charts,
  one bar per image, a `<title>` per bar with the CSV value, colours `tokens.MASK`
  and `tokens.SKELETON` from `app.design.tokens`, generic sans-serif), `main(argv) -> int`
  (`--source DIR` copies the inputs first; without it only measures and redraws).
- Golden file shape: the same as `tests/data/correct_goldens.json` (`cases` with `mask`,
  `mask_clear`, `skeleton`, `skeleton_clear`), cases `real_mask_and_skeleton` (L044),
  `real_empty_generated` (L050), `real_existing_correction` (T012 L047: photo and generated
  skeleton, no mask; the test seeds a correction by erasing the left third of the generated
  skeleton, as `check_goldens_unified.py` does). The ops come from `correct_goldens.json["ops"]`.
  Records hold pixel keys only (no `area_mm2_as_measured`, no `length_mm_if_measured_informational`,
  no file hashes), so a calibration change cannot break the gate.

**Text, exact.** `docs/examples/README.md`:
```
# Example project

Three 640 x 480 minirhizotron photos of 2024-07-18 with the model outputs SPROUTS made
for them, the Measure results and one graph. Open this folder in SPROUTS (Library, then
Open folder) to try Correct, Measure and Visualize on real data.

| File | What it is |
|---|---|
| `24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg` | tube 1, position 44: roots in the mask and the skeleton |
| `24EFCAM1_T001_L050_2024.07.18_111522_001_SMM.jpg` | tube 1, position 50: the skeleton model found no root (empty skeleton) |
| `24EFCAM1_T012_L047_2024.07.18_101909_001_SMM.jpg` | tube 12, position 47 |
| `mask/<stem>.png` | root mask from the mask model (8-bit grey, 640 x 480) |
| `skeletons/<stem>_fake.png` | skeleton from the skeleton model (341 x 256) |
| `skeletons/root_lengths.csv`, `mask/root_areas.csv` | Measure results (columns: `docs/DATA_DICTIONARY.md`) |
| `results/length_and_area.svg` | root length and root area of the three images |

Origin: the Leakey lab 2024 field campaign (camera `24EFCAM1`); masks and skeletons are
the outputs of the checkpoints in `docs/MODELS.md`, copied unchanged from the full project.
Licence: CC-BY-4.0 for the photos, masks, skeletons, CSVs and graph; cite SPROUTS (see the
README). These three images are the only ones of the dataset that are published.
Regenerate the CSVs and the graph with `./venv/Scripts/python.exe scripts/make_examples.py`.
```
README licence section gains: `The three example images in `docs/examples/` are licensed CC-BY-4.0 (see their README).`
(Owner question 1 below: confirm CC-BY-4.0 for the photos.)

- [ ] **Step 1: write the failing tests.**
  `tests/test_examples.py`:
  - `test_every_example_photo_has_its_model_mask_and_skeleton`: three `*.jpg` in
    `docs/examples`; for each stem a `mask/<stem>.png` (mode `L`, 640 x 480) and a
    `skeletons/<stem>_fake.png` (341 x 256) exist.
  - `test_the_examples_stay_small_and_free_of_local_paths`: total size of `docs/examples`
    under 200 KB; no text file contains `C:\Users`, `D:\` or `/home/`.
  - `test_measure_reproduces_the_committed_csvs`: copy `docs/examples` to `tmp_path`, call
    `make_examples.measure(str(tmp_path))`, both CSVs byte-equal the committed ones. Failure
    message: `regenerate with scripts/make_examples.py`.
  - `test_the_graph_is_the_one_the_csvs_draw`: `build_svg(rows_of(length csv), rows_of(area csv))`
    equals the committed SVG text; the SVG contains each value as printed in the CSV.
  `tests/test_correct_goldens_real.py`: reuse `apply`, `replay`, `assert_mask_file`,
  `assert_skeleton_file`, `skeleton_start` and the `make_editor` fixture of
  `tests/test_correct_goldens.py` (import them; do not copy). Per case: copy the needed
  example files to `tmp_path`; seed T012's correction through the layout function WG-T1
  added to `app/project_layout.py` (read its name at dispatch; never a hard-coded
  `gt/` or `skeleton_corrections/` string); open the editor; replay the four scripts exactly
  as `test_correct_goldens.py` does, each op checked against `correct_goldens_real.json`,
  the saved file located by the same `files(folder, stem)` helper. Recording:
  `SPROUTS_RECORD_REAL_GOLDENS=1` makes the test write the JSON and then `pytest.fail("recorded; review the diff")`.
  In `tests/test_correct_goldens.py` change `files(folder)` to `files(folder, stem=SYNTH_STEM)`
  (existing callers keep working; whatever paths WG-T2 / WG-T3 made it return stay as they
  are, only the stem becomes a parameter).
- [ ] **Step 2: run** `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_examples.py tests/test_correct_goldens_real.py -v`. Expected: all fail (no examples, no script, no golden file).
- [ ] **Step 3: create the examples.** `./venv/Scripts/python.exe scripts/make_examples.py --source "D:\2024 EF image data"`
  (run from the repository root; the script reads nine named files only). Check the
  checksums printed against the table in this task, then open the SVG once in a browser.
- [ ] **Step 4: record the goldens** once: `$env:SPROUTS_RECORD_REAL_GOLDENS='1'` and run
  `tests/test_correct_goldens_real.py`; expect the "recorded" failure, then unset the variable.
  **Cross-check:** the `mask` and `mask_clear` entries of the three cases must equal the
  entries of `.claude/handoff/unified-editor-2026-09-29/scratch/contracts-tests/goldens/goldens.json`
  (compare `pixels_sha256`, `on_count`, `shape`, `colour_type`); the audit found the mask
  entries unchanged since 2026-09-29, so a difference means Wave G changed saved mask
  pixels: stop and report. The `skeleton` entries are new (the handoff ones predate Q1).
- [ ] **Step 5: run the command of Step 2 again.** Expected: all pass, none skipped.
- [ ] **Step 6: replace the hard-coded folder** in the four tests by `tests/real_data.py`;
  run those four files; expected: same results as before on this machine.
- [ ] **Step 7: edit** `docs/examples/README.md` (exact text above), the `README.md` licence
  sentence, `CHANGELOG.md:572`, the spec `:580`, and the `CLAUDE.md` sentence about goldens
  (add: `real-image cases: tests/test_correct_goldens_real.py over docs/examples`).
- [ ] **Step 8: ruff** on `scripts/make_examples.py tests/real_data.py tests/test_examples.py tests/test_correct_goldens_real.py tests/test_correct_goldens.py` and the four edited tests.
- [ ] **Step 9: CHANGELOG** `[Unreleased]` `### Added`: "`docs/examples/`: three real photos
  with their mask, skeleton, Measure CSVs and a graph; `tests/test_correct_goldens_real.py`
  replays the Correct goldens on them in CI."
- [ ] **Step 10: commit (lead):** `feat(examples): three real example images with outputs; real-image golden gate in the suite`

## Task W5-T5: Compat shims and the stamp helpers (F5, X8)

**Model:** implementer sonnet / high, reviewer sonnet / medium. **Lane:** L-correct, after
WG-T5 and W1-T4 (Wave G deletes `app/gui/raster_editor_base.py` and
`app/gui/gt_tracing_interface.py`, and W1-T4 adds stamp users).

**The file list is re-checked at dispatch.** Today the private copies are:
`app/gui/correct/session.py:62 _file_stamp`, `correct/mask_document.py:309 _stamp`
(regular files only), `correct/skeleton_document.py:352 _stamp` and `:361 _same_file`,
`correct/read_ahead.py:53 file_stamp`, `app/gui/display_read_ahead.py:51 file_stamp`,
`correct/editor.py:56 _same_file`, `correct/paging.py:59 _same_file`, and (to be deleted by
Wave G) `raster_editor_base.py:76,85`. Run
`git grep -nE "def _?(file_)?stamp\b|def _?same_file\b" -- app` and replace every hit that
is not in `app/io_utils.py`. Callers keep their own names for `file_stamps` methods (plural).

**Files:** modify `app/io_utils.py`, every hit of the grep above, the import sites named
below, `tests/test_layering_contract.py`, `tests/test_import_smoke.py`,
`tests/test_measure_handler.py`, `app/gui/widgets/tokens.py` (docstring), `app/gui/shell_chrome.py`
(only if `build_ribbon` still exists), `app/gui/ui_panels.py:199,274` (two comments),
`CHANGELOG.md`. Delete: `app/handlers/worker_base.py`, `app/gui/feedback.py`,
`app/handlers/window_feedback.py`. Create `tests/test_io_utils_stamps.py`.

**Interfaces.** Produces in `app/io_utils.py` (added to `__all__`):
`file_stamp(path: str) -> tuple` returns `(path, st_mtime_ns, st_size)` of a regular file and
`(path, None)` for anything else (missing, a folder, unreadable);
`same_file(a: str, b: str) -> bool` is True when both are non-empty and
`os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))`.

- [ ] **Step 1: write the failing tests.**
  `tests/test_io_utils_stamps.py`: `file_stamp` of a file equals `(path, st_mtime_ns, st_size)`;
  a missing path and a folder give `(path, None)`; rewriting the file with new bytes and
  `os.utime` changes the stamp; `same_file("", "")` and `same_file("a.png", "")` are False;
  `same_file(str(p), str(p.parent / ".." / p.parent.name / p.name))` is True.
  `tests/test_layering_contract.py` (add):
  - `test_no_compat_reexport_module_is_left`: none of `app/handlers/worker_base.py`,
    `app/gui/feedback.py`, `app/handlers/window_feedback.py` exists.
  - `test_nothing_imports_the_removed_modules`: an `ast` walk of `app/`, `main.py`, `tests/`
    finds no import of `app.handlers.worker_base`, `app.gui.feedback`,
    `app.handlers.window_feedback`.
  - `test_nothing_under_handlers_or_inference_imports_gui_feedback`: no module under
    `app/handlers/` or `app/inference/` imports `app.gui.feedback` (it is gone, so this is
    the guard against it coming back).
  - `test_stamp_helpers_are_defined_once`: an `ast` walk of `app/` finds a top-level
    function named `_stamp`, `_file_stamp`, `file_stamp`, `_same_file` or `same_file` only in
    `app/io_utils.py`.
- [ ] **Step 2: run** `./venv/Scripts/python.exe -m pytest tests/test_io_utils_stamps.py tests/test_layering_contract.py -v`. Expected: the new tests fail.
- [ ] **Step 3: add the two helpers** to `app/io_utils.py` and replace the private copies;
  the stricter regular-file rule of `mask_document._stamp` is now everyone's (a folder named
  like a file reads as unreadable in every caller; the callers' own tests cover this).
- [ ] **Step 4: retire the shims.** Point the app import sites at the real homes:
  `app.workers` for `GuardedThread`, `JobCancelled`, `describe_error`;
  `app.feedback` for `UiFeedback`, `NullFeedback`, `RecordingFeedback`, `NoticeKind`,
  `progress_percent`. Known sites today: `main.py:68`, `app/gui/error_hooks.py:45`,
  `app/gui/task_progress.py:34`, `app/gui/ui_feedback.py:22`, `app/handlers/deps.py:14`,
  `generate_skeleton_handler.py:23,31`, `generation_base.py:23,27`,
  `mask_generation_handler.py:30,38`, `skeleton_handler.py:20` and their tests (grep for
  the three module names). Keep `LOGGER_NAME = "app.handlers.worker_base"` in
  `app/workers.py`: tests and existing logs use it. Move the one test of
  `window_feedback` (`tests/test_measure_handler.py:23,179`) to `QtUiFeedback` /
  `RecordingFeedback`. Delete the three modules; remove their lines from
  `tests/test_import_smoke.py` (`:79,108,109`) and update the compat test in
  `tests/test_layering_contract.py:54`.
- [ ] **Step 5: the two small shims.** Delete `shell_chrome.build_ribbon` and its comment
  block if it is still there; fix the two `build_action_bar` comments in `ui_panels.py`;
  reword the `app/gui/widgets/tokens.py` docstring to say it is "the Qt-side name of the
  tokens" (61 importers; it stays).
- [ ] **Step 6: run** the Step 2 command, then every test file that imports a changed
  module: `git grep -l "io_utils\|worker_base\|gui.feedback\|window_feedback" -- tests` and
  run that list offscreen. Expected: all pass. The lead runs the full suite after the commit.
- [ ] **Step 7: ruff** on every changed Python file.
- [ ] **Step 8: CHANGELOG** `[Unreleased]` `### Removed`: the three re-export modules and
  `build_ribbon`; `### Changed`: one `file_stamp` / `same_file` in `app/io_utils.py`.
- [ ] **Step 9: commit (lead):** `refactor: one file_stamp and same_file; retire the compat re-exports`

## Task W5-T6: CI branches, test log folder, README `fault.log`, start-up Qt warnings (X5, X6, X7)

**Model:** implementer sonnet / medium, reviewer sonnet / low. **Lane:** L-repo, after WX-T5.

**Files:** modify `.github/workflows/ci.yml:3-6`, `tests/conftest.py`, `README.md:96`,
`CLAUDE.md:49-54`, the files the diagnosis names (today `app/gui/shell_chrome.py:399`,
`app/gui/editor_chrome.py:157`, `app/gui/welcome_screen.py:508`; `class_panel.py:291` goes with
Wave G), `CHANGELOG.md`. Create `tests/test_ci_workflow.py`, `tests/test_test_log_dir.py`,
`tests/test_startup_qt_warnings.py`.

**Text, exact.** `ci.yml` push trigger:
`branches: [main, v3, "refactor/**", "feat/**", "fix/**"]` (pull requests unchanged; the
OS matrix is WX-T5's: do not touch it). `README.md:96`: replace
`A native crash writes the stacks of every thread to `fault.log` next to it;` with
`A native crash writes the stacks of every thread to `fault.log` next to it (the file also records handled Windows exceptions such as `0x8001010d`, so an entry alone is not a sign of a crash);`.
`CLAUDE.md:49-54` (the CI bullet and its gotchas): rewrite from the state at dispatch: CI
runs on pushes to `main`, `v3`, `refactor/**`, `feat/**`, `fix/**` and on pull requests, on
the OS matrix `ci.yml` has then; the gotchas keep `requirements.txt` UTF-8 and drop the
pywin32 sentence if WX-T1 removed that dependency (check `requirements.txt`).

- [ ] **Step 1: write the failing tests.**
  - `tests/test_ci_workflow.py::test_ci_runs_on_feature_and_fix_branches`: load
    `.github/workflows/ci.yml` with `yaml.safe_load` (`pytest.importorskip("yaml")`; key
    `on` loads as `True` in PyYAML, so read `doc.get("on", doc.get(True))`); assert
    `{"feat/**", "fix/**"} <= set(on["push"]["branches"])` and `"pull_request" in on`.
  - `tests/test_test_log_dir.py::test_the_suite_logs_into_a_temp_folder`:
    `os.environ["SPROUTS_LOG_DIR"]` is set, `logging_config.default_log_dir()` equals it, it
    lies under `tempfile.gettempdir()`, and differs from `app.user_paths.user_log_dir()` when
    that function is called without the variable (monkeypatch it away for that call).
  - `tests/test_startup_qt_warnings.py::test_building_the_main_window_logs_no_tab_order_or_font_warning`:
    `qInstallMessageHandler` collecting messages; build a window with the `make_window`
    fixture of `tests/test_integration_main_window_wiring.py` (import the fixtures `app`,
    `settings`, `make_window` and the autouse `no_delayed_temp_sweep` from it), show, process
    events; restore the previous handler; assert no message contains `setTabOrder` or
    `Point size <= 0`. Today it fails with 8 `QWidget::setTabOrder: 'first' and 'second'
    must be in the same window` messages offscreen (checked 2026-10-25); the 6
    `QFont::setPointSize` messages appear only on the real Windows platform.
- [ ] **Step 2: run** `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_ci_workflow.py tests/test_test_log_dir.py tests/test_startup_qt_warnings.py -v`. Expected: three failures.
- [ ] **Step 3: CI and conftest.** Edit `ci.yml`. In `tests/conftest.py` before any `app`
  import: `os.environ.setdefault("SPROUTS_LOG_DIR", tempfile.mkdtemp(prefix="sprouts-test-logs-"))`
  and remove the folder in a `pytest_sessionfinish` hook (`shutil.rmtree(..., ignore_errors=True)`).
- [ ] **Step 4: diagnose the tab-order warnings.** In the scratchpad write a probe that
  wraps `QWidget.setTabOrder` to print `traceback.format_stack()` when
  `first.window() is not second.window()`, builds the window as the test does, and lists the
  call sites. The cause is a call made before the widgets share a window (a page not yet
  parented). Fix at each site by making the call after the parenting (or by chaining only
  widgets that already share a window), keeping the Tab order the tests pin
  (`tests/test_correct_tab_order*.py` and the Trace GT / shell equivalents: run
  `git grep -l "tab_order\|Tab order" -- tests` and run those files).
- [ ] **Step 5: the font warnings.** On the real platform (not offscreen), start the app
  from a scratch config folder with the probe handler printing the Python stack when a
  message contains `setPointSize`: `$env:SPROUTS_CONFIG_DIR = <scratch>; ./venv/Scripts/python.exe <probe>`.
  If the stack names an app call (`setPointSize(font.pointSize())` on a font set in pixels),
  fix it by skipping a size of -1. If the stack ends inside Qt (style sheet processing),
  do not change the code: report it to the lead, who records it in `docs/KNOWN_ISSUES.md`
  as benign. The lead decides; this is the only step that cannot be tested offscreen.
- [ ] **Step 6: edit** `README.md:96` and `CLAUDE.md:49-54` with the text above.
- [ ] **Step 7: run** the Step 2 command and `tests/test_foundation_runtime_startup.py`
  (it spawns processes that inherit the log variable). Expected: all pass.
- [ ] **Step 8: ruff** on the changed Python files; CHANGELOG `[Unreleased]` `### Fixed`:
  "CI also runs on `feat/**` and `fix/**`; the test suite no longer writes into the user's
  application log; the 8 start-up `setTabOrder` warnings are gone."
- [ ] **Step 9: commit (lead):** `fix(ci,tests): CI on feat and fix branches; tests log to a temp folder; no start-up tab-order warnings`

## Task W5-T7: Remaining documents (F6, F7, O36)

**Model:** implementer sonnet / medium, reviewer sonnet / low. **Lane:** L-repo, after WG-T10
(Wave G rewrites the Correct composition and the file tree this task keeps).

**Files:**
- Delete: `docs/Architecture_2 useful.md`, `docs/Architecture_2 useful.pdf`,
  `docs/BUILD_EXECUTABLE.md` (the lead runs `git rm` on them).
- Modify: `docs/Architecture_2.md`, `CLAUDE.md` (Docs list `:94-95`; "Commands" gets the
  F6 note), `README.md` (only if a link to a deleted file exists; there is none today).
- Create: `tests/test_doc_paths.py`.

**Facts checked today.** The README build section (`:303-314`) is correct and W5-T1 / W5-T2
add the distribution and weights sentences there, so `BUILD_EXECUTABLE.md` holds nothing
unique: its remaining content is wrong (wrong spec path, `cu118`, `RootMaskAndSkeletons`,
`CUDA_DISTRIBUTION_NOTES.md` that does not exist, `pyinstaller --onefile build_executable.spec`
which PyInstaller rejects). The only references to the deleted files are `CLAUDE.md:94-95`.

**Architecture_2.md.** 814 lines. Keep the title and the summary with Key Capabilities
(`:1-15`, re-read against the code), "The Correct editor (composition)" (`:203-271`, rewrite
against `app/gui/correct/` as Wave G leaves it: three layers), and "File Structure &
Artifacts" (`:573-648`; regenerate the tree from `git ls-files app`, and fix the artifacts
table: `skeletons/root_lengths.csv`, `mask/root_areas.csv`, no `index.html`, dashboards at
`127.0.0.1:8050` and `:8051`, `app/config.py`). Delete everything else (the overview
diagrams `:16-202`, workflows and data flow `:272-572`, requirements, performance, API
reference, security and roadmap `:649-814`; they describe a subprocess architecture, HTML
reports, Python 3.8 and invented contacts). Add one section "Data flow" of at most 25
lines, true to the code: `app/handlers/*` start `GuardedThread` jobs on the GUI process;
`app/inference/batch_engine.py` runs batches; `mask_inference.py` and
`skeleton_inference.py` write `mask/` and `skeletons/`; `metrics.py` + `measure_run.py`
write the CSVs; `app/visualization/` reads them; the README "Pipeline" diagram is the
picture. End with: `For the module map see CLAUDE.md; for formats see docs/DATA_DICTIONARY.md.`

**CLAUDE.md edits, exact.** Docs list: delete the `docs/BUILD_EXECUTABLE.md` line; change
the Architecture line to ``- `docs/Architecture_2.md` — the Correct editor's composition, the file structure and a short data-flow section.``;
Commands, after the lint block, add:
```
- Scripts that build a window or an editor page must delete it before the `QApplication`
  goes, or the process can end in an access violation (about 1 run in 30):
  `from tests.qt_teardown import delete_widgets`, then `delete_widgets([page])` as the last
  step. `main.main` and the test suite already do this.
```

- [ ] **Step 1: write the failing tests** in `tests/test_doc_paths.py` (a module-level
  helper `unresolved(text: str, tracked: set[str]) -> list[str]` and two tests):
  - `test_a_missing_path_is_reported`: `unresolved("see `docs/NOPE.md` and `app/nope.py`", tracked)` returns both; `unresolved("see `README.md` and `app/gui/panels.Panel`", tracked)` returns `[]`.
  - `test_docs_name_only_existing_repository_paths`: for `README.md`, `CLAUDE.md` and every
    `docs/*.md` except `docs/KNOWN_ISSUES.md` (a record that names deleted files by
    design), `unresolved(...)` is empty; the failure message lists `doc:line: path`.
  Rules of `unresolved` (the prototype passes at HEAD except the two cases below): take
  each back-ticked token; cut at the first space or `::`; skip tokens with `<`, `*`, `{`;
  normalise `\` to `/`; skip a first segment in `{checkpoints, dist, build, venv, graphify-out, .claude}`
  (untracked or local) and tokens equal to `MEMORY.md` or `.md`; a token with `/` whose
  first segment is a tracked top-level entry must be a tracked file or folder, or a module
  path (`a/b/c.Name` resolves to `a/b/c.py`); a bare name ending in `.py .md .qss .spec .ps1
  .bat .toml .cff` must exist somewhere in the tracked tree. `tracked` comes from
  `git ls-files` (skip the test when git is missing, as `tests/test_fair_build.py` does).
- [ ] **Step 2: run** `./venv/Scripts/python.exe -m pytest tests/test_doc_paths.py -v`.
  Expected: the first test passes (it proves the helper flags a missing path); the second
  fails on `docs/BUILD_EXECUTABLE.md:76: CUDA_DISTRIBUTION_NOTES.md` and on nothing else
  (a prototype of the rules above found exactly that one at HEAD).
- [ ] **Step 3: rewrite `docs/Architecture_2.md`** as described; edit `CLAUDE.md`.
- [ ] **Step 4: the lead deletes** the three files with `git rm`.
- [ ] **Step 5: run** the Step 2 command. Expected: both pass. If the second reports a real
  stale path in a kept doc, fix the doc; add a token to the skip list only for a genuine
  runtime or local path, with a comment saying why.
- [ ] **Step 6: ruff** on `tests/test_doc_paths.py`; CHANGELOG `[Unreleased]` `### Removed`:
  "`docs/Architecture_2 useful.*` and `docs/BUILD_EXECUTABLE.md` (the README build section is
  the one text); `docs/Architecture_2.md` trimmed to what is true."
- [ ] **Step 7: commit (lead):** `docs: remove the stale architecture copy and build guide; trim Architecture_2; doc-path test`

---

# Wave 6

## Task W6-T4: README line for the runtime venv (O31)

**Model:** implementer sonnet / low, no reviewer. **Lane:** L-repo, last.
**Files:** modify `README.md` (Installation section, after the `pip install -r requirements.txt`
block, near `README.md:50`).

- [ ] **Step 1: add this sentence** to the Installation section:
  `The runtime environment needs neither `pipreqs` nor `ipython`; if an older environment has them, remove them (`pip uninstall -y pipreqs ipython`): IPython adds about 250 ms to the first dashboard import.`
- [ ] **Step 2: run** `./venv/Scripts/python.exe -m pytest tests/test_doc_paths.py -v` (once W5-T7 exists). Expected: pass.
- [ ] **Step 3: commit (lead):** `docs(readme): the runtime venv needs neither pipreqs nor ipython`.
- [ ] **Owner action, not an agent's:** run
  `./venv/Scripts/python.exe -m pip uninstall -y pipreqs ipython` in the dev venv (saves
  about 228 ms on the first dashboard import).

---

## Corrections to the master plan

1. W5-T1's After column needs W1-T7 (it bundles `THIRD_PARTY_NOTICES.md`).
2. The venv has setuptools 65.5.0 and packaging 24.1: `license = "<SPDX>"` makes
   `tests/test_fair_packaging.py` error there, and `packaging.licenses` does not exist.
   W5-T1 skips that fixture below setuptools 77, adds `setuptools>=77` to
   `requirements-dev.txt` (a file the master gives to L-xplat, which is done by then), and
   tests the SPDX id by equality.
3. W5-T4 should run after W3-T8: the example CSVs and graph change with the calibration and
   the estimator, and `tests/test_examples.py` says so.
4. W5-T4 needs no GPU run: the nine files are the 46k project's own outputs.
5. `CLAUDE.md:51-54` (CI bullet) is owned by W5-T6, `CLAUDE.md:55` by W5-T2, `CLAUDE.md:71`
   by WG-T7; W0-T2 skips them. `docs/SPROUTS_DASH_THEME.md` has no owner in the master:
   give the four corrections of `repo-docs.md` F7 to W4-T6.
6. The master's W0-T1 closures omit E5 and X4 (section 1.1 retires them); table 3 closes
   them, pending the lead's confirmation.
7. The old plan's "package data for the wheel" for `CITATION.cff` is not done (a wheel
   cannot carry a root file); see open question 3.

## Lead steps (collected)

1. W5-T2: `git rm --cached checkpoints.zip` before that commit.
2. W5-T7: `git rm "docs/Architecture_2 useful.md" "docs/Architecture_2 useful.pdf" docs/BUILD_EXECUTABLE.md`.
3. Optional, with approval: `./venv/Scripts/python.exe -m pip install "setuptools>=77"` so
   `tests/test_fair_packaging.py` runs locally after W5-T1.
4. Hand `CLAUDE.md:71` to WG-T7, the four `docs/SPROUTS_DASH_THEME.md` corrections to
   W4-T6, `README.md` calibration paragraph to W3-T2.
5. At each wave end set `$env:SPROUTS_REAL_DATA = 'D:\2024 EF image data'` (or rely on the
   default of `tests/real_data.py`) so the real-sample byte-identity tests run.

## Open owner questions

1. Licence of the example photos in `docs/examples/`: the plan says CC-BY-4.0 (the weights'
   licence). The 2026-10-12 sheet said "no sample data"; the chat answer allows these three.
2. Whether the lead accepts closing E5 and X4 (table 3 of W0-T1): both follow from master
   section 1.1, not from the old plan's section 4.
3. Whether wheel installs should also carry `CITATION.cff` (needs a generated
   `app/_citation.py`); the plan ships it in the bundle only.

## Self-review

- **Spec coverage:** W0-T1, W0-T2, W5-T1 to W5-T7 and W6-T4 each have a section; every
  master section 1 row for O5, O25, O30, O31, O32 to O36 maps to a task; the master's
  section 6 code-comment rows are listed as skipped with their owners.
- **Placeholders:** none; the `LICENSE` header, tables, README and MODELS text, the
  checker and the golden-gate shape are given. Items left to the implementer are marked by
  a command that finds them at dispatch (W5-T5 file list, W5-T6 diagnosis, W5-T4 layout
  function name), because Wave G and WX change those files first.
- **Type consistency:** `LICENSE_SPDX`, `COPYRIGHT_HOLDER`, `ModelSpec.license`,
  `fetch` / `main`, `file_stamp` / `same_file`, `real_data_dir`, `copy_inputs` / `measure` /
  `build_svg` are named once and used the same way in tests and consumers.
- **Review Focus:** items 1 to 6 each have a test in W5-T1, W5-T3, W5-T2, W5-T4, W5-T7, W0-T1.
- **Proportion:** the plan is long because the documents are exact copy; code bodies appear
  only for the KNOWN_ISSUES checker and two tests' shape.
