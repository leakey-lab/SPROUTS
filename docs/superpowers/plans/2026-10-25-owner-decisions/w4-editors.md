# Wave 4: Editors and Dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Read `00-master.md` sections 2, 5 and 6 first.

**Goal:** Four small, independent changes to what the person sees: a display-only "Smooth
JPEG blocks" switch (W4-T5), one set of dashboard view names with one blue date ramp
(W4-T6), a Save button that is filled only while something is unsaved with one Clear in the
dock (W4-T1), and a toast when a smoothed, uncommitted pick is dropped (W4-T2).

**Architecture:** T5 adds a pure function and one setting to the Qt-free `app/enhancement.py`
and a check box to the Enhance popover; nothing that writes a file imports it. T6 adds one
function to `app/visualization/theme.py` and renames strings in the two dashboards. T1 and T2
run on the unified Correct editor after Wave G: T1 adds one view method to
`CorrectControls` and calls it from `CorrectEditor._refresh`; T2 adds one flag to
`PolylineDraft` and one line to `SkeletonLayer._drop_transient`.

**Tech Stack:** Python 3.10, PyQt6, numpy + core OpenCV (T5), Dash / Plotly (T6), pytest, ruff.

**Spec:** `00-master.md` (this folder; decisions O18, O20, O22, O27 as built, section 4 task
catalogue, section 5 lanes, section 6 file ownership) and
`docs/superpowers/plans/2026-10-12-known-issues-resolution.md` (Wave 4, section 2 owner
answers; committed `19f3761`), plus the O22 memo
`.claude/handoff/owner-decisions-2026-10-25/research/o22-display-upscaler.md` (its verdict and
8-item test list are W4-T5) and the audit reports
`.claude/handoff/issue-audit-2026-10-12/correct-ux.md` (E1, E3, E7) and `generate-dash.md` (D7).

## Global Constraints

See `00-master.md` section 2 (venv python only, offscreen tests, ruff on own files only, no git
commands, edit only the task's files, goldens and CSVs unchanged, 450-line ratchet on
`app/gui/correct/*.py`, Qt-free core modules, `Panel` members only). Wave-specific lines:

- None of the four tasks may change a saved mask, skeleton, GT or CSV byte. Each is
  display or wording; none goes under "Scientific output changes" in `CHANGELOG.md`.
- T5, T6: `app/enhancement.py` stays Qt-free and imports no torch. T5 adds no dependency.
- T6: dashboard view **keys** (`stacked`, `time`, `lines`, `profile`, `faceted`) do not
  change, so callbacks, stores, export file names and the `# view:` line stay. Only the
  layout golden is re-recorded; `GOLDEN_CALLBACKS` and `GOLDEN_CLIENTSIDE_JS_SHA256` stay.
- T1, T2 edit files owned by L-correct. They start only after WG-T5 is committed
  (the Trace GT stage, `raster_editor_base.py` and `gt_tracing_interface.py` are gone then).
- Line budget (checked 2026-10-25): `app/gui/correct/editor.py` 444, `skeleton_layer.py` 448,
  `controls.py` 350 lines. Wave G changes these; each task re-counts before editing and keeps
  the file at 450 or fewer.
- `CHANGELOG.md` (T5, T6, T1, T2) gets one bullet each under `[Unreleased]`: T5 and T2 under
  `### Added` (line 307), T6 and T1 under `### Changed` (line 331). The lead merges them if
  two tasks touch the file in one batch.

## Review Focus

The inputs most likely to bite, each pinned by a test in the named task:

1. **A PNG project (or a `.png` opened after a `.jpg`) with "Smooth JPEG blocks" saved as the
   user's default.** Expected: the check box is hidden for the PNG and the photo is shown
   untouched. Test in W4-T5 (`test_deblock_is_not_applied_to_a_png_even_when_checked`).
2. **Checking "Smooth JPEG blocks" alone.** Expected: only the block smoothing, no CLAHE or
   stretch (today any control change turns the enhancement on). Test in W4-T5.
3. **A polyline with two points placed, or no image open, or `reset()` after a folder change.**
   Expected: Save enabled for the first, disabled for the others; Clear disabled with no image.
   Tests in W4-T1.
4. **A trial with more than 13 dates, or a tube that misses the first date.** Expected: a date
   has the same colour in every view and for every tube; the ramp never leaves the blue
   hue. Tests in W4-T6.
5. **Smooth already left on (it is sticky) and a branch picked.** Expected: leaving the pick
   says nothing; only switching Smooth on during the pick does. Test in W4-T2.

## Decisions settled by reading the code (2026-10-25, HEAD `19f3761`)

| Question | Finding | Consequence |
|---|---|---|
| Where are Save and Clear built, and when enabled? | `app/gui/correct/controls.py:195-202` builds the dock's `save_button` (`primary_button`, compact) and `clear_icon_button` (`IconButton("trash")`); `:155-159` builds a second, text `clear_button` (`ghost_button`) that `app/gui/shell_chrome.py:376` puts on the Correct page. Nothing ever calls `setEnabled` on Save or either Clear: all three stay enabled with no image, and a click does nothing. | T1 removes `clear_button`, adds the one state setter. |
| What does `has_unsaved_changes()` cover? | `CorrectEditor.has_unsaved_changes()` (`editor.py:237`) is `session.is_dirty()` = any layer `is_dirty()`. `SkeletonLayer.is_dirty()` (`skeleton_layer.py:207`) is `document.is_dirty() or draft.pending()`; `PolylineDraft.pending()` (`skeleton_tools.py:126`) is true for **two or more** points (a new line, or a picked branch whose points moved), so a polyline draft of 2+ points counts and a plain pick does not. | The Save state is `session.is_dirty()`; one point reads clean. |
| Dock width rule? | `controls.py:17-18`: "Nothing is hidden, so the dock keeps its width and place across tools and layers"; pinned by `tests/test_correct_editor.py::test_the_dock_keeps_its_place_across_tools_and_layers`. | The label stays "Save" in every state; only enabled state and tooltip change. A disabled button keeps its size. |
| Is a "quiet" Save needed? | The old plan wanted a quiet Save for "nothing unsaved" so that a click could mark the model mask as checked. Owner decision O2 (built in W1-T3 / WG-T2 / WG-T3) removed that meaning: a Save writes only a layer whose pixels differ from what it would otherwise read. | **No quiet state.** With nothing unsaved a Save would write nothing, so the button is disabled. This drops `#primaryButton[quiet]` from `dark_theme.qss` and the helper from `widgets/buttons.py` (the disabled look exists at `dark_theme.qss:118`). A cleared-and-saved mask (O9, empty GT) is a difference, so it is dirty and enabled. |
| How is the Enhance popover built and persisted? | `EnhancePopover` (`app/gui/widgets/overlays.py:559`) hosts one `NormalizationControls` (`app/gui/image_normalization_interface.py:91`), 264 px wide. `EnhanceSettings` (frozen dataclass, `app/enhancement.py:60`) maps fields to JSON keys in `_KEYS`; `EnhanceDefaultsStore` reads the per-user `normalization_defaults.json`, seeded from `config/normalization_defaults.json`; `from_dict` ignores unknown keys, so an old file loads. The editors call `norm_controls.apply_to(photo)` (`correct/editor.py:229, 311`) with an array and no path. | T5 adds `set_source_path(path)`; `display_image(path)` calls it itself (Trace GT keeps working until Wave G); the Correct editor gets a one-line call. |
| How do the dashboards build titles, legends, colours? | Views: `LENGTH_SHELL` (`dash_length_layout.py:21`) and `AREA_SHELL` (`dash_app_area.py:52`) as `ViewOption(label, key)`. Titles, legend titles and hover dates sit in the figure builders: length `dash_visualizations.py` (`:178-181` lines, `:282/332/350/392` hover dates, `:290` time title, `:405` stacked), area `dash_app_area.py` (`:299-302` stacked, `:334-337` profile, `:374` time), faceted `faceted_profile.py:36`. Date colours: length stacked has no colour (template colourway `theme.COLORWAY`), length lines `theme.color(i)` over **all** dates, area profile `theme.color(index)` over the **plotted tube's** dates (`dash_app_area.py:320`). | T6 colours all three by the date's index in `cache.dates`. |
| What does `tests/test_viz_dash_split_golden.py` pin? | `GOLDEN_LAYOUT_SHA256` (line 21, the served length layout: includes the `view-selector` labels and the "Faceted View Options" card title), `GOLDEN_CLIENTSIDE_JS_SHA256`, `GOLDEN_CALLBACKS`. No figure and no area layout. | Re-record the layout sha only. |

