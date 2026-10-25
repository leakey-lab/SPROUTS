# CLAUDE.md — Root-Mask-and-Skeletons

Desktop app (**PyQt6 + PyTorch**, Python 3.10) for **plant-root phenotyping**
from minirhizotron images. Two model branches, both from the ORIGINAL image
(the skeletonizer never reads masks, so mask corrections change area only):

```
image → ResNet mask      → Correct (mask layer)     → root area (mm²)   ┐
image → Pix2Pix skeleton → Correct (skeleton layer) → root length (mm) ┴→ CSV (+ provenance) → Dash/Plotly viz
```
(Correct is ONE stage that edits both layers of an image on one canvas. Measure
currently reads the generated skeletons only; corrected skeletons are not
measured — owner decision FR-03, see `docs/KNOWN_ISSUES.md` #5.)

See `README.md` for the user-facing overview and calibration math. This file is
the agent contract: how to run things, where things live, and the conventions to
follow.

---

## Environment

- **Use `./venv/Scripts/python.exe` for every run.** System Python is CPU-only
  torch and *falsely* reports CUDA available. Never invoke bare `python` /
  `python3`. (memory: `venv-is-real-torch`)
- Platform: Windows 11, PowerShell. Use PowerShell syntax (`$null`, `$env:VAR`,
  backtick line-continuation).
- PyTorch is installed **separately** from `requirements.txt` so the build
  matches the local CUDA toolkit (pip won't silently swap in a CPU wheel). See
  README "Installation".

---

## Commands

```powershell
# Run the app
./venv/Scripts/python.exe main.py

# Tests (Qt needs an offscreen platform in headless/CI)
$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest

# Lint + import sort + format: ruff only (pyproject.toml — line-length 100, py310).
# Same tools pre-commit runs; black/isort are not used (they fight ruff format).
./venv/Scripts/python.exe -m ruff check --fix .
./venv/Scripts/python.exe -m ruff format .
```

- Test suite pins the scientific calibration (length/area), skeleton
  vectorization, the CSV schema, and an import-smoke of every app module.
- CI: `.github/workflows/ci.yml` runs that suite on push. Two CI gotchas already
  fixed (memory `ci-deps-install-gotchas`): `requirements.txt` must stay UTF-8
  (was UTF-16), and `pywin32` is gated to `platform_system == "Windows"` (no
  Linux wheel). `.pre-commit-config.yaml` mirrors the lint/format hooks.
- Model checkpoints are **not** committed — place under `checkpoints/`
  (`mask_weights/best_mask_model_V5.pth`, `skeletonizer/latest_net_G.pth`).

---

## Architecture map

Entry: `main.py` — sets Qt6 D3D11 RHI + ANGLE flags (WebEngine stability, see
`gles3-error-from-disable-gpu`), loads `resources/themes/dark_theme.qss`, applies
the SPROUTS dark palette, opens `MainWindow`.

| Area | Path | Notes |
|---|---|---|
| Config | `app/config.py` | **Single source of truth**: `Calibration` (FOV 18×13 mm), ports, paths, folder names, batch sizes. Change FOV here only. Must not import torch (lazy `SKELETON_BATCH_SIZE`). |
| Qt-free core | `app/project_layout.py`, `naming.py`, `io_utils.py`, `provenance.py`, `models.py`, `user_paths.py`, `design/tokens.py`, `editing/`, `imaging/`, `masks/`, `enhancement.py` | Project folder conventions, the one file-name parser, atomic + unicode-safe I/O, provenance sidecars, model SHA-256 registry, per-user dirs, design tokens, undo history + the cross-layer `EditJournal` (stdlib only), the image pyramid of smooth zoom, mask store. Unit-testable; no PyQt6/torch at import. |
| Inference | `app/inference/` | `metrics.py` = shared length+area primitives (de-duplicated); `skeleton_inference.py` = Pix2Pix; ResNet mask inference; `runtime.py` = shared GPU auto-tuner (device/autocast/channels_last) + singleton skeleton-model cache. |
| Handlers | `app/handlers/` | Batch orchestration: threads subclass `GuardedThread` (`app/workers.py` via `worker_base.py`: error boundary + cancel); `JobRegistry` (`main_window.jobs`) runs one batch job at a time; progress goes through `main_window.feedback` (`UiFeedback`, `app/feedback.py`). Generation never overwrites hand-traced masks without asking. |
| GUI | `app/gui/` | PyQt6 widgets. `MainWindow` (`main_window.py`) is composed from the parts in `app/gui/window/`: `navigator` (library browsing, arrow debounce, PgDn), `project_loader` (open folder, scan outcomes, recents), `metrics` (`MetricsController`, the one stateful part, held as `window.metrics`), `editors` (page switching, editor reports), `stages` (stage changes, status summary, Visualize entry), `job_buttons`, `lifecycle` (closing), `body` (widgets, toast chrome), `_common`. Stateless parts are functions attached in the class body under their old names (no call hop; slots stay window methods): new window logic goes into a part, and `tests/test_shell_window_split_surface.py` pins the API surface. `MainWindow` orchestrates and **delegates** to `library_index` (O(1) tree index, search, navigation; `neighbours` / `position_of` feed the read-aheads and the position chip), `ui_panels`, `visualization_manager`, `shell_chrome` (`ShellTopbar`, `StatusLine`; six stages Library · Trace GT · Generate · Correct · Measure · Visualize, `Alt+1`…`Alt+6`), `shortcuts` (single key registry → F1 sheet; scopes Global, Library, Trace GT, Correct), `window_state`, `about_dialog`, `recent_projects`, `device_check` (status-line device chip resolved off the GUI thread; `SPROUTS_DEVICE_CHECK=0` opts out; named through the NVIDIA driver by the Qt-free `app/inference/device_probe.py`, no torch), `warmup` (intent-based warm-ups: `STAGE_WARMUPS` / `BUTTON_WARMUPS` tables, one at a time on fresh threads, never during a load, CSV parse or job; torch on a reused `QThreadPool` thread deadlocked) with `app/gc_policy.py` (full collections held during a warm-up import, `gc.freeze()` after start-up and each warm-up), `toast_clearance` (toasts keep clear of the bottom chrome and the editors' side panels), `display_read_ahead` (Qt-free: the Library decodes the next / previous photo on a worker, ≤ 3 photos / 192 MiB, used only while the file stamp matches), `canvas_key_relay` (the library tree hands the hold and gesture keys B, Space, S, Enter, Esc, Delete to the editor canvas), `stage_summary` (Qt-free texts) + `stage_status` (pool scandir) for the Generate / Measure status line, `visualize_empty_state` (Visualize with nothing to chart: why, and the next step). **Page shortcuts** are `WindowShortcut`s: live wherever the keyboard is while their page is on screen (Qt ignores hidden pages; text fields keep their typing, modal dialogs block them); the `PgDn` / `PgUp` owners use `WidgetWithChildrenShortcut`, so exactly one is live. Panel indices: `app/gui/panels.Panel` — `DISPLAY 0`, `CORRECT 1`, `VISUALIZATION 2`, `GT_TRACING 3` (dashboards appended after); use the members, never int literals or the removed keys `"mask_tracing"` / `"skeleton_correction"` (`tests/test_right_panel_contract.py`). |
| Editors | `app/gui/` | Views share `canvas_view.CanvasViewMixin` (zoom/pan/fit/OpenGL, `ZOOM_STOPS` ladder, 120 ms anchored step-zoom glide, `zoom_out_limit` = min(100 %, half the fit), pixel grid from 800 % toggled by `Ctrl+'` — display only). Both editors are built from the same parts: `editor_canvas.EditorCanvasView` (tool input to an `InputSink`), `editor_chrome.EditorChrome` (rail, dock, Enhance popover, status chip, zoom cluster on one `widgets/overlay_layout.OverlayLayout`), `paint_canvas.PaintCanvas` (QImage canvas over `PatchHistory`; 0/1 patches bit-packed) and `editor_prompts` (Save / Discard / Cancel). **Correct** = `app/gui/correct/`: `CorrectEditor` (`editor.py`) only composes — `CorrectSession` (Qt-free: open / close, dirty, save reports, the one prompt, one undo timeline over `app/editing/journal.EditJournal`), `ToolRouter` (input), two `EditLayer`s (`MaskLayer` + `mask_tools.py` over the Qt-free `MaskDocument`; `SkeletonLayer` over the Qt-free `SkeletonDocument` / `SkeletonCorrectionModel` — patch history with one base copy, 100 steps, 64 MiB; windowed endpoints — tools in `skeleton_tools.py`), `CorrectControls`, `LayerPanel` (rows and eyes Tab-reachable), `keys.py` (the page's shortcut table), `chip.py` (status-chip name forms, "Image 12 / 40"), `layer_prefs.LayerPrefs` (layer opacity / visibility in QSettings), `paging.Neighbours` + the Qt-free `read_ahead.ReadAhead` (next / previous image — photo, mask plane, thinned skeleton — read on one worker, ≤ 3 images / 256 MiB, used only while the files' stamps match; layers opt in through `ReadAheadLayer`); contract in `correct/layer.py`, every `correct/*.py` ≤ 450 lines (ratchet test). **Trace GT** = `GtTracingInterface` on `raster_editor_base.RasterEditorBase`. Render items `canvas_layers/` (thresholds use the device scale = zoom × devicePixelRatio): `PhotoLayerItem` (area pyramid `app/imaging/pyramid.py`, bilinear, level 1 blended in below about 76 %; warms the next zoom step when idle), `RasterLayerItem` (exact pixels at scale ≥ 1, area coverage below, 256 px tiles; warms the next zoom step's tiles when idle, only while shown), `SkeletonLayerItem` (a line at every zoom; vector tiles warmed when idle; halo composited once; `vectors=False` in the Library). Every editor has `maybe_save_changes()` (False = stay). |
| Visualization | `app/visualization/` | Dash/Plotly length + area dashboards (`dash_shell.py` shared layout, offline assets in `app/assets/`), `DataProcessor` / `DataCache` / `FigureMemo`, embedded via `QWebEngineView` through one `viz_window.DashVizWindow` base. plotly.js basic is vendored as `app/assets/plotly-basic.min.js` (dcc.Graph uses it through `window.Plotly`; `tests/test_viz_dash_plotly_bundle.py` pins it to plotly.py's plotly.js version and to basic trace types — update it with plotly.py, see `app/assets/THIRD_PARTY.md`). Strip, caret and view-card callbacks are clientside (tests run the JS in QJSEngine via `tests/viz_dash_helpers.call_clientside`). Dashboards are released 5 min after Visualize is left, which restores the OpenGL canvases. `dash_app.py` composes `dash_length_layout.py` (pure layout builders), `dash_length_views.py` (view rendering, export table) and `dash_length_callbacks.py` (`register_callbacks`); `tests/test_viz_dash_split_golden.py` pins the served layout, callbacks and figure JSON. |
| Mask model | `app/mask_model/` | ResNet segmentation (`EnhancedResnetGenerator`, SE/`DualAttention` blocks). |
| GT tracing | `app/gt/` + `app/gui/gt_tracing_interface.py` | Multi-class **ground truth** for segmentation datasets ("Trace GT" stage, right_panel index 3 = `Panel.GT_TRACING`). Qt-free core: `registry.py` (`gt/classes.json`), `store.py` (`gt/<stem>.png` 8-bit palette, id 255 = Ignore), `export.py` (dataset export). **Independent of every ML model**: never reads/writes `mask/` or `skeletons/`, never measures. Spec: `docs/superpowers/specs/2026-09-22-multiclass-labels-design.md`. |

**God nodes** (most-connected core abstractions — touch with care): the list is
refreshed by `graphify update .`; the one recorded before the Correct swap named
the two editors it deleted, so read the current list from
`graphify-out/GRAPH_REPORT.md`.

**Other single-source files:** `app/inference/runtime.py` (how inference runs),
`resources/themes/dark_theme.qss` + `app/gui/widgets/tokens.py` (SPROUTS design
tokens; QSS images such as the check mark resolve through the `sprouts-theme:` search
path registered in `app/gui/widgets/icons.py`). SPROUTS accents: mask green `#5fd6a0`,
skeleton orange `#f0a868`, purple primary `#c39af6`, selection `#b794f6`.

---

## Docs

- `docs/KNOWN_ISSUES.md` — **read first** for current open items.
- `docs/Architecture_2.md` — deeper architecture / data-flow write-up.
- `docs/BUILD_EXECUTABLE.md` — PyInstaller packaging (CUDA build notes).
- `docs/SPROUTS_DASH_THEME.md` — Dash theme + design-token reference.
- `docs/superpowers/{plans,specs}/` — implementation plans + specs (viz redesign,
  ribbon/metrics, SPROUTS shell, multi-class GT, unified Correct editor, and the
  MainWindow / length-dashboard split, executed 2026-09-30).

**Open items**: `docs/KNOWN_ISSUES.md` #5 (owner decisions: licence, FR-03
corrected-skeleton measurement, tracked `checkpoints.zip`; remaining low-severity
review items), #6 (the Correct stage follow-ups; Q1 skeleton save fragmentation resolved)
and #7 (after the 2026-09-30 UX and speed pass: batch-size dependence of GPU outputs,
supported formats, Save placement and Save with nothing unsaved (V1), building the editor
pages after the first paint, remaining speed follow-ups).
The metrics bar shows measured values read-only from the Measure CSVs
(`MeasurementIndex`) — **never fabricate values**. `CHANGELOG.md` has a
"Scientific output changes" section: record anything that can change a CSV value
or an output pixel there. Saved mask and skeleton pixels are pinned by golden tests
(`tests/data/correct_goldens.json`): editor changes must keep them identical.

---

## Memory — local auto-memory (file-based, per-project)

Path: `~/.claude/projects/D--Root-Mask-and-Skeletons/memory/`

- `MEMORY.md` is the index, loaded each session — **read it for the current fact
  list** (don't trust a hard-coded list here; it drifts).
- Each fact is its own `.md` file with frontmatter (`type:` user | feedback |
  project | reference).
- **Before recommending a file/flag named in a recalled memory, verify it still
  exists.** Memories reflect what was true when written.
- Write here for durable decisions, debugging insights, user preferences, env
  gotchas. Update the matching file instead of duplicating; delete memories
  proven wrong.

---

## Knowledge graph — graphify

Graph at `graphify-out/` (built AST-only, no API cost).

- Before architecture/codebase questions, read `graphify-out/GRAPH_REPORT.md`
  (god nodes, communities, surprising connections).
- Navigate `graphify-out/wiki/index.md` instead of raw-file sweeps when it covers
  the area.
- After editing code this session, run `graphify update .` to refresh the graph
  (check staleness: graph header lists its build commit vs `git rev-parse HEAD`).
- `/graphify` for a fresh build of any input.

---

## Skills & tooling conventions

- **Search/explore:** prefer Glob/Grep and the graphify wiki over raw file
  sweeps. For broad multi-file fan-out, delegate to `Explore` or cavecrew agents.
- **Code review:** `/code-review` for correctness bugs; `/simplify` for
  quality-only cleanup; `caveman:caveman-review` for compressed PR feedback.
- **Caveman mode** is active in this workspace — chat responses compressed,
  technical substance preserved. Code / commits / PRs still written normally.
- **superpowers** skills (brainstorming, TDD, systematic-debugging,
  verification-before-completion) govern *how* to approach work — invoke the
  relevant process skill before implementation.
- **Academic / writing work** (reports, papers, lit reviews):
  `academic-research-skills` (`/ars-*`) and `claude-scientific-writer` skills are
  installed.

---

## Workflow defaults

1. Recall memory (file `MEMORY.md`) at start.
2. Consult graphify before architecture questions.
3. Use `./venv/Scripts/python.exe` for any run.
4. Persist new decisions/insights to file memory.
5. Run `graphify update .` after code edits.
