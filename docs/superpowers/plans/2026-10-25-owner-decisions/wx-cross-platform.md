# Wave X — Linux and macOS Implementation Plan (O21)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Read `00-master.md` (this folder) first.

**Goal:** SPROUTS starts and works on Linux (CPU and CUDA) and macOS (Apple silicon, CPU),
declares no Windows-only package, and CI proves it on ubuntu, windows and macos.

**Architecture:** One Qt-free module, `app/qt_environment.py`, decides every start-up flag
by platform (D3D11 / ANGLE only on Windows). Small, separately reviewed fixes follow:
dependencies, trackpad pinch on the shared `CanvasViewMixin`, case-insensitive path identity
for APFS, font fallbacks, OS-neutral tests and a macOS CI job. Fixes in files other lanes own
are scheduled inside those lanes (section "Changes to the master").

**Tech Stack:** Python 3.10, PyQt6 6.11 / PyQt6-WebEngine, PyTorch 2.8.0 (CUDA 12.8 on
Windows / Linux, CPU on macOS), pytest (offscreen), ruff, GitHub Actions.

**Spec:** `docs/superpowers/plans/2026-10-25-owner-decisions/00-master.md` (O21 row, sections
2, 5, 6) + `.claude/handoff/owner-decisions-2026-10-25/research/o21-cross-platform.md`
(the "O21 report": findings and tasks T1-T13; verified on WSL2 Ubuntu 24.04, macOS read-only).

Owner decision O21, verbatim: "Yes, the app should support work on Linux and macOS. We can
ignore GPU operation on macOS, but must keep it workable on Linux, therefore we must not use
any Windows-specific packages like pywin32."

## Global Constraints

