# Unified Correct Editor and Smooth Zoom — Design Spec

**Status:** planned 2026-09-29 from four read-only scout reports (`architecture`, `zoom-render`,
`ux-latency`, `contracts-tests` in `.claude/handoff/unified-editor-2026-09-29/scout/`); revised
the same day after the plan critic (21 issues; the lead's binding decisions are in
`.claude/handoff/unified-editor-2026-09-29/notes/critic_decisions.md`); pending user review.
**Branch:** `feat/unified-correct-editor`, from `feat/ui-perf-refinement` @ `d9a4f6f`; the
"Correct" registry rows (wave 0) are committed as `99876e9`.
**Plan:** `docs/superpowers/plans/2026-09-29-unified-correct-editor.md` (packages, file
ownership, shared contracts, integration checklist).

## Purpose

The user asked (2026-09-29) for two things:

1. **One tracing workspace.** Fixing one image today needs two stages, "Correct Mask" and
   "Correct Skeleton", with two canvases, two decodes, two prompts and a view that resets on
   every switch. Merge them into one stage where both layers are drawn together and edited
   in place.
2. **Zoom that does not pixelate.** Every editor draws the photo nearest-neighbour, so zooming
   in shows "huge squares"; zooming out aliases. The photo must look smooth at every zoom.

The quality bar is optimised code, lower latency and a better user experience, without a new
god class.

## Hard rules (settled with the user before this spec)

1. **Merge the workspace, not the documents.** One stage, "Correct", replaces "Correct Mask" and
   "Correct Skeleton" (7 stages become 6). One canvas, one image decode, both layers drawn
   together (mask in `tokens.MASK`, skeleton in `tokens.SKEL`), an active-layer switch, per-layer
   tools, per-layer opacity and visibility. The inactive layer stays visible as a reference.
2. **Documents and file formats do not change.** The mask is a uint8 plane at image resolution
   with patch undo, saved through `app.masks.MaskStore` to `mask/<stem>.png` (white on
   transparent RGBA, colour type 6; an empty mask deletes the file). The skeleton is a 1 px
   raster in `SkeletonCorrectionModel`, re-thinned after every edit, opened from
   `skeleton_corrections/<stem>_skeleton.png` else the generated `skeletons/<stem>_fake.png`, and
   saved to `skeleton_corrections/<stem>_skeleton.png` at the loaded raster size (usually 341x256).
   For the same user edits the saved files are pixel-identical to today and no CSV value changes.
3. **Trace GT stays a separate stage** (ground truth, independent of every ML model, writes only
   `gt/`). It keeps its behaviour and gets the zoom fix.
4. **Out of scope:** raster spline / anatomy tools (the design must not block adding raster tools
   later), FR-03 (Measure reads only generated skeletons), the licence, `checkpoints.zip`.
5. **Zoom:** the photo is smooth at every zoom and does not alias or shimmer when zoomed out. How
   the edit layers render is decided deliberately (section 10).
6. **Focused modules.** `SkeletonCorrectionInterface` (85 graph edges) and `MainWindow` (72) are
   already god nodes; the new editor is a composition root over small parts.

---

## 1. Goals and non-goals

**Goals (each has a test or a measured gate, section 15):**

- G1 One "Correct" stage: open, edit, undo, save and navigate both documents of an image without
  a stage switch; the view never resets while the image stays the same.
- G2 Saved files are pixel-identical to today for the same edits (pixel goldens captured from the
  pre-merge editors); `root_areas.csv` / `root_lengths.csv` unchanged.
- G3 The photo is smooth when magnified and alias-free when minified, on every canvas (Correct,
  Trace GT, Library).
- G4 Edit layers show exactly the saved pixels when magnified, and thin features never vanish
  when minified.
- G5 Lower latency: one decode per image, a sub-5 ms layer switch, GL stroke frames independent
  of image size, cheaper endpoint markers, and about 200 ms less import time at startup.
- G6 Trace GT keeps every behaviour and test; it moves onto the shared canvas parts.
- G7 No module over about 450 lines in `app/gui/correct/`; `CorrectEditor` only composes.

**Non-goals (this build):** anatomy / raster spline tools; fixing the skeleton save
fragmentation (owner question Q1); measuring corrected skeletons (FR-03); a JPEG deblock filter
(Q2); bicubic refinement; zoom animation and macOS/Linux pinch gestures; background prefetch of
the next image; patch-based skeleton history; a local (faster) branch pick for Select; persisting
layer opacity across app restarts. Section 20 lists them as follow-ups.

---

## 2. What is wrong today (evidence from the scouts)

| Problem | Evidence |
|---|---|
| The photo is drawn nearest-neighbour | Mask/GT photo item is `FastTransformation` (`app/gui/mask_tracing_interface.py:156`) and the view turns smoothing off (`mask_graphics_view.py:32`). The skeleton view asks for `SmoothPixmapTransform` (`skeleton_correction_graphics_view.py:35-40`) but `QGraphicsPixmapItem::paint` overrides it with the item's default `FastTransformation` (Qt 6.11 `qgraphicsitem.cpp:9544-9545`). On the user's GL path (RTX 5070 Ti) this is `GL_NEAREST`: at 400 % 76 % of neighbouring screen pixels are equal (runs of 4.18 px), at 2000 % 95 % (20.5 px). |
| Zoom-out aliases on every canvas | No prefilter anywhere (GL has no mipmaps, raster has no area averaging). Zone-plate ring contrast at 25 %: editors 90.1, Library 73.3, ideal 29.3. 1-px stripes render all white at 25 % and all black at 50 %. Moire crawls during zoom (frame-to-frame change 6.8 vs 2.0 with a pyramid). |
| Thin edit features vanish when zoomed out | A 1-px mask stroke has 0 coverage at 25/33/50/75 % depending on its row; the skeleton line vanishes at 25 % on some rows. |
| GL stroke latency grows with image size | Every stroke segment re-uploads the whole mask texture (120/120 new cacheKeys): 0.86 ms at 640x480, 2.8 ms at 2048x1536, 19.5-22.2 ms at 6000x4000. |
| Two stage visits per image | Zoom and position reset on every switch (4.0 → 1.385 → 1.429); the two editors fit the same image differently (1.288 vs 1.244); 10 prompts for 5 images; stage switches cost 13-20 ms; neither editor shows the other layer. Keystroke-level estimate: 5-10 s of overhead per image. |
| Inconsistent semantics | Mask Discard only marks the mask clean and keeps the "discarded" edits on screen (`mask_tracing_interface.py:560-562`); the mask reads unsaved after undoing back to its saved state (`mask_drawing_tools.py:214-223`); E / `[ ]` / H / Ctrl+S are handled differently in each editor; Enhance is per editor. |
| Latency that grows with density | Endpoint markers are one path re-rasterised per repaint (0.52 / 7.8 / 17.8 ms with 28 / 191 / 1738 markers); a skeleton commit re-thins the whole frame (26 ms dense 2048); `skimage.morphology` is 204 ms of the 402 ms `import app.gui.main_window`. |

Real data (the 40-image harness set) is 640x480 JPEG at quality ~39; per-edit latencies there
are already below one frame, so the workflow gains dominate; the rendering and skeleton gains
matter for large images.

---

## 3. The Correct stage

### 3.1 Placement

