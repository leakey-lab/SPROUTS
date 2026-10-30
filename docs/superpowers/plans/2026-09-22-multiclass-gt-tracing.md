# Multi-class Ground-Truth Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Trace GT" stage that paints multi-class ground-truth masks (Root, Nodule, Root tip, Ignore, plus user classes) into `gt/<stem>.png`, and an "Export dataset" action that writes a segmentation dataset.

**Architecture:** A Qt-free core package `app/gt/` (class registry, palette-PNG store, dataset export) is the single source of truth for GT data. The editor `GtTracingInterface` subclasses the committed `MaskTracingInterface`, adds a floating class panel, and reads/writes only through `app/gt`. The shell gets a seventh stage and `MainWindow` a fifth right-panel page.

**Tech Stack:** Python 3.10, PyQt6, NumPy, Pillow 12.2 (palette PNG + text chunk), pytest (offscreen Qt).

**Spec:** `docs/superpowers/specs/2026-09-22-multiclass-labels-design.md` — read it first; §5 is the frozen core API every task codes against.

## Global Constraints

- Run everything with `./venv/Scripts/python.exe` (system Python has CPU-only torch). Tests: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest` (bash: `QT_QPA_PLATFORM=offscreen ./venv/Scripts/python.exe -m pytest`).
- Lint: `./venv/Scripts/python.exe -m ruff check <files>`; format check: `./venv/Scripts/python.exe -m ruff format --check --line-length 100 <files>`. Line length 100, py310.
- GT code never reads or writes `mask/`, `skeletons/`, `skeleton_corrections/`, and never imports `app.inference` model code. (`app.inference.metrics.parse_image_name` is a pure filename parser and is allowed in export.)
- Pixel ids: `0` background, `1`–`254` classes, `255` Ignore. One class per pixel.
- File I/O through Pillow and Python file handles (non-ASCII Windows paths). Writes atomic: `<path>.tmp` then `os.replace`.
- Match surrounding code style: module docstrings, `logger = logging.getLogger(__name__)`, type hints, comment density like `app/gui/mask_tracing_interface.py`.
- **Parallel work in one folder:** each task owns only the files listed in it. Do not edit files owned by another task. **Do not run `git commit`, `git add`, `git stash`, `git checkout` or any other state-changing git command** — the lead commits after review.
- Existing tests must stay green, in particular `tests/test_mask_tracing_interface_smoke.py` and `tests/test_view_canvas_background.py` (any new `QGraphicsView` subclass must be listed there — this plan adds none).

## Review Focus

- A GT file whose size differs from its image (image replaced/resized): editor warns, opens empty, and does not overwrite the file unless the user saves → Task 4 test `test_size_mismatch_warns_and_keeps_file`.
- Unsaved GT edits when the user closes the window, opens another folder, or switches stage: the save prompt runs, as for masks → Task 4 test `test_lifecycle_hooks_call_gt_prompt`.
- A hand-edited, invalid `gt/classes.json`: editor shows a danger notice and refuses to save; the registry file is never overwritten → Task 4 test `test_invalid_registry_blocks_save`.
- More than 10 classes: panel rows scroll, keys 1–9 still map to the first nine → Task 3 test `test_panel_many_classes_scrolls`.
- Image names with non-ASCII characters and multiple dots (`plötz.T1.L2.png`): `gt_path` keeps the full stem and round-trips → Task 1 test `test_gt_path_multi_dot_and_unicode`.

---

## Execution phases

- **Phase 1:** Task 1 (core registry + store).
- **Phase 2 (parallel, after Task 1 is reviewed and committed):** Task 2 (export core + dialog), Task 3 (class widgets), Task 4 (editor + shell wiring). Task 4 consumes Task 3's widget API and Task 2's dialog class exactly as written below; if those files do not exist yet when Task 4 needs them for a test run, finish the independent parts first and re-check later. Never create another task's files.
- **Phase 3 (lead):** full suite, whole-branch review, README + KNOWN_ISSUES, `graphify update .`.

---

### Task 1: Core registry and palette-PNG store

**Files:**
- Modify: `app/config.py` (append the GT block)
- Create: `app/gt/__init__.py`, `app/gt/registry.py`, `app/gt/store.py`
- Test: `tests/test_gt_registry.py`, `tests/test_gt_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: exactly spec §5.1–§5.3 (names, signatures, dataclass fields). `app/gt/__init__.py` re-exports every public name of `registry` and `store` (not `export`).