- See `00-master.md` section 2 (venv python only, offscreen tests, ruff on own files only,
  no git by implementers, scope = the task's file list, Sci rules, goldens, 450-line cap).
- No Windows-only package in `requirements*.txt` or `pyproject.toml` (no `pywin32`, no
  `sys_platform == "win32"` marker on any runtime dependency).
- A Windows-only API (`ctypes.WinDLL`, `winreg`, `msvcrt`, D3D11 / ANGLE flags) is used only
  behind `sys.platform == "win32"`; `app/inference/device_probe.py` already complies.
- Linux and macOS take Qt's own RHI default (OpenGL on Linux, Metal on macOS): no code sets
  `QSG_RHI_BACKEND` or a `--use-angle` flag off Windows.
- macOS GPU is out of scope: never select MPS; `cuda_usable()` False means CPU.
- Linux CUDA must keep working: nothing in this wave touches `app/inference/runtime.py`.
- Supported platforms (O21 report 1.4): Windows 10/11; Linux x86_64 with glibc 2.34 or newer
  (Ubuntu 22.04+, Debian 12+); macOS 13+ on Apple silicon only (no Intel Mac wheels).
- Platform-dependent code takes the platform as a parameter (default `sys.platform`) or reads
  `sys.platform` at call time, so tests monkeypatch it instead of skipping.
- We have no Mac. A macOS behaviour that cannot be tested offscreen defaults to the path every
  OS already tests, and is listed for the owner's first Mac run (WX-T6 step 8).

## Review Focus

1. **Linux start** (X11 / Wayland, normal user and root in a container): no D3D11 RHI forced,
   dashboards not black, Chromium sandbox off only as root. Pinned in WX-T1
   (`test_linux_takes_qt_defaults`, `test_linux_root_disables_the_chromium_sandbox`,
   `test_importing_main_sets_d3d11_only_on_windows`) and run for real by WX-T6's ubuntu job.
2. **macOS start**: no D3D11 / ANGLE, 96-dpi fonts, raster canvases by default. Pinned in WX-T1
   (`test_darwin_takes_metal_and_96_dpi_fonts`) and WX-T8
   (`test_macos_defaults_to_the_raster_viewport`).
3. **A case-variant path on APFS** (`/Data/T1.png` vs `/data/t1.png`): one file, one recents
   entry, read-ahead hit. Pinned in WX-T4 (`test_darwin_keys_ignore_case_and_unicode_form`,
   `test_recents_dedupe_case_variants_on_darwin`); remaining sites in W5-T5.
4. **A trackpad pinch** (macOS, Wayland): zooms about the fingers, stops at
   `zoom_out_limit` and `MAX_ZOOM`, ignored mid-stroke, smart-zoom toggles fit / 100 %. Pinned
   in WX-T3 (`tests/test_canvas_pinch_zoom.py`).
5. **CI on three OSes**: suite green on ubuntu, windows and macos without checkpoints and
   without Windows fonts. Pinned in WX-T6 (the workflow + the five fixed tests).

---

## Task cut and placement

| ID | Task | Lane | After | Impl | Rev | Sci |
|---|---|---|---|---|---|---|
| WX-T1 | Start-up environment per OS (`app/qt_environment.py`, `main.py`) | L-xplat | — | opus / high | opus / medium | no |
| WX-T2 | Dependencies and metadata: drop `pywin32`, `opencv-python-headless`, OS classifiers | L-xplat | WX-T1 | sonnet / medium | sonnet / low | no |
| WX-T3 | Trackpad pinch zoom on `CanvasViewMixin` | L-xplat | WX-T2 | opus / high | opus / medium | no |
| WX-T4 | Path identity helper, temp names in bytes, macOS device note, platform guards | L-xplat | WX-T3 | sonnet / high | opus / medium | no |
| WX-T5 | Font fallback chains (tokens + QSS) | L-xplat | WX-T4 | sonnet / medium | sonnet / low | no |
| WX-T6 | CI on macOS, suite OS-neutral, README Linux / macOS install | L-xplat | WX-T5 | sonnet / high | opus / medium | no |
| WX-T7 | Platform key text (Cmd / Option), macOS stage keys, Pinch row in F1 | L-correct | W2-T8a (so after WG-T5) | sonnet / high | opus / medium | no |
| WX-T8 | macOS: raster canvas viewport by default | L-misc | W2-T9 | sonnet / medium | sonnet / medium | no |
| WX-T9 | Deterministic CSV / Library tie order | L-measure | W2-T11 | sonnet / high | opus / high | **yes** (row order) |
| WX-T10 | Backslash separators in `image_stem` | L-stems | W1-T5 | sonnet / low | sonnet / low | no |
| WX-T11 | Optional: bundle IBM Plex TTF (SIL OFL) | L-xplat | WX-T6 | sonnet / medium | sonnet / low | no |

Not built (O21 report items, with the reason): `usable_cpus()` (thread pools are already
capped at 6 / 8; oversubscription on a cgroup-limited Linux host slows, never breaks);
`newline="\n"` for GT split files (changes Windows bytes; nobody asked for byte-identical
splits); a Linux / macOS executable (O21 asks for running from source); `docs/BUILD_EXECUTABLE.md`
deletion and its `CLAUDE.md` reference (W5-T7, O36); CI branch filter `feat/**`, `fix/**`
(W5-T6).

---

### Task WX-T1: Start-up environment per OS

Blocker: today `main.py:22` sets `QSG_RHI_BACKEND=d3d11` and `main.py:32-37` adds
`--use-angle=d3d11` on every OS; on Linux Qt then fails "Failed to create QRhi" and the
dashboards render black (O21 report 2.1, run).

**Impl:** opus / high. **Rev:** opus / medium. **Lane:** L-xplat. **After:** —

**Files:**
- Create: `app/qt_environment.py` (Qt-free, stdlib only)
- Modify: `main.py:11-37` (comment block + flags → one call), `main.py:159-176` (`main()`:
  style, desktop file name, log line)
- Test: `tests/test_startup_environment.py` (new)

**Interfaces:**
- Produces:
  - `configure_qt_environment(environ: MutableMapping[str, str], platform: str = sys.platform, euid: int | None = None) -> dict[str, str]`
    — sets with `setdefault` semantics (a user's value wins) and returns what it set.
  - `qt_style_for(platform: str = sys.platform) -> str | None` — `None` on `win32` (keep the
    native style the theme was tuned on), `"Fusion"` elsewhere.
  - `DESKTOP_FILE_NAME = "sprouts"` (Wayland icon matching).

Values decided here: win32 → `QSG_RHI_BACKEND=d3d11` and `--use-angle=d3d11` appended to
`QTWEBENGINE_CHROMIUM_FLAGS` (once); linux → nothing, plus `QTWEBENGINE_DISABLE_SANDBOX=1`
only when `euid == 0`; darwin → only `QT_FONT_DPI=96` (Qt maps 1 pt to 1 px on macOS; the
theme uses `pt`; this keeps text the designed size — unverified without a Mac, listed for
the owner's Mac check; fallback if Cocoa ignores it: convert `pt` to `px` at QSS load).

- [ ] **Step 1: Write the failing tests** in `tests/test_startup_environment.py`

```python
from app import qt_environment as qe

def test_windows_sets_d3d11_and_angle_once():
    env = {"QTWEBENGINE_CHROMIUM_FLAGS": "--foo"}
    qe.configure_qt_environment(env, "win32")
    qe.configure_qt_environment(env, "win32")
    assert env["QSG_RHI_BACKEND"] == "d3d11"
    assert env["QTWEBENGINE_CHROMIUM_FLAGS"] == "--foo --use-angle=d3d11"

def test_windows_keeps_a_users_backend():
    env = {"QSG_RHI_BACKEND": "opengl"}
    qe.configure_qt_environment(env, "win32")
    assert env["QSG_RHI_BACKEND"] == "opengl"

def test_linux_takes_qt_defaults():
    env = {}
    assert qe.configure_qt_environment(env, "linux", euid=1000) == {}
    assert env == {}

def test_linux_root_disables_the_chromium_sandbox():
    env = {}
    qe.configure_qt_environment(env, "linux", euid=0)
    assert env == {"QTWEBENGINE_DISABLE_SANDBOX": "1"}

def test_darwin_takes_metal_and_96_dpi_fonts():
    env = {}
    qe.configure_qt_environment(env, "darwin", euid=0)
    assert env == {"QT_FONT_DPI": "96"}

def test_style_is_native_on_windows_and_fusion_elsewhere():
    assert qe.qt_style_for("win32") is None
    assert qe.qt_style_for("linux") == qe.qt_style_for("darwin") == "Fusion"

def test_the_module_is_qt_free():
    src = Path(qe.__file__).read_text(encoding="utf-8")
    assert "PyQt6" not in src and "torch" not in src

def test_importing_main_sets_d3d11_only_on_windows(tmp_path):
    # subprocess: sys.path = repo root, SPROUTS_LOG_DIR = tmp_path, env without
    # QSG_RHI_BACKEND / QTWEBENGINE_CHROMIUM_FLAGS; run "import main, os;
    # print(os.environ.get('QSG_RHI_BACKEND'))" (pattern: tests/test_foundation_runtime_startup.py _run)
    assert out == ("d3d11" if sys.platform == "win32" else "None")
```

- [ ] **Step 2: Run** `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_startup_environment.py -v`
  Expected: FAIL, `ModuleNotFoundError: No module named 'app.qt_environment'`.
- [ ] **Step 3: Implement** `app/qt_environment.py` with the interfaces above. Move the
  `main.py:14-31` comment into the module docstring, rewritten to say Windows (D3D11 /
  ANGLE), Linux (OpenGL RHI, Qt default), macOS (Metal, Qt default); drop the stale Plotly
  WebGL remark (no `scattergl` in `app/`). In `main.py` replace lines 14-37 with
  `from app.qt_environment import configure_qt_environment` +
  `configure_qt_environment(os.environ, euid=os.geteuid() if hasattr(os, "geteuid") else None)`
  (must stay before `app.logging_config` and any PyQt6 import; `app/__init__.py` must stay
  Qt-free, check). In `main()` after the QApplication exists: `style = qt_style_for()`;
  `if style: app.setStyle(style)` before `apply_stylesheet(app)`;
  `app.setDesktopFileName(DESKTOP_FILE_NAME)`. Log line `main.py:171-173`: also log the
  dict `configure_qt_environment` returned (module-level variable `_QT_ENV_SET`).
- [ ] **Step 4: Run** the Step 2 command. Expected: 8 passed.
- [ ] **Step 5: Existing tests stay green:** `tests/test_foundation_runtime_startup.py`,
  `tests/test_integrator_cross_module.py`, `tests/test_overlay_layout.py`,
  `tests/test_layering_contract.py`.
- [ ] **Step 6: Ruff** `ruff check --fix` + `ruff format` on `app/qt_environment.py main.py tests/test_startup_environment.py`.
- [ ] **Step 7: Manual (lead, Linux):** in WSL2 Ubuntu 24.04 (throw-away copy outside the repo,
  the O21 report's recipe), `python main.py`, open Visualize: the dashboard is not black.
- [ ] **Step 8: Commit** (lead): `fix(startup): set D3D11 / ANGLE flags on Windows only; Qt defaults on Linux and macOS (O21)`

---

### Task WX-T2: Dependencies and metadata

**Impl:** sonnet / medium. **Rev:** sonnet / low. **Lane:** L-xplat. **After:** WX-T1.

**Files:**
- Modify: `requirements.txt:29` (opencv), `requirements.txt:43-44` (delete heading + pywin32),
  `pyproject.toml:27-31` (classifiers), `pyproject.toml:49` (opencv), `pyproject.toml:56`
  (delete pywin32), `codemeta.json:25`, `CLAUDE.md:51-54` (CI-gotchas sentence)
- Test: `tests/test_fair_packaging.py` (add one test after line 100)

Finding on `opencv-python-headless` (asked by the lead): nothing in `app/`, `main.py` or
`scripts/` calls `cv2.imshow`, `waitKey`, `namedWindow` or `destroyAllWindows` (grep,
2026-10-25). The non-headless wheel on Linux sets `QT_QPA_PLATFORM_PLUGIN_PATH` /
`QT_QPA_FONTDIR` to its bundled Qt5 plugins on `import cv2`; Qt 6 still started on xcb and
Wayland (O21 report 1.3, run), so it is not a blocker, but it leaks into the WebEngine child
environment. Switch to `opencv-python-headless==4.13.0.92` (same version, same codecs; the
goldens record `"opencv": "4.13.0"`). The two wheels both install `cv2`: the dev venv swap
(`pip uninstall -y opencv-python` then `pip install opencv-python-headless==4.13.0.92`) is a
lead action that needs the owner's approval (master section 2: no `pip install` without it).

**Interfaces:** Produces the dependency set W5-T1 / W5-T3 edit next (they touch
`pyproject.toml` / `codemeta.json` after this task).

- [ ] **Step 1: Failing test** in `tests/test_fair_packaging.py`:

```python
def test_no_windows_only_runtime_dependency():
    declared = [Requirement(d) for d in _raw_pyproject()["project"]["dependencies"]]
    pinned = list(_requirements(ROOT / "requirements.txt"))
    for req in declared + pinned:
        assert canonicalize_name(req.name) != "pywin32"
        assert "win32" not in str(req.marker or ""), req
        assert canonicalize_name(req.name) != "opencv-python", "use opencv-python-headless"
```
- [ ] **Step 2: Run** `pytest tests/test_fair_packaging.py -v`. Expected: FAIL on `pywin32`.
- [ ] **Step 3: Edit** the files: delete `requirements.txt:43-44`; `opencv-python-headless==4.13.0.92`;
  in `pyproject.toml` delete line 56, `opencv-python-headless~=4.13`, classifiers: drop
  `"Environment :: Win32 (MS Windows)"`, add `"Operating System :: POSIX :: Linux"` and
  `"Operating System :: MacOS"` beside the Windows one; `codemeta.json:25`
  `"operatingSystem": ["Windows", "Linux", "macOS"]`; `CLAUDE.md` CI-gotchas sentence:
  "`pywin32` was removed (O21: no Windows-only package)".
- [ ] **Step 4: Run** `pytest tests/test_fair_packaging.py tests/test_fair_build.py -v`. Expected: all pass.
- [ ] **Step 5 (lead, after approval):** swap the venv package, then run the pixel goldens
  `pytest tests/test_correct_goldens.py tests/test_correct_goldens_data.py tests/test_measure_core.py -v`: identical.
- [ ] **Step 6: Commit:** `build(deps): drop pywin32, use opencv-python-headless, list Linux and macOS (O21)`

---

### Task WX-T3: Trackpad pinch zoom

macOS and Linux (Wayland pointer-gestures, X11 XInput 2.4) send `QNativeGestureEvent`, which
nothing handles (`docs/KNOWN_ISSUES.md:206-207`); Windows precision touchpads send Ctrl+wheel
and keep that path (no double zoom).

**Impl:** opus / high. **Rev:** opus / medium. **Lane:** L-xplat. **After:** WX-T2.

**Files:**
- Modify: `app/gui/canvas_view.py:63-74` (add `QEvent` to the QtCore import), new methods
  after `_wheel_zoom` (`:542-553`)
- Create: `tests/test_canvas_pinch_zoom.py` (reuse the `app` / `make_view` / `_View` pattern
  of `tests/test_canvas_zoom_limits.py:39-66`)
- Modify: `docs/KNOWN_ISSUES.md:206-207` (pinch no longer open), `CHANGELOG.md` `[Unreleased]` Added

**Interfaces:**
- Produces on `CanvasViewMixin`: `viewportEvent(self, event: QEvent) -> bool`,
  `_native_gesture(self, event: QNativeGestureEvent) -> bool`,
  `_pinch_zoom(self, value: float, anchor: QPointF) -> None`.

Behaviour: `ZoomNativeGesture` with `value()` v → `set_zoom(target_zoom * max(1 + v, 0.05), position())`;
when v < 0 the target is floored at `min(zoom_out_limit, current)` as `_wheel_zoom` does;
continuous (no glide, `set_zoom` stops a glide). `SmartZoomNativeGesture` → `fit_to_view()`
when at 100 % (±1e-3), else `set_zoom(1.0, position())`. Both are ignored (but accepted)
while `self.drawing`. Other gesture types fall through to `super().viewportEvent`.
`EditorCanvasView` does not consult its sink for pinch.

- [ ] **Step 1: Failing tests.** Helper builds the event as PyQt6 6.11 accepts it:
  `QNativeGestureEvent(kind, QPointingDevice.primaryPointingDevice(), 2, pos, pos, QPointF(vp.mapToGlobal(pos.toPoint())), value, QPointF(0, 0))`,
  sent with `QApplication.sendEvent(view.viewport(), ev)`. Tests:
  - `test_pinch_multiplies_the_zoom_and_keeps_the_anchor`: `view.set_zoom(0.5)`, pinch +0.25 at
    (150, 120) → `view.zoom_factor == pytest.approx(0.625)`; the scene point under (150, 120)
    before == after (`view.mapToScene`, abs 0.5).
  - `test_pinch_out_stops_at_the_zoom_out_limit`: 60 × pinch −0.10 → `zoom_factor == pytest.approx(view.zoom_out_limit)`.
  - `test_pinch_in_stops_at_max_zoom`: 80 × +0.20 → `zoom_factor == pytest.approx(view.MAX_ZOOM)`.
  - `test_pinch_is_ignored_while_drawing`: `view.drawing = True`; pinch +0.25 → zoom unchanged.
  - `test_smart_zoom_toggles_fit_and_actual_pixels`: from fit → 1.0; again → fit zoom.
  - `test_pinch_ends_a_glide`: `set_zoom_animation(120)`, `zoom_step(+1)`, pinch → `not view.zoom_animating()`.
  - `test_pinch_reaches_the_editor_canvas`: same +0.25 check on an `EditorCanvasView` built as
    in its existing tests (grep `EditorCanvasView(` in `tests/`).
- [ ] **Step 2: Run** `pytest tests/test_canvas_pinch_zoom.py -v`. Expected: FAIL (zoom unchanged).
- [ ] **Step 3: Implement** the three methods (O21 report section 6 holds a prototype run
  against the real mixin).
- [ ] **Step 4: Run** Step 2. Expected: 7 passed.
- [ ] **Step 5: Stay green:** `pytest tests/test_canvas_zoom_*.py tests/test_canvas_gl.py -v`.
- [ ] **Step 6: Ruff** on the three code/test files.
- [ ] **Step 7: Commit:** `feat(canvas): trackpad pinch and smart zoom through native gestures (O21)`

---

### Task WX-T4: Path identity, temp names in bytes, macOS device note, platform guards

**Impl:** sonnet / high. **Rev:** opus / medium. **Lane:** L-xplat. **After:** WX-T3.

**Files:**
- Modify: `app/io_utils.py` (imports `:29-32`; new functions after `_temp_path`; `_temp_path`
  `:95-109`), `app/gui/display_read_ahead.py:60-61` (`_key`), `app/gui/recent_projects.py:57-58`
  (`_same_path`), `app/inference/device_probe.py:361-362` (the no-driver note)
- Test: `tests/test_path_identity.py` (new), `tests/test_foundation_core_io_utils.py` (add),
  `tests/test_warmup_device_probe.py` (add near `:228`), `tests/test_platform_guards.py` (new)

`display_read_ahead.py` and `recent_projects.py` are in no lane's list (master section 6),
so this task takes them. Every other normcase site is **W5-T5's work** (it runs after Wave G
deletes `raster_editor_base.py` / `gt_tracing_interface.py` and already owns "one
`file_stamp` / `same_file` in `app/io_utils.py`"): `app/gui/correct/editor.py:56-57`,
`correct/paging.py:59-60`, `correct/read_ahead.py:63`, `correct/skeleton_document.py:361-362`,
`app/gui/window/_common.py:15`, `app/gui/visualization_manager.py:213`,
`app/enhancement.py:288`, `app/gui/export_dataset_dialog.py:72,88`,
`app/gui/widgets/icons.py:50-51`, and — data safety, first in W5-T5 — the GT export
protected-folder check `app/gt/export.py:150-152` (on APFS an out dir typed `.../Mask` passes
today and could write into `mask/`; W5-T5 adds the test
`test_export_refuses_a_case_variant_of_a_protected_folder_on_darwin` with
`monkeypatch.setattr(sys, "platform", "darwin")`).

**Interfaces:**
- Produces in `app/io_utils.py` (W5-T5 builds on these names):
  - `path_key(path: str | os.PathLike[str], platform: str | None = None) -> str` —
    win32: `os.path.normcase(os.path.abspath(p))`; darwin:
    `unicodedata.normalize("NFC", os.path.abspath(p)).casefold()`; else `os.path.abspath(p)`.
    `platform=None` reads `sys.platform` at call time.
  - `same_file(a, b) -> bool` — `path_key(a) == path_key(b)`.

- [ ] **Step 1: Failing tests.**
  - `tests/test_path_identity.py`:
    `test_darwin_keys_ignore_case_and_unicode_form` — `path_key("/Data/T1.png", "darwin") == path_key("/data/t1.png", "darwin")`
    and `path_key("/x/Ae\u0301.png", "darwin") == path_key("/x/a\u00e9.png", "darwin")`;
    `test_linux_keys_keep_case` — `path_key("/Data/T1.png", "linux") != path_key("/data/t1.png", "linux")`;
    `test_windows_keys_use_normcase` — `path_key("C:/A/B.png", "win32") == path_key("c:\\a\\b.png", "win32")` (skip off Windows: `abspath` of a drive path is OS-specific);
    `test_same_file_follows_sys_platform` (`monkeypatch.setattr(sys, "platform", "darwin")`);
    `test_recents_dedupe_case_variants_on_darwin` — darwin patched, `RecentProjectsStore` add
    `/Proj/A` then `/proj/a` → one entry (build the store as `tests/` already does, grep
    `RecentProjectsStore(`).
  - `tests/test_foundation_core_io_utils.py`:
    `test_temp_name_fits_255_utf8_bytes_on_posix` — `monkeypatch.setattr(io_utils.sys, "platform", "linux")`,
    target `tmp_path / ("根" * 80 + ".png")` (244 bytes):
    `len(os.path.basename(io_utils._temp_path(str(target))).encode("utf-8")) <= 255` and
    `io_utils._is_temp_name(os.path.basename(...))`; plus `atomic_write_bytes` to that
    target succeeds (real on Linux / macOS CI).
  - `tests/test_warmup_device_probe.py`: `test_macos_note_does_not_mention_nvidia` —
    platform patched to darwin, no driver → `guess.device == "CPU"` and
    `"NVIDIA" not in guess.note` and `"macOS" in guess.note`.
  - `tests/test_platform_guards.py` (AST / text ratchets over `main.py`, `app/`, `scripts/`):
    no `import win32*` / `pywintypes` / `msvcrt` / `winreg`; `WinDLL` / `windll` only inside an
    `if sys.platform == "win32"` branch (AST: the node's ancestor `If` test mentions
    `"win32"`); `"d3d11"` appears only in `app/qt_environment.py`; no `os.startfile`; no
    `multiprocessing`, `ProcessPoolExecutor` or `num_workers=` in `app/` (O21 report 8:
    threads only, so a future pool must choose its start method on purpose).
- [ ] **Step 2: Run** the four files. Expected: FAIL (`path_key` missing, temp name 258 bytes,
  note says NVIDIA); the guard test passes already except where WX-T1 is not in.
- [ ] **Step 3: Implement** `path_key`, `same_file`; `_temp_path`: off win32 also cut the kept
  name until `len(f".{kept}{tail}".encode("utf-8")) <= _MAX_NAME_CHARS` (characters on
  Windows, UTF-8 bytes on ext4 / APFS; update the `:70-72` comment); `display_read_ahead._key`
  → `path_key`; `recent_projects._same_path(a, b)` → `path_key(os.path.normpath(a)) == path_key(os.path.normpath(b))`;
  device note on darwin: `"No CUDA GPU on macOS: inference runs on the CPU."`.
- [ ] **Step 4: Run** Step 2 files + `tests/test_shell_recent_projects_*.py tests/test_shell_info_recent_projects.py tests/test_display_read_ahead_budget.py tests/test_foundation_core_io_utils.py tests/test_warmup_device_probe.py`. Expected: all pass.
- [ ] **Step 5: Ruff** on own files. **Commit:** `fix(paths): case-insensitive path identity on macOS, temp names within 255 bytes, platform guards (O21)`

---

### Task WX-T5: Font fallback chains

The Qt UI never loads IBM Plex (only the Dash assets vendor it as woff2, which Qt cannot load);
on Linux the chain falls to DejaVu Sans for both sans and mono (O21 report 4, run).

**Impl:** sonnet / medium. **Rev:** sonnet / low. **Lane:** L-xplat. **After:** WX-T4.

**Files:** Modify `app/design/tokens.py:84-86`, `resources/themes/dark_theme.qss:25` and `:481`;
Test `tests/test_design_system_qss.py` (add). Neither file is in a lane list; W4-T6 (L-viz)
edits `app/visualization/`, not these — the lead checks no running task lists them.

Values: `SANS = '"IBM Plex Sans", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans", sans-serif'`;
`MONO = '"IBM Plex Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Liberation Mono", monospace'`.

- [ ] **Step 1: Failing test** `test_qss_font_families_are_the_token_chains`: read the QSS;
  `f'font: 10pt {tokens.SANS};' in qss` and `f'font: 9pt {tokens.MONO};' in qss`; and
  `"DejaVu Sans Mono" in tokens.MONO and "Menlo" in tokens.MONO`.
- [ ] **Step 2: Run** `pytest tests/test_design_system_qss.py -v`. Expected: FAIL (Menlo missing).
- [ ] **Step 3: Edit** the two token strings and the two QSS lines.
- [ ] **Step 4: Run** Step 2 + `tests/test_design_system_*.py`. Expected: pass.
- [ ] **Step 5: Commit:** `style(theme): font fallbacks for macOS and Linux (O21)`

---

### Task WX-T6: CI on macOS, OS-neutral suite, README for Linux / macOS

Commits are not pushed (master section 1), so GitHub runs happen at the owner's first push.
Before that, the lead verifies Linux locally in WSL2 Ubuntu 24.04 (the O21 report's recipe:
throw-away copy outside the repo, uv Python 3.10, `torch==2.8.0` CPU, `requirements*.txt`,
`QT_QPA_PLATFORM=offscreen`). macOS cannot be run here.

**Impl:** sonnet / high. **Rev:** opus / medium. **Lane:** L-xplat. **After:** WX-T5 (the
font chains change which font Linux resolves; fix the font tests against the final chain).

**Files:**
- Modify: `.github/workflows/ci.yml:14-16` (matrix + comment), `:29-33` (apt list), `:49-50`
  (test step)
- Modify tests (the 12 Linux failures, O21 report 10): `tests/test_correct_controls.py:203-210`
  (helper) and the assertion at `:790`; `tests/test_correct_status_chip.py:105-118`
  (`test_a_narrow_chip_keeps_the_tube_the_l_number_and_the_date`);
  `tests/test_metrics_bar_name_elides.py:29-34`; `tests/test_shell_library_labels.py:196-209`;
  `tests/test_warmup_main_window.py:347-353`
- Modify docs: `README.md:26-57` (Requirements, Installation), `README.md:95` (logs),
  `README.md:324` (CI OSes), `docs/DATA_DICTIONARY.md:274-277` (log folders),
  `docs/MODELS.md` (checksum line: `sha256sum` / `shasum -a 256` beside `Get-FileHash`),
  `docs/KNOWN_ISSUES.md` (case-variant stems row: "Windows and macOS")

Test files are in no lane list, but L-correct may port `test_correct_controls.py` /
`test_correct_status_chip.py` in WG-T5: if WG-T5 is running, the lead applies these two edits
after it, on the ported files.

- [ ] **Step 1: Reproduce on Linux (WSL):** run the five files; expected the 12 failures of the
  O21 report (7 + 2 + 1 + 1 + 1). Record each failure's message. Use
  superpowers:systematic-debugging for the three elision tests: find which width the test
  assumes that the real font does not give.
- [ ] **Step 2: Fix the tests, not the product**, keeping each test's intent:
  - status label colour: new helper `text_colours(image, min_alpha=128)` (un-premultiplied
    `Format_ARGB32`, pixels with alpha ≥ `min_alpha`) used for `state_label` only; the
    assertion stays `== {colour}`; `icon_colours` keeps the exact-opaque rule.
  - elision tests: widths come from the label's own `QFontMetrics` (no constant tuned on
    Segoe UI); assertions on which form is shown stay.
  - warm-up chip: pin the model files present (`monkeypatch` `mask_weights_path` /
    `skeleton_weights_path` as seen by `app.inference.runtime.describe_runtime` to an
    existing file) so the chip text does not depend on `checkpoints/`; assertion unchanged
    (`DEVICE_CHECK_FAILED`).
- [ ] **Step 3: Run** the five files on Windows (offscreen) and in WSL: all pass on both.
  Then the full suite in WSL with `--junitxml`: 0 failures (skips as in the O21 report).
- [ ] **Step 4: CI:** matrix `os: [ubuntu-latest, windows-latest, macos-latest]`; comment
  "The app runs on Windows, Linux and macOS (O21); macOS runs on Apple silicon, CPU only";
  apt line `libgl1 libegl1 libxkbcommon0 libdbus-1-3` stays (offscreen needs no xcb);
  test step `pytest -q -rfE` (failures listed in the log). Test step on macOS:
  `continue-on-error: ${{ matrix.os == 'macos-latest' }}` with a comment "remove after the
  first green macOS run (WX-T6 step 8)" — the only run we cannot make locally; ubuntu and
  windows are required green. Do not change the `on:` branch filter (W5-T6).
- [ ] **Step 5: README:** Requirements = the Global Constraints platform line + "An NVIDIA GPU
  is used on Windows and Linux (CUDA 12.8 build); CPU works everywhere and is the only mode on
  macOS". Add "Installation (Linux / macOS)": `python3.10 -m venv venv && source venv/bin/activate`;
  torch lines for Linux + NVIDIA (`whl/cu128`), Linux CPU (`whl/cpu`, else pip pulls ~3 GB of
  CUDA wheels), macOS (`pip install torch==2.8.0 torchvision==0.23.0`); `pip install -r requirements.txt`;
  the apt list `libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 libdbus-1-3 libnss3 libfontconfig1`;
  "Troubleshooting: as root or in a container the app sets `QTWEBENGINE_DISABLE_SANDBOX=1`
  itself; on Ubuntu 24.04 with restricted user namespaces set it yourself". Logs:
  `~/.local/state/sprouts/log` (Linux), `~/Library/Logs/SPROUTS` (macOS). Same log line in
  `DATA_DICTIONARY.md`.
- [ ] **Step 6: Ruff** on the five test files. `pytest tests/test_fair_*.py -v` (doc links).
- [ ] **Step 7: Commit:** `ci: run the suite on macOS; make font- and checkpoint-dependent tests OS-neutral; Linux / macOS install (O21)`
- [ ] **Step 8: Owner's first push / first Mac (hand-over list, no code):** the three CI jobs;
  remove `continue-on-error` once macOS is green (lead commit `ci: macOS job required`);
  on a Mac: `python main.py`, text size (QT_FONT_DPI), Visualize renders, Cmd+wheel and pinch,
  Option+digit in a text field (WX-T7), canvas speed with
  `ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=1` (WX-T8).

---

### Task WX-T7: Platform key text, macOS stage keys, Pinch row in F1

Qt already maps `Ctrl` to Cmd on macOS, so every shortcut works; only the text says "Ctrl".
`Alt+digit` is Option+digit, which types a character in a focused text field (Mac check).
Runs after Wave G: the stage list becomes Library · Generate · Correct · Measure · Visualize
(`Alt+1` … `Alt+5`) and the Trace GT scope is gone, so the literal sites are counted then.

**Impl:** sonnet / high. **Rev:** opus / medium. **Lane:** L-correct. **After:** W2-T8a (the
last L-correct string task of Phase B; it is after WG-T5).

**Files:**
- Create: `app/gui/key_text.py` (Qt-free), `tests/test_key_text.py`
- Modify: `app/gui/shortcuts.py` (`STAGE_KEYS` `:78` today, `ShortcutSpec.label` `:98`,
  `tooltip` `:247`, gesture rows `:130` / `:142`), `app/gui/shell_chrome.py` (stage tooltips),
  and each user-visible `"Ctrl+` / `"Alt+` literal found at execution time by
  `rg -n '"[^"]*\b(Ctrl|Alt)\+' app/gui` (today: `display_controller.py:136`,
  `metrics_bar.py:46`, `widgets/overlays.py:208-253`, `correct/controls.py:57-58,269`,
  `correct/mask_layer.py:78`, `correct/skeleton_layer.py:63`, `skeleton_tools.py:382-402`,
  `window/stages.py:109`; line numbers will have moved). Files owned by L-measure
  (`metrics_bar.py`) are edited only when that lane is idle; else the lead defers that line.
- Test: `tests/test_shell_info_shortcuts.py` (F1 rows), the registry conflict test (grep
  `find_conflicts` in `tests/`)

**Interfaces:**
- Produces: `platform_keys(text: str, platform: str | None = None) -> str` — on darwin
  replaces the tokens `Ctrl` → `Cmd`, `Alt` → `Option` (whole tokens before `+`), else returns
  `text` unchanged. `STAGE_KEYS` stays `Alt+1..5`; new `stage_keys(platform=None) -> tuple[tuple[str, ...], ...]`
  gives each stage `("Alt+N",)` and on darwin `("Alt+N", "Ctrl+Alt+N")` (Cmd+Option+N).

- [ ] **Step 1: Failing tests:** `platform_keys("Ctrl+Shift+Z", "darwin") == "Cmd+Shift+Z"`;
  `platform_keys("Alt+2", "darwin") == "Option+2"`; `platform_keys("Ctrl+wheel", "win32") == "Ctrl+wheel"`;
  `platform_keys("Control panel", "darwin") == "Control panel"`; with `sys.platform`
  patched to darwin the F1 sheet row of undo shows `Cmd+Z`; `find_conflicts` on the darwin
  registry is empty; `stage_keys("darwin")[1] == ("Alt+2", "Ctrl+Alt+2")`; F1 gesture rows
  contain `("zoom_pinch", ("Pinch",), "Zoom about the fingers", GESTURE)` in the canvas scopes;
  ratchet `test_no_raw_ctrl_text_outside_key_text`: an AST scan of `app/gui` string constants
  matching `\b(Ctrl|Alt)\+` outside `shortcuts.py` / `key_text.py` that are not wrapped in
  `platform_keys(...)` is empty.
- [ ] **Step 2: Run** `pytest tests/test_key_text.py tests/test_shell_info_shortcuts.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `key_text.py`; apply it in `label`, `tooltip`, the literals; bind
  the darwin second stage key through the registry.
- [ ] **Step 4: Run** Step 2 + `tests/test_correct_editor_keys.py tests/test_shell_keys_after_tree_focus.py`, and `tests/test_correct_layering.py` (450-line cap). Expected: pass.
- [ ] **Step 5: Ruff; Commit:** `feat(keys): Cmd / Option key text and Cmd+Option stage keys on macOS; Pinch in the key sheet (O21)`

---

### Task WX-T8: macOS raster canvas viewport by default

On macOS `QOpenGLWidget` runs on Apple's deprecated GL 4.1 composed through Metal, with a
read-back path: risk of slow or glitchy canvases and of the "hall of mirrors" class of bug,
none of which we can test without a Mac. The raster viewport is the path every OS already
tests (offscreen, llvmpipe), so it becomes the macOS default; `ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=1`
keeps GL available for the owner's Mac trial.

**Impl:** sonnet / medium. **Rev:** sonnet / medium. **Lane:** L-misc. **After:** W2-T9 (it
edits `canvas_gl.py` and adds the About row / chip line for the canvas path; this task's reason
text must show there).

**Files:** Modify `app/gui/canvas_gl.py:1-13` (docstring), `:63-68`
(`opengl_viewport_requested`), add `import sys`; Test `tests/test_canvas_gl.py` (add after `:44`).

**Interfaces:** `opengl_viewport_requested() -> bool` keeps its signature; new
`DARWIN_REASON = "macOS: raster canvases by default (set ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=1 to try OpenGL)"`
for the log / About row.

- [ ] **Step 1: Failing tests:** `test_macos_defaults_to_the_raster_viewport` —
  `monkeypatch.setattr(canvas_gl.sys, "platform", "darwin")`, env var deleted →
  `opengl_viewport_requested() is False`; `test_macos_can_opt_in_to_opengl` — env `"1"` → True;
  `test_other_platforms_still_default_on` — `"linux"` / `"win32"`, env unset → True.
- [ ] **Step 2: Run** `pytest tests/test_canvas_gl.py -v`. Expected: first test FAILS.
- [ ] **Step 3: Implement:** env unset → `sys.platform != "darwin"`; set → the existing truthy rule.
- [ ] **Step 4: Run** Step 2 + `tests/test_canvas_zoom_*.py`. Expected: pass.
- [ ] **Step 5: Commit:** `fix(canvas): raster viewport by default on macOS; OpenGL stays opt-in (O21)`

---

### Task WX-T9: Deterministic CSV and Library tie order (Sci)

`os.scandir` order is alphabetical on NTFS, hash order on ext4, unspecified on APFS. Rows with
equal `_sort_key` (no tube / position / date, error rows) keep input order, so their CSV order
differs between OSs. Adding the image name as the last key makes the order the same
everywhere; the scan order itself then no longer matters (no `project_layout.py` change).

**Impl:** sonnet / high. **Rev:** opus / high. **Lane:** L-measure. **After:** W2-T11, and
before WG-T8 (master: insert into L-measure).

**Files:**
- Modify: `app/inference/metrics.py:210-216` (`_sort_key`), `app/gui/library_index.py:91`
  (`images.sort(key=lambda entry: entry[0])` → sort on `(depth, name)`; `library_index.py` is
  in no lane list; if WG-T6 lists it while running, the lead moves this line into WG-T6)
- Test: `tests/test_measure_core.py:324-336` (rewrite), a Library tie test in
  `tests/test_library_index.py`
- Modify: `CHANGELOG.md` "Scientific output changes"

**Interfaces:** `_sort_key(row) -> tuple` gains a fifth element `row.get("Image") or ""`.
Public `run_metric_pool` signature unchanged (master: `metrics.py` stays backward compatible).

- [ ] **Step 1: Failing tests:** replace `test_rows_with_equal_sort_keys_keep_input_order` by
  `test_rows_with_equal_sort_keys_come_out_by_image_name`: input
  `{"img07": "", "img02": "", "img11": "", "IMG_0001": ""}` (no metadata, all tied) in that
  order, worker as today, 3 runs → `[r["Image"] for r in rows] == sorted(images)`;
  Library: two names with the same field / tube / date / no depth given in either order →
  same `build_hierarchy` list.
- [ ] **Step 2: Run** `pytest tests/test_measure_core.py tests/test_library_index.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the two keys.
- [ ] **Step 4: Run** Step 2 + `tests/test_error_column.py tests/test_measure_*.py`. The byte-identity
  test `test_measure_csv_is_byte_identical_to_legacy_pipeline` keeps its intent: if it fails
  only on row order, update the reference order in `_legacy_csv` once, in this commit, with
  a comment "tie order by image name (WX-T9)"; values must stay byte-identical.
- [ ] **Step 5: CHANGELOG** "Scientific output changes": "Rows whose Tube / Date / Time /
  Position are all equal (names without metadata, error rows) are now ordered by image name;
  before, by folder-scan order. Values unchanged." with the before / after row-order diff on
  the synthetic set and the 3000-pair real sample (master section 2 script).
- [ ] **Step 6: Commit:** `fix(measure): order tied CSV rows and Library entries by image name on every OS (O21)`

---

### Task WX-T10: Backslash separators in `image_stem`

On POSIX `os.path.basename` does not split `\`, so a Windows path in a CSV `Image` cell keeps
its folders in the stem.

**Impl:** sonnet / low. **Rev:** sonnet / low. **Lane:** L-stems. **After:** W1-T5 (it rewrites
the extension set at `app/naming.py:44`).

**Files:** Modify `app/naming.py:45` (`_SEPARATORS`) and `:77-78`; Test
`tests/test_foundation_core_naming.py` (add after `test_file_names_and_paths_are_parsed_by_stem`, `:94`).

- [ ] **Step 1: Failing test** `test_windows_paths_split_on_every_os`:
  `image_stem("C:\\data\\run 1\\24EFCAM1_T001_L044.png") == "24EFCAM1_T001_L044"` and
  `image_stem("/data/T001_L044.png") == "T001_L044"`.
- [ ] **Step 2: Run** the naming tests — FAILS on Linux / macOS only. On Windows force it with
  `monkeypatch.setattr(naming.os.path, "basename", posixpath.basename)` so the test fails here too.
- [ ] **Step 3: Implement:** `_SEPARATORS` includes `"\\"`; the stem is the text after the last
  `/` or `\` (after stripping trailing separators), not `os.path.basename`.
- [ ] **Step 4: Run** `pytest tests/test_foundation_core_naming.py -v`. Expected: pass.
- [ ] **Step 5: Commit:** `fix(naming): split Windows paths on every OS (O21)`

---

### Task WX-T11 (optional): Bundle IBM Plex fonts

Identical metrics on all three OSs; the Qt UI then shows the designed typeface (today even
Windows shows Segoe UI unless Plex is installed). Licence: SIL Open Font License 1.1.

**Impl:** sonnet / medium. **Rev:** sonnet / low. **Lane:** L-xplat. **After:** WX-T6 (and
W1-T7, which creates `THIRD_PARTY_NOTICES.md`).

**Files:** Create `resources/fonts/IBMPlexSans-Regular.ttf`, `-SemiBold.ttf`,
`IBMPlexMono-Regular.ttf`, `resources/fonts/OFL.txt`; Modify `main.py` (`apply_stylesheet`
`:84-88`: `QFontDatabase.addApplicationFont` for each file before `setStyleSheet`),
`pyproject.toml` `[tool.setuptools.package-data]` (`:80`), `scripts/build_support.py`
(`REQUIRED_DATAS`), `THIRD_PARTY_NOTICES.md` (IBM Plex, OFL-1.1, copyright IBM Corp.);
Test `tests/test_startup_environment.py`.

- [ ] **Step 1: Failing test** `test_bundled_plex_is_registered`: after `main.apply_stylesheet(app)`,
  `"IBM Plex Sans" in QFontDatabase.families()` and `"IBM Plex Mono" in QFontDatabase.families()`.
- [ ] **Step 2: Run** — FAIL. **Step 3:** get the TTFs from the official IBM/plex release
  (OFL; not from the woff2 in `app/assets/fonts/`), add files and calls.
- [ ] **Step 4: Run** the test + `tests/test_fair_packaging.py tests/test_fair_build.py` +
  WX-T6's five font tests on Windows and WSL; the wheel job's asset list gains one font.
- [ ] **Step 5: Commit:** `feat(theme): ship IBM Plex (OFL-1.1) so the UI looks the same on every OS`

---

## Self-review

1. **Spec coverage** (O21 report tasks → here): T1 → WX-T1; T2 → WX-T2; T3 → WX-T6 (README,
   DATA_DICTIONARY, MODELS, KNOWN_ISSUES), classifiers / codemeta → WX-T2, BUILD_EXECUTABLE →
   W5-T7, Architecture_2 line 654 → W5-T7 (it trims that file; noted below); T4 → WX-T6; T5 →
   WX-T4 + W5-T5; T6 → WX-T3 (+ F1 row in WX-T7); T7 → WX-T7; T8 → WX-T5 (+ QT_FONT_DPI in
   WX-T1, Plex in WX-T11); T9 → WX-T1 (Fusion off Windows, desktop name); T10 → WX-T8; T11 →
   WX-T9 (without `project_layout.py`: the name tie-break makes scan order irrelevant); T12 →
   headless opencv WX-T2, temp bytes / device note / guards WX-T4, backslash WX-T10,
   `usable_cpus` and split newlines not built (reasons above); T13 → WX-T6.
2. **Step scan:** each code step names file, signature and the values; the elision-test fix
   is a diagnosis step by design (the exact width assumption is only visible on Linux).
3. **Type consistency:** `path_key` / `same_file` (WX-T4, reused by W5-T5);
   `configure_qt_environment` / `qt_style_for` / `DESKTOP_FILE_NAME` (WX-T1, read by the WX-T4
   guard); `platform_keys` / `stage_keys` (WX-T7); `DARWIN_REASON` (WX-T8).
4. **Review Focus:** all five lines have named tests in their owning task.
5. **Proportion:** signatures and assertions only; the pinch prototype stays in the report.

## Changes to the master

- Replace the provisional Wave X rows with WX-T1 … WX-T11 above (WX-T11 optional).
- Lanes (section 5):
  - Phase A `L-xplat: WX-T1 → WX-T2 → WX-T3 → WX-T4 → WX-T5 → WX-T6 → WX-T11 (optional)`.
  - `L-stems: W1-T5 → WX-T10`.
  - `L-misc: W2-T9 → WX-T8 → W2-T10 → W3-T5 → W4-T5`.
  - `L-measure: W3-T7 → W2-T2 → W2-T11 → WX-T9`, then Phase B `WG-T8 (after WG-T3, WX-T9) → …`.
  - Phase B `L-correct: … → WG-T10 → W2-T8a → WX-T7`, then Phase C as before.
- After edges: W5-T1 After **WX-T2** (was WX-T1: WX-T2 is the `pyproject.toml` /
  `codemeta.json` task); W5-T6 After **WX-T6** (was WX-T5); WG-T8 also After WX-T9.
- W5-T5 scope grows: adopt `io_utils.path_key` / `same_file` at the sites listed in WX-T4
  (first the GT export protected-folder check `app/gt/export.py:150-152`, with its darwin
  test).
- W5-T7: when trimming `docs/Architecture_2.md`, its platform line (`:654`, "Windows 10/11,
  Ubuntu 20.04+, macOS 11+") becomes the WX Global Constraints platform line.
- Section 6 ownership additions: `app/qt_environment.py`, `app/gui/display_read_ahead.py`,
  `app/gui/recent_projects.py`, `app/design/tokens.py`, `resources/themes/dark_theme.qss`,
  `codemeta.json` (then L-repo for W5-T3) → L-xplat; `app/gui/key_text.py` → L-correct;
  `app/gui/library_index.py` → L-measure for WX-T9 unless WG-T6 claims it first.
- Section 8 owner actions: approve the venv swap to `opencv-python-headless` (WX-T2 step 5);
  the first push runs the macOS job, then `continue-on-error` is removed; one Mac session
  (WX-T6 step 8 list).
