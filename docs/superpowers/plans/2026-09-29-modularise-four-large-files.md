# Modularise MainWindow and the Length Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status (2026-09-30): executed; CQ-07 is closed.** Track B landed in `2e34f45`
> (`dash_app.py` 845 -> 178 lines; its tests are `test_viz_dash_split_golden`,
> `test_viz_dash_split_surface` and `test_viz_dash_length_views` instead of `test_split_*`).
> Track A landed in `2bc8486` (`main_window.py` 1556 -> 699 lines) with one deviation from the
> design below: parts without state of their own are modules of functions attached in the
> `MainWindow` class body under their old names (`navigator`, `project_loader`, `editors`,
> `stages`, `job_buttons`, `lifecycle`, `body`, `_common`), not controllers behind delegators,
> so no hot path gains a call and slots stay window methods; `MetricsController`
> (`window/metrics.py`) is the one controller. Its tests are `test_shell_window_split_surface`,
> `test_shell_window_parts` and `test_shell_window_split_imports`. Building the Correct and
> Trace GT pages after the first paint was measured but not done (an owner decision,
> `docs/KNOWN_ISSUES.md` #7). Re-baselined on `3469885` (2026-09-29): the unified Correct
> editor deleted two of the four files (`mask_tracing_interface.py`,
> `skeleton_correction_interface.py`), so Tracks C and D were removed; the file name is kept
> because the Correct spec links it.

**Goal:** Split `app/gui/main_window.py` (1142 lines) and `app/visualization/dash_app.py` (834) into single-responsibility modules with **zero behaviour change and no measurable latency or startup regression**. Closes review item CQ-07 (`docs/KNOWN_ISSUES.md` #5).

**Architecture:** Pure move-refactor (`caveman:safe-refactor`). `MainWindow` is an orchestrator, so it is decomposed by **composition** into small controller objects that receive the window through a narrow `Host` Protocol. `DashApp` is a single object whose parts share `self`, so it is decomposed into **module-level functions** that take what they read as parameters. Every original import path and every attribute name that code or tests use keeps working (thin delegators / re-exports). Hot paths (arrow-key browse, Dash callbacks) do not gain an extra call hop.

**Tech Stack:** Python 3.10, PyQt6, Dash/Plotly, pytest, ruff (line-length 100). Run everything with `./venv/Scripts/python.exe`.

**Spec:** None — this is a refactor. The behaviour contract is (a) the existing suite (`tests/`, 112 files) and (b) the golden/guard tests added in Task 0.

## Global Constraints

- Use `./venv/Scripts/python.exe` for every run; tests with `$env:QT_QPA_PLATFORM = 'offscreen'`.
- Line length 100, py310, ruff only (`ruff check --fix .` then `ruff format .`) — no black/isort.
- `app/config.py` must not import torch; new modules must not add top-level imports of `torch`, `dash`, `pandas`, `plotly`, `cv2` to any module that does not already import them (Task 0 records the exact baseline set).
- **Public import paths are frozen:** `app.gui.main_window.MainWindow`, `app.visualization.dash_app.DashApp`. Moved classes and constants are re-exported from the old module.
- **Public and underscore attribute names frozen** as captured in Task 0 (`tests/test_split_api_surface.py`). Deleting a delegator is out of scope for this plan.
- Dash component ids, callback Outputs/Inputs/States and figure `customdata` are a contract (see `dash_app.py` docstring): golden test in Task 0.
- No feature changes, no renames, no behaviour "improvements". Moves are verbatim except for replacing `self.` with the explicit host/argument.
- Nothing here can change a CSV value or an output pixel; `CHANGELOG.md` gets a "Refactor" line, and the "Scientific output changes" section is left untouched.
- Saved mask and skeleton pixels stay identical (`tests/data/correct_goldens.json`); nothing here touches the editors.
- Commits: conventional (`refactor(...)`), one per task, no attribution lines. Lead commits; subagents never commit.

## Review Focus

1. **Signal-thread affinity.** `_MeasurementSignals.ready` is emitted from a `QThreadPool` worker and connected to a slot that moves from a `QMainWindow` method to a plain-Python controller method. The slot must still run on the GUI thread (Task A2 Step 4 test).
2. **Construction order.** `ui_panels`/`shell_chrome` wire widgets to `window.<method>` while `init_ui()` runs; controllers must exist before `init_ui()` and must resolve `toasts`, `feedback`, `right_panel`, `file_list` lazily (they are created inside `init_ui`). A controller that captures them in `__init__` raises `AttributeError` at startup (Task A1 + A6).
3. **Close-event semantics.** Cancelling the Correct or Trace GT prompt, cancelling the "job is running" prompt, or a job that outlives both waits must still ignore/allow the close exactly as before (Task A5 tests).
4. **Theme/import-order side effects.** `dash_app.py` calls `theme.use("sprouts")` at import; new sibling modules must not read theme constants at import time before that call (Task B1 golden layout test).

---

## File Structure

### Create
| Path | Responsibility | Approx. lines |
|---|---|---|
| `app/gui/window/__init__.py` | Empty (no eager imports → zero import cost) | 0 |
| `app/gui/window/_common.py` | `USER_ROLE`, `same_path()`, `released()` | 30 |
| `app/gui/window/editor_switcher.py` | `EditorSwitcher`: editor registry, right-panel switching, `toggle_correct` / `toggle_gt_tracing` / `_toggle_editor`, mask-saved/cleared/B-key observers, window-modified marker | 120 |
| `app/gui/window/library_navigator.py` | `LibraryNavigator`: tree click/arrow/PgUp/PgDn navigation, debounce timer, `on_image_selected` routing | 220 |
| `app/gui/window/project_loader.py` | `ProjectLoader`: open-folder flow, loading progress/finished/empty/failed, recent projects | 130 |
| `app/gui/window/metrics_controller.py` | `MetricsController`: metrics bar refresh, off-thread CSV prewarm, `_MeasurementSignals` | 120 |
| `app/gui/window/lifecycle.py` | `WindowLifecycle`: `closeEvent` body, stop-jobs-for-close, persist window state | 100 |
| `app/gui/window/job_buttons.py` | `JobButtons`: disable Generate/Measure buttons while a job runs | 40 |
| `app/gui/window/body.py` | `build_body()`, `build_shell()`, `sync_library_for_panel()` (presentation, like `ui_panels`) | 130 |
| `app/visualization/dash_length_layout.py` | Length dashboard layout builders (pure functions) | 260 |
| `app/visualization/dash_length_views.py` | Length view-model: view rendering, controls summary, export table, legend patch (pure functions) | 250 |
| `app/visualization/dash_length_callbacks.py` | `register_callbacks(dash_app)`: every `@app.callback` closure | 260 |
| `tests/test_split_api_surface.py` | Frozen name surface of `MainWindow` and `DashApp` (Task 0) | 60 |
| `tests/test_split_import_budget.py` | Heavy-import baseline guard (Task 0) | 40 |
| `tests/test_split_dash_golden.py` | Layout + callback-map golden (Task 0) | 60 |
| `tests/test_window_controllers.py` | Fake-host unit tests of the new controllers | 200 |

### Modify (shrinks)
`app/gui/main_window.py` → ~700 lines (about 620 lines move out; roughly 50 one-line delegators stay, and deleting them is out of scope) · `app/visualization/dash_app.py` → ~130 · `CLAUDE.md` (architecture map) · `CHANGELOG.md` · `docs/KNOWN_ISSUES.md` (#5, CQ-07).

### Not touched
`shell_chrome.py` (748, next candidate, out of scope), `dash_app_area.py` (own duplicated `_controls_summary`/`_export_table` — de-dup is a *separate* follow-up, not part of a behaviour-preserving move).

## Design Principles → Concrete Rules

| Principle | Rule in this plan |
|---|---|
| **SRP** | One reason to change per module: load flow / navigation / metrics / close / editor switching each get their own class. |
| **OCP** | Adding a controller needs no edit to the others: each depends only on its own `Host` Protocol. |
| **ISP / DIP** | Each controller declares a `Host` `typing.Protocol` listing exactly the window attributes it reads. Controllers are unit-tested with a fake host, no `QMainWindow`. Protocols are `TYPE_CHECKING`-only → zero runtime cost. |
| **Composition over inheritance** | `MainWindow` (an orchestrator) composes controllers. `DashApp` delegates to module-level functions. No new mixins. |
| **Encapsulation** | Controllers own their state (`_shown_leaf_name`, `_measurements`, …); window exposes it through properties where existing tests read it. |
| **DRY (limited)** | `same_path`/`released`/`USER_ROLE` live once in `window/_common.py`. No other de-duplication in this plan. |

## Latency Guarantees (the "no slowdown" contract)

1. **Hot paths stay put.** These never gain a hop: `LibraryNavigator.on_tree_current_changed` + debounce timer (the arrow-key browse path) and all Dash callbacks (closures registered once; identical body). The editors (`app/gui/correct/`, `raster_editor_base.py`) are not touched.
2. **Import graph.** `app/gui/window/__init__.py` is empty. New modules import only what the moved code already imported. Heavy modules (`dash`, `pandas`, `torch`, `cv2`, handlers) keep their existing laziness (`from app.handlers... import` inside `MainWindow.__init__`; `from .dash_app import DashApp` inside `root_length_visulization.py`). Guard: `tests/test_split_import_budget.py`.
3. **No new indirection per event.** Delegators exist for external callers only. Inside the package, controllers call each other's methods directly; signals that fire per user action connect to the controller's bound method, not through the window delegator.
4. **No new timers / signals / threads / QObjects.** Same `QTimer(self)` (parent = window), same `QThreadPool.globalInstance()` worker, same `_MeasurementSignals` QObject.
5. **Measured gates** (median of 5 runs, before vs after, same machine, offscreen where the script allows): existing scripts in `.claude/handoff/ui-perf-2026-09-28/scratchpad/architecture/` — `bench_startup.py`, `bench_tree_46k.py`, `bench_viz_init.py` — plus `python -X importtime` for `app.gui.main_window`. **Pass = every metric within +3 % of baseline (or within run-to-run noise measured in Task 0).** Any regression → revert that task's commit and rethink; do not accept "small".

## Sequencing Update (2026-09-29)

The Correct swap has landed on `feat/unified-correct-editor` (spec
`docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`): it deleted
`mask_tracing_interface.py`, `skeleton_correction_interface.py` and both editor graphics views,
and replaced the three editor toggles in `main_window.py` with `toggle_correct`,
`toggle_gt_tracing` and a shared `_toggle_editor`.

- **Track A (MainWindow controllers):** unblocked. Its method inventory below is re-derived
  against `3469885`; re-check it with `grep -n "    def " app/gui/main_window.py` before Task A1.
- **Track B (dash_app):** independent, can run any time.
- **Tracks C and D (editor splits):** removed, see below.
- Methods not listed in Tasks A1–A6 stay on `MainWindow`, among them `_prewarm_skeletonize`,
  `_generated_skeleton_for`, `set_opengl_viewports_enabled`, `_install_global_shortcuts`,
  `_go_to_stage`, `_current_stage`, `on_outputs_changed`, `export_gt_dataset` and the
  visualization toggles.

## Execution Order and Parallelism

```
Task 0  baseline + guards (lead, sequential, one commit)
   ├── Track A  main_window   A1 → A2 → A3 → A4 → A5 → A6   (files: app/gui/window/*, main_window.py, tests/test_window_controllers.py)
   └── Track B  dash_app      B1 → B2 → B3                    (files: app/visualization/dash_length_*.py, dash_app.py)
Task Z  final gates + docs (lead, sequential)
```
Tracks touch **disjoint files**, so two subagents can run in parallel (project preference: lead commits, no unexplained worktree folders). Each subagent runs only its own track's tests plus the named guards; the lead runs the full suite after each merge of a track's commit. Persist progress in `.claude/handoff/split-2026-09-29/` (per `durable-workflow-handoff`).

---

## Task 0: Baseline, golden tests and guards

**Files:**
- Create: `tests/test_split_api_surface.py`, `tests/test_split_import_budget.py`, `tests/test_split_dash_golden.py`
- Create (scratch, not committed): `.claude/handoff/split-2026-09-29/baseline.md`

**Interfaces:**
- Produces: frozen name sets and golden JSON that every later task must keep green.

- [ ] **Step 1: Confirm a clean, green starting point**

```powershell
git status --short          # must be clean
$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest -x -q
```
Expected: all pass. If not, stop: the refactor needs a green base.

- [ ] **Step 2: Record performance baselines** (5 runs each, keep medians in `baseline.md`)

```powershell
1..5 | % { ./venv/Scripts/python.exe -X importtime -c "import app.gui.main_window" 2> ".claude/handoff/split-2026-09-29/importtime_$_.txt" }
1..5 | % { ./venv/Scripts/python.exe .claude/handoff/ui-perf-2026-09-28/scratchpad/architecture/bench_startup.py }
1..5 | % { ./venv/Scripts/python.exe .claude/handoff/ui-perf-2026-09-28/scratchpad/architecture/bench_tree_46k.py }
1..5 | % { ./venv/Scripts/python.exe .claude/handoff/ui-perf-2026-09-28/scratchpad/architecture/bench_viz_init.py }
```
Write the median and the spread (max−min) of each metric into `baseline.md`. The spread is the noise floor; the gate is `max(3 %, spread)`.

- [ ] **Step 3: Capture the API surface**

```powershell
./venv/Scripts/python.exe - <<'PY'
import importlib, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication, QMainWindow
app = QApplication([])
targets = {
    "app.gui.main_window:MainWindow": QMainWindow,
    "app.visualization.dash_app:DashApp": object,
}
for spec, base in targets.items():
    mod, cls = spec.split(":")
    obj = getattr(importlib.import_module(mod), cls)
    names = sorted(n for n in dir(obj) if n not in dir(base) and not n.startswith("__"))
    print(spec, names, "\n")
PY
```
Paste each list as a literal into `SURFACE` below. Also capture the **instance** attributes of a constructed `MainWindow` (`sorted(vars(window))` minus Qt internals) into `MAIN_WINDOW_INSTANCE_ATTRS`.

- [ ] **Step 4: Write `tests/test_split_api_surface.py`**

```python
"""Frozen name surface of the modules that were split (behaviour-preserving refactor).

Fails when a class loses a public or underscore attribute that other code or tests use.
Update deliberately, never to make a refactor pass.
"""

import importlib
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SURFACE = {
    # "module:Class": {names captured in Task 0 Step 3}
    "app.gui.main_window:MainWindow": set(),  # PASTE
    "app.visualization.dash_app:DashApp": set(),  # PASTE
}
MAIN_WINDOW_INSTANCE_ATTRS: set[str] = set()  # PASTE


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("spec", sorted(SURFACE))
def test_class_surface_is_unchanged(spec, app):
    module, cls = spec.split(":")
    obj = getattr(importlib.import_module(module), cls)
    missing = sorted(n for n in SURFACE[spec] if not hasattr(obj, n))
    assert not missing, f"{spec} lost: {missing}"


def test_main_window_instance_attributes_are_unchanged(app, tmp_path):
    from PyQt6.QtCore import QSettings

    from app.gui.main_window import MainWindow

    window = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    try:
        missing = sorted(n for n in MAIN_WINDOW_INSTANCE_ATTRS if not hasattr(window, n))
        assert not missing, f"MainWindow lost instance attributes: {missing}"
    finally:
        window.close()
        window.deleteLater()
```
Fill the `PASTE` sets from Step 3 (they must be non-empty; add `assert SURFACE[spec]` inside the first test to enforce it).

- [ ] **Step 5: Write `tests/test_split_import_budget.py`**

```python
"""Importing the window module must not pull heavy libraries it did not before the split."""

import json
import subprocess
import sys

# PASTE from Step 5b: the heavy modules that `import app.gui.main_window` loads TODAY.
HEAVY_BASELINE: set[str] = set()
HEAVY = ("torch", "dash", "pandas", "plotly", "cv2", "flask")


def _loaded(module: str) -> set[str]:
    code = (
        "import json, sys, os; os.environ['QT_QPA_PLATFORM']='offscreen';"
        f"import {module};"
        f"print(json.dumps(sorted(m for m in {HEAVY!r} if m in sys.modules)))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def test_main_window_import_loads_no_new_heavy_module():
    assert _loaded("app.gui.main_window") <= HEAVY_BASELINE


def test_window_package_import_is_free():
    assert _loaded("app.gui.window") == set()
```
- [ ] **Step 5b:** run the one-liner in `_loaded` manually against the **unsplit** code, paste the result into `HEAVY_BASELINE`. (`app.gui.window` does not exist yet; mark `test_window_package_import_is_free` with `pytest.mark.skipif` on `importlib.util.find_spec("app.gui.window") is None` and remove the skip in Task A1.)

- [ ] **Step 6: Write `tests/test_split_dash_golden.py`**

```python
"""Layout and callback wiring of the length dashboard are a contract: freeze them."""

import hashlib
import json

import dash

from tests.viz_dash_helpers import build_length_app

GOLDEN_LAYOUT_SHA = ""  # PASTE from Step 6b
GOLDEN_CALLBACKS: tuple[str, ...] = ()  # PASTE from Step 6b


def _layout_sha(app) -> str:
    payload = json.dumps(app.app.layout.to_plotly_json(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def test_length_layout_is_byte_identical(tmp_path):
    assert _layout_sha(build_length_app(tmp_path)) == GOLDEN_LAYOUT_SHA


def test_length_callback_outputs_are_unchanged(tmp_path):
    build_length_app(tmp_path)
    keys = tuple(sorted(dash.callback_map))
    assert keys == GOLDEN_CALLBACKS
```
- [ ] **Step 6b:** compute both values on unsplit code (temporary print in a scratch script using `build_length_app(tmp)`; note `to_plotly_json` of `dcc.Store` etc. is deterministic — run twice to confirm the sha is stable, otherwise drop volatile keys like generated ids). If `dash.callback_map` accumulates across apps in one process, filter to keys containing `main-graph`/known ids, mirroring `tests/test_viz_dash_shell.py:97`.

- [ ] **Step 7: Verify all three new tests pass on the unsplit code, run ruff, commit**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_split_api_surface.py tests/test_split_import_budget.py tests/test_split_dash_golden.py -q
./venv/Scripts/python.exe -m ruff check --fix tests/test_split_*.py; ./venv/Scripts/python.exe -m ruff format tests/test_split_*.py
git add tests/test_split_api_surface.py tests/test_split_import_budget.py tests/test_split_dash_golden.py
git commit -m "test: freeze API surface, import budget and dash layout before modularising"
```

---

# Track A — `main_window.py` (1142 → ~700 lines)

Common conventions for A1–A6:
- Controllers are plain Python classes (not `QObject`) except `_MeasurementSignals`, which stays a `QObject`.
- Each takes the window: `def __init__(self, host: "XxxHost")`. `XxxHost` is a `typing.Protocol` under `if TYPE_CHECKING:` listing only what that controller reads.
- Resolve `toasts`, `feedback`, `right_panel`, `file_list`, `statusline`, `metrics_bar`, `loading_overlay`, `welcome`, `app_stack` **at call time** via `self._host.<name>` / `getattr(self._host, name, None)` exactly where the original used `getattr(self, ...)`. Never cache them in `__init__`.
- `MainWindow` keeps a one-line delegator for **every** moved method (same name, same signature, same docstring first line). Internal signal connections in new code target the controller's bound method directly.
- Move method bodies **verbatim**; only `self.<window attr>` → `self._host.<window attr>` and `self.<own state>` unchanged.
- Verification after every task: `pytest tests/test_split_*.py tests/test_library_main_window.py tests/test_metrics_bar_smoke.py tests/test_integration_main_window_wiring.py tests/test_shell_chrome_*.py tests/test_shell_info_*.py -q`, then ruff.

## Task A1: `window/_common.py` + package skeleton

**Files:** Create `app/gui/window/__init__.py` (empty), `app/gui/window/_common.py`; Modify `app/gui/main_window.py` (import from `_common`; keep `_same_path`, `_released` names as aliases at module level because tests/other modules may import them).

**Interfaces — Produces:**
```python
USER_ROLE = Qt.ItemDataRole.UserRole
def same_path(a: str | None, b: str | None) -> bool
def released(editor) -> bool
```

- [ ] **Step 1:** Move `_same_path`, `_released` and `USER_ROLE` verbatim into `_common.py` (public names, docstrings kept). In `main_window.py`: `from .window._common import USER_ROLE, released as _released, same_path as _same_path`.
- [ ] **Step 2:** Remove the `skipif` in `test_window_package_import_is_free` (Task 0 Step 5b).
- [ ] **Step 3:** Run the verification set; ruff; commit `refactor(window): extract shared helpers of MainWindow`.

## Task A2: `MetricsController` (lowest coupling, proves the pattern)

**Files:** Create `app/gui/window/metrics_controller.py`; Modify `main_window.py`; Test `tests/test_window_controllers.py`.

**Interfaces — Consumes:** `MeasurementIndex`, `Measurement` from `app.data_processing.measurement_index`; `_common.USER_ROLE`.
**Produces:**
```python
class MetricsController:
    def __init__(self, host): ...
    index: MeasurementIndex | None            # was MainWindow._measurements
    loading: bool                             # was MainWindow._measurements_loading
    def set_folder(self, folder: str | None) -> None      # index = MeasurementIndex(folder) if folder else None
    def current_image_name(self) -> str | None            # was _current_image_name
    def refresh(self, stored_name=None) -> None           # was _refresh_metrics_bar
    def refresh_if_current(self, image_name) -> None      # was _refresh_metrics_if_current
    def prewarm(self) -> None                              # was _prewarm_measurements
    def on_ready(self, index) -> None                      # was _on_measurements_ready
    def image_has_mask(self, name) -> bool / image_has_skeleton(self, name) -> bool
class _MeasurementSignals(QObject): ready = pyqtSignal(object)   # moved here, re-exported from main_window
```
`MetricsHost` Protocol: `image_manager`, `display_controller`, `file_list`, `metrics_bar` (optional).

- [ ] **Step 1: Write the failing fake-host tests**

```python
class FakeIndex:
    def __init__(self, needs_load=False):
        self._needs_load, self.prewarmed = needs_load, 0
    def lookup(self, name, load=False): ...
    def needs_load(self): return self._needs_load
    def is_outdated(self, name): return False
    def sources(self): return {}
    def prewarm(self): self.prewarmed += 1

def test_refresh_without_metrics_bar_is_a_noop():
    controller = MetricsController(SimpleNamespace(image_manager=..., file_list=None))
    controller.refresh("a")           # no metrics_bar attribute: must not raise

def test_prewarm_runs_once_at_a_time(qapp):
    ...  # set loading=True, call prewarm(), assert FakeIndex.prewarmed == 0

def test_on_ready_for_stale_index_does_not_refresh():
    ...  # controller.index = FakeIndex(); controller.on_ready(other_index); metrics_bar.set_metrics not called
```
(Write the full bodies with a `SimpleNamespace` host and a recording `metrics_bar`; each test asserts one behaviour copied from the original method's branches.)

- [ ] **Step 2:** Run → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3:** Move the methods listed above verbatim. In `MainWindow.__init__` replace the four state lines and the `_measurement_signals.ready.connect(...)` with `self.metrics = MetricsController(self)` (the controller owns `_MeasurementSignals` and makes the connection itself). Add window delegators `_refresh_metrics_bar`, `_prewarm_measurements`, `_on_measurements_ready`, `_current_image_name`, `_image_has_mask`, `_image_has_skeleton`, `_refresh_metrics_if_current`, `mask_exists` (calls `image_manager` — leave as is) and properties `_measurements` (getter/setter → `self.metrics.index`), `_measurements_loading`, `_measurement_signals`.
- [ ] **Step 4: Thread-affinity test (Review Focus 1)**

```python
def test_ready_signal_from_pool_thread_runs_on_gui_thread(qapp):
    seen = {}
    controller = MetricsController(host_with_metrics_bar(record=seen))
    controller.index = FakeIndex()
    def work():
        controller._signals.ready.emit(controller.index)
    QThreadPool.globalInstance().start(work)
    QThreadPool.globalInstance().waitForDone(2000)
    QApplication.processEvents()
    assert seen["thread"] is QThread.currentThread()
```
If it fails (slot ran on the pool thread), parent the receiver: keep the connection as a bound method of a `QObject` receiver (a private `QObject` owned by the controller) instead of the plain object.
- [ ] **Step 5:** Verification set + `tests/test_window_controllers.py`; ruff; commit `refactor(window): extract MetricsController from MainWindow`.

## Task A3: `ProjectLoader`

**Files:** Create `app/gui/window/project_loader.py`; Modify `main_window.py`.

**Interfaces — Produces:**
```python
class ProjectLoader:
    last_loaded_dir: str | None
    def load_images(self) -> None
    def open_path(self, dir_name) -> None
    def update_loading_progress(self, value) -> None
    def on_loading_finished(self, images, fake_images, masks, has_fake_real_pairs) -> None
    def on_loading_empty(self, folder) -> None
    def on_loading_failed(self, folder, message) -> None
    def _end_failed_load(self, message, kind) -> None
    def record_recent_project(self, count) -> None
    def refresh_recents(self) -> None
```
Consumes `MetricsController.set_folder/refresh/prewarm`, `EditorSwitcher.editors()` — **not yet extracted**, so in this task use `self._host._editors()` (the window method); A5 rewires to the switcher. `QFileDialog.getExistingDirectory` and `os.path.isdir` stay referenced as `QFileDialog`/`os` inside this module (tests that monkeypatch `app.gui.main_window.QFileDialog` must be re-pointed — grep `QFileDialog` in `tests/` first; if any patch targets `app.gui.main_window.QFileDialog`, keep an alias import in `main_window.py` **and** note the target moves to `app.gui.window.project_loader.QFileDialog`; update those tests in the same commit with an explicit list in the commit body).

- [ ] **Step 1:** grep tests for patched names that live in the moved code: `Grep "main_window\.(QFileDialog|QMessageBox|QApplication|MeasurementIndex)"` in `tests/` (Task 0 found none via the `patch(` pattern; re-check `monkeypatch.setattr(mw_module, ...)` style too).
- [ ] **Step 2:** Move verbatim; `MainWindow.on_loading_finished` etc. become delegators (image_manager calls them on the window — keep).
- [ ] **Step 3:** Verification set; add `test_open_path_missing_folder_notifies_and_keeps_state` and `test_open_path_blocked_by_editor_that_refuses` to `test_window_controllers.py` using a fake host (`notify` recorder, `_editors()` returning a stub whose `maybe_save_changes()` returns `False`; assert `app_stack.setCurrentIndex` never called).
- [ ] **Step 4:** ruff; commit `refactor(window): extract ProjectLoader from MainWindow`.

## Task A4: `LibraryNavigator`

**Files:** Create `app/gui/window/library_navigator.py`; Modify `main_window.py`.

**Interfaces — Produces:**
```python
class LibraryNavigator:
    shown_leaf_name: str | None                     # was MainWindow._shown_leaf_name
    tree_load_timer: QTimer                         # parent = window, same 40 ms single shot
    def image_leaves(self, *, visible_only=False) -> list
    def leaf_for_image(self, image_path, leaves=None)
    def select_tree_image(self, image_path) -> None
    def navigate_image(self, delta) -> None
    def on_tree_current_changed(self, current, _previous=None) -> None
    def load_current_tree_item(self) -> None
    def install_paging_shortcuts(self) -> None      # sets window._paging_shortcuts (tests read it)
    def page_image(self, delta) -> None
    def on_tree_item_clicked(self, item, column) -> None
    def on_image_selected(self, item) -> None
    def kept_other_image(self, editor, image_path) -> bool
```
`TREE_LOAD_DELAY_MS` moves with it and is re-exported from `main_window`.

- [ ] **Step 1:** Move verbatim. The timer is created in the controller ctor as `QTimer(host)` so its parent is still the window (same lifetime as before).
- [ ] **Step 2:** Delegators on `MainWindow` for all names above with their original underscore names (`_image_leaves`, `_leaf_for_image`, `_select_tree_image`, `_load_current_tree_item`, `_page_image`, `_kept_other_image`, `_install_image_paging_shortcuts`) plus properties `_shown_leaf_name`, `_tree_load_timer`.
- [ ] **Step 3:** Tests: debounce (`on_tree_current_changed` twice within 40 ms → `on_image_selected` called once after the timer fires; drive with `QTest.qWait`), `navigate_image` stops at the last leaf (fake `library_index.step` returning `None`), `kept_other_image` re-selects the kept image.
- [ ] **Step 3b — perf gate:** `bench_tree_46k.py` ×5 vs baseline (arrow-key browse path).
- [ ] **Step 4:** Verification set; ruff; commit `refactor(window): extract LibraryNavigator from MainWindow`.

## Task A5: `EditorSwitcher`, `WindowLifecycle`, `JobButtons`

**Files:** Create `editor_switcher.py`, `lifecycle.py`, `job_buttons.py` in `app/gui/window/`; Modify `main_window.py`.

**Interfaces — Produces:**
```python
class EditorSwitcher:
    def editors(self) -> dict                       # {Panel.CORRECT: correct_editor, Panel.GT_TRACING: gt_tracing_interface}
    def switch_right_panel(self, panel) -> bool
    def toggle_correct(self) -> None
    def toggle_gt_tracing(self) -> None
    def toggle_editor(self, panel) -> None          # was MainWindow._toggle_editor
    def on_b_key_status_changed(self, is_pressed) -> None
    def on_mask_saved(self, image_path) -> None
    def on_mask_cleared(self, image_path) -> None
    def sync_window_modified(self, *_args) -> None

class WindowLifecycle:
    def close_event(self, event) -> bool            # True = accept (caller then super().closeEvent), False = ignored
    def stop_jobs_for_close(self) -> bool
    def save_window_state(self) -> None

class JobButtons:
    def on_busy_changed(self, busy: bool) -> None   # was MainWindow._on_jobs_busy_changed
```
`JOB_BUTTONS`, `_CLOSE_WAIT_MS`, `_CLOSE_EXTRA_WAIT_MS` move with their users and are re-exported from `main_window`. `MainWindow.closeEvent` becomes:
```python
def closeEvent(self, event: QCloseEvent) -> None:
    if not self._lifecycle.close_event(event):
        return
    super().closeEvent(event)
```
where `close_event` calls `event.ignore()` on refusal, and performs `QApplication.processEvents()` before returning True (identical order to the original).

- [ ] **Step 1:** Move verbatim. Rewire A3's `self._host._editors()` to the switcher.
- [ ] **Step 2: Close-event tests (Review Focus 3)** in `test_window_controllers.py` with a fake host: (a) editor returns `False` → `event.ignore()` called, `jobs.shutdown` not called; (b) busy job + `QMessageBox.question` patched to `No` → ignored; (c) `stop_all` returns `False` twice → `hide()` called once, close still accepted, warning logged; (d) viz cleanup exception is swallowed and logged, state still saved.
- [ ] **Step 3:** `MainWindow` delegators for `_editors`, `switch_right_panel`, `toggle_correct`, `toggle_gt_tracing`, `_toggle_editor`, `on_b_key_status_changed`, `on_mask_saved`, `on_mask_cleared`, `_sync_window_modified`, `_stop_jobs_for_close`, `_save_window_state`, `_on_jobs_busy_changed`.
- [ ] **Step 4:** Verification set including `tests/test_shell_chrome_topbar_editor_refusal.py`; ruff; commit `refactor(window): extract EditorSwitcher, WindowLifecycle and JobButtons`.

## Task A6: `window/body.py`, composition root, cleanup

**Files:** Create `app/gui/window/body.py`; Modify `main_window.py`.

**Interfaces — Produces:**
```python
def build_body(window) -> QWidget          # was MainWindow._build_body (keeps the right_panel order: `Panel` members, dashboards after)
def build_shell(window) -> QWidget         # was MainWindow._build_shell
def sync_library_for_panel(window, *_args) -> None   # was MainWindow._sync_library_for_panel
def current_widget_is_viz(window) -> bool
```
- [ ] **Step 1:** Move verbatim; `MainWindow._build_body/_build_shell/_sync_library_for_panel/_current_widget_is_viz` become delegators; `right_panel.currentChanged.connect(...)` now targets `functools.partial`-free bound delegator (`window._sync_library_for_panel`) — keep the delegator here because the connection is made before all attributes exist.
- [ ] **Step 2: Composition root (Review Focus 2).** In `MainWindow.__init__` construct controllers in this order, **before** `self.init_ui()`: `metrics` → `editors_ctl` (`EditorSwitcher`) → `navigator` → `loader` → `lifecycle` → `job_buttons`. The existing `self.jobs.busy_changed.connect(self._on_jobs_busy_changed)` moves after `job_buttons` exists. Add `test_window_builds_headless_with_every_controller` asserting `MainWindow()` constructs and `hasattr(window, name)` for each controller attribute.
- [ ] **Step 3:** Confirm `main_window.py` ≤ 750 lines: `(Get-Content app/gui/main_window.py | Measure-Object -Line).Lines`.
- [ ] **Step 4: Full gates:** full suite; `tests/test_split_*`; perf gates (`importtime`, `bench_startup`, `bench_tree_46k`) vs baseline; ruff check + format.
- [ ] **Step 5:** Commit `refactor(main-window): compose MainWindow from single-responsibility controllers`.

---

# Track B — `dash_app.py` (834 → ~130 lines)

## Task B1: `dash_length_layout.py`

**Files:** Create `app/visualization/dash_length_layout.py`; Modify `dash_app.py`.

**Interfaces — Produces (pure functions, no `self`):**
```python
def controls(tube_options: list, date_options: list) -> list          # was DashApp._controls
def tube_selection_card(tube_options: list) -> dbc.Card
def viz_options_card() -> dbc.Card
def faceted_options_card(tube_options: list, date_options: list) -> dbc.Card
def faceted_info(tubes: list, dates: list) -> html.Div
def availability_view(availability: dict, tubes: list, dates: list)
def thumbnail(thumbnail_server, tube: int, position: int, date_str: str) -> html.Div
SHOWN, HIDDEN, IMAGES_SHOWN, GRAPH_STYLE, FACETED_GRAPH_STYLE   # style constants (move here; re-export from dash_app)
def card_style(visible: bool) -> dict
```
The only `self.` uses in `_controls`/`_tube_selection_card`/`_faceted_options_card` are `self._tube_options` (3×) and `self._date_options` (1×) → parameters. `availability_view` receives `availability = self.get_tube_date_availability()` computed by the caller (same call, same moment).

- [ ] **Step 1:** Move verbatim; import only `dash_bootstrap_components as dbc`, `from dash import dcc, html`, `pandas` (only if the moved code uses it), `.dash_shell` names it used, `.faceted_profile.MAX_TUBES`, `count_label`.
- [ ] **Step 2:** `DashApp` methods become one-line delegators (`_controls`, `_tube_selection_card`, `_viz_options_card`, `_faceted_options_card`, `_faceted_info`, `_availability_view`, `_thumbnail`).
- [ ] **Step 3:** Golden test (`tests/test_split_dash_golden.py`) + `tests/test_viz_dash_*.py -q`. This is where Review Focus 5 is caught (layout sha differs if a theme value is read too early).
- [ ] **Step 4:** ruff; commit `refactor(viz): extract length dashboard layout builders`.

## Task B2: `dash_length_views.py`

**Files:** Create `app/visualization/dash_length_views.py`; Modify `dash_app.py`.

**Interfaces — Produces (pure functions; figure builders are passed in so the module never imports `DashVisualizations`):**
```python
def lines_tube(tubes: Sequence, selected_tube) -> int | None
def controls_summary(tubes, view, tube, selected_tubes, faceted_tubes, faceted_dates) -> str
def render_view(charts, view, selected_tube, selected_tubes, viz_options, legend_visible, tubes, dates) -> tuple
def faceted_outputs(charts, tubes, dates) -> tuple
def faceted_styles(tubes) -> tuple
def legend_patch(view: str, visible: bool) -> tuple
def export_table(cache, tubes_all, view, tube, selected_tubes, viz_options, faceted_tubes, faceted_dates) -> dash_export.ExportTable | None
def hover_date(point: dict) -> str | None
```
where `charts` is the `DashApp` instance (it *is* the `DashVisualizations` mixin: `show_growth_over_time`, `show_growth_lines`, `create_stacked_bar_chart`, `create_faceted_depth_profile`). Duck-typed parameter, documented as a `Protocol` (`LengthCharts`) in the module.

- [ ] **Step 1:** Move verbatim; `_DATE_TOKEN` regex moves with `hover_date`.
- [ ] **Step 2:** `DashApp` keeps delegators (`_lines_tube`, `_controls_summary`, `_render_view`, `_faceted_outputs`, `_faceted_styles`, `_legend_patch`, `_export_table`, `_hover_date`) so `tests/test_viz_dash_*` and any `shell.register_export(build_table=self._export_table)` keep working unchanged.
- [ ] **Step 3:** New pure tests (no Dash server): `lines_tube` falls back to the first tube and to `None` when empty; `controls_summary` for each view; `faceted_outputs` returns the error figure when `> MAX_TUBES`; `hover_date` prefers `customdata` over `text`.
- [ ] **Step 4:** Golden + `tests/test_viz_dash_callbacks.py tests/test_viz_dash_export.py -q`; ruff; commit `refactor(viz): extract length dashboard view-model`.

## Task B3: `dash_length_callbacks.py`

**Files:** Create `app/visualization/dash_length_callbacks.py`; Modify `dash_app.py`.

**Interfaces — Produces:** `def register_callbacks(dash_app: "DashApp") -> None` containing the six `@app.callback` closures, the `clientside_callback`, and the two `shell.register_*` calls **in the original order** (registration order affects `dash.callback_map` insertion order and initial-load trigger order). `DashApp._setup_callbacks` becomes `register_callbacks(self)`.

- [ ] **Step 1:** Move verbatim, replacing `self.` with `dash_app.`. Closures capture `dash_app` (same lifetime as `self` before).
- [ ] **Step 2:** `DashApp` now holds only: `__init__`, `get_encoded_image`, `_setup_layout`, delegators, `run_server`. Confirm ≤ 150 lines.
- [ ] **Step 3:** Full viz tests + golden + `bench_viz_init.py` ×5 vs baseline (import of `dash_app` on the worker thread is the cold-start cost; must not grow).
- [ ] **Step 4:** ruff; commit `refactor(viz): move length dashboard callbacks into their own module`.

---

# Tracks C and D — removed (superseded by the Correct swap)

`mask_tracing_interface.py` and `skeleton_correction_interface.py` were deleted by the
unified Correct editor (`feat/unified-correct-editor`). `RasterEditorBase` already lives in
`app/gui/raster_editor_base.py` (Trace GT's base, CQ-11), and the Correct editor is built from
small modules under `app/gui/correct/` with a 450-line ratchet test. Nothing is left to split.

---

## Task Z: Final gates, docs, memory

- [ ] **Step 1:** Full suite: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest -q` — all green, count matches Task 0's plus the new tests.
- [ ] **Step 2:** Re-run every baseline from Task 0 Step 2; append "after" medians to `baseline.md`; each within `max(3 %, noise)`. Any miss → revert the responsible commit (`git revert <sha>`), not "accept".
- [ ] **Step 3:** `git diff --stat main..HEAD -- app` sanity: total non-test line count within ±10 % of before (a move, not a rewrite); `ruff check .` and `ruff format --check .` clean.
- [ ] **Step 4: Manual smoke** (offscreen tests cannot see OpenGL/WebEngine): launch `./venv/Scripts/python.exe main.py`; open a folder; switch every stage; draw+undo on both layers in Correct and in Trace GT; open both dashboards; close with a dirty editor (expect prompt) and with a running generate job (expect prompt).
- [ ] **Step 5:** Docs: update `CLAUDE.md` architecture map (`app/gui/window/`, `dash_length_*`; the god-node numbers will change → re-run `graphify update .` then refresh the list); mark CQ-07 resolved in `docs/KNOWN_ISSUES.md` #5; add a `CHANGELOG.md` "Refactor" entry (no "Scientific output changes").
- [ ] **Step 6:** Persist to both memory layers: file memory `modularised-large-files` (module map, the Latency Guarantees, delegator policy) + `MEMORY.md` pointer; mem0 `add_coding_memory(project="root-mask-skeletons")` (mem0 MCP failed to connect this session — start Qdrant/Ollama first, or write it next session).
- [ ] **Step 7:** Commit `docs: record the modular layout of MainWindow and the length dashboard`.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Hidden external use of an underscore method or attribute | Frozen API-surface test (Task 0); delegators kept; deletions out of scope |
| Tests monkeypatch a name where it moved | Task A3 Step 1 grep; alias + explicit list in commit body |
| Startup import cost creeps up | Empty `window/__init__.py`; import-budget test; `-X importtime` gate |
| Slot thread affinity changes when a slot leaves a `QObject` | Task A2 Step 4 test; fallback = private `QObject` receiver |
| Dash callback registration order changes | Golden test + verbatim order in B3 |
| Parallel tracks conflict | Disjoint file sets (Track A: `app/gui/window/*`, `main_window.py`; Track B: `app/visualization/dash_*`) |

## Self-Review

- **Coverage:** two files → tracks A and B (C and D superseded by the Correct swap); OOP principles → table + per-task Interfaces; latency → dedicated section, per-track measured gates, permanent import-budget test.
- **Placeholders:** the only `PASTE` markers are values that can only be captured from the unsplit code in Task 0 (name sets, sha, heavy-import baseline); each has an explicit capture step. Method moves cite exact method names (line numbers deliberately avoided; they shift).
- **Type consistency:** controller attribute names used later (`metrics.index`, `navigator.shown_leaf_name`, `EditorSwitcher.editors()`) match their definitions in A2–A5; window delegators keep the original underscore names.
- **Review Focus coverage:** items 1→A2 S4, 2→A6 S2, 3→A5 S2, 4→B1 S3.