- [ ] **Step 1: Config block** — append to `app/config.py`:

```python
# --- Ground-truth (GT) tracing ---------------------------------------------
# Hand-traced multi-class ground truth, independent of every ML model in the
# app (see docs/superpowers/specs/2026-09-22-multiclass-labels-design.md).
GT_DIRNAME = "gt"
GT_CLASSES_FILENAME = "classes.json"
GT_META_CHUNK = "sprouts_gt"
GT_BACKGROUND_ID = 0
GT_IGNORE_ID = 255
GT_UNKNOWN_COLOR = "#808080"
DEFAULT_GT_CLASSES = (
    {"id": 1, "key": "root", "name": "Root", "color": "#5fd6a0", "retired": False},
    {"id": 2, "key": "nodule", "name": "Nodule", "color": "#ff7eb6", "retired": False},
    {"id": 3, "key": "root_tip", "name": "Root tip", "color": "#ffd166", "retired": False},
    {"id": 255, "key": "ignore", "name": "Ignore", "color": "#8a8f98", "retired": False},
)
```

- [ ] **Step 2: Failing registry tests** — `tests/test_gt_registry.py` with these cases (each a separate test function):
  - `default_registry()` has keys `root, nodule, root_tip, ignore` in that order; `labelled()` excludes ignore; `active()` ends with ignore.
  - `to_dict` → `from_dict` round-trip equality.
  - `from_dict` raises `RegistryError` for: version 2; duplicate id; duplicate key; duplicate colour (case-insensitive, including a retired class); key `"Root"`; key starting with a digit; empty name; 41-char name; colour `"green"`; normal class id 0, 255 or 300; missing ignore; ignore with id 7; ignore retired.
  - `next_id()` returns 4 on defaults; after `add` + `retire` of id 4 it returns 5 (retired ids not reused).
  - `add(key="dead_root", name="Dead root", color="#9aa0a6")` appends before Ignore with id 4; `update("nodule", name="Nod", color="#123456")` changes only those fields; `update` with unknown key → `KeyError`.
  - `retire("ignore")` → `RegistryError`; `retire("nodule")` sets `retired=True` and removes it from `active()`.
  - `slugify_key("Dead root (old)") == "dead_root_old"`; `slugify_key("3 tips") == "c_3_tips"` (prefix `c_` when not starting with a letter); result ≤ 32 chars.
  - `load_registry(tmp)` without a file returns defaults and creates no file; `save_registry` then `load_registry` round-trips; `save_registry` leaves no `.tmp`.
  - `load_registry` on invalid JSON text → `RegistryError`.

- [ ] **Step 3: Run** `QT_QPA_PLATFORM=offscreen ./venv/Scripts/python.exe -m pytest tests/test_gt_registry.py -q` → FAIL (module missing).

- [ ] **Step 4: Implement `app/gt/registry.py`** per spec §2 and §5.2. Key rules: key regex `^[a-z][a-z0-9_]{0,31}$`; colour regex `^#[0-9a-fA-F]{6}$`, uniqueness compared lower-case across all classes; `from_dict` accepts the JSON of spec §2 and keeps Ignore last (reorder if needed); `add` inserts before Ignore; `next_id` = lowest id in 1..254 not used by any class; `save_registry` writes `json.dumps(reg.to_dict(), indent=2)` via `<path>.tmp` + `os.replace`, creating `gt/`. `load_registry` wraps `json.JSONDecodeError`/`OSError` in `RegistryError`.

- [ ] **Step 5: Run registry tests** → PASS.

