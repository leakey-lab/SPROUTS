# SPROUTS — known issues and open decisions

Status of 2026-10-25 at HEAD `607a7f9` on `feat/unified-correct-editor`. Every owner
question of the 2026-10-12 audit is answered (table 1); each answer names the task that
builds it in `docs/superpowers/plans/2026-10-25-owner-decisions/00-master.md`. Table 2 lists
every engineering item with its ID, the old names it was known by, and the task. Table 3
lists what was closed as accepted. The text below the tables is the record: the entries keep
their original wording and their "Open" / "Resolved" labels as of the pass that wrote them,
so read the tables for the present state. A task that lands moves its rows to the history
with the commit hash. Until then a row that is built names the commit in its last cell
("done"); every other row is decided and not built. Later commits are recorded here at the
end of each wave.

## Decisions: asked on 2026-10-12, answered 2026-10-12 and 2026-10-25

| O | Decision | Built by |
|---|---|---|
| O1 | Measure reads GT first for both length and area; last column `Source` (`gt` or `prediction`) | WG-T8 |
| O2 | An unedited Save is not acceptance; a layer is written only when it differs from what it would otherwise read | W1-T3, WG-T2, WG-T3 |
| O3 | Same camera model (Bartz BTC-100X); 18 x 13.5 mm, confirmed by Rajurkar et al. 2022 (Plant Methods 18:39); one calibration per project file | W3-T2 |
| O4 | Fixed-shape batches; stop on out-of-memory; default on after a GPU session | W2-T3 (done, 41f371e: batch shapes and out-of-memory events in the run record), W3-T3 |
| O5 | Deterministic cuDNN stays the default | W0-T2 (docs only; done, 705377d..dc67e12) |
| O6 | PNG and JPEG only, any case | W1-T5 |
| O7 | Stems compare without case on every platform | W1-T5 |
| O8 | Generate keeps the model size; nearest on display | W3-T4 |
| O9 | A cleared mask stays as an empty GT mask; Generate never touches GT | WG-T2, WG-T7 |
| O10 | One mask threshold, 127 | W2-T6 |
| O11 | A click with the 1 px brush paints one pixel | W3-T5 |
| O12 | Keep the 8-connected arc length; pixels are square; fix the 4.4 % bias with the Vossepoel-Smeulders corner-count estimator (research memo) | W3-T8 |
| O13 | Old 341 x 256 corrections are never measured and are flagged | WG-T3, WG-T8 |
| O14 | Say that the mask gives area, the skeleton gives length, independently | W2-T8a, WG-T10 |
| O15 | Refuse a folder change while a job runs | W1-T1 |
| O16 | A run that writes nothing keeps the record; keep a run history | W2-T1 (done, 51b8136) |
| O17 | Buttons "Measure Length" / "Measure Area"; no incremental Measure | W2-T8a |
| O18 | One Clear, in the dock; Save accent only while something is unsaved | W4-T1 |
| O19 | Discard reloads a changed file; Save asks first | W1-T4, WG-T4 |
| O20 | Toast when a smoothed, uncommitted pick is dropped | W4-T2 |
| O21 | Linux and macOS; GPU optional on macOS; no Windows-only packages | WX-T1 (done, 6bd8a31: start-up flags), WX-T8 (done, 607a7f9: macOS raster default), WX-T2 to WX-T5 |
| O22 | "Smooth JPEG blocks" switch only; no display upscaler meets the bar | W4-T5 (done, 3ceea64) |
| O23 | One unified editor: Trace GT becomes a layer of Correct | WG-T4, WG-T5 |
| O24 | Restore retired classes; Remove GT; GT in the Library; per-class pixel counts; no COCO | WG-T6, WG-T9 |
| O25 | Accept the compact top bar | closed (table 3) |
| O26 | "Prediction" for model output, "GT" for a human-saved result; a third Library mark | WG-T6, WG-T10 |
| O27 | One naming scheme for dashboard views; one blue date ramp | W4-T6 (done, 1c9f043) |
| O28 | Pre-Generate scan as a warm-up only together with other Generate work | W6-T3 (optional) |
| O29 | Canvas indicator: warn toast and About row | W2-T9 (done, 28b71f3) |
| O30 | Do not build the editor pages after the first paint | closed (table 3) |
| O31 | Uninstall `pipreqs` and `ipython` from the runtime venv (the owner runs it) | W6-T4 |
| O32 | GPL-3.0-or-later code, CC-BY-4.0 weights; copyright "the SPROUTS authors" | W5-T1 |
| O33 | Only the weights go to Zenodo; `checkpoints.zip` is untracked | W5-T2 |
| O34 | Cite as "SPROUTS"; authors Kinjalk Parth, Sebastian Varela, Andrew D. B. Leakey; the tag is the owner's | W5-T3 |
| O35 | A few example images with mask, skeleton and a result graph: the audit's three example images | W5-T4 |
| O36 | Delete `docs/Architecture_2 useful.*` and `docs/BUILD_EXECUTABLE.md` | W5-T7 |
| O37 | Masks, skeletons and GT files whose image is gone are skipped and recorded, never deleted | W2-T11 |
| O38 | Only the file name in `Error` cells | W3-T7 |
| Chat | GT design = "GT folder": Trace GT becomes a third layer of Correct; human-saved edits go to `gt/`, predictions are never overwritten, Measure reads GT first with a `Source` column; existing hand-corrected files are copied into `gt/` once (originals kept) | Wave G (WG-T*) |