- Topbar: **Library · Trace GT · Generate · Correct · Measure · Visualize** (six stages,
  `Alt+1 … Alt+6`, Correct = `Alt+4`, icon `brush`, tooltip "Fix the mask (area) and the skeleton
  (length) of an image on one canvas.").
- Right-panel pages (`app/gui/panels.Panel`, renumbered deliberately, no aliases):
  `DISPLAY = 0`, `CORRECT = 1`, `VISUALIZATION = 2`, `GT_TRACING = 3`. Dashboards are still
  appended after the fixed pages. `Panel.coerce("mask_tracing")` and `"skeleton_correction"` now
  raise `ValueError`. `MainWindow.switch_right_panel` logs and ignores an unknown key, so that
  alone would hide a stale caller; a guard test (`tests/test_right_panel_contract.py`) rejects the
  legacy keys anywhere in `app/`, so a stale caller fails in the suite.
- Stage action page: one ghost button **"Clear mask" / "Clear skeleton"** that follows the active
  layer (replaces the two stage-specific buttons).

### 3.2 Page layout

```
+--------------------------------------------------------------------------------------+
| [name.jpg * Unsaved]           [Finish line (Enter)] [Cancel (Esc)] [Smooth]          |  chip + polyline prompt
| * Skeleton . Polyline . 3 points . drag points to adjust            [Enhance popover] |  hint line
| +-------+                                                     +--------------------+  |
| | Brush |  mask group                                         | 1 [] Mask    (eye) |  |  Layers panel
| | Erase |                                                     |      * Saved       |  |  (side_panel role)
| | Fill  |                                                     | 2 [] Skeleton(eye) |  |
| | ----- |                                                     |      * Unsaved     |  |
| | Select|  skeleton group      photo + mask + skeleton        +--------------------+  |
| | Erase |                                                                            |
| | Poly  |                                                                            |
| | Link  |                                                                            |
| | ----- |                                                                            |
| | Pan   |                                                                            |
| | Enh.  |                                                                            |
| +-------+  [size --o- 8 px] [opacity ---o- 60%] | [Load] [Undo] [Redo] [Clear] | [Save]  [- 100% + Fit] |
+--------------------------------------------------------------------------------------+
```

- **Rail:** mask group (Brush B, Eraser E, Fill F) | skeleton group (Select V, Eraser E,
  Polyline P, Connect C) | Pan | Enhance. Each group's "on" colour is its layer colour; the group
  of the inactive layer is drawn in `tokens.TEXT_FAINT` and stays clickable (clicking a tool of the
  other group switches layer). Smooth moves from the rail into the polyline prompt.
- **Dock:** size of the current tool (brush size for the mask brush and eraser, eraser diameter
  for the skeleton eraser, hidden for Fill, Select, Polyline and Connect), opacity of the active
  layer, "Load skeleton file…" (skeleton layer only), Undo, Redo, Clear (active layer), Save.
- **Layers panel** (right, `OverlayLayout` role `side_panel`, never takes focus): one row per
  layer, top row = mask: key hint (1 / 2), colour swatch, name, eye (visibility), state dot and
  label (Saved / Unsaved / No mask / Generated / Loaded file / Not saved). The active row has a
  `tokens.BG_2` fill and a 2 px bar in the layer colour. A click on a row activates it. The
  measured cost of a 155 px side panel on the fit zoom is nil at 1180x740 and 1600x1000 and
  1.079 → 1.072 at the 929x640 minimum page.
- **Status chip:** file name and the aggregate state (error > unsaved > saved > none); hint line
  "● <Layer> · <tool hint>" with the dot in the active layer colour.
- **Zoom cluster** bottom right, **Enhance popover** top right, as today.

---

## 4. Layer model

### 4.1 Documents (unchanged formats)

| | Mask | Skeleton |
|---|---|---|
| Data | uint8 plane 0/1, image size, `PatchHistory` (100 entries / 64 MiB) | uint8 0/255 1 px raster at image size in `SkeletonCorrectionModel`, `SnapshotHistory` (25 whole-mask snapshots) |
| Opens from | `MaskStore.for_image(image).read` (threshold > 200); a size mismatch opens empty with a warning | the correction if it exists, else the generated skeleton (injected resolver first); 341x256 → image size with `INTER_NEAREST` + skeletonize |
| Saves to | `mask/<stem>.png` via `write_mask_file` (RGBA colour type 6); an empty plane removes every saved variant | `skeleton_corrections/<stem>_skeleton.png` via `render_to_size(save_size or SKELETON_OUTPUT_SIZE)` (nearest + skeletonize), atomically |
| Project root | the image's folder | `images_base_folder` or the image's folder |
| Dirty | **differs from the saved baseline** (new: a baseline copy of the plane, compared once per revision, 0.61 ms at 2048x1536) | differs from the saved baseline (unchanged) |

Both documents live in Qt-free modules (`app/gui/correct/mask_document.py`,
`app/gui/correct/skeleton_document.py`) and move today's load/save code verbatim.

### 4.2 Layers on one canvas

- Fixed z-order: photo 0 < mask 1 < skeleton 1.5 < skeleton decorations 2-3.5 (polyline preview
  and connect line 2, endpoint markers and handles 3, highlight 3.5) < rings 10. The active layer
  is never raised: skeleton lines lie inside the mask, so a 60 % green fill over a 1 px line would
  hide it. The skeleton sits below every decoration, so the polyline preview always paints over
  the line.
- Default opacity: mask 60 %, skeleton 85 %, both 0-100 %. The inactive layer is drawn at its own
  opacity × 0.5 (`REFERENCE_DIM`), so it reads as a reference without competing.
- Visibility: eye per layer (Layers panel), **H** shows/hides both layers (peek at the photo),
  **Shift+H** shows/hides the inactive layer. Editing a hidden active layer shows it first, with an
  info toast ("Mask shown").
- Endpoint markers show only while the skeleton layer is active; the skeleton halo stays in both
  states.
- Opacity, visibility, the active layer and each layer's last tool persist for the session
  (across images and stage re-entry), not across app restarts.

### 4.3 Switching the active layer

- Keys **1** (mask), **2** (skeleton), **X** (swap); a tool key of the other layer switches too
  (B/F → mask, V/P/C → skeleton); **E** is the eraser of the active layer; a click on a Layers-panel
  row or on a rail tool of the other group.
- A switch in the middle of a drag first ends the drag as a release there would (a mask stroke is
  committed, a skeleton eraser drag is thinned and recorded), then switches; the rest of that drag
  is ignored until the next press (section 5, "Gestures in progress"). A polyline draft survives a
  switch and resumes when the skeleton becomes active again (its preview is hidden meanwhile).
- Cost target ≤ 5 ms (prototype 0.94 ms), the view does not move. Today's equivalent is a
  13-20 ms stage switch plus a re-fit.

---

## 5. Tools per layer

| Layer | Tool | Key | Size | Behaviour (unchanged from today unless noted) |
|---|---|---|---|---|
| Mask | Brush | B (tap), hold B + wheel = size | brush 1-100 px (8) | round, no antialiasing, pixel centres, one undo entry per stroke |
| Mask | Eraser | E | same brush size | `DestinationOut` |
| Mask | Fill | F | — | fills the closed outline under the click (outer contours); outside → info notice, no undo entry |
| Skeleton | Select | V | — | pick a branch → Polyline edit, Delete removes it |
| Skeleton | Eraser | E | diameter 1-80 px (20) | erases the whole segment per move, thins on release |
| Skeleton | Polyline | P | — | click points, drag, Ctrl+click bend, Shift+drag move, Enter / double-click commit, Esc cancel, S smooth |
| Skeleton | Connect | C | — | drag from an endpoint to a point |

- **`[` / `]`** change the size of the current tool by ±12 % (min ±1 px). For the skeleton eraser
  at its default 20 px this is ±2 px, as today.
- **Rings** use the active layer's colour; eraser rings are dashed (the dash means "erase", the
  colour says which layer). Today the rings were white and red.
