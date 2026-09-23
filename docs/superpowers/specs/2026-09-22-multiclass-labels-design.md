# Multi-class Ground-Truth Tracing — Design Spec

**Status:** design v3 approved in chat 2026-09-22 (replaces the bit-packed `labels/` drafts);
written spec pending user review.
**Branch:** `feat/multiclass-labels`, rebased on `1fb1b13` (tracing-editor overhaul, skeleton dock fix, opaque canvas background).
**Worktree:** `D:\Root-Mask-and-Skeletons-gt`.

## Purpose

Hand-traced, multi-class **ground truth** for training segmentation models that distinguish
roots from nodules (and other user-defined classes). The output is a dataset, not
measurements.

## Hard rules (from the user)

1. **Independent of every ML model in the app.** A GT tracing never starts from the ResNet
   mask (`mask/`), never reads Pix2Pix skeletons (`skeletons/`, `skeleton_corrections/`),
   and no GT output is derived from model output. GT code never writes to `mask/` or
   `skeletons/`.
2. **Trace, don't quantify.** Nodules (and every other class) are painted so their presence
   and extent are in the dataset. No per-class count/area/length metrics, no CSV of numbers.
3. **Labels are masks; one object per image.** Every class, Root included, lives in the same
   per-image file. No per-class files, no derived copies, no sync rules.
4. The existing pipeline (Generate → Correct Mask → Correct Skeleton → Measure → Visualize)
   is unchanged. "Correct Mask" keeps editing ResNet output in `mask/`.

---

## 1. Storage

Project directory = the folder holding the images (`image_manager.original_folder`, the same
convention as `mask/`).

| Path | Content |
|---|---|
| `gt/<stem>.png` | **The GT tracing.** 8-bit palette PNG, single channel. Pixel value = class id. |
| `gt/classes.json` | Project class registry (§2). |

### 1.1 Pixel values

- `0` = background.
- `1`–`254` = classes from the registry. Ids are never reused within a project.
- `255` = **Ignore** (scratches, droplets, occlusions, "unsure"). Built in; the standard void
  label that training code excludes from the loss (`ignore_index=255`).
- One class per pixel. Painting a class overwrites whatever was under it.

### 1.2 Palette and metadata

- Palette entry *i* = the display colour of class id *i* (unknown ids → grey `#808080`);
  entry 0 is marked transparent. Any image viewer shows the tracing in class colours.
- PNG text chunk `sprouts_gt` holds JSON:
  `{"v": 1, "classes": {"1": "root", "2": "nodule", "3": "root_tip", "255": "ignore"},
    "reviewed": ["root", "nodule"]}`.
  `classes` makes each file self-describing (id → key at save time). `reviewed` lists the
  class keys the annotator has checked on this image (§3.4).

### 1.3 Semantics

- **File exists ⇔ image annotated.** Saving an image with no painted pixels still writes the
  file: "checked, nothing present" is a valid negative example. There is no "empty = delete"
  rule (unlike `mask/`). Deleting a GT file is not a v1 feature.
- Read/write with **Pillow** (mode `P`, text chunk via `PngInfo`) through Python file handles
  (non-ASCII Windows paths work). Writes are atomic: `<path>.tmp` then `os.replace`.
- Verified in the venv: a palette PNG round-trips exact class ids and the text chunk.

### 1.4 Loading and id alignment

`read_tracing` returns the ids plus the file's own id→key map. `align_to_registry` converts
the ids into the current registry by **key**:

- file id with a key the registry knows → that class's registry id;
- file id whose key the registry does not know, or an id missing from the file's map and
  from the registry → `255` (Ignore), with a warning naming the key/id;
- `0` stays `0`.

It returns `(tracing, warnings, changed)`. Within one project ids never change, so this is a
no-op except for files copied between projects.

---

## 2. Class registry (`gt/classes.json`)