- [ ] **Step 6: Failing store tests** — `tests/test_gt_store.py`:
  - `test_round_trip_ids_and_chunk`: ids with values {0,1,2,3,255} (shape 30×40) + `reviewed={"root","nodule"}` → `write_tracing` → `read_tracing` returns equal ids, same `reviewed`, and `file_classes == {1:"root",2:"nodule",3:"root_tip",255:"ignore"}`.
  - `test_palette_matches_registry`: open the written file with Pillow: mode `"P"`; `getpalette()[3:6] == [0x5f,0xd6,0xa0]`; palette entry 255 = Ignore colour; entry 0 transparent (`im.info["transparency"] == 0`).
  - `test_gt_path_multi_dot_and_unicode`: `gt_path(r"C:\d\plötz.T1.L2.png") == os.path.join(r"C:\d", "gt", "plötz.T1.L2.png")`; round-trip a tracing through a `tmp_path / "ünï" / "gt" / "a.b.png"` path.
  - `test_atomic_no_tmp_left` and `test_empty_tracing_still_written` (all-zero ids write a file that reads back all zeros).
  - `test_missing_returns_none`; `test_rgb_file_raises` (save a 3-channel PNG, `read_tracing` → `GtFileError`); `test_16bit_file_raises` (`I;16`); `test_bad_chunk_json_raises`.
  - `test_file_without_chunk_reads_empty_meta` (an `L`-mode PNG with ids): `reviewed == frozenset()`, `file_classes == {}`.
  - `test_align_same_project_noop`: written with defaults, aligned with defaults → `changed is False`, `warnings == []`.
  - `test_align_unknown_key_to_ignore`: tracing whose `file_classes={1:"root",2:"weed"}` and pixels of 2 → aligned pixels 2→255, one warning mentioning `weed`, `changed is True`.
  - `test_align_remaps_by_key`: `file_classes={1:"nodule"}` pixels of 1 with default registry → pixels become 2.
  - `test_align_id_missing_from_map`: no `file_classes`, pixels of 7 → 255 with warning; pixels of 1 stay 1.
  - `test_present_keys_and_is_complete`: ids containing 1 and 255 → `present_keys == {"root"}`; `is_complete` false until `reviewed ⊇ {"root","nodule","root_tip"}`.

- [ ] **Step 7: Run store tests** → FAIL.

- [ ] **Step 8: Implement `app/gt/store.py`** — core I/O:

```python
def write_tracing(path: str, tracing: Tracing, registry: Registry) -> None:
    ids = np.ascontiguousarray(tracing.ids, dtype=np.uint8)
    if ids.ndim != 2:
        raise GtFileError(f"GT ids must be 2-D, got shape {ids.shape}")
    palette = [0, 0, 0] * 256
    unknown = _hex_to_rgb(GT_UNKNOWN_COLOR)
    for i in np.unique(ids).tolist():
        if i != GT_BACKGROUND_ID:
            palette[3 * i : 3 * i + 3] = unknown
    for c in registry.classes:
        palette[3 * c.id : 3 * c.id + 3] = _hex_to_rgb(c.color)
    image = Image.fromarray(ids, mode="P")
    image.putpalette(palette)
    meta = {
        "v": 1,
        "classes": {str(c.id): c.key for c in registry.classes},
        "reviewed": sorted(tracing.reviewed),
    }
    info = PngImagePlugin.PngInfo()
    info.add_text(GT_META_CHUNK, json.dumps(meta))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        image.save(fh, format="PNG", pnginfo=info, transparency=GT_BACKGROUND_ID)
    os.replace(tmp, path)
```

`read_tracing`: return `None` if the file is missing; open with Pillow inside `with open(path, "rb")`; accept mode `"P"` or `"L"` only, else `GtFileError`; `ids = np.array(im, dtype=np.uint8)`; parse `im.text.get(GT_META_CHUNK)` (missing → empty meta; invalid JSON or wrong types → `GtFileError`); `file_classes = {int(k): v for k, v in meta.get("classes", {}).items()}`. `align_to_registry` implements spec §1.4 with a 256-entry lookup table (`lut[old] = new`) applied as `lut[ids]`. `present_keys` excludes Ignore; `is_complete` = every `registry.labelled()` key in `reviewed`.

- [ ] **Step 9: Run** both test files → PASS; `ruff check app/gt tests/test_gt_*.py app/config.py` clean; `ruff format --check` clean.

- [ ] **Step 10: Report** files changed and test output to the lead (lead commits: `feat(gt): class registry and palette-PNG store for ground-truth tracing`).

---

### Task 2: Dataset export core and dialog

**Files:**
- Create: `app/gt/export.py`, `app/gui/export_dataset_dialog.py`
- Test: `tests/test_gt_export.py`, `tests/test_export_dataset_dialog.py`

**Interfaces:**
- Consumes (Task 1): `Registry`, `ClassDef`, `Tracing`, `read_tracing`, `align_to_registry`, `is_complete`, `present_keys`, `gt_path`, `GtFileError`, config `GT_IGNORE_ID`, `GT_BACKGROUND_ID`; `app.inference.metrics.parse_image_name` (pure filename parser).
- Produces: spec §5.4 exactly, plus the dialog:

