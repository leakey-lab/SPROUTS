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

- `MainWindow` is still over 1,000 lines; split it into a library navigator, project
  loader and metrics controller (review CQ-07; planned after the Correct swap, which
  already replaced its two editor toggles with one and dropped the editor-signal
  connect / disconnect bookkeeping). The compat shims `app/gui/widgets/tokens.py`
  (token re-export) and `shell_chrome.build_ribbon` have no deprecation plan (CQ-10).
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
- Robustness leftovers found while fixing R4-R8: `app/gt/export.py` writes the split files,
  `classes.json`, `manifest.csv` and `errors.txt` without the atomic writer; after a sticky
  CUDA error, `runtime.release_cuda_cache()` in the generation `finally` can replace the
  run's own error; a progress callback that raises after a save counts that input as both
  written and failed (`batch_engine.run_batches`); the per-user settings folder (Enhance
  defaults) is not swept for stale temp files; a run whose provenance record cannot be
  built (logged, the run still succeeds) leaves the previous `_sprouts_run.json` in place.
  Owner decision: a run that completes without writing or failing anything (a *Keep
  existing masks* run with nothing missing, an empty folder) still replaces
  `_sprouts_run.json`, so the record of the run that made the existing outputs is lost;
  leaving the record alone in that case would keep it.
- Toasts can cover the metrics bar on the display page; the status line says "Device not
  detected yet" until the first inference; library labels truncate at laptop widths.
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
- **Q5 First frame at a new zoom on a dense 2048 px skeleton (OpenGL).** The first frame
  after a zoom change to 200 % and the first hover frame after a polyline draft starts
  take about 16-19 ms (spec: at most 16 ms, first frames included); steady frames take
  about 0.7 ms. Accept the one-off frame, or profile it (level / tile texture uploads
  versus the endpoint-marker cache) and warm those after the zoom settles.

Follow-ups (not in this build):

- Patch-based skeleton history (25 → 100 steps; 75 MiB → ~0.1 MiB of undo memory at
  2048 px).
- A local branch pick for Select (1.5 s → 0.13 ms on a dense 2048 px skeleton; it
  disagrees with today's pick in 1-3 % of clicks next to junctions, so it needs a
  semantics decision).
- Windowed endpoints and a dirty-rect skeleton overlay beyond what the layer item does:
  a polyline commit on the dense 2048 px stress skeleton takes about 38 ms (budget
  40 ms), of which about 10 ms recompute the endpoints of the whole frame and about 16 ms
  re-rasterise the endpoint markers' cache. Real 640 x 480 skeletons take about 3 ms.
- Background prefetch of the next image (PgDn 9.6 → 2.0 ms of GUI time at 640 px,
  78.7 → 8.7 ms at 2048 px).
- Zoom animation (120 ms, anchored) and `QNativeGestureEvent` pinch (Windows touchpads
  already pinch through Ctrl+wheel); a minimum zoom relative to fit; a bicubic refine
  when idle.
- Keep layer opacity and visibility across app restarts (QSettings; they persist for the
  session only).
- "Image 12 / 40" in the status chip; an optional pixel grid at 800 % and above.
- The status chip elides a long file name in the middle, which can drop the L-number and
  date while the polyline prompt shares its row (1280 x 800); elide the date / time part
  first, show the tree's short label, or move the prompt off that row.
- Grab and snap distances of the skeleton tools are image px (as before), so at 1600 %
  an endpoint grabs from about 160 screen px while its marker is 4 px; they could scale
  with the zoom like the markers.
- Optionally count a polyline draft of two or more points as unsaved, so the prompt also
  covers a tree click or a stage switch (today they drop the draft with a notice).
- On the raster (non-OpenGL) canvas each full frame also repaints the five floating
  panels with their drop shadows (about 1.3 ms of shadows and 1.1 ms of widgets at
  400 %; the frame stays well inside 16 ms); a cached 9-slice shadow would save the
  shadows' share. The OpenGL canvas, used on the owner's machine, is not affected.
- Scripts that build an editor page and leave it and the `QApplication` to interpreter
  shutdown can end with an access violation (about 1 in 30 runs); tear pages down first
  (`close()`, `deleteLater()`, `sendPostedEvents(DeferredDelete)`) as the tests do. The
  app path (`main.main`) deletes its window itself.
- Trace GT as a one-layer client of the Correct workspace; a Trace GT Discard that
  reverts (today it marks the tracing saved and keeps it on screen, as the old mask
  editor did).

Limits measured during the build (not regressions):

- Masks saved by the editor (RGBA) open about twice as slowly as model masks (8-bit
  grey): 2.5-2.8 ms vs 1.1-1.3 ms at 640 x 480, 28-29 ms vs 14 ms at 2048 x 1536,
  because `app/masks/store.py` decodes RGBA through libpng's grey conversion. An exact
  fast path exists (decode unchanged, use one colour channel when R = G = B).
- Undo / redo of a large mask edit at 2048 x 1536 takes 15-22 ms, mostly
  `PaintCanvas.render_rect` building the patch box through a NumPy lookup; the `cv2.LUT`
  path of `PaintCanvas.rebuild` would take it to about 1-2 ms.
- Between 50 % and 100 % zoom the photo is drawn from the full-size image with bilinear
  filtering (the pyramid starts at 50 %), so fine texture can still alias a little there;
  a blend between the two pyramid levels would remove it.
- On OpenGL, the first frame after zooming into a new pyramid level of a very large
  photo (6000 x 4000) takes about 8.6 ms (one-time per level and image; photos up to
  2048 px are unaffected).
- A single click with brush size 1 paints nothing (Qt's non-antialiased 1 x 1 ellipse
  fills no pixel); the old editors did the same. Fixing it changes saved pixels.
- The skeleton's semi-transparent halo can look slightly darker at sharp bends, where
  line segments overlap.
