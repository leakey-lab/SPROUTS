# Wave G: GT Folder and One Editor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Read `00-master.md` first (sections 2, 5, 6, 7), then
> this file's Design section, then your task.

**Goal:** Every human-saved edit (mask, skeleton, class labels) lives under `gt/`, the model's
predictions in `mask/` and `skeletons/` are never overwritten by a save, Measure reads the human
file first and says so in a `Source` column, and one Correct editor with three layers replaces
the separate Trace GT editor and stage.

**Architecture:** `gt/` gains two mirror folders (`gt/mask/`, `gt/skeletons/`) next to the
existing class-label files (`gt/<stem>.png`, `gt/classes.json`); a one-time, idempotent copy
moves legacy hand-corrected files there on project open. The Correct editor's mask and skeleton
layers change only *where* they read and write; a third `ClassesLayer` is built on a
`RasterLayer` base lifted out of `MaskLayer` and a Qt-free `ClassesDocument` lifted out of
`GtTracingInterface`. The Library badge gets three marks whose shape carries the state; Generate
touches predictions only; a Qt-free resolver gives Measure GT-first inputs for both metrics.
Twelve tasks on four lanes; the two science-visible commits (the `Source` column, GT-first
values) are the last two commits of WG-T8.

**Tech Stack:** Python 3.10, PyQt6, numpy, OpenCV, Pillow, scikit-image, pytest, ruff. No new
dependency.

**Spec:** `00-master.md` (owner decisions O1, O2, O7, O9, O13, O18, O19, O21, O23, O24, O26 and
the chat answer "GT design = GT folder") plus the **Design** section below, which is the Wave G
spec. It supersedes hard rule 1 of
`docs/superpowers/specs/2026-09-22-multiclass-labels-design.md` ("GT never starts from model
output") and section 11 of `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`
("Trace GT keeps its stage"); the class-label file formats of the first spec (sections 1, 2, 4,
5) stay in force unchanged. Evidence: `.claude/handoff/issue-audit-2026-10-12/trace-gt.md`
(G1, G2, G4), `length.md` (L1, L5), `mask-calibration.md` (M1, M4, M5), `correct-ux.md` (E1,
E2).

## Global Constraints

See `00-master.md` section 2. Wave-G-specific lines:

- Pixels of a saved mask or skeleton are pinned by `tests/data/correct_goldens.json`
  (`per_op_*_sha256`, `saved.pixels_sha256`, `saved.on_sha256`, `saved.on_count`,
  `saved.colour_type`, `saved.area_mm2_as_measured`). Wave G changes two non-pixel entries only,
  each in the task that causes it: `cases.synth_model_mask_and_skeleton.mask_clear.saved`
  (`{"exists": false}` becomes an empty GT file, O9; WG-T2) and `cases.*.skeleton.start.
  skeleton_source` (`"generated"` becomes `"prediction"`; WG-T3). Every other value stays
  byte-identical.
- Class-label pixels, ticks and the decoded file are pinned by `tests/data/gt_goldens.json`
  (WG-T0) before any GT code changes; WG-T4 and WG-T5 reproduce them through the new layer and
  the page.
- Every `app/gui/correct/*.py` stays at 450 lines or fewer; the budgets per module are in
  Design section 8.3. `editor.py` must end Wave G at **440 lines or fewer** (W4-T1 adds a line).
- Qt-free (no PyQt6, no torch at import): `app/gt/*`, `app/gui/correct/classes_document.py`,
  `app/inference/measure_sources.py`, `app/project_layout.py`. Pinned by subprocess probes.
- Old data is out of scope (owner, 2026-10-25): no migration, no compatibility path, no
  legacy-specific state or text. A GT file is validated generically (readable, the photo's
  size) and an invalid one is never deleted or overwritten by anything but a Save over it.
- `app/inference/measure_inputs.py` is W2-T2's inputs record (committed `a3dc3c7`); the GT
  resolver is `app/inference/measure_sources.py`.
- A layer writes only its own file: the mask layer `gt/mask/<stem>.png`, the skeleton layer
  `gt/skeletons/<stem>.png`, the classes layer `gt/<stem>.png` (+ `gt/classes.json` when
  missing). Pinned by `test_each_layer_writes_only_its_own_file` (WG-T5).
- Vocabulary in every new string: **prediction** (a model output in `mask/` or `skeletons/`),
  **GT** (a file under `gt/`), **class labels** (the `gt/<stem>.png` file; layer row "Classes").
  Never "tracing", "hand-traced", "corrected" in a new string.
- Measure CSVs stay byte-identical until WG-T8 commit 2 (`Source` column), the only Sci commit
  of this wave besides WG-T9's manifest columns.
- O21: no Windows-only call in new code; every new file test builds its fixture with
  `os.path.join` and `tmp_path`; the read-only-folder tests use "a regular file where the
  folder should be", which fails the same way on every OS.

## Design

### 0. Vocabulary and the Wave 1 interfaces this plan assumes

Reconciled on 2026-10-25 with `w1-data-loss.md`, `w2-records-wording.md`, `w3-science.md` and
the commits `19f3761..4850564` (W1-T1 `2d94694`, W1-T2 `7d5ae3e`, W1-T3 `4850564`, W2-T2
`a3dc3c7`, W4-T5 `3ceea64`); W1-T4 and W1-T5 were running (their plan interfaces are used):

| From | Interface this plan builds on |
|---|---|
| W1-T2 (committed) | `MaskRead.not_loaded: str` (`""` / `"size"` / `"unreadable"`), `MaskDocument.unloaded_file() -> bool`, `MaskSaveResult(..., kept: bool = False)`; `SaveOutcome.declined`; `layer.DiskGuardedLayer` with `unloaded_label() -> str`; `app/gui/correct/save_gate.py`: `Prompts` (`ask_unsaved`, `ask_after_failed_save`, `ask_replace_unloaded`), `SaveReport.declined`, `SaveGate(prompts, parent).before_save(layer_id, layer, *, ask)`, `unloaded_label_of(layer)`; status "Mask not shown" |
| W1-T3 (committed) | `app/editing/thinning.py`: `thin_raster(raster_gray, target_size, threshold="otsu")`, `is_generated_as_opened(mask, generated_path, photo_size) -> bool` (no generated file means "equals empty"); `SkeletonSaveResult(ok, path=None, error="", unchanged=False, removed=False)`; `SkeletonDocument._remove_correction`, `outcome(result) -> SaveOutcome` |
| W1-T4 (running) | `DiskGuardedLayer.write_stamps() -> tuple`, `matches_disk() -> bool`; `save_gate.write_stamps_of`, `matches_disk_of`, `OVERWRITE`, `RELOAD`, `Prompts.ask_changed_on_disk(parent, noun, label)`; `SaveGate(prompts, parent, *, reopen, root)`, `.take`, `.forget`; `MaskDocument.matches_disk()`, `SkeletonDocument.write_stamps()` / `matches_disk()`; `editors.follow_generated_outputs(window)` |
| W1-T5 (running) | `app.naming.stem_key(stem) -> str` (`stem.lower()`); `ProjectLayout.scan_image_folder() -> ImageScan` (`images`, `hidden`, `unsupported`), `scan_images()` = its `images`; `key_to_stems(found, stems)`; `ImageManager.last_scan`; the keyed `_scan_ranked` |
| W2-T2 (committed) | `app/inference/measure_inputs.py`: `Fingerprint = (size, mtime_ns, sha256)`, `Stamp = (path, size, mtime_ns)`, `write_inputs_record(csv_path, inputs)`, `load_inputs_record(csv_path) -> InputsRecord | None`, `input_key(path, project_root)`, `scan_stamps(folder, suffix, project_root, *, keep=None) -> dict[str, Stamp]`, `diff_inputs(record, current) -> InputsDiff`, `is_unchanged(record, current)`; `MeasurementIndex.outdated()` compares that record |
| W2-T11 (planned) | `measure_run.split_by_image(inputs, image_stems) -> (kept, without_image_names)`; `MeasureRun(..., without_image: Sequence[str] = ())`; `Freshness.without_image`; "WG-T8 consumes `split_by_image` for GT files too" |
| W3-T2 (planned, after WG-T8) | `app/calibration.py`; a CSV measured with another calibration or length method is flagged; nothing here depends on it |
| W3-T4 (planned, after WG-T2) | `MaskRead.resized_from`: a **prediction** of another size opens stretched (nearest) as the clean baseline; a GT mask of another size stays "not shown". WG-T2's equality rule compares with the prediction plane **as opened**, so a stretched prediction compares at the photo's size |
| W3-T5 (planned, after WG-T0) | 1 px click; no interface used here |

Words: **prediction** = `mask/<stem>.png` (8-bit grey, 640 x 480) or `skeletons/<stem>_fake.png`
(341 x 256); **GT** = any file under `gt/`; **class labels** = `gt/<stem>.png`.

### 1. Storage under `gt/`

```
<root>/gt/<stem>.png                class labels   (unchanged: 8-bit palette PNG, ids, chunk sprouts_gt)
<root>/gt/classes.json              class registry (unchanged)
<root>/gt/mask/<stem>.png           GT mask        (today's editor encoder: RGBA colour type 6,
                                                    (0,0,0,0) / (255,255,255,255), via write_mask_file)
<root>/gt/skeletons/<stem>.png      GT skeleton    (today's correction encoder: 8-bit grey 0/255,
                                                    1 px lines at the photo's size, via imwrite_unicode)
```

**Decision: mirror folders under `gt/`, class labels stay where they are.** `gt/<folder>/` is
the GT of `<folder>/` with the same `<stem>.png` name; the `_fake` and `_skeleton` suffixes are
not carried over (every GT file is `<stem>.png`). `ProjectLayout` builds the paths from the
existing `MASK_DIRNAME` and `SKELETONS_DIRNAME`: no new `config.py` constant.
- Rejected: suffixes in one folder (`gt/<stem>_mask.png`): the labels file has no suffix, so the
  mask of image `X` and the labels of an image named `X_mask` would collide.
- Rejected: moving the labels to `gt/labels/`: lossless but a second migration, a documented
  layout change and churn in `app/gt/store.py`, `export.py`, `export_dataset_dialog.py` for no
  user gain. `count_traced` (`export_dataset_dialog.py:52-74`) lists `gt/` by `<stem>.png`
  names; the two folder entries `mask` and `skeletons` have no `.png` and are not counted.

**Formats.** Unchanged encoders (`app/masks/store.py:193-211`, `app/gui/correct/
skeleton_document.py:280-306`): the goldens' pixel hashes, colour types and
`area_mm2_as_measured` hold. The PNG colour-type rule of `app/inference/mask_origin.py`
decides nothing any more: the folder says what a file is (section 2).

**Case rules (O7).** GT files are written under the image's real stem (the Library's winner
stem); every scan keys by `stem_key` as W1-T5 keys `scan_masks` (`_scan_ranked` with the
`(".png",)` extension list). On a case-sensitive file system two GT files that differ only in
case resolve like two masks do: one winner, the other reported.

**`ProjectLayout` (WG-T1), exact names:**

```python
@dataclass(frozen=True)
class GtStems:
    mask: frozenset[str]; skeleton: frozenset[str]; labels: frozenset[str]   # stems (stem_key)

class ProjectLayout:
    gt_mask_dir: str                     # property, <root>/gt/mask
    gt_skeletons_dir: str                # property, <root>/gt/skeletons
    def gt_mask_path(self, stem: str) -> str          # gt/mask/<stem>.png  (stem, name or image path)
    def gt_skeleton_path(self, stem: str) -> str      # gt/skeletons/<stem>.png
    def gt_path(self, stem: str) -> str               # unchanged: gt/<stem>.png (class labels)
    def find_gt_mask(self, stem: str) -> str | None
    def find_gt_skeleton(self, stem: str) -> str | None
    def find_gt_labels(self, stem: str) -> str | None
    def scan_gt_masks(self) -> dict[str, str]         # _scan_ranked(gt_mask_dir, (".png",))
    def scan_gt_skeletons(self) -> dict[str, str]     # _scan_ranked(gt_skeletons_dir, (".png",))
    def scan_gt_labels(self) -> dict[str, str]        # _scan_ranked(gt_dir, (".png",)): files only
    def scan_gt_stems(self) -> GtStems                # the three scans, one scandir each
    output_dirs                                       # gains gt_mask_dir and gt_skeletons_dir (9 entries; 8 once WG-T3 drops corrections_dir)
```

`app/gt/files.py` (WG-T1, Qt-free): the per-image view and the removal.

```python
SOURCE_GT = "gt"; SOURCE_PREDICTION = "prediction"
@dataclass(frozen=True)
class GtFiles:
    mask: str | None; skeleton: str | None; labels: str | None    # existing files only
def gt_files(layout: ProjectLayout, stem: str) -> GtFiles
def remove_gt(layout: ProjectLayout, stem: str) -> tuple[str, ...]   # paths removed (0-3); never classes.json, mask/, skeletons/
def png_size(path: str) -> tuple[int, int] | None                     # IHDR only (33 bytes read)
def image_size(path: str) -> tuple[int, int] | None                   # png_size, else PIL lazy open (header)
```

### 2. Old data: no migration (owner, 2026-10-25)

"Don't worry about previous runs; with this app it will never be used on old data … I will
regenerate everything there." So Wave G builds **no migration and no compatibility path**:
- `skeleton_corrections/` is never read again. WG-T3 removes `corrections_dir`,
  `corrected_skeleton_path`, `find_corrected_skeleton`, `scan_corrected_skeletons`,
  `scan_corrected_stems` and `CORRECTED_SKELETON_SUFFIX` from `ProjectLayout` (the dead
  `config.SKELETON_CORRECTIONS_DIRNAME` and `export._PROTECTED_DIRS` entry go with W5-T5).
- Every file in `mask/` is a prediction, whatever its PNG colour type; the colour-type rule of
  `app/inference/mask_origin.py` is read by nothing after WG-T7 (W5-T5 deletes the module).
- No "older save" state, no 341 x 256 flag in the Library or in Correct (O13's flag UX is not
  built; the owner's answer supersedes it). What remains is **generic validation of a GT
  file**, cheap and recorded:
  - Correct, mask layer: a GT mask that is unreadable or not the photo's size is "not shown"
    under W1-T2's rule (`MaskRead.not_loaded`, status "Mask not shown", never removed or
    replaced silently); the prediction is not opened in its place (the GT file is the one the
    layer would write).
  - Correct, skeleton layer: a GT skeleton of another size opens as today's `thin_raster`
    opens any raster (resampled to the photo); a Save then writes it at the photo's size.
  - Measure: a GT file that is unreadable (`unreadable`), a GT skeleton that is not the
    photo's size (`size`) or that equals the prediction as opened (`unedited`) is not used; the
    prediction is measured and the reason is recorded in the provenance sidecar and shown in
    the metrics bar (section 7).
- The Generate prompt never mentions hand-corrected masks (section 6).

### 3. The one editor

#### 3.1 Three layers

| Layer id | Class / module | noun (prompts) | Row label | colour (`app/design/tokens.py`) | z | default opacity | writes |
|---|---|---|---|---|---|---|---|
| `MASK = "mask"` | `MaskLayer` (`mask_layer.py`, on `RasterLayer`) | `"mask"` | Mask | `tokens.MASK` `#5fd6a0` | 1.0 | 0.60 | `gt/mask/<stem>.png` |
| `SKELETON = "skeleton"` | `SkeletonLayer` (unchanged structure) | `"skeleton"` | Skeleton | `tokens.SKEL` `#f0a868` | 1.5 | 0.85 | `gt/skeletons/<stem>.png` |
| `CLASSES = "classes"` | `ClassesLayer` (`classes_layer.py`, on `RasterLayer`) over `ClassesDocument` (`classes_document.py`, Qt-free) | `"class labels"` | Classes | `tokens.ACCENT` `#c39af6` | 1.2 | 0.60 | `gt/<stem>.png` (+ `gt/classes.json` when missing) |

`LAYER_IDS = (MASK, SKELETON, CLASSES)` is the panel order and the session's save order (mask
first); z comes from each layer's `Z`, so the 1 px skeleton line stays above both rasters. The
classes layer paints in the **active class colour** (`ClassesLayer.brush_color` property) while
its rail group, ring, swatch and row use the constant purple: the ring and the rail take their
colour at build time (`FootprintRing(scene, self.color, …)`, `IconButton(on_color=layer.color)`),
and the paint itself shows which class is active. The audit's "`color` is the active class
colour" is therefore not followed; the class panel names the class.

The master's `GtDocument` / `GtLayer` are named `ClassesDocument` / `ClassesLayer` here: every
layer now writes GT, so "GT layer" would name all three.

`RasterLayer` (`app/gui/correct/raster_layer.py`, WG-T4) is the generic part of today's
`MaskLayer` (`mask_layer.py` gestures 225-238, tools and input 114-120 + 244-320, ring 401,
opacity and view 325-398, history replay 191-220 + 381, `_on_patch` 374, `clear` 181):

```python
class RasterLayer:
    layer_id: str; noun: str; color: str; Z: float; DEFAULT_SIZE: int; SIZE_RANGE: tuple[int, int]
    DEFAULT_OPACITY: float; CLEARED_NOTICE: str; SIZE_SPEC: SizeSpec; tools = tuple(cls.spec for cls in TOOL_CLASSES)
    def __init__(self, host: LayerHost, scene: QGraphicsScene, *, document: object,
                 lut: Callable[[], np.ndarray], to_plane: Callable[[np.ndarray, Rect], np.ndarray],
                 fill_source: Callable[[np.ndarray], tuple[np.ndarray, int]]) -> None
    brush_color: QColor            # attribute (mask) or property (classes); MaskToolHost
    canvas: PaintCanvas; item: RasterLayerItem; ring: FootprintRing
    def replace_plane(self, plane: np.ndarray) -> bool   # one undo entry: document.history.commit(full rect, plane); canvas.rebuild(); _on_patch
    # + every EditLayer member that is not document-specific (listed above)
```

`document` must expose `history: PatchHistory | None`, `is_open()`, `revision`, `touch()`.
Subclasses keep `open / close / is_open / file_stamps / write_stamps / is_dirty / save /
revert / status / read_ahead`. `mask_tools.py` is unchanged: `MaskToolHost` is satisfied by
`RasterLayer`.

#### 3.2 Side panel

`side_panel` of the `EditorChrome` becomes one `SidePanels` widget
(`app/gui/correct/side_panels.py`, WG-T5): the `LayerPanel` (three rows) above the `ClassPanel`
(`app/gui/class_panel.py`, unchanged), the class panel shown only while the classes layer is
active (`SidePanels.set_active_layer(layer_id)`). It implements `preferred_height(host_height)`
and `fit_height(height)` by summing its visible children. `CorrectEditor.layer_panel` and
`CorrectEditor.class_panel` stay attributes: `window/body.py:40` (`_EDITOR_SIDE_CHROME =
("layer_panel", "class_panel")`, pinned by `test_shell_window_split_surface.py:176`) keeps the
toasts clear of both. Tab order: layer rows and eyes, then the gear and the "checked" boxes
(`EditorChrome.order_tab_focus`, side panel last).
- Rejected: the class panel always visible: 4-5 extra rows for users who never trace classes.

#### 3.3 Keys (`app/gui/shortcuts.py` Correct rows, `app/gui/correct/keys.py`)

Plain `1` / `2` switch layers today (`shortcuts.py:166-167`), and the class panel's keys are
`1`-`9`, `0`; one key keeps one meaning, so the layer keys move to letters:

| Key | action | Text (registry row) |
|---|---|---|
| `M` | `mask_layer` | Edit the mask (root area) |
| `L` | `skeleton_layer` | Edit the skeleton (root length) |
| `G` | `classes_layer` | Edit the class labels (GT for training) |
| `X` | `swap_layer` | Switch to the previous layer |
| `1`-`9`, `0` | `select_class` (display "1–9, 0") | Pick the n-th class; 0 = Ignore (class labels layer only) |
| `H` | `toggle_layers` | Show or hide every layer (see the photo) |
| `Shift+H` | `toggle_reference` | Show or hide the other layers |