## Open: engineering

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
| R1 | batch-size item of section 7 | Batch size changes GPU outputs | O4, W2-T3 (done, 41f371e: step 1, batch shapes in the run record), W3-T3 (open) |
| R3 | supported formats | Formats differ per reader | O6, W1-T5 |
| R4 | case-variant stems | Case-variant stems | O7, W1-T5 |
| R5 | "Changed since measured" | Compares file times | W2-T2 |
| R6 | | A run that writes nothing replaces `_sprouts_run.json` | O16, W2-T1 (done, 51b8136) |
| R7 | | A job ends after another folder was opened | O15, W1-T1 |
| R8 | W12 | Measure buttons are nouns | O17, W2-T8a (incremental Measure closed, table 3) |
| E1 | W16 | Save placement and states | O18, W4-T1 |
| E2 | | Discard / Save after an external change | O19, W1-T4, WG-T4 |
| E3 | J1 | Smooth on a picked branch | O20, W4-T2 |
| E4 | | A click with brush size 1 paints nothing | O11, W3-T5 |
| E6 | | Pinch zoom (bicubic refine closed, table 3) | O21, WX-T3 |
| E7 | Q2 | JPEG deblock | O22, W4-T5 (done, 3ceea64) |
| G1 | | Two tracing interfaces | O23, WG-T4, WG-T5 |
| G2 | | Trace GT v1 limits; retired classes cannot return | O24, WG-T6, WG-T9 |
| G4 | | Naming: trace, correct, GT, mask | O26, WG-T6, WG-T10 |
| P2 | | First image read on the GUI thread | not built now (only for images larger than 640 x 480): W6-T1 |
| P3 | | Deflate in the skeleton history | not built now (only for large images): W6-T2 |
| P6 | | Hardware OpenGL check; no indicator | O29, W2-T9 (done, 28b71f3) |
| D2 | | Pre-Generate scan as a warm-up | O28, W6-T3 (optional) |
| D5 | | IPython in the venv | O31, W6-T4 |
| D7 | W19 | Dashboard view names and date colours | O27, W4-T6 (done, 1c9f043) |
| F1 | FR-01 | No licence; Pix2Pix notice missing | O32, W1-T7, W5-T1 |
| F2 | FR-07 | `checkpoints.zip` tracked; no DOI; no mask model | O33, W5-T2 |
| F3 | | Metadata placeholders; citation not shipped | O34, W5-T3 |
| F4 | Q3 | Real images as test data; stale gate | O35, W5-T4 |
| F5 | CQ-10 | Compat shims; stamp helpers | W5-T5 |
| F6 | | Script teardown note | W5-T7 (note moves to `CLAUDE.md`) |
| F7 | | Documents consistent; this file | W0-T1, W0-T2 (done, 705377d..dc67e12), W5-T7, O36 |
| X1 | | The length estimator reads about 4.4 % long | O12, W3-T8 |
| X2 | | Measure measures masks whose image is gone | O37, W2-T11 |
| X3 | | `Error` cells hold absolute paths | O38, W3-T7 |
| X5 | | pytest writes into the user's log | W5-T6 |
| X6 | | `fault.log` wording; start-up Qt warnings | W5-T6 |
| X7 | | CI has not run since 2026-07-25 | W5-T6, WX-T5 |
| X8 | | Stamp helpers defined six times | W5-T5 |
| X9 | | Correct with no image shows a tool hint | W2-T8a |
| XP1 | | Linux and macOS support (new) | O21, WX-T1 (done, 6bd8a31: start-up flags), WX-T8 (done, 607a7f9: macOS raster default), WX-T2 to WX-T5 |

## Closed on 2026-10-25 (accepted, nothing to build)

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
| M3 | No reader change for EXIF | every reader already uses the stored grid (W2-T10 pins the policy with tests; done, d097097) |
| R2 (O5) | Deterministic cuDNN stays the default | already in the code and in every run record |
| R8 (O17, incremental part) | No incremental Measure | a safe reuse has to verify content, which costs as much as measuring (17 to 21 s for 46k) |
| E5 | Exact windowed pick not built | 11 ms per pick on a real 640 x 480 skeleton (master section 1.1) |
| X4 | Generate overwriting a mask saved during a run: dropped | Generate never writes `gt/`; a mask saved during a run is GT (master section 1.1) |

## By design (scope limits)

Added on `feat/multiclass-labels` (spec:
`docs/superpowers/specs/2026-09-22-multiclass-labels-design.md`). Deliberately
not in v1:

- No measurement of GT classes (the stage builds a segmentation dataset only).
- No ML overlay or seeding in the GT editor; GT is independent of the models.
- No per-class visibility toggle (`H` hides the whole tracing), no GT badges in
  the library tree, no deleting a GT file from the editor, no un-retiring a class.
- Export is semantic segmentation only (no instance ids / COCO).
- Only single-folder projects (the library already scans one folder).
- (Resolved 2026-09-28) The topbar used to need about 1170 px; it now switches to a
  compact density (icon-only unselected stages, names in tooltips) and the window
  shrinks to about 1000 px. The compact density changes the "names always visible" rule
  and still needs design sign-off. (Audit 2026-10-12, offscreen with the app theme: all
  six names need 1040 px, the full row comes back at 1064 px and the window shrinks to
  about 700 px, not 1000. The sign-off question is G3 in section 8.)

## Details and history

Introduction of the file until 2026-10-25: Tracked during the `feat/sprouts-redesign` work. These are open items, not regressions introduced by the redesign unless noted. Old section numbers used inside the entries below: sections 1 to 3 are "Early passes", section 4 is "By design (scope limits)", section 5 is the 2026-09-28 optimization pass, section 6 the 2026-09-29 unified Correct editor, section 7 the 2026-09-30 UX and speed pass, section 8 the 2026-10-12 audit.

### Audit of 2026-10-12 (old section 8)

Eight read-only audits, one agent per package, checked every open item above against the
code, reproduced what a script could reproduce, and looked at the questions the owner
asked (why there are two tracing interfaces, what happens to a corrected skeleton, what
else is ambiguous). Nothing was fixed. An entry above carries an "Audit 2026-10-12" note
where the audit corrected it. The resolution plan is
`docs/superpowers/plans/2026-10-12-known-issues-resolution.md`; the full reports and the
scripts are in `.claude/handoff/issue-audit-2026-10-12/` (git-ignored). The IDs below (L,
M, R, E, G, P, D, F, X) are the plan's. "Run" means a script produced the result; "read"
means it comes from reading the code.

Closed by the audit (already resolved, or not an issue):

- Q4, EXIF orientation (section 6): not an issue, the entry was wrong (run).
- Deterministic cuDNN (section 5): resolved in code; the owner's confirmation and a stale
  passage in `docs/MODELS.md` are left (read; the runtime tests pass).
- "Check that a normal launch gets hardware OpenGL" (section 7): checked, hardware in 16 of
  16 logged launches. The missing on-screen indicator is P6.
- The README and `checkpoints.zip` (section 5): the README is right. The DOI and the
  missing mask model stay open (F2).
- The script teardown note (section 6): a note for script authors, not a product issue.
- L4, the name `<stem>_fake.png`: not an issue. It is the Pix2Pix name for a generated
  image, built in five places and present in no CSV, dashboard or UI text. Explain it in
  the docs and keep one constant; a rename would need a reader for both names for good.
- M5, Measure-area on masks saved by the editor: right. The editor writes only 0 and 255
  and Measure counts them exactly (19,200 of 19,200 px). Left for files from other
  programs: Measure thresholds at 127 and Correct at 200, and Measure ignores alpha.

Worse than recorded, each reproduced by a run (noted in place above): R7 (a chained
skeleton pass writes into a project opened during the mask pass), R4 (case-variant stems
replace a hand-corrected mask), E2 (Save overwrites a file changed on disk without
asking), and un-retiring a GT class (section 4: a retired class has no row in the class
editor, its key cannot be added again, and its pixels export as 255; only a hand edit of
`gt/classes.json` brings it back, G2).