```python
class ExportDatasetDialog(QDialog):
    def __init__(self, project_dir: str, images: dict[str, str], registry: Registry,
                 parent=None): ...
    # .exec() runs it; the "Export" button starts ExportThread; on finish shows a summary
    # label and turns "Export" into "Close". Exposes .result_summary: ExportResult | None.

class ExportThread(QThread):
    progress = pyqtSignal(int)
    finished_ok = pyqtSignal(object)   # ExportResult
    failed = pyqtSignal(str)
```

- [ ] **Step 1: Failing export tests** — `tests/test_gt_export.py` builds a tmp project: 6 images `plot_T1_L1_2024.01.01_120000.png`, `…T1_L2…`, `…T2_L1…`, `…T2_L2…`, `…T3_L1…`, `…T3_L2…` (20×20 RGB), GT files for all written with `write_tracing` (mix of ids 1/2/3/255, one with a retired class id 4 via a registry that retired `dead_root`), reviewed complete for 5 and incomplete for 1. Cases:
  - `remap_ids`: defaults → background 0, root 1, nodule 2, root_tip 3, ignore 255; with an added-then-retired class 4 → 4 maps to 255; an unknown id 9 → 255.
  - `assign_splits` deterministic for the same seed; different seed can differ; `group_by_tube=True` never splits a tube across train/val; with ≥2 groups both splits non-empty; `val_fraction=0.0` → all train.
  - `export_dataset` with `only_complete=True` exports 5, `skipped_incomplete == 1`; with `False` exports 6.
  - Output layout: `images/<stem>.png` exists and bytes equal the source; `masks/<stem>.png` is mode `"L"` with remapped values; `classes.json` has `ignore_index == 255` and ids `0..3`; `splits/train.txt` + `splits/val.txt` partition the exported stems; `manifest.csv` header `stem,source_image,split,tube,position,date,classes_present` and one row per exported stem.
  - Non-empty `out_dir` → `ExportError`; missing `out_dir` is created.
  - A corrupt GT file (write bytes `b"not a png"` to one `gt/*.png`) → appears in `errors`, others still exported.
  - Source GT files unchanged (compare bytes before/after).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement `app/gt/export.py`.** Split algorithm:

```python
def assign_splits(stems, val_fraction, group_by_tube, seed):
    groups: dict[str, list[str]] = {}
    for stem in stems:
        tube = parse_image_name(stem).get("tube_number") if group_by_tube else None
        key = f"tube:{tube}" if tube is not None else f"stem:{stem}"
        groups.setdefault(key, []).append(stem)
    order = sorted(groups)
    random.Random(seed).shuffle(order)
    val_keys: list[str] = []
    if val_fraction > 0 and len(order) >= 2:
        target = val_fraction * len(stems)
        count = 0
        for key in order[:-1]:  # the last group always stays in train
            if count >= target:
                break
            val_keys.append(key)
            count += len(groups[key])
    return {
        s: ("val" if key in val_keys else "train") for key in order for s in groups[key]
    }
```

Properties the tests pin: deterministic per seed, a tube never spans both splits, and with ≥ 2 groups and `val_fraction > 0` both splits are non-empty. `export_dataset`: validate/create `out_dir` (`ExportError` if it exists and is non-empty), create `images/ masks/ splits/`, iterate `sorted(images)`, skip stems without a GT file, read + align (errors → `errors`), filter by `is_complete`, `remap_ids`, save mask with `Image.fromarray(ids, mode="L")`, `shutil.copy2` the image, then write `classes.json`, splits, `manifest.csv` (`csv` module, `classes_present` = `;`-joined `present_keys` in registry order). `progress_cb(int(100 * i / n))`.
- [ ] **Step 4: Run** export tests → PASS.
- [ ] **Step 5: Failing dialog test** — `tests/test_export_dataset_dialog.py` (offscreen): construct the dialog on the tmp project, set the output path field to a new tmp dir, set val % spin to 50, call the dialog's start method, wait for the thread (`thread.wait(10000)` then `QApplication.processEvents()`), assert `result_summary.exported > 0`, the summary label text mentions the exported count, and the Export button text is now "Close". Second test: non-empty output dir → the dialog shows the `ExportError` message in its status label and no thread result.
- [ ] **Step 6: Implement `app/gui/export_dataset_dialog.py`** — `QDialog` titled "Export dataset": output folder `QLineEdit` + "Browse…" (`QFileDialog.getExistingDirectory`), `QSpinBox` val % (0–90, default 20), `QCheckBox` "Keep each tube in one split" (checked), `QCheckBox` "Only fully checked images" (checked), `QSpinBox` seed (default 0), `QProgressBar`, status `QLabel`, buttons Export / Cancel. Validate empty output path → status "Choose an output folder". Style with `app.gui.widgets.tokens` like other dialogs; Export uses objectName `primaryButton`.
- [ ] **Step 7: Run** both test files + `ruff check`/`ruff format --check` on the four files → clean.
- [ ] **Step 8: Report** to the lead (lead commits: `feat(gt): dataset export with tube-grouped split and export dialog`).