```json
{"version": 1, "classes": [
 {"id": 1,   "key": "root",     "name": "Root",     "color": "#5fd6a0", "retired": false},
 {"id": 2,   "key": "nodule",   "name": "Nodule",   "color": "#ff7eb6", "retired": false},
 {"id": 3,   "key": "root_tip", "name": "Root tip", "color": "#ffd166", "retired": false},
 {"id": 255, "key": "ignore",   "name": "Ignore",   "color": "#8a8f98", "retired": false}
]}
```

- These are the defaults (`app/config.py`). `load_registry` returns them when the file is
  missing; the file is written on the first GT save or class edit.
- Order in the file = display order; Ignore is always last.
- **Validation (`RegistryError`):** `version == 1`; `key` matches `^[a-z][a-z0-9_]{0,31}$`
  and is unique; `name` non-empty, ≤ 40 chars; `color` matches `^#[0-9a-fA-F]{6}$`;
  colours unique across all classes, retired included (the editor maps colours back to ids,
  so a retired class sharing a colour would be silently converted);
  `id` unique, 1–254 for normal classes; exactly one entry with `key == "ignore"`,
  `id == 255`, `retired == false`.
- **Mutability:** `name`, `color` editable; `id`, `key` fixed. Delete = retire (hidden in the
  editor, id reserved, pixels untouched, exported as Ignore). Ignore cannot be retired.
  Adding a class takes the lowest id in 1–254 never used by any class (retired included).
- Root is a normal default class with no special rules.

---

## 3. Editor — "Trace GT" stage (Track E)

### 3.1 Placement

- New shell stage **"Trace GT"** directly after Library (icon `layers`, tooltip "Paint
  ground-truth classes for a segmentation dataset."). Seven stages again.
- Its right-panel page is a new `GtTracingInterface`, added to `right_panel` at index 4
  (indexes 0–3 unchanged); `switch_right_panel("gt_tracing")`.
- Stage action bar: **Classes…**, **Export dataset…** (Save stays in the editor dock, as in
  Correct Mask; its label reads "Save GT").
- App lifecycle: closing the window, opening another folder, and leaving the stage all run the
  GT editor's unsaved-changes prompt, exactly like the mask editor today.
- Correct Mask is untouched.

### 3.2 `GtTracingInterface` (`app/gui/gt_tracing_interface.py`)

Subclass of the committed `MaskTracingInterface`, so it inherits the canvas, rail, dock,
zoom cluster, brush preview, hotkeys, dirty tracking and save prompt. The base class may get
small hook methods (e.g. a `stroke_committed` hook, class-aware fill hook) but its mask-mode
behaviour must not change (`tests/test_mask_tracing_interface_smoke.py` stays green).

- **Canvas model:** unchanged single ARGB `mask_pixmap`; each pixel is opaque in its class
  colour or transparent (background). Strokes are already non-antialiased with
  `CompositionMode_Source`, so colours stay exact. `brush_color` = active class colour.
- **ids ⇄ pixmap:** load renders ids to colours via the registry (retired classes in their
  own colour; unknown ids never reach the canvas because `align_to_registry` maps them to
  Ignore); save maps colours back to ids by exact match over all registry classes (colours
  are unique across the registry).
- **Brush:** paints the active class, overwriting any class. **Eraser:** paints background.
  **Fill:** fills the closed outline of the **active class** (contours of that class's
  pixels only) with the active class. **Clear:** whole tracing to background (undoable).
- **Per-class brush size:** each class remembers its last brush size (so tip dots stay small).
- **Load:** `gt/<stem>.png` if present (read → align, warnings as notices; `changed` marks
  dirty), else an empty tracing. Never reads `mask/`. A GT file whose size differs from the
  image, or a `GtFileError`, → danger notice, empty canvas, and the file on disk is left
  untouched unless the user saves. An invalid `gt/classes.json` (`RegistryError`) → danger
  notice naming the error; the editor shows the image but painting, fill, clear, ticks and
  saving are refused until the file is fixed (the registry file is never overwritten). A
  fixed file takes effect on the next image load.
- **Save:** always writes `gt/<stem>.png` (even when empty) with
  `reviewed = ticked ∪ keys of classes with pixels` (Ignore excluded), then writes
  `gt/classes.json` if missing. Emits `gt_saved(image_path)`. Status chip:
  `name ● Saved · 2/3 checked` / `● Unsaved` / `● No GT` / `● GT unreadable`.
- **Failed save never loses edits:** if a save fails (invalid registry, locked or read-only
  file, full disk), the unsaved-changes prompt offers Discard / Keep editing; choosing Keep
  editing cancels the action that asked (image switch, stage switch, folder open, window
  close) and the open image and its edits stay. The mask editor's behaviour is unchanged.
- The base `mask_saved` / `mask_cleared` signals are not emitted in GT mode.

### 3.3 Class panel (`app/gui/class_panel.py`)

Floating overlay on the right edge, vertically centred, clear of the enhance popover (top
right) and zoom cluster (bottom right). One row per active class, registry order:
number key hint, colour swatch, name, **checked ✓** checkbox. Active row highlighted
`#b794f6`. More than 10 rows scroll inside the panel (max height 60% of the canvas). Gear button → class editor. Signals: `active_changed(str key)`,
`reviewed_changed(str key, bool)`, `edit_requested()`.

### 3.4 Checked (reviewed) per class

- A class's ✓ ticks automatically when it has pixels on the image (and cannot be unticked
  while it has pixels). The annotator ticks it by hand for "checked, none present".
