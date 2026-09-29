# SPROUTS redesign — known issues / to reconsider

Tracked during the `feat/sprouts-redesign` work. These are open items, not regressions introduced by the redesign unless noted.

## 1. Visualization layout — RESOLVED (redesigned 2026-06-01)

Rebuilt both Dash dashboards to the SPROUTS VizView (spec:
`docs/superpowers/specs/2026-06-01-viz-redesign-design.md`): full-bleed,
width-fluid chart that auto-fits the panel; collapsible control strip; metric
chips; Growth-Lines hover images now sit **beside** the chart (300px right
column), not below. No data/callback/id/customdata changes. Verify in the
running app that the chart fills width and the faceted profile scrolls.

## 2. Ribbon split — RESOLVED (2026-06-01)

Ribbon is now 7 explicit stages: Library · Generate Mask · Trace ·
Generate Skeleton · Correct · Measure · Visualize. Generation (auto) and manual
editing (Trace/Correct editors) are distinct entries, each wired to the existing
handlers.

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

- `MainWindow` grew to ~1180 lines; split into a library navigator, project loader and
  metrics controller (review CQ-07). Compat shims (token re-export, `mask_handler.py`,
  `build_ribbon`) have no deprecation plan (CQ-10); `RasterEditorBase` lives in the mask
  editor module (CQ-11).
- A deeply nested `gt/classes.json` raises `RecursionError` instead of `RegistryError`
  (R4); the OOM backoff retries the sibling half at the old batch size (R5); a run where
  every input failed is reported as success (R6); a non-OOM failure mid-run leaves no run
  manifest (R7); a killed process can leave hidden `.tmp` files (R8).
- Toasts can cover the metrics bar on the display page; the status line says "Device not
  detected yet" until the first inference; library labels truncate at laptop widths.
- `skimage.morphology` is still imported at startup (~0.2 s of the 0.6 s cold start).
- Prompt dialogs are created per prompt and not freed; one progress-hide timer is never
  cancelled (RB-16).