- **Tools are classes behind a small protocol** (`LayerTool`: press / move / release /
  double_click / key / deactivate / hint): the skeleton tools already are (`app/gui/skeleton_tools.py`),
  the mask tools become `app/gui/correct/mask_tools.py`. A future raster tool is one more class.
- **Gestures in progress.** A key or click that changes the tool, the layer or visibility (H,
  Shift+H, the Layers eye), and every save, PgDn / PgUp, open, close, Discard, reload or stage
  switch, first ends a drag in progress as a release at the pointer's last position would: a mask
  stroke is committed, a skeleton eraser drag is thinned and recorded, a Connect drag joins at that
  point. Nothing is dropped silently, and the rest of that drag is ignored until the next press.
  Undo and redo are ignored during a drag. Esc in the middle of a mask stroke keeps the stroke (the
  mask has nothing to cancel); on the skeleton Esc keeps today's behaviour (the pixels an eraser
  drag already erased stay, a Connect drag and the polyline draft are dropped). Today E in the
  middle of a mask stroke erased the rest of it; now the rest of that drag is ignored.
- A double-click on the mask paints one dab (one undo entry), as today.

---

## 6. Key map (one registry scope, "Correct")

| Key | Action | Kind |
|---|---|---|
| 1 / 2 / X | Mask / Skeleton / swap layers | QShortcut |
| B (tap) · hold B + wheel | Mask brush · brush size (from either layer) | key (router) |
| F · V · P · C | Mask fill · Skeleton select / polyline / connect (switch layer) | QShortcut |
| E | Eraser of the active layer | QShortcut |
| `[` / `]` | Smaller / larger current tool | QShortcut |
| H / Shift+H | Show/hide both layers / the inactive layer | QShortcut |
| S | Smooth the polyline (Polyline only) | key |
| Enter · Esc · Delete / Backspace | Finish line · cancel · delete picked branch | key |
| Space (hold), middle-drag | Pan | key / gesture |
| Ctrl+Z · Ctrl+Y / Ctrl+Shift+Z | Undo · redo (one timeline) | QShortcut |
| Ctrl+S | Save (section 8) | QShortcut |
| Ctrl+0 · Ctrl+1 · Ctrl+= / Ctrl++ · Ctrl+- · Ctrl+wheel | Fit · 100 % · zoom in · zoom out · zoom about the cursor | QShortcut / gesture |
| PgDn / PgUp | Save the unsaved layers, then next / previous image | editor QShortcut (documented by the Global rows) |
| Alt+1 … Alt+6 | Stages (Correct = Alt+4) | Global |

Letters that act on the whole page are `QShortcut`s (they work whichever overlay control has
focus, like the mask editor today); keys that depend on the gesture (B hold, S, Enter, Esc,
Delete) are handled by the canvas router. No key is both. `find_conflicts()` stays empty: 1 / 2
exist only in Trace GT's scope, X and Shift+H nowhere else. Gestures: Ctrl+click (bend),
Shift+drag (move line), double-click (finish line). X, H and Shift+H do not auto-repeat, so
holding them does not flicker the layers.

---

## 7. History and undo: one timeline

- Each document keeps its own stack (mask `PatchHistory`, skeleton `SnapshotHistory`); a Qt-free
  `EditJournal` (`app/editing/journal.py`) records the **order** of actions across layers.
- A mask action is recorded when its patch is committed (stroke release, fill, clear); a skeleton
  action when its snapshot is pushed (`SkeletonCorrectionModel.on_action`, called by `push_undo`).
- A new action ends every redo branch: the journal's redo list is emptied and the other layer's
  redo stack is dropped (`drop_redo()` on `SnapshotHistory`, `PatchHistory` and the model).
- **Ctrl+Z:** ignored while a drag is in progress; otherwise, with the skeleton active, a polyline
  draft loses its last point first (today's behaviour); otherwise the journal undoes the newest
  action of **either** layer. Entries a layer has already evicted (skeleton cap 25, mask cap 100 /
  64 MiB) are skipped.
- A layer's undo or redo steps exactly one entry of its own stack and reports whether it did. It
  reports "nothing done" only when that stack is empty, which the journal reads as "evicted"; it
  never pops a draft point (only the Ctrl+Z draft rule above does).
- An undo or redo of a skeleton action drops a polyline draft, as today. When the draft was hidden
  (the mask is active), an info toast says "Polyline draft discarded".
- Undoing an action of the **inactive** layer does not switch tools or layers: that layer is shown
  if hidden, its Layers row flashes, and an info toast says "Undo · skeleton".
- Opening an image or a new folder clears the journal; Discard or loading another skeleton file
  forgets that layer's entries.
- Undo / Redo buttons follow the journal. Trace GT is a one-layer editor and keeps its own undo.

Why one timeline: one canvas means "undo what I just did", as in Photoshop, Krita and GIMP.
Per-layer undo (QGIS) would silently undo an older action of the layer the user switched to.

---

## 8. Dirty, save and the prompt

- **Dirty** = the document differs from its saved state, for both layers. Undoing back to the
  saved state reads clean (a change for the mask). The editor is dirty when any layer is;
  `dirty_changed` fires on transitions only.
- **Ctrl+S / Save:** saves every dirty layer (mask first, then skeleton). With nothing dirty it
  saves the **active** layer, exactly as each editor's Ctrl+S does today (the explicit way to
  accept a generated skeleton or to mark a model mask as hand-checked). A clean inactive layer is
  never rewritten. A stroke in progress is committed first; a polyline draft is not.
- One summary toast ("Saved mask and skeleton · name.jpg"); an emptied mask says "Empty mask:
  removed saved mask for name.jpg"; a failed layer gets a danger toast with the reason, keeps its
  edits and shows "Not saved" until its next edit. `MainWindow.on_mask_saved` stops toasting (it
  still records the badge and refreshes the metrics bar).
- **Every save reports to `MainWindow`**, whichever path made it (Ctrl+S, PgDn / PgUp, Save in the
  prompt): a written mask → `mask_saved`, an emptied mask whose file was removed → `mask_cleared`
  (the Library badge and the metrics bar follow), a written skeleton → `skeleton_saved` (not
  connected, FR-03). A failed save reports nothing. Today's prompt saves report the same way.
- **The prompt** (tree click, arrow keys, stage switch, window close, open folder, Load skeleton
  file on a dirty skeleton): one Save / Discard / Cancel box per image naming the dirty layers, with
  today's wording: title "Unsaved mask" / "Unsaved skeleton" / "Unsaved mask and skeleton", text
  "Save changes to the mask and skeleton for name.jpg?", informative text "Discarded edits cannot be
  recovered.", Esc = Cancel, default Save.
  - Save → saves the dirty layers; if one fails, a second box names only the failed layers
    ("The skeleton for name.jpg could not be saved." — Discard / Keep editing).
  - **Discard reverts the dirty layers**: the mask from its saved baseline in memory (identical to
    the file), the skeleton by re-reading its start file, exactly as today's skeleton Discard.
    This fixes today's mask Discard, which marked the edits clean but left them on screen.
  - Cancel / Keep editing → the caller stays (`maybe_save_changes()` returns False).
- `has_unsaved_changes()`, `maybe_save_changes()` (False = stay) and `reset()` keep their names
  and meaning for `MainWindow`.

---

## 9. Opening, re-entry and navigation