---

### Task 3: Class panel and class editor widgets

**Files:**
- Create: `app/gui/class_panel.py`, `app/gui/class_editor_dialog.py`
- Test: `tests/test_gt_widgets.py`

**Interfaces:**
- Consumes (Task 1): `Registry`, `ClassDef`, `RegistryError`, `slugify_key`.
- Produces (Task 4 relies on these exact names):

```python
class ClassPanel(_FloatingPanel):          # from app.gui.widgets.overlays
    active_changed = pyqtSignal(str)        # class key
    reviewed_changed = pyqtSignal(str, bool)
    edit_requested = pyqtSignal()
    def __init__(self, parent=None): ...
    def set_registry(self, registry: Registry) -> None      # rebuild rows (registry.active())
    def set_active(self, key: str) -> None                  # highlight, no signal
    def active_key(self) -> str
    def set_reviewed(self, reviewed: set[str], locked: set[str]) -> None
        # checkbox checked for keys in reviewed|locked; disabled for keys in locked; no signal
    def key_for_shortcut(self, n: int) -> str | None        # n=1..9 -> n-th labelled class; 0 -> "ignore"
    def reposition(self) -> None                            # right edge, vertically centred

class ClassEditorDialog(QDialog):
    def __init__(self, registry: Registry, parent=None): ...
    def result_registry(self) -> Registry | None            # None if cancelled / unchanged
```

