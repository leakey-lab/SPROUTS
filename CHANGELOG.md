# Changelog

All notable changes to SPROUTS (Root-Mask-and-Skeletons) are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/); the version lives in `app/__init__.py`.

Every release has a **Scientific output changes** section: anything that can change a
value in `root_lengths.csv` / `root_areas.csv`, a mask or skeleton pixel, or a
ground-truth export. Record it there even when the change is a bug fix, so results
produced before and after the change can be told apart.

## [Unreleased]

Two passes, not yet released:

- **Unified Correct editor** (branch `feat/unified-correct-editor`, 2026-09-29; spec
  `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`): one **Correct**
  stage replaces *Correct Mask* and *Correct Skeleton*, and the photo stays smooth at every
  zoom on every canvas. Entries that name *Correct Mask* or *Correct Skeleton* describe
  those stages before the merge; their fixes carry over to the mask and skeleton layers of
  *Correct*.
- **Optimization, UI-error and FAIR pass** (branch `feat/ui-perf-refinement`, 2026-09-28):
  a 161-finding audit implemented by per-area packages, then integrated and reviewed.

### Fixed
- Correct: a skeleton file that *Load* cannot read changes nothing; it used to forget the
  skeleton's undo steps and drop its polyline draft.
- The arrow keys in the Library tree keep stepping images while Correct or Trace GT is
  open (the canvas took the keyboard focus after the first step).
- **Discard reverts the mask**: answering *Discard* puts the mask back to its saved file.
  The mask editor used to mark the edits as saved but leave them on screen.
- Undoing back to the saved mask reads as saved again (no unsaved-changes prompt for a
  mask with no net change).
- Enhance on a JPEG with an EXIF orientation tag no longer shows the photo rotated against
  the mask: the photo and Enhance share one decode that ignores the tag, as Qt does.
- **Generate no longer overwrites hand-corrected masks**: when `mask/` already has
  masks, a dialog offers "Keep existing masks (generate missing only)" (default),
  "Overwrite all" (counts the hand-corrected ones) or Cancel.
- Dashboards no longer drop data: `Position` was downcast to int8 and wrapped for depths
  L121-L127, hiding 7.9% of the real 2024 rows in Growth Lines and Faceted views.
- Correct Skeleton no longer freezes on a skeleton with a loop off a junction
  (`vectorize_skeleton` looped forever).
- A successful skeleton run no longer ends in a false "Images directory not found" error,
  and new outputs appear without reloading the folder.
- JPG projects no longer show every traced mask as "No mask" (the status check looked
  for `mask/<stem>.jpg`).
- Ctrl+Y redo works in Correct Mask and Trace GT (two identical shortcuts cancelled each
  other); Ctrl+Shift+Z also redoes.
- A metric CSV open in Excel no longer crashes the app: writes are atomic and a locked
  file gives a Retry prompt. Unhandled exceptions are logged and shown once instead of
  aborting the process.
- Correct Skeleton asks before discarding unsaved corrections and opens the generated or
  corrected skeleton for the selected image automatically (no file dialog per image).
- Keyboard browsing: arrow keys load the highlighted image; PgDn/PgUp step images in
  every stage.
- Toasts no longer nest graphics effects (seven QPainter warnings per repaint); the
  metrics bar elides long image names instead of forcing a 1058 px minimum window width.

### Performance
- Correct decodes an image once for the photo, Enhance and the zoom pyramid, and switching
  between the mask and the skeleton reads no file and keeps the view (switching between the
  two old stages cost 13-20 ms plus a re-fit).
- Mask strokes on OpenGL cost the same at any image size: a stroke re-uploads only the
  256 px tiles it touched (stroke frame at 6000 x 4000: 19.5-22.2 ms -> 0.7-1.8 ms).
- Opening a mask builds its canvas 14-21x faster (640 x 480: 2.8-4.2 ms -> 0.2 ms;
  2048 x 1536: 29-33 ms -> 1.5 ms).
- Skeleton edits re-thin only the edited window, bit-identical to re-thinning the whole
  image (dense 2048 x 1536 stress skeleton: 26.6-27.3 ms -> 0.7 ms per edit).
- Skeleton endpoint markers are cached and drawn only while the skeleton layer is active
  (191 markers: 7.8 ms -> 0.13 ms per repaint); a dense skeleton at high zoom extracts its
  line segments per 256 px tile, at most 4 ms per frame (extracting a whole dense 2048
  frame took 334 ms).