- **Open** (`load_image(path, images_base_folder)`): one decode through the editor's
  `NormalizationControls` source cache (`cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION`, which
  matches Qt's geometry: Qt ignores EXIF orientation, cv2 applied it, so a rotated JPEG would have
  had a transposed plane). The same array feeds the photo, Enhance and the photo pyramid. Both
  layers open; the view fits. Enhance, if on, applies to every image.
- **Same image again** (stage re-entry, tree re-click): with unsaved edits nothing happens;
  when clean and none of the stamped files changed since the documents were opened, documents,
  history and view are kept (no reload, no re-fit); when one changed (a Generate run, or our own
  skeleton save wrote the correction), the documents reload and the view is kept. The stamped
  files are the image, every mask candidate (`mask/<stem>.png` and the legacy types, in lookup
  order), the correction (`skeleton_corrections/<stem>_skeleton.png`) and the generated skeleton
  as the resolver finds it at the time of the check (else `skeletons/<stem>_fake.png`), each by
  path, mtime and size, or as absent. This keeps saved pixels identical to today, including the
  skeleton save round trip (Q1): after our own save the correction's stamp changed, so re-entry
  reloads the saved correction, exactly as today's editor did.
- **PgDn / PgUp** save the dirty layers without a prompt, then open the next / previous visible
  image, counted from the image open in the editor (Trace GT's contract). A failed save stays on
  the image with its notice. Every other exit uses the prompt.
- **Arrow keys** in the library keep the 40 ms debounce; **tree clicks** prompt.
- No background prefetch in this build: at 640x480 an open costs about 5-10 ms (section 14).

---

## 10. Rendering and zoom

### 10.1 Photo (every canvas: Correct, Trace GT, Library)

`PhotoLayerItem` (`app/gui/canvas_layers/photo.py`) over a Qt-free area-averaged pyramid
(`app/imaging/pyramid.py`: level k = `INTER_AREA` half of level k-1, ceil sizes, built lazily):

- The level is chosen from the painter's **device scale** (zoom × devicePixelRatio):
  `level = floor(-log2 s)` for s < 1, else 0. It is drawn with bilinear filtering
  (`SmoothPixmapTransform`), exposed rect only.
- Measured with the prototype: 400 % equal neighbours 0.76 → 0.15 (real GL), zone plate at 25 %
  equals the ideal (29.3), moire during zoom 6.8 → 2.0, frame cost unchanged (GL 0.4-1.2 ms,
  raster 0.11-0.47 ms), pyramid 0.09 ms (640) to 5.4 ms (6000) and built only for the level in use.
- At exactly 100 % bilinear equals the source (no blur at identity).

### 10.2 Mask and GT edit layers: exact pixels, never vanishing

`RasterLayerItem` (`app/gui/canvas_layers/raster.py`) draws the editor's canvas `QImage`:

- **Device scale ≥ 1: exact pixels (nearest).** Each displayed pixel is one saved pixel; the brush
  ring is centred on pixel centres; Fill needs closed outlines and a smoothed layer would show
  half-painted edges that are not in the file and hide the 1 px gaps that make a fill fail. Over a
  smooth photo a crisp layer reads as annotation (napari, QuPath). This keeps
  `test_zoomed_canvas_pixels_stay_crisp`.
- **Device scale < 1: area coverage.** A premultiplied area-averaged pyramid of the canvas
  (patched lazily by dirty rect): a 1 px stroke at 25 % shows at a quarter of its opacity instead
  of disappearing on three row parities out of four (72 dB vs 28.9 dB against the ideal).
- **256 px tiles:** a stroke segment invalidates only the tiles it touched, so on GL only those
  tiles re-upload. Stroke frames at 6000x4000: 19.5-22.2 ms → 0.77-0.89 ms.
- The canvas becomes a `QImage` (ARGB32 premultiplied) instead of a `QPixmap`; painting uses the
  same raster engine and composition modes, so bytes are identical (golden tests prove it).

### 10.3 Skeleton layer: a line at every zoom

`SkeletonLayerItem` (`app/gui/canvas_layers/skeleton.py`) draws the model's 1 px raster:

- **s < 1:** the line + halo overlay (`skeleton_overlay.overlay_indices`) through a **max-pool**
  pyramid, so a 1 px line never breaks into dashes.
- **1 ≤ s < 2:** the overlay at level 0, nearest.
- **s ≥ 2:** vector links between 8-neighbour pixel centres (m-adjacency: no corner triangles;
  isolated pixels as dots; collinear runs merged), drawn as a 2 px cosmetic line in the skeleton
  colour over a 4 px halo (`tokens.BG_0` at alpha 170). Vectors are extracted lazily at paint
  time, per 256 px tile of the exposed region, and cached per tile; an edit only invalidates the
  tiles it touched and never extracts anything itself (the raster overlay is updated for the
  edited box only). One paint spends at most 4 ms extracting; tiles still pending draw the level-0
  overlay for a frame or two. Budgets keep every frame cheap: GL up to 6000 visible segments;
  raster 2 px + halo up to 400, 1 px up to 2500, else the level-0 overlay. Measured ≤ 1.3 ms on
  GL for a real 640 skeleton; a whole-frame extraction costs 334 ms on the dense 2048 stress
  skeleton (zoom-render scout §3.3), hence the tiles.
- Justification: the skeleton's data is a graph of pixel centres. Linking 8-neighbours shows its
  connectivity exactly (every vertex is a model pixel), where 20 px orange squares at 2000 % made
  diagonal steps and gaps hard to read. The saved raster is untouched.
- The Library overlay uses the same item with `vectors=False` and `scale = image size / skeleton
  size`. Its input is the generated file, whose lines are about 2 px wide (1.56-2.23x their
  skeletonized pixel count on the harness set), not a 1 px skeleton, so it keeps today's look:
  the max-pool pyramid below 1, and at s ≥ 1 the file's pixels drawn with bilinear filtering (soft
  edges, no pixel blocks), without the `INTER_NEAREST` blow-up and with no halo.

### 10.4 GL viewport

- 4x MSAA on the canvas `QOpenGLWidget` so vector items (skeleton, rings, endpoints, previews) are
  antialiased on GL (measured within noise on the RTX 5070 Ti). `ROOT_VIEWER_GL_SAMPLES=0` turns it
  off for weak GPUs.
