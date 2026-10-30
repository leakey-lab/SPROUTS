# Unified Correct Editor and Smooth Zoom — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:test-driven-development inside every package: the failing test comes first.

**Goal:** One "Correct" stage that edits the mask and the skeleton of an image on one canvas (7 stages → 6), and a photo that stays smooth at every zoom on every canvas, with pixel-identical saved files, lower latency and no new god class.

**Architecture:** Qt-free documents (`MaskDocument`, `SkeletonDocument`) and a Qt-free `EditJournal` under the unchanged history classes; shared canvas parts (`EditorCanvasView`, `PaintCanvas`, `EditorChrome`, `editor_prompts`) used by Correct and Trace GT; three render items over a Qt-free pyramid (`PhotoLayerItem`, `RasterLayerItem`, `SkeletonLayerItem`) used by all canvases; a small `app/gui/correct/` package where `CorrectEditor` only composes the session, router, layers and controls. The old editors stay untouched and working until one swap commit replaces them.

**Tech Stack:** Python 3.10, PyQt6 6.11 (Qt 6.11), NumPy 2.2, OpenCV 4.13, scikit-image 0.25, pytest (offscreen Qt), ruff.

**Spec:** `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`. Read it first: sections 3-10 are the product behaviour, section 18 the decisions. The **Shared contracts** below are frozen: code against them exactly.