---

## Task W4-T5: Display-only "Smooth JPEG blocks" (E7, O22)

**Model / effort:** implementer sonnet / high; reviewer opus / medium. **Lane:** L-misc
(after W3-T5; no other dependency). **Sci:** no.

**Files:**
- Modify: `app/enhancement.py` (new `JPEG_EXTENSIONS`, `is_jpeg_path`, `deblock`, `_deblock_axis`;
  `EnhanceSettings` fields at `:60-134`; `enhance` at `:219-223`; `__all__` at `:40-52`)
- Modify: `app/gui/image_normalization_interface.py` (`settings` `:128`, `set_settings` `:138`,
  `is_active` `:153`, `apply_to` `:157`, `display_image` `:169`, `init_ui` `:212`, handlers
  `:343-364`)
- Modify: `app/gui/correct/editor.py:229` (one line; see Step 12), `config/normalization_defaults.json`,
  `README.md:189-190`, `CHANGELOG.md`
- Test: extend `tests/test_enhance_core.py`, `tests/test_enhance_controls.py`; create
  `tests/test_enhance_display_only.py`

**Interfaces:**
- Produces `app.enhancement.deblock(img: np.ndarray, threshold: float = 14.0) -> np.ndarray`;
  `app.enhancement.is_jpeg_path(path: str | os.PathLike[str] | None) -> bool` (`.jpg` /
  `.jpeg`, any case); `EnhanceSettings.deblock: bool = False` with JSON key `jpeg_deblock`;
  `enhance(img, settings, *, tone: bool = True) -> np.ndarray`;
  `NormalizationControls.set_source_path(path: str | None) -> None`;
  `NormalizationControls.deblock_check: QCheckBox` ("Smooth JPEG blocks").
- Consumes nothing from other tasks. Wave G's plan must keep the
  `norm_controls.set_source_path(image_path)` call (re-check list, end of this task).

**Behaviour (from the memo):** `deblock` is the H.264 weak-filter idea: luma at every 8 px
boundary, Cr and Cb at every 16 px boundary (4:2:0). A boundary step `d = q0 - p0` is spread
over six pixels (taps `d/8, d/4, 3d/8` on each side) only when `|d| < threshold` and both
sides are flat (`|p1 - p0|` and `|q1 - q0|` < `threshold / 2`). Port the algorithm from
`.claude/handoff/owner-decisions-2026-10-25/research/scripts/o22/methods.py:20-50` with three
changes: (a) a 2-D gray input is filtered as luma only; (b) a non-uint8 input is returned as a
copy; (c) only pixels whose filtered YCrCb differs from the original YCrCb are written back
(`changed = (new != base).any(axis=2)`), so untouched pixels stay bit-identical and a flat or
unfiltered image comes back equal (a whole-image YCrCb round trip is off by one on colours).
The input array is never modified (the source cache hands out read-only arrays).
Measured on 640 x 480: 5-13 ms, blockiness 1.95 -> 1.14.

Design points that are decided here:
- The switch runs first inside `enhance()`, before CLAHE / stretch, because CLAHE amplifies
  block steps.
- The check box is independent of the "enhancement on" state. Checking it must not turn CLAHE
  on (`_enabled` stays as it is); it leaves "Show original", restarts the preview timer.
  `apply_to` therefore calls `enhance(image, settings, tone=self._enabled)`.
- Effective only for a JPEG source: `deblock_check` is hidden for any other source and
  `apply_to` ignores a checked box then. The saved default keeps the user's choice.
- Not built (reason): lighting the Enhance rail button or naming the switch in the chip
  tooltip. Today no enhancement lights that button, and the memo does not list it; it would
  be a separate UI change. The "Show original" check and the box's own tooltip carry it.

- [ ] **Step 1: Write the failing core tests** in `tests/test_enhance_core.py`

```python
def _gray_step(left=100, right=108, width=24, height=16):
    img = np.full((height, width), left, np.uint8)
    img[:, 8:] = right
    return img

def test_deblock_turns_a_flat_step_below_the_threshold_into_a_ramp():
    out = enh.deblock(_gray_step(100, 108))
    assert out[0, 5:11].tolist() == [101, 102, 103, 105, 106, 107]
    assert out.shape == (16, 24) and out.dtype == np.uint8          # gray in, gray out
    bgr = enh.deblock(cv2.merge([_gray_step(100, 108)] * 3))
    for channel in range(3):
        assert bgr[0, 5:11, channel].tolist() == [101, 102, 103, 105, 106, 107]

@pytest.mark.parametrize("right", [114, 120])        # the step equals / exceeds the threshold 14
def test_deblock_leaves_a_step_at_or_above_the_threshold_alone(right):
    img = _gray_step(100, right)
    assert np.array_equal(enh.deblock(img), img)

def test_deblock_leaves_a_textured_side_alone():
    img = _gray_step(100, 108)
    img[:, 6] = 92                                    # |p1 - p0| = 8 >= 7
    assert np.array_equal(enh.deblock(img), img)

def test_deblock_flat_image_is_unchanged_and_a_second_pass_changes_nothing():
    flat = np.full((32, 32, 3), (30, 120, 200), np.uint8)
    assert np.array_equal(enh.deblock(flat), flat)
    assert np.array_equal(enh.deblock(enh.deblock(flat)), flat)
    gray = np.full((16, 24), 77, np.uint8)
    assert np.array_equal(enh.deblock(gray), gray)

def test_deblock_filters_chroma_on_a_16_px_grid_only():
    ycc = np.full((16, 40, 3), 128, np.uint8)
    ycc[:, 16:, 1] = 136                              # a Cr step of 8 on the 16 px grid
    bgr = cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)
    cr = lambda a: cv2.cvtColor(a, cv2.COLOR_BGR2YCrCb)[0, :, 1].astype(int)  # noqa: E731
    before, after = cr(bgr), cr(enh.deblock(bgr))
    assert after[15] > before[15] and after[16] < before[16]
    ycc8 = np.full((16, 40, 3), 128, np.uint8)
    ycc8[:, 8:, 1] = 136                              # the same step on the 8 px (luma) grid
    bgr8 = cv2.cvtColor(ycc8, cv2.COLOR_YCrCb2BGR)
    assert np.array_equal(enh.deblock(bgr8), bgr8)

def test_deblock_never_modifies_its_input_and_keeps_shape_and_dtype():
    img = cv2.merge([_gray_step(100, 108)] * 3)
    img.flags.writeable = False                       # what SourceImageCache hands out
    out = enh.deblock(img)
    assert out is not img and out.shape == img.shape and out.dtype == np.uint8
    assert np.array_equal(img, cv2.merge([_gray_step(100, 108)] * 3))
    assert enh.deblock(np.zeros((5, 5, 3), np.uint8)).shape == (5, 5, 3)   # too small to filter

def test_enhance_runs_the_deblock_first_and_tone_false_skips_clahe():
    img = cv2.merge([_gray_step(100, 108)] * 3)
    on = EnhanceSettings(deblock=True)
    assert np.array_equal(enh.enhance(img, on, tone=False), enh.deblock(img))
    assert np.array_equal(enh.enhance(img, on), enh.clahe(enh.deblock(img), 2.0, 8))
    off = EnhanceSettings()
    assert np.array_equal(enh.enhance(img, off), enh.clahe(img, 2.0, 8))   # unchanged path

def test_is_jpeg_path_is_case_insensitive_and_jpeg_only():
    for name in ("a.jpg", "a.JPG", "a.jpeg", "A.Jpeg", "C:/x y/é.jpg"):
        assert enh.is_jpeg_path(name)
    for name in ("a.png", "a.jpg.png", "a", "", None):
        assert not enh.is_jpeg_path(name)

def test_deblock_setting_defaults_off_loads_from_an_old_file_and_round_trips():
    assert DEFAULT_SETTINGS.deblock is False
    old = {"clahe_clip": 12, "clahe_tile": 4, "last_method": "CLAHE"}   # no jpeg_deblock
    loaded, problems = EnhanceSettings.from_dict(old)
    assert loaded.deblock is False and problems == []
    s = EnhanceSettings(deblock=True)
    assert s.to_dict()["jpeg_deblock"] is True
    assert EnhanceSettings.from_dict(s.to_dict())[0] == s
    bad, problems = EnhanceSettings.from_dict({"jpeg_deblock": "yes"})
    assert bad.deblock is False and any("jpeg_deblock" in p for p in problems)
    kept, _ = EnhanceSettings.from_dict({"jpeg_deblock": 1}, base=s)    # an int is not a bool
    assert kept.deblock is True

def test_deblock_core_imports_no_torch_or_qt():
    import subprocess, sys
    from tests.test_layering_contract import ROOT
    code = ("import sys, numpy as np; from app.enhancement import deblock; "
            "deblock(np.zeros((16, 24, 3), np.uint8)); "
            "print('torch' in sys.modules, any(m.startswith('PyQt6') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ["False", "False"]
```

  Also edit `test_settings_legacy_dict_round_trip` (`:104`): its expected dict gains
  `"jpeg_deblock": False`.