New, not recorded before:

- **M1 A model mask on a photo that is not 640 x 480 (run).** Masks are generated at
  640 x 480 whatever the photo. On a photo of another size Correct opens the mask empty
  ("No mask"); `Ctrl+S` with nothing edited then deletes `mask/<stem>.png` without a
  prompt, and one stroke followed by Save or `PgDn` writes a photo-sized mask that holds
  only the stroke (26.96 mm² became 0.04 mm²). A mask that cannot be decoded takes the
  same path, with no notice at open. The Library draws no mask for such an image. All 40
  harness images and 384 sampled source images are 640 x 480, so today's data is not hit.
- **M2 One field of view for every image (run).** Calibration assumes that each image
  covers 18 x 13 mm whatever its size, aspect or crop: a mask covering a quarter of a
  640 x 480 frame, of a 320 x 240 crop and of a 1920 x 1080 frame all measure 58.5 mm²,
  with no warning, and provenance records the field of view but no pixel sizes. The pixel
  is not square (0.028125 x 0.027083 mm); the constant has been in the code since the
  first commit, without a source. With 18 x 13.5 mm the pixel is square, every area grows
  by 3.85 % and lengths by 1.1 to 3.7 %. Which value the cameras have is the owner's to
  confirm.
- **M4 Hand-corrected and model masks are told apart by the PNG colour type only (run).**
  A hand mask flattened to grey counts as a model mask, and a model mask saved again as
  RGBA as hand-corrected. The origin only feeds the wording of the Generate prompt, so it
  never causes a silent overwrite. One real case does: a mask cleared by hand is a deleted
  file, so *Keep existing masks* generates it again.
- **L3 The mask and the skeleton are independent, and Correct does not say so (run).** A
  corrected mask changes area only; the README and the data dictionary say it, the
  Correct page does not, and the sentence written for the Generate page is never shown
  (`shell_chrome.py:365-369` overwrites it). In every 15th image of the 2024 EF project
  the mask model reports a root and the skeleton model none in 32 % of the images, so
  area and length describe different sets of images.
- **L5 Two ways of thinning the same skeleton (run).** Measure thins the generated file
  at 341 x 256; the editor upscales it to the photo first. Neither is closer to the
  truth: on synthetic lines of known length both read about 4.4 % long (0 % at 0° and
  45°, +8.2 % at 22.5°), which is the 8-connected arc-length estimator (X1, a science
  question for the owner: a change moves every length in every CSV).
- **D7 Date colours in the dashboards (run).** Worse than W19 says: colourway slots 1, 5
  and 3 are exactly the mask green, the skeleton orange and the selection purple; slots 4
  and 5 are hard to tell apart; the colours repeat from the eleventh date; and the area
  profile indexes only the plotted tube's dates, so a date changes colour between tubes.
- **F1 The Pix2Pix licence notice is missing (run).** `app/inference/skeleton_inference.py`
  lines 59-252 come from pytorch-CycleGAN-and-pix2pix (added in `d24d0eb`) without the BSD
  notice that licence requires. It can be added before the licence decision.
- **F3 The shipped app shows no citation (read; no bundle was built).** `CITATION.cff` is
  in neither the PyInstaller data files nor the wheel, so the About dialog's "How to cite"
  block is missing outside a checkout.
- **F4 The real-image golden gate is stale (run).** It lives in a git-ignored handoff
  folder; at HEAD it fails with 153 differing lines, all in the skeleton layers, because
  its goldens predate the Q1 change. Real skeleton saves are unchecked.
- **R6, addition (run).** The record a no-op run writes also names the checkpoint
  installed now, which made none of the masks in the folder.
- **X, smaller findings.** Measure-area measures every `*.png` in `mask/`, including a
  mask whose image is gone (read). The `Error` cell of the Measure CSVs holds the absolute
  path of the unreadable file (run). A Generate run can overwrite a mask saved by hand
  while it runs (read, not reproduced). pytest writes into the user's application log
  (`tests/conftest.py` sets no `SPROUTS_LOG_DIR`). CI has not run since 2026-07-25: it
  triggers on `main`, `v3`, `refactor/**` and pull requests only. `fault.log` holds four
  benign `0x8001010d` entries from normal launches, while the README calls it a crash
  log. Each start logs 8 `setTabOrder` and 6 `QFont::setPointSize` warnings.

The two tracing interfaces (G1): Trace GT and Correct already share the canvas, the paint
engine and the chrome (the same strokes give the same 19,899 pixels in both); what is
duplicated is the page logic. They stay two stages on purpose: Trace GT must never show
or write the model's mask. The plan keeps two editors, fixes the wording (G4) and leaves
the code merge as an owner decision.

### UX and speed pass, 2026-09-30 (old section 7)

The pass worked through sections 5 and 6 with the emphasis on UI/UX and speed and hunted
for bugs: per-area packages, a UI walkthrough (findings W1-W19) and a measured runtime
review, then a review of every commit. A fourth wave split `MainWindow`, reviewed the
warm-up and memory work and walked the app again (findings V1-V11). Resolved items are
marked above, and `CHANGELOG.md` lists the changes. Timings are medians of interleaved runs
on a shared machine, offscreen unless stated.

Owner questions (science or product scope; nothing here blocks use):

- **The batch size changes GPU outputs slightly.** With deterministic cuDNN the kernel still
  depends on the batch shape. On 300 images, masks made with batch 16 and batch 8 differ in
  148 files (327 pixels flip between 0 and 255); skeletons made with batch 128 and batch 8
  differ in 1 file (43 values by one grey level), with 128 and 1 in 208 files (by at most 5
  levels). So the halving after an out-of-memory error and the skeleton batch sized from
  free VRAM can change the outputs of the images they touch (`_sprouts_run.json` records
  `batch_size` and `effective_batch_size`). A fixed batch per model, without halving, would
  make runs reproducible across machines but changes outputs: it sits next to the
  deterministic-cuDNN decision in section 5. (Audit 2026-10-12: the 300 images were
  synthetic, and a fixed batch makes runs reproducible per GPU model and software stack,
  not across machines. Two more causes: the last batch of every run is smaller, and a
  *Keep existing masks* run batches only the missing stems; the run record cannot say
  which image ran at which size. On the CPU, 8 real images gave identical masks at batch
  4, 2 and 1. R1 in section 8.)
- **Supported formats.** Skeleton generation also reads `.bmp`, `.tif`, `.tiff` and `.ppm`,
  which the Library, mask generation and Measure-area ignore: such an image gets a skeleton
  and a `root_lengths.csv` row, but no mask and no Library entry. Changing either side
  changes outputs.