**Revision:** 2026-09-29, after the plan critic (21 issues; the lead's binding decisions are in `.claude/handoff/unified-editor-2026-09-29/notes/critic_decisions.md`). From it: the split into 13 packages (P1a/P1b, P4a/P4b, P5a/P5b, P7a/P7b, P8a/P8b), the completion-driven execution, the mid-gesture rule (C2), per-layer save reports (C15, C17), lazy per-tile skeleton vectors and the Library's `vectors=False` (C6), the undo semantics (C2), the pinned file stamps (C13, C14), the scikit-image prewarm (C4, C19) and the pytest / GL-lock rules below. The machine-readable package list is `.claude/handoff/unified-editor-2026-09-29/notes/packages_v2.json`.

**Scout evidence** (numbers, prototypes, benches): `.claude/handoff/unified-editor-2026-09-29/scout/*.md` and `scratch/{architecture,zoom-render,ux-latency,contracts-tests,plan-critic}/`. Copy what you need; never edit them.

---

## Global Constraints

- Run everything with `./venv/Scripts/python.exe` (system Python is CPU-only torch and lies about CUDA). Baseline: **1650 passed, 8 skipped, 0 failed, about 60 s**.
- **pytest, every run** (PowerShell), with `<id>` your package id:
  ```powershell
  $env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/<id>/pt --continue-on-collection-errors <test files>
  ```
  The cache provider is off (parallel sessions would race on `.pytest_cache`), every agent has its own `--basetemp` (the default one is shared on Windows), and a foreign test file that fails to import is reported as an error instead of aborting the session. `pyproject.toml` already adds `-q`; never add another `-q` (it hides the summary line). While working, run only your own test files and the ones your package names; run the full suite (the same command without file arguments) only at your final gate.
- Lint and format with ruff only: `./venv/Scripts/python.exe -m ruff check <files>` and `./venv/Scripts/python.exe -m ruff format --check <files>` (line length 100, py310). No black or isort.
- **Parallel work in one folder.** Each package edits, creates or deletes only the files it owns (File ownership, below). Never create a file another package owns, even to unblock yourself. The workflow starts a package only when every package in its "Start after" list has finished, so what you consume exists and works. The only overlaps inside a wave are P1b on P1a's Step 1 and P4a / P5a on P6's Step 0, which land within the first minutes; until they do, build and test the parts that do not need them, then check again (no waiting loops). If a contract looks wrong, follow it as written and describe the problem in your notes file. A bug found in a file another package owns (including a wave-1 file found in wave 2) is reported in your notes with the failing case; the lead fixes it or re-dispatches the owner.
- **Providers land working code first.** A provider's Step 1 lands a working minimal implementation of the public API its in-wave consumers need, and later steps refine the internals without changing the API: P1a (C5, C6 `device_scale` / photo / raster, C7 names), P1b (`SkeletonLayerItem` = today's overlay pixmap), P6 (C2, C4 `SnapshotHistory.drop_redo`). P2, P4a and P5a have no consumer running beside them and build each contract complete, in their step order. Nothing is ever a `NotImplementedError` placeholder (abstract hooks that a subclass must override, such as `RasterEditorBase._open_document`, are not placeholders).
- **`import app.gui.main_window` never breaks.** After changing a module that `MainWindow` imports (directly or through the old editors), run `./venv/Scripts/python.exe -c "import app.gui.main_window"` before moving on. Live code never imports a half-built module (P3's base-switch rule is the main case).
- **Full-suite runs in a shared folder.** While other packages are mid-change a full run can fail in files they own. Fix everything in your own files, list any foreign failures (test id + owning package) in your notes, and let the lead judge; never "fix" a foreign file. Expected in W2: P8a's two guard tests stay red until P8b's swap.
- **No state-changing git commands** (`add`, `commit`, `stash`, `checkout`, `switch`, `reset`, `restore`, `clean`, `rm`). Delete files with the shell, not git. Only the lead commits, after each wave (see Execution).
- **Never touch `config/normalization_defaults.json`** (the user's own change) and never click "Save as Defaults" in a test or harness run.
- **The old editors keep working until the swap (P8b).** Wave-1 changes to shared modules are backward compatible: `mask_tracing_interface.py`, `skeleton_correction_interface.py`, their views, `mask_drawing_tools.py`, `canvas_item.py` and their tests stay green until P8b deletes them.
- **Saved pixels are sacred.** Painting, fill, commit, undo, skeleton tools, `render_to_size` and the save/load code move verbatim. Any change there must keep the golden tests green. A gesture in progress is never dropped silently (C2, mid-gesture rule).
- **Colours only from tokens** (`from app.gui.widgets import tokens`: `MASK`, `SKEL`, `BG_0`…`BG_3`, `TEXT_FAINT`, `SEL`, `DANGER`…). No hex colour literal anywhere under `app/gui`, **including comments and docstrings** (`tests/test_design_system_colour_literals.py` counts them; new modules start at 0). No `QColor(r, g, b)` literals.
- Every new public class, function and method has a docstring (the FAIR docstring ratchet). Module docstrings say what the module is for and name the Qt-free ones.
- **No new eager heavy imports.** New modules import only numpy, cv2, PyQt6 and app modules; `app/gui/correct/__init__.py`, `app/gui/canvas_layers/__init__.py` and `app/imaging/__init__.py` import nothing heavy (`canvas_layers/__init__.py` re-exports lazily through PEP 562; consumers import the submodules). Modules marked Qt-free must not import PyQt6 at import time (`TYPE_CHECKING` imports only).
- **Hot paths gain no heavy hop:** mouse move → layer → tool → canvas is at most two extra Python calls versus today; no per-event allocation of pixmaps or arrays beyond today's.
- Qt test hygiene (from the contracts scout): one `QApplication` per module kept referenced; parent every `QGraphicsScene` to its view or keep it referenced; an autouse fixture fails on any unexpected `QMessageBox.exec`; set `ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=0` in render tests; prefer structural assertions over wall clock. Test helpers are imported as `from tests.<module> import …` (`tests` is a package).
- Scratch scripts, benches and screenshots go to `.claude/handoff/unified-editor-2026-09-29/scratch/<package-id>/`; each package keeps `.claude/handoff/unified-editor-2026-09-29/notes/<package-id>.md` with a PROGRESS checklist at the top (update it as you go so a cut-off run can resume), the API notes other packages need, the perf numbers and how you measured them.
- **Real-GL measurements** need the real desktop: `QT_QPA_PLATFORM=windows`, `QT_OPENGL=desktop`, a frameless window that closes itself (hard `os._exit` watchdog) within 30 s; reuse `scratch/zoom-render/gl_common.py`. **One GL bench at a time:** before creating the window, take the lock `.claude/handoff/unified-editor-2026-09-29/scratch/GL.lock` atomically (`os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)`, then write your package id and pid). If it exists, wait for it, checking every 5 s for up to 10 minutes; a lock still there after 10 minutes is stale (a bench closes within 30 s): remove it and take it. Remove the lock in a `finally` and in the watchdog before `os._exit`.

## Review Focus

1. **Pixel identity.** Mask and skeleton goldens replayed per layer (P4b, P5b) and through the editor (P7b), plus the real-dataset gate the lead runs at integration (`check_goldens_unified.py --check` → `MATCHES GOLDEN` for all 6 cases). Colour type 6 for edited masks; a model mask never rewritten unless the user saves it (spec D7).
2. **Gestures in progress** (C2): every transition commits a drag first, undo / redo are refused mid-drag, Esc on the mask commits, the rest of a finished drag is ignored; `pixmap_to_argb_array(canvas) == lut[plane]` after Esc / F / E / 2 / PgDn mid-stroke (P4b, P7b), the skeleton equivalents (P5b), the session and router order (P6).
3. **Save reports** (C15, C17): every save path (Ctrl+S, PgDn, the prompt's Save) emits `mask_saved` / `mask_cleared` exactly once per written / removed mask (P6, P7b, P8b tree-click test); `skeleton_saved` stays unconnected.
4. **Undo journal:** redo branches dropped on a new action, evicted entries skipped, Ctrl+Z ignored mid-drag, a polyline point popped first (P6); `undo()` / `redo()` step one entry and return False only on an empty stack (C2); 60 mixed actions with skeleton eviction undone and redone exactly on the real layers (P7b).
5. **Trace GT unchanged:** every GT test green, override hooks still win (P3 MRO test), `SHORTCUT_SCOPE` of the new base is `TRACE_GT`; GT moves to the new base only after the base passes its own tests (P3).
6. **Canvas background:** the new `EditorCanvasView` calls `configure_canvas_background`; discovery is automatic and transitive, in place before the class exists (P2 Step 1).
7. **Keys:** no key is both a `QShortcut` and a key-event row in the Correct scope; `find_conflicts()` empty; X / H / Shift+H do not auto-repeat; dock and panel clicks keep canvas focus (P7a, P7b).
8. **GL:** a stroke re-keys only the tiles it touched (P1a `built_tiles` test); MSAA requested and switchable; `EditorCanvasView` antialiases decorations while tiles never blend (C6, C8); GL benches serialized by the lock.
9. **HiDPI:** every level / tier threshold uses the device scale (P1a, P1b tests at devicePixelRatio 2).
10. **Skeleton frames stay bounded:** vectors are extracted lazily per tile within 4 ms per paint; an edit never extracts (P1b); dense-2048 frame and commit gates (P1b, P5b).
11. **Library look:** the generated 2 px file draws soft at s ≥ 1 (`vectors=False`), no pixel blocks (P3).
12. **FR-03:** `skeleton_saved` is never connected; a skeleton save changes neither `has_skeleton` nor the metrics bar (P8b).
13. **EXIF:** a rotated JPEG decodes with Qt's geometry (P2).
14. **No god class:** every `app/gui/correct/*.py` ≤ 450 lines (P6 ratchet test); `CorrectEditor` holds no document, tool or history logic.

---

## Execution (completion-driven; agents never poll)

```
W0  lead     C1 registry rows: committed (99876e9). Commit this revised spec + plan,
             create the 13 notes files, launch W1.
W1  nine agents. The workflow script starts each agent as soon as every package in its
    "start after" list has finished.
      at once    P1a render-core    P1b render-skeleton    P2 kit    P6 workspace-logic
                 P4a mask-document  P5a skeleton-document
      then       P4b mask-layer       after P1a, P2, P6, P4a
                 P5b skeleton-layer   after P1b, P6, P5a, P4a (test data)
                 P3  gt-library       after P1a, P1b, P2
    lead: when all nine have stopped: review, one full-suite run of the combined tree,
          then commits in dependency order P6, P2, P1a, P1b, P4a, P5a, P4b, P5b, P3
W2  four agents, same rule (every W1 package is committed)
      at once    P7a correct-controls    P8a docs-guards
      then       P7b correct-editor   after P7a
                 P8b swap             after P7b
    lead: integration checklist (end of this plan): review, one full-suite run, then
          commits P7a, P7b, the swap (P8b + P8a's two guard tests), the docs (P8a)
```

- **In-wave overlaps (by design).** P1b builds on P1a's Step 1 (C5, `device_scale`, the lazy re-exports: working bodies within the first minutes); P4a and P5a build on P6's Step 0 (`layer.py`, `SnapshotHistory.drop_redo`). Until those land, P1b writes its Qt-free vector extraction and P4a / P5a their fixtures and Qt-free tests. Every other dependency is finished before its consumer starts.
- **Critical path:** max(P1a, P1b, P2) → P3 in W1; P7a → P7b → P8b in W2.
- **Lead commits.** Nobody commits while agents run. When all nine W1 agents have stopped, the lead reviews each package against the spec and the contracts, runs the full suite once on the combined tree (records count and duration), then commits package by package in the dependency order above (conventional commits, no attribution lines), so each commit depends only on the commits before it. Verifying each commit alone in a temporary worktree is optional; if the lead does it (in the background), it says so in one line in `STATUS.md` (where, why, removed when). W2 follows the same rule. P8a's guard tests go into the swap commit because they are red until the swap; P8a's docs follow the swap because they describe the swapped app.

## File ownership

Every file has exactly one owner at any time. W0's files are committed; wave-1 files are read-only in wave 2 (committed by then). Every package also owns its `notes/<package-id>.md` and `scratch/<package-id>/`.

| Package | Wave | Start after | Creates | Modifies | Deletes |
|---|---|---|---|---|---|
| **W0 lead** (done, `99876e9`) | 0 | — | — | `app/gui/shortcuts.py` (additive: C1), `tests/test_shell_info_shortcuts.py` (one assertion) | — |
| **P1a** render-core | 1 | — | `app/imaging/__init__.py`, `app/imaging/pyramid.py`, `app/gui/canvas_layers/__init__.py`, `app/gui/canvas_layers/photo.py`, `app/gui/canvas_layers/raster.py`, `tests/test_image_pyramid.py`, `tests/test_canvas_layers.py`, `tests/test_canvas_zoom_ux.py`, `tests/render_metrics.py` | `app/gui/canvas_view.py`, `app/gui/canvas_gl.py`, `app/gui/widgets/overlays.py`, `tests/test_overlays_widgets.py` | — |
| **P1b** render-skeleton | 1 | — (uses P1a's Step 1) | `app/gui/canvas_layers/skeleton.py`, `tests/test_canvas_layer_skeleton.py` | — | — |
| **P2** kit | 1 | — | `app/gui/editor_canvas.py`, `app/gui/paint_canvas.py`, `app/gui/editor_chrome.py`, `app/gui/editor_prompts.py`, `tests/test_editor_canvas.py`, `tests/test_paint_canvas.py`, `tests/test_editor_chrome.py`, `tests/test_editor_prompts.py` | `app/gui/image_normalization_interface.py`, `app/enhancement.py`, `tests/test_enhance_controls.py`, `tests/test_enhance_core.py`, `tests/test_view_canvas_background.py` | — |
| **P6** workspace-logic | 1 | — | `app/gui/correct/__init__.py`, `app/gui/correct/layer.py`, `app/gui/correct/session.py`, `app/gui/correct/tool_router.py`, `app/editing/journal.py`, `tests/test_editing_journal.py`, `tests/test_correct_session.py`, `tests/test_correct_tool_router.py`, `tests/test_correct_layering.py`, `tests/correct_fakes.py` | `app/editing/history.py`, `app/editing/__init__.py`, `tests/test_editing_history.py` | — |
| **P4a** mask-document | 1 | — (uses P6's Step 0) | `app/gui/correct/mask_document.py`, `tests/test_correct_mask_document.py`, `tests/test_correct_goldens_data.py`, `tests/correct_goldens_support.py`, `tests/data/correct_goldens.json` | `app/gui/patch_history.py`, `tests/test_mask_editor_history.py` | — |
| **P5a** skeleton-document | 1 | — (uses P6's Step 0) | `app/gui/correct/skeleton_document.py`, `tests/test_correct_skeleton_document.py`, `tests/test_skeleton_roi.py`, `tests/test_startup_import_budget.py`, `tests/test_skeleton_overlay.py` | `app/gui/skeleton_graph_model.py`, `app/inference/metrics.py`, `tests/test_skeleton_model_editing.py` | — |
| **P4b** mask-layer | 1 | P1a, P2, P6, P4a | `app/gui/correct/mask_layer.py`, `app/gui/correct/mask_tools.py`, `tests/test_correct_mask_layer.py`, `tests/test_correct_mask_goldens.py` | — | — |
| **P5b** skeleton-layer | 1 | P1b, P6, P5a, P4a | `app/gui/correct/skeleton_layer.py`, `tests/test_correct_skeleton_layer.py`, `tests/test_correct_skeleton_goldens.py` | — | — |
| **P3** gt-library | 1 | P1a, P1b, P2 | `app/gui/raster_editor_base.py`, `tests/test_raster_editor_behaviour.py`, `tests/test_canvas_zoom_quality.py` | `app/gui/gt_tracing_interface.py`, `app/gui/display_controller.py`, `tests/test_canvas_display_controller.py`, `tests/test_gt_tracing_interface.py` (W1 only), `tests/test_mask_editor_behaviour.py` (W1 only: moves the GT cases out) | — |
| **P7a** correct-controls | 2 | — | `app/gui/correct/controls.py`, `app/gui/correct/layer_panel.py`, `tests/test_correct_controls.py` | — | — |
| **P8a** docs-guards | 2 | — | `tests/test_gui_public_names.py`, `tests/test_right_panel_contract.py`, and the harness copy `.claude/handoff/unified-editor-2026-09-29/scratch/ui-harness/` (git-ignored) | `README.md`, `CLAUDE.md`, `CHANGELOG.md`, `docs/KNOWN_ISSUES.md`, `docs/Architecture_2.md`, `docs/DATA_DICTIONARY.md` | — |
| **P7b** correct-editor | 2 | P7a | `app/gui/correct/editor.py`, `tests/test_correct_editor.py`, `tests/test_correct_editor_history.py`, `tests/test_correct_editor_keys.py`, `tests/test_correct_goldens.py`, `tests/test_correct_perf.py` | — | — |
| **P8b** swap | 2 | P7b | `tests/test_correct_main_window.py` | `app/gui/main_window.py`, `app/gui/shell_chrome.py`, `app/gui/panels.py`, `app/gui/shortcuts.py`, `app/gui/ui_panels.py`, `app/gui/_lazy.py`, `app/gui/welcome_screen.py`, `app/gui/skeleton_document.py`, `app/gui/skeleton_overlay.py`, `tests/test_canvas_views.py`, `tests/test_shell_chrome_smoke.py`, `tests/test_shell_chrome_topbar.py`, `tests/test_shell_chrome_topbar_editor_refusal.py`, `tests/test_integration_main_window_wiring.py`, `tests/test_library_main_window.py`, `tests/test_shell_info_shortcuts.py`, `tests/test_integrator_cross_module.py`, `tests/test_foundation_runtime_panels_feedback.py`, `tests/test_metrics_bar_smoke.py`, `tests/test_viz_embed_manager.py`, `tests/test_import_smoke.py`, `tests/test_gt_tracing_interface.py` (deletes `test_mask_mode_unchanged`), `tests/test_split_*.py` (only if present) | `app/gui/mask_tracing_interface.py`, `app/gui/skeleton_correction_interface.py`, `app/gui/mask_graphics_view.py`, `app/gui/skeleton_correction_graphics_view.py`, `app/gui/mask_drawing_tools.py`, `app/gui/canvas_item.py`, `app/gui/mask_cursor_utils.py`, `app/handlers/mask_handler.py`, `tests/test_mask_tracing_interface_smoke.py`, `tests/test_skeleton_correction_interface_smoke.py`, `tests/test_skeleton_editor_lifecycle.py`, `tests/test_skeleton_editor_tools.py`, `tests/test_canvas_item.py`, `tests/test_generation_mask_handler.py`, `tests/test_mask_editor_behaviour.py` |

**Files that change owner between waves** (sequential ownership, never shared):

| File | W0 | W1 | W2 |
|---|---|---|---|
| `app/gui/shortcuts.py` | lead (C1, committed) | read-only | P8b (final registry) |
| `tests/test_shell_info_shortcuts.py` | lead (committed) | read-only | P8b |
| `tests/test_mask_editor_behaviour.py` | — | P3 (moves the GT cases out, keeps every mask case) | P8b (deletes the file) |
| `tests/test_gt_tracing_interface.py` | — | P3 (imports `pixmap_to_argb_array` from `paint_canvas`) | P8b (deletes `test_mask_mode_unchanged` and the `MaskTracingInterface` import) |
| `tests/test_split_*.py` (the peer plan's guards, only if present in this folder) | — | nobody | P8b (updates the frozen sets deliberately) |

Read-only for everyone unless listed above: `app/gui/skeleton_tools.py`, `app/gui/brush_preview.py`, `app/gui/class_panel.py`, `app/gui/skeleton_overlay.py` and `app/gui/skeleton_document.py` (until P8b), `app/gui/widgets/*` (except `overlays.py`: P1a), `app/masks/*`, `app/gt/*`, `app/project_layout.py`, `app/io_utils.py`, `app/config.py`, `app/design/tokens.py`, `app/provenance.py` (unchanged by decision, spec D34).

---

## Shared contracts

These are frozen. Types are exact; bodies that "move verbatim" are named by their current file:line.

### C0 Conventions

- Image pixels are `QPoint(x, y)` integers, already clamped by the view to `[0, w-1] × [0, h-1]`. `Rect = tuple[int, int, int, int]` is `(x, y, width, height)` in image pixels.
- Layer ids: `"mask"`, `"skeleton"`. Tool ids: mask `"brush"`, `"eraser"`, `"fill"`; skeleton `skeleton_tools.SELECT/ERASER/POLYLINE/CONNECT` = `"select"`, `"eraser"`, `"polyline"`, `"connect"`.
- Scene z-values: photo `0.0`; mask item `1.0`; skeleton item `1.5`; skeleton decorations keep their values (`app/gui/skeleton_overlay.py:90-106`: polyline preview and connect line `2`, endpoint markers and handles `3`, highlight `3.5`), so every decoration paints above the skeleton line; rings `BrushPreview.Z_VALUE = 10.0`.
- Default opacities: mask `0.60`, skeleton `0.85`; the inactive layer is drawn at opacity × `REFERENCE_DIM = 0.5`.
- Notices are `(message, kind)`, kind in `"success" | "info" | "warn" | "danger"`.

### C1 Registry rows for the "Correct" scope (W0, lead) — `app/gui/shortcuts.py` — committed as `99876e9`

Additive in W0 (the old scopes stay until P8b). Add `CORRECT` to `__all__`, append it to `SCOPES`, and add these rows to `REGISTRY` after the `CORRECT_SKELETON` block:

```python
CORRECT = "Correct"


def _correct_rows() -> list[tuple]:
    """The unified Correct stage: both layers, one scope (spec section 6)."""
    return [
        *_history_rows(),
        ("save", ("Ctrl+S",), "Save the unsaved layers (nothing unsaved: the active layer)"),
        ("mask_layer", ("1",), "Edit the mask (root area)"),
        ("skeleton_layer", ("2",), "Edit the skeleton (root length)"),
        ("swap_layer", ("X",), "Switch between the mask and the skeleton"),
        ("brush", ("B",), "Mask brush (tap); hold B and scroll to resize the brush", KEY),
        ("fill", ("F",), "Mask fill: fill a closed outline"),
        ("select_tool", ("V",), "Skeleton select: pick a branch"),
        ("polyline", ("P",), "Skeleton polyline"),
        ("connect", ("C",), "Skeleton connect: drag from an endpoint to another point"),
        ("eraser", ("E",), "Eraser of the active layer"),
        ("size_smaller", ("[",), "Smaller brush or eraser"),
        ("size_larger", ("]",), "Larger brush or eraser"),
        ("toggle_layers", ("H",), "Show or hide both layers (see the photo)"),
        ("toggle_reference", ("Shift+H",), "Show or hide the other layer"),
        ("smooth", ("S",), "Smooth the polyline (Polyline tool)", KEY),
        *_canvas_rows(SHORTCUT),
        ("finish_line", ("Enter",), "Finish the polyline", KEY),
        ("cancel", ("Esc",), "Cancel the current line or selection", KEY),
        (
            "delete_branch",
            ("Delete", "Backspace"),
            "Delete the picked branch (Select, then Delete); clears a Connect selection",
            KEY,
        ),
        ("add_bend", ("Ctrl+click",), "Add a bend to a polyline segment", GESTURE),
        ("move_line", ("Shift+drag",), "Move the whole polyline", GESTURE),
        ("finish_line_click", ("Double-click",), "Finish the polyline", GESTURE),
    ]
```

and `*_rows(CORRECT, _correct_rows())` in `REGISTRY`. W0 also changed the `SCOPES` tuple in `tests/test_shell_info_shortcuts.py::test_every_scope_has_rows_and_scopes_are_ordered` (appended `sc.CORRECT`). Verified: `tests/test_shell_info_shortcuts.py`, `tests/test_integrator_cross_module.py` green, `sc.find_conflicts() == []`, 29 Correct rows.

### C2 `app/gui/correct/layer.py` (P6; Qt-free at import)

P6 creates this module **verbatim** (docstrings may be longer):

```python
"""Contract between the Correct workspace and its edit layers (Qt-free at import).

The workspace (session, router, editor) drives each layer only through EditLayer; a
layer calls back only through LayerHost. Positions are image pixels already clamped
by the canvas view.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QKeyEvent

MASK = "mask"
SKELETON = "skeleton"
LAYER_IDS: tuple[str, ...] = (MASK, SKELETON)  # scene order bottom-to-top, panel order, keys 1 / 2
REFERENCE_DIM = 0.5  # the inactive layer is drawn at its opacity times this


@dataclass(frozen=True)
class Notice:
    """A transient message for the user (shown as a toast)."""

    message: str
    kind: str = "info"


@dataclass(frozen=True)
class LayerStatus:
    """Save state of one layer: StatusChip state + optional label and tooltip."""

    state: str  # "none" | "clean" | "dirty" | "error"
    label: str | None = None
    tooltip: str = ""


@dataclass(frozen=True)
class SaveOutcome:
    """What one layer's save did."""

    layer_id: str
    ok: bool
    path: str | None = None  # the file written; None when nothing was written
    cleared: bool = False  # mask only: an empty mask removed an existing saved file
    message: str = ""  # detail for a toast ("" = nothing beyond the summary)
    kind: str = "success"


@dataclass(frozen=True)
class ToolSpec:
    """A tool as the rail shows it."""

    tool_id: str
    icon: str  # resources/icons/sprouts/<icon>.svg
    label: str
    key: str  # its registry key, shown in the tooltip
    tip: str  # tooltip tail
    sized: bool  # the dock size slider applies to this tool


@dataclass(frozen=True)
class SizeSpec:
    """The dock size slider of the current tool."""

    icon: str
    tooltip: str
    minimum: int
    maximum: int
    fmt: str = "{} px"


class LayerHost(Protocol):
    """What a layer may ask of the workspace."""

    def notify(self, message: str, kind: str = "info") -> None: ...
    def layer_edited(self, layer_id: str) -> None: ...  # a new undoable action exists
    def layer_changed(self, layer_id: str) -> None: ...  # UI state changed (never per drag frame)
    def is_panning(self) -> bool: ...


class LayerTool(Protocol):
    """One tool of a layer (app.gui.skeleton_tools.SkeletonTool already has this shape)."""

    tool_id: str

    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None: ...
    def move(self, pos: QPoint) -> None: ...
    def release(self, pos: QPoint) -> None: ...
    def double_click(self, pos: QPoint) -> None: ...
    def key(self, event: QKeyEvent) -> bool: ...
    def deactivate(self) -> None: ...
    def hint(self) -> tuple[str, str]: ...


class EditLayer(Protocol):
    """One editable document drawn on the shared canvas (the mask or the skeleton)."""

    layer_id: str
    noun: str  # "mask" | "skeleton" (prompts and toasts)
    color: str  # tokens.MASK | tokens.SKEL
    tools: tuple[ToolSpec, ...]
    current_tool: str

    # document
    def open(
        self, image_path: str, size: tuple[int, int], images_base_folder: str | None
    ) -> list[Notice]: ...
    def close(self) -> None: ...
    def is_open(self) -> bool: ...
    def file_stamps(self) -> tuple: ...  # the pinned file set of C13 / C14, read afresh per call
    def is_dirty(self) -> bool: ...  # differs from the saved state
    def save(self) -> SaveOutcome: ...
    def revert(self) -> list[Notice]: ...  # Discard: back to the saved state, history cleared
    def status(self) -> LayerStatus: ...
    def clear(self) -> bool: ...  # undoable Clear; False when nothing is open
    # history (the layer's own stack)
    def can_undo(self) -> bool: ...
    def can_redo(self) -> bool: ...
    def undo(self) -> bool: ...  # exactly one entry of its own stack; False only when it is empty
    def redo(self) -> bool: ...  # exactly one entry of its own redo stack; False only when empty
    def drop_redo(self) -> None: ...
    def undo_in_gesture(self) -> bool: ...  # polyline draft: pop its last point (True if done)
    # gestures (the mid-gesture rule below)
    def gesture_active(self) -> bool: ...  # mouse-down drag in progress
    def finish_gesture(self) -> None: ...  # end the drag as a release at the last position would
    def cancel_transient(self) -> None: ...  # Esc: mask commits a stroke; skeleton drops drafts
    # tools and input (image pixels)
    def set_tool(self, tool_id: str) -> None: ...
    def tool_hint(self) -> tuple[str, str]: ...  # (short hint, tooltip) for the chip
    def size_spec(self) -> SizeSpec | None: ...  # None: the current tool has no size
    def size(self) -> int: ...  # mask: brush size; skeleton: eraser diameter
    def set_size(self, value: int) -> None: ...
    def step_size(self, direction: int) -> None: ...  # +-12 %, at least 1 px, clamped
    def press(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> None: ...
    def move(self, pos: QPoint, dragging: bool) -> None: ...  # hover (False) or drag (True)
    def release(self, pos: QPoint) -> None: ...
    def double_click(self, pos: QPoint) -> None: ...
    def key(self, event: QKeyEvent) -> bool: ...  # gesture keys (S, Enter, Delete, Backspace)
    def hover(self, pos: QPoint | None) -> None: ...  # ring at pos; None hides it
    # view
    def set_active(self, active: bool) -> None: ...
    def is_active(self) -> bool: ...
    def set_visible(self, visible: bool) -> None: ...
    def is_visible(self) -> bool: ...
    def opacity(self) -> float: ...  # the user's opacity 0..1 (not the dimmed one)
    def set_opacity(self, value: float) -> None: ...


def missing_members(obj: object, protocol: type = EditLayer) -> list[str]:
    """Names declared by ``protocol`` (attributes and methods) that ``obj`` lacks."""
```

Rules every layer follows:

- `layer_edited` fires once per undoable action, when it is recorded in the layer's own history (mask: after `PatchHistory.commit`; skeleton: from `model.on_action`), never for undo, redo, open, revert or save.
- `layer_changed` fires after anything the UI shows changed (dirty state, status, tool, hint, draft, can_undo / can_redo): at the end of a gesture and after a full render, **never for the intermediate renders of a drag** (coalesced stroke segments or eraser frames), so a drag does not run the session's dirty compare and the UI refresh at 60 Hz.
- **Undo and redo.** `undo()` / `redo()` step exactly one entry of the layer's own history and return True iff they did. They return False only when that stack is empty: `EditJournal` reads False as "evicted" and drops the entry for good, so a layer never returns False for a transient reason. They never pop draft points (only `undo_in_gesture()` does). The skeleton's `undo()` / `redo()` also drop a polyline draft (today's `_step_history`); when a draft existed and the layer is inactive at that moment (the draft was hidden), the layer calls `host.notify("Polyline draft discarded", "info")`.
- `set_active(False)` hides rings, endpoint markers and the polyline preview but keeps a polyline draft; `set_active(True)` restores the preview. Editing a hidden layer is the router's job (it shows the layer first).
- **Mid-gesture rule.** A gesture is a mouse-down drag: a mask stroke; a skeleton eraser drag, Connect drag or polyline point drag. `finish_gesture()` ends it as a release at the last pointer position would (mask: `PaintCanvas.commit()` → history → `layer_edited`; skeleton: the drag's tool gets `release(last_pos)`: the eraser thins and records, Connect joins at that point, a dragged polyline point stays where it is); without a gesture it does nothing. Every transition that could strand a gesture first calls `finish_gesture()` on the layer that has one: tool change, layer switch, visibility toggle (H, Shift+H, the Layers eye), save, navigate (PgDn / PgUp), open, close, revert, reload and stage switch. Nothing is ever dropped silently. Undo and redo are refused while any layer's `gesture_active()` (C15). Esc on the mask commits the stroke: `MaskLayer.cancel_transient()` is `finish_gesture()` (there is no mask abort). A layer's own `set_tool`, `set_visible(False)`, `save`, `revert`, `open` and `close` call `finish_gesture()` first themselves, so the rule holds whoever calls them.
- **After a forced finish.** When `finish_gesture()` ends a drag before its release, the layer ignores the rest of that drag until the next `press`: moves with `dragging=True` only move the ring, and the release does nothing. The drag state lives in the layer (and its `PaintCanvas`), not in a tool object: the layer records which tool began the drag and forwards the drag only to it, so a release can never reach a tool that did not begin the drag.

### C3 `app/editing/journal.py` (P6; stdlib only)

```python
class EditJournal:
    """Chronological order of the undoable actions of one image, across layers.

    Each layer keeps its own undo stack; the journal only records which layer acted,
    newest last, so Ctrl+Z undoes "what I just did" on either layer.
    """

    DEFAULT_MAXLEN = 256

    def __init__(self, maxlen: int = DEFAULT_MAXLEN) -> None: ...
    def record(self, layer_id: str) -> None: ...  # new action: appended; the redo list is emptied
    def undo(self, step: Callable[[str], bool]) -> str | None: ...
    def redo(self, step: Callable[[str], bool]) -> str | None: ...
    def can_undo(self, probe: Callable[[str], bool]) -> bool: ...
    def can_redo(self, probe: Callable[[str], bool]) -> bool: ...
    def forget(self, layer_id: str) -> None: ...  # drop every undo and redo entry of that layer
    def clear(self) -> None: ...
    @property
    def undo_entries(self) -> tuple[str, ...]: ...  # oldest first
    @property
    def redo_entries(self) -> tuple[str, ...]: ...  # next redo last
    def __len__(self) -> int: ...  # number of undo entries
```

`undo(step)` takes the newest undo entry, calls `step(layer_id)`; `True` → the entry moves to the redo list and its layer id is returned; `False` (the layer evicted that action: its stack is empty, C2) → the entry is dropped and the next newest is tried; `None` when nothing is left. `redo` mirrors it. `can_undo(probe)` is True when some undo entry's `probe(layer_id)` is True. Past `maxlen` the oldest entries fall off.

### C4 History additions

- `app/editing/history.py` (P6): `SnapshotHistory.drop_redo(self) -> None` — forget the redo stack (another layer's new action ended this branch); calls `on_change` only if something was dropped.
- `app/gui/patch_history.py` (P4a): `PatchHistory.drop_redo(self) -> None` → `self._history.drop_redo()`.
- `app/gui/skeleton_graph_model.py` (P5a), on `SkeletonCorrectionModel`:
  - `on_action: Callable[[], None] | None` instance attribute (default `None`), called at the end of `push_undo()` when a snapshot was pushed (not when `mask is None`).
  - `drop_redo(self) -> None` → `self._history.drop_redo()`.
  - `take_changed_box(self) -> tuple[int, int, int, int] | None` — the `(x, y, w, h)` union of the pixels that may have changed since the last call, `(0, 0, 0, 0)` when nothing changed, `None` when unknown (after load, `set_empty`, undo, redo, the `mask` setter, a full-frame skeletonize); resets on each call. **Its only caller is `SkeletonLayer`'s item update (C14)**: a second caller would starve the item of boxes.
  - `skeletonize()` keeps its result bit-identical; it may use the ROI path of P5a Step 3 internally.
  - `skimage.morphology` is imported inside `_skeletonize_uint8` (lazy).
- `app/gui/skeleton_graph_model.py` (P5a), module level: `prewarm_skeletonize() -> None` — imports `skimage.morphology` (idempotent, safe from a worker thread). `MainWindow` calls it off the GUI thread after the window shows (C19).

### C5 `app/imaging/pyramid.py` (P1a; Qt-free: numpy + cv2)

```python
def area_half(img: np.ndarray) -> np.ndarray: ...  # cv2.INTER_AREA to (ceil(w/2), ceil(h/2)); any channel count
def max_half(img: np.ndarray) -> np.ndarray: ...  # 2x2 max-pool, ceil sizes (line overlays never break)


class ImagePyramid:
    """Level 0 = the image; level k = reducer(level k-1), built lazily and cached."""

    def __init__(
        self,
        image: np.ndarray,
        *,
        reducer: Callable[[np.ndarray], np.ndarray] = area_half,
        min_side: int = 32,
    ) -> None: ...  # image is kept by reference as level 0
    @property
    def count(self) -> int: ...  # levels down to min_side (at least 1)
    def level(self, k: int) -> np.ndarray: ...
    def level_size(self, k: int) -> tuple[int, int]: ...  # (w, h)
    def invalidate(self, rect: tuple[int, int, int, int] | None = None) -> None: ...
    @staticmethod
    def level_for_scale(device_scale: float, count: int) -> int: ...  # floor(-log2 s) for s < 1, else 0; clamped
```

`invalidate(rect)` means level-0 pixels inside `rect` changed; built levels are patched lazily (the next `level(k)` recomputes only the affected region) and equal a full rebuild. P1a's Step 1 may drop the built levels on `invalidate` (a working minimal body); the lazy patch comes in its Step 2.

### C6 `app/gui/canvas_layers` (P1a: `__init__`, photo, raster; P1b: skeleton)

```python
# app/gui/canvas_layers/__init__.py (P1a): imports nothing heavy
def device_scale(painter: QPainter) -> float: ...  # hypot(m11, m12) of painter.deviceTransform() (includes devicePixelRatio)
def __getattr__(name: str): ...  # PEP 562: PhotoLayerItem -> .photo, RasterLayerItem -> .raster, SkeletonLayerItem -> .skeleton
__all__ = ["device_scale", "PhotoLayerItem", "RasterLayerItem", "SkeletonLayerItem"]
# consumers import the submodules: from app.gui.canvas_layers.raster import RasterLayerItem


class PhotoLayerItem(QGraphicsItem):  # photo.py (P1a)
    """The photo, smooth at every zoom: an area-averaged pyramid level chosen from the
    device scale, drawn with bilinear filtering (exposed rect only)."""

    def __init__(self, parent: QGraphicsItem | None = None, *, smooth: bool = True) -> None: ...
    def set_image(self, image: np.ndarray | None) -> None: ...  # uint8 BGR (h, w, 3) or gray (h, w); kept by reference, never modified; None clears
    def image_size(self) -> tuple[int, int]: ...  # (w, h); (0, 0) when empty
    def pixmap(self) -> QPixmap: ...  # level 0 (null when empty); compatibility for tests
    def level_painted(self) -> int: ...  # level used by the last paint, -1 before any


class RasterLayerItem(QGraphicsItem):  # raster.py (P1a)
    """An editable ARGB canvas (a QImage read at paint time): exact pixels at device
    scale >= 1 from 256 px tile pixmaps, area coverage below 1."""

    TILE = 256

    def __init__(
        self, source: Callable[[], QImage | None], parent: QGraphicsItem | None = None
    ) -> None: ...
    def canvas_changed(self, rect: QRect) -> None: ...  # pixels in rect (image px) were painted
    def canvas_replaced(self) -> None: ...  # replaced, resized or changed wholesale
    def built_tiles(self) -> dict[tuple[int, int], int]: ...  # (tx, ty) -> cacheKey of that tile's pixmap (test hook)


class SkeletonLayerItem(QGraphicsItem):  # skeleton.py (P1b)
    """A 1 px skeleton raster drawn as a line at every zoom (spec section 10.3)."""

    VECTOR_MIN_SCALE = 2.0
    TILE = 256  # vector extraction tile, image px
    EXTRACT_BUDGET_MS = 4.0  # vector extraction per paint
    GL_SEGMENT_BUDGET = 6000
    RASTER_HALO_BUDGET = 400
    RASTER_THIN_BUDGET = 2500

    def __init__(
        self,
        parent: QGraphicsItem | None = None,
        *,
        color: str = tokens.SKEL,
        halo: bool = True,
        vectors: bool = True,
    ) -> None: ...
    def set_mask(
        self,
        mask: np.ndarray | None,
        *,
        changed: tuple[int, int, int, int] | None = None,
        scale: tuple[float, float] = (1.0, 1.0),
    ) -> None: ...  # uint8 0/255, kept by reference; changed=None: everything changed; O(box), never extracts vectors
    def revision(self) -> int: ...  # +1 per set_mask (test hook)
    def mode_painted(self) -> str: ...  # "raster-lod" | "raster" | "raster-smooth" | "vector" | "vector-thin" | "" (test hook)
    def vector_tiles(self) -> dict[tuple[int, int], int]: ...  # (tx, ty) -> tile revision of its cached segments (test hook)
```

- **Working minimal bodies first** (P1a Step 1, P1b Step 1), never `NotImplementedError`: `PhotoLayerItem` = a smooth (`SmoothPixmapTransform`) draw of the level-0 pixmap over the exposed rect; `RasterLayerItem` = a nearest draw of the exposed rect of the source `QImage` (today's `CanvasPixmapItem` logic); `SkeletonLayerItem` = today's `SkeletonOverlayRenderer` pixmap (line + halo; line only with `halo=False`), drawn nearest with `scale` as the item transform. Tiles, pyramids and vectors come later in the package without changing the API. The finished `SkeletonLayerItem` uses only `overlay_indices`, `LINE_INDEX`, `HALO_INDEX` and `HALO_ALPHA` from `skeleton_overlay` (P8b deletes `SkeletonOverlayRenderer`).
- All three set `ItemUsesExtendedStyleOption`, paint `option.exposedRect` only, restore every render hint they change, and use `device_scale(painter)` for every threshold. Their `boundingRect()` is `(0, 0, w, h)` in their own pixels; `SkeletonLayerItem` applies `scale` as its item transform (`setTransform(QTransform.fromScale(sx, sy))`), so its thresholds count device pixels per skeleton pixel.
- `RasterLayerItem` and the nearest tiers of `SkeletonLayerItem` paint with `Antialiasing` and `SmoothPixmapTransform` off, so tile edges never blend under the view's `Antialiasing` hint (C8), and restore the painter's hints.
- `SkeletonLayerItem` raster overlay: `app.gui.skeleton_overlay.overlay_indices` with the colours `color` (line) and `tokens.BG_0` at alpha `skeleton_overlay.HALO_ALPHA` (halo); `halo=False` draws the line only. `set_mask(changed=box)` recomputes the overlay only for `box` padded by 1 px (the halo's 3x3 dilation) and bumps the revision of the vector tiles the padded box touches (links cross tile borders); `changed=None` rebuilds the overlay and invalidates every tile. `set_mask` never extracts vectors.
- **Tiers with `vectors=True`** (Correct), by device scale `s`: `s < 1` → the overlay through a `max_half` pyramid, nearest; `1 <= s < 2` → overlay level 0, nearest; `s >= 2` → vector links between 8-neighbour pixel centres (m-adjacency, isolated pixels as dots, collinear runs merged inside a tile). Vectors are extracted lazily at paint time, per `TILE` of the exposed rect, and cached with the tile's revision; extraction reads a 1 px margin and a link belongs to the tile of its first pixel (row-major), so every link is extracted once. One paint spends at most `EXTRACT_BUDGET_MS` extracting; a tile not yet extracted draws the level-0 overlay (nearest) and the item schedules one `update()`, so the next frames finish the job. With the exposed tiles' segments: on an OpenGL paint engine a 2 px cosmetic line over a 4 px halo up to `GL_SEGMENT_BUDGET` visible segments; on the raster engine 2 px + halo up to `RASTER_HALO_BUDGET`, a 1 px cosmetic line up to `RASTER_THIN_BUDGET`, otherwise the level-0 overlay. Antialiasing on for vectors only.
- **`vectors=False`** (the Library, P3; its input is a generated file with lines about 2 px wide, not a 1 px skeleton): no vector and no nearest tier. `s < 1` → the `max_half` pyramid, nearest, as above; `s >= 1` → the level-0 overlay drawn with bilinear filtering (`SmoothPixmapTransform` on): today's Library look, soft edges and no pixel blocks. `mode_painted()` is `"raster-lod"` / `"raster-smooth"`.

### C7 View, zoom and the status chip (P1a)

- `CanvasViewMixin` (`app/gui/canvas_view.py`) gains:
  ```python
  ZOOM_STOPS: tuple[float, ...] = (0.1, 0.125, 0.167, 0.25, 0.333, 0.5, 0.667, 0.8, 1.0, 1.25,
                                   1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0)
  def zoom_step(self, direction: int, anchor: QPointF | None = None) -> None: ...
  ```
  `zoom_step(+1)` goes to the smallest stop above the current zoom (tolerance 1e-3), `-1` to the largest below; clamped to `MIN_ZOOM`/`MAX_ZOOM`. Ctrl+= / Ctrl++ / Ctrl+- call it. `KEY_STEP` is removed; `WHEEL_STEP = 1.2` and the continuous wheel stay; `MIN_ZOOM` stays `0.1`, `MAX_ZOOM` `20.0`. `set_zoom`, `zoom_by`, `fit_to_view`, `zoom_changed` are unchanged. The GL viewport requests `canvas_gl.gl_samples()` MSAA samples (`QSurfaceFormat` on the `QOpenGLWidget`).
- `app/gui/canvas_gl.py` gains `SAMPLES_ENV_VAR = "ROOT_VIEWER_GL_SAMPLES"` and `def gl_samples() -> int` (unset or invalid → 4; `0` disables; clamped to 0..16).
- `ZoomCluster` (`app/gui/widgets/overlays.py`): the − / + buttons call `view.zoom_step(-1)` / `view.zoom_step(+1)`; the readout keeps click = 100 % ⇄ fit and gains a context menu (right-click) with Fit, 50 %, 100 %, 200 %, 400 %, 800 %, 1600 %. `ZOOM_IN_STEP` / `ZOOM_OUT_STEP` stay defined until the swap (the old mask editor reads them).
- `StatusChip.set_hint(self, text: str, tooltip: str = "", *, dot: str | None = None) -> None` (`app/gui/widgets/overlays.py`): `dot` is a colour from `tokens` (a layer's `color`, e.g. `tokens.MASK`); when given, a separate `hint_dot` `QLabel` ("●" in that colour) shows before the hint text; `None` hides it. `hint_label` stays `PlainText`, and existing `set_hint(text, tooltip)` callers are unchanged. The Correct editor shows `"<Layer> · <tool hint>"` with `dot=layer.color` (spec section 3.2).

### C8 `app/gui/editor_canvas.py` (P2)

```python
class InputSink(Protocol):
    """Where EditorCanvasView sends tool input (positions in image pixels)."""

    def canvas_size(self) -> QSize: ...  # image size; empty = nothing to edit
    def canvas_press(self, pos: QPoint, event: QMouseEvent) -> bool: ...  # True: a drag starts
    def canvas_move(self, pos: QPoint, event: QMouseEvent, dragging: bool) -> None: ...
    def canvas_release(self, pos: QPoint, event: QMouseEvent) -> None: ...
    def canvas_double_click(self, pos: QPoint, event: QMouseEvent) -> None: ...
    def canvas_wheel(self, event: QWheelEvent) -> bool: ...  # True: consumed (e.g. B + wheel)
    def canvas_key_press(self, event: QKeyEvent) -> bool: ...  # True: consumed
    def canvas_key_release(self, event: QKeyEvent) -> bool: ...
    def canvas_hover_left(self) -> None: ...  # the pointer left the canvas
    def canvas_pan_changed(self, panning: bool) -> None: ...


class EditorCanvasView(CanvasViewMixin, QGraphicsView):
    """The canvas of every editor (Correct and Trace GT): zoom/pan/fit from the mixin,
    tool input to an InputSink."""

    def __init__(self, sink: InputSink | None = None, parent: QWidget | None = None) -> None: ...
    def set_sink(self, sink: InputSink | None) -> None: ...
    def map_to_image(self, pos: QPointF) -> QPoint: ...  # floor to a pixel, clamp to canvas_size()
    def end_gesture(self) -> None: ...  # forget a drag in progress: drawing = False
```

Behaviour: `_init_canvas()` then `configure_canvas_background(self)`; `NoFrame`. Render hints: `Antialiasing | TextAntialiasing` (decorations, rings and previews stay antialiased on the raster viewport, as `SkeletonCorrectionGraphicsView` does today); no view-level `SmoothPixmapTransform` (the items decide). Saved pixels are unaffected: `PaintCanvas` paints with its own `QPainter`, antialiasing off. Left press (not panning, image open) → `drawing = sink.canvas_press(...)`, event accepted. Mouse moves that are not pans → `canvas_move(pos, ev, dragging=self.drawing)` (hover included); a left drag that did not start with an accepted press is not forwarded. Left release while drawing → `drawing = False`, `canvas_release`. Double-click → `canvas_double_click`. Wheel: ignored while drawing; `canvas_wheel` first, then the mixin (Ctrl+wheel zoom, plain wheel scroll). Keys: `canvas_key_press` / `canvas_key_release` first, then the mixin (Ctrl+0/1/=/-, Space, PgUp/PgDn left to parents). `leaveEvent` → `canvas_hover_left()`; `refresh_cursor()` → `canvas_pan_changed(self.is_panning_mode())`. `scrollContentsBy` and `ensureVisible` do nothing while drawing. `end_gesture()` sets `drawing = False`: the rest of that drag reaches the sink as hover moves and its release is not forwarded; the editor calls it on open, layer switch, navigation and stage switch (C17). `sink=None` gives a plain canvas.

### C9 `app/gui/paint_canvas.py` (P2)

```python
Rect = tuple[int, int, int, int]


def pixmap_to_argb_array(canvas: QImage | QPixmap) -> np.ndarray: ...  # (h, w, 4) uint8 B,G,R,A of Format_ARGB32 (not premultiplied); a copy
def argb_array_to_image(arr: np.ndarray) -> QImage: ...  # an owning QImage in Format_ARGB32


class PaintCanvas:
    """The editable ARGB canvas over a PatchHistory plane (Correct mask layer, Trace GT).

    Painting is today's MaskDrawingMixin, moved verbatim (mask_drawing_tools.py:85-223):
    pixel centres, round caps, no antialiasing, CompositionMode_Source / DestinationOut,
    dirty rect = segment box padded by r + 2. The canvas is a QImage in
    Format_ARGB32_Premultiplied, so painting a segment never copies it.
    """

    def __init__(
        self,
        history: PatchHistory,
        *,
        lut: Callable[[], np.ndarray],  # (256, 4) uint8 B,G,R,A per plane value
        to_plane: Callable[[np.ndarray, Rect], np.ndarray],  # ARGB box -> plane values
        fill_source: Callable[[np.ndarray], tuple[np.ndarray, int]],  # (0/255 outline image, value)
        on_region: Callable[[QRect], None],  # canvas pixels in rect changed
        on_replaced: Callable[[], None],  # canvas replaced or changed wholesale
        on_patch: Callable[[Patch, bool], None] | None = None,  # (patch, forward) after commit/undo/redo
    ) -> None: ...
    history: PatchHistory
    image: QImage | None  # None while history.plane is None
    def rebuild(self) -> None: ...  # whole canvas from history.plane via Indexed8 + colour table; on_replaced()
    def begin(self, pos: QPoint, color: QColor, size: int, *, erase: bool) -> None: ...  # first dab
    def extend(self, pos: QPoint) -> None: ...  # segment from the last point (begin's colour/size/erase)
    def in_stroke(self) -> bool: ...
    def commit(self) -> Patch | None: ...  # read the stroke box back -> history.commit; None if nothing painted
    def fill(self, pos: QPoint) -> Patch | None: ...  # flood_fill verbatim; None outside every closed outline (no history entry)
    def clear(self) -> Patch | None: ...  # _clear_canvas verbatim (zeros committed, image filled transparent); None if no plane
    def undo(self) -> Patch | None: ...  # history.undo + repaint of patch.rect
    def redo(self) -> Patch | None: ...
    def render_rect(self, rect: Rect) -> None: ...  # _render_plane_rect verbatim
```

`commit`, `fill`, `clear`, `undo`, `redo` call `on_patch(patch, forward)` (forward False only for undo) and repaint through `on_region` / `on_replaced`. `rebuild` produces exactly the pixels of today's `argb_array_to_pixmap(lut[plane])` (the architecture scout verified Indexed8 + convert gives identical pixels, 12-15x faster). There is no abort: a stroke in progress is always committed (C2). `in_stroke()` is False after `commit()`; the mask layer uses it to ignore the rest of a drag after a forced finish.

### C10 `app/gui/editor_chrome.py` (P2)

```python
class EditorChrome:
    """The floating overlays every canvas editor shares, placed by one OverlayLayout
    over the editor widget: tool rail (empty), dock (empty), Enhance popover, status
    chip, zoom cluster, plus the Pan and Enhance rail buttons."""

    def __init__(self, host: QWidget, view: EditorCanvasView, norm: NormalizationControls) -> None: ...
    rail: ToolRail
    dock: FloatingDock
    popover: EnhancePopover  # content = norm
    chip: StatusChip  # top-left, with its hint line
    zoom: ZoomCluster  # bound to view; fit = view.fit_to_view()
    layout: OverlayLayout  # host = the editor widget; roles rail, dock, popover, zoom_cluster, top_chip
    pan_button: IconButton  # checkable "hand"; toggled -> view.set_mode(checked); not yet on the rail
    enhance_button: IconButton  # checkable "contrast"; toggles the popover and follows it; not yet on the rail
    def register(self, **roles: QWidget | None) -> None: ...  # more roles: side_panel, prompt
    def relayout(self) -> None: ...
```

It calls `view.set_fit_insets_provider(layout.insets)`, gives focus back to the view when the popover closes, and applies `keep_canvas_focus` to the rail, dock and popover buttons. The editor adds its own tool buttons and places `pan_button` / `enhance_button` on the rail.

### C11 `app/gui/editor_prompts.py` (P2)

```python
SAVE, DISCARD, CANCEL, KEEP = "save", "discard", "cancel", "keep"


def join_nouns(nouns: Sequence[str]) -> str: ...  # ["mask"] -> "mask"; ["mask", "skeleton"] -> "mask and skeleton"
def ask_unsaved(parent: QWidget | None, noun: str, name: str) -> str: ...  # SAVE | DISCARD | CANCEL
def ask_after_failed_save(parent: QWidget | None, noun: str, name: str) -> str: ...  # DISCARD | KEEP
```

Texts are today's (`mask_tracing_interface.py:550-576`): `ask_unsaved` → question icon, title `f"Unsaved {noun}"`, text `f"Save changes to the {noun} for {name}?"`, informative `"Discarded edits cannot be recovered."`, buttons Save / Discard / Cancel, default Save, Esc = Cancel. `ask_after_failed_save` → warning icon, title `f"{noun[:1].upper()}{noun[1:]} not saved"`, text `f"The {noun} for {name} could not be saved."`, informative `"Discard the edits, or keep editing? Discarded edits are lost."`, buttons Discard and Cancel renamed "Keep editing", default and Esc = Keep editing. The box is freed after `exec()` (`deleteLater`).

### C12 Enhancement (P2)

- `app/enhancement.py`: `SourceImageCache.get` decodes with `cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION` (Qt's geometry: `QImageReader.autoTransform` is off, so a stored 80x40 JPEG with orientation 6 stays 80x40).
- `NormalizationControls` gains `source_image(path: str) -> np.ndarray | None` (the cached decoded BGR array, read-only, same object on repeat calls) and `display_image(path: str) -> np.ndarray | None` (`apply_to(source_image(path))`). `display_pixmap` stays.

### C13 Mask layer (P4a: document; P4b: layer and tools)

```python
# app/gui/correct/mask_document.py (P4a, Qt-free)
@dataclass(frozen=True)
class MaskSaveResult:
    ok: bool
    path: str | None = None
    removed: bool = False  # an empty mask removed an existing file
    error: str = ""


class MaskDocument:
    """The mask of one image: committed plane + patch history + saved baseline + MaskStore I/O."""

    def __init__(self, *, on_history_change: Callable[[], None] | None = None) -> None: ...
    history: PatchHistory  # .plane: uint8 0/1 (h, w)
    image_path: str  # "" when closed
    has_saved_file: bool
    @property
    def revision(self) -> int: ...
    def open(self, image_path: str, size: tuple[int, int]) -> list[Notice]: ...  # mask_tracing_interface.py:722-747
    def close(self) -> None: ...
    def is_open(self) -> bool: ...
    def touch(self) -> None: ...  # the plane changed: revision += 1
    def is_dirty(self) -> bool: ...  # plane != baseline, memoized per revision
    def save(self) -> MaskSaveResult: ...  # mask_tracing_interface.py:757-792 without Qt
    def revert(self) -> None: ...  # plane := baseline copy, history cleared, touch()
    def status(self) -> LayerStatus: ...
    def file_stamps(self) -> tuple: ...  # every mask candidate path, stamped or absent (below)


# app/gui/correct/mask_layer.py (P4b)
class MaskLayer:  # implements EditLayer
    layer_id = MASK
    noun = "mask"
    color = tokens.MASK
    DEFAULT_SIZE = 8
    SIZE_RANGE = (1, 100)
    DEFAULT_OPACITY = 0.60
    Z = 1.0
    CLEARED_NOTICE = "Mask cleared. Ctrl+Z to undo, Save to remove the file."
    FILL_MISS_NOTICE = "Fill works inside a closed outline. Close the loop first."

    def __init__(self, host: LayerHost, scene: QGraphicsScene) -> None: ...
    document: MaskDocument
    canvas: PaintCanvas
    item: RasterLayerItem  # z 1.0, added to scene
    ring: BrushPreview
```

- `tools = (ToolSpec("brush", "brush", "Brush", "B", "hold B + scroll to resize", True), ToolSpec("eraser", "erase", "Eraser", "E", "[ and ] resize", True), ToolSpec("fill", "fill", "Fill a closed outline", "F", "click inside a closed outline", False))`. Tools live in `app/gui/correct/mask_tools.py` (`MaskTool` base with the `LayerTool` shape; `BrushTool`, `EraserTool`, `FillTool`).
- The layer's `PaintCanvas` gets today's `MaskTracingInterface._plane_lut` / `_argb_to_plane` / `_fill_source` (`mask_tracing_interface.py:702-712`, value 1 drawn opaque in `tokens.MASK`); strokes use `QColor(tokens.MASK)`, the layer's brush size and `erase=(current_tool == "eraser")`.
- **File stamps (pinned):** `MaskDocument.file_stamps()` returns one entry per path of `MaskStore.for_image(image_path).layout.mask_candidates(stem)`, in lookup order: `(path, mtime_ns, size)` when the file exists, `(path, None)` when it does not; `()` when closed. It is read afresh on each call and compares with `==`. `MaskLayer.file_stamps()` returns the document's.
- `double_click(pos)` does nothing: Qt delivers the second click of a double-click as a double-click, not a press, so a double-click paints one dab and records one undo entry, as today.
- `finish_gesture()` commits a stroke in progress (`canvas.commit()` → history → `layer_edited`); `cancel_transient()` is `finish_gesture()`. After a forced finish, `move(pos, dragging=True)` only moves the ring and `release` does nothing while `canvas.in_stroke()` is False (C2).
- `layer_changed` fires at commit, undo, redo, save, revert, open, close and tool changes, never per stroke segment (C2).

### C14 Skeleton layer (P5a: document and model; P5b: layer)

```python
# app/gui/correct/skeleton_document.py (P5a, Qt-free)
LOADED = "loaded"


@dataclass(frozen=True)
class SkeletonSaveResult:
    ok: bool
    path: str | None = None
    error: str = ""


class SkeletonDocument:
    """The skeleton of one image: model + which file it came from + where it saves."""

    def __init__(self, resolver: GeneratedResolver | None = None) -> None: ...
    model: SkeletonCorrectionModel
    image_path: str
    images_base_folder: str | None
    source: str | None  # CORRECTED | GENERATED | LOADED | None
    save_size: tuple[int, int] | None  # (w, h) of the loaded raster
    save_error: str
    error_revision: int
    def set_resolver(self, resolver: GeneratedResolver | None) -> None: ...
    def files(self) -> SkeletonFiles: ...
    def open(self, image_path: str, size: tuple[int, int], images_base_folder: str | None) -> list[Notice]: ...  # skeleton_correction_interface.py:487-508
    def reopen(self) -> list[Notice]: ...  # re-read the start file (Discard), history cleared
    def load_file(self, path: str) -> list[Notice]: ...  # skeleton_correction_interface.py:521-532 minus the dialog
    def close(self) -> None: ...
    def is_open(self) -> bool: ...
    def is_dirty(self) -> bool: ...  # model.has_unsaved_changes()
    def save(self) -> SkeletonSaveResult: ...  # skeleton_correction_interface.py:534-557 without Qt
    def status(self) -> LayerStatus: ...  # skeleton_correction_interface.py:568-589
    def file_stamps(self) -> tuple: ...  # the correction and the resolved generated path (below)


# app/gui/correct/skeleton_layer.py (P5b)
@dataclass(frozen=True)
class DraftState:
    in_polyline: bool
    points: int
    smooth: bool


class SkeletonLayer:  # implements EditLayer and app.gui.skeleton_tools.ToolHost
    layer_id = SKELETON
    noun = "skeleton"
    color = tokens.SKEL
    DEFAULT_ERASER = 20
    ERASER_RANGE = (1, 80)
    DEFAULT_OPACITY = 0.85
    RENDER_INTERVAL_MS = 16
    Z = 1.5
    CLEARED_NOTICE = "Skeleton cleared. Ctrl+Z to undo."
    DRAFT_DISCARDED_NOTICE = "Polyline draft discarded"

    def __init__(self, host: LayerHost, scene: QGraphicsScene, *, resolver: GeneratedResolver | None = None) -> None: ...
    document: SkeletonDocument
    item: SkeletonLayerItem  # z 1.5, below every decoration (2-3.5)
    decorations: SkeletonDecorations
    eraser_ring: BrushPreview
    # ToolHost: model (property -> document.model), draft, decorations, draw_thickness = 3,
    # clamp_to_image, image_size, eraser_footprint_radius, render_skeleton, schedule_render,
    # cancel_scheduled_render, refresh_draft, refresh_hint, switch_tool, show_eraser_ring, notify
    def set_resolver(self, resolver: GeneratedResolver | None) -> None: ...
    def dialog_start(self) -> str: ...
    def load_file(self, path: str) -> list[Notice]: ...  # forgets its own history; the session forgets the journal
    def draft_state(self) -> DraftState: ...
    def finish_polyline(self) -> None: ...
    def cancel_polyline(self) -> None: ...
    def set_smooth(self, on: bool) -> None: ...
```

- `tools = (ToolSpec("select", "cursor", "Select", "V", "pick a branch, Delete removes it", False), ToolSpec("eraser", "erase", "Eraser", "E", "[ and ] resize", True), ToolSpec("polyline", "polyline", "Polyline", "P", "Enter finish · Esc cancel", False), ToolSpec("connect", "link", "Connect endpoints", "C", "drag from an endpoint", False))`. `app/gui/skeleton_tools.py` is used unchanged.
- **File stamps (pinned):** `SkeletonDocument.file_stamps()` returns two entries, the correction path (`files().correction_path`) and the resolved generated path (`files().generated_path()`, or `files().layout.skeleton_path(stem)` when that is None), each `(path, mtime_ns, size)` when the file exists and `(path, None)` when it does not; `()` when closed. It is computed afresh on each call (the resolver is asked again), so our own first save (which creates the correction) and a Generate run (which creates or rewrites the generated file) both change it. This is what makes re-entry after our own save reload the saved correction, as today (spec D11, Q1).
- **The item update** is `item.set_mask(model.mask, changed=model.take_changed_box())`, the only caller of `take_changed_box()` (C4). Coalesced drag renders (`render_skeleton(full=False)`) fire no `layer_changed`; the release and every full render do (C2).
- **Gestures:** `gesture_active()` is True during an eraser drag, a Connect drag and a polyline point drag. `finish_gesture()` sends `release(last_pos)` to the tool that began the drag; `cancel_transient()` (Esc) is today's `_clear_transient()` (the tool's `deactivate()`: an eraser drag keeps what it erased, a Connect drag and the polyline draft are dropped), after which the rest of the drag is ignored too.
- **Undo / redo** follow C2: one snapshot step, True iff done, False only on an empty stack; they drop a polyline draft (today's `_step_history`) and, when a draft existed while the layer was inactive, notify `DRAFT_DISCARDED_NOTICE` (info). `undo_in_gesture()` pops the last draft point.
- Endpoint markers: `decorations.endpoints_item.setCacheMode(DeviceCoordinateCache)`, shown only while the layer is active.

### C15 `app/gui/correct/session.py` (P6; Qt-free)

```python
class Prompts(Protocol):
    def ask_unsaved(self, parent: object, noun: str, name: str) -> str: ...
    def ask_after_failed_save(self, parent: object, noun: str, name: str) -> str: ...


@dataclass(frozen=True)
class SaveReport:
    outcomes: tuple[SaveOutcome, ...] = ()
    @property
    def ok(self) -> bool: ...  # every outcome ok (True when empty)
    @property
    def failed(self) -> tuple[str, ...]: ...  # layer ids that failed


class CorrectSession:
    """Document lifecycle of the Correct workspace: open/close, the one undo timeline,
    dirty aggregation, save and the one unsaved-changes prompt."""

    def __init__(
        self,
        layers: Sequence[EditLayer],
        *,
        prompts: Prompts,
        parent: Callable[[], object],
        on_dirty_changed: Callable[[bool], None],
        on_notice: Callable[[str, str], None],
        on_history_changed: Callable[[], None],
        on_undone: Callable[[str, str], None],  # (layer_id, "undo" | "redo")
        on_saved: Callable[[SaveOutcome], None],  # once per layer save attempt, failed ones included
    ) -> None: ...
    layers: dict[str, EditLayer]  # by id, LAYER_IDS order
    journal: EditJournal
    image_path: str  # "" when nothing is open
    def open(self, image_path: str, size: tuple[int, int], images_base_folder: str | None) -> None: ...
    def reload_documents(self) -> None: ...  # same image, files changed: reopen every layer, journal cleared
    def close(self) -> None: ...
    def files_changed(self) -> bool: ...  # any layer's file_stamps() differ from those at open
    def image_changed(self) -> bool: ...  # the image file's stamp differs from the one at open
    def finish_gestures(self) -> None: ...  # finish_gesture() on every layer whose gesture_active()
    def layer_edited(self, layer_id: str) -> None: ...  # journal.record, other layers drop_redo, sync
    def layer_changed(self, layer_id: str) -> None: ...  # dirty + history sync
    def forget(self, layer_id: str) -> None: ...  # journal.forget + sync (Load skeleton file)
    def is_dirty(self) -> bool: ...
    def dirty_layers(self) -> list[str]: ...  # LAYER_IDS order
    def save(self, active_id: str) -> SaveReport: ...  # dirty layers; none dirty -> the active layer
    def save_dirty(self) -> SaveReport: ...  # PgDn: dirty layers only
    def release(self, layer_ids: Sequence[str] | None = None) -> bool: ...  # the prompt; True = safe to move on
    def reset(self) -> bool: ...  # release, then close
    def undo(self, active_id: str) -> str | None: ...
    def redo(self) -> str | None: ...
    def can_undo(self) -> bool: ...
    def can_redo(self) -> bool: ...
```

- **Gestures first.** `save`, `save_dirty`, `release`, `open`, `close` and `reload_documents` call `finish_gestures()` first, before `dirty_layers()`, so a stroke committed by it is saved or prompted for like any other edit. The editor calls `finish_gestures()` before its other transitions (C17).
- `save`: each target first gets `finish_gesture()`; mask before skeleton; a failure does not stop the next layer. Toasts through `on_notice`: one `"Saved <join_nouns(written)> · <image basename>"` success toast for the layers whose outcome has a `path`; every other non-empty `message` of an ok outcome with its `kind` (an emptied mask: `"Empty mask: removed saved mask for <name>"`, info); each failure's `message` with its `kind` (danger). Nothing written and nothing to say (an empty mask with no file) → no toast. A failed layer stays dirty.
- **Save reports.** `on_saved(outcome)` is called once per layer save attempt, right after that layer's `save()` returns, from `save`, `save_dirty` and the Save branch of `release`, whether it succeeded or failed; every retry (the user saves again after a failure) is a new attempt and a new call. The editor turns outcomes into signals (C17); toasts stay `on_notice`'s job.
- Outcome texts the layers produce: mask written → `path` set, no message; mask emptied with a file removed → `cleared=True`, the message above; mask failed → `f"Could not save mask to {path}: {error}"` / `f"Could not remove mask: {error}"`, danger; skeleton written → `path` = the correction file; skeleton failed → `f"Could not save {file name}: {error}"`, danger (today's texts).
- `release(ids)`: `finish_gestures()`; dirty layers among `ids` (all when None); none → True. Otherwise `prompts.ask_unsaved(parent(), join_nouns(nouns), basename)`: CANCEL → False; DISCARD → `revert()` each (their notices forwarded), `journal.forget` each → True; SAVE → save them (each reported through `on_saved`); all ok → True; else `ask_after_failed_save` naming the failed layers: DISCARD → revert those → True; KEEP → False.
- `undo(active)`: None while any layer `gesture_active()`; else if `layers[active].undo_in_gesture()` → return active (not a journal step); else `journal.undo(lambda i: layers[i].undo())` with stale skipping (a layer's False means its stack is empty, C2); on success `on_undone(id, "undo")` and sync. `redo` mirrors (no gesture pop).
- `on_dirty_changed` fires only when the aggregate changes; `on_history_changed` after every journal or layer history change.

### C16 `app/gui/correct/tool_router.py` (P6)

```python
class ToolRouter:  # implements editor_canvas.InputSink
    """Canvas input of the Correct workspace: pointer to the active layer, B hold/tap,
    gesture keys to the active layer's tool."""

    def __init__(
        self,
        layers: Mapping[str, EditLayer],
        *,
        canvas_size: Callable[[], QSize],
        on_tool_request: Callable[[str, str], None],  # (layer_id, tool_id): B tap -> (MASK, "brush")
        on_b_hold: Callable[[bool], None],
        on_layer_shown: Callable[[str], None],  # a hidden active layer was shown to be edited
        active: str = MASK,
    ) -> None: ...
    active_id: str
    b_held: bool
    def set_active(self, layer_id: str) -> bool: ...  # finishes a gesture first, then switches; False if already active
```

Press: active layer not open → False; hidden → `set_visible(True)` + `on_layer_shown`; then `layer.press(pos, event.modifiers())` → True. Move → `layer.move(pos, dragging)`. Release → `layer.release(pos)`. Double-click → `layer.double_click(pos)`. Wheel with `b_held` → `layers[MASK].step_size(±1)`, the tap is cancelled, consumed. Key press: B (no modifier or keypad, not auto-repeat) → hold; B release → end of hold, a tap (no wheel) → `on_tool_request(MASK, "brush")`; Esc → `cancel_transient()` of the active layer (the mask commits its stroke, C2); any other key → `layer.key(event)`; Space is never consumed. Hover left / pan started → `layer.hover(None)`. `set_active(layer_id)`: when any layer's `gesture_active()`, calls its `finish_gesture()` first (C2), then makes `layer_id` active; returns True when the active layer changed, False when it was already active.

### C17 `CorrectEditor` (P7b) — the MainWindow-facing API (`app/gui/correct/editor.py`)

```python
class CorrectEditor(QWidget):
    notice = pyqtSignal(str, str)
    dirty_changed = pyqtSignal(bool)
    mask_saved = pyqtSignal(str)  # image path: a mask file was written
    mask_cleared = pyqtSignal(str)  # image path: an empty mask removed the saved file
    skeleton_saved = pyqtSignal(str)  # correction path (FR-03: MainWindow does not connect it)
    b_key_status_changed = pyqtSignal(bool)
    navigate_requested = pyqtSignal(int)  # PgDn/PgUp after the dirty layers were saved
    active_layer_changed = pyqtSignal(str)

    EDITOR_NOUN = "mask and skeleton"
    SHORTCUT_SCOPE = shortcuts.CORRECT

    graphics_view: EditorCanvasView
    dock: FloatingDock
    clear_button: QPushButton  # controls.clear_button: topbar ghost "Clear mask" / "Clear skeleton" (shell_chrome reparents it)
    @property
    def current_image_path(self) -> str: ...
    @property
    def active_layer_id(self) -> str: ...
    def load_image(self, image_path: str, images_base_folder: str | None = None) -> None: ...
    def has_unsaved_changes(self) -> bool: ...
    def maybe_save_changes(self) -> bool: ...  # False = the caller stays
    def reset(self) -> bool: ...
    def navigate(self, delta: int) -> None: ...  # save dirty layers, then emit navigate_requested(delta)
    def set_generated_skeleton_resolver(self, resolver: GeneratedResolver | None) -> None: ...
    def set_active_layer(self, layer_id: str) -> bool: ...  # True when the active layer changed
    def save(self) -> bool: ...
    def undo(self) -> None: ...
    def redo(self) -> None: ...
    def clear_active_layer(self) -> None: ...
    def apply_normalization(self) -> None: ...
```

- It also exposes, for tests: `scene`, `photo_item`, `layers` (`dict[str, EditLayer]`), `session`, `router`, `chrome`, `controls`, `layer_panel`, `norm_controls`, `status_chip`, `tool_rail`, `zoom_cluster`, `enhance_popover`, `save_button`, `undo_button`, `redo_button`. It installs its own PgDown / PgUp `QShortcut`s (documented by the Global rows, like Trace GT).
- **Save reports → signals.** The session's `on_saved(outcome)`: mask, `ok` and a `path` → `mask_saved(image_path)`; mask, `ok` and `cleared` → `mask_cleared(image_path)`; skeleton, `ok` and a `path` → `skeleton_saved(path)`; a failed outcome emits nothing. Every save path goes through the session (Ctrl+S, PgDn / PgUp, the prompt's Save from a tree click, arrow keys, stage switch, window close, open folder or Load skeleton file), so each written mask emits `mask_saved` exactly once.
- **Gestures.** Every handler of a C2 transition — tool request (rail, F / E / V / P / C, B tap), layer switch (1 / 2 / X, rail, Layers row), H / Shift+H / the Layers eye, Ctrl+S, PgDn / PgUp, Load skeleton file, Clear, `maybe_save_changes` / `reset` (stage switch, close) — starts with `session.finish_gestures()` (the session's own methods do it too). `graphics_view.end_gesture()` runs on open (`load_image`), layer switch, navigation and in `maybe_save_changes`.
- `install_shortcuts` returns the `QShortcut`s; the editor calls `setAutoRepeat(False)` on those of `swap_layer` (X), `toggle_layers` (H) and `toggle_reference` (Shift+H), so holding them does not flicker.
- The chip hint is `status_chip.set_hint(f"{Layer} · {hint}", tooltip, dot=active_layer.color)`.

### C18 Test support

- `tests/data/correct_goldens.json` (P4a): the scout's `goldens.json` reduced to `meta`, `ops` and the two committable cases `synth_model_mask_and_skeleton`, `synth_blank_png` (`json.dump(..., indent=1, sort_keys=True)`).
- `tests/correct_goldens_support.py` (P4a), copied from `scratch/contracts-tests/goldens/capture_goldens.py`: `GOLDENS`, `load_goldens() -> dict`, `SYNTH_STEM`, `synth_inputs(folder, with_mask, with_skel) -> str`, `make_synth_project(root, case_name) -> tuple[str, str]` (folder, image path; `synth_model_mask_and_skeleton` = mask + skeleton, `synth_blank_png` = neither), `sha(arr)`, `file_sha(path)`, `png_colour_type(path)`, `describe_mask_file(path)`, `describe_skeleton_file(path)`. Op semantics (all drivers): `["brush", n]` / `["eraser", n]` select the tool and set the size; `["fill"]`; `["stroke", pts]` = press, move(dragging) per point, release; `["click", p]` = press + release; `["tool", t]`; `["smooth", b]`; `["eraser_size", n]`; `["drag", pts]`; `["connect_endpoints", i, j]` on `sorted(model.endpoints())`; `["double_click", p]`; `["key", "Delete"]`; `["finish"]`; `["clear"]`; `["undo"]` = pop a draft point if any, else undo; `["redo"]`; `["save"]`.
- `tests/render_metrics.py` (P1a): `grab(view) -> np.ndarray` (RGB of `viewport().grab()`), `equal_neighbour_fraction`, `mean_run`, `sine_image`, `sine_step_ratio`, `chirp_image`, `chirp_alias_std`, `stripes_image`, `zone_plate(n)`, `ring_std`, `psnr`, `render_view(items, zoom, size)` helper; ported from `scratch/contracts-tests/render/render_metrics.py` and `scratch/zoom-render/zr_common.py`. P1b may import it read-only (if it does not exist yet, P1b keeps private helpers in its own test file).
- `tests/correct_fakes.py` (P6): `FakeLayer` implementing `EditLayer` with call recording, scriptable dirty / save / undo / stamps results, a bounded fake history (to simulate eviction) and a scriptable gesture (`gesture_active()` True until `finish_gesture()`, which can be set to make the layer dirty).

### C19 The shell after the swap (P8b; for P7b's and P8a's information)

`Panel`: `DISPLAY = 0`, `CORRECT = 1`, `VISUALIZATION = 2`, `GT_TRACING = 3` (`Panel.key` → `"correct"`; `"mask_tracing"` / `"skeleton_correction"` raise `ValueError`; `tests/test_right_panel_contract.py` rejects them anywhere in `app/`, because `switch_right_panel` logs and ignores unknown keys). `shell_chrome.STAGES`: Library, Trace GT, Generate, Correct, Measure, Visualize; `_EDITOR_STAGES = {Panel.GT_TRACING: "Trace GT", Panel.CORRECT: "Correct"}`. `shortcuts.SCOPES = (GLOBAL, LIBRARY, TRACE_GT, CORRECT)`, `STAGE_KEYS = Alt+1 … Alt+6`. `MainWindow.correct_editor`, `toggle_correct()`, `_editors() = {Panel.CORRECT: correct_editor, Panel.GT_TRACING: gt_tracing_interface}`. At the end of `MainWindow.__init__`, `QTimer.singleShot(0, self._prewarm_skeletonize)` (it fires once the event loop runs, after `main.py` shows the window); `_prewarm_skeletonize` starts `skeleton_graph_model.prewarm_skeletonize` on `QThreadPool.globalInstance()`, like `_prewarm_measurements`, so the ~217 ms import never blocks the GUI thread.

### C20 `CorrectControls` and `LayerPanel` (P7a; consumed by P7b)

```python
# app/gui/correct/controls.py
class CorrectControls(QObject):
    """The Correct page's own controls on the shared EditorChrome: two rail groups, the
    dock controls and the polyline prompt. A pure view: it emits requests and shows
    state; it never changes a layer."""

    tool_requested = pyqtSignal(str, str)  # (layer_id, tool_id): a rail tool button was clicked
    size_changed = pyqtSignal(int)  # the visible size slider moved (px)
    opacity_changed = pyqtSignal(float)  # the opacity slider moved (0..1, the active layer)
    load_requested = pyqtSignal()  # "Load skeleton file…"
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    clear_requested = pyqtSignal()  # the dock Clear or the topbar clear button
    save_requested = pyqtSignal()
    finish_line_requested = pyqtSignal()  # prompt "Finish line (Enter)"
    cancel_line_requested = pyqtSignal()  # prompt "Cancel (Esc)"
    smooth_toggled = pyqtSignal(bool)  # prompt Smooth toggle

    def __init__(self, parent: QWidget, chrome: EditorChrome, layers: Mapping[str, EditLayer]) -> None: ...
    tool_buttons: dict[tuple[str, str], IconButton]  # (layer_id, tool_id) -> checkable rail button, one exclusive group
    size_sliders: dict[SizeSpec, DockSlider]  # one per distinct SizeSpec, made on first sight in a slot at the dock's start
    opacity_slider: DockSlider  # 0-100 %, the active layer
    load_button: IconButton  # "load", visible only while the skeleton is active
    undo_button: IconButton
    redo_button: IconButton
    clear_icon_button: IconButton  # "trash"; the tooltip names the active layer
    save_button: QPushButton  # primary
    clear_button: QPushButton  # topbar ghost button "Clear mask" / "Clear skeleton"
    prompt: QFrame  # the polyline prompt, registered with chrome.register(prompt=...)
    finish_button: QPushButton
    cancel_button: QPushButton
    smooth_button: IconButton  # checkable
    def show_layer(self, layer_id: str) -> None: ...  # group colours, Load visibility, clear texts
    def sync(self, layer: EditLayer) -> None: ...  # the active layer's tool, size slider, opacity
    def set_history(self, can_undo: bool, can_redo: bool) -> None: ...
    def set_draft(self, state: DraftState | None) -> None: ...  # the prompt shows only with points


# app/gui/correct/layer_panel.py
class LayerPanel(QFrame):
    """The Layers panel (OverlayLayout role side_panel): one row per layer, mask on top.
    Never takes focus."""

    activate_requested = pyqtSignal(str)  # a row was clicked
    visibility_toggled = pyqtSignal(str, bool)  # an eye was clicked: (layer_id, visible)

    def __init__(self, layers: Sequence[EditLayer], parent: QWidget | None = None) -> None: ...
    def set_status(self, layer_id: str, status: LayerStatus) -> None: ...  # state dot + label + tooltip
    def set_active(self, layer_id: str) -> None: ...  # BG_2 fill + 2 px bar in the layer colour
    def set_visible_state(self, layer_id: str, visible: bool) -> None: ...  # the eye icon only; emits nothing
    def flash(self, layer_id: str) -> None: ...  # 300 ms highlight (undo of the inactive layer)
    def preferred_height(self, host_height: int) -> int: ...  # OverlayLayout side_panel hooks
    def fit_height(self, height: int) -> None: ...
```

- Rail: mask group (Brush, Eraser, Fill) | separator | skeleton group (Select, Eraser, Polyline, Connect) | separator | `chrome.pan_button` | separator | `chrome.enhance_button`, built from each layer's `tools` (`IconButton(icon, tooltip "Label (Key) · tip", checkable, on_color=layer.color)`). `show_layer(id)` gives the inactive group `set_colors(color=tokens.TEXT_FAINT)` and the active one `tokens.TEXT_MUTED`; buttons of both groups stay clickable.
- `sync(layer)`: checks the button of `(layer.layer_id, layer.current_tool)`; shows the slider of `layer.size_spec()` (made on first sight, keyed by the `SizeSpec`: the mask brush and eraser share one, the skeleton eraser has its own; all hidden when None) with `layer.size()`; sets the opacity slider to `round(layer.opacity() * 100)`. It blocks the sliders' signals while syncing and emits nothing.
- `set_draft(state)`: the prompt is visible only for `state` with `in_polyline` and `points > 0`; Finish is enabled from 2 points; Smooth shows `state.smooth`. `set_draft(None)` hides it.
- Layers rows: key hint (1 / 2), swatch in the layer colour, name, eye `IconButton`, state dot and label: `status.label` when set, else `"Saved"` (clean), `"Unsaved"` (dirty), `"Not saved"` (error), `""` (none); tooltip `status.tooltip`. A row click emits `activate_requested`; an eye click emits `visibility_toggled(id, not visible)` and does not activate the row.
- `keep_canvas_focus` on every control; the panel and its buttons have `Qt.FocusPolicy.NoFocus`.

---

## Wave 0 (lead, before any package starts)

- [x] Add C1 to `app/gui/shortcuts.py` (additive; `CORRECT_MASK`, `CORRECT_SKELETON`, `STAGE_KEYS` kept) and append `sc.CORRECT` to the `SCOPES` assertion in `tests/test_shell_info_shortcuts.py`; suite green; committed as `99876e9` (`feat(shortcuts): register the Correct scope`).
- [ ] Commit the revised spec and this plan (`docs: spec and plan for the unified Correct editor and smooth zoom`).
- [ ] Create `.claude/handoff/unified-editor-2026-09-29/notes/` entries for the 13 packages (empty PROGRESS checklists; ids below) and launch W1 with the completion-driven workflow (Execution).

---

## P1a — Render core: pyramid, photo and raster layers, zoom (wave 1) — id `P1a-render-core`

**Goal:** the photo is smooth at every zoom and alias-free when minified; the mask and GT layers show exact pixels when magnified and coverage when minified, re-uploading only touched tiles on GL; zoom keys follow a ladder; MSAA on GL; the status chip can show a coloured dot. Only render items, the shared view and two widgets: no editor code.

**Start:** at once. **Provides:** C5, C6 (`device_scale`, lazy re-exports, `PhotoLayerItem`, `RasterLayerItem`), C7, C18 `tests/render_metrics.py`. **Consumers:** P1b (from Step 1, in parallel), P3 and P4b (after P1a finishes).
**Owns:** creates `app/imaging/__init__.py`, `app/imaging/pyramid.py`, `app/gui/canvas_layers/__init__.py`, `app/gui/canvas_layers/photo.py`, `app/gui/canvas_layers/raster.py`, `tests/test_image_pyramid.py`, `tests/test_canvas_layers.py`, `tests/test_canvas_zoom_ux.py`, `tests/render_metrics.py`; modifies `app/gui/canvas_view.py`, `app/gui/canvas_gl.py`, `app/gui/widgets/overlays.py`, `tests/test_overlays_widgets.py`.
**Read-only:** `app/gui/skeleton_overlay.py`, tokens, `app/gui/canvas_item.py` (the nearest reference), `scratch/zoom-render/{proto_layers.py,bench_proto_raster.py,bench_gl_proto.py,gl_common.py,zr_common.py}`, `scratch/contracts-tests/render/*.py`, `scratch/plan-critic/probe_tile_seams*.py`.
**Must stay green without editing them** (the zoom ladder keeps 1.25 / 0.8 around 100 %, the wheel keeps 1.2 per notch, `MIN_ZOOM` stays 0.1, `set_hint(text, tooltip)` is unchanged): `tests/test_canvas_views.py`, `tests/test_canvas_item.py`, the view-class tests of `tests/test_view_canvas_background.py`, and every old-editor test.
**Size:** about 1,500 changed lines.

- [ ] **Step 1: working API first (first minutes; P1b builds on it).** Create `app/imaging/__init__.py` (docstring only), `app/imaging/pyramid.py` (C5 in a simple complete form: `area_half`, `max_half`, `ImagePyramid` building levels on demand, `invalidate` dropping the built levels, `level_for_scale`), `app/gui/canvas_layers/__init__.py` (`device_scale`, the PEP 562 `__getattr__`, `__all__`; nothing heavy imported), `photo.py` and `raster.py` with every public name of C6 and the minimal bodies of C6 (smooth level-0 draw; nearest draw of the exposed rect), plus working `zoom_step`, `ZOOM_STOPS`, `gl_samples`, `SAMPLES_ENV_VAR`. No `NotImplementedError` anywhere. Check: `./venv/Scripts/python.exe -c "import app.imaging.pyramid, app.gui.canvas_layers, app.gui.canvas_layers.photo, app.gui.canvas_layers.raster, app.gui.main_window"`. Write "Step 1 landed" at the top of your notes.
- [ ] **Step 2: pyramid, tests first** (`tests/test_image_pyramid.py`, Qt-free): level sizes are ceil halves and `count` stops at `min_side`; `level(k)` equals `cv2.resize(level(k-1), INTER_AREA)`; `level_for_scale`: 1.0→0, 0.75→0, 0.5→1, 0.499→1, 0.25→2, 0.1→3, 2.0→0, clamped to `count-1`; `invalidate(rect)` then `level(k)` equals a full rebuild for odd sizes and rects touching the edges, and recomputes only the affected region (spy on the reducer's input sizes); premultiplied BGRA: an opaque 2x2 block averages opaque, a checkerboard to alpha 127/128; `max_half` keeps a 1 px diagonal and an 8-connected random-walk line as one component at every level while nearest decimation breaks it; zone plate: `level(2)` placed bilinear has ring std ≤ 1.1x the `INTER_AREA` reference. Run → FAIL; implement the lazy patch (adapt `ImagePyramid` of `proto_layers.py`); run → PASS.
- [ ] **Step 3: `tests/render_metrics.py`** (C18) ported from the scouts' scripts; one smoke test of the helpers inside `tests/test_canvas_layers.py`.
- [ ] **Step 4: `PhotoLayerItem`, tests first** (`tests/test_canvas_layers.py`, offscreen, raster viewport): at 800 % the grab is ≥ 40 dB against `cv2.remap(INTER_LINEAR)` and ≥ 5 dB better than `INTER_NEAREST`, equal-neighbour fraction < 0.4, mean run < 3 px; at 100 % with integer scroll the grab equals the source ±1; at 25 % the zone-plate ring std ≤ 1.2x the ideal; painting into a `QImage` with `setDevicePixelRatio(2)` at scale 0.25 gives `level_painted() == 1`, at dpr 1 `== 2`; `set_image(None)` empties it; `pixmap()` is level 0; after `set_image(new)` no pixel of the old image survives at 25 %. Implement; PASS.
- [ ] **Step 5: `RasterLayerItem`, tests first:** bit-exact against the nearest reference at 1x, 4x, 20x (port `tests/test_canvas_item.py::test_zoomed_canvas_pixels_stay_crisp` and its exposed-rect / no-second-reference checks); **fractional zooms 1.288, 2.35 and 3.7**, rendered in a view with `Antialiasing | TextAntialiasing` (as `EditorCanvasView` sets them): with the image's x / y encoded in the pixel colours, every image column and row of the exposed area appears in the grab (no pixel skipped), the decoded mapping is monotonic, every block width is within floor(z)..ceil(z), and no column or row at a tile border shows a blended colour (bit-exactness against one nearest canvas is the wrong oracle here: 256 px tiles sample with a different phase, `scratch/plan-critic/probe_tile_seams*.py`); at 25 % ≥ 60 dB against the area reference over black; a 1 px horizontal line on rows 240-243 is visible (differs from the layer hidden by ≥ 1 grey level) in ≥ 95 % of its columns at 0.25, 0.33, 0.5, 0.75; after one `canvas_changed(rect)` only the tiles intersecting `rect` get a new `cacheKey` in `built_tiles()`; `canvas_replaced()` rebuilds all; a smooth photo and a crisp layer in one scene (the item restores the painter's hints). Implement (adapt `EditLayerItem`); PASS.
- [ ] **Step 6: status-chip dot, tests first** (`tests/test_overlays_widgets.py`): `set_hint("Mask · Brush", "tip", dot=tokens.MASK)` shows a visible `hint_dot` label "●" in that colour before the text; `dot=None` hides it; `set_hint(text, tooltip)` behaves as today; `hint_label.textFormat()` stays `PlainText`; the chip's width fit accounts for the dot. Implement (C7); PASS.
- [ ] **Step 7: zoom UX and MSAA, tests first** (`tests/test_canvas_zoom_ux.py`): `ZOOM_STOPS` is sorted and contains 0.8, 1.0, 1.25 and every integer 2-6; `zoom_step(+1)` from 1.0 → 1.25, `-1` → 0.8, from 1.63 → 2.0 / 1.5, at 20 → 20; Ctrl+= / Ctrl+- use it; one wheel notch still gives 1.2; `ZoomCluster` −/+ call `zoom_step(∓1)`; the readout context menu lists Fit, 50, 100, 200, 400, 800, 1600 % and each sets that zoom (Fit fits); `gl_samples()`: unset → 4, `"0"` → 0, `"8"` → 8, `"abc"` → 4; `_new_gl_viewport().format().samples()` equals it (construct, do not show). Implement in `canvas_view.py`, `canvas_gl.py`, `overlays.py`; update the zoom-cluster expectations in `tests/test_overlays_widgets.py` (`("zoom_step", 1)` / `("zoom_step", -1)`). Update the `canvas_view.py` module docstring (canvases: Library, Correct, Trace GT).
- [ ] **Step 8: perf.** Copy and adapt `scratch/zoom-render/bench_proto_raster.py` → `scratch/P1a-render-core/bench_layers.py` (offscreen, 1200x800, median of 11) and `bench_gl_proto.py` → `scratch/P1a-render-core/bench_gl_layers.py` (real GL, ≤ 27 s window, under the GL lock). Record in `notes/P1a-render-core.md`.
- [ ] **Step 9: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P1a-render-core/pt --continue-on-collection-errors tests/test_image_pyramid.py tests/test_canvas_layers.py tests/test_canvas_zoom_ux.py tests/test_overlays_widgets.py tests/test_canvas_views.py tests/test_canvas_item.py tests/test_view_canvas_background.py tests/test_canvas_gl.py tests/test_widgets_smoke.py` → all pass.
- The same command without file arguments (full suite) → no failure in P1a's files; foreign failures listed in the notes.
- `./venv/Scripts/python.exe -m ruff check app/imaging app/gui/canvas_layers/__init__.py app/gui/canvas_layers/photo.py app/gui/canvas_layers/raster.py app/gui/canvas_view.py app/gui/canvas_gl.py app/gui/widgets/overlays.py tests/test_image_pyramid.py tests/test_canvas_layers.py tests/test_canvas_zoom_ux.py tests/render_metrics.py tests/test_overlays_widgets.py` clean; the same with `ruff format --check`.

**Perf budget (5-run medians):** photo frame at zoom 0.1-20: raster ≤ 1 ms (bare view), GL ≤ 1.5 ms; level switch first frame ≤ 8 ms (6000 px image); raster-layer stroke frame GL ≤ 2 ms at 6000x4000, raster ≤ 2 ms; pyramid build (all levels) ≤ 6 ms at 6000x4000.

**UX details:** the readout context menu uses the app's menu style; presets are checkable and show the current zoom checked when it matches; keyboard zoom lands on round percentages; "Fit" is the first entry; the chip dot is the same size as the hint text.

---

## P1b — Skeleton render item (wave 1) — id `P1b-render-skeleton`

**Goal:** `SkeletonLayerItem`: the skeleton is a line at every zoom (max-pool LOD below 1, nearest from 1 to 2, m-adjacency vectors from 2 device px), with vectors extracted lazily per 256 px tile so a dense skeleton never stalls a frame and an edit never re-extracts; `vectors=False` for the Library (bilinear at s ≥ 1).

**Start:** at once (builds on P1a's Step 1: `device_scale`, `app.imaging.pyramid`; until it lands, write the Qt-free extraction of Step 2). **Provides:** C6 `SkeletonLayerItem`. **Consumers:** P3, P5b (after P1b finishes).
**Owns:** creates `app/gui/canvas_layers/skeleton.py`, `tests/test_canvas_layer_skeleton.py`.
**Read-only:** `app/gui/skeleton_overlay.py` (`overlay_indices`, `LINE_INDEX`, `HALO_INDEX`, `HALO_ALPHA`; `SkeletonOverlayRenderer` only for the Step 1 body), `app/imaging/pyramid.py`, `app/gui/canvas_layers/__init__.py`, `tests/render_metrics.py` (P1a's; if it does not exist yet, keep private helpers in your test file), `scratch/zoom-render/{proto_layers.py,bench_gl_proto.py,gl_common.py}` (`skeleton_segments`, `sparse_segments`, `merge_runs`, `SkeletonPathItem`), zoom-render scout §3.3 and §4.4.
**Size:** about 800 changed lines.

- [ ] **Step 1: working API first.** `skeleton.py` with the full C6 API; the body is today's `SkeletonOverlayRenderer` pixmap (line + halo; line only with `halo=False`) drawn nearest with `scale` as the item transform; `mode_painted()` → `"raster"`; `vector_tiles()` → `{}`. Check: `./venv/Scripts/python.exe -c "import app.gui.canvas_layers.skeleton, app.gui.main_window"`. Note it in your notes.
- [ ] **Step 2: vector extraction, Qt-free helpers, tests first** (`tests/test_canvas_layer_skeleton.py`): m-adjacency segments for an L corner, a T junction, an isolated pixel and a diagonal staircase equal hand-written expected sets; per-tile extraction (256 px tiles, 1 px margin, each link in the tile of its first pixel in row-major order) gives the same link set as a whole-frame extraction for seeded random skeletons and for lines crossing tile corners; collinear runs merge inside a tile. Implement (adapt `skeleton_segments`, `sparse_segments`, `merge_runs`); PASS.
- [ ] **Step 3: tiers, tests first:** at 800 % no run of skeleton-coloured pixels ≥ 4 px across the line (no 8x8 blocks); at 25 % the drawn line pixels form as many connected components as the skeleton; `mode_painted()` is `"raster-lod"` at 0.5, `"raster"` at 1.5, `"vector"` at 4 (raster engine, ≤ 400 visible segments), `"vector-thin"` / `"raster"` above the raster budgets; painting into a `QImage` with devicePixelRatio 2 at zoom 1 gives `"vector"` (s = 2); `scale=(640/341, 480/256)` covers a 640x480 photo; `halo=False` draws no halo pixels; `vectors=False` gives `"raster-lod"` at 0.5 and `"raster-smooth"` at 1.5 and 4, never a vector mode, and a 2 px wide line at 4x has soft edges (intermediate values across its border). The nearest tiers paint with antialiasing and smoothing off under a view that sets `Antialiasing` (no blended pixels at tile borders). Implement; PASS.
- [ ] **Step 4: lazy tiles and O(box) updates, tests first:** `set_mask(..., changed=box)` renders the same pixels as a full `set_mask`; it calls `overlay_indices` only on the box padded by 1 px (spy on the input shape) and extracts nothing (`vector_tiles()` loses exactly the tiles the padded box touches, gains none); a paint at s ≥ 2 extracts only the exposed tiles; with the budget exhausted (`EXTRACT_BUDGET_MS = 0` on the instance) the first paint draws the level-0 overlay for pending tiles and schedules an `update()`, and after enough paints every exposed tile is extracted and the render equals an unbudgeted one. Implement; PASS.
- [ ] **Step 5: perf.** `scratch/P1b-render-skeleton/bench_skeleton_item.py` (offscreen) and `bench_gl_skeleton.py` (real GL, under the GL lock), adapted from `bench_gl_proto.py`: real 640 skeleton frames at zoom 1-20; the dense 2048 stress skeleton at zoom 2: every hover repaint (no mask change) including the first frames after reaching zoom 2; `set_mask(changed=32x32 box)` on dense 2048. Record in the notes.
- [ ] **Step 6: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P1b-render-skeleton/pt --continue-on-collection-errors tests/test_canvas_layer_skeleton.py tests/test_skeleton_editor_tools.py tests/test_view_canvas_background.py` → all pass.
- Full suite (same flags, no file arguments) → no failure in P1b's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on `app/gui/canvas_layers/skeleton.py tests/test_canvas_layer_skeleton.py` clean.

**Perf budget (5-run medians):** skeleton frame, real 640: GL ≤ 1.5 ms, raster ≤ 5 ms within the tiers; dense 2048 hover frame at zoom 2: ≤ 16 ms on GL and on raster, first frames included (extraction ≤ 4 ms per paint); `set_mask(changed=32x32 box)` on dense 2048 ≤ 1 ms.

**UX details:** the vector line is 2 px in `tokens.SKEL` over a 4 px `tokens.BG_0` halo at alpha 170; pending tiles show the pixel overlay for at most a few frames; the Library overlay looks as today (soft, no blocks).

---

## P2 — Shared editor kit (wave 1) — id `P2-kit`

**Goal:** the parts every canvas editor shares: one decode for photo + Enhance, one paint engine (`PaintCanvas`, today's mixin moved verbatim onto a `QImage`), one view (`EditorCanvasView`), one overlay chrome and one set of prompts.

**Start:** at once. **Provides:** C8-C12, in the order C12 → C9 → C8 → C10 → C11. **Consumers:** P3, P4b (after P2 finishes). P2 does not use P1a's additions (`zoom_step`, `ZOOM_STOPS`, `set_hint(dot=)`) in its code or tests, so its commit is green before P1a's.
**Owns:** creates `app/gui/editor_canvas.py`, `app/gui/paint_canvas.py`, `app/gui/editor_chrome.py`, `app/gui/editor_prompts.py`, `tests/test_editor_canvas.py`, `tests/test_paint_canvas.py`, `tests/test_editor_chrome.py`, `tests/test_editor_prompts.py`; modifies `app/gui/image_normalization_interface.py`, `app/enhancement.py`, `tests/test_enhance_controls.py`, `tests/test_enhance_core.py`, `tests/test_view_canvas_background.py`.
**Read-only:** `app/gui/canvas_view.py`, `app/gui/canvas_background.py`, `app/gui/mask_drawing_tools.py` (the verbatim source and the identity oracle), `app/gui/mask_graphics_view.py` and `skeleton_correction_graphics_view.py` (routing semantics, render hints), `app/gui/mask_tracing_interface.py` (`_ask`, `_build_overlays`), `app/gui/skeleton_correction_interface.py` (`_build_overlays`), `app/gui/widgets/*`, `app/gui/patch_history.py`.
**Size:** about 1,500 changed lines.

- [ ] **Step 1: one decode (C12) and the automatic canvas-background guard, tests first.** (a) `tests/test_enhance_core.py`, `tests/test_enhance_controls.py`: a JPEG stored 80x40 with EXIF orientation 6 (written with PIL) decodes through `SourceImageCache` to shape `(40, 80, 3)`, the same geometry as `QImage(path)`; `source_image(path)` returns the same object twice; `display_image` returns the source itself when inactive and `apply_to(source)` when active; `display_pixmap` unchanged. Implement C12. (b) `tests/test_view_canvas_background.py`: replace the hand-written `VIEW_CLASSES` by discovery: parse `app/**/*.py`, collect classes whose bases name `QGraphicsView` or (transitively) another collected class, import them by module path, and run the existing parametrized pixel tests over them (`view_cls(None)`); `test_every_graphics_view_subclass_is_covered` becomes "every discovered class calls `configure_canvas_background` in its own `__init__` or inherits a class that does" and asserts `"MagnifyingGraphicsView" in names` (Step 3 adds `"EditorCanvasView"`). The old views are still discovered until P8b deletes them. PASS. From here on, creating a view class never turns this file red by itself.
- [ ] **Step 2: `PaintCanvas` (C9), tests first** (`tests/test_paint_canvas.py`): **identity with today's mixin** (this one test starts with `pytest.importorskip("app.gui.mask_tracing_interface")`: its oracle is deleted by P8b, after which the goldens carry the proof and the lead removes the skipped test in the post-swap cleanup) — load a synthetic 640x480 image into a `MaskTracingInterface` and build a `PaintCanvas` over an equal `PatchHistory` with the same LUT / `to_plane` / `fill_source`; replay the golden mask ops (`scratch/contracts-tests/goldens/capture_goldens.py` `MASK_OPS`: brush 1/2/3/6/9/14/25, eraser 7 and a 4 px click, a closed outline, fill inside and outside, clear + undo, undo ×2 + redo, a size-11 full diagonal, a 1 px eraser line) through both; after each op planes are equal (`np.array_equal`) and canvases are equal (`pixmap_to_argb_array`). Also (no old module needed): after `rebuild()`, `pixmap_to_argb_array(canvas.image)` equals `lut[plane]` exactly for a 0/1 plane and for a 4-class id plane with a transparent background; `commit()` is None when nothing was painted; `in_stroke()` is True after `begin` and False after `commit`; `fill()` outside is None with no history entry; `on_region` receives today's dirty rects (segment box padded by r + 2, clipped); `on_patch` gets `(patch, True)` on commit/redo and `(patch, False)` on undo; the canvas serial (`image.cacheKey() >> 32`) stays constant over 100 segments; `pixmap_to_argb_array` gives equal arrays for a `QImage` and a `QPixmap` of the same pixels; `argb_array_to_image` round-trips. Implement by moving `draw_point`, `commit_stroke`, `_render_plane_rect`, `flood_fill`, `_clear_canvas`, `_step_history` verbatim (`self.mask_pixmap` → `self.image`, the button check → the `erase` argument); PASS.
- [ ] **Step 3: `EditorCanvasView` (C8), tests first** (`tests/test_editor_canvas.py`, recording fake sink): press → `canvas_press` with the floored image pixel; `drawing` follows the sink's return; drag moves → `canvas_move(dragging=True)`; hover → `canvas_move(dragging=False)`; a left drag that did not start with an accepted press is not forwarded; release → `canvas_release`; double-click → `canvas_double_click`; Space held turns a left drag into a pan and the sink sees nothing; middle drag pans; a left press during a middle drag is ignored; wheel: consumed by the sink → no zoom, otherwise Ctrl+wheel zooms 1.2; wheel ignored while drawing; keys reach the sink first, unconsumed Ctrl+0 fits; key release reaches the sink; leave → `canvas_hover_left`; `set_mode(True/False)` → `canvas_pan_changed(True/False)`; `map_to_image` clamps to `canvas_size()` and returns (0, 0) for an empty size; no scrolling while drawing; **`end_gesture()`** after an accepted press: `drawing` is False, the next moves arrive as `canvas_move(dragging=False)`, the release is not forwarded, wheel and scrolling work again; **render hints**: `renderHints()` has `Antialiasing` and `TextAntialiasing` and not `SmoothPixmapTransform`; `EditorCanvasView(None)` pans and zooms without errors; its background brush is the opaque canvas colour. Add `"EditorCanvasView"` to the names the background guard requires. Implement; PASS.
- [ ] **Step 4: `EditorChrome` (C10), tests first** (`tests/test_editor_chrome.py`): builds rail, dock, popover (content = the given controls), chip, zoom on the host; the five roles are registered; `register(side_panel=w)` adds a role; `view.fit_insets() == layout.insets()`; the Enhance button toggles the popover and follows it; closing the popover focuses the view; `pan_button` toggles `view.pan_mode`; the readout follows the view's zoom; overlays do not intersect at 1400x900 and 700x520 with a dummy side panel and prompt. Implement by moving `RasterEditorBase._build_overlays` / `_on_enhance_toggled` logic; PASS.
- [ ] **Step 5: prompts (C11), tests first** (`tests/test_editor_prompts.py`): patch `QMessageBox.exec` to return Save / Discard / Cancel / Escape and check the mapping; titles, texts and button labels exactly as C11 for nouns `"mask"`, `"skeleton"`, `"mask and skeleton"`, `"GT"`; failed-save box: Discard / "Keep editing", default and Esc = Keep; `join_nouns`; the box is deleted after use (no top-level `QMessageBox` left after `processEvents()`). Implement; PASS.
- [ ] **Step 6: perf.** `scratch/P2-kit/bench_paint.py`: `rebuild()` vs today's LUT path at 640x480 and 2048x1536 (target ≥ 10x faster: ≤ 0.3 ms / ≤ 2.5 ms); stroke segment ≤ 0.05 ms; 1000 synthetic hover moves through `EditorCanvasView` + a no-op sink within +5 % of `MaskTracingGraphicsView` with a no-op host.
- [ ] **Step 7: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P2-kit/pt --continue-on-collection-errors tests/test_editor_prompts.py tests/test_paint_canvas.py tests/test_editor_canvas.py tests/test_editor_chrome.py tests/test_view_canvas_background.py tests/test_enhance_core.py tests/test_enhance_controls.py tests/test_mask_tracing_interface_smoke.py tests/test_skeleton_editor_tools.py` → all pass.
- Full suite (same flags, no file arguments) → no failure in P2's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** plane → canvas rebuild ≤ 0.3 ms (640) / ≤ 2.5 ms (2048); stroke segment ≤ 0.05 ms; no extra copy of the canvas per segment (serial check).

**UX details:** prompts keep today's wording word for word; the chrome's Pan button tooltip "Pan (hold Space, or middle-drag)", Enhance "Image enhancement".

---

## P3 — Trace GT and the Library on the new parts (wave 1) — id `P3-gt-library`

**Goal:** Trace GT runs on the shared kit and render layers with no behaviour change; the Library canvas uses the photo and skeleton items (smooth zoom, no aliasing, no per-zoom cache rebuild) and still shows the generated skeleton file as today (soft edges, no blocks).

**Start after:** P1a, P1b, P2. **Consumes:** C6, C7, C8-C12. **Provides:** `app/gui/raster_editor_base.py` (GT's base; no other package consumes it).
**Owns:** creates `app/gui/raster_editor_base.py`, `tests/test_raster_editor_behaviour.py`, `tests/test_canvas_zoom_quality.py`; modifies `app/gui/gt_tracing_interface.py`, `app/gui/display_controller.py`, `tests/test_canvas_display_controller.py`, `tests/test_gt_tracing_interface.py` (W1 only; P8b in W2), `tests/test_mask_editor_behaviour.py` (W1 only; P8b deletes it in W2).
**Read-only:** `app/gui/mask_tracing_interface.py` (source of `RasterEditorBase`; leave the old class untouched — `MaskTracingInterface` keeps using it until P8b), `app/gui/class_panel.py`, `app/gui/shortcuts.py`, `scratch/architecture/surface.json` (every attribute GT and its tests use), `scratch/plan-critic/check_fake_width.py` (the 2 px measurement).
**Base-switch rule:** `gt_tracing_interface.py` keeps importing the old base (`app.gui.mask_tracing_interface.RasterEditorBase`) until the new base passes the base cases of `tests/test_raster_editor_behaviour.py` (Step 3). The switch is then one import edit, followed at once by `./venv/Scripts/python.exe -c "import app.gui.main_window"` and a construct-`MainWindow` smoke run. If P3 has to stop while GT tests fail on the new base, it restores the old import first and says so in its notes.
**Size:** about 1,400 changed lines.

- [ ] **Step 1: inventory.** From `surface.json` and `grep`, list every base attribute, hook and method `gt_tracing_interface.py` and the GT tests use; paste the list into `notes/P3-gt-library.md`. It must stay complete: `graphics_view`, `scene`, `image_item` (`.pixmap()`), `mask_item`, `mask_pixmap` (now the canvas `QImage`), `image_pixmap` (property → `image_item.pixmap()`), `edits`, `undo_stack`, `redo_stack`, `brush_color`, `brush_size`, `size_slider`, `size_container`, `opacity_slider`, `opacity_container`, `brush_button`, `eraser_button`, `fill_button`, `tool_button_group`, `pan_button`, `mode_toggle`, `mask_visible_button`, `enhance_button`, `undo_button`, `redo_button`, `clear_icon_button`, `save_button`, `clear_button`, `norm_controls`, `tool_rail`, `dock`, `enhance_popover`, `status_chip`, `file_label`, `state_label`, `zoom_cluster`, `zoom_out_button`, `zoom_pct_label`, `zoom_in_button`, `fit_button`, `overlay_layout`, `brush_preview`, `b_key_pressed`, `drawing`, `dirty`, `current_image_path`, `_shortcuts`, `undo_shortcut`, `redo_shortcut`; methods `begin_stroke`, `continue_stroke`, `end_stroke`, `flood_fill`, `clear_mask`, `undo`, `redo`, `mark_dirty`, `_set_clean`, `has_unsaved_changes`, `maybe_save_changes`, `reset`, `load_image`, `update_display`, `refresh_mask_display`, `refresh_mask_region`, `apply_normalization`, `_finish_stroke`, `step_brush_size`, `update_brush_size`, `update_opacity`, `toggle_mode`, `update_brush_preview`, `set_brush_preview_visible`, `_sync_brush_preview`, `_style_brush_preview`, `_update_status_chip`, `_sync_history_buttons`, `_tip`, `_install_shortcuts`, `_reposition_overlays`, `_plane_to_pixmap`; hooks `_open_document`, `_document_opened`, `save_mask`, `_plane_lut`, `_argb_to_plane`, `_fill_source`, `_status`, `_on_patch`, `_after_stroke`, `_edit_blocked`.
- [ ] **Step 2: tests first.** Create `tests/test_raster_editor_behaviour.py` with two groups:
  - (a) **GT cases**, moved from `tests/test_mask_editor_behaviour.py` with GT parameters only: undo/redo matches full snapshots; registry keys once each; fit clear of overlays at 3 sizes; GT has no mask storage (without the `issubclass(…, MaskTracingInterface)` check); class presence tracks every edit; plus GT versions of the mask-only behaviours: strokes paint in place without deep copies; small-stroke history on a 2048 canvas < 1 MiB; failed save keeps the open image; Cancel keeps editing; redo keys; undo/redo buttons follow history; dock clicks keep canvas focus; enhancement live and sticky. Add the override guard: `GtTracingInterface._install_shortcuts`, `_open_document`, `save_mask`, `_status`, `reset` are not `app.gui.raster_editor_base.RasterEditorBase`'s, and `issubclass(GtTracingInterface, app.gui.raster_editor_base.RasterEditorBase)`.
  - (b) **Base cases** through a test-local `_ProbeEditor(RasterEditorBase)` (overrides `_open_document` → a zero plane, not dirty; `save_mask` → records and returns True; and the plane hooks the base leaves to subclasses, `_plane_lut` / `_argb_to_plane` / `_fill_source`, as one-class copies of `MaskTracingInterface`'s, `mask_tracing_interface.py:702-712`): `mask_item` is a `RasterLayerItem` at z 1 with opacity `DEFAULT_OPACITY / 100`, `image_item` a `PhotoLayerItem` at z 0, `mask_pixmap` the canvas `QImage` (serial check `cacheKey() >> 32` still works); `_open_document` receives the image path and a `QSize(w, h)`; `load_image` decodes once through `norm_controls.display_image` (spy); an undecodable image → the "Could not open …" danger notice and nothing changes; Ctrl+= / Ctrl+- call `view.zoom_step(±1)`; `RasterEditorBase.SHORTCUT_SCOPE == shortcuts.TRACE_GT`; a stroke paints in place and commits one history entry; the view is an `EditorCanvasView` whose sink is the editor.
  - Strip the GT parameters and the GT-only tests from `tests/test_mask_editor_behaviour.py`; keep the file and every mask case (P7b ports them, P8b deletes the file at the swap). In `tests/test_gt_tracing_interface.py` import `pixmap_to_argb_array` from `app.gui.paint_canvas`; keep `test_mask_mode_unchanged` (P8b deletes it at the swap). Run → (b) FAIL.
- [ ] **Step 3: build `app/gui/raster_editor_base.py`** until group (b) passes, with GT still on the old base. `RasterEditorBase(QWidget)` implements `InputSink` and composes `EditorChrome`, `EditorCanvasView(sink=self)`, `PhotoLayerItem` (z 0), `RasterLayerItem(lambda: self.paint.image)` (z 1, opacity `DEFAULT_OPACITY / 100`), `PaintCanvas(self.edits, lut=self._plane_lut, to_plane=self._argb_to_plane, fill_source=self._fill_source, on_region=…, on_replaced=…, on_patch=self._on_patch)` and `BrushPreview`. Keep GT semantics exactly: flag-based dirty (`mark_dirty` on every edit, undo and redo), Discard marks clean (prompts through `editor_prompts`), `SHORTCUT_SCOPE = shortcuts.TRACE_GT` (never `CORRECT_MASK`), `DEFAULT_BRUSH = 8`, `DEFAULT_OPACITY = 60`. Photo: `load_image` decodes once via `norm_controls.display_image(path)` (None → "Could not open …" danger notice), calls `_open_document(image_path, QSize(w, h))` (w, h from the array; GT's body only reads `width()` / `height()`, so it stays unchanged) and fits; `apply_normalization` re-sets the photo from `display_image`. Zoom shortcuts call `view.zoom_step(±1)`. Drop the dead mask-only `zoom_slider`, `update_zoom`, `_on_zoom_changed`, `zoom_factor`. Input: `canvas_press` → `begin_stroke`, `canvas_move` → `update_brush_preview` (+ `continue_stroke` when dragging), `canvas_release` → `end_stroke`, `canvas_wheel` → B held: `step_brush_size`, `canvas_key_press/release` → B hold/tap (today's `eventFilter`), `canvas_hover_left` / `canvas_pan_changed` → `set_brush_preview_visible`. PASS (b).
- [ ] **Step 4: switch and port `gt_tracing_interface.py`** (base-switch rule): imports from `.paint_canvas`, `.raster_editor_base`, `.patch_history`; `_recolour` → `self.paint.rebuild()`; `_pixmap_to_ids` accepts the canvas `QImage`. Nothing else changes. Check the `MainWindow` import at once. Run group (a), `tests/test_gt_tracing_interface.py` and `tests/test_mask_editor_behaviour.py` → PASS.
- [ ] **Step 5: Library.** `display_controller.py`: decode with `imread_unicode(path, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)` (undecodable → today's message and toast); Single = `PhotoLayerItem`; Overlay = photo + `SkeletonLayerItem(halo=False, vectors=False)` at opacity 0.8 (today's 204/255), `set_mask(otsu_binary, scale=(w / sw, h / sh))`, no `INTER_NEAREST` blow-up; Side by side = photo + a second `PhotoLayerItem` with the skeleton image, scaled to the photo height and placed `SIDE_BY_SIDE_GAP` to the right; delete the per-item `SmoothTransformation` / `DeviceCoordinateCache` lines (`display_controller.py:244-246`: the items choose their own filtering); keep the view's render hints and `CacheBackground`. Update `tests/test_canvas_display_controller.py` (item types, `vectors=False`, overlay colour sampled from a grab, side-by-side heights, non-ASCII folders, fit on new image).
- [ ] **Step 6: zoom-quality tests** (`tests/test_canvas_zoom_quality.py`, using `tests/render_metrics.py`) over the GT canvas and the Library canvas: photo at 2, 4, 8, 16x: sine step ratio ≤ 1.5/z and equal-neighbour fraction ≤ 0.05; at 1x exact ±1; chirp alias std ≤ 35 and pan delta ≤ 50 at 0.2, 0.25, 0.33, 0.4; alias std ≤ 45 at 0.6 and 0.75; 1 px stripes at 0.25 and 0.5 have mean 127.5 ± 12; the GT layer is exact at 4x and 8x and a 1 px GT stroke stays visible at 0.25-0.75 on rows 240-243; Enhance on/off and the next image change the minified render (no stale level); an OpenGL on/off swap keeps the items' smoothing (config-level). **Library overlay of a 2 px generated file:** a 341x256 file with lines 2 px wide over a flat grey 640x480 photo, at 4 device px per file pixel: every file pixel is covered (the device pixel at its centre differs from the photo-only render), and within the line's footprint no run of equal pixels across the line is longer than 5 device px (bilinear gives 4 between the two pixel centres; a nearest draw gives 8). The thresholds come from the contracts scout, whose measurements of today's canvases (`scratch/contracts-tests/render/render_metrics.json`, `layer_visibility*.json`) fail every one of the photo thresholds; they must pass now.
- [ ] **Step 7: perf.** `scratch/P3-gt-library/bench_gt.py`: GT `load_image` (640) ≤ 7.6 ms (today's mask editor), stroke frame (raster) ≤ 1 ms; Library zoom frame (offscreen) ≤ today's static frame + 0.5 ms; optional real-GL Library zoom frame via P1a's GL bench (under the GL lock) ≤ 1.5 ms.
- [ ] **Step 8: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P3-gt-library/pt --continue-on-collection-errors tests/test_gt_tracing_interface.py tests/test_raster_editor_behaviour.py tests/test_mask_editor_behaviour.py tests/test_canvas_zoom_quality.py tests/test_canvas_display_controller.py tests/test_gt_widgets.py tests/test_fair_gt_registry_gui.py tests/test_shell_chrome_smoke.py tests/test_canvas_views.py tests/test_view_canvas_background.py` → all pass.
- `./venv/Scripts/python.exe -c "import app.gui.main_window"` → no error.
- Full suite (same flags, no file arguments) → no failure in P3's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** GT open ≤ 7.6 ms (640); GT stroke segment ≤ 0.05 ms; Library zoom frame GL ≤ 1.5 ms (was 2.3-6.2 ms).

**UX details:** GT's look and keys are unchanged except that the photo is smooth and the class layer is exact when magnified / coverage when minified; the brush ring keeps the class colour; the Library overlay looks as today (soft lines, 80 %).

---

## P4a — Mask document and golden data (wave 1) — id `P4a-mask-document`

**Goal:** the Qt-free half of the mask layer: `MaskDocument` (today's load/save verbatim, exact dirty against a saved baseline, in-memory revert, pinned file stamps), `PatchHistory.drop_redo`, and the committed golden data every later golden test replays.

**Start:** at once (builds on P6's Step 0: `Notice`, `LayerStatus` in `layer.py`; until it lands, write the golden data and the history change). **Provides:** C13 `MaskDocument`, C4 `PatchHistory.drop_redo`, C18 golden data and support. **Consumers:** P4b, P5b (test data), P7b (after P4a finishes).
**Owns:** creates `app/gui/correct/mask_document.py`, `tests/test_correct_mask_document.py`, `tests/test_correct_goldens_data.py`, `tests/correct_goldens_support.py`, `tests/data/correct_goldens.json`; modifies `app/gui/patch_history.py`, `tests/test_mask_editor_history.py`.
**Read-only:** `app/masks/store.py`, `app/project_layout.py`, `app/gui/mask_tracing_interface.py` (verbatim source), `scratch/contracts-tests/goldens/{capture_goldens.py,goldens.json}`.
**Size:** about 1,000 changed lines plus the golden data.

- [ ] **Step 1: goldens in the repo.** Create `tests/data/correct_goldens.json` (C18) and `tests/correct_goldens_support.py` (C18) by copying from the scout's files; `tests/test_correct_goldens_data.py` checks that `make_synth_project` regenerates the inputs byte for byte twice (deterministic), that the JSON has both cases and all four op scripts, and that the support module imports no PyQt6 at import time.
- [ ] **Step 2: `PatchHistory.drop_redo`, test first** (`tests/test_mask_editor_history.py`): after commit + undo, `drop_redo()` empties the redo stack, keeps the undo stack and the plane, fires `on_change` once; a no-op when empty. Implement (C4).
- [ ] **Step 3: `MaskDocument`, tests first** (`tests/test_correct_mask_document.py`; a subprocess check that importing it loads no PyQt6): opens empty without a mask; a model mask (8-bit gray PNG) is thresholded above 200; a wrong-size mask opens empty with the warn notice `"Saved mask size does not match the image; starting empty"`; an undecodable mask opens empty; save writes RGBA colour type 6, white where set, transparent elsewhere; an empty plane removes every saved variant (legacy `.jpg` too) and reports `removed=True` only if a file existed; a failed atomic write keeps the old file, leaves no `.tmp` and stays dirty; an unwritable folder returns a result instead of raising; dirty is a pixel comparison (stroke then undo → clean; save → clean); `revert()` restores the saved plane and clears the history; `status()` gives `("none", "No mask")`, `("clean", None)`, `("dirty", None)`, `("error", "Not saved", reason)` after a failed save until the next `touch()`; **stamps:** `file_stamps()` has exactly one entry per `MaskStore.for_image(image).layout.mask_candidates(stem)` path in that order, `(path, None)` for a missing file and `(path, mtime_ns, size)` for a present one; writing the `.png`, rewriting it, or adding a legacy `.jpg` each change it; `()` when closed; 25 small strokes on 2048x1536 keep the history under 1 MiB. Implement (move `_open_document` and `save_mask` bodies verbatim); PASS.
- [ ] **Step 4: perf.** `scratch/P4a-mask-document/bench_mask_document.py`: save ≤ 10 ms (640); dirty check ≤ 0.1 ms (640) / ≤ 1 ms (2048) once per revision. Record in the notes.
- [ ] **Step 5: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P4a-mask-document/pt --continue-on-collection-errors tests/test_correct_mask_document.py tests/test_correct_goldens_data.py tests/test_mask_editor_history.py tests/test_mask_editor_store.py tests/test_mask_editor_behaviour.py` → all pass.
- Full suite (same flags, no file arguments) → no failure in P4a's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on every owned file clean (the JSON excepted).

**Perf budget:** save ≤ 10 ms (640); dirty check ≤ 0.1 ms (640) / ≤ 1 ms (2048) once per revision.

**UX details:** notices keep today's wording.

---

## P4b — Mask layer (wave 1) — id `P4b-mask-layer`

**Goal:** the mask as an `EditLayer`: `MaskLayer` with Brush / Eraser / Fill tools over `PaintCanvas` and `RasterLayerItem`, following the mid-gesture rule, and the mask goldens replayed through it.

**Start after:** P1a, P2, P6, P4a. **Consumes:** C2, C4, C6 `RasterLayerItem`, C9, C13 `MaskDocument`, C18. **Provides:** C13 `MaskLayer`. **Consumers:** P7a, P7b (W2).
**Owns:** creates `app/gui/correct/mask_layer.py`, `app/gui/correct/mask_tools.py`, `tests/test_correct_mask_layer.py`, `tests/test_correct_mask_goldens.py`.
**Read-only:** `app/gui/correct/mask_document.py`, `app/gui/paint_canvas.py`, `app/gui/canvas_layers/raster.py`, `app/gui/brush_preview.py`, `app/gui/mask_tracing_interface.py` (today's semantics), `tests/correct_goldens_support.py`, `tests/data/correct_goldens.json`.
**Size:** about 1,000 changed lines.

- [ ] **Step 1: tools and layer, tests first** (`tests/test_correct_mask_layer.py`, offscreen; a fake `LayerHost` records calls): `missing_members(layer) == []`; `tools` as C13; brush/eraser/fill driven through `press` / `move(dragging=True)` / `release` give exactly the plane of a direct `PaintCanvas` replay; fill outside → `FILL_MISS_NOTICE` (info) and no history entry; `clear()` is undoable and posts `CLEARED_NOTICE`; `layer_edited("mask")` once per committed stroke / fill / clear and never for undo, redo, open, revert or save; `layer_changed` after undo, redo, save, revert, tool change and at commit, **never per stroke segment**; `undo()` / `redo()` return True iff a step happened and False only on an empty stack; `set_active` toggles the ring and the effective opacity (0.60 active, 0.30 inactive); `set_visible(False)` hides the item and the ring and a hidden layer shows no hover ring; ring colour `tokens.MASK`, solid for the brush, dashed for the eraser, hidden for fill and while panning (`host.is_panning()`); `set_size` clamps to 1..100, `step_size` is ±12 % (min 1), `size_spec()` is None for fill; `double_click` does nothing (press, release, double-click, release paints one dab and records one history entry); `gesture_active()` during a stroke, `finish_gesture()` commits it; **mid-gesture:** press + two dragging moves, then each of Esc (`cancel_transient()`), `set_tool("fill")`, `set_tool("eraser")`, a layer switch (`finish_gesture()` + `set_active(False)`) and PgDn (`finish_gesture()` + `save()`), then two more dragging moves and a release: those change nothing (only the ring moves), `layer_edited` fired once (at the forced finish), `save()` writes a file equal to that of a replay of the committed part through a fresh `PaintCanvas` over an equal plane, and `pixmap_to_argb_array(layer.canvas.image)` equals `lut[plane]` (the canvas holds nothing that is not in the plane); `open` / `close` / `revert` / `save` return C2 values (`SaveOutcome.cleared` when a file was removed, the path of the written file) and commit a stroke in progress first; `file_stamps()` is the document's; the item is at z 1.0 and receives `canvas_changed` for each segment's rect (spy). Run → FAIL.
- [ ] **Step 2: implement** `mask_tools.py` and `mask_layer.py` until Step 1 passes. The stroke state lives in the layer and its `PaintCanvas` (C2).
- [ ] **Step 3: goldens through the layer** (`tests/test_correct_mask_goldens.py`): for both synthetic cases replay `ops.mask` through `MaskLayer` (C18 semantics): every per-op plane sha equals `per_op_plane_sha256` and the saved file matches `saved` (pixels sha, shape, colour type 6, on count, measured area); `ops.mask_clear` on a fresh project matches its golden. PASS.
- [ ] **Step 4: perf.** `scratch/P4b-mask-layer/bench_mask_layer.py` (adapt `scratch/ux-latency/bench_mask_edits.py`).
- [ ] **Step 5: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P4b-mask-layer/pt --continue-on-collection-errors tests/test_correct_mask_layer.py tests/test_correct_mask_goldens.py tests/test_correct_mask_document.py tests/test_correct_layering.py` → all pass.
- Full suite (same flags, no file arguments) → no failure in P4b's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** segment handler ≤ 0.05 ms; 200-segment commit ≤ 2 ms (640); open (document + rebuild) ≤ 1.5 ms (640) / ≤ 15 ms (2048).

**UX details:** rings in the mask colour; Fill has no ring; notices keep today's wording.

---

## P5a — Skeleton document, model hooks and startup (wave 1) — id `P5a-skeleton-document`

**Goal:** the Qt-free half of the skeleton layer: model hooks for the journal (`on_action`, `drop_redo`, `take_changed_box`), cheaper commits (ROI skeletonize, gated), lazy scikit-image with a prewarm hook (startup), `SkeletonDocument` (today's load/save verbatim, pinned file stamps) and the pure overlay tests.

**Start:** at once (builds on P6's Step 0: `layer.py`, `SnapshotHistory.drop_redo`; until it lands, do Steps 2 and 3). **Provides:** C4 (model, `prewarm_skeletonize`), C14 `SkeletonDocument`. **Consumers:** P5b (after P5a finishes), P8b (the prewarm).
**Owns:** creates `app/gui/correct/skeleton_document.py`, `tests/test_correct_skeleton_document.py`, `tests/test_skeleton_roi.py`, `tests/test_startup_import_budget.py`, `tests/test_skeleton_overlay.py`; modifies `app/gui/skeleton_graph_model.py`, `app/inference/metrics.py`, `tests/test_skeleton_model_editing.py`.
**Read-only:** `app/gui/skeleton_overlay.py`, `app/gui/skeleton_document.py` (`SkeletonFiles`, `CORRECTED`, `GENERATED`, `GeneratedResolver`), `app/gui/skeleton_correction_interface.py` (verbatim source), `app/project_layout.py`, `app/provenance.py` (unchanged), `scratch/ux-latency/{check_roi_guard.py,bench_skel_edits.py}`. P5a builds its own small fixtures (it does not wait for P4a's golden support).
**Size:** about 1,200 changed lines.

- [ ] **Step 1: model hooks, tests first** (`tests/test_skeleton_model_editing.py`): `on_action` fires once per `push_undo()` and not when `mask is None`; `drop_redo()` clears redo only; `take_changed_box()` returns the union of edit boxes since the last call, `(0, 0, 0, 0)` when nothing changed, `None` after load / `set_empty` / undo / redo / the `mask` setter, and resets; a subprocess `import app.gui.skeleton_graph_model` does not load `skimage`, `skimage` loads on the first `skeletonize()`, and `prewarm_skeletonize()` loads `skimage.morphology`, is idempotent (a second call does nothing) and works from a `threading.Thread`. Implement (C4); PASS.
- [ ] **Step 2: lazy scikit-image at startup, test first** (`tests/test_startup_import_budget.py`): a subprocess `import app.gui.main_window` loads none of `skimage`, `scipy`, `torch`, `dash`, `pandas`, `plotly`, `flask` (measured today: it loads `skimage` and, through it, `scipy`; `cv2` and `PIL` are allowed); a subprocess import of `app.handlers.skeleton_handler` and `app.handlers.mask_generation_handler` (what `MainWindow.__init__` imports) loads no `skimage` either. Move `from skimage.morphology import skeletonize` into `app/inference/metrics.py::preprocess_image` (no other change: same function, same values); `tests/test_measure_core.py`, `tests/test_regression.py` stay green. `app/provenance.py` is not changed (spec D34). PASS.
- [ ] **Step 3: ROI skeletonize (gated), test first** (`tests/test_skeleton_roi.py`): seeded random edits (polyline, eraser, connect) on the synthetic golden skeleton at 640x480, a deterministic dense 640 skeleton (≥ 15k px) and a 2048x1536 upscale; after each edit `model.skeletonize()` equals `_skeletonize_uint8(edited)` on the full frame (0 mismatches); a base that is not a fixed point (set through the `mask` setter) takes the full path. **Always on:** about 50 edits per case. **Opt-in** (`SPROUTS_PERF_TESTS=1`, `pytest.mark.skipif` otherwise): 300 edits per case, and a 2,800-edit run over the three cases (the scout's `check_roi_guard.py` scale). Implement in `skeletonize()`: track the edit box since the last skeletonize and whether the base was a fixed point; skeletonize the window (box + 16 px), write back the core (box + 8 px) only if nothing was deleted in the 2 px band outside the core, else run the full frame (`scratch/ux-latency/check_roi_guard.py::guard_roi`). **Gate:** if any mismatch remains (always-on or opt-in), remove the ROI path (keep the full frame) and record why in the notes; the equality test stays.
- [ ] **Step 4: `SkeletonDocument`, tests first** (`tests/test_correct_skeleton_document.py`; Qt-free subprocess check): the correction wins over the generated file; the generated file opens when there is no correction; the injected resolver is asked first; nothing on disk → empty at image size, `source is None`; a 341x256 raster is resized nearest and thinned to image size; save uses the loaded raster size, or `SKELETON_OUTPUT_SIZE` without one; saved values are 0/255; a failed save keeps the old file, leaves no `.tmp`, sets `save_error` and `status()` is `("error", "Not saved", reason)` until the next edit; an unwritable folder does not raise; `reopen()` re-reads the start file and clears the history; `load_file(path)` sets `source` to `CORRECTED` / `GENERATED` / `LOADED` by path identity; dirty is the model's pixel comparison; `status()` labels are today's (`"Saved"`, `"Generated"`, `"Generated · empty"`, `"Loaded file"`, `"No skeleton"`); **stamps:** `file_stamps()` is `((correction_path, …), (generated_path, …))` as C14 (each `(path, mtime_ns, size)` or `(path, None)`), with the generated path from the resolver when it finds a file, else `layout.skeleton_path(stem)`; our own first save changes it (the correction appears); rewriting the generated file changes it; a resolver that starts finding a file changes it (asked afresh per call); `()` when closed. Implement (verbatim moves); PASS.
- [ ] **Step 5: pure overlay tests.** Copy `test_overlay_halo_surrounds_the_line_only` and `test_touching_endpoint_markers_stay_filled` into `tests/test_skeleton_overlay.py` (the originals go with P8b's deletion).
- [ ] **Step 6: perf.** `scratch/P5a-skeleton-document/bench_model.py` (adapt `bench_skel_edits.py`): ROI skeletonize per edit; `-X importtime -c "import app.gui.main_window"` median of 5. Record in the notes.
- [ ] **Step 7: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P5a-skeleton-document/pt --continue-on-collection-errors tests/test_skeleton_model_editing.py tests/test_skeleton_model_topology.py tests/test_skeleton_roi.py tests/test_startup_import_budget.py tests/test_correct_skeleton_document.py tests/test_skeleton_overlay.py tests/test_skeleton_editor_tools.py tests/test_skeleton_editor_lifecycle.py tests/test_measure_core.py tests/test_regression.py` → all pass.
- `$env:SPROUTS_PERF_TESTS='1'` + the same command for `tests/test_skeleton_roi.py` → pass (the opt-in runs; record the duration).
- Full suite (same flags, no file arguments) → no failure in P5a's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** ROI skeletonize per edit (median) ≤ 0.2 ms (real 640) / ≤ 1.5 ms (dense 2048) (scout: 0.067 / 0.73 ms); `import app.gui.main_window` at least 150 ms below the 402.5 ms baseline; the always-on ROI test adds ≤ 2 s to the suite.

**UX details:** none visible (behaviour unchanged; startup faster).

---

## P5b — Skeleton layer (wave 1) — id `P5b-skeleton-layer`

**Goal:** the skeleton as an `EditLayer` + `ToolHost` over the unchanged tools and `SkeletonLayerItem`, following the mid-gesture and undo rules, with cached endpoint markers shown only while active, the skeleton below its decorations, and the skeleton goldens replayed through it.

**Start after:** P1b, P6, P5a, P4a (test data). **Consumes:** C2, C4, C6 `SkeletonLayerItem`, C14 `SkeletonDocument`, C18. **Provides:** C14 `SkeletonLayer`, `DraftState`. **Consumers:** P7a, P7b (W2).
**Owns:** creates `app/gui/correct/skeleton_layer.py`, `tests/test_correct_skeleton_layer.py`, `tests/test_correct_skeleton_goldens.py`.
**Read-only:** `app/gui/skeleton_tools.py` (unchanged), `app/gui/skeleton_overlay.py` (`SkeletonDecorations`), `app/gui/correct/skeleton_document.py`, `app/gui/skeleton_graph_model.py`, `app/gui/canvas_layers/skeleton.py`, `app/gui/skeleton_correction_interface.py` (verbatim source), `app/gui/brush_preview.py`, `tests/correct_goldens_support.py`, `tests/data/correct_goldens.json`, `scratch/ux-latency/{bench_skel_edits.py,bench_endpoint_paint.py}`.
**Size:** about 1,200 changed lines.

- [ ] **Step 1: `SkeletonLayer`, tests first** (`tests/test_correct_skeleton_layer.py`, offscreen, fake host) — the layer-level port of `tests/test_skeleton_editor_lifecycle.py` and `tests/test_skeleton_editor_tools.py`: `missing_members(layer) == []` for `EditLayer` and for `skeleton_tools.ToolHost`; `set_tool` behaves like today's `_activate_tool` (deactivates, resets the draft unless kept, hides the ring, clears connect/highlight) and Select hands a branch to Polyline keeping the draft; a fast eraser drag leaves no fragments; drag repaints are coalesced (≤ 1 `item.set_mask` per 16 ms, polled like `test_skeleton_editor_tools.py:208-221`) and the release thins and renders fully; `layer_changed` is not fired for the coalesced drag renders, only at the release and full renders; the eraser ring is the footprint at 1x and 4x and hidden for other tools, while panning and when inactive; Delete removes a picked branch and undo restores it; Delete with nothing picked is a no-op; polyline commit by Enter, double-click and `finish_polyline()`; Esc (`cancel_transient`) drops the draft; S toggles smooth only in Polyline (`key()`); `undo_in_gesture()` pops a draft point; Connect joins two endpoints; `clear()` is undoable and posts `CLEARED_NOTICE`; `layer_edited("skeleton")` once per action (through `model.on_action`), `layer_changed` on status / tool / draft / history changes; **undo / redo:** one snapshot step, True iff done, False only on an empty stack, never a draft pop; with a draft and the layer inactive `undo()` drops the draft and notifies `("Polyline draft discarded", "info")`; with the layer active it drops the draft without that toast; endpoint markers visible only while active, and `decorations.endpoints_item.cacheMode()` is `DeviceCoordinateCache`; `set_active(False)` hides preview, markers and ring but keeps `draft.points`, `set_active(True)` restores the preview; effective opacity 0.85 / 0.425; `set_visible(False)` hides item and decorations; `step_size` from 20 gives 22 / 18, clamped to 1..80; `size_spec()` only for the eraser; the item receives `changed` boxes from `take_changed_box()`, and `take_changed_box(` is called nowhere in `app/` except `app/gui/correct/skeleton_layer.py` (AST scan; the model's definition excepted); `tool_hint()` gives today's texts, including the empty-skeleton hint; **gestures:** `gesture_active()` during an eraser drag, a Connect drag and a polyline point drag; `finish_gesture()` ends an eraser drag (thinned, recorded once); an eraser drag then `set_tool("polyline")` releases the eraser first and the rest of that drag (moves, release) reaches no tool (the polyline draft stays empty); a Connect drag then `finish_gesture()` joins at the last position (one action); Esc during a Connect drag drops it (no action, as today); **z-order:** `item.zValue() == 1.5`, and at 800 % a polyline draft drawn across a skeleton line paints in the preview colour (`tokens.ACCENT`) where they cross; `file_stamps()` is the document's. Run → FAIL.
- [ ] **Step 2: implement** `skeleton_layer.py` (move the `ToolHost` methods of `skeleton_correction_interface.py:326-396` and the document glue) until Step 1 passes.
- [ ] **Step 3: goldens through the layer** (`tests/test_correct_skeleton_goldens.py`): replay `ops.skeleton` and `ops.skeleton_clear` (C18) on both synthetic cases: `start` (source, raster sha and shape, save size), every per-op raster sha and the saved file match the goldens. PASS.
- [ ] **Step 4: perf.** `scratch/P5b-skeleton-layer/bench_skeleton_layer.py` (adapt `bench_skel_edits.py`, `bench_endpoint_paint.py`): polyline commit through the layer, item update and the next paint included; eraser release; an eraser drag on the dense 2048 skeleton (coalesced render + paint per frame); endpoint markers full repaint with 191 markers. Record in the notes.
- [ ] **Step 5: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P5b-skeleton-layer/pt --continue-on-collection-errors tests/test_correct_skeleton_layer.py tests/test_correct_skeleton_goldens.py tests/test_correct_skeleton_document.py tests/test_skeleton_overlay.py tests/test_skeleton_editor_tools.py tests/test_skeleton_editor_lifecycle.py tests/test_correct_layering.py` → all pass.
- Full suite (same flags, no file arguments) → no failure in P5b's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** polyline commit through the layer (item update included) ≤ 6 ms (640 real) / ≤ 40 ms (2048 dense); eraser release ≤ 5 ms (640); eraser-drag frame ≤ 16 ms (2048 dense); endpoint markers full repaint with 191 markers ≤ 0.5 ms.

**UX details:** eraser ring dashed in the skeleton colour; endpoint markers only when the skeleton is active; the polyline preview hides (draft kept) while the mask is active and always paints above the line; "Polyline draft discarded" (info) when an undo drops a hidden draft.

---

## P6 — Workspace logic (wave 1) — id `P6-workspace-logic`

**Goal:** the contracts everyone codes against, and the Qt-free brain of the workspace: the layer protocol, the undo journal, the session (open / save / prompt / dirty / undo / save reports / gestures first) and the input router.

**Start:** at once. **Provides:** C2, C3, C4 (`SnapshotHistory.drop_redo`), C15, C16, C18 `correct_fakes.py`. **Consumers:** P4a and P5a (Step 0, in parallel), P4b, P5b (after P6 finishes), P7a, P7b (W2).
**Owns:** creates `app/gui/correct/__init__.py`, `app/gui/correct/layer.py`, `app/gui/correct/session.py`, `app/gui/correct/tool_router.py`, `app/editing/journal.py`, `tests/test_editing_journal.py`, `tests/test_correct_session.py`, `tests/test_correct_tool_router.py`, `tests/test_correct_layering.py`, `tests/correct_fakes.py`; modifies `app/editing/history.py`, `app/editing/__init__.py`, `tests/test_editing_history.py`.
**Read-only:** `app/gui/editor_canvas.py` (P2's `InputSink`, structural only; P6 does not import it), `app/gui/skeleton_tools.py`, `app/gui/mask_tracing_interface.py` / `skeleton_correction_interface.py` (today's semantics).
**Size:** about 1,600 changed lines.

- [ ] **Step 0 (first minutes): unblock the others.** Create `app/gui/correct/__init__.py` (a docstring naming every module the package will hold: `layer`, `session`, `tool_router`, `mask_document`, `mask_layer`, `mask_tools`, `skeleton_document`, `skeleton_layer`, `controls`, `layer_panel`, `editor`; no imports) and `app/gui/correct/layer.py` exactly as C2 (with `missing_members` implemented); add `SnapshotHistory.drop_redo()` to `app/editing/history.py` with its test in `tests/test_editing_history.py` (drops redo only, `on_change` once, no-op when empty). Write "Step 0 landed" at the top of your notes.
- [ ] **Step 1: journal, tests first** (`tests/test_editing_journal.py`, stdlib only): records order across layers; `undo` returns the newest layer and moves it to redo; `redo` mirrors; `record` empties the redo list; a `step` returning False drops that entry and tries the next (eviction); `can_undo` / `can_redo` with probes; `forget(layer)` removes only that layer's entries (undo and redo); `clear`; `maxlen` drops the oldest; `undo_entries` / `redo_entries` order. Implement `app/editing/journal.py`; export `EditJournal` from `app/editing/__init__.py` and note in its docstring that the package stays standard-library only. PASS.
- [ ] **Step 2: fakes.** `tests/correct_fakes.py` (C18), including the scriptable gesture.
- [ ] **Step 3: session, tests first** (`tests/test_correct_session.py`, no Qt): `open` opens every layer with the same arguments, forwards their notices, clears the journal and records stamps; `files_changed` / `image_changed`; save targets: dirty layers only (mask first), none dirty → the active layer, a failure does not stop the next layer; toasts: `"Saved mask and skeleton · a.jpg"` when both saved, single noun when one, the cleared-only message as info, each failure as danger with its message; `save_dirty` (PgDn) saves only dirty layers and returns an empty report when clean; **save reports:** `save()` with two dirty layers calls `on_saved` twice, mask first, with the layers' outcomes (a failed one included); `save_dirty()` likewise, and nothing when clean; `release()` → SAVE calls `on_saved` once per dirty layer; saving again after a failure calls it again; none dirty → `save(active)` reports the active layer once; **gestures first:** `save`, `save_dirty`, `release`, `open`, `close` and `reload_documents` call `finish_gesture()` on a layer whose `gesture_active()` is True before `dirty_layers()` (a fake whose `finish_gesture()` makes it dirty is saved by `save()` / `save_dirty()` and prompted for by `release()`); `finish_gestures()` leaves layers without a gesture alone; `release`: clean → True without a prompt; Cancel → False; Discard → each dirty layer `revert()`ed, its journal entries forgotten → True; Save → saved → True; a failed save → the second prompt names only the failed layers; Keep → False (edits kept); Discard → reverted → True; `release([SKELETON])` asks about the skeleton only; nouns `"mask"`, `"skeleton"`, `"mask and skeleton"` and the image basename reach the prompt; `undo`: None while any layer's `gesture_active()`; the active layer's `undo_in_gesture()` wins; otherwise the journal's newest across layers; evicted entries (a fake whose bounded history returns False) skipped; `on_undone(layer, "undo")`; `redo` mirrors; `layer_edited` records, drops the other layers' redo and empties the journal's redo list; `on_dirty_changed` fires once per aggregate transition; `reset` = release + close; `close` clears the journal and `image_path`; `forget(layer)`. Implement; PASS.
- [ ] **Step 4: router, tests first** (`tests/test_correct_tool_router.py`, offscreen for the event objects, fake layers): press on an open active layer → `layer.press` and True; closed → False; hidden → shown first (`on_layer_shown`); moves forwarded with `dragging`; release and double-click forwarded; hover left and `pan_changed(True)` → `layer.hover(None)`; B press / release: `b_held`, `on_b_hold(True/False)`, a tap → `on_tool_request(MASK, "brush")`, B + wheel → `layers[MASK].step_size(±1)` and no tap on release; auto-repeat and modified B are ignored; Esc → active `cancel_transient()`; S, Enter, Delete, Backspace → active `key(event)`; Space never consumed; **`set_active` mid-gesture:** with the active layer's `gesture_active()` True, `set_active(other)` calls its `finish_gesture()` first, then switches and returns True; `set_active(current)` → False; `canvas_size` forwarded. Implement; PASS.
- [ ] **Step 5: guards** (`tests/test_correct_layering.py`): a subprocess import of `app.editing.journal`, `app.gui.correct.layer`, `app.gui.correct.session` (and of `app.gui.correct.mask_document`, `app.gui.correct.skeleton_document` when those files exist, skipped otherwise) loads no `PyQt6`; every `app/gui/correct/*.py` has at most 450 lines.
- [ ] **Step 6: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P6-workspace-logic/pt --continue-on-collection-errors tests/test_editing_journal.py tests/test_editing_history.py tests/test_correct_session.py tests/test_correct_tool_router.py tests/test_correct_layering.py` → all pass.
- Full suite (same flags, no file arguments) → no failure in P6's files; foreign failures listed in the notes.
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** journal record / undo ≤ 5 µs; session dirty aggregation ≤ 0.1 ms with two layers (layers memoize their own compares); `finish_gestures()` with no gesture ≤ 5 µs.

**UX details:** the prompt wording is C11's; the toast texts are fixed by the tests above.

---

## W1 → W2 handover (lead)

- [ ] Every W1 agent has stopped; read each `notes/<id>.md` (PROGRESS complete, foreign failures, contract problems, perf numbers).
- [ ] Review each package against the spec and the contracts (Review Focus).
- [ ] One full-suite run of the combined tree (Global Constraints flags, `--basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/lead/pt`) → all pass except the known skips; record count and duration in `STATUS.md`.
- [ ] `ruff check` and `ruff format --check` on every W1 file.
- [ ] Commit in dependency order: P6, P2, P1a, P1b, P4a, P5a, P4b, P5b, P3 (optional per-commit verification in a temporary worktree, explained in one line in `STATUS.md`).
- [ ] Launch W2.

---

## P7a — Correct controls and Layers panel (wave 2) — id `P7a-correct-controls`

**Goal:** the Correct page's own widgets on the shared chrome: two rail groups, dock controls (one size slider per distinct size spec, opacity, Load, Undo, Redo, Clear, Save), the polyline prompt, the topbar clear button and the Layers panel — pure views with the C20 API.

**Start:** at once in W2 (every W1 package committed). **Consumes:** C2, C10, C13, C14 (`DraftState`), C18 `correct_fakes.py`. **Provides:** C20. **Consumer:** P7b (after P7a finishes).
**Owns:** creates `app/gui/correct/controls.py`, `app/gui/correct/layer_panel.py`, `tests/test_correct_controls.py`.
**Read-only:** every wave-1 module; `app/gui/class_panel.py` (visual reference for the Layers panel); `app/gui/widgets/*` (`IconButton`, `DockSlider`, `FloatingDock`, `ToolRail`, `OverlayLayout`, `keep_canvas_focus`).
**Size:** about 800 changed lines.

- [ ] **Step 1: tests first** (`tests/test_correct_controls.py`, offscreen): build `EditorChrome` on a host with the real `MaskLayer` and `SkeletonLayer` on a parented scene (and `FakeLayer`s where a state is easier to script):
  - rail order: Brush, Eraser, Fill | Select, Eraser, Polyline, Connect | Pan | Enhance, with separators; tooltips exactly as in the UX details below; one exclusive group; a click emits `tool_requested(layer_id, tool_id)` and changes no layer;
  - `show_layer("skeleton")`: the mask group uses `tokens.TEXT_FAINT`, the skeleton group `tokens.TEXT_MUTED` with `on_color` = its layer colour; Load visible only for the skeleton; the topbar clear button reads "Clear skeleton" with its tooltip, "Clear mask" for the mask; the dock Clear tooltip names the active layer;
  - `sync(layer)`: the current tool's button is checked; the brush and the mask eraser share one size slider, the skeleton eraser has its own, none is visible for Fill / Select / Polyline / Connect; the visible slider shows `layer.size()` and the spec's range, icon and tooltip; the opacity slider shows `round(layer.opacity() * 100)`; `sync` emits no signal; moving a slider emits `size_changed(v)` / `opacity_changed(v / 100)`;
  - `set_history` enables Undo / Redo; `set_draft(DraftState(True, 2, False))` shows the prompt with Finish enabled, 1 point → Finish disabled, 0 points or `None` → hidden; Smooth shows `smooth` and emits `smooth_toggled`; Finish / Cancel emit their signals;
  - `LayerPanel`: two rows, mask on top, key hints 1 / 2, swatches in the layer colours; `set_status` label and tooltip mapping (C20); `set_active` gives only that row the `BG_2` fill and the 2 px bar; a row click emits `activate_requested`; an eye click emits `visibility_toggled(id, not visible)` and no activation; `set_visible_state` changes the icon only; `flash` highlights for about 300 ms and restores; `preferred_height` / `fit_height` work with `OverlayLayout`;
  - focus: clicking any control leaves keyboard focus on the view; the panel and its buttons have `NoFocus`; overlays (rail, dock, chip, zoom, popover, panel, prompt) do not intersect at 1400x900 and 700x520.
  Run → FAIL.
- [ ] **Step 2: implement** `layer_panel.py` (ClassPanel's visual language), then `controls.py`, until Step 1 passes (each ≤ 450 lines).
- [ ] **Step 3: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P7a-correct-controls/pt --continue-on-collection-errors tests/test_correct_controls.py tests/test_correct_layering.py tests/test_design_system_colour_literals.py` → all pass.
- Full suite (same flags, no file arguments) → no failure in P7a's files; the expected foreign reds are P8a's two guard tests (until the swap).
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** `show_layer()` + `sync()` ≤ 1.5 ms together (part of the ≤ 5 ms layer switch).

**UX details:** spec sections 3.2 and 4. The Layers panel is about 155 px wide, `tokens.BG_2` active fill with a 2 px bar in the layer colour; dots and labels follow the chip's state colours; the prompt reads "Finish line (Enter)", "Cancel (Esc)", "Smooth". Rail tooltips: `"Brush (B) · hold B + scroll to resize"`, `"Eraser (E) · [ and ] resize"`, `"Fill a closed outline (F)"`, `"Select (V) · pick a branch, Delete removes it"`, `"Polyline (P) · Enter finish · Esc cancel"`, `"Connect endpoints (C)"`. Topbar clear button: `"Clear mask"` (tooltip "Clear the canvas. Undo with Ctrl+Z; Save removes the file.") / `"Clear skeleton"` (tooltip "Remove every skeleton line from this image (Ctrl+Z undoes)").

---

## P7b — The Correct editor (wave 2) — id `P7b-correct-editor`

**Goal:** `CorrectEditor` (C17) composes the canvas, photo, both layers, session, router, chrome, controls and Layers panel into the product behaviour of spec sections 3-9; editor-level behaviour, history, key, golden and perf tests.

**Start after:** P7a. **Consumes:** every W1 contract, C20. **Provides:** C17. **Consumer:** P8b (after P7b finishes).
**Owns:** creates `app/gui/correct/editor.py`, `tests/test_correct_editor.py`, `tests/test_correct_editor_history.py`, `tests/test_correct_editor_keys.py`, `tests/test_correct_goldens.py`, `tests/test_correct_perf.py`.
**Read-only:** every wave-1 module, `app/gui/correct/controls.py`, `app/gui/correct/layer_panel.py`, `app/gui/widgets/*`, the tests P8b will delete (the behaviours to port).
**Size:** about 1,700 changed lines.

`editor.py` builds `EditorCanvasView` (sink set to the router once it exists), the scene (parented to the view), `PhotoLayerItem` (z 0), `MaskLayer(self, scene)`, `SkeletonLayer(self, scene)`, `EditorChrome(self, view, norm)`, `CorrectControls(self, chrome, layers)`, `LayerPanel(layers)` in the `side_panel` role, `CorrectSession(layers, prompts=<the editor_prompts module>, parent=lambda: self, on_dirty_changed=…, on_notice=…, on_history_changed=…, on_undone=…, on_saved=…)` and `ToolRouter(layers, …)`; implements `LayerHost` by delegation; installs the Correct `QShortcut`s through `shortcuts.install_shortcuts(self, shortcuts.CORRECT, {...})` for `undo`, `redo`, `save`, `mask_layer`, `skeleton_layer`, `swap_layer`, `fill`, `select_tool`, `polyline`, `connect`, `eraser`, `size_smaller`, `size_larger`, `toggle_layers`, `toggle_reference`, `fit`, `zoom_100`, `zoom_in`, `zoom_out` (zoom in/out → `zoom_step(±1)`), with `setAutoRepeat(False)` on `swap_layer`, `toggle_layers`, `toggle_reference`, plus PgDown / PgUp `QShortcut`s (→ `navigate(±1)`). It wires C20's signals (tool requests, sliders, buttons, prompt, Layers rows) and C17's rules (save reports → signals, `finish_gestures()` first, `end_gesture()`).

- [ ] **Step 1: behaviour tests first** (`tests/test_correct_editor.py`, `tests/test_correct_editor_keys.py`, `tests/test_correct_editor_history.py`), porting every behaviour of the tests P8b deletes (`tests/test_mask_tracing_interface_smoke.py`, `tests/test_skeleton_correction_interface_smoke.py`, `tests/test_skeleton_editor_lifecycle.py`, `tests/test_skeleton_editor_tools.py`, the mask cases of `tests/test_mask_editor_behaviour.py`, `tests/test_gt_tracing_interface.py::test_mask_mode_unchanged` → "saving never writes `gt/`"), plus the new ones:
  - page protocol (C17 signals and methods); `load_image` decodes the photo once (spy on `SourceImageCache.get` / `imread_unicode`); opens both layers; fits; an undecodable image → danger notice, nothing changes;
  - same image again: dirty → no prompt, edits and history kept; clean and unchanged → no reload, zoom and centre kept; a changed mask / skeleton file → documents reload, view kept; a changed image file or a new image → opens fitted; **re-entry after our own save:** edit the skeleton, Ctrl+S, `load_image` of the same path (what leaving and re-entering Correct does) → `layers["skeleton"].document.model.mask` equals the mask of a fresh `SkeletonCorrectionModel` after `load_from_raster(<saved correction read as grayscale>, target_size=(w, h))`, as today's editor gives;
  - layer switching by 1 / 2 / X, by a rail tool of the other group, by a Layers-panel click; **mid-drag** the drag is committed first, then the layer switches, and the rest of the drag paints nothing; a polyline draft survives mask → skeleton → mask; effective opacities (active vs × 0.5); H hides / shows both layers, Shift+H the inactive one; pressing on a hidden active layer shows it with an info toast; endpoint markers only with the skeleton active;
  - rail shows both groups, the inactive group faint; the dock size slider follows the current tool (hidden for Fill / Select / Polyline / Connect), the opacity slider the active layer, Load only for the skeleton; the polyline prompt only with skeleton + Polyline + points; Smooth in the prompt; the chip shows the aggregate state and the hint `"<Layer> · <hint>"` with the dot in the active layer's colour; Layers rows show each layer's status and flash when an inactive layer's action is undone;
  - keys: every live `QShortcut` of the page is documented in the Correct scope (plus the Global PgDown/PgUp); no key is both a `QShortcut` and a key row; the X, H and Shift+H shortcuts have `autoRepeat()` False; Ctrl+Y and Ctrl+Shift+Z redo; E is the eraser of the active layer; `[ ]` resize the active tool; hold B + wheel sizes the mask brush from either layer; B (tap) and F switch to the mask and its tool, V / P / C to the skeleton and its tool; S / Enter / Delete act only while the skeleton is active; Esc cancels the active layer's transient state (and commits a mask stroke);
  - Undo / Redo buttons follow the journal; Ctrl+Z undoes the newest action of either layer without switching layers;
  - save: mask-only edit → only `mask/<stem>.png` written (no `skeleton_corrections/` created); skeleton-only edit → only the correction written, the mask file's bytes and colour type unchanged; nothing dirty → the active layer is saved; one success toast; a partial failure names the failed layer, keeps it dirty and still saves the other; an empty mask deletes the file and emits `mask_cleared` once; `mask_saved` / `skeleton_saved` emitted once per written file; saving never writes `gt/`; a no-edit session (switch layers, change opacity and visibility, zoom, pan, Enhance, PgDn) writes nothing and a model mask keeps PNG colour type 0;
  - **save reports on every path:** `maybe_save_changes()` answered Save → `mask_saved` exactly once for a dirty mask, `skeleton_saved` once for a dirty skeleton; an emptied mask saved through the prompt → `mask_cleared` exactly once; PgDn → the same signals once each; a failed save emits nothing;
  - prompt: one prompt naming the dirty layers; Save saves then moves on; Discard reverts both (the mask plane equals the file; the skeleton equals the re-read start file); Cancel keeps image, edits and history; failed save → Keep editing returns False, Discard returns True; `dirty_changed` once per transition; `reset` honours Cancel; the clean editor never prompts;
  - PgDn / PgUp save the dirty layers without a prompt and emit `navigate_requested(±1)`; a failed save stays;
  - enhancement live and sticky across images (one `NormalizationControls`); dock and panel clicks keep keyboard focus on the canvas; overlays never collide from 1400x900 down to 700x520 with either layer active; fit keeps the image clear of every overlay;
  - Correct canvas zoom (using `tests/render_metrics.py`): smooth photo at 4x, exact mask pixels at 4x, no skeleton blocks at 8x; the OpenGL on/off swap keeps it;
  - **history across layers** (`tests/test_correct_editor_history.py`, real layers): 60 mixed actions (mask strokes, fills, clears; skeleton polylines, erasers, connects), more than 25 of them on the skeleton (eviction); undo-all restores the mask to its open state and the skeleton to the oldest state its bounded history still holds; redo-all then restores the final state of both exactly (plane and raster equal); Ctrl+Z mid-drag does nothing; an undo that drops a hidden polyline draft shows "Polyline draft discarded";
  - **mid-gesture at the editor level** (`tests/test_correct_editor_history.py`, real `QMouseEvent`s through `graphics_view` and real key events): in the middle of a mask stroke press each of Esc, F, E, 2 and PgDn, continue the drag and release, then save: the saved file equals the file of a plane replay, `pixmap_to_argb_array(mask.canvas.image) == lut[plane]`, and the rest of the drag painted nothing; after 2 and PgDn `graphics_view.drawing` is False.
  Run → FAIL.
- [ ] **Step 2: implement** `editor.py` until Step 1 passes. Keep it a composition root: no document, tool or history logic in it (the 450-line ratchet of P6 applies).
- [ ] **Step 3: goldens through the editor, tests first** (`tests/test_correct_goldens.py`): for both synthetic cases run `ops.mask` then `ops.skeleton` in one `CorrectEditor` (activating the right layer), each save matching its golden; `ops.mask_clear` / `ops.skeleton_clear` on fresh projects; a self-consistency run that interleaves the non-history ops of both scripts with a layer switch between every op and checks each saved file equals the file produced by that layer's ops alone; the same strokes delivered as real `QMouseEvent`s through `graphics_view` at zoom 1.0 and 3.7 give the same plane as the layer API. PASS.
- [ ] **Step 4: perf tests** (`tests/test_correct_perf.py`): structural, always on — one photo decode per image load; a layer switch performs 0 document loads and 0 file reads (spy `MaskStore.read`, `imread_unicode`); stroke segments never deep-copy the canvas (serial); skeleton drag `set_mask` calls ≤ 1 per 16 ms timer; journal length bounded by its maxlen. Wall-clock smoke, always on — open < 150 ms (median of 10), layer switch < 20 ms, stroke segment < 1 ms, polyline commit < 60 ms, save both < 100 ms. Strict, only with `SPROUTS_PERF_TESTS=1` — open ≤ 10 ms (640 real), layer switch ≤ 5 ms, polyline commit ≤ 6 ms; **first open in a fresh process:** a subprocess builds a `CorrectEditor`, calls `prewarm_skeletonize()` (as `MainWindow` does after show), then times the first `load_image` of a 640 image: ≤ 100 ms, and `skimage.morphology` was already loaded before it (record the time without the prewarm in the notes).
- [ ] **Step 5: gates** (below). The old editors still exist and nothing in `MainWindow` uses `CorrectEditor` yet.

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P7b-correct-editor/pt --continue-on-collection-errors tests/test_correct_editor.py tests/test_correct_editor_history.py tests/test_correct_editor_keys.py tests/test_correct_goldens.py tests/test_correct_perf.py tests/test_correct_controls.py tests/test_correct_layering.py` → all pass.
- `$env:SPROUTS_PERF_TESTS='1'` + the same command for `tests/test_correct_perf.py` → pass on this machine.
- Full suite (same flags, no file arguments) → no failure in P7b's files; the expected foreign reds are P8a's two guard tests.
- `ruff check` and `ruff format --check` on every owned file clean.

**Perf budget:** open ≤ 10 ms (640, both layers), ≤ 90 ms (2048); layer switch ≤ 5 ms; stroke segment ≤ 0.05 ms; polyline commit ≤ 6 ms (640); save both ≤ 25 ms (640); first open in a fresh process after the prewarm ≤ 100 ms (opt-in).

**UX details:** spec sections 3.2, 4.2, 4.3, 5, 6, 8. Toasts: "Mask shown" / "Skeleton shown" (info) when a hidden active layer is edited; "Undo · mask" / "Undo · skeleton" (info) when the inactive layer is undone; "Polyline draft discarded" (info, from the skeleton layer). Rail tooltips: `"Brush (B) · hold B + scroll to resize"`, `"Eraser (E) · [ and ] resize"`, `"Fill a closed outline (F)"`, `"Select (V) · pick a branch, Delete removes it"`, `"Polyline (P) · Enter finish · Esc cancel"`, `"Connect endpoints (C)"`. Topbar clear button: `"Clear mask"` (tooltip "Clear the canvas. Undo with Ctrl+Z; Save removes the file.") / `"Clear skeleton"` (tooltip "Remove every skeleton line from this image (Ctrl+Z undoes)").

---

## P8a — Docs, harness and guard tests (wave 2) — id `P8a-docs-guards`

**Goal:** everything of the swap that does not touch app code: the final docs text (describing the swapped app), the UI harness adapted to six stages, and the two guard tests that pin the post-swap contract.

**Start:** at once in W2. **Consumes:** the spec, C17, C19. **Provides:** docs, the harness copy (used by P8b's gate and the lead's integration walk), the guard tests (committed with the swap).
**Owns:** modifies `README.md`, `CLAUDE.md`, `CHANGELOG.md`, `docs/KNOWN_ISSUES.md`, `docs/Architecture_2.md`, `docs/DATA_DICTIONARY.md`; creates `tests/test_gui_public_names.py`, `tests/test_right_panel_contract.py` and the harness copy `.claude/handoff/unified-editor-2026-09-29/scratch/ui-harness/` (git-ignored).
**Read-only:** `app/`, every other test, `.claude/handoff/ui-perf-2026-09-28/scratchpad/ui-harness/` (the source; copy, never edit).
**Size:** about 600 changed lines (docs and tests) plus the harness adaptation.

- [ ] **Step 1: docs (final text).** Apply spec section 16: README (pipeline "→ Correct →", stage list, a Correct section with the key map and the PgDn / Discard / Ctrl+S rules), `CLAUDE.md` (pipeline diagram, Editors row: `CorrectEditor` composition + `RasterEditorBase` for GT, Panel indices 0-3, GT is right_panel index 3, remove the god-node claim until `graphify update .` refreshes it), `CHANGELOG.md` [Unreleased] (Changed / Fixed / Performance entries as spec section 13, "Alt+1..7" → "Alt+1..6", the provenance note of spec D34, "Scientific output changes: none (pixel goldens)"), `docs/KNOWN_ISSUES.md` (#2 six stages; #5 close CQ-10, CQ-11 and the skimage-at-startup item; add Q1 skeleton save fragmentation and the follow-ups of spec section 20), `docs/Architecture_2.md` (editor node, tree), `docs/DATA_DICTIONARY.md` (line 121: hand-traced masks come from *Correct*). These describe the app after the swap; the lead commits them right after the swap commit.
- [ ] **Step 2: harness.** Copy `.claude/handoff/ui-perf-2026-09-28/scratchpad/ui-harness/` (`harness.py`, `README.md`, `build_dataset.py`, `dataset/`) to `.claude/handoff/unified-editor-2026-09-29/scratch/ui-harness/` and update it for six stages: `STAGES` (`:53-61`), `Driver.inspect` (`:798-801`), one Correct walk replacing the two editor walks (`:1247-1369`: mask strokes, switch to the skeleton, a polyline, undo across layers, Ctrl+S, PgDn, H / Shift+H, zoom shots at 25 / 50 / 100 / 400 / 1600 %), the view map (`:1421-1423`), the matrix editor list (`:1545-1549`), the stage scenario map (`:1574-1608`), perf46k timings (`:1728-1732`). It can only run after the swap; write "harness adapted" in your notes when done (P8b and the lead read it).
- [ ] **Step 3: guard tests, red until the swap by design.** `tests/test_gui_public_names.py`: every name in `app.gui.__all__` resolves (import + `getattr`); `"CorrectEditor"` is in it; none of `MaskTracingInterface`, `MaskTracingGraphicsView`, `MaskDrawingMixin`, `create_brush_cursor`, `create_panning_cursor` is. `tests/test_right_panel_contract.py` (AST over `app/**/*.py`): no int literal in `right_panel.setCurrentIndex(<int>)`, `right_panel.widget(<int>)` or `right_panel.currentIndex() == <int>`; no string constant `"mask_tracing"` or `"skeleton_correction"` anywhere in `app/` except docstrings (the first statement of a module, class or function). Record in the notes exactly which assertions fail before the swap (today: the legacy names in `app/gui/_lazy.py`, `switch_right_panel("mask_tracing")` / `("skeleton_correction")` in `app/gui/main_window.py`).
- [ ] **Step 4: gates** (below).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P8a-docs-guards/pt --continue-on-collection-errors tests/test_gui_public_names.py tests/test_right_panel_contract.py` → fails only on the expected pre-swap assertions listed in the notes (both pass after P8b's swap).
- `./venv/Scripts/python.exe -m py_compile .claude/handoff/unified-editor-2026-09-29/scratch/ui-harness/harness.py .claude/handoff/unified-editor-2026-09-29/scratch/ui-harness/build_dataset.py` → no output.
- `ruff check` and `ruff format --check` on the two test files clean; every doc link and path named in the changed docs exists after the swap (list them in the notes).

**Perf budget:** none (no runtime code).

**UX details:** docs use the stage name "Correct" and the key map of spec section 6; the F1 sheet description matches the registry rows.

---

## P8b — Shell integration and the clean-break swap (wave 2) — id `P8b-swap`

**Goal:** one atomic swap: `MainWindow`, the topbar, the registry and `Panel` switch to `CorrectEditor`; the scikit-image prewarm starts after the window shows; the old editors and dead code go; every affected test is updated.

**Start after:** P7b. **Consumes:** C17, C19, P8a's harness (if ready). **Provides:** the swapped app.
**Owns:** creates `tests/test_correct_main_window.py`; modifies `app/gui/main_window.py`, `app/gui/shell_chrome.py`, `app/gui/panels.py`, `app/gui/shortcuts.py`, `app/gui/ui_panels.py`, `app/gui/_lazy.py`, `app/gui/welcome_screen.py`, `app/gui/skeleton_document.py`, `app/gui/skeleton_overlay.py`, `tests/test_canvas_views.py`, `tests/test_shell_chrome_smoke.py`, `tests/test_shell_chrome_topbar.py`, `tests/test_shell_chrome_topbar_editor_refusal.py`, `tests/test_integration_main_window_wiring.py`, `tests/test_library_main_window.py`, `tests/test_shell_info_shortcuts.py`, `tests/test_integrator_cross_module.py`, `tests/test_foundation_runtime_panels_feedback.py`, `tests/test_metrics_bar_smoke.py`, `tests/test_viz_embed_manager.py`, `tests/test_import_smoke.py`, `tests/test_gt_tracing_interface.py`, `tests/test_split_*.py` (only if present); deletes the files of its ownership row.
**Read-only:** everything else (P8a's files included).
**Size:** about 1,300 changed lines, deletions not counted.

- [ ] **Step 1: `tests/test_correct_main_window.py` first (red):** six stages in order with Alt+1…6 (`len(STAGE_KEYS) == len(STAGES)`); entering Correct loads the selected image; leaving prompts once; a refused leave keeps the Correct stage and page; Trace GT ⇄ Correct hand over with one prompt each way; tree click, the 40 ms arrow debounce and PgDn / PgUp open the next image in Correct; the kept image is reselected after Cancel; open folder and window close prompt once and reset both layers; `mask_saved` is connected once (enter / leave Correct 3 times, emit once → `on_mask_saved` runs once); a mask save updates the library badge and the metrics status without extra per-leaf stats; **a tree click on another image with a dirty mask, prompt answered Save → `on_mask_saved` runs exactly once and the Library badge shows the mask; an emptied mask saved through that prompt → `on_mask_cleared` exactly once and the badge clears**; leaving Correct for Measure and re-entering after a skeleton save reloads the saved correction; a skeleton save changes neither `has_skeleton` nor the metrics bar and `skeleton_saved` has no receivers; the generated-skeleton resolver reaches the skeleton layer; the OpenGL toggle reaches display, Correct and GT views; the toast offset clears the Correct dock; the F1 sheet opens on the Correct scope in the Correct stage; **the prewarm:** with `prewarm_skeletonize` patched by a spy, constructing `MainWindow`, `processEvents()` and `QThreadPool.globalInstance().waitForDone(5000)` → the spy ran once, not on the GUI thread.
- [ ] **Step 2: the swap** (app files):
  - `panels.py`: `DISPLAY = 0`, `CORRECT = 1`, `VISUALIZATION = 2`, `GT_TRACING = 3`; docstring (the contract, and that legacy keys are gone).
  - `shortcuts.py`: remove `CORRECT_MASK`, `CORRECT_SKELETON` (constants, rows, `__all__`); `SCOPES = (GLOBAL, LIBRARY, TRACE_GT, CORRECT)`; `STAGE_KEYS` Alt+1 … Alt+6; `go_to_stage` text `"Go to stage 1–6 in ribbon order (Library … Visualize)"`, display `"Alt+1 … Alt+6"`; `next_image` description `"Next image (editors save or ask first)"`; module docstring.
  - `shell_chrome.py`: `_STAGES` with one `("Correct", "Correct", "brush", "Fix the mask (area) and the skeleton (length) of an image on one canvas.")` entry after Generate; `_EDITOR_STAGES` (C19); `_enter_stage`: `"Correct"` → `if index != Panel.CORRECT: mw.toggle_correct()`; `populate()`: drop `skeleton_clear_button` and the mask `trace_clear` lookup, page `"Correct": (mw.correct_editor.clear_button,)`; docstrings (six stages).
  - `ui_panels.py`: delete `toggle_skeleton_correction_button` and `toggle_mask_tracing_button` (`:207-223`).
  - `main_window.py`: import `CorrectEditor`; construct `self.correct_editor = CorrectEditor()` (replacing both editors); delete `_mask_tracing_signals_connected`; connect once: `correct_editor.mask_saved → on_mask_saved`, `mask_cleared → on_mask_cleared`, `b_key_status_changed → on_b_key_status_changed`, `navigate_requested → navigate_image`; `set_generated_skeleton_resolver(self._generated_skeleton_for)`; never connect `skeleton_saved`; `_editors()` (C19); `_build_body` pages 0 display, 1 correct, 2 viz placeholder, 3 GT (docstring); GL toggle list = display, Correct, GT; `on_image_selected`: one Correct branch `load_image(image_path, images_base_folder=self.image_manager.original_folder)` + `_kept_other_image`, the GT branch unchanged; `toggle_correct()` (enter / leave like `toggle_gt_tracing`, then reload the current tree item); delete `toggle_mask_tracing`, `toggle_skeleton_correction` and the special cases in `toggle_gt_tracing`; no `switch_right_panel` call with a legacy key remains; `on_mask_saved` no longer toasts (records the badge, refreshes metrics); `navigate_image` steps from the image open in the current editor page (Correct or GT); `_page_image` → `editor.navigate(delta)` for Correct and GT, else `navigate_image`; paging-shortcut owners = left panel, ribbon, statusline, display page; `_install_global_shortcuts` docstring Alt+1…6; **the prewarm (C19):** at the end of `__init__`, `QTimer.singleShot(0, self._prewarm_skeletonize)`; `_prewarm_skeletonize` starts `skeleton_graph_model.prewarm_skeletonize` on `QThreadPool.globalInstance()` (exceptions logged, never raised).
  - `_lazy.py`: remove `MaskTracingInterface`, `MaskTracingGraphicsView`, `MaskDrawingMixin`, `create_brush_cursor`, `create_panning_cursor`; add `"CorrectEditor": "correct.editor"`.
  - `welcome_screen.py`: docstring "The ribbon stage labels".
  - `skeleton_document.py`: delete `ask_unsaved` and `SAVE` / `DISCARD` / `CANCEL` (replaced by `editor_prompts`); docstring.
  - `skeleton_overlay.py`: delete `SkeletonOverlayRenderer` (only the old editor used it; `SkeletonLayerItem` replaces it); module docstring.
- [ ] **Step 3: deletions.** Delete the app and test files of the P8b ownership row (with the shell). Cross-check the replaced tests against `scout/contracts-tests.md` §1.2 and name the replacing test for each in the notes (P1a, P4b, P5b, P6, P7a, P7b).
- [ ] **Step 4: test updates.**
  - `tests/test_canvas_views.py`: `VIEWS = [MagnifyingGraphicsView, EditorCanvasView]` (instantiate with `view_cls(None)`); retarget the mask hover / Space pan / left-press-mid-middle-drag tests to `CorrectEditor` with the mask layer active and the skeleton image-pixel delegation test to the skeleton layer; delete `test_skeleton_view_reports_every_zoom_change_to_its_editor`.
  - `tests/test_shell_chrome_smoke.py`: `_CONNECT_MAP_ATTRS` without the two legacy buttons; `right_panel.count() == 4`; six stage names; `widget(Panel.GT_TRACING) is gt_tracing_interface`; fixtures patch `correct_editor.maybe_save_changes`; the stage-button test clicks "Correct" and expects `Panel.CORRECT`; the failed-GT-save test uses the six stages and drops `_mask_tracing_signals_connected`.
  - `tests/test_shell_chrome_topbar.py`: `STAGES`; replace `test_correct_skeleton_page_has_a_working_action` by "the Correct page's clear button clears the active layer and relabels on a layer switch".
  - `tests/test_shell_chrome_topbar_editor_refusal.py`: `STAGES`; the refusing-editor fixture patches `toggle_correct`; parameters `(CORRECT, "Correct", "Measure")`, `(CORRECT, "Correct", "Library")`, `(CORRECT, "Correct", "Trace GT")`, `(GT, "Trace GT", "Generate")`, `(GT, "Trace GT", "Correct")`; wider-action-page source stages.
  - `tests/test_integration_main_window_wiring.py`: editor attribute parameters → `correct_editor`; `== 7` → `len(shell_chrome.STAGES)`; the stage-shortcut test finds "Measure" by `STAGES` index.
  - `tests/test_library_main_window.py`: fixture; the saved-mask test toggles Correct (the "no per-leaf stat" assertion must hold); the PgDn test's stub `load_image` accepts `images_base_folder=`; the GL view list is `[display, correct, gt]`.
  - `tests/test_shell_info_shortcuts.py`: `SCOPES`; the duplicate / global-conflict tests use `sc.CORRECT`; redo keys in `(TRACE_GT, CORRECT)`; `STAGE_KEYS == tuple(f"Alt+{i}" for i in range(1, 7))`; tooltip examples; the live-shortcut documentation test over `(CorrectEditor, CORRECT)` and `(GtTracingInterface, TRACE_GT)`; replace the skeleton sheet test by "the Correct sheet lists both layers' keys, gestures and 1 / 2 / X, and no key is both a QShortcut and a key row"; the dialog-order test uses `sc.CORRECT`.
  - `tests/test_integrator_cross_module.py`: the skeleton-sheet test → scope `CORRECT` (tool keys V/E/P/C, S, Ctrl+S, `[`, `]`, Delete, Backspace documented); the mask-enhance-button test → `CorrectEditor` and `GtTracingInterface`.
  - `tests/test_foundation_runtime_panels_feedback.py`: values `[0, 1, 2, 3]`; key round trip for `display`, `correct`, `visualization`, `gt_tracing`; `coerce(1) is Panel.CORRECT`; rejects `"mask_tracing"`, `"skeleton_correction"`, `4`.
  - `tests/test_metrics_bar_smoke.py`: `right_panel.count() == 4`.
  - `tests/test_viz_embed_manager.py`: derive `_PANELS` from `Panel`.
  - `tests/test_import_smoke.py`: remove the deleted modules; add `app.imaging.pyramid`, `app.gui.canvas_layers` (+ `.photo`, `.raster`, `.skeleton`), `app.gui.editor_canvas`, `app.gui.paint_canvas`, `app.gui.editor_chrome`, `app.gui.editor_prompts`, `app.gui.raster_editor_base`, `app.editing.journal`, and every `app.gui.correct.*` module.
  - `tests/test_gt_tracing_interface.py`: delete `test_mask_mode_unchanged` and the `MaskTracingInterface` import (P7b's "saving never writes `gt/`" replaces it).
  - `tests/test_split_*.py`, **only if present in this folder** (the peer plan's Task 0 guards): update the frozen sets deliberately (drop `MaskTracingInterface`, `SkeletonCorrectionInterface`, `RasterEditorBase` of the old module and the MainWindow attributes `mask_tracing_interface`, `skeleton_correction_interface`, `_mask_tracing_signals_connected`, `toggle_mask_tracing`, `toggle_skeleton_correction`; add `correct_editor` / `toggle_correct`); record what changed in the notes.
- [ ] **Step 5: gates.**
  - Full suite green (P8a's guard tests included).
  - `ruff check` and `ruff format --check` on every changed file.
  - Grep gate (Git Bash): `grep -rnE --include='*.py' "mask_tracing_interface|skeleton_correction_interface|\bMASK_TRACING\b|\bSKELETON_CORRECTION\b|\bCORRECT_MASK\b|\bCORRECT_SKELETON\b|toggle_mask_tracing|toggle_skeleton_correction|[\"']mask_tracing[\"']|[\"']skeleton_correction[\"']" app tests` returns only: the rejection cases that name the legacy keys (`tests/test_foundation_runtime_panels_feedback.py`, `tests/test_right_panel_contract.py`), docstrings that name the removed keys (the AST guard ignores docstrings too; `Panel`'s may say which keys are gone), the frozen-set edits of `tests/test_split_*.py` if present, the skipped identity test in `tests/test_paint_canvas.py`, and comments in untouched docstring-only tests; every hit is listed in the notes (the lead's cleanup removes the identity test). `SKELETON_CORRECTIONS_DIRNAME` does not match (word boundaries).
  - `scratch/contracts-tests/bench/bench_mainwindow.py` adapted → `scratch/P8b-swap/bench_mainwindow.py`: stage enter Correct ≤ 25 ms, image switch in Correct ≤ 35 ms, leave ≤ 5 ms.
  - Harness: if `notes/P8a-docs-guards.md` says "harness adapted", run `--scenario stage --stage Correct --stage "Trace GT" --stage Library --size 1280x800 --size 1600x900` and the full walk: 0 exceptions, 0 overlay overlaps, 0 ambiguous shortcuts; otherwise say so in the notes (the lead runs it at integration).

**Acceptance:**
- `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/P8b-swap/pt --continue-on-collection-errors` (full suite) → all pass; report the count (the 8 known skips remain).
- `./venv/Scripts/python.exe -c "import app.gui.main_window; import sys; assert 'skimage' not in sys.modules"` → no error.
- `./venv/Scripts/python.exe -m ruff check .` and `./venv/Scripts/python.exe -m ruff format --check .` → clean for changed files.
- The grep gate returns only the listed exceptions; the MainWindow bench within budget; the harness report (or the reason it did not run) in the notes.

**Perf budget:** stage enter ≤ 25 ms, image switch ≤ 35 ms (strict), leave ≤ 5 ms; `import app.gui.main_window` loads no `skimage`; the prewarm never runs on the GUI thread.

**UX details:** the Correct stage button tooltip "Correct — Fix the mask (area) and the skeleton (length) of an image on one canvas. (Alt+4)"; the welcome screen shows six chips; the F1 sheet shows Global, Library, Trace GT, Correct.

---

## Integration checklist (lead, after W2)

- [ ] Every W2 agent has stopped; review P7a, P7b, P8a, P8b against the spec; one full-suite run of the combined tree; commit in order: P7a (`feat(correct): controls and Layers panel`), P7b (`feat(correct): the Correct editor`), the swap = P8b + P8a's `tests/test_gui_public_names.py` and `tests/test_right_panel_contract.py` (`refactor(shell): one Correct stage replaces the two editors`), then P8a's docs (`docs: the Correct stage`). Optional per-commit worktree verification, explained in one line in `STATUS.md`.
- [ ] Full suite: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=.claude/handoff/unified-editor-2026-09-29/scratch/lead/pt --continue-on-collection-errors` → all pass (baseline 1650 passed / 8 skipped; expected about +250 new and −90 deleted tests; record the numbers and the duration, which must stay under about 75 s). Then with `$env:SPROUTS_PERF_TESTS='1'`: `tests/test_correct_perf.py` (strict budgets, first open after the prewarm) and `tests/test_skeleton_roi.py` (the 900- and 2,800-edit runs).
- [ ] `./venv/Scripts/python.exe -m ruff check .` and `./venv/Scripts/python.exe -m ruff format --check .` clean.
- [ ] **Pixel goldens, real dataset:** the committed tests, and `scratch/lead/check_goldens_unified.py` — a copy of `scratch/contracts-tests/goldens/capture_goldens.py` with its two drivers replaced by `CorrectEditor` drivers (C18 op semantics) — run `--check ../../contracts-tests/goldens/goldens.json` → `MATCHES GOLDEN` for all six cases; record the output in `STATUS.md`.
- [ ] **Correct bench:** `scratch/lead/bench_correct.py` (adapt `scratch/architecture/bench_unified_open.py` and `scratch/contracts-tests/bench/bench_editors.py`): open 640 / 2048, layer switch, stroke segment, polyline commit, save both; 5-run medians against spec section 14.
- [ ] **GL rows** (real desktop, under the GL lock): "hover frame, both layers visible" (photo + mask tiles + skeleton + ring, `FullViewportUpdate`) for the real 640 image (≤ 4 ms) and the dense 2048 stress case (≤ 16 ms); stroke frame at 6000x4000 (≤ 2 ms); Library zoom frame (≤ 1.5 ms).
- [ ] UI harness walk (P8a's copy): full walk + `--scenario stage` at **1280x800 and 1600x900**, screenshots of Correct with the mask active, the skeleton active (draft visible), both layers hidden (H), zoom 25 / 100 / 400 / 1600 %, Trace GT and Library at 400 % and 25 %; 0 exceptions, 0 overlay overlaps, 0 ambiguous shortcuts, longest GUI freeze per step ≤ 30 ms.
- [ ] Perf table versus the scout baselines (spec section 14): open 640 / 2048, layer switch, stage enter, image switch, mask stroke segment, GL stroke frame at 6000, photo frame GL / raster, Library zoom frame GL, polyline commit 640 / 2048, eraser-drag frame 2048, skeleton hover frame at zoom 2 (2048 dense), endpoint repaint (191 markers), Enhance preview, `-X importtime` of `app.gui.main_window`, first open after the prewarm (5-run medians; a regression beyond max(3 %, noise) on an unchanged path fails).
- [ ] Manual GL smoke on the user's machine (the offscreen suite cannot see GL): `./venv/Scripts/python.exe main.py`, open the dataset, zoom 400 % and 1600 % in Correct, Trace GT and Library (smooth photo, crisp mask pixels, skeleton as a line, soft Library overlay), paint a long stroke on a large image, switch layers mid-stroke, undo across layers, PgDn, close with unsaved edits.
- [ ] Post-swap cleanup (things only the deleted editors used): `ZoomCluster.ZOOM_IN_STEP` / `ZOOM_OUT_STEP`; the now permanently skipped identity test in `tests/test_paint_canvas.py` (its oracle is gone; the goldens keep the guarantee); any `argb_array_to_pixmap` leftovers; stale docstrings naming "Correct Mask" / "Correct Skeleton" in app code; run the test suite again.
- [ ] `graphify update .`; check the degree of `CorrectEditor` (it should not approach `MainWindow`'s 72); refresh the god-node list in `CLAUDE.md`.
- [ ] Docs as P8a Step 1 (verify), and the vault / memory: file memory `unified-correct-editor.md` + `MEMORY.md` pointer (stage, layers, decisions, where things live), mem0 `add_coding_memory(project="root-mask-skeletons")` (start Qdrant and Ollama first if the server is down), a vault log via `/save`.
- [ ] Tell the user: the owner questions (spec section 19) and the peer-plan order (spec section 17: Track B in its own worktree or after W2; Task 0's API freeze and Track A after the swap).

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A consumer runs against a half-built provider | Completion-driven start: a consumer starts only when its providers have finished; the two in-wave overlaps (P1b on P1a's Step 1, P4a / P5a on P6's Step 0) land working bodies within minutes; no `NotImplementedError` placeholders. |
| A half-built module breaks `import app.gui.main_window` for every agent | P3's base-switch rule; every agent checks the import after touching a module `MainWindow` imports. |
| Parallel pytest sessions collide | No cache provider, a per-agent `--basetemp`, `--continue-on-collection-errors`; own test files while working, the full suite only at the final gate. |
| A gesture in progress is stranded (canvas pixels that are not in the plane reach a later save) | C2 mid-gesture rule (commit first, rest of the drag ignored, undo refused), session gestures-first (C15), `end_gesture()` (C8); tests at layer (P4b, P5b), session (P6) and editor level (P7b). |
| Prompt saves leave the Library badge and the metrics stale | Per-layer save reports (`on_saved`, C15) mapped to signals on every path (C17); P6, P7b and P8b tree-click tests. |
| `QImage` canvas bytes differ from the old `QPixmap` canvas | P2's per-op identity test against the live old mixin; P4b / P5b / P7b goldens; the real-dataset gate (lead). |
| Journal and layer histories disagree after eviction | Journal entries are tokens; `undo()` / `redo()` return False only on an empty stack (C2); P6 eviction tests with bounded fakes; P7b's 60-action test on the real layers. |
| ROI skeletonize diverges | Always-on equality test (about 50 edits per case), opt-in 900- and 2,800-edit runs; full-frame fallback; the goldens. |
| Dense skeletons stall frames or commits | Lazy per-tile vector extraction with a 4 ms budget per paint; edits never extract (C6); P1b and P5b dense-2048 gates. |
| The Library overlay changes how users judge generation quality | `vectors=False`, bilinear at s ≥ 1, max-pool below (C6); P3's 2 px test. |
| GT behaviour drifts in the port | Every GT test and the moved behaviour tests stay green; attribute inventory; override guard; `SHORTCUT_SCOPE = TRACE_GT`; the base switch only after the base passes. |
| The first Correct open pays the lazy scikit-image import | The prewarm after the window shows (C19); P7b's opt-in first-open gate. |
| Letter shortcuts fire while a popover control has focus | Same as today's mask editor; the popover holds no text inputs; noted for follow-up if users complain. |
| Offscreen tests cannot see GL | Config-level GL tests (samples, update mode, smoothing, render hints), GL benches on the real desktop (serialized by the lock), the manual GL smoke. |
| MSAA costs on weak GPUs | `ROOT_VIEWER_GL_SAMPLES=0`; measured within noise on the RTX 5070 Ti only. |
| Raster-viewport vector budgets measured on one machine | Budgets are class constants; the item falls back to the overlay above them. |
| The swap commit is large | Docs, harness and guards split off to P8a; P8b derives values (`len(STAGES)`, `Panel` members) instead of pinning numbers; the grep gate; the harness. |
| Commits that were never tested alone | Commits only after each wave stops, in dependency order, after one combined full-suite run; optional per-commit worktree verification. |
| Peer session runs its plan concurrently | Spec section 17: Track B only in its own worktree or after W2; Task 0's API freeze and Track A after the swap; C / D / Task Z dropped; P8b updates any `tests/test_split_*.py` present at the swap. |

## Self-review

- **Spec coverage:** stage and layout (spec 3) → P7a, P7b, P8b; layer model (4) → P4b, P5b, P7a, P7b; tools and gestures (5) → P4b, P5b, P6 (router), P7b; keys (6) → W0, P6, P7b, P8b; undo (7) → P6 (+ C4 in P4a, P5a), P4b, P5b, P7b; dirty / save / prompt / save reports (8) → P4a, P5a, P6, P7b, P8b; opening / stamps / navigation (9) → P2 (decode), P4a, P5a, P6, P7b, P8b; rendering and zoom (10) → P1a, P1b, P3, P7b; GT and Library (11) → P3; architecture (12) → all, ratchet in P6, prewarm in P5a + P8b; compatibility (13) → P8b, P8a; budgets (14) → every package's perf step and the lead's integration table; testing (15) → every package; docs (16) → P8a and the lead; peer plan (17) → spec + P8b's conditional row + lead.
- **Exclusive ownership:** every file appears once per wave in the ownership table; files that change owner between waves are listed with their owner per wave; `notes/packages_v2.json` carries the same lists and a check script confirms no path is owned twice in one wave.
- **Contracts:** every cross-package call names a C-section signature; P7a's widget API is C20; test data and helpers are C18.
- **Placeholders:** none; values that only exist after a run (perf numbers, test counts) are recorded in each package's notes.
- **Sizes:** each package is about 1,500 changed lines or less except P6 (about 1,600) and P7b (about 1,700), whose scopes the lead's decisions fix; deletions are not counted.