- [ ] **Step 2: Run, expect failure**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_enhance_core.py -v`
Expected: the new tests FAIL (`AttributeError: module 'app.enhancement' has no attribute
'deblock'`, `unexpected keyword argument 'deblock'`); `test_settings_legacy_dict_round_trip`
fails on the missing key.

- [ ] **Step 3: Implement the core in `app/enhancement.py`**
  - `JPEG_EXTENSIONS = (".jpg", ".jpeg")`; `is_jpeg_path(path) -> bool` lower-cases
    `os.path.splitext(os.fspath(path))[1]`; `None` and `""` are False.
  - `deblock(img, threshold=14.0)` and `_deblock_axis(ch, block, t)` as described above;
    `_deblock_axis` returns early when no boundary fits (`xs.size == 0`).
  - `EnhanceSettings`: add `deblock: bool = False`; `_KEYS["deblock"] = "jpeg_deblock"`;
    `from_dict` branch `elif field == "deblock"`: accept only `type(raw) is bool`, else report
    `"{key}={raw!r} is not true or false"` and keep the base value. `RANGES` unchanged.
  - `enhance(img, settings, *, tone: bool = True)`: deblock first when `settings.deblock`,
    then CLAHE / stretch only when `tone`; with neither it returns `img.copy()`.
  - Add `deblock`, `is_jpeg_path`, `JPEG_EXTENSIONS` to `__all__` and a line to the module
    docstring.
  - `config/normalization_defaults.json` gains `"jpeg_deblock": false`.

- [ ] **Step 4: Run, expect pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_enhance_core.py -v`
Expected: all PASS, including every pre-existing test (stretch bit-exact, store tests).

- [ ] **Step 5: Write the failing control tests** in `tests/test_enhance_controls.py`
  (`_store`, `_image`, `app` fixture exist; add two helpers: `_blocky()` returns a 48 x 64
  uint8 BGR array whose 8 x 8 blocks differ from their neighbours by 6 to 10 grey levels
  (seeded `default_rng`), so every block boundary is a step below 14 between flat sides;
  `_jpeg(tmp_path)` writes `_blocky()` as `a.jpg` with
  `cv2.imencode(".jpg", ..., [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tofile` and returns the path)

```python
def test_the_box_is_offered_only_for_jpeg_sources(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path))
    assert not controls.deblock_check.isVisibleTo(controls)           # no source yet
    for name, shown in (("a.jpg", True), ("a.JPEG", True), ("a.png", False), (None, False)):
        controls.set_source_path(name)
        assert controls.deblock_check.isVisibleTo(controls) is shown, name
    assert controls.deblock_check.text() == "Smooth JPEG blocks" and controls.deblock_check.toolTip()

def test_checking_the_box_smooths_blocks_without_turning_on_clahe(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path))
    img = _blocky()                                    # 48 x 64 BGR with 8 px block steps of 8
    controls.set_source_path("a.jpg")
    hits = []
    controls.enhancement_changed.connect(lambda: hits.append(1))
    assert not controls.is_active() and controls.apply_to(img) is img
    controls.deblock_check.setChecked(True)
    QTest.qWait(120)                                   # past the 80 ms preview delay
    assert controls.is_active() and hits == [1]
    assert np.array_equal(controls.apply_to(img), enhance(img, controls.settings(), tone=False))
    assert not np.array_equal(controls.apply_to(img), img)
    controls.original_check.setChecked(True)           # "Show original" bypasses it
    assert not controls.is_active() and controls.apply_to(img) is img
    controls.deblock_check.setChecked(False)           # a control change shows the result again
    assert not controls.original_check.isChecked() and not controls.is_active()

def test_deblock_is_not_applied_to_a_png_even_when_checked(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path, user={"jpeg_deblock": True}))
    img = _blocky()
    assert controls.settings().deblock is True          # the saved default is kept
    controls.set_source_path("a.png")
    assert not controls.is_active() and controls.apply_to(img) is img
    controls.set_source_path("a.jpg")
    assert controls.is_active() and not np.array_equal(controls.apply_to(img), img)

def test_deblock_then_stretch_applies_both_in_order(app, tmp_path):
    controls = NormalizationControls(store=_store(tmp_path))
    controls.set_source_path("a.jpg")
    controls.deblock_check.setChecked(True)
    controls.method_combo.setCurrentText(METHOD_STRETCH)
    controls.apply_button.click()
    img = _blocky()
    assert np.array_equal(controls.apply_to(img), enhance(img, controls.settings()))

def test_the_box_is_saved_with_the_defaults_and_reset_restores_it(app, user_config):
    controls = NormalizationControls()
    controls.set_source_path("a.jpg")
    controls.deblock_check.setChecked(True)
    controls.save_current_as_defaults()
    saved = json.loads((user_config / "normalization_defaults.json").read_text("utf-8"))
    assert saved["jpeg_deblock"] is True
    again = NormalizationControls()
    assert again.deblock_check.isChecked() and again.settings().deblock
    again.deblock_check.setChecked(False)
    again.reset_to_defaults()
    assert again.deblock_check.isChecked()

def test_display_image_follows_the_path_it_is_given(app, tmp_path):
    jpg, png = _jpeg(tmp_path), tmp_path / "p.png"
    cv2.imencode(".png", _blocky())[1].tofile(str(png))
    controls = NormalizationControls(store=_store(tmp_path))
    controls.deblock_check.setChecked(True)
    assert not np.array_equal(controls.display_image(str(jpg)), controls.source_image(str(jpg)))
    assert controls.display_image(str(png)) is controls.source_image(str(png))
```
  Extend `test_every_button_fits_inside_the_264px_popover_with_the_app_theme` (`:113`): call
  `controls.set_source_path("a.jpg")` before the method loop and add
  `assert controls.deblock_check.isVisible() and controls.deblock_check.width() >= controls.deblock_check.sizeHint().width()`
  inside it; the popover width stays 264.

- [ ] **Step 6: Run, expect failure**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_enhance_controls.py -v`
Expected: the new tests FAIL (`no attribute 'deblock_check'` / `set_source_path`).

- [ ] **Step 7: Implement the controls in `app/gui/image_normalization_interface.py`**
  - `init_ui`: `self.deblock_check = QCheckBox("Smooth JPEG blocks")` with a tooltip ("Soften
    the 8 x 8 blocks of a JPEG photo when zoomed in. Display only: nothing saved changes."),
    added to the layout **above** the method block, `setVisible(False)`; connect `toggled` to
    a new `_on_deblock_toggled`.
  - State `self._jpeg_source = False`. `set_source_path(path)`: `self._jpeg_source =
    is_jpeg_path(path)`, `self.deblock_check.setVisible(self._jpeg_source)`.
  - `settings()` adds `deblock=self.deblock_check.isChecked()`; `set_settings` sets the box
    under `_updating`.
  - `_deblock_effective()`: `deblock_check.isChecked() and self._jpeg_source`.
    `is_active()`: `(self._enabled or self._deblock_effective()) and not
    self.original_check.isChecked()`. `apply_to`: when active,
    `enhance(image, replace(self.settings(), deblock=self._deblock_effective()), tone=self._enabled)`.
  - `_on_deblock_toggled`: return while `_updating`; leave "Show original" silently (the code
    of `_show_enhanced` without setting `_enabled`; give `_show_enhanced` a keyword
    `tone: bool = True` for it); start the preview timer.
  - `display_image(path)` calls `self.set_source_path(path)` first. Update the module
    docstring list.

- [ ] **Step 8: Run, expect pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_enhance_controls.py tests/test_enhance_core.py -v`
Expected: all PASS (every older test in both files stays green).