- scikit-image is no longer imported at startup (`import app.gui.main_window` about
  200 ms faster, from 402 ms); a background prewarm loads it once the window shows, so the
  first Correct open does not wait for it either.
- Library zoom on OpenGL: 2.8-7.0 ms -> 0.6-0.95 ms per frame (the view no longer
  re-renders a cached background at every zoom step).
- Cold start to first window 4.1 s -> 0.6 s: no torch, CUDA, Dash or model load on the
  startup path.
- 46k-image library: view-mode switch 2.0 s -> 27 ms, entering/leaving Correct Mask
  1.7 s -> 3 ms, search 80 ms -> 6-19 ms per keystroke (debounced), PgDn 79 ms -> 1 ms.
- Mask/GT editor strokes repaint only their dirty rectangle (3-7 ms -> <0.1 ms per
  segment); undo stores patches (2048 px canvas: +300 MiB -> <1 MiB after 50 strokes);
  saved masks are compressed (1.2 MB -> ~5-8 KB, identical pixels).
- Dashboards: first chart about 30-40% sooner, closing 0.5 s -> 3 ms, faceted profile
  3-7x faster; first GPU run no longer spends 40-70 s in cuDNN autotuning.

### Added
- FAIR metadata: `CITATION.cff` (GitHub "Cite this repository"), `codemeta.json`, this
  changelog, a model card (`docs/MODELS.md`, with the SHA-256 of both checkpoints) and a
  data dictionary (`docs/DATA_DICTIONARY.md`: every output file, column, unit and
  encoding).
- `pip install .` builds an installable package with a `sprouts` command; the version is
  read from `app/__init__.py` only.
- GT dataset export writes `manifest.csv.provenance.json` (`app.provenance` schema): the
  export options (including the split seed), the counts, the class registry and the app
  version/commit.
- Reproducible Windows build: the PyInstaller spec is tracked, anchored at the repository
  root, bundles the SPROUTS icons and dashboard assets, and adds a version resource and
  `build_info.json` (version, commit, build time) to the app.
- CI runs the tests on Windows as well as Linux, builds and inspects the wheel, and
  validates `CITATION.cff`.
- Interface: responsive top bar (compact below the full width), one status line with the
  real device and task state, task progress with counts, ETA and Cancel, Alt+1..6 stage
  keys, a generated F1 shortcut sheet, an About dialog (version, commit, model checksums,
  citation), measured length/area in the metrics bar (read from the Measure CSVs),
  visible focus rings, AA-contrast text tokens, crisp HiDPI icons, collision-free editor
  overlays, offline dashboard assets and a CSV export from each dashboard.
- Provenance sidecars (`*.provenance.json`) next to metric CSVs and generation runs:
  app version, commit, model SHA-256, calibration and runtime precision.

### Changed
- **One Correct stage** replaces *Correct Mask* and *Correct Skeleton* (seven stages become
  six: Library, Trace GT, Generate, Correct, Measure, Visualize; `Alt+1` … `Alt+6`, Correct
  is `Alt+4`). The mask and the skeleton of an image are drawn together on one canvas and
  edited one at a time: `1` / `2` / `X` pick the layer (a tool key of the other layer
  switches too), the other layer stays visible at half its opacity, a Layers panel shows
  each layer's state and visibility, `H` hides both layers and `Shift+H` the other one.
  One prompt names every unsaved layer, and one undo timeline covers both (`Ctrl+Z` undoes
  the last action of either layer without switching layers). Coming back to the same
  image keeps the zoom and position. File formats and locations do not change.
- `Ctrl+S` in Correct saves every layer with unsaved changes, the mask first; with nothing
  unsaved it saves the active layer, as each editor did. A layer without changes that is
  not active is never rewritten (a model mask keeps its 8-bit greyscale file).
