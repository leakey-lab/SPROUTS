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
- A deeply nested `gt/classes.json` raises `RecursionError` instead of `RegistryError`
  (R4); the OOM backoff retries the sibling half at the old batch size (R5); a run where
  every input failed is reported as success (R6); a non-OOM failure mid-run leaves no run
  manifest (R7); a killed process can leave hidden `.tmp` files (R8).
- Toasts can cover the metrics bar on the display page; the status line says "Device not
  detected yet" until the first inference; library labels truncate at laptop widths.
- (Resolved 2026-09-29) `skimage.morphology` is no longer imported at startup: it is
  imported lazily and prewarmed on a worker thread once the window shows.
- Dialogs created per prompt outside the editors (in `MainWindow`, the F1
  `ShortcutsDialog`) are not freed, and one progress-hide timer is never cancelled
  (RB-16). The editors' unsaved-changes prompts are freed after each answer
  (`app/gui/editor_prompts.py`).

## 6. Open after the unified Correct editor (branch `feat/unified-correct-editor`, 2026-09-29)

Spec: `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md` (sections 19
and 20).

Owner questions (science or product scope; nothing here blocks use):

- **Q1 Skeleton save fragmentation.** Saving a skeleton re-renders the whole
  image-resolution raster down to the loaded size (usually 341 x 256) with
  nearest-neighbour and re-thins it: one 3 px erase + Save turned 13 components into 109,
  and the dotted result comes back on reopen. Correct keeps this bit-compatible with the
  old editor. A fix (edit at the native raster and display it scaled, or a
  connectivity-preserving downscale) changes saved skeleton pixels and is linked to FR-03.
- **Q2 JPEG deblock.** A display-only "Smooth JPEG blocks" toggle (default off, never
  written to disk or used by inference)? The images are JPEG quality ~39: smooth zoom
  removes the renderer's pixel squares, but the 8 x 8 compression blocks only fade.
- **Q3 Real images as test data.** Commit 2-3 small dataset images as golden inputs, or
  keep only the synthetic goldens in the repository (current) with the real ones as a
  pre-merge gate?
- **Q4 EXIF orientation in generation.** Generated masks come from OpenCV (EXIF applied)
  while the editors use Qt's geometry (EXIF ignored): an EXIF-rotated image would get a
  "mask size does not match" warning. None of the 40 harness images carries the tag.

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