- [ ] **Step 9: Write the failing proof tests** in new `tests/test_enhance_display_only.py`
  (memo items 3, 4 and 6)

```python
"""Smooth JPEG blocks is display only (O22): saved pixels, the models and Measure never see it."""
# imports: ast, os, pathlib, time, numpy, cv2, pytest; from tests.test_correct_goldens import
# (GOLDENS, files, replay, skeleton_start, assert_mask_file, assert_skeleton_file,
#  app, guards, make_editor)  -- reuse the module-scope fixtures by importing them
# from tests.correct_goldens_support import SYNTH_STEM, make_synth_project
from app.gui.correct.layer import MASK, SKELETON

FORBIDDEN = ("inference", "handlers", "gt", "masks")

def _imports_enhancement(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(a.name.split(".")[:2] == ["app", "enhancement"]
                                                for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0 and (mod == "app.enhancement"
                                    or (mod == "app" and any(a.name == "enhancement" for a in node.names))):
                return True
            if node.level > 0 and (mod.split(".")[-1] == "enhancement"
                                   or any(a.name == "enhancement" for a in node.names)):
                return True
    return False

@pytest.mark.parametrize("package", FORBIDDEN)
def test_nothing_that_writes_or_measures_imports_the_enhancement_module(package):
    files_ = sorted(pathlib.Path("app", package).rglob("*.py"))
    assert files_, package
    offenders = [str(p) for p in files_ if _imports_enhancement(p.read_text(encoding="utf-8"))]
    assert offenders == []

def test_the_import_scan_detects_the_import_forms():
    for src in ("import app.enhancement", "from app.enhancement import enhance",
                "from app import enhancement", "from .. import enhancement",
                "from ..enhancement import clahe"):
        assert _imports_enhancement(src), src
    assert not _imports_enhancement("import os\nfrom app.io_utils import imread_unicode")

def test_the_correct_goldens_replay_identically_with_the_box_on(make_editor, tmp_path):
    case = "synth_model_mask_and_skeleton"
    golden = GOLDENS["cases"][case]
    folder, png = make_synth_project(str(tmp_path), case)
    jpg = _blocky_jpeg_instead_of(png)         # writes <stem>.jpg (q 90, 8 px block offsets), removes the png
    editor = make_editor(jpg, folder)
    controls = editor.norm_controls
    assert controls.deblock_check.isVisibleTo(controls)
    raw = editor.photo_item.pixmap().toImage()
    controls.deblock_check.setChecked(True)
    editor.apply_normalization()
    assert editor.photo_item.pixmap().toImage() != raw           # the box really changed the picture
    mask_file, correction = files(folder)
    assert skeleton_start(editor) == golden["skeleton"]["start"]
    assert replay(editor, MASK, "mask", golden["mask"]["per_op_plane_sha256"]) is golden["mask"]["save_returned"]
    assert_mask_file(mask_file, golden["mask"]["saved"])
    assert replay(editor, SKELETON, "skeleton", golden["skeleton"]["per_op_raster_sha256"]) is golden["skeleton"]["save_returned"]
    assert_skeleton_file(correction, golden["skeleton"]["saved"])

@pytest.mark.skipif(os.environ.get("SPROUTS_PERF_TESTS") != "1", reason="wall-clock budget")
def test_deblock_of_a_640x480_photo_takes_under_30_ms():
    img = (np.random.default_rng(1).random((480, 640, 3)) * 255).astype(np.uint8)
    times = []
    for _ in range(9):
        start = time.perf_counter(); enh.deblock(img); times.append(time.perf_counter() - start)
    assert sorted(times)[4] < 0.030
```
  `_blocky_jpeg_instead_of(png)`: reads the gradient photo, adds a per-8x8-block offset in
  -4..4 (seeded), writes `<stem>.jpg` next to it with `cv2.imencode(".jpg", ..., [IMWRITE_JPEG_QUALITY, 90])`
  and removes the PNG. The three fixtures `app`, `guards`, `make_editor` are imported from
  `tests.test_correct_goldens` (the pattern `tests/test_enhance_controls.py` uses for
  `exif_rotated_jpeg`). GT bytes and exports (memo item 3, second half) are covered by the
  import scan: `app/gt` cannot see the module, and the GT store writes from the class raster,
  not from the photo. After Wave G, the lead adds the same box-on parametrisation to
  `tests/test_gt_goldens.py` (re-check list).

- [ ] **Step 10: Run, expect failure**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_enhance_display_only.py -v`
Expected: the import-scan tests PASS already (nothing imports it today: the scan is a
guard); `test_the_correct_goldens_replay_identically_with_the_box_on` FAILS (the box is
hidden: the editor never calls `set_source_path`).

- [ ] **Step 11: Wire the Correct editor** (`app/gui/correct/editor.py`, `load_image`, before
  `self.photo_item.set_image(self.norm_controls.apply_to(photo))` at `:229`): add
  `self.norm_controls.set_source_path(image_path)`. **File-ownership exception:** this one line
  is in an L-correct file. The lead dispatches Steps 9-12 when no L-correct task is in flight
  (or the lead applies the line in the next L-correct commit). Until the line is in, the box
  stays hidden in Correct and nothing else changes. Re-count lines: 444 + 1.

- [ ] **Step 12: Run, expect pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_enhance_display_only.py tests/test_enhance_core.py tests/test_enhance_controls.py tests/test_correct_goldens.py tests/test_correct_layering.py tests/test_layering_contract.py -v`
Expected: all PASS. Also `SPROUTS_PERF_TESTS=1` once for the speed guard.

- [ ] **Step 13: Docs.** `README.md:189-190` (Enhance paragraph): add one sentence: "For `.jpg` /
  `.jpeg` photos, **Smooth JPEG blocks** (off by default) softens the 8 x 8 compression blocks
  you see when zoomed in; like the rest of Enhance it is display only." `CHANGELOG.md`
  `### Added` bullet (display only, not under "Scientific output changes").

- [ ] **Step 14: Ruff on the task's files:** `./venv/Scripts/python.exe -m ruff check --fix app/enhancement.py app/gui/image_normalization_interface.py app/gui/correct/editor.py tests/test_enhance_core.py tests/test_enhance_controls.py tests/test_enhance_display_only.py` then `ruff format` on the same list. Expected: clean.

- [ ] **Step 15: Commit (lead):**
  `git add app/enhancement.py app/gui/image_normalization_interface.py app/gui/correct/editor.py config/normalization_defaults.json README.md CHANGELOG.md tests/test_enhance_core.py tests/test_enhance_controls.py tests/test_enhance_display_only.py`
  `git commit -m "feat(enhance): display-only JPEG block smoothing, default off"`

**Existing tests that must stay green (verified by name):**
`tests/test_enhance_core.py::test_fast_stretch_is_bit_exact_with_the_float64_reference`,
`::test_enhance_dispatches_on_method`, `::test_from_dict_never_raises_and_clamps`,
`::test_store_seeds_user_file_from_factory_on_first_run`;
`tests/test_enhance_controls.py::test_apply_to_is_identity_until_active_then_enhances`,
`::test_apply_activates_immediately_and_original_toggles_back`,
`::test_legacy_public_surface_is_kept`,
`::test_save_as_default_writes_the_user_config_and_not_the_repo`;
`tests/test_correct_goldens.py` (all); `tests/test_correct_editor.py` Enhance tests near `:1640`.