- [ ] **Step 1: Failing tests** — `tests/test_gt_widgets.py` (offscreen):
  - Panel shows one row per active class in registry order (4 rows for defaults); Ignore row has no checkbox.
  - Clicking a row (call the row's click handler or `QTest.mouseClick`) emits `active_changed("nodule")` and highlights it; `set_active` does not emit.
  - Toggling the nodule checkbox emits `reviewed_changed("nodule", True)`; `set_reviewed({"root"}, {"root"})` checks and disables root without emitting.
  - `key_for_shortcut(1) == "root"`, `(3) == "root_tip"`, `(0) == "ignore"`, `(9) is None` on defaults.
  - `test_panel_many_classes_scrolls`: registry with 12 labelled classes → rows live inside a `QScrollArea`; panel height ≤ 60% of a 600-px-high parent; `key_for_shortcut(9)` is the 9th labelled class.
  - Gear button emits `edit_requested`.
  - Dialog: add a class via its add form (name "Dead root", colour set programmatically) then accept → `result_registry().by_key("dead_root").id == 4`; rename nodule → reflected; retire nodule (bypass the confirm via a `confirm=False` test hook or monkeypatching `QMessageBox.question`) → retired; Ignore row offers no retire; duplicate colour → inline error label, OK stays disabled; cancel → `None`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement `ClassPanel`** — subclass `_FloatingPanel` from `app/gui/widgets/overlays.py` (vertical). Header row: "Classes" label + gear `IconButton("settings", "Edit classes")`. Rows in a `QScrollArea` (frameless, transparent). Row widget: number hint label (`1`…`9`, `0` for Ignore), 12×12 swatch, name label, `QCheckBox` (not for Ignore). Active row background `tokens.SEL_SOFT`, left border `tokens.SEL`. `reposition()`: `adjustSize()`, cap height at `int(parent.height() * 0.6)`, x = `parent.width() - width - 14`, y centred. Tooltip on the checkbox: "Checked: this class was looked for on this image".
- [ ] **Step 4: Implement `ClassEditorDialog`** — table-like list of classes (swatch button opening `QColorDialog`, name `QLineEdit`, read-only id/key, Retire button except Ignore) plus an add form (name, key auto-filled by `slugify_key` and editable, colour button). Every edit rebuilds a candidate `Registry` via `add`/`update`/`retire`; a `RegistryError` shows in a red inline label and disables OK. `result_registry()` returns the candidate if accepted and different from the input.
- [ ] **Step 5: Run** tests + ruff → clean.
- [ ] **Step 6: Report** to the lead (lead commits: `feat(gt): class panel and class editor widgets`).

---

### Task 4: GT editor, shell stage and main-window wiring

**Files:**
- Create: `app/gui/gt_tracing_interface.py`
- Modify: `app/gui/mask_tracing_interface.py` (small hooks only), `app/gui/mask_drawing_tools.py` (hooks only, if needed), `app/gui/main_window.py`, `app/gui/shell_chrome.py`
- Test: `tests/test_gt_tracing_interface.py`, `tests/test_shell_chrome_smoke.py` (update counts)

**Interfaces:**
- Consumes: Task 1 core (`load_registry`, `save_registry`, `registry_path`, `read_tracing`, `write_tracing`, `align_to_registry`, `present_keys`, `empty_tracing`, `gt_path`, `Tracing`, `Registry`, `RegistryError`, `GtFileError`); Task 3 `ClassPanel`, `ClassEditorDialog` (API above); Task 2 `ExportDatasetDialog(project_dir, images, registry, parent)`.
- Produces:

```python
class GtTracingInterface(MaskTracingInterface):
    gt_saved = pyqtSignal(str)                 # image path
    navigate_requested = pyqtSignal(int)       # +1 next image, -1 previous
    registry: Registry
    class_panel: ClassPanel
    def load_image(self, image_path: str) -> None
    def save_mask(self, save_path=None) -> bool   # writes gt/<stem>.png
    def set_active_class(self, key: str) -> None
    def edit_classes(self) -> None               # opens ClassEditorDialog
# MainWindow additions
MainWindow.gt_tracing_interface: GtTracingInterface     # right_panel index 4
MainWindow.toggle_gt_tracing() -> None
MainWindow.navigate_image(delta: int) -> None
MainWindow.export_gt_dataset() -> None
```

- [ ] **Step 1: Failing editor tests** — `tests/test_gt_tracing_interface.py` (offscreen; fixture writes an 80×60 image `plot_001.png` into `tmp_path` and a white `mask/plot_001.png` to prove it is ignored):
  - `test_opens_empty_ignoring_mask`: after `load_image`, canvas alpha is all zero although `mask/plot_001.png` is full white.
  - `test_paint_classes_and_save_ids`: `set_active_class("root")`, stroke; `set_active_class("nodule")`, stroke crossing the root; `save_mask()` → `read_tracing(gt_path)` has 1 where only root was painted, 2 on the crossing; `reviewed == {"root","nodule"}`; `gt/classes.json` now exists.
  - `test_fill_limited_to_active_class`: draw a closed root square outline (brush strokes along four sides) and a small nodule dot elsewhere outside it. With nodule active, click-fill inside the square → returns no fill (the nodule has no outline there), `notice` emitted, ids unchanged. With root active, click-fill inside the square → an interior pixel becomes root (1) and the nodule dot outside stays nodule (2).
  - `test_empty_save_writes_file`: load, tick nodule ✓ via `class_panel.reviewed_changed` path, `save_mask()` → file exists, all ids 0, `reviewed == {"nodule"}`.
  - `test_checkbox_locked_while_pixels`: after painting root, root checkbox is checked and disabled.
  - `test_digit_keys_select_classes`: send key `2` to `graphics_view` (`QTest.keyClick`) → active class `nodule`; `0` → `ignore`.
  - `test_pgdn_saves_and_navigates`: dirty editor, connect a list to `navigate_requested`, press PgDn → file written and `[1]` recorded.
  - `test_per_class_brush_size`: root size 20, switch to root_tip set 3, back to root → slider shows 20.
  - `test_retired_class_pixels_survive`: write a GT file with id 4 under a registry where `dead_root` (id 4) is retired; load, paint root elsewhere, save → id-4 pixels unchanged.
  - `test_size_mismatch_warns_and_keeps_file`: GT file 10×10 for the 80×60 image → a `notice` with kind `"danger"`, empty canvas, file bytes unchanged after load.
  - `test_invalid_registry_blocks_save`: write `gt/classes.json` = `"{"`; load → danger notice; `save_mask()` returns False; the file still contains `"{"`.
  - `test_mask_mode_unchanged`: plain `MaskTracingInterface` still saves white PNG to `mask/` (reuse one assertion from the existing smoke test).
- [ ] **Step 2: Shell/main-window tests** — edit `tests/test_shell_chrome_smoke.py::test_main_window_contract`: `right_panel.count() == 5`; `ribbon_buttons` keys == `["Library", "Trace GT", "Generate", "Correct Mask", "Correct Skeleton", "Measure", "Visualize"]`; `len == 7`. Add `test_lifecycle_hooks_call_gt_prompt` there (or in the editor test file): construct `MainWindow`, monkeypatch `mw.gt_tracing_interface.maybe_save_changes` with a counter, then `mw.open_path("")` is a no-op, call `mw.toggle_gt_tracing()` then `mw.switch_right_panel("display")` → counter 1; `mw.close()` → counter 2 (patch the mask interface's prompt too so no dialog blocks).
- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Base hooks** in `mask_tracing_interface.py` — keep mask behaviour identical. Allowed additions: a class attribute `EDITOR_NOUN = "mask"` used in `maybe_save_changes` text ("Save changes to the {noun} for {name}?") and in `save_button`/`clear_button` labels; a no-op method `_after_stroke(self)` called at the end of `end_stroke` and after a successful fill; nothing else. Run `tests/test_mask_tracing_interface_smoke.py` → still PASS.
- [ ] **Step 5: Implement `GtTracingInterface`** (`EDITOR_NOUN = "GT"`):
  - `__init__`: `super().__init__(parent)`; `self.registry = default_registry()`; `self._registry_error: str | None = None`; `self._project_dir = None`; `self._ids_on_load: np.ndarray | None`; `self._reviewed: set[str]`; `self._brush_sizes: dict[str, int]`; build `self.class_panel = ClassPanel(self)` and connect its three signals; add it to `_overlays()` (override to append); install extra shortcuts (digits 0–9 via `class_panel.key_for_shortcut`, `PgDown`/`PgUp` → `_navigate(±1)`), same context as base shortcuts.
  - `set_active_class(key)`: store the current brush size under the old key, set `self.brush_color = QColor(cls.color)`, restore the new key's size (default `DEFAULT_BRUSH`), `class_panel.set_active(key)`, restyle the brush preview.
  - `load_image(path)`: run base prompt logic via `maybe_save_changes()` when the path changes; load the image pixmap like the base (copy the minimal part of base `load_image` rather than calling it, because the base reads `mask/`); reload the registry when `dirname(path)` changes (`RegistryError` → `_registry_error`, danger notice, keep defaults for display); read + align the GT file (size mismatch / `GtFileError` → danger notice, empty); render ids → ARGB pixmap with `argb_array_to_pixmap` (lookup table class id → BGRA; 0 → transparent); remember `_ids_on_load`; set `_reviewed` from the file; refresh panel checkboxes (`locked = present keys`); `_set_clean()` unless `align` changed ids.
  - `_pixmap_to_ids()`: `pixmap_to_argb_array` → map each colour of `registry.classes` to its id via a dict keyed by packed `B | G<<8 | R<<16`; transparent → 0; any other colour → the `_ids_on_load` value at that pixel.
  - `save_mask(save_path=None)`: refuse with a danger notice when `_registry_error`; compute ids; `reviewed = self._reviewed | present_keys(...)`; `write_tracing(gt_path(image), Tracing(ids, frozenset(reviewed)), registry)`; `save_registry` if `registry_path` missing; `_set_clean()`; emit `gt_saved`; return True. Ignore `save_path` (GT always saves to its own path).
  - `flood_fill(pos)`: same algorithm as the mixin but contours from pixels equal to the active colour only.
  - `_after_stroke()`: recompute present keys; lock their checkboxes (`class_panel.set_reviewed(self._reviewed, present)`).
  - `reviewed_changed(key, checked)`: update `_reviewed`, `mark_dirty()`.
  - `edit_classes()`: `ClassEditorDialog(self.registry, self)`; if `result_registry()` is not None and no `_registry_error`: `save_registry`, set `self.registry`, `class_panel.set_registry`, re-render the canvas colours from current ids (ids unchanged).
  - Status chip text: `● Saved · {n}/{m} checked` / `● Unsaved` / `● No GT`.
  - `_navigate(delta)`: if dirty, `save_mask()`; emit `navigate_requested(delta)`.
  - The dock `save_button` text becomes "Save GT"; the base `mask_saved`/`mask_cleared` are never emitted.
- [ ] **Step 6: Wire `MainWindow`:**
  - Construct `self.gt_tracing_interface = GtTracingInterface()` next to the mask interface; connect `notice → self.notify`, `navigate_requested → self.navigate_image`, `gt_saved → lambda p: self.notify(f"GT saved for {os.path.basename(p)}")`.
  - `_build_body`: `self.right_panel.addWidget(self.gt_tracing_interface)` after the skeleton interface (index 4); update the docstring index list.
  - `switch_right_panel`: add `"gt_tracing" → setCurrentIndex(4)`; when leaving index 4 for another panel call `self.gt_tracing_interface.maybe_save_changes()` (mirror the index-1 rule).
  - `toggle_gt_tracing()`: if current index != 4: leave mask tracing cleanly (`if index == 1: self.toggle_mask_tracing()`), `switch_right_panel("gt_tracing")`; else `switch_right_panel("display")`; then load the current tree item via `on_image_selected`.
  - `on_image_selected`: route to `self.gt_tracing_interface.load_image(image_path)` when it is the current widget.
  - `navigate_image(delta)`:

```python
    def navigate_image(self, delta: int) -> None:
        """Select the next/previous visible image leaf in the library tree."""
        from PyQt6.QtWidgets import QTreeWidgetItemIterator

        leaves = []
        it = QTreeWidgetItemIterator(self.file_list)
        while it.value():
            item = it.value()
            if item.data(0, Qt.ItemDataRole.UserRole) and not item.isHidden():
                leaves.append(item)
            it += 1
        if not leaves:
            return
        current = self.file_list.currentItem()
        idx = leaves.index(current) if current in leaves else -1
        new = idx + delta
        if 0 <= new < len(leaves):
            self.file_list.setCurrentItem(leaves[new])
            self.on_image_selected(leaves[new])
```

  - `export_gt_dataset()`: require a loaded folder (`image_manager.original_folder`, else warn); `ExportDatasetDialog(folder, dict(self.image_manager.images), self.gt_tracing_interface.registry, self).exec()`.
  - `closeEvent`: also call `self.gt_tracing_interface.maybe_save_changes()`. `open_path`: also call `self.gt_tracing_interface.reset()`. `set_opengl_viewports_enabled`: also toggle `gt_tracing_interface.graphics_view`.
- [ ] **Step 7: Wire `shell_chrome.py`:** insert `("Trace GT", "Trace GT", "layers", "Paint ground-truth classes for a segmentation dataset.")` right after Library in `_STAGES`; `base_handlers["Trace GT"] = lambda: (mw.toggle_gt_tracing() if mw.right_panel.currentIndex() != 4 else None)`; the Correct Mask / Correct Skeleton handlers are unchanged. In `_populate`, build two ghost `QPushButton`s — "Classes…" (`mw.gt_tracing_interface.edit_classes`) and "Export dataset…" (`mw.export_gt_dataset`) — styled like `trace_clear` (objectName `ghostButton`), stored as `mw.gt_classes_button` / `mw.gt_export_button`, and add page `"Trace GT": _page(mw.gt_classes_button, mw.gt_export_button)`. Update the module comment that says six stages. Check the default 1200-px window still fits the topbar (shorten labels if the smoke render clips).
- [ ] **Step 8: Run** `tests/test_gt_tracing_interface.py`, `tests/test_shell_chrome_smoke.py`, `tests/test_mask_tracing_interface_smoke.py`, `tests/test_view_canvas_background.py` → PASS; then the full suite; ruff check + format check on touched files.
- [ ] **Step 9: Report** to the lead with the full-suite output (lead commits: `feat(gt): Trace GT stage with multi-class editor and class panel`).

---

## Phase 3 (lead)

- [ ] Full suite green; whole-branch review (`/code-review` or a reviewer subagent) against the spec; fix findings.
- [ ] README: "Ground-truth tracing" section (what `gt/` holds, classes, keys, export layout). `docs/KNOWN_ISSUES.md`: note v1 scope limits from spec §8.
- [ ] `graphify update .`; update memory (file + vault).
