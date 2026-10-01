# SPROUTS redesign — known issues / to reconsider

Tracked during the `feat/sprouts-redesign` work. These are open items, not regressions introduced by the redesign unless noted.

## 1. Visualization layout — RESOLVED (redesigned 2026-06-01)

Rebuilt both Dash dashboards to the SPROUTS VizView (spec:
`docs/superpowers/specs/2026-06-01-viz-redesign-design.md`): full-bleed,
width-fluid chart that auto-fits the panel; collapsible control strip; metric
chips; Growth-Lines hover images now sit **beside** the chart (300px right
column), not below. No data/callback/id/customdata changes. Verify in the
running app that the chart fills width and the faceted profile scrolls.

## 2. Ribbon stages — RESOLVED (six stages since 2026-09-29)

The top bar has six stages: Library · Trace GT · Generate · Correct · Measure ·
Visualize (`Alt+1` … `Alt+6`). Generation (automatic) and editing are distinct
stages. *Correct* edits the mask and the skeleton of an image on one canvas; it
replaced the separate *Correct Mask* and *Correct Skeleton* stages (spec
`docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`). The
2026-06-01 split had seven stages; generation has since become one Generate
stage, Trace GT was added, and the two editing stages became Correct.

## 3. Metrics bar — length/area have no read-only source (RESOLVED 2026-09-28)

`app/data_processing/measurement_index.py` (`MeasurementIndex`) reads the Measure CSVs
read-only (parsed off the GUI thread, re-read only when a CSV changes) and the metrics
bar shows the measured length and area of the selected image. A failed row shows
`failed` with its reason; a value whose inputs changed after it was measured is shown in
the warning colour with a "Measure again" tooltip. Nothing is computed or fabricated.

## 4. Ground-truth tracing (Trace GT) — v1 scope limits (OPEN, by design)

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
  and still needs design sign-off.

## 5. Open after the 2026-09-28 optimization pass (branch `feat/ui-perf-refinement`)

Owner decisions (not made by the tooling):

- **Licence**: the repository has no LICENSE (PyQt6 is GPLv3 or commercial; options are
  listed in the audit, FR-01). `CITATION.cff` / `pyproject.toml` carry TODOs.
- **FR-03**: Measure reads only the generated `skeletons/*_fake.png`; corrected skeletons in
  `skeleton_corrections/` are never measured. Using them changes values for corrected images.
- **Weights**: `checkpoints.zip` (48 MB) is tracked in git although the README says the
  checkpoints are not committed; the weights have no DOI (FR-07, `docs/MODELS.md`).
- **Deterministic cuDNN** changes GPU outputs within the previous run-to-run noise
  (see CHANGELOG "Scientific output changes"); `SPROUTS_CUDNN_BENCHMARK=1` restores
  autotuning.

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
  are pinned identical by `tests/test_viz_dash_split_golden.py`. Building the editor pages
  after the first paint was not part of the move: see the owner question in #7.
- The compat shims `app/gui/widgets/tokens.py` (token re-export) and
  `shell_chrome.build_ribbon` have no deprecation plan (CQ-10).
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

## 6. Open after the unified Correct editor (branch `feat/unified-correct-editor`, 2026-09-29)

Spec: `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md` (sections 19
and 20).

Owner questions (science or product scope; nothing here blocks use):

- **Q1 Skeleton save fragmentation — RESOLVED (2026-09-29).** Corrections are now saved
  at image resolution and reopen pixel for pixel; generated skeletons open with a
  bilinear upscale (see CHANGELOG, Scientific output changes). Corrections saved before
  the fix are still 341 x 256 and keep their lost fragments. Measuring corrections stays
  an owner decision (FR-03); their lengths are now within a few percent of the generated
  file's instead of 8-22 % of it.
- **Q2 JPEG deblock.** A display-only "Smooth JPEG blocks" toggle (default off, never
  written to disk or used by inference)? The images are JPEG quality ~39: smooth zoom
  removes the renderer's pixel squares, but the 8 x 8 compression blocks only fade.
- **Q3 Real images as test data.** Commit 2-3 small dataset images as golden inputs, or
  keep only the synthetic goldens in the repository (current) with the real ones as a
  pre-merge gate?
- **Q4 EXIF orientation in generation.** Generated masks come from OpenCV (EXIF applied)
  while the editors use Qt's geometry (EXIF ignored): an EXIF-rotated image would get a
  "mask size does not match" warning. None of the 40 harness images carries the tag.
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
  semantics decision).
- (Resolved 2026-09-30) Windowed endpoints and a partial marker repaint: a polyline commit
  plus its paint on the dense 2048 px stress skeleton takes 4.7 ms instead of 29.6 ms.
- (Resolved 2026-09-30) The next and previous images are read ahead on a worker thread:
  `PgDn` costs 19.2 ms of GUI time instead of 78.9 ms at 2048 px in Correct (8.9 instead of
  17.9 ms at 640 px), and about 4.4 instead of 10.4 ms at 2048 px in the Library.