**Re-check at dispatch after Wave G:** `CorrectEditor.load_image` still passes the path to
`set_source_path` before `apply_to` (WG-T4 / WG-T5 may move the open flow); `tests/test_gt_goldens.py`
(WG-T0) gains a box-on case; `raster_editor_base.py` no longer exists, so `display_image`
is used only by tests.

---

## Task W4-T6: One set of dashboard view names; one blue date ramp (D7, O27)

**Model / effort:** implementer sonnet / high; reviewer sonnet / medium. **Lane:** L-viz
(start now; no dependency). **Sci:** no (no dashboard CSV export changes: export names use the
view key, `dash_export.py:181, 220-222`).

**Files:**
- Modify: `app/visualization/theme.py` (new `DATE_RAMP`, `date_colors`; near `color()` at `:278`)
- Modify: `app/visualization/dash_length_layout.py:21-32` (labels), `:241-245` (card title)
- Modify: `app/visualization/dash_app_area.py:52-62` (labels), `:297-303`, `:305-343`, `:374`
- Modify: `app/visualization/dash_visualizations.py:121-190` (lines), `:282, 290, 332, 350` (time
  view), `:384-406` (stacked)
- Modify: `app/visualization/faceted_profile.py:36` (`TITLE`)
- Modify: `docs/SPROUTS_DASH_THEME.md:164, 228-231`, `CHANGELOG.md`
- Test: `tests/test_viz_dash_split_golden.py:21`, `tests/test_viz_dash_callbacks.py:224`,
  `tests/test_viz_data_metric_charts.py:46`, `tests/test_viz_data_time_view.py:105-179`;
  create `tests/test_viz_dash_view_names.py`; extend `tests/test_viz_data_theme.py`

**Interfaces:**
- Produces `theme.DATE_RAMP: tuple[str, ...]` (the 13 colours below) and
  `theme.date_colors(n: int) -> list[str]`: `n` colours, oldest darkest, newest lightest, one hue.
  For `n <= 13` the stops `DATE_RAMP[round(i * 12 / (n - 1))]` (`n == 1`: the first stop); for
  `n > 13` linear interpolation in sRGB between the 13 stops; `n <= 0` gives `[]`.
- A date's colour is `theme.date_colors(len(cache.dates))[cache.dates.index(date)]`: its place
  among **all** dates of the trial, so it is the same in every view and for every tube.

**Decided content (old plan, W4-T6; keys unchanged):**

| Key | Label (both dashboards) | Chart title |
|---|---|---|
| `stacked` | Totals by Tube | `Root {Length\|Area} by Tube and Date` |
| `time` | Growth Over Time | `Root {Length\|Area} Growth Over Time` |
| `lines` (length), `profile` (area) | Depth Profile | `Root {Length\|Area} Depth Profile - Tube N` |
| `faceted` (length only) | Depth Profile Grid | `Depth Profile Grid - Field Average (Left) vs Tube (Right)` |

Also: legend title "Measurement date" everywhere (stacked, lines, area profile); hover
dates in ISO `%Y-%m-%d` (length time view and stacked; the area dashboard already does it);
"Position (L)" on both depth axes (area profile x axis was "Position"); the faceted options
card title "Faceted View Options" becomes "Depth Profile Grid Options". `DATE_RAMP` =
`#127abe #2c84c4 #3e8dcb #4f97d1 #5ea1d8 #6cabde #7bb4e5 #89beeb #97c8f2 #a6d2f8 #b4dcff #c8e5ff #dbeefe`.
Applied to: length stacked (`marker_color` per date trace), length Growth Lines
(`line.color`), area stacked, area profile (`marker_color`). Tubes in Growth Over Time keep
the colourway. Above about seven dates neighbouring ramp colours are close; the legend order,
a 1 px gap between stacked segments and the hover carry the date (state this in the docs).
Not built: renaming keys; coloured depth (Plasma stays for the faceted bars); the faceted
"Vertical Depth (cm)" axis title.

- [ ] **Step 1: Write the failing tests**

  In `tests/test_viz_data_theme.py` (it has `contrast_ratio`, `_parse_colour`):
```python
def _oklab(hex_colour):                      # sRGB '#rrggbb' -> OKLab (Ottosson)
    ...                                      # 10 lines: linearise, LMS matrix, cube root, Lab matrix

def _delta(a, b):                            # OKLab distance x 100
    ...

def test_date_ramp_is_the_decided_list_and_n13_returns_it_whole():
    assert theme.DATE_RAMP == ("#127abe", "#2c84c4", "#3e8dcb", "#4f97d1", "#5ea1d8", "#6cabde",
                               "#7bb4e5", "#89beeb", "#97c8f2", "#a6d2f8", "#b4dcff", "#c8e5ff",
                               "#dbeefe")
    assert theme.date_colors(13) == list(theme.DATE_RAMP)
    assert theme.date_colors(1) == ["#127abe"] and theme.date_colors(2) == ["#127abe", "#dbeefe"]
    assert theme.date_colors(0) == []

@pytest.mark.parametrize("n", range(1, 25))
def test_date_colors_are_ordered_readable_and_clear_of_the_layer_accents(n):
    colours = theme.date_colors(n)
    assert len(colours) == n and all(len(c) == 7 and c.startswith("#") for c in colours)
    lightness = [_oklab(c)[0] for c in colours]
    assert lightness == sorted(lightness)                          # oldest darkest, newest lightest
    assert all(contrast_ratio(c, theme.BG_2) >= 3.0 for c in colours)
    for accent in (theme.ACCENT, theme.SKEL, theme.SEL):           # mask green, skeleton, selection
        assert min(_delta(c, accent) for c in colours) >= 10, accent
```
  If the interpolated colours for some `n > 13` dip under 10 from an accent, the implementer
  reports it to the lead instead of changing the list (the list is the owner-approved one).

  New `tests/test_viz_dash_view_names.py` (fixtures: reuse `length_app`, `area_app` from
  `tests/test_viz_dash_callbacks.py` and `charts` pattern from `test_viz_data_theme.py`):
```python
def test_both_dashboards_label_their_views_with_the_same_words():
    from app.visualization.dash_length_layout import LENGTH_SHELL
    from app.visualization.dash_app_area import AREA_SHELL
    length = {v.value: v.label for v in LENGTH_SHELL.views}
    area = {v.value: v.label for v in AREA_SHELL.views}
    assert length == {"stacked": "Totals by Tube", "time": "Growth Over Time",
                      "lines": "Depth Profile", "faceted": "Depth Profile Grid"}
    assert area == {"stacked": "Totals by Tube", "time": "Growth Over Time",
                    "profile": "Depth Profile"}
    assert length["stacked"] == area["stacked"] and length[LENGTH_SHELL.tube_view] == area[AREA_SHELL.tube_view]
```
  (check the `ViewOption` field names `label` / `value` at `dash_shell.py:120`), plus:
  - `test_chart_titles_use_one_scheme`: length stacked `layout.title.text == "Root Length by Tube and Date"`,
    area `"Root Area by Tube and Date"`; length time `"Root Length Growth Over Time"`, area
    `"Root Area Growth Over Time"`; length lines `"Root Length Depth Profile - Tube 1"`, area
    profile `"Root Area Depth Profile - Tube 1"`; faceted
    `"Depth Profile Grid - Field Average (Left) vs Tube (Right)"`.
  - `test_legend_title_and_depth_axes_agree`: `legend.title.text == "Measurement date"` on the
    length stacked, length lines, area stacked and area profile figures; length lines
    `yaxis.title.text == "Position (L)"`, area profile `xaxis.title.text == "Position (L)"`.
  - `test_hover_dates_are_iso`: no `%b %d, %Y` in any length hovertemplate of stacked / time
    (`"%b" not in json.dumps(fig.to_plotly_json())`).
  - `test_a_date_has_one_colour_in_every_view_and_every_tube`: build the length and area
    caches on a CSV whose tube 2 has no row on the first date (rows from
    `tests/test_viz_data_cache.py::ROWS` plus a third date for tube 1 only; write with
    `write_metric_csv`); expected `ramp = theme.date_colors(len(cache.dates))`; for each date
    index `i`: the stacked bar trace named that date has `marker.color == ramp[i]`; the Growth
    Lines trace (tube 1) has `line.color == ramp[i]`; the area profile bar of tube 2 for a
    date it has uses the same `ramp[i]`, so the colour of the date it shares with tube 1 is
    equal in both tubes' figures.
  - `test_more_than_13_dates_stay_on_the_ramp`: 15 dates in the CSV; every stacked trace colour
    is in `theme.date_colors(15)` and the first and last differ.

  Update existing tests so they assert the new strings: `tests/test_viz_data_metric_charts.py:46`
  (`f"Root {label} by Tube and Date"`), `tests/test_viz_data_time_view.py` reference builders
  (`:105, :121, :144` hover dates to `%{x|%Y-%m-%d}`; `:150` title
  `f"Root {label} Growth Over Time"`), `tests/test_viz_dash_callbacks.py::test_area_profile_bars_use_the_theme_palette`
  (expects `theme.date_colors(2)` for the two dates of the fixture: `[ramp[0], ramp[1]]`).