- **Case-variant stems on Windows.** `A.png` and `a.jpg` are two Library images but share
  one output file on NTFS (`mask/A.png`), so their saves still race (two files with exactly
  the same stem no longer do). It needs case-insensitive stems in the Library and in
  generation together. Rare. (Audit 2026-10-12, worse than recorded: with `A.png` and
  `a.jpg` the winner is random (10 against 10 in 20 runs), the mask and the skeleton of
  one run can come from different images, and a *Keep existing masks* run regenerates the
  other-case stem every time and replaced a hand-corrected mask without a prompt. R4 in
  section 8.)
- **Save placement in Correct (W16).** Correct's only top-bar action is the destructive
  *Clear*, which the dock also has, while *Save* sits only in the dock; the accent-filled
  *Save* (and Trace GT's *Save GT*) stays highlighted when there is nothing to save. Left as
  a design question.
- **Save with nothing unsaved (V1, spec D7).** `Ctrl+S` or *Save* with nothing unsaved
  saves the active layer (D7, kept: it is how a generated skeleton is accepted or a model
  mask marked hand-checked). On the mask layer that rewrites the mask, and only AREA is
  flagged as outdated. On the skeleton layer it writes
  `skeleton_corrections/<stem>_skeleton.png` for an image nobody edited, and the Library
  then shows the image as corrected (the diamond, "Skeleton corrected", the correction
  drawn). The file is not a copy: T100_L049's generated skeleton (341 x 256, 46.79 mm)
  became a 640 x 480 file of 45.99 mm (-1.7 %), which would change root length if
  corrections were measured (FR-03). Keep it as an acceptance, write nothing and say
  "Nothing to save", apply D7 to the mask only, or write a correction only when it differs
  from the generated skeleton? Best decided together with W16. (Audit 2026-10-12: the
  Library shows the image as corrected only after another image is selected or the
  project is reopened, and the owner's project holds one such unedited correction. On the
  mask layer the same Save removes a mask file that Correct did not load: M1 in
  section 8. L2 in section 8.)
- **Build the editor pages after the first paint?** The hidden Correct and Trace GT pages
  cost about 140 ms of the `MainWindow()` build (214.7 -> 74.1 ms with empty stand-ins,
  n = 12): their constructors take about 50 ms, the rest is reparenting and style work.
  That is about a third of process start to first paint (about 440 ms). (Audit 2026-10-12:
  true offscreen only. On the real platform the pages are about 130 ms of the 0.87 s
  before the window shows, and logged launches took 1 to 5 s to reach the event loop. A
  deferred-build prototype gave the same first paint and then two GUI blocks of 83 ms and
  39 ms: the cost moves, it does not shrink. P1 in section 8.) The split left
  them as they are because building them later is not a clean move:
  `tests/test_shell_chrome_smoke.py::test_main_window_contract` checks the pages on a window
  that is never shown; pages made after the first paint join the end of the Tab chain;
  `correct_editor` and `gt_tracing_interface` would need a build on first access and a
  placeholder at their fixed `Panel` index; and all their wiring (signals, the skeleton
  resolver, read-ahead providers, toast clearance, the heap freeze after start-up) would
  have to wait as well.
- **Discard after an external change.** When another program rewrites `gt/<stem>.png` while
  the image has unsaved edits, *Discard* returns to the copy in memory and the chip says
  Saved although the file on disk differs; the next selection reloads it. The Correct mask
  layer behaves the same way. Reload a changed file on *Discard*, or say that it changed?
  (Audit 2026-10-12, worse than recorded: Save, the prompt's Save and `PgDn` overwrite a
  file that changed on disk without asking, in both editors, and a Generate run is such a
  writer, since it does not refresh the image open in Correct. The skeleton layer already
  re-reads on Discard. E2 in section 8.)
- **Smooth on a picked branch (J1).** Pressing `S` on a picked branch without moving it is
  not an unsaved change, so leaving drops the smoothed preview without a prompt. Counting it
  would prompt after a mere pick whenever Smooth is on; the recommendation is to keep the
  rule.
- **Bar labels above 30 bars.** Dashboard bars carry value labels only when at most 30 share
  the axis, also when zoomed in on a few tubes; the hover still shows the values. The
  recommendation is to keep it.
- **Area time-view legend — RESOLVED (2026-09-30).** The area dashboard's Growth Over Time
  view shows its legend by itself up to 30 tubes, as the length view does, and *Toggle
  Legend* (over the chart's top-left margin, on that view only) shows or hides it in place;
  the choice is kept across views, and the view still has no control strip. With 160 tubes
  the view paints in 501 ms instead of 600 ms when entered and in 399 ms instead of 524 ms
  the next time. The next profile or stacked view pays part of it back inside plotly.js
  (251 -> 272 ms, 304 -> 394 ms); five switches still take 1,699 ms instead of 1,808 ms.
- Wording and naming: the two dashboards name their views differently ("Faceted Depth
  Profile" vs "Area Profile by Position") and their date colours reuse the mask green and
  the skeleton orange (W19); the Measure buttons are nouns ("Root Length", "Root Area") and
  re-measure every input even when nothing changed (W12). (Resolved 2026-10-02: the Generate
  prompt's button reads "Overwrite all" when no mask is hand-corrected.)

Follow-ups:

- (Resolved 2026-09-30) Start-up and project-load warm-ups (the device check and the
  dashboard imports) made every full garbage collection a 60-120 ms GUI pause at
  `49d3f11` and cost 0.4-0.6 GB and 0.4-1.5 s at quit. Warm-ups now follow intent
  (`app/gui/warmup.py`) and the heap is frozen after them (`app/gc_policy.py`). Left as
  they are: quitting while a torch warm-up imports waits for the import (1.1-1.3 s, then a
  clean exit); a dashboard opened with no intent signal (a click within the 200 ms hover
  dwell, or `Alt+6`) first waits for its imports (1.3 s to the first chart), and those
  imports run without the collection hold and stay unfrozen until the next warm-up or job
  end (each later full collection about 30 ms longer). The About dialog now names the
  device before torch loads: "NVIDIA GeForce RTX 5070 Ti (from the NVIDIA driver; PyTorch
  not loaded yet)", asked on a worker thread, 59 ms after the dialog opened; once torch is
  loaded it shows the resolved runtime.
- (Resolved 2026-09-30) A warm-up job ended by `SystemExit` or `KeyboardInterrupt` stayed
  "running" for good, so no later warm-up started and, with freezing on, automatic full
  collections stayed off for the session; it now reports a failure and the queue goes on.
  No production path raises one there. The same review found nothing else in the warm-up
  and memory work: across 5 project loads and about 53 image opens a freeze pinned no
  document, image, array, data frame or dashboard, only the scan thread below, and fast
  random Library / Correct switching (5 x 250 steps) always showed the right photo.
- (Resolved 2026-09-30) A finished folder scan's thread lived in a reference cycle with its
  results until a full collection (46,178 images: 17.2 MB and 46,838 objects, freed by a
  41-51 ms pass), and a heap freeze made while that project was open kept it for the session
  (13.5 MB per freeze). It is now freed as soon as its scan has ended.
- (Resolved 2026-10-02) Same class as the warm-up fix: the Measure CSV parse, the device
  check, the folder scan (which caught only `OSError`) and `GuardedThread` (every batch job)
  now report any `BaseException` as a failure, so the metrics no longer stay loading, the
  chip no longer stays at "Checking GPU…", the loading card goes and a job's progress row
  stops.
- (Resolved 2026-09-30) Photo and raster layer items were freed only by the cyclic garbage
  collector (`TiledLevels` held a bound method of the item); they are now freed as soon as
  their scene lets go of them (browse at 2048 px: peak 1374 -> 657 MB, no GC pause).
- (Resolved 2026-09-30) Read-ahead duplicates and small Correct fixes: Correct keeps the
  image it leaves as a finished read and adopts the Library's photo decodes (a 15-action
  session at 2048 px: 48 -> 27 reads; back to the Library 65.5 -> 2.2 ms); the 256 MiB
  budget is enforced when reads complete; `LayerPrefs` writes only changed values (it
  synced on every Correct hide, 54 ms with an INI file); `PgDn` drops a picked branch that
  was not moved instead of refusing; *Discard* with only a draft pending drops the draft
  and keeps the skeleton's history (J2). (Resolved 2026-10-02) The Library read-ahead
  counts a decoded photo against its 192 MiB as soon as the decode finishes. Still open:
  entering Correct reads the first image's mask and skeleton on the GUI thread.
- (Resolved 2026-09-30) Correct's Tab order follows the screen (WCAG 2.4.3): canvas,
  polyline prompt (while shown), rail (at the chosen tool), Enhance popover (while open),
  the dock left to right, the zoom cluster, then Layers; `Shift+Tab` walks back. It was
  canvas, zoom cluster, rail, dock, then the size slider after Layers.
- (Resolved 2026-09-30) Trace GT's Tab order follows the screen as Correct's does: rail,
  dock, zoom cluster, classes (it followed the build order).
- Very large skeleton edits pay for deflate in the patch history: a Clear at 6000 x 4000
  records in 61-66 ms (6.3 ms with whole copies) and undoes in 30 ms (3.6 ms); at 2048 px in
  6.4-7.7 ms and 3.8 ms. The 640 x 480 data is not affected. Bit-pack 0/255 patches if large
  images become common.
- The skeleton halo's layer costs up to 7 ms per full raster repaint at 4K (3840 x 2160 at
  800 %: 7.06 ms, against 1.29 ms drawn directly with darker joints); the OpenGL canvas is
  not affected.
- (Resolved 2026-09-30) GT export: a source image or tracing over about 179 Mpx (Pillow's
  `DecompressionBombError`) is that image's entry in `errors.txt`, its partial outputs are
  removed and the export goes on; it used to abort the whole export. (Resolved 2026-10-02)
  A drive-relative export folder (`D:out` with the project on `C:`) is resolved once, when
  the export starts, and the dialog shows it in full.
- Dashboards: while one is alive the canvases use raster viewports (Qt cannot show a web
  view in a window that holds OpenGL viewports); hidden dashboards are now released
  5 minutes after Visualize is left, and OpenGL comes back then (the trade-off is a ~1 s
  reopen after that). The first dashboard open still freezes the GUI for 230-270 ms (its
  click handler takes 306-345 ms; real WebEngine, offscreen), although its first length
  chart now comes 1.88 s after a cold click and 0.73 s after entering Measure (second
  walkthrough; the first measured 2.14 s cold). The first faceted build of a grid shape
  takes about 130 ms, and `dcc.Graph` relayouts the grid once (about 120 ms) after its
  first draw. The runtime venv has IPython (pulled in by pipreqs), about 250 ms of the
  first dashboard import; a clean install from `requirements.txt` has none.
- `refresh_outputs` stays on the GUI thread at the end of a generation run (56-64 ms for 23k
  masks and 23k skeletons, once per run; an asynchronous scan would race with saves). The
  pre-Generate mask-origin scan still blocks the GUI thread under a wait cursor, but reads
  the headers on six threads since 2026-10-02: 46k masks 1.4 -> 0.6 s (opening the files
  is most of it). Next step: scan ahead as a Generate warm-up, keyed by size and mtime from
  `scandir`, so a click lists the two folders only (about 0.2 s).
- "Changed since measured" compares file times, so a copied folder can report changes that
  did not happen. (`Ctrl+S` with nothing unsaved: see the owner question V1 above.)
  (Audit 2026-10-12: a copy that keeps the file times, such as `Copy-Item`, raises no
  false alarm. Files stamped anew, or regenerated identically, are all flagged (40 of
  40), and real changes are missed: a repainted mask with an older time reads as current
  and a deleted input is never reported. In the real 46k project 1,152 masks are flagged
  and 1,116 of them still measure the CSV's exact value. R5 in section 8.)
- (Checked 2026-10-12: all 16 launches through `main.py` in the per-user log say "OpenGL
  canvas viewports available (NVIDIA GeForce RTX 5070 Ti/PCIe/SSE2)"; only headless runs
  log "off". Left: nothing in the window says which path is active, P6 in section 8.)
  Check that a normal launch gets hardware OpenGL: in the test sessions Qt picked its
  software renderer (the adapter read as vendor 0x0), which turns the OpenGL canvases off.
  The app log says "OpenGL canvas viewports available" or "off: software renderer"; raster
  frames are about 10x slower.
- (Resolved 2026-10-02) Small texts: the progress row says "Generating masks —
  cancelling…"; toasts keep clear of the Welcome screen's About button; a metric CSV
  value that is not a number shows the dash in the failure colour, not "nan mm".
  (Resolved 2026-09-30: the status line elides a long message in the middle, so a saved
  file's name stays readable; the full text is in the tooltip.)

Second walkthrough (V1-V11, 2026-09-30); V1 is the owner question above, the rest:

- (Resolved 2026-09-30) V2: entering Visualize with the library tree focused gave the
  focus to the Library stage button (two stages looked highlighted, Space left the
  dashboard); the dashboard page takes it now and the tree gets it back on return.
- (Resolved 2026-09-30) V3: opening another folder clears the previous project's
  "Saved <CSV path>" status and its toasts.
- (Resolved 2026-09-30) V4: library date rows were cut mid-word at the default width
  ("2024.07.18 · 8 ima…") because the fit ignored the style's text margins; a row shows
  the longest short form that fits again.
- (Resolved 2026-09-30) V5: with the tree focused in Correct or Trace GT, a letter that
  is not a shortcut no longer moves the tree selection (and so the edited image); the
  search field finds images, arrows and PgDn / PgUp still browse.
- (Resolved 2026-09-30) V6: save toasts update in place instead of stacking, with one
  wording for both editors ("Saved mask · X", "Saved skeleton · X", "Saved GT · X").
- (Resolved 2026-09-30) V7: Trace GT counts as unsaved only while the tracing differs
  from the saved or opened one (undoing every stroke no longer asks to save).
- (Resolved 2026-09-30) V8: entering Generate on a 46k project cost up to 21.7 ms of GUI
  time (2.8 ms before the pass) because the status text recounted every name; the counts
  are cached until the library's outputs change (6.8 -> 1.4 ms to paint).
- (Resolved 2026-09-30) V9: stage warm-ups wait for a 1 s stay in the stage, so passing
  through Generate no longer starts a torch import (worst GUI gap 45.4 -> 12.7 ms);
  the freeze after a warm-up no longer walks the frozen heap (30.4 -> 0.5 ms).
- (Resolved 2026-09-30) V10: the device chip keeps warnings such as "mask model missing"
  whole and shortens the device name first.
- (Resolved 2026-09-30) V11: "Measuring root length is running" and "1 of 40 images
  have a GT tracing" reworded.
- Still open from that walkthrough: a batch job that finishes after another folder was
  opened still reports the previous project (refuse or ask before opening while a job
  runs, or name the project in the job's toasts?). (Audit 2026-10-12, worse than
  recorded: for Generate it is a correctness bug. With folder B opened during the mask
  pass of A, A got its masks and no skeletons, and the chained skeleton pass wrote into B
  and replaced B's existing skeleton, because it reads the open project when it starts
  (`generate_skeleton_handler.py:113`). Measure writes into the right folder. R7 in
  section 8.) (Resolved 2026-10-02: a long file name
  is elided in the middle so the save toast keeps one line.)

### Unified Correct editor, 2026-09-29 (old section 6)

Spec: `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md` (sections 19
and 20).

Owner questions (science or product scope; nothing here blocks use):

- **Q1 Skeleton save fragmentation — RESOLVED (2026-09-29).** Corrections are now saved
  at image resolution and reopen pixel for pixel; generated skeletons open with a
  bilinear upscale (see CHANGELOG, Scientific output changes). Corrections saved before
  the fix are still 341 x 256 and keep their lost fragments. Measuring corrections stays
  an owner decision (FR-03); their lengths are now within a few percent of the generated
  file's instead of 8-22 % of it. (Audit 2026-10-12: on 285 real files over 5 mm a
  correction reads a median 0.8 % longer than Measure's value for the same generated
  skeleton, 90 % of the files between -1.0 % and +3.9 %, extremes -3.4 % and +11.3 %. The
  published `main` (`81d8bcc`) still saves the old format; an old correction is detected
  exactly by its size differing from the photo's. L1 and L5 in section 8.)
- **Q2 JPEG deblock.** A display-only "Smooth JPEG blocks" toggle (default off, never
  written to disk or used by inference)? The images are JPEG quality ~39: smooth zoom
  removes the renderer's pixel squares, but the 8 x 8 compression blocks only fade.
- **Q3 Real images as test data.** Commit 2-3 small dataset images as golden inputs, or
  keep only the synthetic goldens in the repository (current) with the real ones as a
  pre-merge gate?
- **Q4 EXIF orientation in generation — NOT AN ISSUE (closed 2026-10-12).** The entry was
  wrong: no reader applies the EXIF orientation. Mask and skeleton generation decode with
  Pillow, which ignores the tag (masks did so before `abc34fa` too); the Library, Correct
  and Trace GT read with `IMREAD_IGNORE_ORIENTATION`; GT export checks the stored size. A
  JPEG stored 640 x 480 with Orientation 6 gets a mask that opens in Correct without a
  notice. The app uses the stored pixel grid everywhere, which is also what the
  calibration assumes. Left (M3 in section 8): write that policy down, pin it with a test
  per reader, and flag tagged photos in GT export, because the export copies the photo
  with its tag and a reader that applies it (`cv2.imread`) sees the image rotated against
  its label. (Recorded before: "Generated masks come from OpenCV (EXIF applied) while the
  editors use Qt's geometry (EXIF ignored): an EXIF-rotated image would get a 'mask size
  does not match' warning. None of the 40 harness images carries the tag.")
- **Q5 First frame at a new zoom on a dense 2048 px skeleton (OpenGL) — mostly resolved
  (2026-09-30).** The skeleton's vector tiles, the photo's next pyramid level and the
  mask's tiles are made ahead in idle slices, and the endpoint markers are culled and drawn
  as sprites. On OpenGL (dense 2048 px page, each item painted as the viewport paints it)
  the frame after fit → 200 % takes 2.3 ms instead of 3.9 ms and `Ctrl+=` from 50 % takes
  0.8 ms instead of 8.3 ms. Left: the skeleton item's first frame at 100 % (about 3.5 ms).

Follow-ups:

- (Resolved 2026-09-30) Patch-based skeleton history: 100 steps (was 25) with a 64 MiB cap;
  25 commits at 2048 px hold 3.0 MiB instead of 75 MiB (one base copy plus patches).
- A local branch pick for Select (1.5 s → 0.13 ms on a dense 2048 px skeleton; it
  disagrees with today's pick in 1-3 % of clicks next to junctions, so it needs a
  semantics decision). (Audit 2026-10-12: today's pick costs about 1.1 s for the first
  click after an open or an edit on the dense 2048 px skeleton, 70 ms for later clicks and
  11 ms on a real 640 x 480 one. The nearest-pixel pick differs in 2.4-3.4 % of clicks
  within 3 px of the skeleton and in 5.9-9.2 % within 5 px of a junction. An exact
  windowed pick returned today's polyline in 9,000 of 9,000 clicks at about 40 ms, so the
  speed needs no semantics decision. E5 in section 8.)
- (Resolved 2026-09-30) Windowed endpoints and a partial marker repaint: a polyline commit
  plus its paint on the dense 2048 px stress skeleton takes 4.7 ms instead of 29.6 ms.
- (Resolved 2026-09-30) The next and previous images are read ahead on a worker thread:
  `PgDn` costs 19.2 ms of GUI time instead of 78.9 ms at 2048 px in Correct (8.9 instead of
  17.9 ms at 640 px), and about 4.4 instead of 10.4 ms at 2048 px in the Library.
- (Resolved 2026-09-30) Step zooms glide over 120 ms, anchored, and zooming out stops at
  min(100 %, half the fit zoom). (Resolved 2026-10-25, WX-T3) Trackpad pinch on macOS and
  Linux (`QNativeGestureEvent`) zooms about the fingers within the same limits; a smart
  zoom (two-finger double tap) toggles fit and 100 %. Windows touchpads keep pinching
  through Ctrl+wheel. A bicubic refine when idle is not planned (E6, table 3).
- (Resolved 2026-09-30) Layer opacity and visibility are kept across restarts (QSettings,
  `app/gui/correct/layer_prefs.py`).
- (Resolved 2026-09-30) The status chip shows "Image 12 / 40" (counting the rows the library
  search shows), and `Ctrl+'` shows a pixel grid at 800 % and above (display only, in Trace
  GT too).
- (Resolved 2026-09-30) The status chip shortens a camera file name by its suffix, time and
  field first, so tube, L-number and date stay; the full name is the tooltip.
- (Resolved 2026-09-30) Grab and snap distances are screen px (unchanged at 100 %): at
  1600 % an endpoint grabs from its own pixel or a neighbour, not from 160 screen px away.
- (Resolved 2026-09-30) A polyline draft of two or more points counts as unsaved: the one
  prompt covers a tree click, a stage switch, *Load* and quitting, and Save finishes the
  draft first. `PgDn` still stays and warns; when the draft is hidden behind the mask layer
  it now brings the skeleton forward, so Enter and Esc reach the draft.
- (Resolved 2026-09-30) The floating panels' drop shadow is cached on the raster canvas: a
  full frame at 400 % takes 2.8 ms instead of 3.9 ms (1.2 ms instead of 2.6 ms at 100 %).
- (Closed 2026-10-12: a note for script authors, not a product issue; `main.main`,
  `tests/qt_teardown.py` and the one tracked page-building script already tear down.)
  Scripts that build an editor page and leave it and the `QApplication` to interpreter
  shutdown can end with an access violation (about 1 in 30 runs); tear pages down first:
  `close()`, `deleteLater()`, then a few rounds of `sendPostedEvents(None, DeferredDelete)`
  each followed by `sendPostedEvents()` (PyQt frees the signal proxies of deleted widgets
  through queued calls), then `gc.collect()`. The app path (`main.main`) deletes its window
  itself. The tests use `tests/qt_teardown.py` (`flush_deleted`, `delete_widgets`).
- (Resolved 2026-09-30) The GT tests' intermittent access violation: a dead wrapper kept
  alive by those proxies made `findChildren` hand `keep_canvas_focus` a stale wrapper of the
  wrong type; `app/gui/widgets/focus.py` now skips non-widgets, and the GT and raster-editor
  tests tear down deterministically.
- Trace GT as a one-layer client of the Correct workspace.
- (Resolved 2026-09-30) Trace GT *Discard* reverts: it puts back the tracing as last saved
  (or as opened), class ticks and counts included, and clears the history.

Limits measured during the build (not regressions):

- (Resolved 2026-09-29 and 2026-10-02) Masks saved by the editor (RGBA) opened about
  twice as slowly as model masks (8-bit grey). The cost is inflating four bytes per pixel,
  not libpng's grey conversion (OpenCV's unchanged decode is no faster). Both kinds now
  take an exact Pillow fast path (zlib-ng) and are read from disk once: at 640 x 480 an
  editor mask reads in 1.5 ms (2.4 ms through the reference decode) and a model mask in
  0.7 ms (0.85 ms); at 2048 x 1536 16.5 ms (22 ms) and 7.4 ms (9.1 ms). The rest of the
  gap is the four-channel inflate.
- (Resolved 2026-09-30) Undo / redo of a large mask edit at 2048 x 1536 takes about 2 ms
  (was 15-22 ms): the patch box goes through `cv2.LUT` straight into the canvas, and large
  0/1 patches are stored bit-packed.
- (Resolved 2026-09-30) From about 76 % down to 50 % the half-size pyramid level is blended
  in, so fine texture no longer aliases between 50 % and 100 % (display only).
- (Resolved 2026-09-30) On OpenGL the next zoom step's pyramid level is made and uploaded
  when idle: the first frame at 33 % or 67 % of a 6000 x 4000 photo takes 0.3-0.4 ms instead
  of about 10 ms. The first frame after opening such a photo (about 7 ms) remains.
- A single click with brush size 1 paints nothing (Qt's non-antialiased 1 x 1 ellipse
  fills no pixel); the old editors did the same. Fixing it changes saved pixels.
  (Audit 2026-10-12: 0 px at 600 of 600 positions in Correct and in Trace GT, eraser
  included, and the click still adds an empty undo step. A fix limited to size 1 changes
  no golden: every op of both golden cases replays identically. E4 in section 8.)
- (Resolved 2026-09-30) The skeleton's semi-transparent halo is composited once, so it no
  longer looks darker at sharp bends.

### Optimization pass, 2026-09-28 (old section 5)

Owner decisions (not made by the tooling):

- **Licence**: the repository has no LICENSE (PyQt6 is GPLv3 or commercial; options are
  listed in the audit, FR-01). `CITATION.cff` / `pyproject.toml` carry TODOs.
- **FR-03**: Measure reads only the generated `skeletons/*_fake.png`; corrected skeletons in
  `skeleton_corrections/` are never measured. Using them changes values for corrected images.
- **Weights**: `checkpoints.zip` (48 MB) is tracked in git although the README says the
  checkpoints are not committed; the weights have no DOI (FR-07, `docs/MODELS.md`).
  (Audit 2026-10-12: the README mismatch is resolved, `README.md:67` has said that the zip
  is tracked since `9fd535a`; `CLAUDE.md`, `app/models.py:10` and
  `scripts/build_support.py:64` still say the checkpoints are not committed. Still open:
  no DOI, and the zip holds the skeleton model only, so the mask model cannot be obtained
  from the repository. F2 in section 8.)
- **Deterministic cuDNN** changes GPU outputs within the previous run-to-run noise
  (see CHANGELOG "Scientific output changes"); `SPROUTS_CUDNN_BENCHMARK=1` restores
  autotuning. (Resolved in code, checked 2026-10-12: `app/inference/runtime.py:152-154`
  sets it and every run record carries the flags, so nothing is left to build. Left: the
  owner's confirmation of the default, and `docs/MODELS.md:54,82`, which still describe
  autotuning. R2 in section 8.)

Remaining low-severity review items:

- (Resolved 2026-09-30) CQ-07, both tracks of
  `docs/superpowers/plans/2026-09-29-modularise-four-large-files.md`. Track A:
  `main_window.py` 1,556 -> 699 lines; `MainWindow` is built from the parts in
  `app/gui/window/` (library navigator, project loader, `MetricsController`, editor pages,
  stages, job buttons, closing, widgets). Parts without state of their own are functions
  attached to the window under their old names, so no hot path gains a call and every slot
  stays a window method. `tests/test_shell_window_split_surface.py` pins the API surface
  (153 tests); a scripted golden run gave identical session states, signal receivers and
  QObject trees, and start-up stayed within noise (`MainWindow()` 221.0 -> 218.9 ms,
  process start to first paint 438.4 -> 441.7 ms, n = 20). Track B: `dash_app.py`
  (845 -> 178 lines) composes `dash_length_layout.py`, `dash_length_views.py` and
  `dash_length_callbacks.py`; the served layout, callbacks, clientside JS and figure JSON
  are pinned identical by `tests/test_viz_dash_split_golden.py` (audit 2026-10-12: it pins
  the layout hash, the callback wiring and the clientside JS hash; it compares no figure
  JSON). Building the editor pages
  after the first paint was not part of the move: see the owner question in #7.
- The compat shims `app/gui/widgets/tokens.py` (token re-export) and
  `shell_chrome.build_ribbon` have no deprecation plan (CQ-10). (Audit 2026-10-12:
  `build_ribbon` has no caller left and can be deleted; `widgets/tokens.py` is imported by
  61 files and is better kept as the Qt-side name of the tokens. Not listed here: the
  re-exports `app/handlers/worker_base.py` and `app/gui/feedback.py`, and the deprecated
  `app/handlers/window_feedback.py`. F5 in section 8.)
- (Resolved 2026-09-29) CQ-10 for `app/handlers/mask_handler.py`: deleted with the old
  mask editor. CQ-11: `RasterEditorBase` now lives in `app/gui/raster_editor_base.py`
  (Trace GT's editor, built on the shared canvas parts).
- (Resolved 2026-09-29, branch `fix/batch-robustness`) R4: a deeply nested JSON file
  (`gt/classes.json`, a GT metadata chunk, the Enhance defaults, `build_info.json`, the
  recent-projects list) is reported like any other unreadable file. R5: out of GPU memory,
  a sub-batch at least as long as one that already ran out is split untried (under a steady
  memory limit the sub-batches that run are unchanged). R6: a run in which every input
  failed ends as a failure and does not chain the skeleton pass. R7: a run that raises
  after writing outputs replaces `_sprouts_run.json` (`status` "failed", the error); one
  that raises before writing anything leaves it. R8: stale `.<name>.<hex>.tmp` files (older
  than 1 h) are swept from the output folders a moment after a project has loaded, and GT
  files are written through `app.io_utils`.
- Robustness leftovers found while fixing R4-R8:
  - (Resolved 2026-09-30) `app/gt/export.py` writes the split files, `classes.json`,
    `manifest.csv` and `errors.txt` through the atomic writer (same bytes as before).
  - (Resolved 2026-09-30) `runtime.release_cuda_cache()` logs its own errors, so the cleanup
    after a sticky CUDA error no longer replaces the run's own error.
  - (Resolved 2026-09-30) A progress callback that raises is logged and ignored, so a
    written input is never also counted as failed; counts from several writer threads now
    arrive in order (the bar could end at "1 of 7").
  - (Resolved 2026-09-30) The per-user settings folder is swept for stale temp files once
    per process, on a daemon thread.
  - (Resolved 2026-09-30) A run whose full provenance record cannot be built writes a
    minimal `_sprouts_run.json` (same keys, `provenance_error`), or removes the outdated
    one.
  - Owner decision: a run that completes without writing or failing anything (a *Keep
    existing masks* run with nothing missing, an empty folder) still replaces
    `_sprouts_run.json`, so the record of the run that made the existing outputs is lost;
    leaving the record alone in that case would keep it.
- (Resolved 2026-09-30) Toasts keep clear of the status line, the metrics bar and the
  editors' dock, zoom cluster and side panels (Layers, Classes) at every window size
  (`app/gui/toast_clearance.py`).
- (Resolved 2026-09-30) The status line names the device without waiting for a first
  inference ("Checking GPU…" meanwhile; `app/gui/device_check.py`, `SPROUTS_DEVICE_CHECK=0`
  turns the check off). It asks the NVIDIA driver through ctypes
  (`app/inference/device_probe.py`), so torch is not loaded for it: the chip is named
  271 ms after the window shows.
- (Resolved 2026-09-30) Narrow library rows show shorter forms that keep their start
  ("2024.07.18 · 128", "07.18", "L44 · 11:15", "L44") with the full text as a tooltip; the
  metrics bar falls back to "T1 · L44 · 2024.07.18 · 11:15:30".
- (Resolved 2026-09-29) `skimage.morphology` is no longer imported at startup: it is
  imported lazily and prewarmed on a worker thread once the window shows.
- (Resolved 2026-09-29, branch `fix/batch-robustness`) RB-16: the Measure Retry prompt
  deletes itself once answered and the Generate overwrite prompt is freed; the F1 sheet and
  the About dialog no longer raise "wrapped C/C++ object ... has been deleted" when they
  close (`QDialog.exec()` already deletes a `WA_DeleteOnClose` dialog). The progress-hide
  timer is a tracked member timer, pinned by a regression test.

### Early passes, 2026-06 (old sections 1 to 3)

#### 1. Visualization layout — RESOLVED (redesigned 2026-06-01)

Rebuilt both Dash dashboards to the SPROUTS VizView (spec:
`docs/superpowers/specs/2026-06-01-viz-redesign-design.md`): full-bleed,
width-fluid chart that auto-fits the panel; collapsible control strip; metric
chips; Growth-Lines hover images now sit **beside** the chart (300px right
column), not below. No data/callback/id/customdata changes. Verify in the
running app that the chart fills width and the faceted profile scrolls.

#### 2. Ribbon stages — RESOLVED (six stages since 2026-09-29)

The top bar has six stages: Library · Trace GT · Generate · Correct · Measure ·
Visualize (`Alt+1` … `Alt+6`). Generation (automatic) and editing are distinct
stages. *Correct* edits the mask and the skeleton of an image on one canvas; it
replaced the separate *Correct Mask* and *Correct Skeleton* stages (spec
`docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`). The
2026-06-01 split had seven stages; generation has since become one Generate
stage, Trace GT was added, and the two editing stages became Correct.

#### 3. Metrics bar — length/area have no read-only source (RESOLVED 2026-09-28)

`app/data_processing/measurement_index.py` (`MeasurementIndex`) reads the Measure CSVs
read-only (parsed off the GUI thread, re-read only when a CSV changes) and the metrics
bar shows the measured length and area of the selected image. A failed row shows
`failed` with its reason; a value whose inputs changed after it was measured is shown in
the warning colour with a "Measure again" tooltip. Nothing is computed or fabricated.