- Ignore has no ✓.
- Ticking/unticking marks the tracing dirty. Saved in the chunk's `reviewed` list.
- Why: an image traced for roots but never checked for nodules would teach a model "no
  nodules here". Export skips incomplete images by default (§4).

### 3.5 Keys

`1`–`9` (top row or keypad) select the n-th active class; `0` selects Ignore; `PgDn` /
`PgUp` save (if dirty, without a prompt) and open the next / previous image in the library
tree, counted from the image open in the editor. Exception: after a lossy load-time remap
(`align_to_registry` returned warnings, e.g. an unknown class key mapped to Ignore) they use
the normal unsaved-changes prompt instead of saving silently. Class-panel widgets never take
keyboard focus, so Space and the other canvas keys keep working after a panel click. Plus every base
key (B/E/F, `[` `]`, Space, H, Ctrl+S, Ctrl+Z/Y, zoom keys). Navigation is a signal
`navigate_requested(int delta)` that `MainWindow` handles by moving the tree selection.

### 3.6 Class editor (`app/gui/class_editor_dialog.py`)

Modal dialog over the registry: add (name, key auto-slugged from name and editable, colour
picker), rename, recolour, retire (confirm). Id and key read-only for existing classes;
Ignore shows only name/colour. Returns a new `Registry` or `None`; the caller saves it and
refreshes the panel and canvas colours.

### 3.7 Files (Track E only)