- [ ] **Step 2: Run, expect failure**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_viz_data_theme.py tests/test_viz_dash_view_names.py tests/test_viz_data_metric_charts.py tests/test_viz_data_time_view.py tests/test_viz_dash_callbacks.py tests/test_viz_dash_split_golden.py -v`
Expected: new and updated tests FAIL (`module 'app.visualization.theme' has no attribute
'date_colors'`, old titles); `test_served_layout_is_unchanged` still passes (not yet changed).

- [ ] **Step 3: Implement.** `theme.py`: `DATE_RAMP`, `date_colors` (module-level, no new
  import beyond `colorsys`-free arithmetic). Then rename strings per the table in the five
  files above; stacked bars get `marker_color=ramp[i]`; Growth Lines `line.color =
  ramp[i]` (replace `theme.color(i)`); the area profile maps each plotted date to
  `index = dates.index(day)` over `[d.strftime("%Y-%m-%d") for d in cache.dates]` (not
  `enumerate(groupby)`). Title helpers take `label` from `self._metric_text()` on the length
  side and the literal "Area" on the area side.

- [ ] **Step 4: Re-record the layout golden.** Run
  `./venv/Scripts/python.exe -m pytest tests/test_viz_dash_split_golden.py::test_served_layout_is_unchanged -v`;
  the failure message prints `new sha256 ...`. Write it into `GOLDEN_LAYOUT_SHA256` (`:21`).
  The commit message names the reason (view labels and the card title). Confirm
  `GOLDEN_CALLBACKS` and `GOLDEN_CLIENTSIDE_JS_SHA256` did not need changes.

- [ ] **Step 5: Run, expect pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests -k "viz" -v`
Expected: all PASS, including `tests/test_viz_data_theme.py::test_trace_marks_reach_graphical_contrast`
(every ramp colour is at least 3:1 on `BG_2`), `tests/test_viz_dash_shell.py`,
`tests/test_viz_dash_export.py` (keys unchanged), `tests/test_viz_data_faceted.py`,
`tests/test_viz_data_bar_labels.py`.