- `FullViewportUpdate` on GL and `SmartViewportUpdate` on raster stay; both paths must be good
  (this agent's sessions got llvmpipe, which the app rejects, so raster is a real fallback).
- The Library drops `DeviceCoordinateCache` (a CPU re-render and new texture per zoom step:
  2.3-6.2 ms GL zoom frames → the static cost, about 0.5 ms).

### 10.5 Zoom controls

- **Keys and buttons** step along a ladder with integer stops from 2: 0.1, 0.125, 0.167, 0.25,
  0.333, 0.5, 0.667, 0.8, 1, 1.25, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20. Exact-pixel layers then
  show uniform blocks and the readout shows round numbers. From 100 % the steps are still 125 %
  and 80 %.
- **Wheel and touchpad** stay continuous (1.2 per notch, fractional deltas), anchored at the cursor.
- **Readout:** click toggles 100 % / fit (as today); right-click opens presets: Fit, 50, 100, 200,
  400, 800, 1600 %.
- Limits stay 10 %-2000 %.

### 10.6 HiDPI

Every threshold (photo level, exact vs coverage, skeleton vector tier) uses the device scale from
`painter.deviceTransform()`, which includes the devicePixelRatio (verified with
`QT_SCALE_FACTOR=2`: zoom 0.25 picks level 1 at dpr 2, level 2 at dpr 1).

### 10.7 What the user will still see

The images are JPEG quality ~39: the gradient across 8x8 block boundaries is 2.39x the interior
gradient. Bilinear removes the renderer's squares; 32 px compression squares at 400 % only fade
(block-edge ratio 2.61 → 1.90). A display-only deblock toggle is owner question Q2.

---

## 11. Trace GT and the Library

- **Trace GT** keeps its stage, its document, its semantics (flag-based dirty, Discard marks clean,
  PgDn saves then moves, class panel, digits) and all 31 of its tests. `RasterEditorBase` moves to
  `app/gui/raster_editor_base.py` (closes KNOWN_ISSUES CQ-11) and is rebuilt from the shared parts:
  `EditorCanvasView`, `PhotoLayerItem`, `RasterLayerItem`, `PaintCanvas`, `EditorChrome` and
  `editor_prompts`. Every hook GT overrides and every attribute its tests read keeps its name
  (`_open_document` receives the image size as a `QSize` instead of the decoded `QPixmap`; GT's
  body is unchanged because it only reads `width()` / `height()`). GT moves onto the new base only
  once that base passes its own tests, so the app never imports a half-built base.
  `MaskDrawingMixin`, `MaskTracingGraphicsView` and `CanvasPixmapItem` are deleted.
- **Library:** `PhotoLayerItem` for the photo, `SkeletonLayerItem(halo=False, vectors=False)` at
  80 % for the overlay (bilinear at s ≥ 1, as today; section 10.3), a second `PhotoLayerItem` for
  the side-by-side skeleton image.

---

## 12. Architecture

### 12.1 Modules

| Module | Qt | Responsibility | ~Lines |
|---|---|---|---|
| `app/editing/journal.py` | no (stdlib) | `EditJournal`: order of actions across layers | 110 |
| `app/imaging/pyramid.py` | no (numpy, cv2) | `ImagePyramid`, `area_half`, `max_half` | 140 |
| `app/gui/canvas_layers/{photo,raster,skeleton}.py` | yes | the three layer items (section 10) | 170 / 260 / 400 |
| `app/gui/editor_canvas.py` | yes | `EditorCanvasView` + `InputSink`: one view for Correct and GT | 160 |
| `app/gui/paint_canvas.py` | yes | `PaintCanvas`: QImage canvas over a `PatchHistory` (today's mixin, verbatim painting) | 270 |
| `app/gui/editor_chrome.py` | yes | `EditorChrome`: rail, dock, popover, chip, zoom cluster, `OverlayLayout` | 170 |
| `app/gui/editor_prompts.py` | yes | the Save / Discard / Cancel and failed-save boxes (today's strings) | 90 |
| `app/gui/raster_editor_base.py` | yes | Trace GT's shell on the shared parts | 380 |
| `app/gui/correct/layer.py` | no | `EditLayer` / `LayerHost` / `LayerTool` protocols, `LayerStatus`, `SaveOutcome`, `ToolSpec`, `SizeSpec`, `Notice` | 150 |
| `app/gui/correct/mask_document.py` | no | `MaskDocument` (plane, history, baseline, `MaskStore` I/O) | 180 |
| `app/gui/correct/mask_layer.py`, `mask_tools.py` | yes | `MaskLayer` (EditLayer) and Brush / Eraser / Fill tools | 260 / 150 |
| `app/gui/correct/skeleton_document.py` | no | `SkeletonDocument` (model, files, source, save size, errors) | 190 |
| `app/gui/correct/skeleton_layer.py` | yes | `SkeletonLayer` (EditLayer + `ToolHost`; `skeleton_tools.py` unchanged) | 330 |
| `app/gui/correct/session.py` | no | `CorrectSession`: open / close / journal / dirty / save / the prompt | 300 |
| `app/gui/correct/tool_router.py` | yes | `ToolRouter`: `InputSink` for Correct (pointer, B hold, gesture keys) | 200 |
| `app/gui/correct/controls.py`, `layer_panel.py` | yes | rail groups, dock, polyline prompt; the Layers panel | 280 / 200 |
| `app/gui/correct/editor.py` | yes | `CorrectEditor`: composition root and the MainWindow-facing API | 300 |

Qt-free modules already in `app/gui` stay where they are (`skeleton_graph_model.py`,
`patch_history.py`, `skeleton_tools.py`, `skeleton_overlay.py`, `skeleton_document.py`): moving
them is optional layering that would churn six test files for no user gain. `app/editing` stays
stdlib-only (the journal qualifies; `PatchHistory` uses numpy and stays in `app/gui`).

### 12.2 No new god class

`CorrectEditor` owns no tool, document or history logic: it builds the parts, forwards the
MainWindow API, implements `LayerHost` by delegation and re-emits signals. Logic lives in the
session (lifecycle), the router (input), the layers (documents + tools + items) and the controls.
A ratchet test caps every `app/gui/correct/*.py` at 450 lines; `graphify update .` after the build
checks `CorrectEditor`'s degree.

### 12.3 Hot paths gain no heavy hop

Stroke: view → router (1 call) → layer → tool → `PaintCanvas.extend` → `RasterLayerItem.canvas_changed`
(today: view → `continue_stroke` → `draw_point` → `CanvasPixmapItem.update`). Two extra Python calls,
well under a microsecond. Hover, eraser drag and zoom paints follow the same rule. No new eager
heavy imports; `skimage` becomes lazy in `skeleton_graph_model.py` and `app/inference/metrics.py`,
and `MainWindow` prewarms it off the GUI thread once the window shows (`prewarm_skeletonize()` in
the thread pool, like `_prewarm_measurements`), so neither startup nor the first Correct open pays
the ~217 ms import. `app/provenance.py` is unchanged: it lists the packages a run has loaded, which
normally includes `skimage` through the prewarm (CHANGELOG notes it).

### 12.4 Deleted

`mask_tracing_interface.py`, `skeleton_correction_interface.py`, `mask_graphics_view.py`,
`skeleton_correction_graphics_view.py`, `mask_drawing_tools.py`, `canvas_item.py`,
`mask_cursor_utils.py` (dead), `app/handlers/mask_handler.py` (dead), the hidden legacy toggle
buttons in `ui_panels.py`, the F-015 connect/disconnect bookkeeping in `MainWindow`, `ask_unsaved`
in `skeleton_document.py`, `SkeletonOverlayRenderer` (replaced by the layer item).

---

## 13. Compatibility

- **Clean break, one swap commit, no aliases.** New code is built and tested next to the old
  editors (the suite stays green at every commit); one commit switches `MainWindow`, the shell,
  the shortcut registry and `Panel`, deletes the old editors and updates the tests. IntEnum
  aliases were rejected: `{Panel.MASK_TRACING: …, Panel.SKELETON_CORRECTION: …}` would silently
  collapse to one key in `_EDITOR_STAGES` and `_editors()`.
- **MainWindow contract kept:** `has_unsaved_changes()`, `maybe_save_changes()` (False = stay),
  `reset()`, `load_image(path, images_base_folder=None)`, `current_image_path`, `dock` (toast
  offset), `graphics_view.set_opengl_viewport_enabled`, `notice(str, str)`, `dirty_changed(bool)`.
- **Signals:** `mask_saved(str)`, `mask_cleared(str)`, `b_key_status_changed(bool)` are connected
  once at construction (the F-015 dance goes); `navigate_requested(int)` joins them; `skeleton_saved(str)`
  is emitted and still **not** connected (FR-03: corrections are not measured and do not change
  `has_skeleton`). The editor emits the save signals from the session's per-layer save report, so
  a save made from the prompt (tree click, arrows, stage switch, close, open folder, Load skeleton
  file) updates the Library badge and the metrics exactly like Ctrl+S.
- **Attributes:** `mw.correct_editor` replaces `mw.mask_tracing_interface` and
  `mw.skeleton_correction_interface`; `mw.toggle_correct()` replaces both toggles; `_editors()` is
  `{Panel.CORRECT, Panel.GT_TRACING}`; the GL toggle covers display, Correct and GT.
- **Shortcut registry:** scope `CORRECT = "Correct"` replaces `CORRECT_MASK` / `CORRECT_SKELETON`;
  `SCOPES = (GLOBAL, LIBRARY, TRACE_GT, CORRECT)`; `STAGE_KEYS` = Alt+1 … Alt+6.
- **Behaviour changes to record in CHANGELOG "Changed":** one Correct stage; PgDn/PgUp save then
  move; Discard really reverts the mask; undoing back to the saved mask reads clean; a clean
  inactive layer is never rewritten by Ctrl+S; `[ ]` step is proportional for the skeleton eraser;
  rings in layer colours; a tool, layer or visibility key pressed mid-drag ends the drag first;
  smooth zoom. "Scientific output changes": **none** (goldens).

---

## 14. Latency and memory budgets

Measured on this machine by the scouts (offscreen raster unless GL is named; 640 = the real
640x480 set, 2048 / 6000 = upscaled or synthetic stress data). Targets are medians.

| Path | Today | Target | Gate |
|---|---|---|---|
| Open an image in Correct, both layers, fit, 640 | mask 7.6-7.9 ms + skeleton 6.0-8.1 ms (separate stages) + 13-20 ms stage switch | ≤ 10 ms | perf test (smoke < 150 ms; strict opt-in) + bench |
| Same at 2048 | mask 59-71 ms, skeleton 74-82 ms (one layer each) | ≤ 90 ms both layers | bench |
| Image switch in Correct via MainWindow, 640 | 14.5 ms (mask) / 6.8 ms (skeleton) per stage | ≤ 35 ms | opt-in perf test |
| Layer switch | 8.8-20 ms stage hop + re-fit | ≤ 5 ms, 0 loads, 0 file reads | structural + opt-in |
| Stage enter Correct | 10.9-16.3 ms | ≤ 25 ms | opt-in |
| Mask stroke segment (handler) | 0.008 ms | ≤ 0.05 ms | bench |
| Stroke frame, GL, any image size | 0.86 / 2.8 / 19.5-22.2 ms (640 / 2048 / 6000) | ≤ 2 ms | GL bench (windows platform) + tile-key test |
| Photo frame, any zoom | GL 0.42-1.17 ms (blocky), raster 0.07-0.31 ms | GL ≤ 1.5 ms, raster ≤ 1 ms, smooth | benches |
| Library zoom frame, GL | 2.3-6.2 ms | ≤ 1.5 ms | GL bench |
| Skeleton polyline commit, through the skeleton layer (item update included) | 3.4 ms (640 real), 63.6 ms (2048 dense) | ≤ 6 ms / ≤ 40 ms | bench |
| Skeleton eraser-drag frame (coalesced render + paint), 2048 dense | — | ≤ 16 ms | bench |
| Endpoint markers, full repaint, 191 markers | 7.8 ms | ≤ 0.5 ms | bench |
| Skeleton vector frame, real skeleton | — | GL ≤ 1.5 ms, raster ≤ 5 ms | benches |
| Skeleton hover frame at zoom 2, 2048 dense (first frames included) | — (whole-frame vector extraction: 334 ms) | ≤ 16 ms, GL and raster | benches |
| Hover frame, both layers visible, GL (photo + mask tiles + skeleton + ring) | — | ≤ 4 ms (640 real), ≤ 16 ms (2048 dense) | GL bench (lead, integration) |
| Enhance preview after the 80 ms debounce, 640 | ~2.2 ms | ≤ 5 ms | bench |
| `import app.gui.main_window` | 402.5 ms, 204 ms of it `skimage.morphology` | skimage not imported (prewarmed off the GUI thread after the window shows); ≥ 150 ms less | import-budget test + `-X importtime` |
| First Correct open in a fresh process, after the prewarm, 640 | — (a lazy import alone would add ~217 ms here) | ≤ 100 ms | opt-in perf test |
| Undo memory | skeleton 7.3 MiB (640) / 75 MiB (2048) per 25 actions | unchanged in this build (follow-up) | — |

Perf gates reuse the peer plan's rule: 5-run medians, a regression beyond max(3 %, run-to-run
noise) on an unchanged path is a failure. The two "hover frame, both layers" targets are derived
from the component budgets (photo, skeleton and stroke frames) and are checked once both layers
share a canvas. Real-GL benches run one at a time (a lock file in the handoff folder).

---

## 15. Testing

- **Pixel goldens (G2).** The contracts scout captured goldens from the pre-merge editors (6
  cases × 4 op scripts, deterministic across processes). The two fully synthetic cases are
  committed (`tests/data/correct_goldens.json`, inputs regenerated by `synth_inputs()`), replayed
  per layer in wave 1 and through `CorrectEditor` in wave 2 (sequential and interleaved with layer
  switches). The four real-dataset cases stay a pre-merge gate the lead runs from the handoff
  folder at integration. Each records saved-file pixel sha256, shape, PNG colour type, the area /
  length Measure would write, and a per-op sha to name the first diverging op.
- **Qt-free unit tests:** `MaskDocument`, `SkeletonDocument` (both with their pinned stamped-file
  sets), `EditJournal` (order, routing, redo-branch, eviction skip, forget/clear), `CorrectSession`
  with fake layers (save targets, per-layer save reports on every path, prompt answers, failed
  save, revert, dirty transitions, undo during a gesture, gestures finished before anything is
  saved or prompted), `ImagePyramid` (sizes, levels, refresh = rebuild, max-pool connectivity,
  zone-plate aliasing).
- **Offscreen render tests** with thresholds from the scouts: photo magnified (sine step ratio ≤
  1.5/z, equal neighbours ≤ 0.05 at 2-16x), photo at 100 % exact ±1, photo minified (chirp alias
  std ≤ 35 at 0.2-0.4, 1 px stripes mean 127.5 ± 12 at 0.25 and 0.5), edit layers exact when
  magnified (bit-exact at integer zooms; at fractional zooms 1.288, 2.35 and 3.7 every image pixel
  shown with block widths between floor(z) and ceil(z), since 256 px tiles sample with a different
  phase than one canvas), thin features visible at 0.25-0.75 on every row parity, skeleton
  connected at 25 % and free of 8x8 blocks at 800 %, the polyline preview above the skeleton at
  800 %, the Library's 2 px generated lines soft at 4x, tiles re-keyed only where painted, HiDPI
  level choice. All fail on today's editors.
- **Editor tests:** every behaviour of the 63 replaced tests (mask smoke / behaviour, skeleton
  lifecycle / tools / smoke) re-targeted at `CorrectEditor` or a layer; layer switching, reference
  dimming, the key map, one prompt, save targets, save signals on every path, PgDn, re-entry
  (including after our own skeleton save), Esc / F / E / 2 / PgDn in the middle of a stroke, and 60
  mixed actions with skeleton eviction undone and redone exactly.
- **MainWindow / shell tests:** six stages with Alt+1…6, `Panel` values, refused leave keeps the
  Correct stage, one prompt on leave / close / open-folder, signals connected once, mask save
  (Ctrl+S or the prompt's Save after a tree click) updates badge and metrics without extra stats,
  a skeleton save changes neither `has_skeleton` nor the metrics (FR-03), GL toggle reaches every
  canvas, the scikit-image prewarm runs once off the GUI thread.
- **Guards:** `app.gui.__all__` resolves; the canvas-background check discovers view classes
  (transitively) instead of a hand list; no `right_panel` int literals and no legacy panel keys in
  `app/`; the Qt-free modules load no PyQt6; `STAGE_KEYS` matches the stage count; `import
  app.gui.main_window` loads no `skimage`; `app/gui/correct/*.py` ≤ 450 lines.
- **Perf tests:** structural always on (one decode per load, a layer switch does 0 loads and 0
  file reads, strokes never deep-copy the canvas, drag repaints coalesced, a skeleton edit
  extracts no vectors); wall-clock smoke with 8-10x headroom always on; strict budgets behind
  `SPROUTS_PERF_TESTS=1`. The ROI-skeletonize equality test runs about 50 random edits per case
  always on; the 300-edit-per-case and 2,800-edit runs are opt-in (`SPROUTS_PERF_TESTS=1`).
- Baseline: 1650 passed, 8 skipped, 0 failed, 61.1 s. The suite must stay green at every commit
  (the lead commits each wave's packages in dependency order after one full-suite run of the
  combined tree) and below about 75 s.

---

## 16. Docs and memory

README (pipeline and stage list, one Correct section with the key map), `CLAUDE.md` (architecture
map: editors row, Panel note, god nodes after `graphify update .`), `CHANGELOG.md` ([Unreleased]:
Changed, Fixed, Performance; "Scientific output changes": none, with the goldens as evidence; the
unreleased "Alt+1..7" line becomes "Alt+1..6"; a note that provenance records list the packages
loaded by the run, which normally includes scikit-image through the startup prewarm),
`docs/KNOWN_ISSUES.md` (#2 six stages; #5: CQ-10 `mask_handler.py` and CQ-11 closed, skimage at
startup closed; new open items: skeleton save fragmentation Q1, follow-ups of section 20),
`docs/Architecture_2.md` (editor node and tree), `docs/DATA_DICTIONARY.md` (hand-traced masks now
come from the *Correct* stage, not *Correct Mask*). The lead records the decisions in both memory
layers (file memory + mem0) and runs `graphify update .`.

---

## 17. The peer session's modularise plan

`docs/superpowers/plans/2026-09-29-modularise-four-large-files.md` (another session, untracked, not
executed) splits `main_window.py`, `dash_app.py`, `mask_tracing_interface.py` and
`skeleton_correction_interface.py` by pure moves.

| Part | Verdict | When |
|---|---|---|
| Task 0 guards | Reuse the import-budget test and the 5-run +3 % perf gates (this plan adopts both). `tests/test_split_dash_golden.py` and `tests/test_split_import_budget.py` are independent of the editors and may land before the swap. The API-surface freeze (`tests/test_split_api_surface.py`) is captured **after** the swap: before it, it would freeze `MaskTracingInterface`, `SkeletonCorrectionInterface` and the MainWindow editor attributes that the swap deletes. Task 0 Step 1 needs a clean tree and a green suite, which never exist in this folder while W1 or W2 run. If any `tests/test_split_*.py` is present in this folder when package P8b runs, P8b updates its frozen sets deliberately (drops the old editors, adds `correct_editor` / `toggle_correct`). | Dash golden and import budget: outside the W1 / W2 windows or in the peer's own worktree. API surface: after the swap commit. |
| Track A (MainWindow controllers) | Valid, but A4 `LibraryNavigator` (moves `on_image_selected`), A5 `EditorSwitcher` (the `toggle_*`, F-015, `_editors`) and A6 (`_build_body`, panel order) edit exactly what the swap rewrites. After the swap `EditorSwitcher` has two editors and one toggle each, and **may** de-duplicate them. Keep its close-event and thread-affinity tests. | **After** this plan's swap commit. A1-A3 could go before, but running them in parallel with the swap doubles the conflicts in `main_window.py`. |
| Track B (dash_app) | Its files are disjoint, but it must **not** run in this folder during W1 or W2: its work in progress would enter every full-suite gate and its commits would interleave on `feat/unified-correct-editor`. | On its own branch in a separate, explained worktree, or after W2. |
| Track C (mask editor split) | Superseded: C2 (move `RasterEditorBase` to `raster_editor_base.py`) is done here by package P3; C1 is replaced by `EditorChrome`. Keep its GT-override MRO test idea (P3 adopts it). | Drop. |
| Track D (skeleton editor split) | Superseded: the class is deleted; D2's document boundary survives as `SkeletonDocument` (composition, not a mixin). | Drop. |
| Task Z | Superseded by this plan's docs step. | Drop. |

**Order (settled with the user, refined by the plan critic):** this merge first (W0 → W1 → W2 →
integration); Track B in its own worktree at any time, or after W2; Task 0's API-surface freeze and
Track A (A1-A6) after the swap commit, on the smaller post-merge `MainWindow`; Tracks C, D and
Task Z dropped.

---

## 18. Decisions (where the scouts differed or left it open)

| # | Topic | Choice | Why |
|---|---|---|---|
| D1 | Stage name | "Correct" (icon `brush`, Alt+4) | Fits the compact topbar; the layers panel names what is corrected. |
| D2 | Layer keys | 1 = mask, 2 = skeleton, X = swap; tool keys imply the layer; E = eraser of the active layer | Digits mirror Trace GT's digit keys (UX scout); M/K (architecture) were arbitrary; Tab drives focus. |
| D3 | H | H = show/hide both layers (peek at the photo), Shift+H = the inactive layer; editing a hidden active layer shows it | Keeps today's meaning of H (see the root under the annotation); hiding only the layer being edited is rarely wanted. UX scout proposed H = active layer. |
| D4 | Undo | One chronological journal over the per-layer stacks; no auto layer switch; toast + row flash | All scouts favour one timeline; the contracts scout's auto-activate would change tools under the user. |
| D5 | Skeleton history | Keep 25 whole-mask snapshots; the journal skips evicted entries | Patch history (75 MiB → 0.1 MiB at 2048) is independent of the merge; follow-up. |
| D6 | Dirty | Exact "differs from saved" for both layers | Unanimous; removes the spurious mask prompt after undo. |
| D7 | Ctrl+S | Save the dirty layers; with none dirty, the active layer | Keeps each editor's explicit save (accepting a generated skeleton, marking a model mask hand-checked) without ever rewriting a clean inactive layer (contracts: an untouched model mask would flip to MANUAL). Hybrid of architecture (active + dirty) and contracts/UX (dirty only). |
| D8 | Prompt | One Save / Discard / Cancel box naming the dirty layers, today's strings; failed save asks only about the failed layers | Unanimous; no per-layer checkbox dialog. |
| D9 | Discard | Reverts: mask from its in-memory baseline, skeleton by re-reading its start file | Fixes the mask Discard bug; the skeleton re-read keeps today's pixels after a save (Q1). |
| D10 | PgDn / PgUp | Save the dirty layers, then move (Trace GT's contract); other exits prompt | Removes the 10 prompts per 5 images when paging; same rule as Trace GT. Architecture scout had PgDn prompt. |
| D11 | Re-entry | Same image + unchanged files: keep documents, history and view; changed files: reload, keep the view; new image: fit | Fixes the zoom loss; reloading on changed files keeps saved pixels identical to today. |
| D12 | Decode | One cv2 decode with `IMREAD_IGNORE_ORIENTATION` feeding photo, Enhance and pyramid | 1.14 vs 1.31 ms (640), 9.74 vs 13.05 ms (2048); matches Qt geometry. |
| D13 | Photo | Area pyramid + bilinear on every canvas; no bicubic, no deblock | Removes the squares and the moire at unchanged frame cost; bicubic adds a visible refine pop for +2.8 dB. |
| D14 | Mask / GT layers | Exact nearest at device scale ≥ 1, area coverage below 1, 256 px tiles | Shows the saved pixels; thin strokes never vanish; GL stroke cost independent of image size. Architecture had nearest only. |
| D15 | Skeleton layer | Max-pool raster below 1, nearest 1-2, culled vectors from 2 device px, extracted lazily per 256 px tile at paint time (4 ms per paint; edits never extract); MSAA on GL. The Library uses the same item without vectors (bilinear at s ≥ 1) | A line at every zoom, vertices = model pixels, with a bounded cost on dense skeletons (a whole-frame extraction is 334 ms at dense 2048). The Library's input is a 2 px generated file, which vectors would draw as ladders. Architecture had "stay raster". |
| D16 | Zoom controls | Ladder for keys / buttons (keeps 80 % / 125 % around 100 %, integer stops from 2), continuous wheel, presets menu; min zoom 10 % absolute; no animation or NativeGesture now | Keeps the pinned canvas tests and the precise touchpad feel; animation and macOS/Linux pinch are polish for later (Windows touchpads already pinch via Ctrl+wheel). |
| D17 | Module moves | None of the existing Qt-free modules move | Optional layering (architecture) that churns six test files; new Qt-free code sits in `app/gui/correct/`, `app/editing/journal.py`, `app/imaging/`. |
| D18 | Compatibility | Clean break, one swap commit, `Panel` renumbered, legacy panel keys rejected | Contracts scout: aliases collapse dict keys silently; every consumer is in-repo; no index is persisted. |
| D19 | Controls | Rail = mask tools | skeleton tools | pan | enhance; Smooth in the polyline prompt; Load skeleton file in the dock (skeleton active); Layers panel on the right | UX scout's layout, with Load kept where users find it today rather than in a context menu. |
| D20 | Reference layer | Inactive layer at opacity × 0.5; endpoint markers only when the skeleton is active | UX scout; no extra toggle in v1. |
| D21 | Persistence | Opacity, visibility, active layer, last tool per layer: per session only | QSettings persistence would couple tests to user settings; follow-up. |
| D22 | Skeleton save round trip | Bug-compatible in this merge | Hard rule 2 (pixel identity); the fix changes saved skeletons: Q1. |
| D23 | Select tool | Keep today's topology pick | The faster local pick disagrees in 1-3 % of clicks next to junctions; follow-up. |
| D24 | Skeleton latency | ROI skeletonize with the quiet-band check (full-frame fallback, gated by a randomized equality test); endpoint markers `DeviceCoordinateCache`; endpoints only when active; lazy skimage, prewarmed off the GUI thread after the window shows | Provably exact or falls back; 26 → 0.73 ms per commit at dense 2048; 7.8 → 0.14 ms per repaint with 191 markers; ~200 ms off startup without moving the ~217 ms import onto the first Correct open. |
| D25 | Brush size keys | `[ ]` ±12 % of the current tool for both layers; hold B + wheel sizes the mask brush from either layer; B tap selects the mask brush | One rule; the skeleton eraser keeps ±2 px at its default size. |
| D26 | Save toasts | One summary toast from the editor; `MainWindow.on_mask_saved` no longer toasts | One save, one toast. |
| D27 | Trace GT semantics | Unchanged (flag dirty, Discard marks clean, PgDn saves) | GT must keep working; aligning GT is a separate decision. |
| D28 | Registry rows | The lead adds the "Correct" scope rows in wave 0 (additive; committed as `99876e9`); package P8b finalises the registry at the swap | Wave-1 packages need the rows to install shortcuts; old editors need the old scopes until the swap. |
| D29 | Gestures in progress | Every transition (tool, layer, visibility, save, navigate, open, close, revert, reload, stage switch) first ends a drag as a release at the last position would; undo / redo refused mid-drag; Esc on the mask commits; the rest of a finished drag is ignored until the next press; no abort | Keeps the canvas and the plane identical, so no painted pixel can reach a later save without being in the document (plan critic). |
| D30 | Save reports | The session reports every layer save attempt (`on_saved`); the editor turns them into `mask_saved` / `mask_cleared` / `skeleton_saved` on every path | Prompt saves must update the Library badge and the metrics like Ctrl+S, as today. |
| D31 | Library overlay | `vectors=False`: max-pool below 1, bilinear level 0 at s ≥ 1, no halo | The generated file has 2 px lines; users judge generation quality by it and dislike pixel blocks. |
| D32 | Layer undo | One entry of the layer's own stack; False only when empty; never pops draft points; a skeleton undo / redo drops a draft, with a toast when it was hidden | The journal reads False as eviction; today's skeleton undo drops the draft. |
| D33 | File stamps | Mask: every mask candidate path; skeleton: the correction and the resolved generated path; each stamped or absent | Re-entry after our own save must reload the correction (D11, Q1). |
| D34 | Provenance | `app/provenance.py` unchanged; CHANGELOG notes that it lists the packages loaded by the run | The prewarm normally loads skimage; recording versions from package metadata would need an owner for that file. |
| D35 | Skeleton z | Item at 1.5, decorations unchanged | Equal z stacks by insertion order; the preview must paint over the line. |

---

## 19. Owner questions (science or product scope only; nothing here blocks the build)

- **Q1 Skeleton save fragmentation.** Saving re-renders the whole image-resolution raster down to
  341x256 with nearest neighbour and re-thins it: one 3 px erase + Save turned 13 components into
  109, and the dotted result comes back on reopen. This merge keeps it bit-compatible. Fix it
  (edit at the native raster and display scaled, or a connectivity-preserving downscale)? Either
  fix changes saved skeleton pixels and is linked to FR-03.
- **Q2 JPEG deblock.** Add a display-only "Smooth JPEG blocks" toggle (default off, never written
  to disk or used by inference)? It changes what users trace on.
- **Q3 Real images as test data.** Commit 2-3 small dataset images (~150 KB of research data) as
  golden inputs, or keep only the synthetic goldens in the repo (default) with the real ones as a
  pre-merge gate?
- **Q4 EXIF orientation in generation.** Generated masks come from cv2 (EXIF applied) while the
  editors use Qt geometry (EXIF ignored): an EXIF-rotated image would get a "mask size does not
  match" warning. None of the 40 harness images carries the tag. Track it?
- **Q5 Sequencing with the peer session's plan** (section 17): settled 2026-09-29 (the user
  delegated it): merge first; Track B in its own worktree or after W2; Task 0's API-surface freeze
  and Track A after the swap; Tracks C, D and Task Z dropped.

---

## 20. Follow-ups (not in this build)

Patch-based skeleton history (25 → 100 steps, 75 MiB → ~0.1 MiB at 2048); local branch pick for
Select (1.5 s → 0.13 ms on dense 2048, with the semantics decision); windowed endpoints and a
dirty-rect skeleton overlay beyond what the layer item does; background prefetch of the next image
(PgDn 9.6 → 2.0 ms GUI time at 640, 78.7 → 8.7 ms at 2048); zoom animation (120 ms, anchored) and
`QNativeGestureEvent` pinch; a minimum zoom relative to fit; bicubic idle refine; QSettings
persistence of layer opacity and visibility; "image 12 / 40" in the chip; an optional pixel grid at
≥ 800 %; Trace GT as a one-layer client of the Correct workspace; Trace GT Discard revert.