`app/gui/gt_tracing_interface.py` (new), `app/gui/class_panel.py` (new),
`app/gui/class_editor_dialog.py` (new), `app/gui/mask_tracing_interface.py` and
`app/gui/mask_drawing_tools.py` (hooks only), `app/gui/main_window.py` (panel index 4,
switch/toggle, navigation slot, `export_gt_dataset` slot that opens Track X's dialog),
`app/gui/shell_chrome.py` (stage + action page), `app/gui/ui_panels.py` (action buttons, if
buttons are created there), `tests/test_gt_tracing_interface.py` (new),
`tests/test_shell_chrome_smoke.py` (stage count 6 → 7).

---

## 4. Dataset export (core in §5.4, dialog = Track X)

**Export dataset…** opens `ExportDatasetDialog` (`app/gui/export_dataset_dialog.py`): output
folder, validation share (default 20%), "keep each tube in one split" (default on), "only
fully checked images" (default on), seed (default 0). Runs `export_dataset` on a `QThread`
with progress, then shows a summary (exported, train/val counts, skipped incomplete,
errors).

Output layout:

```
<out>/images/<stem>.<ext>     copied original images (shutil.copy2)
<out>/masks/<stem>.png        8-bit grayscale ("L") class-id PNG
<out>/classes.json            {"version": 1, "ignore_index": 255, "classes": [
                                {"id": 0, "key": "background", "name": "Background"},
                                {"id": 1, "key": "root", "name": "Root", "color": "#5fd6a0"}, …]}
<out>/splits/train.txt        one stem per line
<out>/splits/val.txt
<out>/manifest.csv            stem, source_image, split, tube, position, date, classes_present
```

- **Id remap:** background → 0; active non-Ignore classes in registry order → 1..N; Ignore,
  retired and unknown ids → 255. The export's `classes.json` documents the mapping.
- **Selection:** images with a GT file; with "only fully checked", an image is included only
  if every active non-Ignore class is in its `reviewed` list.
- **Split:** group key = tube number from `parse_image_name` when "keep each tube in one
  split" is on and the tube parses, else the stem. Groups are sorted, shuffled with
  `random.Random(seed)`, and assigned to val until val holds ≥ `val_fraction` of the images;
  with ≥ 2 groups both splits are non-empty. Deterministic for a given seed and input.
- **Safety:** `out_dir` must not exist or be empty, and must not lie inside the project's
  `mask/`, `skeletons/`, `skeleton_corrections/` or `gt/` folders, else `ExportError`.
  `mask/`, `skeletons/` and the GT files are never modified.
- **Errors:** a failure on one image adds `(stem, message)` to `errors`, leaves no partial
  output for it, and the export continues. The dialog lists the failed images (first 10) and
  writes `<out>/errors.txt`.
- **Reserved key:** `background` cannot be used as a class key (the export uses it for id 0).

---

## 5. Core package `app/gt/` (no Qt) — frozen API

### 5.1 `app/config.py` additions

```python
GT_DIRNAME = "gt"
GT_CLASSES_FILENAME = "classes.json"
GT_META_CHUNK = "sprouts_gt"
GT_BACKGROUND_ID = 0
GT_IGNORE_ID = 255
GT_UNKNOWN_COLOR = "#808080"
DEFAULT_GT_CLASSES = (...)   # the four entries of §2 (Ignore last)
```

### 5.2 `app/gt/registry.py`

```python
class RegistryError(ValueError): ...

@dataclass(frozen=True)
class ClassDef:
    id: int
    key: str
    name: str
    color: str
    retired: bool = False
    @property
    def is_ignore(self) -> bool

@dataclass(frozen=True)
class Registry:
    classes: tuple[ClassDef, ...]            # display order, Ignore last, retired included
    def active(self) -> tuple[ClassDef, ...]           # non-retired, Ignore last
    def labelled(self) -> tuple[ClassDef, ...]         # active minus Ignore
    def by_id(self, id: int) -> ClassDef | None
    def by_key(self, key: str) -> ClassDef             # KeyError
    def next_id(self) -> int                           # RegistryError when 1–254 exhausted
    def add(self, *, key: str, name: str, color: str) -> "Registry"
    def update(self, key: str, *, name: str | None = None,
               color: str | None = None) -> "Registry"
    def retire(self, key: str) -> "Registry"           # RegistryError for Ignore
    def to_dict(self) -> dict
    @classmethod
    def from_dict(cls, data: dict) -> "Registry"      # validates

def slugify_key(name: str) -> str
def default_registry() -> Registry
def registry_path(project_dir: str) -> str
def load_registry(project_dir: str) -> Registry
def save_registry(project_dir: str, registry: Registry) -> str     # atomic
```

### 5.3 `app/gt/store.py`

```python
class GtFileError(ValueError): ...

@dataclass
class Tracing:
    ids: np.ndarray                        # (h, w) uint8
    reviewed: frozenset[str] = frozenset()
    file_classes: dict[int, str] = field(default_factory=dict)   # id -> key from the file

def gt_dir(project_dir: str) -> str
def gt_path(image_path: str) -> str                    # <dir>/gt/<stem>.png
def empty_tracing(height: int, width: int) -> Tracing
def read_tracing(path: str) -> Tracing | None          # None if missing; GtFileError if not
                                                       # 8-bit single-channel or bad chunk
def write_tracing(path: str, tracing: Tracing, registry: Registry) -> None   # atomic
def align_to_registry(tracing: Tracing, registry: Registry
                      ) -> tuple[Tracing, list[str], bool]
def present_keys(tracing: Tracing, registry: Registry) -> set[str]   # classes with pixels,
                                                                     # Ignore excluded
def is_complete(tracing: Tracing, registry: Registry) -> bool
```

### 5.4 `app/gt/export.py`

```python
class ExportError(ValueError): ...

@dataclass(frozen=True)
class ExportOptions:
    out_dir: str
    val_fraction: float = 0.2
    group_by_tube: bool = True
    only_complete: bool = True
    seed: int = 0

@dataclass
class ExportResult:
    out_dir: str
    exported: int
    train: int
    val: int
    skipped_incomplete: int
    errors: list[tuple[str, str]]          # (stem, message)

def remap_ids(ids: np.ndarray, registry: Registry) -> np.ndarray
def assign_splits(stems: list[str], val_fraction: float, group_by_tube: bool,
                  seed: int) -> dict[str, str]         # stem -> "train" | "val"
def export_dataset(project_dir: str, images: dict[str, str], registry: Registry,
                   options: ExportOptions,
                   progress_cb: Callable[[int], None] | None = None) -> ExportResult
```

`images` is `image_manager.images` (stem → image path). One bad file adds an `errors` entry
and the export continues.

---

## 6. Testing

- **Registry:** defaults valid; round-trip; each validation rule (duplicate id/key/colour,
  bad key/colour/name, Ignore rules, version); `next_id` skips retired; add/update/retire;
  Ignore retire rejected; `slugify_key`.
- **Store:** write→read round-trip of ids 0/1/2/3/255 and the chunk; palette colours match
  the registry and index 0 is transparent; non-ASCII path; atomic write leaves no `.tmp`;
  RGB/16-bit file → `GtFileError`; missing file → `None`; empty tracing still writes a file;
  `align_to_registry` for same-project (no-op), unknown key → 255 + warning, id missing from
  map; `present_keys`, `is_complete`.
- **Export:** remap to contiguous ids with Ignore/retired/unknown → 255; only-complete
  filter; split determinism for a seed, group-by-tube keeps a tube in one split, both splits
  non-empty with ≥ 2 groups; output layout and `classes.json`/`manifest.csv` contents;
  non-empty `out_dir` → `ExportError`; a corrupt GT file lands in `errors` and export
  continues; source files untouched.
- **Editor (offscreen Qt):** opens empty without reading `mask/` even when a mask exists;
  painting class A then B over it leaves B; fill limited to the active class outline; save
  writes ids + `reviewed` (auto-ticked painted classes, manually ticked empty class);
  empty save still writes the file; ✓ cannot be unticked while pixels exist; keys 1–9/0
  select classes; `PgDn` emits `navigate_requested(+1)` after saving; per-class brush size;
  retired-class pixels survive a load→save round-trip with their id; mask-mode smoke test
  unchanged.
- **Shell:** 7 stages, Trace GT after Library.
- Full suite green: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest`.

---

## 7. Build plan

1. Lead: commit this spec; build `app/gt/` + config + core tests (§5, §6 core/export).
2. In parallel, each in its own worktree branched from the core commit:
   - **Track E** — §3 (files §3.7).
   - **Track X** — `app/gui/export_dataset_dialog.py` (dialog + `QThread` + summary) and
     `tests/test_export_dataset_dialog.py`. No other files.
3. Lead merges both, runs the full suite, reviews, updates README ("Ground-truth tracing"),
   `docs/KNOWN_ISSUES.md`, and runs `graphify update .`.

## 8. Out of scope (v1)

Any measurement of GT classes; ML overlays or seeding in the GT editor; per-class
visibility; file-tree badges for GT; deleting GT files; instance ids / COCO export;
multi-folder projects; un-retire; automatic tip detection.