- [ ] **Step 6: Docs.** `docs/SPROUTS_DASH_THEME.md`: line 164 example title, lines 228-231 the
  `views` example, and a short "Date ramp" paragraph (list, indexing rule, the many-dates note).
  `CHANGELOG.md` `### Changed` bullet ("Dashboard views have one set of names; dates are
  coloured on one blue ramp that keeps clear of the mask, skeleton and selection colours").

- [ ] **Step 7: Ruff** on `app/visualization/theme.py app/visualization/dash_length_layout.py app/visualization/dash_app_area.py app/visualization/dash_visualizations.py app/visualization/faceted_profile.py` and the touched tests (`check --fix`, then `format`). Expected: clean.

- [ ] **Step 8: Commit (lead):** `git add` the files above;
  `git commit -m "feat(viz): one set of view names; dates on a blue ramp that keeps clear of the layer accents"`

---

## Task W4-T1: Save is filled only while something is unsaved; one Clear, in the dock (E1, O18)

**Model / effort:** implementer opus / medium; reviewer sonnet / high. **Lane:** L-correct,
Phase C, after WG-T5 and W3-T4. **Sci:** no.

**Decision recorded:** there is **no quiet Save state**. Reason: under O2 a Save writes only a
layer that differs from what it would read, so a Save with nothing unsaved writes nothing; a
button that does nothing is disabled. This also means no `#primaryButton[quiet]` rule in
`resources/themes/dark_theme.qss` and no helper in `app/gui/widgets/buttons.py` (the old
plan's two extra files). "Accent fill" = enabled (`primaryButton`), "no accent" = the existing
`#primaryButton:disabled` look (`dark_theme.qss:118`). Unsaved = `session.is_dirty()`: a
polyline of two or more points, a moved pick, a cleared mask or an edit all count.

**Files:**
- Modify: `app/gui/correct/controls.py` (docstring `:13-21`; remove `_CLEAR_TEXTS` `:55-59`, the
  text `clear_button` `:155-159` and its naming in `show_layer` `:267-273`; add
  `set_image_actions`)
- Modify: `app/gui/correct/editor.py` (`:147` alias; `_refresh` `:424-434`; optional `save` `:284`)
- Modify: `app/gui/shell_chrome.py:375-376` (the `"Correct"` page)
- Modify: `README.md:220-222` (only if it still says an unsaved-free Save accepts the layer),
  `CHANGELOG.md`
- Test: `tests/test_correct_controls.py` (`:371, :586, :634, :966`), `tests/test_correct_editor.py`
  (`:462, :1957`), `tests/test_correct_main_window.py:327-328`,
  `tests/test_shell_chrome_topbar.py:184-207`, `tests/test_correct_tab_order.py:84-91`

**Interfaces:**
- Produces `CorrectControls.set_image_actions(has_image: bool, unsaved: bool) -> None`:
  `save_button.setEnabled(has_image and unsaved)`, `clear_icon_button.setEnabled(has_image)`;
  Save text stays "Save"; Save tooltip is `shortcuts.tooltip("Save the unsaved layers", "save",
  shortcuts.CORRECT)` when enabled and the same text plus " · Nothing unsaved" (no image:
  " · Open an image first") when disabled. A new page starts with `(False, False)`.
- Consumes `CorrectEditor.session.image_path` and `.is_dirty()`.
- Removes `CorrectControls.clear_button` and `CorrectEditor.clear_button`.

- [ ] **Step 0: Re-check at dispatch (Wave G has landed).** Re-read, in this order, and record
  differences in the report before editing: (1) the dock builders in `controls.py` (`_build_dock`:
  are `save_button` and `clear_icon_button` still the names, and does the GT layer add its own
  clear or save button?); (2) `shell_chrome.py` `pages` dict: which widgets the Correct page
  holds now (GT "Classes…" / "Export dataset…" may have moved there: only Clear is removed);
  (3) `CorrectEditor._refresh` and `reset`; (4) `CorrectSession.save` for the "nothing dirty"
  fallback (`dirty_layers() or [active_id]`) after WG-T2 / WG-T3; (5) tests that Wave G ported
  and that name the old controls: `grep -rn "clear_button\|save_button\|clear_icon_button" tests`
  (today: `test_gt_editor_ux.py:188-196`, `test_raster_editor_behaviour.py:217`,
  `test_gt_tab_order.py:104, 149, 154`, `test_gt_tracing_interface.py:121` belong to the deleted
  Trace GT editor and are gone or ported); (6) the line counts of `editor.py` and `controls.py`.

- [ ] **Step 1: Write the failing tests**

  `tests/test_correct_controls.py` (page fixture `make_page`):
```python
def test_save_and_clear_follow_the_image_and_the_unsaved_state(make_page):
    controls = make_page(show=False).controls
    assert not controls.save_button.isEnabled() and not controls.clear_icon_button.isEnabled()
    controls.set_image_actions(True, False)                      # an image, nothing unsaved
    assert controls.clear_icon_button.isEnabled() and not controls.save_button.isEnabled()
    assert "Nothing unsaved" in controls.save_button.toolTip()
    controls.set_image_actions(True, True)
    assert controls.save_button.isEnabled() and "Ctrl+S" in controls.save_button.toolTip()
    assert "Nothing unsaved" not in controls.save_button.toolTip()
    controls.set_image_actions(False, True)                      # no image wins over a stale dirty flag
    assert not controls.save_button.isEnabled() and not controls.clear_icon_button.isEnabled()
    assert controls.save_button.text() == "Save"                 # the label never changes

def test_there_is_one_clear_and_it_is_in_the_dock(make_page):
    controls = make_page(show=False).controls
    assert not hasattr(controls, "clear_button")                 # the top-bar twin is gone
    assert controls.clear_icon_button.toolTip().startswith("Clear ")
```
  The dock Clear stays covered by `BUTTON_REQUESTS` (`clear_icon_button`). Edit existing tests: delete
  `("clear_button", "clear_requested")` from `BUTTON_REQUESTS` (`:586`); in
  `test_each_button_requests_its_action_and_touches_no_layer` add
  `controls.set_image_actions(True, True)` before the click; in
  `test_the_clear_buttons_name_the_shown_layer` drop the `tip` parameter and the
  `clear_button` assertion (keep the `clear_icon_button` tooltip lines); delete the
  `clear_button.text()` assertion at `:634` and the `clear_button.focusPolicy()` assertion at
  `:966`; in `tests/test_correct_tab_order.py::dock` (`:84-91`) end with
  `[b for b in (controls.clear_icon_button, controls.save_button) if b.isEnabled()]`.

  `tests/test_correct_editor.py` (helpers `paint`, `click`, `make_editor`, `project`):
```python
def test_save_is_enabled_only_while_something_is_unsaved(make_editor, project):
    editor = make_editor()                                       # no image
    assert not editor.save_button.isEnabled() and not editor.controls.clear_icon_button.isEnabled()
    editor.load_image(project.a, project.folder)
    assert not editor.save_button.isEnabled() and editor.controls.clear_icon_button.isEnabled()
    paint(editor, (20, 100), (80, 110))
    assert editor.save_button.isEnabled()
    editor.undo()                                                # back to the saved state
    assert not editor.save_button.isEnabled()
    paint(editor, (20, 100), (80, 110))
    assert editor.save() is True
    assert not editor.save_button.isEnabled()
    assert editor.reset() is True                                # a new folder
    assert not editor.save_button.isEnabled() and not editor.controls.clear_icon_button.isEnabled()

def test_a_polyline_of_two_points_enables_save_and_a_cancel_disables_it(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.controls.tool_buttons[(SKELETON, "polyline")].click()
    click(editor, (40, 60))
    assert not editor.save_button.isEnabled()                    # one point is not a line yet
    click(editor, (150, 70))
    assert editor.save_button.isEnabled()
    editor.layers[SKELETON].cancel_polyline()
    assert not editor.save_button.isEnabled()

def test_clearing_the_mask_makes_save_available(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.clear_active_layer()
    assert editor.save_button.isEnabled()                        # an empty mask is a change (O9)

def test_the_dock_keeps_its_width_and_place_in_every_save_state(make_editor, project):
    editor = make_editor(size=(1280, 800))
    save, places = editor.controls.save_button, []
    def place():
        settle(editor)
        return (editor.dock.geometry().getRect(), save.mapTo(editor, QPoint(0, 0)).x())
    places.append(place())                                       # no image
    editor.load_image(project.a, project.folder); places.append(place())   # clean
    paint(editor, (20, 100), (80, 110)); places.append(place())            # unsaved
    assert len(set(places)) == 1, places
```
  Edit `:462` (remove the `clear_button` alias assertion) and replace
  `test_the_topbar_clear_button_follows_and_clears_the_edited_layer` (`:1957`) with the same
  body driven through `editor.controls.clear_icon_button` and
  `assert "mask" in button.toolTip()` / `"skeleton" in button.toolTip()` after
  `set_active_layer(SKELETON)`. Add to `test_correct_main_window.py:327-328` and
  `test_shell_chrome_topbar.py:184` (rename `test_the_correct_page_has_no_clear_button`):
```python
    page = window.action_bar_stack.currentWidget()
    assert not hasattr(editor, "clear_button")
    assert not [b for b in page.findChildren(QPushButton) if "Clear" in b.text()]
```
  Conditional test (add it, see Step 4): `test_ctrl_s_with_nothing_unsaved_writes_no_file` in
  `tests/test_correct_editor.py`: clean `project.a`, `editor.save()`, then
  `assert not os.path.exists(project.correction())` and the mask file's `st_mtime_ns` equals its
  value before the call.

- [ ] **Step 2: Run, expect failure**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_correct_controls.py tests/test_correct_editor.py tests/test_shell_chrome_topbar.py tests/test_correct_main_window.py tests/test_correct_tab_order.py -v`
Expected: the new tests FAIL (`no attribute 'set_image_actions'`, Save always enabled,
`clear_button` still present).

- [ ] **Step 3: Implement.** `controls.py`: add `set_image_actions` as specified (use `_retip`),
  call `self.set_image_actions(False, False)` at the end of `__init__`, remove the text Clear
  and `_CLEAR_TEXTS`, rewrite the docstring lines about the top-bar button (`:21`) and add
  "Save and Clear are disabled with no image; Save only while something is unsaved". `editor.py`:
  `self.save_button = controls.save_button` at `:147`; in `_refresh` add
  `controls.set_image_actions(bool(self.session.image_path), self.session.is_dirty())`
  (one line, 445 lines or fewer). `shell_chrome.py`: `"Correct": ()` with the comment
  "image actions live in the dock" (keep any non-Clear widget Step 0 found there).

- [ ] **Step 4: Ctrl+S guard (only if the conditional test fails).** If, after Wave G,
  `CorrectEditor.save()` with nothing unsaved still writes (the `or [active_id]` fallback in
  `CorrectSession.save` was not removed by WG-T2 / WG-T3), make `CorrectEditor.save()` return
  `False` without calling the session when `not self.session.is_dirty()`; the Save button is
  disabled in that state, so the shortcut must agree. If the test passes, skip this step and
  say so in the report.

- [ ] **Step 5: Run, expect pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_correct_controls.py tests/test_correct_editor.py tests/test_shell_chrome_topbar.py tests/test_correct_main_window.py tests/test_correct_tab_order.py tests/test_correct_goldens.py tests/test_correct_layering.py tests/test_shell_window_split_surface.py -v`
Expected: all PASS; `test_dock_and_panel_clicks_leave_the_keys_on_the_canvas`,
`test_the_dock_keeps_its_place_across_tools_and_layers` and the goldens stay green.

- [ ] **Step 6: Docs.** `README.md:220-222`: if it still says "With nothing unsaved it saves the
  active layer", replace with "**Save** is available only while something is unsaved". One
  `CHANGELOG.md` `### Changed` bullet.

- [ ] **Step 7: Ruff** on `app/gui/correct/controls.py app/gui/correct/editor.py app/gui/shell_chrome.py` and the touched tests.

- [ ] **Step 8: Commit (lead):** `git add` the task's files;
  `git commit -m "feat(editors): Save is filled only while something is unsaved; one Clear, in the dock"`

---

## Task W4-T2: A toast when a smoothed, uncommitted pick is dropped (E3, O20)

**Model / effort:** implementer sonnet / medium; reviewer sonnet / low. **Lane:** L-correct,
Phase C, after W4-T1. **Sci:** no.

**Rule kept:** a branch picked with Select and left as it was is not an unsaved change
(`PolylineDraft.plain_pick`). The new toast fires only when Smooth was switched **on during
that pick** (the preview was smoothed by a key press the user made) and the pick is dropped by
leaving it: `PgDn` / `PgUp` or opening another image. Not on Esc, a tool change, sticky Smooth
left on from before the pick, or `S` pressed twice.

**Files:**
- Modify: `app/gui/skeleton_tools.py` (`PolylineDraft` `:94-142`, `SelectTool.press` `:216`)
- Modify: `app/gui/correct/skeleton_layer.py` (`_drop_transient` `:201-205` and its callers
  `:182, :190, :196, :248`)
- Modify: `app/gui/correct/editor.py` (`navigate`, `:268-269`), `CHANGELOG.md`
- Test: extend `tests/test_correct_pick_paging_and_draft_discard.py`

**Interfaces:**
- Produces `PolylineDraft.smooth_on_pick: bool = False` (set where `edit_original` is set),
  `PolylineDraft.unapplied_smooth() -> bool` (`plain_pick() and smooth and not smooth_on_pick`),
  `PolylineDraft.SMOOTH_DROPPED_NOTICE: ClassVar[str] = "Smoothed branch not applied · pick it
  and press Enter to apply"`, and `SkeletonLayer.drop_transient()` (the renamed
  `_drop_transient`, public so `navigate` can call it).
- Line budget: `skeleton_layer.py` is at 448; the change must be 2 lines or fewer net (the
  constant lives on `PolylineDraft`, which has no ratchet).

- [ ] **Step 0: Re-check at dispatch.** Wave G / W4-T1 may have moved these lines: re-read
  `_drop_transient`, `navigate` and `SelectTool.press`; confirm the notice for a lost new line
  (`DRAFT_DISCARDED_NOTICE`) is still produced only by `holds_work()`; recount
  `skeleton_layer.py` lines.

- [ ] **Step 1: Write the failing tests** (helpers `pick`, `record`, `answers`, `project`,
  `make_editor` exist in the file; `answers` fails the test on any prompt)

```python
SMOOTH_TOAST = ("Smoothed branch not applied · pick it and press Enter to apply", "info")

def test_a_tree_click_after_pick_and_smooth_toasts_once_and_asks_nothing(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    editor.layers[SKELETON].set_smooth(True)                 # what the S key does
    notices = record(editor.notice)
    editor.load_image(project.b, project.folder)             # what MainWindow does on a tree click
    assert notices.count(SMOOTH_TOAST) == 1 and answers.seen == []

def test_paging_after_pick_and_smooth_toasts_once_and_moves_on(make_editor, project, answers):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    editor.layers[SKELETON].set_smooth(True)
    moved, notices = record(editor.navigate_requested), record(editor.notice)
    editor.navigate(1)
    assert moved == [1] and notices == [SMOOTH_TOAST] and answers.seen == []
    editor.load_image(project.b, project.folder)             # the pick is gone: no second toast
    assert notices.count(SMOOTH_TOAST) == 1

def test_a_pick_with_smooth_already_on_says_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    editor.layers[SKELETON].set_smooth(True)                 # sticky, before the pick
    pick(editor)
    notices = record(editor.notice)
    editor.navigate(1)
    assert notices == []

def test_smooth_switched_on_and_off_again_says_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    skeleton = editor.layers[SKELETON]
    skeleton.set_smooth(True); skeleton.set_smooth(False)
    notices = record(editor.notice)
    editor.navigate(1)
    assert notices == []

def test_escape_after_pick_and_smooth_says_nothing(make_editor, project):
    editor = make_editor(project.a, project.folder)
    pick(editor)
    editor.layers[SKELETON].set_smooth(True)
    notices = record(editor.notice)
    editor.layers[SKELETON].cancel_transient()               # Esc
    editor.navigate(1)
    assert notices == []
```
  Existing tests that must stay green: `test_paging_after_a_plain_pick_drops_the_pick_and_moves_on`,
  `test_the_next_image_opens_without_a_dropped_draft_notice`,
  `test_a_tree_click_after_a_plain_pick_says_nothing_about_a_draft`,
  `test_paging_after_a_pick_whose_points_moved_still_stays`, and
  `tests/test_skeleton_draft_unsaved.py` (pending rule unchanged).

- [ ] **Step 2: Run, expect failure**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_correct_pick_paging_and_draft_discard.py -v`
Expected: the two toast tests FAIL (`notices` is empty); the other three new tests PASS.

- [ ] **Step 3: Implement.** `skeleton_tools.py`: the field, the method and the class
  constant on `PolylineDraft`; in `SelectTool.press` set
  `host.draft.smooth_on_pick = host.draft.smooth` next to `edit_original = branch`.
  `skeleton_layer.py`: rename `_drop_transient` to `drop_transient` (four callers) and change
  its body to read `holds_work()` and `unapplied_smooth()` before `cancel_transient()`, then
  `notify(DRAFT_DISCARDED_NOTICE if dropped else SMOOTH_DROPPED_NOTICE, "info")` when either
  is true. `editor.py::navigate`: the plain-pick branch calls
  `self._skeleton.drop_transient()` instead of `cancel_polyline()` (same effect plus the toast).

- [ ] **Step 4: Run, expect pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; ./venv/Scripts/python.exe -m pytest tests/test_correct_pick_paging_and_draft_discard.py tests/test_skeleton_draft_unsaved.py tests/test_correct_layering.py tests/test_correct_skeleton_goldens.py -v`
Expected: all PASS; `skeleton_layer.py` and `editor.py` at 450 lines or fewer.

- [ ] **Step 5: Ruff** on `app/gui/skeleton_tools.py app/gui/correct/skeleton_layer.py app/gui/correct/editor.py tests/test_correct_pick_paging_and_draft_discard.py`; one `CHANGELOG.md` `### Added` bullet.

- [ ] **Step 6: Commit (lead):**
  `git commit -m "feat(correct): say when a smoothed, uncommitted pick is dropped"` after
  `git add` of the task's files.

---

## Self-review (run 2026-10-25)

**Spec coverage.** W4-T5: switch in the popover (Steps 7), `deblock(img, threshold=14)`
(Step 3), default off, `.jpg` / `.jpeg` only (`is_jpeg_path`, hidden box, PNG test), key
`jpeg_deblock`, first inside `enhance()` (Step 1 `test_enhance_runs_the_deblock_first...`).
Memo's 8 items: 1 deblock cases (five core tests), 2 settings (`test_deblock_setting_defaults...`),
3 display-only goldens (`test_the_correct_goldens_replay...`; GT bytes/exports by the import
scan, box-on GT golden after Wave G), 4 import boundary (AST scan incl. relative imports),
5 torch-free (`test_deblock_core_imports_no_torch_or_qt`), 6 speed guard (gated by
`SPROUTS_PERF_TESTS=1`), 7 UI (offered / saved / original bypass / preview), 8 cross-platform
(core only needs numpy + cv2; CI ubuntu runs the core tests; the manual macOS smoke stays an
owner/WX item, not a task here). W4-T6: names, titles, legend, ISO hover, "Position (L)", ramp,
indexing over all dates, golden re-record. W4-T1: states, single Clear, dock width, draft,
no quiet state and why. W4-T2: toast cases from E3 (4 cases) plus the sticky-Smooth case.

**Placeholders / step scan.** The two `_oklab` / `_delta` helpers and `_blocky_jpeg_instead_of`
are described by what they compute (the signatures and inputs fix them); no step says "handle
edge cases".
**Type consistency.** `set_source_path` (T5 Step 7 and 11), `set_image_actions(has_image,
unsaved)` (T1), `drop_transient` (T2), `date_colors(n)` / `DATE_RAMP` (T6) are named the same
wherever they appear. `deblock_check` is the only widget name for the box.

**Review Focus.** Five lines, each with a test: PNG + saved default (T5), box without CLAHE
(T5), two-point polyline / no image / `reset()` (T1), 13+ dates and a tube missing a date
(T6), sticky Smooth (T2).

**Proportion.** Four small tasks; code bodies appear only for `deblock`'s taps (described, not
written), and tests.

**Open points for the lead.**
1. T5 Step 11 edits one line of `app/gui/correct/editor.py` (L-correct). Dispatch it between
   L-correct tasks, or let the lead apply it; until then the box is simply hidden in Correct.
2. T1 and T2 need Wave G landed; their Step 0 lists the names to re-verify
   (dock buttons, the Correct page tuple, `_refresh`, `CorrectSession.save`'s fallback,
   ported tests that mention `clear_button` / `save_button`).
3. T6: if `date_colors(n)` for some `n > 13` lands within 10 of an accent colour, the
   implementer reports it instead of altering the owner-approved list.