M = mask (area), L = length (skeleton), G = GT classes: the O14 vocabulary. `select_class`
binds ten slots (`install_shortcuts` takes a list of slots per action, as `go_to_stage` does);
`CorrectEditor.select_class(n)` does nothing unless the classes layer is active, so no shortcut
is toggled. `find_conflicts()` stays empty: Trace GT's `select_class` rows live in a scope that
only overlaps Global, and WG-T11 removes that scope. B / F (the raster tools) switch to the mask
only from the skeleton layer; on the classes layer they stay there (the B tap and B + wheel in
`tool_router.py:125-128, 158` request the brush of the active layer when it has one, else the
mask's). V / P / C still switch to the skeleton. `X` remembers the previously active layer id.
- Rejected: `Shift+1/2/3` for layers: `QShortcut("Shift+1")` does not fire on a US layout (the
  event key is `!`); platform-dependent, against O21.
- Rejected: digits for layers with a different meaning on the classes layer: a key with two
  meanings.

#### 3.4 Per-layer Save (O2), empty GT (O9), Load prediction, Remove GT (O24)

`CorrectSession.save(active_id)` saves `dirty_layers()` only. With none dirty it writes
nothing, returns `SaveReport()` and notifies `("Nothing unsaved", "info")`; the `active_id`
parameter stays for the callers and tests but no longer selects a layer. W4-T1 then disables the
button in that state and skips its conditional step.

**Mask layer (`MaskDocument`, WG-T2):**
- `read` / `open`: `gt/mask/<stem>.png` if it exists (`source = "gt"`), else the prediction
  candidates `mask_candidates` (`source = "prediction"`), else empty (`source = None`). The
  prediction plane as opened (W3-T4 stretches a prediction of another size) is kept as
  `prediction_plane` for the comparison below. W1-T2's "not loaded" rule applies to the file
  that was chosen; `unloaded_label()` names `gt/mask/<stem>.png` only when that GT file exists
  and was not loaded (a save never replaces the prediction, so an unshown prediction gives `""`).
- `save()` of a dirty plane: when a prediction exists and `plane == prediction_plane`, the GT
  file is removed (`MaskSaveResult(ok=True, removed=True)`, message
  `"Mask equals the prediction: removed gt/mask/{name}"`, info; `mask_cleared` is emitted);
  otherwise `write_mask_file(gt_mask_path, plane)` whatever the pixel count: an empty plane
  over a non-empty prediction is written (O9; message `"Empty mask saved as GT for {name}:
  root area 0"`, info; `mask_saved`). `mask/` is never written or removed.
- `MaskDocument.prediction() -> np.ndarray | None` gives the plane for "Load prediction".
- Status labels: `LayerStatus("none", "Prediction", "mask/<stem>.png — the model's output.
  Save after an edit writes gt/mask/<stem>.png")`, `LayerStatus("clean", "GT",
  "gt/mask/<stem>.png")`, `LayerStatus("none", "No mask")`, `LayerStatus("dirty")`,
  `LayerStatus("error", "Not saved", …)`, W1-T2's `("error", "Mask not shown", …)`.
- `file_stamps()` = (GT mask, then the prediction candidates); `write_stamps()` = (GT mask,).

**Skeleton layer (`SkeletonFiles`, `SkeletonDocument`, WG-T3):** `correction_path` becomes
`gt_path` = `layout.gt_skeleton_path(stem)`; `start_file()` returns `(gt_path, GT)` or
`(generated, PREDICTION)` with `GT = "gt"`, `PREDICTION = "prediction"` replacing `CORRECTED` /
`GENERATED` (`app/gui/skeleton_document.py:21-22`); W1-T3's save rule and W1-T4's
`write_stamps` / `matches_disk` apply to the GT path (a skeleton equal to the prediction as
opened removes the GT file: `removed=True`). A GT skeleton of another size opens as any raster
does (`thin_raster` to the photo's size) and saves at the photo's size; no flag. Status labels:
`("none", "Prediction", "Not corrected yet")` (replaces "Generated"), `("clean", "GT", gt_path)`,
`("none", "Loaded file")`, `("none", "No skeleton")`.

**Load prediction (both layers; dock button `prediction_button`, icon `cpu`, tooltip "Load the
prediction into the {noun} (undoable)"):** mask: `MaskLayer.load_prediction() -> bool` =
`replace_plane(document.prediction())`; skeleton: `SkeletonLayer.load_file(files.generated_path())`
(exists). Disabled on the classes layer and when no prediction file exists. A later Save then
applies the equality rule above and removes the GT file.

**Classes layer:** `save()` always writes the file (an empty file is "checked, nothing
present"); nothing to compare with. Dirty = ids or ticks differ from the saved ones, or a lossy
remap is pending.

**Remove GT… (top-bar Correct page, `gt_remove_button`, icon `trash`):** enabled only while an
image is open and nothing is unsaved (tooltip otherwise "Save or discard your edits first").
Confirm box: title "Remove GT", text `"Remove the GT of {name}?"`, informative text
`"Deletes gt/mask/{stem}.png, gt/skeletons/{stem}.png and gt/{stem}.png where they exist. The
predictions in mask/ and skeletons/ are kept. This cannot be undone."`, buttons "Remove GT"
(destructive) / "Cancel" (default and Esc). Then `remove_gt(layout, stem)`,
`paging.forget(path)`, `load_image(path, base)` (the stamps changed, so every layer reopens from
the predictions / empty), `gt_removed.emit(image_path)`, toast `"Removed GT · {name}"` (success,
key `"editor-saved"`). It sits in the top bar with the other GT actions because it is rare and
destructive; the dock holds the per-stroke actions (E1).

#### 3.5 Undo, read-ahead, prompts, PgDn, registry errors, prefs, chip

- One timeline: the classes layer records in the `EditJournal` like the mask (`layer_edited`
  per committed patch; `drop_redo` on another layer's action).
- Read-ahead: `ClassesLayer.read_ahead` = `partial(ClassesDocument.read, image_path)`; the
  worker reads `gt/classes.json` and `gt/<stem>.png` and aligns; the payload (`ClassesRead`,
  with `nbytes`) is adopted only while both stamps match. `read_back`: the document in memory
  when clean, as the mask does.
- Prompts: nouns joined by `editor_prompts.join_nouns`: "Unsaved mask and class labels",
  "Save changes to the mask and class labels for name.jpg?", "The class labels for name.jpg could
  not be saved."
- `PgDn` / `PgUp`: `CorrectEditor.navigate` saves the dirty layers silently, except when a dirty
  layer answers `save_needs_prompt()` true (optional protocol `GuardedSaveLayer`, `layer.py`):
  then the normal prompt (`session.release()`), and the page stays when it is refused. The
  classes layer answers true after a lossy remap (`align_to_registry` returned warnings) until
  saved. A pure renumber (changed without warnings) saves silently, as today.
- Invalid `gt/classes.json` (`RegistryError`): the classes layer opens with the default registry,
  `ClassesDocument.registry_error` set, status `("error", "Read-only", "gt/classes.json is
  invalid: {err}")`; `press`, `clear`, `set_ticked`, `save` and `edit_classes` are refused with one
  danger notice per image: `"Editing is off: gt/classes.json is invalid ({err}). Fix the file,
  then reopen the image."`; `save()` then returns `SaveOutcome(ok=False, message="Not saved:
  gt/classes.json is invalid ({err})", kind="danger")`. The mask and skeleton layers are
  unaffected. The registry is re-read on the next open of the image (its stamp is part of
  `file_stamps()`).
- Opacity / visibility: `LayerPrefs` iterates the layers, so the classes layer gets
  `correct/classes/opacity` and `correct/classes/visible` with no change to `layer_prefs.py`.
- Chip: `chip_content` unchanged; hints read `"Class labels · Brush · drag to paint"`. Position
  "Image 12 / 40" and the worst-layer rule stay.
- Class panel wiring (`CorrectEditor` ↔ `ClassesLayer`): `active_changed(key)` →
  `layer.set_active_class(key)`; `reviewed_changed(key, on)` → `layer.set_ticked(key, on)`;
  `edit_requested` → `actions.edit_classes()`; the layer's `on_classes_changed` callback →
  `class_panel.set_registry / set_active / set_reviewed`. Per-class brush size is kept by the
  document (`brush_size(key)` / `set_brush_size(key, px)`).

#### 3.6 Entry points after the Trace GT stage is gone

Top-bar Correct action page (`ShellTopbar.populate`, `shell_chrome.py:371-379`):
`(mw.correct_editor.clear_button, mw.gt_classes_button, mw.gt_export_button, mw.gt_remove_button)`
— `gt_classes_button` → `mw.correct_editor.edit_classes`, `gt_export_button` →
`mw.export_gt_dataset` (which now calls `self.correct_editor.maybe_save_changes()`),
`gt_remove_button` → `mw.correct_editor.remove_gt`. W4-T1 removes `clear_button` later; the
attribute names `gt_classes_button` / `gt_export_button` are kept (pinned by
`test_shell_window_split_surface.py:154-155`). The page's file actions live in
`app/gui/correct/actions.py` (`PageActions`: `load_skeleton_file`, `load_prediction`,
`edit_classes`, `remove_gt`), which also takes `load_skeleton_file` out of `editor.py`
(`editor.py:357-371`, `_SKELETON_FILES`) so the page stays under 440 lines.

#### 3.7 The guard: one layer, one file

Each document receives its paths from `ProjectLayout` only (`gt_mask_path`,
`gt_skeleton_path`, `gt_path`) and imports nothing from the other documents
(`classes_document.py` imports `app.gt`, never `app.masks`; `mask_document.py` never `app.gt`).
`test_each_layer_writes_only_its_own_file` (WG-T5) edits and saves each layer in turn on one
image and asserts the set of files whose stamp changed under `<root>` is exactly that layer's.

### 4. Stage removal (WG-T11)

- `shell_chrome._STAGES` loses the `"Trace GT"` entry: Library · Generate · Correct · Measure ·
  Visualize; `shortcuts.STAGE_KEYS = ("Alt+1", …, "Alt+5")`; the Global `go_to_stage` row says
  "Go to stage 1–5 in ribbon order (Library … Visualize)", display "Alt+1 … Alt+5"
  (`install_shortcuts` raises on a slot-count mismatch, so the two change together).
  Correct's tooltip becomes
  `"Fix the model's mask (area) and skeleton (length), and paint class labels (GT for training), on one canvas."`;
  its icon stays `brush`.
- `_EDITOR_STAGES = {Panel.CORRECT: "Correct"}`; the `if key == "Trace GT"` branch
  (`shell_chrome.py:320-322`) goes; the `"Trace GT"` action page goes.
- `panels.Panel`: `DISPLAY = 0, CORRECT = 1, VISUALIZATION = 2`; dashboards are appended after
  index 2. Every `Panel.GT_TRACING` use goes (`shell_chrome.py:135, 321`,
  `window/editors.py:39, 137`, `window/body.py:180`); `tests/test_right_panel_contract.py:164`
  keeps its line with `Panel.VISUALIZATION` in place of the member.
- `shortcuts.py`: `TRACE_GT` and its 17 rows go; `SCOPES = (GLOBAL, LIBRARY, CORRECT)`;
  `scope_for_stage("Trace GT")` is `None`; the F1 sheet shows three sections.
- `MainWindow`: no `GtTracingInterface` (`main_window.py:38, 158-160, 183, 370`);
  `toggle_gt_tracing` and `window/editors.toggle_gt_tracing` go; `_editors()` is
  `{Panel.CORRECT: correct_editor}`; `window/navigator.py:175-177` branch goes;
  `window/body.py:180` goes. `export_gt_dataset` stays (on the Correct editor since WG-T5).
- Deleted: `app/gui/raster_editor_base.py` (876), `app/gui/gt_tracing_interface.py` (666);
  `tests/test_import_smoke.py:80, :146` drop them. `class_panel.py`,
  `class_editor_dialog.py`, `export_dataset_dialog.py`, `app/gt/*` stay.
- The welcome line derives from `STAGES` (`welcome_screen.py:59-63`); nothing to change.

**The 133 editor tests (12 files), per file:**

| File | Tests | Verdict | Reason |
|---|---|---|---|
| `test_gt_tracing_interface.py` | 27 | port → `tests/test_correct_classes_layer.py` (WG-T4) and `tests/test_correct_classes_editor.py` (WG-T5) | class semantics (A over B, fill limited to the class, ids + ticks on save, empty save, locked ticks, keys, PgDn, per-class brush, retired ids survive, never reads `mask/`) |
| `test_gt_unsaved_state.py` | 16 | port → `test_correct_classes_layer.py` (WG-T4) | dirty from ticks, pixels, remap; discard |
| `test_gt_editor_discard.py` | 7 | port 4 → `test_correct_classes_editor.py`; delete 3 | Discard / reload; the three that pin `RasterEditorBase` plumbing are covered by `test_correct_editor_prompt_flow.py` |
| `test_gt_editor_open.py` | 6 | port 4; delete 2 | open / stamps / unreadable / size mismatch; two duplicate Correct's re-selection tests |
| `test_gt_editor_ux.py` | 16 | rewrite → `test_correct_classes_editor.py` | chip words, empty hint, notices, panel changed for the merged page |
| `test_gt_tab_order.py` | 9 | rewrite as 2 tests in `tests/test_correct_tab_order.py` | the side panel order is new |
| `test_gt_class_panel_keyboard.py` | 8 | port → `test_correct_classes_editor.py` | panel never takes focus; digits after a panel click |
| `test_gt_editor_save_order.py` | 2 | port → `tests/test_correct_classes_document.py` (WG-T4) | registry first |
| `test_gt_editor_speed_paths.py` | 8 | port → `test_correct_classes_document.py` (WG-T4) | `ids_from_argb` fallback, counts from patches |
| `test_gt_editor_store_equivalence.py` | 4 | keep unchanged | core only (no editor despite the name) |
| `test_gt_editor_refocus.py`, `test_gt_editor_teardown.py` | 2 + 3 | delete | the page is `CorrectEditor`, whose refocus and teardown tests exist (`test_correct_editor_teardown.py`, `test_shell_keys_after_tree_focus.py`) |
| `test_raster_editor_behaviour.py` | 29 | delete | pins the deleted base; its behaviours are Correct's (`test_correct_session.py`, `test_correct_editor_prompt_flow.py`, `test_correct_mask_document.py`) |

Edited, not ported (stage count, panels, names): `test_correct_main_window.py` (`:72, :274-286,
:292-298, :378, :471, :573, :692, :715-728, :763`), `test_shell_chrome_topbar.py:25-32`,
`test_shell_chrome_topbar_editor_refusal.py:27-33, :79, :93-95`, `test_shell_chrome_smoke.py`
(the 12 GT-editor tests go; `:130-145`, `:204-212`, `:233`, `:432-436`, `:487`),
`test_integration_main_window_wiring.py:160`, `test_integrator_cross_module.py:68-108`,
`test_shell_info_shortcuts.py:78-79, :113, :143-146, :203-211`,
`test_foundation_runtime_panels_feedback.py:29-52`, `test_viz_embed_manager.py:385, :392`,
`test_window_library_focus.py:234`, `test_shell_welcome_polish.py:31`,
`test_shell_window_split_surface.py:95, :135, :154-155, :237, :408`,
`test_shell_keys_after_tree_focus.py:224, :361, :381`, `test_shell_stage_return_display.py:87`,
`test_tree_type_ahead.py:119, :160`, `test_shell_toast_side_panels.py:151`,
`test_window_feedback.py:245`, `test_warmup_main_window.py:173`, `test_warmup_stage_dwell.py:147`,
`test_viz_embed_manager.py:658`, `test_library_main_window.py:285`, `test_metrics_bar_smoke.py:71`,
`test_json_nesting_robustness.py`, `test_fair_gt_registry_gui.py`, `test_canvas_zoom_quality.py`,
`test_stale_temp_sweep.py:458, :464`, `test_import_smoke.py`.

### 5. Library badge (WG-T6)

Three marks in one `32 x 12` pixmap (`_BADGE = QSize(32, 12)`, boxes at x = 1.5, 11.5, 21.5,
9 x 9 each; was 22 x 12 with two). Shape carries the state, colour says the kind:

| Mark | kind colour | `none` | `prediction` | `gt` / traced |
|---|---|---|---|---|
| 1 mask | `tokens.MASK` | ring (`TEXT_FAINT`, 7 px) | filled circle | filled diamond (the polygon of `library_index.py:197-210`) |
| 2 skeleton | `tokens.SKEL` | ring | filled circle | filled diamond |
| 3 class labels | `tokens.ACCENT` | ring | — | filled diamond |

The owner confirmed (2026-10-25): mark 3 = "class labels exist"; mask GT and skeleton GT are
shown on their own marks (circle = prediction, diamond = GT), where the diamond already means
"a human made this" for the skeleton today.
- Rejected: mark 3 = "any GT exists": the mask corrector could not see which kind.

Signatures (`app/gui/library_index.py`):
```python
def _badge_pixmap(mask: str, skeleton: str, labels: bool, dpr: float = 2.0) -> QPixmap   # mask/skeleton in ("none","prediction","gt")
def status_text(mask: str, skeleton: str, labels: bool) -> str
class LibraryIndex:
    def __init__(self, tree, *, path_of=None, mask_state=None, skeleton_state=None, has_labels=None)
    def status_of(self, name: str) -> tuple[str, str, bool]
LibraryItemDelegate.status_icon(self, mask: str, skeleton: str, labels: bool) -> QIcon  # cache key = the triple
```
`status_text` words, joined by `" · "`: `"Mask: GT"` / `"Mask: prediction"` / `"No mask"`;
`"Skeleton: GT"` / `"Skeleton: prediction"` / `"No skeleton"`; `"Classes: traced"` /
`"No classes"`. Example: `"Mask: GT · Skeleton: prediction · No classes"`.

State source (`app/gui/image_manager.py`, filled on the loader thread by `scan_gt_stems()`
(WG-T1); updated by `set_gt` on every save, removal and rescan):
```python
ImageManager.gt_mask_stems: set[str]; gt_skeleton_stems: set[str]; gt_label_stems: set[str]
def has_gt_mask(self, name) -> bool; def has_gt_skeleton(self, name) -> bool; def has_gt_labels(self, name) -> bool
def set_gt(self, name: str, kind: str, present: bool) -> None        # kind in ("mask", "skeleton", "labels")
def mask_state(self, name) -> str; def skeleton_state(self, name) -> str   # "gt" | "prediction" | "none"
def gt_skeleton_path_for(self, name) -> str | None                     # WG-T3; replaces corrected_skeleton_path
def refresh_outputs(self) -> None                                      # rescans masks, skeleton_stems AND the three GT sets
```
`corrected_stems`, `has_correction`, `set_correction`, `corrected_skeleton_path` are removed in
WG-T3 (their callers: `window/editors.sync_open_correction`, `display_controller.
_current_skeleton_path`, `file_tree_manager.py`, `window/metrics.py:89`).

### 6. Generate (WG-T7)

- `ask_overwrite_policy(parent, existing_count: int, total: int) -> OverwritePolicy | None`:
  title `"Predictions already exist"`, text `f"{existing_count:,} of {total:,} images already
  have a predicted mask."`, informative `"Keep them and predict only the missing ones, or replace
  every prediction with a new one. Hand-corrected masks live in gt/ and are never touched."`,
  buttons `"Keep predictions (predict missing only)"` (AcceptRole, default) / `"Replace all
  predictions"` (DestructiveRole) / Cancel. `OverwritePolicy` values (`keep_existing`,
  `overwrite_all`) and `skip_stems` are unchanged (the run manifest records them,
  `tests/test_generation_inference.py:134-141`). `ExistingMasks.manual` is read by nothing after
  this task (`scan_existing_masks(...).count` is all the prompt needs; W5-T5 may delete the
  classifier).
- Generate never reads or writes `gt/` (`tests/test_generate_predictions_only.py`); the skeleton
  run rewrites every `*_fake.png` as today (corrections are safe in `gt/skeletons/`).
- Docs: `docs/DATA_DICTIONARY.md:132-141` (the prompt paragraph) and `README.md:137` follow.

### 7. Measure (WG-T8, Sci)

**Resolver `app/inference/measure_sources.py` (Qt-free; W2-T2 owns `measure_inputs.py`):**
```python
GT = "gt"; PREDICTION = "prediction"
SIZE = "size"; UNEDITED = "unedited"; UNREADABLE = "unreadable"
@dataclass(frozen=True)
class MeasureSource:
    stem: str; path: str; source: str; gt_path: str | None = None; reason: str = ""   # reason set only when a GT file was not used
@dataclass(frozen=True)
class GtReport:
    folder: str; files: dict[str, str]; skipped: tuple[tuple[str, str], ...]        # stem -> sha256 of the GT files used; (stem, reason)
    @property
    def n_used(self) -> int: ...
def resolve_area_sources(layout: ProjectLayout, predictions: Mapping[str, str], gt_files: Mapping[str, str]) -> tuple[dict[str, MeasureSource], GtReport]
def resolve_length_sources(layout: ProjectLayout, images: Mapping[str, str], predictions: Mapping[str, str], gt_files: Mapping[str, str]) -> tuple[dict[str, MeasureSource], GtReport]
```
The handler scans `predictions` (today's scan of the measured folder) and `gt_files`
(`scan_gt_masks()` / `scan_gt_skeletons()`), passes both through W2-T11's
`split_by_image(…, image_stems)` (GT files whose image is gone are skipped and recorded like
predictions), then resolves: stems = predictions ∪ GT files, keyed by `stem_key`; the measured
path is the GT file when valid, else the prediction; a GT-only stem gets a row. `images`
(stem → photo) serves the size check.

**Validity (generic, cheap).** Area: the GT mask decodes (`imread_unicode` grayscale not
None), else `unreadable`. Length: decodes; `png_size(gt) == image_size(photo)`, else `size`;
not `is_generated_as_opened(gt_gray, prediction_path, photo_size)`, else `unedited`. The
`unedited` rule exists for skeletons because the editor path measures a median 0.8 % longer
than the prediction path on the same lines (L1); a mask equal to its prediction measures the
same pixels, so a GT mask is used whatever its pixels and `Source` stays stable across a
Generate run.

**CSV.** `LENGTH_CSV_HEADERS` and `AREA_CSV_HEADERS` gain a last column `"Source"` with the
values `gt` / `prediction` (`[5]` and `[:6]` keep their meaning; `Error` stays at index 6).
`MeasureRun(spec, images, output_dir, *, project_root=None, without_image=(), sources: Mapping[str, str] | None = None, gt_report: GtReport | None = None)`
(W2-T11's keyword kept) stamps `row["Source"] = sources.get(row["Image"], PREDICTION)` after
`compute`; a missing `sources` gives `prediction` everywhere (the legacy shims keep working).
The CSV lands where it does today (`skeletons/root_lengths.csv`, `mask/root_areas.csv`); a
project with GT files and no prediction folder measures into `skeletons/` / `mask/` created
for it.

**Records.** W2-T2's `<csv>.inputs.json` receives the resolved path per stem (`input_key(path,
project_root)` → fingerprint), so its content comparison follows the measured file.
Provenance (`<csv>.provenance.json`): `parameters.input` =
`"gt/skeletons/<stem>.png where valid, else skeletons/<stem>_fake.png"` /
`"gt/mask/<stem>.png where readable, else mask/<stem>.png"`; `parameters.source_rule = "gt-first"`;
`inputs.gt = {"folder": "gt/skeletons", "n_used": 2, "files": {stem: sha256}, "skipped": [{"stem", "reason"}]}`;
`outputs.n_gt`, `outputs.n_prediction`; W2-T11's `inputs.without_image` counts GT files too.

**Freshness (`MeasurementIndex.outdated(stem) -> {"length": bool, "area": bool}`)**, per
metric, with the row's `Source` (absent column = `prediction`) and `ProjectLayout(project_root)`:
`current` for the stem is the stamp of the file the row names (the GT path when `gt`, else the
prediction in the CSV's folder) plus, for a `prediction` row, the GT file when one exists;
the metric is outdated when W2-T2's `diff_inputs(record, current)` reports anything for that
stem (a changed or missing measured file, or a GT file that is not in the record). A
regenerated prediction under a `gt` row does not outdate it. `Measurement` (NamedTuple) gains
`length_source: str | None = None`, `area_source: str | None = None` (defaults keep
`Measurement(18.77, 16.41, None)` equal). `MeasurementIndex.gt_skip_reason(stem) -> dict[str, str]`
reads `inputs.gt.skipped` of the sidecars. A saved mask never outdates length and a saved
skeleton never outdates area: each metric looks only at its own kind of file.

**Texts.** Metrics bar: `source=` `"gt/skeletons/<stem>.png (GT)"` or
`"skeletons/<stem>_fake.png (prediction)"` ("Measured value from …"); `length_note` /
`area_note` when a GT file exists but the row says `prediction`:
`GT_NOT_USED = "A GT {noun} exists (gt/{folder}/) but was not used: {why}. Measure read the prediction."`
with `why` = `"it is not the photo's size"` (`size`), `"it could not be read"` (`unreadable`),
`"it equals the prediction"` (`unedited`); a GT saved after the CSV shows the outdated tip
instead. Status label = `status_text(mask_state, skeleton_state, has_labels)` of section 5
(shared words; `"Mask ready"` / `"Skeleton corrected"` go). Constants `GT_NOT_USED` and
`GT_STATUS_TIP` (`"Overlay and Side by side draw the GT skeleton (gt/skeletons/) when one
exists, else the prediction (skeletons/)."`) replace `CORRECTION_NOT_MEASURED` /
`CORRECTED_STATUS_TIP` (`window/metrics.py:26-35`; the `split_surface` pin at `:284-285`
changes with them). Status line: `measure_text(skeletons, masks, length, area, *,
gt_skeletons=0, gt_masks=0)` → `"20 skeletons (2 GT): measured, current · 20 masks (3 GT):
not measured yet"`; `input_freshness` also stamps the GT folder. End-of-run notice when GT
files were skipped: `"{n} GT {noun}(s) not used: {k} not the photo's size, {u} equal to the
prediction, {r} unreadable (named in {csv}.provenance.json)"` (warn; zero parts omitted).

**Build order (two commits):** commit 1 = the resolver and its tests, `Measurement`'s two
fields and the optional `Source` read in `_read_metric_csv`, nothing wired: every CSV
byte-identical (`test_measure_csv_is_byte_identical_to_legacy_pipeline`,
`test_real_sample_is_byte_identical_to_legacy_pipeline` untouched and green). Commit 2 (Sci) =
headers, `MeasureRun`, the handlers, records, `outdated`, texts, docs, CHANGELOG with the
before / after diff on the synthetic set (two GT rows measure their files: 28.86 and 60.83 mm,
`Source` = `gt`) and on the audit's 3000-pair real sample, where no `gt/` exists and
`skeleton_corrections/` is no longer read: `Source` = `prediction` on every row and every value
unchanged (the test compares the first seven columns byte for byte).

### 8. Tests and gates

**8.1 GT goldens (WG-T0, before any GT code changes).** `tests/data/gt_goldens.json`
(`{"meta", "ops", "cases"}`) and `tests/gt_goldens_support.py` (Qt-free: `GT_CASES`, `GT_OPS`,
`make_gt_project(root, case) -> tuple[str, str]`, `describe_ids(ids, reviewed) -> dict`,
`describe_file(path) -> dict`, `write_goldens(data)` behind `SPROUTS_WRITE_GT_GOLDENS=1`,
`sha()` as in `correct_goldens_support`). Cases:
- `two_classes_crossing`, 640 x 480 photo, default registry, ops as data:
  `("class","root"), ("size",8), ("stroke",[(100,100),(150,150),(200,200),(250,250),(300,300)]),
  ("class","nodule"), ("size",25), ("stroke",[(300,100),(250,150),(200,200),(150,250),(100,300)]),
  ("tool","eraser"), ("size",10), ("stroke",[(200,180),(200,200),(200,220)]),
  ("tool","brush"), ("class","root"), ("size",3), ("stroke",[(150,350),(250,350),(250,420),(150,420),(150,350)]),
  ("tool","fill"), ("click",(200,385)), ("clear",), ("undo",), ("tick","root_tip",True), ("save",)`;
- `empty_save`: `("save",)` on an untouched image;
- `lossy_remap_on_load`: a prepared `gt/<stem>.png` whose chunk maps id 2 to key `"unknown_x"`;
  ops `("open",), ("save",)`.
Recorded per case: `per_op_ids_sha256`, `ids_sha256`, `counts` (`{"0": n, "1": n, …}`),
`reviewed` (sorted), `dirty_before_save`, `save_returned`, `warnings` (count),
`file: {exists, mode, size, ids_sha256, reviewed, classes, palette_sha256, transparency}`;
`meta.deterministic_across_two_runs`. Expected values are captured from HEAD's
`GtTracingInterface`, not predicted: WG-T0 pins the editor as it is. WG-T4 reproduces every case
through `ClassesLayer` with a `FakeHost`; WG-T5 through `CorrectEditor` and the `ToolRouter`.

**8.2 `correct_goldens.json`.** No golden encodes a path (sweep: zero hits); the replay tests
join `mask/` and `skeleton_corrections/` themselves (`tests/test_correct_goldens.py:153-158`,
`test_correct_mask_goldens.py:125`, `test_correct_skeleton_goldens.py:128`,
`test_correct_mask_document.py:240`). WG-T2 / WG-T3 switch those joins to
`layout.gt_mask_path(SYNTH_STEM)` / `layout.gt_skeleton_path(SYNTH_STEM)`; `pixels_sha256`,
`on_sha256`, `on_count`, `colour_type`, `area_mm2_as_measured` and every `per_op_*` hash stay.
The two allowed non-pixel changes are in Global Constraints.

**8.3 The 450-line ratchet (`tests/test_correct_layering.py`).** Today (after W1-T2 / W1-T3 /
W4-T5): `editor.py` 445, `skeleton_layer.py` 444, `mask_layer.py` 434, `skeleton_document.py`
420, `session.py` 408, `mask_document.py` 349, `layer.py` 338, `controls.py` 350, `save_gate.py`
106. Budgets after Wave G: `raster_layer.py` ≤ 300 (new), `mask_layer.py` ≤ 240,
`classes_document.py` ≤ 420 (new, Qt-free), `classes_layer.py` ≤ 330 (new), `side_panels.py`
≤ 150 (new), `actions.py` ≤ 250 (new), `editor.py` ≤ 440 (WG-T5 moves `load_skeleton_file`
and `_SKELETON_FILES` out), `session.py` ≤ 450, `mask_document.py` ≤ 440, `skeleton_document.py`
≤ 450, `skeleton_layer.py` ≤ 450 (no growth), `layer.py` ≤ 370, `controls.py` ≤ 400, `keys.py`
≤ 110, `tool_router.py` ≤ 200. The Qt-free list gains `app.gui.correct.classes_document`;
`tests/test_gt_layout_and_files.py` and `tests/test_measure_sources.py` run the same
subprocess probe for `app.gt.files` and `app.inference.measure_sources`.

**8.4 The master's Review Focus item 1 ("a project saved before this build")** is void: old
data is out of scope (owner, 2026-10-25). Its replacement is item 1 of the Review Focus below.

## Review Focus

1. **A GT file that is unreadable or not the photo's size** (a copy tool truncated it, a photo
   was replaced). Expected: Correct shows "Mask not shown" and never removes or replaces it
   silently (W1-T2's rule on the GT path); Measure measures the prediction and records the
   reason; nothing is deleted. Tests: `test_an_unreadable_or_wrong_size_gt_mask_is_not_shown_
   and_kept` (WG-T2), `test_wrong_size_unedited_and_unreadable_gt_skeletons_fall_back_with_a_
   reason` (WG-T8).
2. **A project folder where `gt/` cannot be created** (a read-only share). Expected: the save
   fails with the existing failed-save prompt (Discard / Keep editing), the layer shows "Not
   saved", `mask/` and `skeletons/` are untouched. Test:
   `test_a_save_that_cannot_create_gt_keeps_the_prediction_and_reports` (WG-T2).
3. **`gt/classes.json` is invalid while the user corrects a mask.** Expected: the mask and
   skeleton layers work and save; only the classes layer is read-only, with one notice. Test:
   `test_an_invalid_registry_locks_only_the_classes_layer` (WG-T5).
4. **Remove GT… with unsaved edits, or while the read-ahead holds that image.** Expected: the
   button is disabled until the edits are saved or discarded; after a removal the page shows the
   predictions and no stale read-ahead payload comes back. Tests:
   `test_remove_gt_is_disabled_while_edits_are_unsaved_and_asks_before_it_deletes`,
   `test_remove_gt_forgets_the_read_ahead_of_that_image` (WG-T5).
5. **Generate "Replace all predictions" while an image with a GT mask is open in Correct.**
   Expected: the GT layer stays as it is (W1-T4's `follow_generated_outputs` reloads an
   unedited *prediction* only), "Load prediction" shows the new one. Test:
   `test_replace_predictions_leaves_an_open_gt_mask_as_it_is` (WG-T7).

---

## Tasks

| ID | Task | Impl | Rev | Sci | Lane | After |
|---|---|---|---|---|---|---|
| WG-T0 | GT goldens from today's Trace GT | sonnet / high | opus / medium | no | L-gold (Phase A) | — |
| WG-T1 | GT storage paths and scans; `ImageManager` GT sets | sonnet / high | opus / medium | no | L-correct | W1-T5, W1-T4 |
| WG-T2 | Mask layer reads and writes GT | opus / high | opus / high | no (one golden entry, O9) | L-correct | WG-T1 |
| WG-T3 | Skeleton layer reads and writes GT; `skeleton_corrections/` retired | opus / high | opus / high | no (one golden label) | L-correct | WG-T2 |
| WG-T4 | `RasterLayer`, `ClassesDocument`, `ClassesLayer` | opus / xhigh | opus / high | no | L-correct | WG-T0, WG-T3 |
| WG-T5 | The classes layer in Correct; page actions | opus / xhigh | opus / high | no | L-correct | WG-T4 |
| WG-T11 | Trace GT stage removed; old modules deleted; tests ported | opus / high | opus / high | no | L-correct | WG-T5, WG-T6 |
| WG-T6 | Library badge with three marks | sonnet / high | sonnet / medium | no | L-library | WG-T3 |
| WG-T7 | Generate: predictions only | sonnet / high | opus / medium | no | L-generate | WG-T2 |
| WG-T8 | Measure GT-first, `Source` column | opus / xhigh | opus / high | **yes** (commit 2) | L-measure | WG-T3, WG-T6, W2-T11 |
| WG-T9 | Restore, per-class counts, `exif_orientation` | sonnet / high | sonnet / medium | manifest only | L-correct | WG-T11 |
| WG-T10 | Vocabulary and docs | sonnet / medium | sonnet / medium | no | L-correct | WG-T11, WG-T8 |

Lanes: L-correct `WG-T1 → WG-T2 → WG-T3 → WG-T4 → WG-T5 → WG-T11 → WG-T9 → WG-T10 → W2-T8a`;
L-library `WG-T6` (after WG-T3); L-generate `WG-T7` (after WG-T2); L-measure `WG-T8` (after
WG-T3, WG-T6, W2-T11). Run commands below assume
`$env:QT_QPA_PLATFORM = 'offscreen'` and `./venv/Scripts/python.exe -m pytest … -v`.

---

### Task WG-T0: GT goldens captured from today's Trace GT

**Model / effort:** implementer sonnet / high; reviewer opus / medium. **Lane:** L-gold, Phase
A, before any GT code changes. **Sci:** no.

**Files:**
- Create: `tests/gt_goldens_support.py`, `tests/data/gt_goldens.json`, `tests/test_gt_goldens.py`
- Test: `tests/test_gt_goldens.py`

**Interfaces:**
- Consumes: `app.gui.gt_tracing_interface.GtTracingInterface` (`load_image`, `set_active_class`,
  `begin_stroke(QPoint)`, `continue_stroke(QPoint)`, `end_stroke()`, `brush_button` /
  `eraser_button` / `fill_button` (`raster_editor_base.py:215-230`), `size_slider`, `clear_mask`,
  `undo`, `_on_reviewed_changed(key, checked)`, `save_mask`, `has_unsaved_changes`);
  `app.gt.store.read_tracing`, `write_tracing`; `tests/correct_goldens_support.sha`.
- Produces: `gt_goldens_support.GT_CASES = ("two_classes_crossing", "empty_save", "lossy_remap_on_load")`,
  `GT_OPS: dict[str, list[tuple]]` (section 8.1), `make_gt_project(root: str, case: str) -> tuple[str, str]`
  (image path, project folder; writes the 640 x 480 photo, and for `lossy_remap_on_load` the
  prepared `gt/<stem>.png` + `gt/classes.json`), `describe_ids(ids: np.ndarray, reviewed: Iterable[str]) -> dict`,
  `describe_file(path: str) -> dict`, `load_goldens() -> dict`, `write_goldens(data: dict) -> None`,
  `GT_STEM = "SYNTH_T002_L001_2024.01.01_000000_001_SMM"`. WG-T4 and WG-T5 drive the same `GT_OPS`
  through the new layer and page and compare with `load_goldens()`.

- [ ] **Step 1: Write the support module and the failing test.** `tests/test_gt_goldens.py`:
```python
def test_the_trace_gt_editor_reproduces_the_goldens(app, tmp_path, case):   # parametrized over GT_CASES
    expected = load_goldens()["cases"][case]
    recorded = replay_legacy(case, str(tmp_path))      # drives GtTracingInterface with GT_OPS[case]
    assert recorded == expected

def test_the_goldens_are_deterministic_across_two_runs(app, tmp_path):
    assert replay_legacy("two_classes_crossing", str(tmp_path / "a")) == replay_legacy("two_classes_crossing", str(tmp_path / "b"))

def test_the_golden_file_has_the_three_cases_and_pins_ids_ticks_and_the_file():
    data = load_goldens()
    assert set(data) == {"meta", "ops", "cases"} and set(data["cases"]) == set(GT_CASES)
    case = data["cases"]["two_classes_crossing"]
    assert set(case) >= {"per_op_ids_sha256", "ids_sha256", "counts", "reviewed", "dirty_before_save", "save_returned", "warnings", "file"}
    assert case["reviewed"] == ["nodule", "root", "root_tip"] and case["file"]["mode"] == "P" and case["file"]["transparency"] == 0
    assert case["counts"]["2"] > 0 and case["counts"]["1"] > case["counts"]["3"] == 0   # root painted, nodule painted, root_tip ticked only
    assert data["cases"]["empty_save"]["file"]["exists"] is True and data["cases"]["empty_save"]["counts"] == {"0": 640 * 480}
    assert data["cases"]["lossy_remap_on_load"]["warnings"] == 1 and data["cases"]["lossy_remap_on_load"]["dirty_before_save"] is True
```
  `replay_legacy` lives in the test module (it dies with the old editor in WG-T11); `describe_*`
  live in the support module (they outlive it). `describe_file` reads the PNG with Pillow
  (`mode`, `size`, `getpalette()` sha, `info["transparency"]`) and `read_tracing` (ids sha,
  `reviewed`, `file_classes`).

- [ ] **Step 2: Run, expect failure.** Run:
  `pytest tests/test_gt_goldens.py -v`. Expected: FAIL, `tests/data/gt_goldens.json` missing
  (`FileNotFoundError` in `load_goldens`).

- [ ] **Step 3: Capture.** Run once with `$env:SPROUTS_WRITE_GT_GOLDENS = '1'`; `write_goldens`
  writes `json.dump(data, indent=1, sort_keys=True)` with `meta` (`numpy`, `opencv`, `pillow`,
  `pyqt`, `qt`, `purpose = "class-label goldens of the Trace GT editor before the Correct merge;
  cases.* is the contract"`, `deterministic_across_two_runs`) and `ops = GT_OPS`. Remove the
  variable. Read the JSON once: the counts and `reviewed` above hold.

- [ ] **Step 4: Run, expect pass.** Run: `pytest tests/test_gt_goldens.py tests/test_gt_tracing_interface.py -v`.
  Expected: all PASS (the existing GT tests are untouched).

- [ ] **Step 5: Ruff** on the three new files.

- [ ] **Step 6: Commit (lead):** `test(gt): class-label goldens of the Trace GT editor before the merge`

---

### Task WG-T1: GT storage paths and scans; `ImageManager` GT sets

**Model / effort:** implementer sonnet / high; reviewer opus / medium. **Lane:** L-correct,
Phase B start, after W1-T5 (`stem_key`, the keyed `_scan_ranked`, `ImageScan`) and W1-T4.
**Sci:** no.

**Files:**
- Modify: `app/project_layout.py` (docstring lines 1-20: the two folders; the folders block
  (`gt_dir` neighbourhood); the GT block after `gt_path`; `output_dirs`), `app/gui/image_manager.py`
  (`ImageLoaderThread.run`: `self.gt = layout.scan_gt_stems()` after the skeleton scans;
  the fields next to `skeleton_stems`; `_on_scan_finished` / `on_loading_finished` carry the
  three sets like `skeleton_stems`; `refresh_outputs` rescans them; the lookups next to
  `has_skeleton`), `docs/DATA_DICTIONARY.md` (folder tree lines 15-20 and the `gt/` section:
  the two new folders)
- Create: `app/gt/files.py`
- Test: Create `tests/test_gt_layout_and_files.py`, `tests/test_image_manager_gt.py`; Modify
  `tests/test_foundation_core_project_layout.py` (the `gt` block, `:189-192` at `19f3761`),
  `tests/test_stale_temp_sweep.py:174-185` (`output_dirs` is 9 entries)

**Interfaces:**
- Consumes: `app.naming.stem_key`; the keyed `ProjectLayout._scan_ranked`; `ImageManager.
  last_scan` (W1-T5); `app.provenance.file_sha256` (WG-T8 hashes GT files with it).
- Produces: Design section 1 (`GtStems`, the `ProjectLayout` members, `app.gt.files.*`) and on
  `ImageManager`: `gt_mask_stems`, `gt_skeleton_stems`, `gt_label_stems`, `has_gt_mask`,
  `has_gt_skeleton`, `has_gt_labels`, `set_gt(name, kind, present)`, `mask_state`,
  `skeleton_state`, `refresh_outputs` rescanning GT. `corrected_stems` / `has_correction` /
  `set_correction` / `corrected_skeleton_path` stay as they are until WG-T3.

- [ ] **Step 1: Failing tests** (`tests/test_gt_layout_and_files.py`):
```python
def test_gt_paths_mirror_the_prediction_folders(tmp_path):
    root = str(tmp_path); layout = ProjectLayout(root)
    assert layout.gt_mask_path("T001_L044") == os.path.join(root, "gt", "mask", "T001_L044.png")
    assert layout.gt_skeleton_path("T001_L044.jpg") == os.path.join(root, "gt", "skeletons", "T001_L044.png")
    assert layout.gt_path("T001_L044") == os.path.join(root, "gt", "T001_L044.png")
    assert layout.output_dirs[-2:] == (layout.gt_mask_dir, layout.gt_skeletons_dir) and len(layout.output_dirs) == 9

def test_scan_gt_stems_reads_the_three_kinds_and_ignores_the_rest(tmp_path):
    # writes gt/a.png, gt/mask/b.png, gt/skeletons/c.png, gt/classes.json, gt/mask/notes.txt
    stems = ProjectLayout(str(tmp_path)).scan_gt_stems()
    assert (stems.labels, stems.mask, stems.skeleton) == (frozenset({"a"}), frozenset({"b"}), frozenset({"c"}))
    assert ProjectLayout(str(tmp_path / "nowhere")).scan_gt_stems() == GtStems(frozenset(), frozenset(), frozenset())

def test_scans_key_by_stem_key(tmp_path):   # gt/mask/B.png for image b.jpg: one winner, keyed as the Library keys it
    ...; assert "b" in ProjectLayout(root).scan_gt_masks() or "B" in ...   # whichever stem W1-T5's keyed scan returns, exactly one

def test_gt_files_and_remove_gt_touch_only_that_image(tmp_path):
    # gt/a.png, gt/mask/a.png, gt/skeletons/a.png, gt/mask/b.png, gt/classes.json, mask/a.png
    assert gt_files(layout, "a") == GtFiles(layout.gt_mask_path("a"), layout.gt_skeleton_path("a"), layout.gt_path("a"))
    removed = remove_gt(layout, "a")
    assert set(removed) == {layout.gt_path("a"), layout.gt_mask_path("a"), layout.gt_skeleton_path("a")}
    assert os.path.isfile(layout.gt_mask_path("b")) and os.path.isfile(layout.mask_path("a")) and os.path.isfile(registry_path(str(tmp_path)))
    assert remove_gt(layout, "a") == ()

def test_png_size_and_image_size_read_headers_only(tmp_path):
    assert png_size(write_png(tmp_path / "s.png", (256, 341))) == (341, 256)
    assert image_size(write_jpeg(tmp_path / "p.jpg", (480, 640))) == (640, 480)
    assert png_size(str(tmp_path / "missing.png")) is None and image_size(write_text(tmp_path / "x.png", "no")) is None

def test_files_is_qt_free():   # subprocess probe of tests/test_correct_layering.py for app.gt.files
```
  `ImageManager` (`tests/test_image_manager_gt.py`, offscreen, the loader thread driven as
  `tests/test_library_main_window.py` does):
```python
def test_the_loader_scans_gt_after_the_predictions(gt_project, app):
    manager = load(gt_project)      # gt/mask/a.png, gt/skeletons/b.png, gt/c.png; mask/d.png prediction
    assert (manager.gt_mask_stems, manager.gt_skeleton_stems, manager.gt_label_stems) == ({"a"}, {"b"}, {"c"})
    assert manager.mask_state("a") == "gt" and manager.mask_state("d") == "prediction" and manager.skeleton_state("a") == "none"

def test_set_gt_and_refresh_outputs_follow_the_files(gt_project, app):
    manager.set_gt("d", "mask", True); assert manager.has_gt_mask("d")
    os.remove(layout.gt_mask_path("a")); manager.refresh_outputs(); assert not manager.has_gt_mask("a")
```
  Edit `tests/test_stale_temp_sweep.py:174-185` to the 9-entry tuple;
  `tests/test_foundation_core_project_layout.py` gains the two new path asserts.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_gt_layout_and_files.py tests/test_image_manager_gt.py tests/test_stale_temp_sweep.py -v`.
  Expected: FAIL (`ImportError: app.gt.files`, `AttributeError: gt_mask_path`, 7-tuple).

- [ ] **Step 3: Implement** the Design section 1 signatures in `app/project_layout.py` and
  `app/gt/files.py`; the `ImageManager` sets and lookups; `scan_gt_*` through the keyed
  `_scan_ranked` with `(".png",)`.

- [ ] **Step 4: Run, expect pass.** The Step 2 command plus
  `pytest tests/test_foundation_core_project_layout.py tests/test_foundation_core_config.py tests/test_library_main_window.py tests/test_robust_project_scan_speed.py tests/test_stem_case_and_formats.py -v`.
  Expected: all PASS; the import probe of `app.project_layout` still loads no cv2 / PyQt6.

- [ ] **Step 5: Ruff** on the task's files.

- [ ] **Step 6: Commit (lead):** `feat(gt): gt/mask and gt/skeletons paths and scans; the Library knows which images have GT`

---

### Task WG-T2: The mask layer reads GT first and writes GT only

**Model / effort:** implementer opus / high; reviewer opus / high. **Lane:** L-correct, after
WG-T1 (and W1-T2 / W1-T4, which precede it on the lane). **Sci:** no CSV value; one golden
entry (`mask_clear.saved` of the model-mask case) changes by O9.

**Files:**
- Modify: `app/gui/correct/mask_document.py` (`MaskRead` `:70`; `read` `:171`; `open` `:197`;
  `save` `:249-293`; `status` `:143`; `file_stamps` `:158`; `unloaded_file` `:139` and W1-T4's
  `matches_disk` on the GT path), `app/gui/correct/mask_layer.py` (`_outcome` `:419`;
  `unloaded_label` `:169`; `load_prediction`), `app/gui/correct/session.py` (`save` `:244-256`),
  `app/gui/correct/layer.py:73` (the `cleared` comment), `app/gui/correct/controls.py`
  (`prediction_button` in `_build_dock` `:196-235`; the enabled rule in `show_layer`
  `:254-277`), `app/gui/correct/editor.py` (`_report` `:415`; `_refresh` `:425` for the button
  state),
  `app/gui/file_tree_manager.py:64-74` (`highlight_saved_mask` / `unhighlight_cleared_mask`
  call `manager.set_gt(image_path, "mask", True / False)` instead of `set_mask`),
  `app/gui/correct/read_back.py` (`mask_read_back` carries `source` and `prediction_plane`),
  `README.md:218-236` (Save paragraph), `CHANGELOG.md`
  (`### Changed`)
- Test: Modify `tests/test_correct_goldens.py:153-158`, `tests/test_correct_mask_goldens.py:125,
  :156-157`, `tests/test_correct_mask_document.py` (the `mask/` joins at `:48, :146, :210, :221,
  :240, :259, :282`; `:239-240`), `tests/test_correct_editor.py:1333-1343`
  (`test_saving_writes_binary_masks_and_never_the_gt_folder`), `tests/test_correct_session.py`
  (`test_save_with_nothing_dirty_saves_the_active_layer`), `tests/test_correct_main_window.py:579-609`,
  `tests/data/correct_goldens.json` (`mask_clear.saved` of `synth_model_mask_and_skeleton` only);
  Create `tests/test_correct_mask_gt.py`

**Interfaces:**
- Consumes: `ProjectLayout.gt_mask_path / find_gt_mask`, `app.gt.files.SOURCE_GT /
  SOURCE_PREDICTION`, `ImageManager.set_gt`; W1-T2's `MaskRead.not_loaded`.
- Produces: `MaskRead(image_path, size, stamps, plane, has_saved_file, not_loaded, …,
  source: str | None, prediction_plane: np.ndarray | None, gt_path: str, prediction_path: str | None)`
  (new fields keyword-defaulted; W3-T4 adds `resized_from` later); `MaskDocument.source: str | None`
  (`"gt"` / `"prediction"` / None), `MaskDocument.prediction() -> np.ndarray | None`,
  `MaskDocument.write_stamps() -> tuple` (the GT path), `MaskSaveResult(ok, path=None,
  removed=False, kept=False, error="", message="")`; `MaskLayer.load_prediction() -> bool`;
  `MaskLayer.unloaded_label()` = `"gt/mask/<stem>.png"` only when that file exists and was not
  loaded, else `""`;
  `CorrectControls.prediction_button` (`IconButton("cpu", …)`, signal
  `prediction_requested = pyqtSignal()`), `CorrectControls.set_prediction_available(bool, tip: str)`;
  `CorrectEditor.load_prediction() -> None`; `CorrectSession.save` writes dirty layers only
  (notice `("Nothing unsaved", "info")`).

- [ ] **Step 1: Failing tests** (`tests/test_correct_mask_gt.py`, fixtures `make_editor`,
  `project`, `paint` from `tests/test_correct_editor.py`; `project.a` has a model mask and a
  generated skeleton, `project.blank` neither):
```python
def test_a_mask_edit_saves_to_gt_mask_and_never_touches_the_prediction(make_editor, project):
    before = file_bytes(project.model_mask); editor = make_editor(project.a, project.folder)
    paint(editor, (20, 100), (80, 110)); assert editor.save() is True
    gt = ProjectLayout(project.folder).gt_mask_path(project.a)
    assert os.path.isfile(gt) and png_colour_type(gt) == 6 and file_bytes(project.model_mask) == before
    assert not os.path.exists(ProjectLayout(project.folder).gt_path(project.a))    # never the class labels
    assert editor.layers[MASK].status() == LayerStatus("clean", "GT", gt)

def test_the_layer_opens_the_gt_mask_when_one_exists_else_the_prediction(make_editor, project):
    editor = make_editor(project.a, project.folder)
    assert editor.layers[MASK].document.source == "prediction" and editor.layers[MASK].status().label == "Prediction"
    write_mask_file(gt_path, plane_with_one_block); editor.load_image(project.a, project.folder)
    assert editor.layers[MASK].document.source == "gt" and (editor.layers[MASK].document.history.plane == plane_with_one_block).all()

def test_clear_and_save_writes_an_empty_gt_mask(make_editor, project, signals):          # O9
    editor = make_editor(project.a, project.folder); editor.clear_active_layer(); assert editor.save() is True
    assert describe_mask_file(gt_path)["on_count"] == 0 and metrics.area_row("a", gt_path)[AREA_VALUE_COLUMN] == 0.0
    assert signals["mask_saved"] == [project.a] and signals["mask_cleared"] == []
    assert ("Empty mask saved as GT for a.png: root area 0", "info") in notices

def test_a_mask_equal_to_the_prediction_removes_the_gt_file(make_editor, project, signals):   # O2, symmetric
    write_mask_file(gt_path, plane_with_one_block); editor = make_editor(project.a, project.folder)
    editor.load_prediction(); assert editor.layers[MASK].is_dirty() and editor.save() is True
    assert not os.path.exists(gt_path) and signals["mask_cleared"] == [project.a]
    assert ("Mask equals the prediction: removed gt/mask/a.png", "info") in notices
    assert editor.layers[MASK].status().label == "Prediction"

def test_load_prediction_is_one_undo_step_and_is_off_without_a_prediction(make_editor, project):
    editor = make_editor(project.a, project.folder); write_mask_file(gt_path, plane_with_one_block); editor.load_image(project.a, project.folder)
    editor.load_prediction(); assert (plane(editor) == prediction_plane).all() and editor.session.can_undo()
    editor.undo(); assert (plane(editor) == plane_with_one_block).all() and not editor.layers[MASK].is_dirty()
    editor.load_image(project.blank, project.folder); assert not editor.controls.prediction_button.isEnabled()

def test_save_with_nothing_unsaved_writes_nothing_and_says_so(make_editor, project, notices):
    editor = make_editor(project.a, project.folder); stamps = stamps_of(project.folder)
    assert editor.save() is False and stamps_of(project.folder) == stamps and ("Nothing unsaved", "info") in notices

def test_an_unreadable_or_wrong_size_gt_mask_is_not_shown_and_kept(make_editor, project):   # Review Focus 1 (W1-T2's rule on the GT path)
    write_mask_file(gt_path, plane_80x60); editor = make_editor(project.a, project.folder)
    assert editor.layers[MASK].document.source == "gt" and editor.layers[MASK].status().label == "Mask not shown"
    assert editor.layers[MASK].unloaded_label() == "gt/mask/a.png" and editor.save() is False and file_bytes(gt_path) == before
    paint(editor, (20, 100), (80, 110)); prompts.answer = CANCEL; assert editor.save() is False and file_bytes(gt_path) == before

def test_a_save_that_cannot_create_gt_keeps_the_prediction_and_reports(make_editor, project, prompts):   # Review Focus 2
    open(os.path.join(project.folder, "gt"), "w").close()      # a regular file where gt/ should be: every OS refuses the folder
    editor = make_editor(project.a, project.folder); paint(editor, (20, 100), (80, 110))
    assert editor.save() is False and editor.layers[MASK].status().label == "Not saved" and file_bytes(project.model_mask) == before
    assert prompts.calls == [] and editor.layers[MASK].is_dirty()                      # Ctrl+S reports; the prompt comes on leaving

def test_read_ahead_reads_the_gt_mask_first_and_stamps_cover_both(project):
    read = MaskDocument.read(project.a, (640, 480))
    assert read.source == "prediction" and read.prediction_plane is not None and read.gt_path.endswith(os.path.join("gt", "mask", "a.png"))
    doc = MaskDocument(); doc.open(project.a, (640, 480), read)
    assert len(doc.file_stamps()) == 1 + 6 and doc.write_stamps() == (doc.file_stamps()[0],)

def test_the_library_marks_a_gt_mask_after_a_save(window_with_project):   # file_tree_manager wiring
    ...; assert window.image_manager.has_gt_mask(stem) and stem not in window.image_manager.masks_added
```
  Rewrite `test_saving_writes_binary_masks_and_never_the_gt_folder` as
  `test_saving_writes_the_gt_mask_and_never_the_class_labels` (`assert os.path.isfile(gt_mask)`
  and `not os.path.exists(layout.gt_path(stem))`); in `tests/test_correct_session.py` the
  nothing-dirty test becomes `test_save_with_nothing_dirty_writes_nothing_and_notifies`
  (`assert report.outcomes == () and notices[-1] == ("Nothing unsaved", "info")`); the goldens'
  `files()` helpers return `layout.gt_mask_path(SYNTH_STEM)`; `tests/test_correct_mask_document.py:239-240`
  and `tests/test_correct_mask_goldens.py:156-157` become
  `assert (outcome.path is not None, outcome.cleared) == (case == "synth_model_mask_and_skeleton", False)`
  with `describe_mask_file(path)["on_count"] == 0` for the model case.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_correct_mask_gt.py tests/test_correct_goldens.py tests/test_correct_mask_goldens.py tests/test_correct_mask_document.py tests/test_correct_session.py -v`.
  Expected: new tests FAIL (`no attribute 'load_prediction'`, file written under `mask/`);
  goldens FAIL on the path.

- [ ] **Step 3: Implement** Design section 3.4 (mask part): `MaskDocument.read` resolves
  `gt_path` / `prediction_path` / `source` and keeps `prediction_plane`; `save` applies the
  equality and the empty-GT rules and writes with `write_mask_file(self.gt_path, on)`;
  `status` per the label table; `file_stamps` = GT first; `CorrectSession.save` =
  `self._save(self.dirty_layers())` or the notice; `MaskLayer.load_prediction` =
  `replace_plane`-equivalent through `document.history.commit(full rect, plane)` +
  `canvas.rebuild()` + `_on_patch` (WG-T4 lifts it into `RasterLayer.replace_plane`; here it
  may live on `MaskLayer`); `CorrectControls.prediction_button` next to `load_button`;
  `editor._refresh` sets its enabled state and tip from the active layer
  (`document.prediction_path`, `source != "legacy"`). Re-capture only the
  `mask_clear.saved` entry of the model case with `SPROUTS_WRITE_CORRECT_GOLDENS` (or the
  support module's writer) and check `git diff tests/data/correct_goldens.json` shows that
  block only.

- [ ] **Step 4: Run, expect pass.** Step 2 command plus
  `pytest tests/test_correct_editor.py tests/test_correct_mask_layer.py tests/test_correct_read_ahead_handback.py tests/test_correct_editor_prompt_flow.py tests/test_correct_main_window.py tests/test_correct_layering.py tests/test_correct_goldens_data.py -v`.
  Expected: all PASS; every `per_op_plane_sha256` and `mask.saved` hash unchanged.

- [ ] **Step 5: Ruff** on the task's files.

- [ ] **Step 6: Commit (lead):** `feat(correct): the mask layer opens GT first and saves to gt/mask; an empty mask is GT; Save writes only dirty layers`
  (body: "golden `mask_clear.saved` of synth_model_mask_and_skeleton: file removed -> empty GT
  file, O9; pixels of every other entry unchanged").

---

### Task WG-T3: The skeleton layer reads GT first and writes GT only; `skeleton_corrections/` retired

**Model / effort:** implementer opus / high; reviewer opus / high. **Lane:** L-correct, after
WG-T2. **Sci:** no CSV value; the goldens' `start.skeleton_source` label changes from
`"generated"` to `"prediction"`.

**Files:**
- Modify: `app/gui/skeleton_document.py` (`CORRECTED` / `GENERATED` → `GT` / `PREDICTION`
  `:21-22`; `correction_path` `:44-45` → `gt_path` = `layout.gt_skeleton_path(self.stem)`;
  `start_file` `:55-62`), `app/gui/correct/skeleton_document.py` (`files :120`, `save :285-324`,
  `_remove_correction :325`, `status :367-389` labels, `file_stamps :390`, `write_stamps` /
  `matches_disk` from W1-T4 on the GT path), `app/gui/correct/editor.py` (`load_prediction` on
  the skeleton = `self._skeleton.load_file(files.generated_path())`), `app/project_layout.py`
  (remove `corrections_dir`, `corrected_skeleton_path`, `find_corrected_skeleton`,
  `scan_corrected_skeletons`, `scan_corrected_stems`, `CORRECTED_SKELETON_SUFFIX`; `output_dirs`
  is 8 entries; docstring line 11), `app/gui/image_manager.py` (`corrected_stems` /
  `has_correction` / `set_correction` / `corrected_skeleton_path` → `gt_skeleton_stems` /
  `has_gt_skeleton` / `set_gt` / `gt_skeleton_path_for`), `app/gui/window/editors.py`
  (`sync_open_correction :76-96` stats `layout.gt_skeleton_path(stem)` and calls
  `set_gt(stem, "skeleton", present)`), `app/gui/display_controller.py` (`_current_skeleton_path`
  uses `gt_skeleton_path_for`), `app/gui/window/metrics.py` (`image_has_correction` →
  `image_has_gt_skeleton`), `docs/DATA_DICTIONARY.md` (the `skeleton_corrections/` rows)
- Test: Modify `tests/test_correct_goldens.py` (`files()`; `start.skeleton_source`),
  `tests/test_correct_skeleton_goldens.py:128`, `tests/test_correct_skeleton_document.py`,
  `tests/test_correct_skeleton_layer.py`, `tests/test_correct_editor.py`,
  `tests/test_correct_main_window.py`, `tests/test_correct_session.py`,
  `tests/test_correct_editor_history.py`, `tests/test_skeleton_save_only_when_changed.py`
  (W1-T3), `tests/test_correct_changed_on_disk.py` (W1-T4), `tests/test_shell_library_corrections.py`,
  `tests/test_foundation_core_project_layout.py` (the corrections block `:157-186` goes),
  `tests/test_stale_temp_sweep.py` (8 entries), `tests/data/correct_goldens.json`
  (`start.skeleton_source` only) — every `skeleton_corrections` join becomes
  `layout.gt_skeleton_path(...)` (`rg skeleton_corrections tests` lists them); Create
  `tests/test_correct_skeleton_gt.py`

**Interfaces:**
- Consumes: `ProjectLayout.gt_skeleton_path / find_gt_skeleton`; W1-T3's `SkeletonSaveResult.
  removed`, `is_generated_as_opened`; W1-T4's `write_stamps` / `matches_disk`.
- Produces: `app.gui.skeleton_document.GT = "gt"`, `PREDICTION = "prediction"` (`CORRECTED` /
  `GENERATED` removed); `SkeletonFiles.gt_path` (property; `correction_path` removed);
  `ImageManager.gt_skeleton_path_for(name) -> str | None`; `CorrectEditor.skeleton_cleared =
  pyqtSignal(str)` (image path; emitted when W1-T3's `removed` outcome removes the GT file);
  `CorrectEditor.load_prediction()` covers the skeleton layer too; `ProjectLayout` without the
  corrections members.

- [ ] **Step 1: Failing tests** (`tests/test_correct_skeleton_gt.py`):
```python
def test_a_skeleton_edit_saves_to_gt_skeletons_and_never_creates_skeleton_corrections(make_editor, project):
    editor = make_editor(project.a, project.folder); editor.set_active_layer(SKELETON); draw_polyline(editor, [(40, 60), (150, 70)])
    assert editor.save() is True
    layout = ProjectLayout(project.folder)
    assert os.path.isfile(layout.gt_skeleton_path(project.a)) and not os.path.exists(os.path.join(project.folder, "skeleton_corrections"))
    assert file_bytes(project.generated) == before and editor.layers[SKELETON].status() == LayerStatus("clean", "GT", layout.gt_skeleton_path(project.a))

def test_the_skeleton_opens_the_gt_file_first(project):
    files = SkeletonFiles(project.a, project.folder)
    assert files.start_file() == (files.generated_path(), PREDICTION)
    imwrite_unicode(files.gt_path, raster_640x480); assert files.start_file() == (files.gt_path, GT)

def test_a_gt_skeleton_of_another_size_opens_resampled_and_saves_at_the_photo_size(project, make_editor):
    imwrite_unicode(ProjectLayout(project.folder).gt_skeleton_path(project.a), raster_341x256)
    editor = make_editor(project.a, project.folder)
    assert editor.layers[SKELETON].document.model.mask.shape == (480, 640) and editor.layers[SKELETON].status().label == "GT"
    editor.set_active_layer(SKELETON); draw_polyline(editor, [(40, 60), (150, 70)]); assert editor.save() is True
    assert png_size(ProjectLayout(project.folder).gt_skeleton_path(project.a)) == (640, 480)

def test_load_prediction_on_the_skeleton_is_undoable_and_save_removes_the_gt(make_editor, project, signals):
    ...gt exists...; editor.set_active_layer(SKELETON); editor.load_prediction(); assert editor.session.can_undo()
    assert editor.save() is True and not os.path.exists(gt_path) and signals["skeleton_cleared"] == [project.a]   # W1-T3's rule

def test_the_library_follows_a_skeleton_save_and_its_removal(window_with_project):
    ...save...; assert window.image_manager.has_gt_skeleton(stem) and window.image_manager.gt_skeleton_path_for(stem) == layout.gt_skeleton_path(stem)
    ...load prediction, save...; assert not window.image_manager.has_gt_skeleton(stem)

def test_the_overlay_draws_the_gt_skeleton(window_with_project):   # display_controller
    assert window.display_controller._current_skeleton_path() == layout.gt_skeleton_path(stem)

def test_the_layout_has_no_corrections_folder_any_more():
    assert not hasattr(ProjectLayout, "corrected_skeleton_path") and len(ProjectLayout("x").output_dirs) == 8
```
  Edit the listed `skeleton_corrections` joins to `layout.gt_skeleton_path(...)`; the golden
  `start.skeleton_source` to `"prediction"`; `CORRECTED` / `GENERATED` imports to `GT` /
  `PREDICTION`.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_correct_skeleton_gt.py tests/test_correct_goldens.py tests/test_correct_skeleton_goldens.py tests/test_correct_skeleton_document.py -v`.
  Expected: FAIL (file under `skeleton_corrections/`; `ImportError: GT`).

- [ ] **Step 3: Implement** Design section 3.4 (skeleton part) and section 2's removals; the
  `ImageManager` renames; `display_controller._current_skeleton_path` uses
  `gt_skeleton_path_for`; `sync_open_correction` stats the GT path. `skeleton_layer.py` does
  not grow.

- [ ] **Step 4: Run, expect pass.** Step 2 command plus
  `pytest tests/test_correct_skeleton_layer.py tests/test_correct_editor.py tests/test_correct_main_window.py tests/test_shell_library_corrections.py tests/test_skeleton_save_only_when_changed.py tests/test_correct_changed_on_disk.py tests/test_correct_layering.py tests/test_library_main_window.py tests/test_foundation_core_project_layout.py tests/test_stale_temp_sweep.py -v`.
  Expected: all PASS; every `per_op_raster_sha256` and `skeleton.saved` hash unchanged.

- [ ] **Step 5: Ruff** on the task's files.

- [ ] **Step 6: Commit (lead):** `feat(correct): the skeleton layer opens GT first and saves to gt/skeletons; skeleton_corrections/ is retired`
  (body: "golden `start.skeleton_source`: generated -> prediction; pixels unchanged").

---

### Task WG-T4: `RasterLayer` lifted from `MaskLayer`; `ClassesDocument`; `ClassesLayer`

**Model / effort:** implementer opus / xhigh; reviewer opus / high. **Lane:** L-correct, after
WG-T0 and WG-T3. **Sci:** no (GT goldens must be reproduced; mask goldens unchanged).

**Files:**
- Create: `app/gui/correct/raster_layer.py`, `app/gui/correct/classes_document.py`,
  `app/gui/correct/classes_layer.py`
- Modify: `app/gui/correct/mask_layer.py` (becomes `MaskLayer(RasterLayer)`: identity,
  document, `read_ahead` / `open` / `close` / `is_open` / `file_stamps` / `write_stamps` /
  `is_dirty` / `save` / `revert` / `status` / `load_prediction` / `_outcome`; ≤ 230 lines),
  `app/gui/correct/layer.py` (`CLASSES = "classes"`, `LAYER_IDS = (MASK, SKELETON, CLASSES)`,
  the `GuardedSaveLayer` protocol, `SaveOutcome.cleared` comment), `app/gui/correct/__init__.py`
  (module list), `app/gui/correct/read_back.py` (a layer with `read_back` wins; classes supply it)
- Test: Create `tests/test_correct_raster_layer.py`, `tests/test_correct_classes_document.py`,
  `tests/test_correct_classes_layer.py`, `tests/test_correct_classes_goldens.py`; Modify
  `tests/test_correct_layering.py` (`QT_FREE` + `app.gui.correct.classes_document`;
  `_EDIT_LAYER_MEMBERS` unchanged), `tests/correct_fakes.py` (`FakeLayer("classes")` allowed)

**Interfaces:**
- Consumes: `app.gt` (`load_registry`, `save_registry`, `registry_path`, `default_registry`,
  `Registry`, `RegistryError`, `read_tracing`, `write_tracing`, `align_to_registry`,
  `present_keys`, `Tracing`, `GtFileError`, `gt_path` via `ProjectLayout.gt_path`),
  `app.gui.paint_canvas.PaintCanvas`, `app.gui.patch_history.PatchHistory`, `mask_tools.build_tools`,
  `TOOL_CLASSES`, `FootprintRing`, `tests/gt_goldens_support`.
- Produces:
```python
# layer.py
CLASSES = "classes"; LAYER_IDS = (MASK, SKELETON, CLASSES)
class GuardedSaveLayer(Protocol):
    def save_needs_prompt(self) -> bool: ...          # True: PgDn must ask instead of saving silently
# raster_layer.py
class RasterLayer:   # Design section 3.1; EditLayer minus the document members
    def __init__(self, host: LayerHost, scene: QGraphicsScene, *, document, lut, to_plane, fill_source) -> None
    def replace_plane(self, plane: np.ndarray) -> bool
# classes_document.py (Qt-free)
@dataclass(frozen=True)
class ClassesRead:
    image_path: str; size: tuple[int, int]; stamps: tuple; registry: Registry; registry_error: str
    tracing: Tracing | None; warnings: tuple[str, ...]; changed: bool; error: str     # error: "" | "unreadable" | "size"
    nbytes: int   # property
@dataclass(frozen=True)
class ClassesSaveResult: ok: bool; path: str | None = None; error: str = ""
class ClassesDocument:
    noun = "class labels"; history: PatchHistory | None; registry: Registry; registry_error: str; active_key: str
    def __init__(self, *, on_history_change: Callable[[], None] | None = None) -> None
    @staticmethod
    def read(image_path: str, size: tuple[int, int]) -> ClassesRead        # any thread
    def open(self, image_path: str, size: tuple[int, int], read: ClassesRead | None = None) -> list[Notice]
    def close(self) -> None; def is_open(self) -> bool; revision: int (property); def touch(self) -> None
    def file_stamps(self) -> tuple     # (gt/<stem>.png, gt/classes.json)
    def write_stamps(self) -> tuple    # (gt/<stem>.png,)
    def is_dirty(self) -> bool; def is_read_only(self) -> bool; def lossy_remap(self) -> bool
    def lut(self) -> np.ndarray                                               # (256, 4) BGRA, registry colours, row 0 transparent
    def ids_from_argb(self, argb: np.ndarray, rect: Rect) -> np.ndarray      # exact colour match; committed-plane fallback (gt_tracing_interface.py:390-416)
    def fill_source(self, plane: np.ndarray) -> tuple[np.ndarray, int]       # the active class only
    def set_active_class(self, key: str) -> bool; def brush_size(self, key: str) -> int; def set_brush_size(self, key: str, px: int) -> None
    def counts(self) -> dict[str, int]; def present_keys(self) -> set[str]
    def ticked(self) -> frozenset[str]; def reviewed(self) -> frozenset[str]   # ticked | present
    def set_ticked(self, key: str, on: bool) -> bool                          # False: present class, Ignore, or read-only
    def save(self) -> ClassesSaveResult                                       # registry first when missing; always writes the file
    def revert(self) -> None; def status(self) -> LayerStatus
    def apply_registry(self, registry: Registry) -> None                      # after the class editor saved it
    LOCKED_NOTICE = "Editing is off: gt/classes.json is invalid ({err}). Fix the file, then reopen the image."
# classes_layer.py
class ClassesLayer(RasterLayer):   # EditLayer + ReadAheadLayer + GuardedSaveLayer
    layer_id = CLASSES; noun = "class labels"; color = tokens.ACCENT; Z = 1.2; DEFAULT_OPACITY = 0.60
    CLEARED_NOTICE = "Class labels cleared. Ctrl+Z to undo; Save writes an empty file."
    brush_color: QColor            # property: the active class colour
    document: ClassesDocument
    def set_active_class(self, key: str) -> None; def active_class(self) -> str
    def set_ticked(self, key: str, on: bool) -> bool
    def registry(self) -> Registry; def apply_registry(self, registry: Registry) -> None
    def is_read_only(self) -> bool; def save_needs_prompt(self) -> bool
    on_classes_changed: Callable[[], None] | None      # set by the page; called after open, save, revert, apply_registry, ticks, active class
    def read_ahead(self, image_path, images_base_folder) -> Callable; def read_back(self, stamps) -> ClassesRead | None
```

- [ ] **Step 1: Failing tests.** `tests/test_correct_raster_layer.py`:
```python
def test_mask_layer_is_a_raster_layer_and_misses_no_member(scene):
    layer = MaskLayer(FakeHost(), scene)
    assert isinstance(layer, RasterLayer) and missing_members(layer) == [] and missing_members(layer, DiskGuardedLayer) == []
    assert len(Path("app/gui/correct/mask_layer.py").read_text().splitlines()) <= 240

def test_replace_plane_is_one_undo_entry(scene, project):
    layer = MaskLayer(FakeHost(), scene); layer.open(project.a, (640, 480), project.folder)
    assert layer.replace_plane(block) is True and layer.can_undo() and (layer.document.history.plane == block).all()
    assert layer.undo() and not layer.is_dirty()
```
  `tests/test_correct_classes_document.py` (Qt-free; fixture `gt_project` from `gt_goldens_support.make_gt_project`):
```python
def test_the_document_is_qt_free():   # subprocess probe
def test_opens_empty_and_never_reads_mask_or_skeletons(gt_project, monkeypatch):
    monkeypatch.setattr("app.masks.store.MaskStore.read", boom); monkeypatch.setattr("app.io_utils.imread_unicode", boom)
    doc = ClassesDocument(); assert doc.open(image, (640, 480)) == [] and doc.counts() == {"background": 640 * 480}
def test_a_lossy_remap_opens_dirty_with_warnings_and_needs_a_prompt(gt_project_lossy):
    doc = ClassesDocument(); notices = doc.open(image, (640, 480))
    assert [n.kind for n in notices] == ["warn"] and doc.is_dirty() and doc.lossy_remap()
    assert doc.status() == LayerStatus("dirty", "Unsaved · remapped", "Unknown classes were mapped to Ignore:\n" + notices[0].message.split(": ", 1)[1])
def test_an_invalid_registry_makes_the_document_read_only(gt_project_bad_registry):
    doc = ClassesDocument(); notices = doc.open(image, (640, 480))
    assert doc.is_read_only() and doc.status().label == "Read-only" and notices[0].kind == "danger"
    assert doc.set_ticked("root_tip", True) is False and doc.save() == ClassesSaveResult(False, None, doc.registry_error)
def test_ticks_are_locked_while_pixels_exist(gt_project): paint root pixels; assert doc.set_ticked("root", False) is False and "root" in doc.reviewed()
def test_save_writes_the_registry_first_then_the_file(gt_project, recorder): assert recorder.order == ["classes.json", f"{GT_STEM}.png"]
def test_an_empty_save_writes_a_file(gt_project): doc.save(); assert os.path.isfile(layout.gt_path(GT_STEM)) and read_tracing(...).ids.max() == 0
def test_counts_follow_the_patches(gt_project): ... == histogram(doc.history.plane)   # port of test_gt_editor_speed_paths
def test_ids_from_argb_keeps_the_committed_id_under_an_unmatched_colour(gt_project): ...   # port of the fallback test
def test_file_stamps_cover_the_file_and_the_registry_and_write_stamps_the_file(gt_project): assert len(doc.file_stamps()) == 2 and doc.write_stamps() == (doc.file_stamps()[0],)
def test_read_matches_open(gt_project): read = ClassesDocument.read(image, (640, 480)); doc.open(image, (640, 480), read); assert (doc.history.plane == read.tracing.ids).all()
```
  `tests/test_correct_classes_layer.py` (offscreen; `FakeHost`, a scene):
```python
def test_the_classes_layer_misses_no_member_and_answers_the_optional_protocols(scene):
    layer = ClassesLayer(FakeHost(), scene)
    assert missing_members(layer) == [] and missing_members(layer, ReadAheadLayer) == [] and missing_members(layer, GuardedSaveLayer) == [] and missing_members(layer, DiskGuardedLayer) == []
    assert (layer.layer_id, layer.noun, layer.color, layer.Z) == ("classes", "class labels", tokens.ACCENT, 1.2)
def test_painting_class_b_over_a_leaves_b_and_the_eraser_leaves_background(scene, gt_project): ...
def test_fill_is_limited_to_the_active_class_outline(scene, gt_project): ...
def test_the_brush_colour_is_the_active_class_colour(scene, gt_project): layer.set_active_class("nodule"); assert layer.brush_color.name() == "#ff7eb6"
def test_press_and_clear_are_refused_while_read_only_with_one_notice(scene, gt_project_bad_registry):
    host = FakeHost(); layer = ClassesLayer(host, scene); layer.open(image, (640, 480), folder)
    layer.press(QPoint(10, 10), NO_MODIFIER); layer.release(QPoint(10, 10)); assert layer.clear() is False
    assert host.notices.count(("Editing is off: gt/classes.json is invalid (" + layer.document.registry_error + "). Fix the file, then reopen the image.", "danger")) == 1
def test_retired_class_pixels_survive_open_and_save(scene, gt_project): ...
def test_per_class_brush_size_is_remembered(scene, gt_project): layer.set_active_class("root_tip"); layer.set_size(2); layer.set_active_class("root"); assert layer.size() == 8; layer.set_active_class("root_tip"); assert layer.size() == 2
```
  `tests/test_correct_classes_goldens.py`:
```python
def test_the_classes_layer_reproduces_the_gt_goldens(scene, tmp_path, case):   # parametrized over GT_CASES
    expected = load_goldens()["cases"][case]
    recorded = replay_layer(case, str(tmp_path))     # GT_OPS through ClassesLayer: set_active_class / set_size / set_tool / press+move+release / double-click for "click" / clear / undo / set_ticked / save
    assert recorded == expected
```
  Port the `test_gt_unsaved_state.py` and `test_gt_tracing_interface.py` cases listed in
  Design section 4 into these two files, with the old file left in place until WG-T11.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_correct_raster_layer.py tests/test_correct_classes_document.py tests/test_correct_classes_layer.py tests/test_correct_classes_goldens.py -v`.
  Expected: FAIL (`ImportError: raster_layer`, `classes_document`).

- [ ] **Step 3: Implement.** Lift the generic members of `mask_layer.py` (sweep list in Design
  3.1) into `RasterLayer`; `ClassesDocument` from `gt_tracing_interface.py:376-460, 507-666`
  (`_lut`, `_ids_from_argb`, `_fill_source`, `_on_patch` counts, `_read_tracing`,
  `_open_document`, `_rest_differs`, `_restore_saved`, `save_mask`) with the Qt bits removed
  (ids are a numpy plane; colours as BGRA words); `ClassesLayer` = `RasterLayer` +
  `ClassesDocument` + the class API. `PaintCanvas` is built with
  `lut=document.lut, to_plane=document.ids_from_argb, fill_source=document.fill_source`.

- [ ] **Step 4: Run, expect pass.** Step 2 command plus
  `pytest tests/test_correct_mask_layer.py tests/test_correct_mask_goldens.py tests/test_correct_goldens.py tests/test_correct_mask_gt.py tests/test_correct_editor.py tests/test_correct_layering.py tests/test_correct_read_ahead.py tests/test_gt_goldens.py -v`.
  Expected: all PASS; the mask goldens' hashes unchanged.

- [ ] **Step 5: Ruff** on the task's files.

- [ ] **Step 6: Commit (lead):** `feat(correct): RasterLayer base; ClassesDocument and ClassesLayer reproduce the class-label goldens`

---

### Task WG-T5: The classes layer in Correct; the page's GT actions

**Model / effort:** implementer opus / xhigh; reviewer opus / high. **Lane:** L-correct, after
WG-T4. **Sci:** no. Transitional: the Trace GT stage still exists after this task (its
Classes… / Export… buttons move to the Correct page, so its own page is empty for one commit);
WG-T11 removes it.

**Files:**
- Create: `app/gui/correct/side_panels.py`, `app/gui/correct/actions.py`
- Modify: `app/gui/correct/editor.py` (third layer in `_build_parts` `:98`; `SidePanels` at
  `:140-141`; `_report` `:415` emits `gt_saved` / `gt_removed`; `navigate` `:258` guarded save;
  `select_class`, `set_active_layer` `:315` history for `X`, `toggle_reference` `:353` for n
  layers, `_refresh` `:425`; `load_skeleton_file` `:358-373` and `_SKELETON_FILES` `:53` move
  to `actions.py`; 445 lines today, ≤ 440 after), `app/gui/correct/keys.py` (M / L / G / X,
  `select_class` ten slots),
  `app/gui/shortcuts.py:161-192` (Correct rows: `mask_layer` "M", `skeleton_layer` "L",
  `classes_layer` "G", `swap_layer` text, `select_class`, `toggle_layers` / `toggle_reference`
  texts), `app/gui/correct/tool_router.py:125-128, :158` (B goes to the active raster layer),
  `app/gui/correct/controls.py` (`load_button` enabled for the skeleton only; the polyline
  prompt only for the skeleton; `prediction_button` disabled on the classes layer),
  `app/gui/correct/layer_panel.py` (nothing structural; the eye tooltip wording for n layers
  `:98-106`), `app/gui/shell_chrome.py:343-382` (`gt_classes_button`, `gt_export_button`
  rewired to the Correct editor; new `gt_remove_button`; the `"Correct"` page; `"Trace GT": ()`),
  `app/gui/main_window.py` (`:177-184`: connect `correct_editor.gt_saved` → `_on_gt_saved`,
  `gt_removed` → `_on_gt_removed`, `skeleton_cleared` → `_on_skeleton_cleared`;
  `export_gt_dataset` `:683` saves the Correct editor), `app/gui/window/editors.py`
  (`on_gt_saved` also calls `image_manager.set_gt(stem, "labels", True)`,
  `library_index.refresh_status(stem)`; new `on_gt_removed` (all three kinds False, refresh,
  `metrics.refresh_if_current`), `on_skeleton_cleared`), `README.md` (Correct section: the
  Classes layer, keys M / L / G, Remove GT…), `CHANGELOG.md`
- Test: Create `tests/test_correct_classes_editor.py`; Modify `tests/test_correct_editor_keys.py`
  (key table), `tests/test_correct_tab_order.py` (side panel), `tests/test_correct_main_window.py`
  (`:564-572` receivers: `gt_saved`, `gt_removed`, `skeleton_cleared` connected once),
  `tests/test_shell_chrome_smoke.py:144-145` (buttons now on the Correct page),
  `tests/test_shell_chrome_topbar.py:184-207` (page contents), `tests/test_correct_classes_goldens.py`
  (page driver), `tests/test_shell_window_split_surface.py` (`gt_remove_button` attribute;
  `_on_gt_removed`, `_on_skeleton_cleared` bound names)

**Interfaces:**
- Consumes: WG-T4's classes API; `app.gui.class_panel.ClassPanel`; `app.gui.class_editor_dialog.
  ClassEditorDialog`; `app.gt.files.remove_gt`; `ImageManager.set_gt`.
- Produces: `CorrectEditor.gt_saved = pyqtSignal(str)` (image path), `gt_removed =
  pyqtSignal(str)`, `skeleton_cleared` (WG-T3), `class_panel: ClassPanel`, `side_panels:
  SidePanels`, `actions: PageActions`, `select_class(n: int) -> None`, `edit_classes() -> None`,
  `remove_gt() -> None`, `load_prediction() -> None`, `load_skeleton_file() -> None` (kept name,
  delegates), `previous_layer_id: str | None`; `SidePanels(layer_panel: LayerPanel, class_panel:
  ClassPanel, parent)` with `set_active_layer(layer_id: str) -> None`, `preferred_height`,
  `fit_height`; `PageActions(editor)` with `load_skeleton_file()`, `load_prediction()`,
  `edit_classes()`, `remove_gt()`, `confirm_remove: Callable[[QWidget, str, str], bool]`
  (injectable for tests; default a `QMessageBox` with the texts of Design 3.4); `MainWindow.
  gt_remove_button`.

- [ ] **Step 1: Failing tests** (`tests/test_correct_classes_editor.py`; `make_editor`,
  `project`, `paint`, `press(editor, key)`, `FakePrompts`, `signals` recorder):
```python
def test_the_page_has_three_layers_in_panel_order_and_the_class_panel_shows_only_when_active(make_editor, project):
    editor = make_editor(project.a, project.folder)
    assert list(editor.layers) == [MASK, SKELETON, CLASSES] and list(editor.layer_panel.rows) == [MASK, SKELETON, CLASSES]
    assert not editor.class_panel.isVisible(); editor.set_active_layer(CLASSES); assert editor.class_panel.isVisible()

def test_keys_m_l_g_switch_layers_and_x_returns_to_the_previous_one(make_editor, project):
    editor = make_editor(project.a, project.folder)
    press(editor, "L"); assert editor.active_layer_id == SKELETON
    press(editor, "G"); assert editor.active_layer_id == CLASSES
    press(editor, "X"); assert editor.active_layer_id == SKELETON
    press(editor, "M"); press(editor, "X"); assert editor.active_layer_id == SKELETON
    assert shortcuts.find_conflicts() == []

def test_digits_pick_classes_only_while_the_classes_layer_is_active(make_editor, project):
    editor = make_editor(project.a, project.folder); press(editor, "2")
    assert editor.active_layer_id == MASK and editor.layers[CLASSES].active_class() == "root"
    press(editor, "G"); press(editor, "2"); assert editor.layers[CLASSES].active_class() == "nodule"
    press(editor, "0"); assert editor.layers[CLASSES].active_class() == "ignore"

def test_b_tap_requests_the_brush_of_the_active_raster_layer(make_editor, project):
    editor = make_editor(project.a, project.folder); editor.set_active_layer(CLASSES); tap_b(editor)
    assert (editor.active_layer_id, editor.layers[CLASSES].current_tool) == (CLASSES, "brush")
    editor.set_active_layer(SKELETON); tap_b(editor); assert editor.active_layer_id == MASK

def test_a_classes_save_writes_only_gt_stem_png_and_emits_gt_saved(make_editor, project, signals):
    editor = make_editor(project.a, project.folder); editor.set_active_layer(CLASSES); paint(editor, (20, 100), (80, 110))
    before = tree(project.folder); assert editor.save() is True
    assert changed(before, tree(project.folder)) == {layout.gt_path(project.a), registry_path(project.folder)} and signals["gt_saved"] == [project.a]

def test_each_layer_writes_only_its_own_file(make_editor, project):      # the guard of Design 3.7
    for layer_id, expected in ((MASK, {layout.gt_mask_path(a)}), (SKELETON, {layout.gt_skeleton_path(a)}), (CLASSES, {layout.gt_path(a)})):
        editor.set_active_layer(layer_id); edit(editor, layer_id); before = tree(project.folder); assert editor.save()
        assert changed(before, tree(project.folder)) == expected

def test_pgdn_prompts_after_a_lossy_remap_and_saves_silently_otherwise(make_editor, project_lossy, prompts):
    editor = make_editor(project_lossy.a, project_lossy.folder); editor.navigate(1)
    assert prompts.calls == [("ask_unsaved", "class labels", "a.png")]                # lossy: the prompt
    prompts.answer = CANCEL; ...; editor.set_active_layer(CLASSES); paint(...); prompts.calls.clear(); editor.navigate(1)
    assert prompts.calls == [] and os.path.isfile(layout.gt_path(a))                # clean remap: silent save

def test_an_invalid_registry_locks_only_the_classes_layer(make_editor, project_bad_registry, notices):   # Review Focus 3
    editor = make_editor(a, folder); paint(editor, (20, 100), (80, 110)); assert editor.save() is True
    editor.set_active_layer(CLASSES); paint(editor, (20, 100), (80, 110))
    assert editor.layers[CLASSES].status().label == "Read-only" and not editor.layers[CLASSES].is_dirty()
    assert [n for n in notices if n[0].startswith("Editing is off: gt/classes.json is invalid")] and len(...) == 1

def test_remove_gt_is_disabled_while_edits_are_unsaved_and_asks_before_it_deletes(window_with_project, confirm):   # Review Focus 4
    ...paint...; assert not window.gt_remove_button.isEnabled() and "Save or discard" in window.gt_remove_button.toolTip()
    editor.save(); assert window.gt_remove_button.isEnabled()
    confirm.answer = False; window.gt_remove_button.click(); assert os.path.isfile(gt_mask)
    confirm.answer = True; window.gt_remove_button.click()
    assert not any(map(os.path.exists, (gt_mask, gt_skel, gt_labels))) and os.path.isfile(prediction_mask)
    assert signals["gt_removed"] == [a] and editor.layers[MASK].status().label == "Prediction" and not window.image_manager.has_gt_mask(stem)
    assert confirm.calls[-1] == (a_name, "Deletes gt/mask/a.png, gt/skeletons/a.png and gt/a.png where they exist. The predictions in mask/ and skeletons/ are kept. This cannot be undone.")

def test_remove_gt_forgets_the_read_ahead_of_that_image(window_with_project, confirm): ... assert window.correct_editor.paging.pending() == [] and a not in cached

def test_prompts_name_the_class_labels(make_editor, project, prompts):
    ...mask + classes dirty...; editor.maybe_save_changes(); assert prompts.calls[0][1] == "mask and class labels"

def test_opacity_and_visibility_of_the_classes_layer_persist(settings): ... "correct/classes/opacity"

def test_edit_classes_saves_the_registry_and_recolours(make_editor, project, dialog_stub): ...   # "Classes updated" notice; palette of the next save

def test_the_correct_page_holds_the_gt_actions(window_with_project):
    assert [b.text() for b in window.topbar._pages["Correct"].findChildren(QPushButton)] == ["Clear mask", "Classes…", "Export dataset…", "Remove GT…"]

def test_export_gt_dataset_saves_the_correct_editor_first(window_with_project, monkeypatch): ...

def test_the_page_reproduces_the_gt_goldens(make_editor, tmp_path, case): ...   # in tests/test_correct_classes_goldens.py, via the ToolRouter
```
  Port the panel-keyboard, discard, open and ux cases (Design 4 table) into this file.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_correct_classes_editor.py tests/test_correct_classes_goldens.py -v`.
  Expected: FAIL (`KeyError: 'classes'`, no `G` shortcut, no `gt_remove_button`).

- [ ] **Step 3: Implement** Design sections 3.2, 3.3, 3.5, 3.6: `CorrectEditor._build_parts`
  adds `ClassesLayer(self, self.scene)` with `on_classes_changed` wired to the panel;
  `SidePanels` registered as `side_panel`; `keys.py` rows; `ToolRouter` brush rule;
  `PageActions`; `_report` emits `gt_saved` for `CLASSES`; `navigate` checks
  `save_needs_prompt`; `MainWindow` connections and the page. Keep the editor at 440 lines by
  moving the Load-skeleton dialog into `actions.py`.

- [ ] **Step 4: Run, expect pass.** Step 2 command plus
  `pytest tests/test_correct_editor.py tests/test_correct_editor_keys.py tests/test_correct_tab_order.py tests/test_correct_main_window.py tests/test_shell_chrome_smoke.py tests/test_shell_chrome_topbar.py tests/test_shell_window_split_surface.py tests/test_correct_layering.py tests/test_correct_goldens.py tests/test_shell_info_shortcuts.py tests/test_shell_toast_side_panels.py tests/test_gt_goldens.py -v`.
  Expected: all PASS; `find_conflicts() == []`; `editor.py` ≤ 440 lines.

- [ ] **Step 5: Ruff** on the task's files.

- [ ] **Step 6: Commit (lead):** `feat(correct): class labels as the third layer; keys M / L / G; Classes, Export dataset and Remove GT on the Correct page`

---

### Task WG-T11: The Trace GT stage removed; old editor modules deleted; tests ported

**Model / effort:** implementer opus / high; reviewer opus / high. **Lane:** L-correct, after
WG-T5 and WG-T6. **Sci:** no.

**Files:**
- Delete: `app/gui/raster_editor_base.py`, `app/gui/gt_tracing_interface.py`,
  `tests/test_raster_editor_behaviour.py`, `tests/test_gt_editor_refocus.py`,
  `tests/test_gt_editor_teardown.py`, and the ported files once their cases live in the Correct
  tests: `tests/test_gt_tracing_interface.py`, `tests/test_gt_unsaved_state.py`,
  `tests/test_gt_editor_discard.py`, `tests/test_gt_editor_open.py`, `tests/test_gt_editor_ux.py`,
  `tests/test_gt_tab_order.py`, `tests/test_gt_class_panel_keyboard.py`,
  `tests/test_gt_editor_save_order.py`, `tests/test_gt_editor_speed_paths.py`; the legacy driver
  `test_the_trace_gt_editor_reproduces_the_goldens` in `tests/test_gt_goldens.py`
- Modify: `app/gui/shell_chrome.py` (`:6-10`, `:18-22` (the G3 width comment: "about 700 px
  instead of about 1040 px"), `:32`, `_STAGES :110-127`, `_EDITOR_STAGES :134-137`,
  `_enter_stage :320-322`, `populate :343-382`), `app/gui/shortcuts.py` (`:21`, `:29`, `:50`,
  `:65-70`, `:77-78`, `:146-158`, `:201-207`, `:212-225`, `:294`), `app/gui/panels.py`
  (`:5`, `:27`, `:31`, `:39`), `app/gui/main_window.py` (`:38`, `:158-160`, `:183`, `:370`,
  `:665`, `:672`, `:687`), `app/gui/window/editors.py` (`:1`, `:35-40`, `:111`, `:135-137`),
  `app/gui/window/body.py` (`:6`, `:144`, `:180`), `app/gui/window/navigator.py` (`:7`, `:69`,
  `:106`, `:175-177`), `app/gui/ui_panels.py:142`, `tests/test_import_smoke.py:80, :146`,
  `tests/test_right_panel_contract.py:6, :164`, `CLAUDE.md:72-73, :76` (stage list, scopes,
  `Panel` members; the Editors row names the three layers; the GT tracing row becomes "Class
  labels: the Classes layer of Correct, `app/gt/` core"), `README.md:112-117` (five stages)
- Test: every file in the "Edited, not ported" list of Design section 4, with these exact edits:
  `STAGE_NAMES = ["Library", "Generate", "Correct", "Measure", "Visualize"]`;
  `test_the_topbar_has_six_stages_with_alt_1_to_alt_6` → `..._five_stages_with_alt_1_to_alt_5`
  with `STAGE_KEYS == ("Alt+1", …, "Alt+5")`, Correct's tooltip
  `"Correct — Fix the model's mask (area) and skeleton (length), and paint class labels (GT for training), on one canvas. (Alt+3)"`,
  `alt_4` → `_global_shortcuts["go_to_stage"][2]`; `right_panel.count() == 3` (+ dashboards);
  `[int(p) for p in Panel] == [0, 1, 2]`; F1 sections `[["Correct", "Global", "Library"]]`;
  `scope_for_stage("Trace GT") is None`; `_PANELS` without `"gt_tracing"`; `receivers(
  editor.skeleton_saved) == 1` (the three pins become 1, see Interfaces); the 12 GT-editor tests
  of `test_shell_chrome_smoke.py` deleted; parametrizations lose `"Trace GT"`; the `"Alt+6"`
  labels of `test_window_library_focus.py` read `"Alt+5"`.

**Interfaces:**
- Consumes: WG-T5's page; WG-T6's `ImageManager.set_gt`.
- Produces: `shell_chrome.STAGES` of five; `shortcuts.STAGE_KEYS` of five, `SCOPES = (GLOBAL,
  LIBRARY, CORRECT)`; `Panel` of three; `MainWindow` without `gt_tracing_interface` and
  `toggle_gt_tracing` (`_on_gt_saved` and `export_gt_dataset` stay, bound to
  `editors.on_gt_saved` and the Correct editor); `editors()` = `{Panel.CORRECT: window.correct_editor}`;
  `correct_editor.skeleton_saved` connected to `editors.on_skeleton_saved(window, path)`
  (`image_manager.set_gt(stem, "skeleton", True)`, `library_index.refresh_status(stem)`,
  `metrics.refresh_if_current(stem)`): the Library and the metrics bar learn of a GT skeleton at
  once; `tests/test_shell_window_split_surface.py` loses `toggle_gt_tracing` and
  `gt_tracing_interface`, keeps `export_gt_dataset`, gains `on_skeleton_saved` and
  `gt_remove_button`, and `"Trace GT"` leaves the hot-path loop at `:408`.

- [ ] **Step 1: Failing tests.** Add to `tests/test_correct_main_window.py`:
```python
def test_the_topbar_has_five_stages_with_alt_1_to_alt_5(window):
    assert list(window.ribbon_buttons) == STAGE_NAMES == [s[0] for s in shell_chrome.STAGES]
    assert shortcuts.STAGE_KEYS == ("Alt+1", "Alt+2", "Alt+3", "Alt+4", "Alt+5") and len(shortcuts.STAGE_KEYS) == len(shell_chrome.STAGES)
    assert window.ribbon_buttons["Correct"].toolTip() == "Correct — Fix the model's mask (area) and skeleton (length), and paint class labels (GT for training), on one canvas. (Alt+3)"
    assert window.welcome.stage_labels() == STAGE_NAMES

def test_there_is_no_trace_gt_stage_scope_or_panel(window):
    assert "Trace GT" not in shortcuts.SCOPES and shortcuts.scope_for_stage("Trace GT") is None
    assert not hasattr(Panel, "GT_TRACING") and [int(p) for p in Panel] == [0, 1, 2]
    assert not hasattr(window, "gt_tracing_interface") and window._editors() == {Panel.CORRECT: window.correct_editor}
    assert window.right_panel.count() == len(Panel) + window.visualization_manager.dashboard_count()

def test_a_skeleton_save_reaches_the_library_and_the_metrics_bar_at_once(window_with_project):
    assert window.correct_editor.receivers(window.correct_editor.skeleton_saved) == 1
    ...save a skeleton...; assert window.image_manager.has_gt_skeleton(stem) and metrics_refreshed == [stem]

def test_the_deleted_editor_modules_are_gone():
    assert not Path("app/gui/raster_editor_base.py").exists() and not Path("app/gui/gt_tracing_interface.py").exists()
    assert not any("gt_tracing_interface" in p.read_text(encoding="utf-8") for p in Path("app").rglob("*.py"))
```
  Apply the edits listed under **Test** above.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_correct_main_window.py tests/test_shell_chrome_smoke.py tests/test_shell_info_shortcuts.py tests/test_foundation_runtime_panels_feedback.py -v`.
  Expected: FAIL (six stages, `Panel.GT_TRACING` present).

- [ ] **Step 3: Implement** Design section 4. Delete the two modules and the listed test files
  only after `pytest tests/test_correct_classes_*.py` is green (the ports exist since WG-T4 /
  WG-T5). Rewire `skeleton_saved` in `main_window.py:178-183` and add `editors.on_skeleton_saved`.

- [ ] **Step 4: Run, expect pass.** Run the **full suite** with `--junitxml` (this task touches
  30+ test files): `pytest --junitxml=tests/_wg_t11.xml -q`, then read the counts from the XML.
  Expected: 0 failures, 0 errors; `tests/test_import_smoke.py` green; `tests/test_right_panel_contract.py`
  self-test green with `Panel.VISUALIZATION` at `:164`.

- [ ] **Step 5: Ruff** on the task's files (`ruff check app/gui/shell_chrome.py app/gui/shortcuts.py app/gui/panels.py app/gui/main_window.py app/gui/window/ tests/test_correct_main_window.py …`).

- [ ] **Step 6: Commit (lead):** `feat(shell): one editor — the Trace GT stage, page and scope are removed; five stages, Alt+1 … Alt+5`
  (the lead refreshes the graph afterwards; `GtTracingInterface` and `RasterEditorBase` leave
  the god-node list).

---

### Task WG-T6: Library badge with three marks

**Model / effort:** implementer sonnet / high; reviewer sonnet / medium. **Lane:** L-library,
after WG-T3 (`ImageManager.mask_state / skeleton_state / has_gt_labels` exist). **Sci:** no.

**Files:**
- Modify: `app/gui/library_index.py` (`StatusOf :177`, `_BADGE :179`, `_badge_pixmap :182-220`,
  `status_text :223-230`, `LibraryItemDelegate.__init__ :242-254`, `status_icon :256-267`,
  `initStyleOption :269-277`, `LibraryIndex.__init__ :339-377`, `status_of :586-588`,
  `corrected_of :590-592` removed, `tooltip_for :594-600`),
  `app/gui/file_tree_manager.py:21-35` (providers: `mask_state=manager.mask_state`,
  `skeleton_state=manager.skeleton_state`, `has_labels=manager.has_gt_labels`), `README.md`
  (the Library badge paragraph)
- Test: Modify `tests/test_library_index.py:165-233`, `tests/test_shell_library_corrections.py:158-173`,
  `tests/test_shell_library_selection.py:111-118`, `tests/test_library_row_fit.py:143`,
  `tests/test_correct_main_window.py:608, :628-647, :694` (3-tuples); Create
  `tests/test_library_gt_badge.py`

**Interfaces:**
- Consumes: `ImageManager.mask_state`, `skeleton_state`, `has_gt_labels`, `set_gt`,
  `refresh_outputs`; `tokens.MASK / SKEL / ACCENT / TEXT_FAINT`.
- Produces: Design section 5's signatures; `status_text` is imported by WG-T8
  (`app/gui/window/metrics.py`).

- [ ] **Step 1: Failing tests** (`tests/test_library_gt_badge.py`):
```python
def test_the_badge_has_three_marks_and_the_shape_carries_the_state(app):
    pix = _badge_pixmap("gt", "prediction", True)
    assert pix.size() / pix.devicePixelRatio() == QSize(32, 12)
    ring, circle, diamond = opaque(_badge_pixmap("none", "none", False), 0), opaque(_badge_pixmap("prediction", "none", False), 0), opaque(_badge_pixmap("gt", "none", False), 0)
    assert circle > diamond > ring > 0                       # 63 px² circle, ~50 px² diamond, a thin ring (per mark box, dpr 2)
    assert dominant_colour(pix, 0) == tokens.MASK and dominant_colour(pix, 1) == tokens.SKEL and dominant_colour(pix, 2) == tokens.ACCENT
    assert opaque(_badge_pixmap("gt", "gt", True), 2) == opaque(_badge_pixmap("none", "none", True), 2)   # the labels diamond is the same shape

def test_status_text_words():
    assert status_text("gt", "prediction", False) == "Mask: GT · Skeleton: prediction · No classes"
    assert status_text("none", "none", True) == "No mask · No skeleton · Classes: traced"
    assert status_text("prediction", "gt", False) == "Mask: prediction · Skeleton: GT · No classes"

def test_the_tooltip_names_the_state_of_each_kind(index_with_providers):
    assert index.tooltip_for(STEM) == f"{STEM}\nMask: GT · Skeleton: prediction · No classes\n{path}"

def test_the_delegate_caches_one_icon_per_state_triple(index_with_providers): ... len(delegate._icons) == 2 after two distinct states, 2 after a repeat

def test_the_badge_follows_saves_removals_and_a_rescan(window_with_project):
    key = icon_key(window, stem); window.image_manager.set_gt(stem, "mask", True); window.library_index.refresh_status(stem); assert icon_key(window, stem) != key
    ...
```
  Edit the 2-tuple pins to 3-tuples (`("gt", "prediction", False)` etc.) and
  `test_tooltip_names_the_full_stem_path_and_status` to assert `"Mask: prediction" in text`;
  `test_shell_library_corrections.py:166-167`: `"Skeleton: GT" in tooltip` / not in.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_library_gt_badge.py tests/test_library_index.py tests/test_shell_library_corrections.py tests/test_shell_library_selection.py -v`.
  Expected: FAIL (2-tuple, 22 px, "Mask saved").

- [ ] **Step 3: Implement** Design section 5 (the diamond polygon of `:197-210` reused for
  every GT mark; `_BADGE = QSize(32, 12)`; boxes at x = 1.5 / 11.5 / 21.5; colours from
  `tokens` only, the colour-literal ratchet `tests/test_design_system_colour_literals.py` stays
  green).

- [ ] **Step 4: Run, expect pass.** Step 2 command plus `pytest tests/test_library_row_fit.py tests/test_correct_main_window.py tests/test_library_main_window.py tests/test_design_system_colour_literals.py -v`. Expected: all PASS.

- [ ] **Step 5: Ruff** on the task's files.

- [ ] **Step 6: Commit (lead):** `feat(library): three badge marks — mask, skeleton, class labels; the shape says prediction or GT`

---

### Task WG-T7: Generate writes predictions only

**Model / effort:** implementer sonnet / high; reviewer opus / medium. **Lane:** L-generate,
after WG-T2 (the window test opens a GT mask in Correct). **Sci:** no.

**Files:**
- Modify: `app/handlers/mask_generation_handler.py` (module docstring; `ask_overwrite_policy`;
  the `_confirm` call in `generate_masks` passes `existing.count`), `app/inference/mask_origin.py`
  (docstring only: the classifier is read by nothing), `docs/DATA_DICTIONARY.md` (the Generate
  prompt paragraph), `README.md` (the prompt sentence), `CHANGELOG.md`
- Test: Modify `tests/test_generation_handlers.py` (`test_overwrite_all_replaces_hand_traced_masks`
  and the label asserts), `tests/test_prompt_dialog_lifecycle.py` (texts), `tests/test_mask_origin_scan_fast.py`
  (the three "Overwrite all" texts); Create `tests/test_generate_predictions_only.py`

**Interfaces:**
- Consumes: `scan_existing_masks(...).count`; WG-T2's GT mask layer (window test).
- Produces: `ask_overwrite_policy(parent: QWidget | None, existing_count: int, total: int) -> OverwritePolicy | None`
  (`ConfirmOverwrite` alias follows); `OverwritePolicy` unchanged.

- [ ] **Step 1: Failing tests** (`tests/test_generate_predictions_only.py`; the handler fixtures
  of `tests/test_generation_handlers.py`, a stub model):
```python
def test_the_prompt_says_keep_or_replace_predictions(qtbot, prompt_box):
    box = capture(lambda: ask_overwrite_policy(None, 3, 10))
    assert box.windowTitle() == "Predictions already exist" and box.text() == "3 of 10 images already have a predicted mask."
    assert box.informativeText() == "Keep them and predict only the missing ones, or replace every prediction with a new one. Hand-corrected masks live in gt/ and are never touched."
    assert [b.text() for b in box.buttons()] == ["Keep predictions (predict missing only)", "Replace all predictions", "Cancel"]
    assert box.defaultButton().text() == "Keep predictions (predict missing only)"

def test_replace_all_predictions_never_touches_gt(handler, gt_project):     # replaces test_overwrite_all_replaces_hand_traced_masks
    before = tree_sha(layout.gt_dir); run(handler, policy=OverwritePolicy.OVERWRITE_ALL)
    assert tree_sha(layout.gt_dir) == before and png_colour_type(layout.mask_path("a")) == 0
    assert run_manifest(layout)["overwrite_policy"] == "overwrite_all"

def test_keep_predictions_generates_only_the_missing_ones(handler, project): ... report.skipped == existing stems

def test_an_rgba_file_in_mask_is_a_prediction_like_any_other(handler, project):   # old data out of scope: no hand-corrected category
    write_mask_file(layout.mask_path("a"), plane); run(handler, policy=OverwritePolicy.OVERWRITE_ALL)
    assert png_colour_type(layout.mask_path("a")) == 0 and prompt_texts[-1][0] == "2 of 2 images already have a predicted mask."

def test_replace_predictions_leaves_an_open_gt_mask_as_it_is(window_with_project, confirm_replace):   # Review Focus 5
    ...open a in Correct with gt/mask/a.png shown...; run Generate (Replace); window.on_outputs_changed()
    assert (plane(editor) == gt_plane).all() and editor.layers[MASK].status().label == "GT"
    editor.load_prediction(); assert (plane(editor) == new_prediction_plane).all()
```
  Edit `tests/test_generation_handlers.py` (`"Keep predictions" in clicked[0]`),
  `tests/test_prompt_dialog_lifecycle.py` (roles unchanged, texts), and the three
  `"Overwrite all"` texts of `tests/test_mask_origin_scan_fast.py` to the new labels.

- [ ] **Step 2: Run, expect failure.** Run: `pytest tests/test_generate_predictions_only.py tests/test_generation_handlers.py tests/test_prompt_dialog_lifecycle.py tests/test_mask_origin_scan_fast.py -v`.
  Expected: FAIL (old texts; `TypeError` on the new signature).

- [ ] **Step 3: Implement** Design section 6.

- [ ] **Step 4: Run, expect pass.** Step 2 command plus `pytest tests/test_generation_inference.py tests/test_robust_generation_handler_ux.py tests/test_correct_main_window.py tests/test_correct_changed_on_disk.py -v`. Expected: all PASS.

- [ ] **Step 5: Ruff** on the task's files.

- [ ] **Step 6: Commit (lead):** `feat(generate): predictions only — "Keep / Replace predictions"; gt/ is never read or written`

---

### Task WG-T8: Measure reads GT first for both metrics; `Source` column — **Sci**

**Model / effort:** implementer opus / xhigh; reviewer opus / high. **Lane:** L-measure, after
WG-T3, WG-T6 and W2-T11. **Sci:** yes, commit 2 only. Two commits; the owner is told before
commit 2 lands (the benchmark session reads `metrics.py`, which this task does not change).

**Files:**
- Create: `app/inference/measure_sources.py` (commit 1; `measure_inputs.py` is W2-T2's)
- Modify (commit 1): `app/data_processing/measurement_index.py` (`Measurement`,
  `_read_metric_csv`: optional `Source`)
- Modify (commit 2): `app/config.py` (`LENGTH_CSV_HEADERS`, `AREA_CSV_HEADERS` + `"Source"`),
  `app/inference/measure_run.py` (`LENGTH` / `AREA` parameters; `MeasureRun.__init__`
  keyword-only `sources`, `gt_report`; `compute` stamps `Source`; `_write_provenance`; the
  W2-T2 record gets the resolved paths), `app/handlers/skeleton_handler.py` (`calculate_root_length`,
  `calculate_root_area`, `_measure`, `_on_finished`), `app/data_processing/measurement_index.py`
  (`outdated`, `_table_outdated`, `gt_skip_reason`), `app/gui/window/metrics.py` (the two
  constants, `image_has_gt_skeleton`, the status strings, `refresh`), `app/gui/metrics_bar.py`
  (`_LENGTH_INPUT` / `_AREA_INPUT`, `area_note` kwarg beside `length_note`), `app/gui/
  stage_summary.py` (`measure_text`, `input_freshness`), `app/gui/stage_status.py` (`check`),
  `app/gui/ui_panels.py` (the two Measure tooltips: "Measures the GT skeleton where one is
  valid, else the prediction"), `docs/DATA_DICTIONARY.md` (CSV table rows, a `Source` row after
  `Error`, the "not read by Measure" sentences, the provenance row), `README.md` (pipeline
  note, the Measure paragraph), `CLAUDE.md` (pipeline lines 12-13), `CHANGELOG.md`
  ("### Scientific output changes")
- Test: Create `tests/test_measure_sources.py` (commit 1), `tests/test_measure_gt_first.py`
  (commit 2); Modify (commit 2) `tests/test_foundation_core_config.py` (the header lists),
  `tests/test_measure_core.py` (the inlined legacy writer gets `"Source": "prediction"`; the
  real-sample test compares the first seven columns byte for byte and asserts the eighth is
  `prediction` everywhere), `tests/test_shell_info_measurement_index.py`,
  `tests/test_shell_library_corrections.py`, `tests/test_shell_metrics_per_metric_staleness.py`,
  `tests/test_shell_window_split_surface.py` (the two constant prefixes), `tests/test_shell_window_parts.py`
  (`set_metrics` kwargs gain `area_note`), `tests/test_shell_stage_summary.py`,
  `tests/test_window_feedback.py`, `tests/test_library_main_window.py`, `tests/test_correct_main_window.py`
  ("Mask ready" texts), `tests/test_measure_without_image.py` (W2-T11: GT files counted),
  `tests/test_fair_docs.py` (green once the dictionary has the row)

**Interfaces:**
- Consumes: `ProjectLayout.scan_gt_masks / scan_gt_skeletons / gt_mask_path / gt_skeleton_path /
  scan_images`; `app.gt.files.png_size / image_size`; `app.editing.thinning.is_generated_as_opened`;
  `app.provenance.file_sha256`; W2-T2's `input_key`, `write_inputs_record`, `load_inputs_record`,
  `scan_stamps`, `diff_inputs`; W2-T11's `split_by_image`, `MeasureRun(..., without_image=)`,
  `Freshness.without_image`; `library_index.status_text`; `ImageManager.mask_state /
  skeleton_state / has_gt_labels`.
- Produces: Design section 7's `measure_sources` API; `MeasureRun(..., *, sources=None,
  gt_report=None)`; `Measurement(length_mm, area_mm2, error, length_source=None, area_source=None)`;
  `MeasurementIndex.gt_skip_reason(stem) -> dict[str, str]`; `window.metrics.GT_NOT_USED`,
  `GT_STATUS_TIP`; `MetricsBar.set_metrics(..., area_note=None)`; `stage_summary.measure_text(
  skeletons, masks, length=None, area=None, *, gt_skeletons=0, gt_masks=0)`.

- [ ] **Step 1 (commit 1): Failing tests** (`tests/test_measure_sources.py`; fixture `gt_project`
  with photos 640 x 480: stems `p` (prediction only), `g` (prediction + valid GT), `o` (GT only),
  `s` (prediction + 341 x 256 GT skeleton), `u` (prediction + GT skeleton equal to
  `thin_raster(prediction, (640, 480))`), `x` (prediction + undecodable GT), `m` (GT mask equal to
  the prediction mask)):
```python
def test_gt_wins_when_valid_and_a_gt_only_stem_gets_a_row(gt_project):
    sources, report = resolve_length_sources(layout, images, layout.scan_skeletons(), layout.scan_gt_skeletons())
    assert sources["g"] == MeasureSource("g", layout.gt_skeleton_path("g"), GT, layout.gt_skeleton_path("g"))
    assert sources["o"].source == GT and sources["p"] == MeasureSource("p", layout.skeleton_path("p"), PREDICTION)
    assert report.folder == "gt/skeletons" and set(report.files) == {"g", "o"} and report.n_used == 2

def test_wrong_size_unedited_and_unreadable_gt_skeletons_fall_back_with_a_reason(gt_project):   # Review Focus 1
    sources, report = resolve_length_sources(layout, images, layout.scan_skeletons(), layout.scan_gt_skeletons())
    assert (sources["s"].source, sources["s"].reason, sources["s"].path) == (PREDICTION, SIZE, layout.skeleton_path("s"))
    assert sources["u"].reason == UNEDITED and sources["x"].reason == UNREADABLE
    assert set(report.skipped) == {("s", SIZE), ("u", UNEDITED), ("x", UNREADABLE)}

def test_a_gt_mask_is_used_whatever_its_pixels_and_an_unreadable_one_is_not(gt_project):
    sources, report = resolve_area_sources(layout, layout.scan_masks(extensions=(".png",)), layout.scan_gt_masks())
    assert sources["m"].source == GT and sources["m"].reason == "" and sources["x"].reason == UNREADABLE

def test_stems_compare_without_case(gt_project): ...  # gt/skeletons/G.png for image g.jpg resolves to one row

def test_the_resolver_is_qt_free(): ...  # subprocess probe

def test_measurement_reads_an_optional_source_column(tmp_path):
    assert Measurement(18.77, 16.41, None) == Measurement(18.77, 16.41, None, None, None)
    ...CSV without Source...; assert index.lookup("a").length_source is None
    ...CSV with Source gt...; assert index.lookup("a").length_source == "gt"
```
- [ ] **Step 2: Run, expect failure.** `pytest tests/test_measure_sources.py -v`: FAIL (`ImportError`).
- [ ] **Step 3: Implement commit 1** (Design 7: the resolver; `Measurement` fields;
  `_read_metric_csv` reads `Source` when the header has it). Nothing wired.
- [ ] **Step 4: Run, expect pass.** `pytest tests/test_measure_sources.py tests/test_measure_core.py tests/test_shell_info_measurement_index.py tests/test_foundation_core_config.py tests/test_layering_contract.py tests/test_measure_inputs_record.py -v` (the last is W2-T2's file, whichever name it has): all PASS; the two byte-identity tests untouched and green.
- [ ] **Step 5: Ruff; commit 1 (lead):** `feat(measure): resolve GT-first sources for length and area (not wired)`

- [ ] **Step 6 (commit 2): Failing tests** (`tests/test_measure_gt_first.py`):
```python
def test_the_csvs_gain_a_last_source_column():
    assert LENGTH_CSV_HEADERS == ["Image", "Tube", "Position", "Date", "Time", "Length (mm)", "Error", "Source"]
    assert AREA_CSV_HEADERS == ["Image", "Tube", "Position", "Date", "Time", "Area (mm²)", "Error", "Source"]

def test_rows_say_gt_or_prediction_and_values_follow_the_measured_file(gt_project):
    rows = run_length(gt_project)
    assert rows["g"]["Source"] == "gt" and rows["g"]["Length (mm)"] == metrics.length_row("g", layout.gt_skeleton_path("g"))["Length (mm)"]
    assert rows["s"]["Source"] == "prediction" and rows["s"]["Length (mm)"] == metrics.length_row("s", layout.skeleton_path("s"))["Length (mm)"]
    assert rows["o"]["Source"] == "gt" and rows["p"]["Source"] == "prediction"

def test_the_synthetic_goldens_corrections_measure_their_recorded_lengths(synth_project_with_golden_corrections):
    # the two correct_goldens corrections seeded as gt/skeletons/ through layout.gt_skeleton_path
    assert rows[SYNTH_STEM]["Length (mm)"] == 28.86 and rows[SYNTH_STEM]["Source"] == "gt"      # length_mm_if_measured_informational

def test_prediction_rows_are_byte_identical_to_the_legacy_writer_in_the_first_seven_columns(...)   # the adapted test_measure_core pattern
def test_gt_files_without_an_image_are_skipped_and_recorded(gt_project): ...  # W2-T11's split_by_image on gt/: inputs.without_image names them
def test_the_records_list_gt_files_skips_and_counts(gt_project):
    rec = sidecar(csv); assert rec["parameters"]["source_rule"] == "gt-first" and rec["inputs"]["gt"]["n_used"] == 2
    assert rec["inputs"]["gt"]["skipped"] == [{"stem": "s", "reason": "size"}, {"stem": "u", "reason": "unedited"}, {"stem": "x", "reason": "unreadable"}]
    assert (rec["outputs"]["n_gt"], rec["outputs"]["n_prediction"]) == (2, 5) and set(rec["inputs"]["gt"]["files"]) == {"g", "o"}
    assert input_key(layout.gt_skeleton_path("g"), root) in load_inputs_record(csv).inputs     # W2-T2's record names the measured file

def test_outdated_follows_the_measured_file(gt_project):
    index = MeasurementIndex(root); assert index.outdated("g") == {"length": False, "area": False}
    rewrite(layout.gt_skeleton_path("g")); index.invalidate(); assert index.outdated("g")["length"] is True          # measured GT file changed
    write_gt_skeleton("p"); index.invalidate(); assert index.outdated("p")["length"] is True                            # new GT after the CSV
    os.remove(layout.gt_skeleton_path("o")); index.invalidate(); assert index.outdated("o")["length"] is True          # GT gone
    fresh(); rewrite(layout.skeleton_path("g")); index.invalidate(); assert index.outdated("g") == {"length": False, "area": False}   # a regenerated prediction under a gt row
    write_gt_mask("p"); index.invalidate(); assert index.outdated("p")["area"] is True and index.outdated("p")["length"] is False
    assert index.gt_skip_reason("s") == {"length": "size"}

def test_the_metrics_bar_names_the_source_and_the_skip_reason(window_with_measured_project):
    assert "gt/skeletons/g.png (GT)" in length_tooltip("g") and "skeletons/p_fake.png (prediction)" in length_tooltip("p")
    assert length_note("s") == "A GT skeleton exists (gt/skeletons/) but was not used: it is not the photo's size. Measure read the prediction."
    assert area_note("x") == "A GT mask exists (gt/mask/) but was not used: it could not be read. Measure read the prediction."
    assert status("g") == "Mask: prediction · Skeleton: GT · No classes"

def test_the_status_line_counts_gt_files(): assert measure_text(20, 20, Freshness(20, True), Freshness(20, False), gt_skeletons=2, gt_masks=3) == "20 skeletons (2 GT): measured, current · 20 masks (3 GT): not measured yet"
def test_the_end_of_run_notice_names_skipped_gt_files(handler_run): assert notice == ("3 GT skeletons not used: 1 not the photo's size, 1 equal to the prediction, 1 unreadable (named in root_lengths.csv.provenance.json)", "warn")
def test_a_project_with_gt_files_and_no_predictions_is_measured_into_the_prediction_folder(...): csv at skeletons/root_lengths.csv; rows all gt
```
  Edit the listed existing tests (headers, legacy writer `"Source": "prediction"`, the `set_metrics`
  kwargs dict gains `area_note`, the `split_surface` constants, "Mask ready" →
  `"Mask: prediction · No skeleton · No classes"`).
- [ ] **Step 7: Run, expect failure.** `pytest tests/test_measure_gt_first.py tests/test_foundation_core_config.py -v`: FAIL (seven headers).
- [ ] **Step 8: Implement commit 2** (Design 7 wiring, records, freshness, texts, docs).
  Then the CHANGELOG entry: run Measure (both metrics) before and after on (a) the synthetic
  `tests/data` project of `test_measure_core` and the goldens' synthetic project with its two
  corrections seeded as GT, and (b) the audit's 3000-pair real sample
  (`.claude/handoff/issue-audit-2026-10-12/scripts/length/sample_real.py`, read-only on
  `D:\2024 EF image data`); paste the row diff (expected: (a) `Source` added, the two seeded
  rows read 28.86 / 60.83 mm with `gt`, nothing else changes; (b) `Source` = `prediction` on
  every row, no value changes) under "### Scientific output changes".
- [ ] **Step 9: Run, expect pass.** `pytest tests/test_measure_gt_first.py tests/test_measure_sources.py tests/test_measure_core.py tests/test_measure_without_image.py tests/test_foundation_core_config.py tests/test_fair_docs.py tests/test_shell_info_measurement_index.py tests/test_shell_library_corrections.py tests/test_shell_metrics_per_metric_staleness.py tests/test_shell_window_split_surface.py tests/test_shell_window_parts.py tests/test_shell_stage_summary.py tests/test_window_feedback.py tests/test_library_main_window.py tests/test_correct_main_window.py tests/test_regression.py tests/test_viz_data_processor.py tests/test_measure_threads.py -v`: all PASS (the dashboards read by column name and keep `Source`).
- [ ] **Step 10: Ruff; commit 2 (lead):** `feat(measure)!: GT-first for length and area; Source column; records and freshness follow the measured file`

---

### Task WG-T9: Restore retired classes; per-class pixel counts and `exif_orientation` in the manifest

**Model / effort:** implementer sonnet / high; reviewer sonnet / medium. **Lane:** L-correct,
after WG-T11. **Sci:** the export manifest gains columns (no mask pixel, no CSV value).

**Files:**
- Modify: `app/gt/registry.py` (`Registry.restore` after `retire :130`), `app/gui/class_editor_dialog.py`
  (a "Retired" group below the rows, `:331` builds from `active()`; a Restore button per retired
  class; `_free_add_color :238-249` unchanged), `app/gt/export.py` (`_MANIFEST_FIELDS :48`;
  the per-image row `:300-307`), `README.md` (classes section: Restore; the manifest columns),
  `docs/DATA_DICTIONARY.md` (export manifest columns), `CHANGELOG.md`
- Test: Modify `tests/test_gt_registry.py`, `tests/test_gt_widgets.py`, `tests/test_gt_export.py`
  (manifest columns); Create `tests/test_gt_restore_and_counts.py`

**Interfaces:**
- Consumes: `Registry`, `ClassEditorDialog.result_registry`, `export_dataset`, `remap_ids`,
  W2-T10's EXIF policy (orientation is read, never applied: the column records the tag).
- Produces: `Registry.restore(key: str) -> Registry` (`RegistryError` when the key is unknown
  or not retired; the entry keeps its position, id and colour); `ClassEditorDialog.restore_buttons:
  dict[str, QPushButton]`; manifest columns in order `stem, source_image, split, tube, position,
  date, classes_present, exif_orientation, px_background, px_<key> (labelled classes in registry
  order), px_ignore`, counts taken from the remapped ids; `exif_orientation` = the source
  image's EXIF tag 0x0112 as an int 1-8, else blank.

- [ ] **Step 1: Failing tests** (`tests/test_gt_restore_and_counts.py`):
```python
def test_restore_puts_a_retired_class_back_with_its_id_colour_and_place():
    reg = default_registry().retire("nodule"); back = reg.restore("nodule")
    assert back.by_key("nodule") == default_registry().by_key("nodule") and [c.key for c in back.classes] == [c.key for c in default_registry().classes]
    with pytest.raises(RegistryError): reg.restore("root")
    with pytest.raises(RegistryError): reg.restore("nothing")

def test_the_class_editor_lists_retired_classes_with_a_restore_button(app):
    dialog = ClassEditorDialog(default_registry().retire("nodule"))
    assert list(dialog.restore_buttons) == ["nodule"]; dialog.restore_buttons["nodule"].click(); dialog.accept()
    assert not dialog.result_registry().by_key("nodule").retired

def test_export_after_restore_gives_the_class_its_own_id_again(gt_project_with_nodules, tmp_path):
    ...retired export: nodule pixels are 255...; ...restored export: nodule pixels are 2...

def test_the_manifest_has_per_class_pixel_counts_and_exif_orientation(gt_project_with_nodules, tmp_path):
    rows = manifest(export_dataset(...))
    assert list(rows[0]) == ["stem", "source_image", "split", "tube", "position", "date", "classes_present", "exif_orientation", "px_background", "px_root", "px_nodule", "px_root_tip", "px_ignore"]
    assert int(rows[0]["px_nodule"]) == 885 and sum(int(rows[0][c]) for c in rows[0] if c.startswith("px_")) == 640 * 480
    assert rows[0]["exif_orientation"] == "" and rows[1]["exif_orientation"] == "6"    # the second image carries a rotated-JPEG tag
```
- [ ] **Step 2: Run, expect failure.** `pytest tests/test_gt_restore_and_counts.py -v`: FAIL (`no attribute 'restore'`).
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run, expect pass.** `pytest tests/test_gt_restore_and_counts.py tests/test_gt_registry.py tests/test_gt_widgets.py tests/test_gt_export.py tests/test_export_dataset_dialog.py tests/test_robust_gt_export_atomic.py tests/test_robust_gt_export_size_mismatch.py -v`: all PASS.
- [ ] **Step 5: Ruff** on the task's files.
- [ ] **Step 6: Commit (lead):** `feat(gt): restore retired classes; per-class pixel counts and exif_orientation in the export manifest`

---

### Task WG-T10: Vocabulary and documents

**Model / effort:** implementer sonnet / medium; reviewer sonnet / medium. **Lane:** L-correct,
after WG-T11 and WG-T8. **Sci:** no.

**Files:**
- Modify: `README.md` (`:18-22` pipeline note; `:112-117` five stages; `:122` new "Which
  layer?" table: fix the model's mask → Mask layer (area); fix the skeleton → Skeleton layer
  (length); make labels to train a model → Classes layer; every save goes to `gt/`, predictions
  are never overwritten; `:128-133` file table with `gt/mask/` and `gt/skeletons/`, no
  `skeleton_corrections/` row; `:185-188`; `:207`; `:245-290` the Trace GT section becomes
  "Class labels (GT for training)" under Correct; `:352`), `docs/DATA_DICTIONARY.md` (`:15-20`
  tree; `:130-141` prediction vs GT masks; `:154-180` GT section),
  `docs/MODELS.md:42`, `CLAUDE.md` (`:12-13` pipeline; `:71` Handlers row: "Generate never
  touches gt/"; `:73` Editors row: three layers, keys M / L / G; `:76` the class-labels row),
  `docs/KNOWN_ISSUES.md` (#5 FR-03 and the G1-G4, M4, L1, L5 rows: the lead moves them to
  "Resolved history" with the commit hashes; this task writes the one-line texts),
  `app/gui/ui_panels.py:215-220` ("Independent of masks" sentence → "Mask → area, skeleton →
  length: the layers are independent"), `app/gui/export_dataset_dialog.py:47-49, :82, :168-170,
  :215-216` ("Trace GT stage" → "the Classes layer of Correct"; "GT tracing" → "class labels";
  "class masks" → "class-id label images (masks/)"), `app/gui/shell_chrome.py:355-361` (export
  tooltip "Export the labelled images and their class-id label images as a training dataset")
- Test: Create `tests/test_wording_guards.py`; Modify `tests/test_fair_docs.py` (README table
  rows and the three `gt/` paths present), `tests/test_gt_export_dialog_wording.py:52-56, :72`

**Interfaces:** none produced; consumes the final strings of WG-T5 … WG-T8.

- [ ] **Step 1: Failing tests** (`tests/test_wording_guards.py`, AST string-constant scan of
  `app/**/*.py` as in `tests/test_right_panel_contract.py`):
```python
def test_no_hand_traced_under_app(): assert hits(r"hand-traced") == []
def test_no_trace_gt_stage_name_under_app(): assert hits(r"\bTrace GT\b") == []
def test_skeleton_corrections_is_named_only_by_the_dead_constant_and_the_export_guard():
    assert {p.name for p in files_with(r"skeleton_corrections")} == {"config.py", "export.py"}   # both go with W5-T5
def test_the_readme_has_the_which_layer_table():
    text = README.read_text(encoding="utf-8")
    assert "| Fix the model's mask of an image | Mask layer (area) |" in text and "gt/mask/<stem>.png" in text and "skeleton_corrections" not in text
def test_the_data_dictionary_documents_the_gt_folders():
    assert all(s in DATA_DICTIONARY.read_text(encoding="utf-8") for s in ("`gt/mask/<stem>.png`", "`gt/skeletons/<stem>.png`", "`Source`"))
```
- [ ] **Step 2: Run, expect failure.** `pytest tests/test_wording_guards.py -v`: FAIL (README).
- [ ] **Step 3: Write** the documents and the four string edits.
- [ ] **Step 4: Run, expect pass.** `pytest tests/test_wording_guards.py tests/test_fair_docs.py tests/test_gt_export_dialog_wording.py tests/test_export_dataset_dialog.py tests/test_shell_chrome_topbar.py -v`: all PASS.
- [ ] **Step 5: Ruff** on the touched `.py` files.
- [ ] **Step 6: Commit (lead):** `docs: prediction and GT vocabulary; one editor with three layers; the gt/ folders`

---

## Self-review (run 2026-10-25, revised the same day after the owner's answers)

**Spec coverage** (every owner decision of the brief and the three chat answers):
- O23 one editor → WG-T4, WG-T5, WG-T11. O26 "GT" kept, third Library mark = class labels
  (owner answer a) → Design 5, WG-T6. GT folder → WG-T1 … WG-T3. Layer keys M / L / G, digits
  for classes (owner answer b) → Design 3.3, WG-T5. Old data out of scope (owner answer c) →
  Design 2: no migration, no legacy state, generic validation only (WG-T2, WG-T8).
- O24: Restore → WG-T9; Remove GT → `remove_gt` (WG-T1) + the page action (WG-T5); GT in the
  Library tree → WG-T6; per-class pixel counts → WG-T9; no COCO: not built.
- O2 → `CorrectSession.save` dirty-only + the mask equality rule (WG-T2), W1-T3's skeleton rule
  on the GT path (WG-T3). O9 → the empty GT mask (WG-T2) and Generate's prompt (WG-T7). O1 →
  WG-T8 (both metrics, `Source`). O13 → only its Measure half survives the owner's answer (a GT
  skeleton that is not the photo's size is never used, reason recorded); the Library / Correct
  flag UX is not built. O19 → the classes layer takes `file_stamps` / `write_stamps` /
  `matches_disk` and the session's reload (WG-T4). O18 → left to W4-T1, whose Step 0
  assumptions this plan meets. O21 → the read-only test uses "a file where the folder should
  be"; no OS call. O7 → every scan keyed by `stem_key` (WG-T1, WG-T8 test).
- The multi-class spec's formats: untouched (`gt/<stem>.png`, `classes.json`, export layout
  except the added manifest columns).

**Reconciliation with the tree:** W1-T2's `save_gate` / `DiskGuardedLayer`, W1-T3's
`app/editing/thinning.py`, W1-T4's `write_stamps` / `matches_disk`, W1-T5's `ImageScan` and
keyed scans, W2-T2's `measure_inputs.py` (so the resolver is `measure_sources.py`), W2-T11's
`split_by_image` / `without_image`, W3-T4's stretched prediction (the equality rule compares
with the prediction as opened) are named in Design 0 and consumed by name in the tasks; line
numbers were re-read at `4850564` (`editor.py` 445, `mask_layer.py` 434, `mask_document.py`
349, `skeleton_document.py` 420, `session.py` 408, `layer.py` 338).

**Step scan:** every test step names its assertions; every code step names the signature or the
Design subsection that holds it. `replay_legacy` / `replay_layer` are test helpers whose ops
are data (`GT_OPS`).

**Type consistency:** `MeasureSource`, `GtReport`, `SIZE` / `UNEDITED` / `UNREADABLE` (WG-T8)
match Design 7; `ClassesLayer` / `ClassesDocument` names are used in WG-T4, WG-T5, WG-T11;
`set_gt(name, kind, present)` with kinds `"mask" | "skeleton" | "labels"` everywhere;
`LayerStatus` labels ("Prediction", "GT", "Read-only", "Mask not shown") are the same in
Design 3.4 and the tests; `status_text` words of Design 5 are reused by WG-T8; `GT` /
`PREDICTION` constants of `app/gui/skeleton_document.py` (WG-T3) and of `measure_sources.py`
(WG-T8) hold the same strings.

**Review Focus:** five items, each pinned in its owning task.

**Proportion:** about 2,000 lines for twelve tasks over about 60 files (the Design section is
the Wave G spec); code appears only as signatures, test bodies and data.

## Changes to the master

1. **Dropped (owner, 2026-10-25, "old data will never be used"):** the one-time migration
   (`app/gt/migration.py`, `gt/_sprouts_migration.json`, the copy of RGBA masks and
   `skeleton_corrections/`, "Generate refuses while incomplete"), the "Older save" state, O13's
   flag UX (W2-T7's folded content) and `skeleton_corrections/` as a readable location. The
   master's rows O13 (flag half), the WG-T1 "copy of legacy hand-corrected files" wording, the
   WG-T3 "flags legacy 341 x 256 GT skeletons" wording, the WG-T7 "migration before a run"
   wording and Review Focus item 1 ("a project saved before this build") are void. No task ID
   disappears: WG-T1, WG-T3, WG-T7 shrink; WG-T1 and WG-T7 drop to sonnet / high.
2. **Re-cut:** WG-T5 (provisional "one editor … stage removed") is split into **WG-T5** (the
   classes layer inside Correct; Trace GT still present, its two buttons moved) and **WG-T11**
   (stage, panel, scope removal; module deletions; test port). WG-T6 moves **after WG-T3** (it
   needs `ImageManager.mask_state / skeleton_state`); WG-T7 after WG-T2 (its window test);
   WG-T8 also after WG-T6 (it imports `library_index.status_text`). WG-T1 absorbs the
   `ImageManager` GT sets (the master listed them under WG-T6).
3. **Ownership:** `app/gui/image_manager.py` is L-correct's for WG-T1 → WG-T3 and L-library's
   only for WG-T6 (after WG-T3). `app/gui/file_tree_manager.py`, `app/gui/display_controller.py`
   and `app/gui/skeleton_document.py` (none in the master's table) belong to L-correct
   (WG-T2 / WG-T3), `file_tree_manager.py:21-35` to L-library in WG-T6 after that.
   `app/gui/main_window.py` is L-correct's for WG-T5 and WG-T11 (the editor wiring block,
   `export_gt_dataset`, the GT editor lines); WG-T7 and WG-T8 do not touch it: the
   `skeleton_saved` wiring the master gave WG-T8 is done by **WG-T11**, and WG-T8 only adds the
   `outdated` rule behind it. `app/gui/window/metrics.py` (`image_has_correction` →
   `image_has_gt_skeleton`) is edited by WG-T3, not L-measure. `config.SKELETON_CORRECTIONS_DIRNAME`
   stays (dead) until W5-T5: `config.py` is L-measure's and W3-T2 edits it in the same phase.
4. **Goldens:** two non-pixel entries of `tests/data/correct_goldens.json` change
   (`mask_clear.saved` of the model case, O9, in WG-T2; `start.skeleton_source` label in
   WG-T3). The master's "only the path entry changes" did not foresee O9's empty GT file.
5. **Names:** the master's `GtDocument` / `GtLayer` are `ClassesDocument` / `ClassesLayer`;
   the resolver is `app/inference/measure_sources.py` (`measure_inputs.py` is W2-T2's record,
   committed `a3dc3c7`; the master's "`app/inference/length_inputs.py` (or the name WG-T8 gives
   the resolver)" is superseded); W5-T4 seeds corrections through `ProjectLayout.gt_skeleton_path`,
   as `w0-w5-docs-repo.md` already says; W5-T5's compat-shim list gains
   `config.SKELETON_CORRECTIONS_DIRNAME`, `export._PROTECTED_DIRS`'s entry and
   `app/inference/mask_origin.py`'s classifier.
6. **W4-T1 interplay:** WG-T5 leaves the top-bar `clear_button` on the Correct page (W4-T1
   removes it) and ends with `editor.py` ≤ 440 lines (it is 445 after W4-T5); `CorrectSession.
   save`'s fallback is removed in WG-T2, so W4-T1's conditional Step 4 is skipped. **W3-T4
   interplay:** its Step 0 reads WG-T2's `source` rule (GT else prediction); WG-T2's equality
   rule compares with the prediction plane as opened, so a stretched prediction compares at the
   photo's size.
7. **Science commits:** WG-T8 is two commits (resolver unwired; then `Source` + GT-first);
   on the owner's current data no `gt/` exists and `skeleton_corrections/` is no longer read, so
   commit 2 changes no value there; WG-T9's manifest columns are a dataset-export change.
8. **Keys:** the Correct layer keys move from `1` / `2` / `X` to `M` / `L` / `G` / `X`
   (Design 3.3, owner answer b); the master's row for O14 is satisfied by the key texts and
   `README.md`'s "Which layer?" table (WG-T5, WG-T10).
9. **Lane order (section 5 of the master), Phase B:** `L-correct: WG-T1 → WG-T2 → WG-T3 →
   WG-T4 → WG-T5 → WG-T11 → WG-T9 → WG-T10 → W2-T8a`; `L-library: WG-T6 (after WG-T3)`;
   `L-generate: WG-T7 (after WG-T2) → W6-T3`; `L-measure: WG-T8 (after WG-T3, WG-T6, W2-T11) →
   W2-T4 → …`. Phase C unchanged.