- (Resolved 2026-09-30) Step zooms glide over 120 ms, anchored, and zooming out stops at
  min(100 %, half the fit zoom). Still open: `QNativeGestureEvent` pinch (Windows touchpads
  already pinch through Ctrl+wheel) and a bicubic refine when idle.
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
- Scripts that build an editor page and leave it and the `QApplication` to interpreter
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

- Masks saved by the editor (RGBA) open about twice as slowly as model masks (8-bit
  grey): 2.5-2.8 ms vs 1.1-1.3 ms at 640 x 480, 28-29 ms vs 14 ms at 2048 x 1536,
  because `app/masks/store.py` decodes RGBA through libpng's grey conversion. An exact
  fast path exists (decode unchanged, use one colour channel when R = G = B).
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
- (Resolved 2026-09-30) The skeleton's semi-transparent halo is composited once, so it no
  longer looks darker at sharp bends.

## 7. Open after the UX and speed pass (branch `feat/unified-correct-editor`, 2026-09-30)

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
  deterministic-cuDNN decision in section 5.
- **Supported formats.** Skeleton generation also reads `.bmp`, `.tif`, `.tiff` and `.ppm`,
  which the Library, mask generation and Measure-area ignore: such an image gets a skeleton
  and a `root_lengths.csv` row, but no mask and no Library entry. Changing either side
  changes outputs.
- **Case-variant stems on Windows.** `A.png` and `a.jpg` are two Library images but share
  one output file on NTFS (`mask/A.png`), so their saves still race (two files with exactly
  the same stem no longer do). It needs case-insensitive stems in the Library and in
  generation together. Rare.
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
  from the generated skeleton? Best decided together with W16.
- **Build the editor pages after the first paint?** The hidden Correct and Trace GT pages
  cost about 140 ms of the `MainWindow()` build (214.7 -> 74.1 ms with empty stand-ins,
  n = 12): their constructors take about 50 ms, the rest is reparenting and style work.
  That is about a third of process start to first paint (about 440 ms). The split left
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
  re-measure every input even when nothing changed, and the Generate prompt says "Overwrite
  all (0 hand-corrected)" (W12).

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
- Same class as the warm-up fix, no known trigger: the Measure CSV parse
  (`app/gui/window/metrics.py`) and the device check catch `Exception` only, so a
  `BaseException` there would leave the metrics loading for good (warm-ups blocked, the bar
  not refreshed) or the chip at "Checking GPU…"; a folder scan that fails with anything but
  `OSError` reports neither outcome, and the loading card would stay up.
- (Resolved 2026-09-30) Photo and raster layer items were freed only by the cyclic garbage
  collector (`TiledLevels` held a bound method of the item); they are now freed as soon as
  their scene lets go of them (browse at 2048 px: peak 1374 -> 657 MB, no GC pause).
- (Resolved 2026-09-30) Read-ahead duplicates and small Correct fixes: Correct keeps the
  image it leaves as a finished read and adopts the Library's photo decodes (a 15-action
  session at 2048 px: 48 -> 27 reads; back to the Library 65.5 -> 2.2 ms); the 256 MiB
  budget is enforced when reads complete; `LayerPrefs` writes only changed values (it
  synced on every Correct hide, 54 ms with an INI file); `PgDn` drops a picked branch that
  was not moved instead of refusing; *Discard* with only a draft pending drops the draft
  and keeps the skeleton's history (J2). Still open: the Library read-ahead counts a photo
  against its 192 MiB only once it is shown; entering Correct still reads the first
  image's mask and skeleton on the GUI thread.
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
  removed and the export goes on; it used to abort the whole export. Still open: a
  drive-relative export folder (`D:out` with the project on `C:`) stays relative.
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
  pre-Generate mask-origin scan still runs on the GUI thread (about 1.0 s for 46k masks,
  with a wait cursor).
- "Changed since measured" compares file times, so a copied folder can report changes that
  did not happen. (`Ctrl+S` with nothing unsaved: see the owner question V1 above.)
- Check that a normal launch gets hardware OpenGL: in the test sessions Qt picked its
  software renderer (the adapter read as vendor 0x0), which turns the OpenGL canvases off.
  The app log says "OpenGL canvas viewports available" or "off: software renderer"; raster
  frames are about 10x slower.
- Small texts: "Cancelling Generating masks…" reads awkwardly; a danger toast can cover the
  Welcome screen's About button at 1024 x 640; a metric CSV edited to hold `nan` shows
  "nan mm". (Resolved 2026-09-30: the status line elides a long message in the middle,
  so a saved file's name stays readable; the full text is in the tooltip.)

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
  runs, or name the project in the job's toasts?); a long file name wraps the Correct
  save toast onto two lines.