- `PgDn` / `PgUp` in Correct save the unsaved layers without asking, then open the next /
  previous image, as in Trace GT; every other way of leaving an image asks once. A polyline
  draft that could be finished (two or more points) is never saved: `PgDn` / `PgUp` then
  stay and say "Finish the line (Enter) or cancel it (Esc) first". A draft dropped by
  opening another image, *Discard*, *Load* or a new folder is announced ("Polyline draft
  discarded").
- Holding `B` and scrolling from the skeleton (or from Fill) switches to the mask brush on
  the first notch, so the dock's slider and the ring show the size being set.
- The rail dims the tools of the layer that is not edited more clearly, and the two erasers'
  tooltips name their layer ("Mask eraser", "Skeleton eraser"). The dock keeps its width
  and place across tools and layers: a tool without a size shows a disabled placeholder,
  *Load skeleton file* is disabled (not hidden) while the mask is edited, and the size
  readout has a fixed width.
- Skeleton endpoint markers, the highlighted endpoint and polyline handles stop growing at
  100 % zoom (at 1600 % a marker was a 128 px disc hiding the end of its line); below
  100 % they shrink with the image as before. Grab distances are unchanged (image px).
- `[` / `]` change the size of the current tool by 12 % (at least 1 px) for the brush and
  both erasers; at the skeleton eraser's default 20 px this is still 2 px.
- Brush and eraser rings use the layer's colour (green mask, orange skeleton); eraser rings
  are dashed.
- A key or click that changes the tool, the layer or the visibility in the middle of a drag
  first ends the drag as a release there would (a mask stroke is committed, a skeleton
  eraser drag is thinned and recorded, a Connect drag joins there); the rest of that drag
  is ignored, and undo / redo wait until it ends. `Esc` in the middle of a mask stroke
  keeps the stroke. (`E` pressed in the middle of a mask stroke used to erase the rest of
  it.)
- **Smooth zoom** on every canvas (Correct, Trace GT, Library): the photo is drawn from an
  area-averaged pyramid with bilinear filtering, so it shows no pixel squares when zoomed
  in and does not shimmer when zoomed out. Masks and GT tracings show their exact saved
  pixels when magnified and thin strokes no longer vanish when zoomed out; the skeleton is
  a line at every zoom. Zoom keys and the − / + buttons step through round percentages
  (… 80, 100, 125, 150, 200, 300, 400 …, up to 2000 %), a right-click on the zoom readout
  offers presets, and the wheel stays continuous. OpenGL canvases use 4x multisampling
  (`ROOT_VIEWER_GL_SAMPLES=0` turns it off, a number sets the sample count).
- The Library overlay draws the generated skeleton with the same layer item: soft at
  100 % and above as before, and never broken into dashes when zoomed out.
- Trace GT runs on the same canvas parts as Correct; its behaviour is unchanged except the
  zoom, and a class key, `E` / `B` or `[` / `]` pressed in the middle of a GT stroke now
  applies from the next stroke (the stroke keeps its class, tool and size).
- Provenance records (`*.provenance.json`) list the packages the run had loaded
  (`app/provenance.py` is unchanged). scikit-image is no longer imported at startup: a
  background prewarm loads it once the window shows (and measuring loads it when needed),
  so records normally still list it; one written before the prewarm finished may not.
- `gt/classes.json` is parsed strictly: a wrong field type (for example
  `"retired": "false"` or `"id": 1.9`) is reported with the class and field it concerns
  instead of being silently coerced, and `"id": 1e999` no longer aborts the app.
- `scripts/build.ps1` and `scripts/build.bat` work from any folder, stop when model
  weights are missing (unless told otherwise) and exit with an error when PyInstaller
  fails (`build.bat` used to report success after a failed build).
- README: the documented PyTorch nightly no longer exists; install the stable
  torch 2.8.0 / torchvision 0.23.0 build for your CUDA version (or the CPU build).
- `SPROUTS_CHECKPOINTS_DIR` relocates the model weights, so a `pip install .` copy
  (whose application folder is `site-packages`) can find them.
- Cancel (or "Stop it and quit") during the last mask batch no longer starts the
  chained skeleton pass; once the window closes no new job can start. On the CPU
  fallback batches are small (masks 4, skeletons 8), so Cancel takes a second or two
  instead of about 10 s; a job that still runs after the first 5 s on close gets the
  window hidden and a longer wait instead of being killed mid-batch.
- `CUDA_VISIBLE_DEVICES=""` (CUDA reported available with no device) now takes the CPU
  fallback; the mask model used to fail to load.

### Removed
- The two editors the Correct stage replaced: `MaskTracingInterface`,
  `SkeletonCorrectionInterface`, their graphics views, `MaskDrawingMixin`,
  `CanvasPixmapItem`, `SkeletonOverlayRenderer` (replaced by `SkeletonLayerItem`) and
  `skeleton_document.ask_unsaved` (replaced by `editor_prompts`); the unused
  `mask_cursor_utils.py` and `app/handlers/mask_handler.py`; the hidden legacy toggle
  buttons and `MainWindow.toggle_mask_tracing` / `toggle_skeleton_correction` (now
  `toggle_correct`). The right-panel pages are renumbered without aliases (`Panel.DISPLAY`
  0, `CORRECT` 1, `VISUALIZATION` 2, `GT_TRACING` 3): the keys `"mask_tracing"` and
  `"skeleton_correction"` are rejected. The shortcut scopes "Correct Mask" and "Correct
  Skeleton" become one scope, "Correct".
- `MainWindow.mask_handler` and the legacy `MainWindow.loading_progress_bar` progress
  shim (handlers report through `MainWindow.feedback`).
- `VisualizationInitWorker` / `VisualizationInitWorkerArea` and their `finished` /
  `error` signals; both dashboards use `app.visualization.viz_window.DashInitWorker`
  (`built` / `failed`).
- black and isort configuration: ruff is the single lint, import-sort and format tool,
  as pre-commit already ran it.

### Scientific output changes
- **None from the Correct stage and the zoom work.** For the same edits, saved masks
  (`mask/<stem>.png`, RGBA; a model mask is never rewritten unless you save it) and saved
  skeleton corrections are pixel-identical to the two editors they replace, and no CSV
  value changes. This is pinned by pixel goldens captured from the pre-merge editors
  (`tests/data/correct_goldens.json`, replayed per layer and through the Correct editor);
  four more cases from the real dataset are a pre-merge gate. Interaction changes that
  alter what a gesture paints (a tool, layer or visibility key pressed in the middle of a
  drag; a GT class key in the middle of a stroke) are listed under *Changed*. The
  skeleton save keeps its known fragmentation (`docs/KNOWN_ISSUES.md` #6, Q1).
- **Tube / Position** (`app/naming.py`, FR-15) come only from stand-alone `T<n>` /
  `L<n>` tokens of the file name. A leading field such as `PLOT12_` or `EXPT2_` is no
  longer read as the tube, and a name without separators (`T1L2_...`) now gives an
  empty Tube and Position. This changes the `Tube` / `Position` columns of both metric
  CSVs and the tube grouping (`group_by_tube`) of a GT dataset export split.
- **Non-ASCII paths** (for example `résumé_T1_L1.png`) are now read
  (`io_utils.imread_unicode`) and measured; they used to become an error row with
  value 0.
- **Deterministic GPU inference** (`app/inference/runtime.py`): cuDNN runs with
  `benchmark` off and `deterministic` on (set `SPROUTS_CUDNN_BENCHMARK=1` for the old
  autotuning). Generated masks and skeletons are now reproducible run to run on the same
  GPU, but they are numerically equivalent to 2.x/3.0 output, not bit-identical: on 128
  real images, 40 of 39.3 M mask pixels differed (area changed on 24/128 images by at
  most 0.00076 mm², 0.085 %) and 502 of 33.5 M skeleton channel values differed by one
  grey level with no pixel crossing the binarisation threshold (root length identical on
  128/128). 2.x/3.0 masks were not reproducible run to run on the GPU either (two
  baseline runs gave different masks).
- Batch sizes (including the new CPU batches) do not change outputs: BatchNorm runs in
  eval mode.

## [3.0.0] - not yet tagged

Version number set on 2026-05-29 (`9a17485`); no release or tag has been published yet.
Main changes since the untagged 2.x code: shared metric core and central configuration,
runtime GPU auto-tuning with CPU fallback, the SPROUTS guided interface, the reworked mask
and skeleton editors, multi-class ground-truth tracing with dataset export, and CI.

### Scientific output changes
- `7b326d7` (2026-05-29) **Root length**: the skeleton is measured as an 8-connected arc
  length (diagonal steps count as the diagonal pixel pitch, per-axis calibration) instead
  of a pixel count. On 300 real skeletons, new/old length has median 1.104 (5th-95th
  percentile 0.975-1.288). **Root area** is calibrated from the mask's actual resolution
  (a mask that is not 640x480 was previously off by the square of the resolution ratio).
- `aa59106` (2026-05-31) Metric CSVs gain the `Error` column (failed rows keep value 0;
  the reason is in `Error`). GPU mask and skeleton inference switched to mixed-precision
  autocast (bf16 where the GPU supports it).
- `bf1f898` (2026-06-01) GPU mask and skeleton inference returned to fp32 without
  autocast. In this release cuDNN TF32 convolutions and `cudnn.benchmark` stayed
  enabled, so its GPU results were not bit-reproducible run to run (fixed under
  [Unreleased]: deterministic cuDNN). Masks and skeletons generated on a GPU between
  2026-05-31 and 2026-06-01 used mixed precision.
