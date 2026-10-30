# Wave 1: Stop Data Loss — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Read `00-master.md` (this folder) first.

**Goal:** Close every Wave 1 path that loses or misplaces a user's file: a job writing into
another project (W1-T1), a mask the editor never showed being deleted or replaced (W1-T2), an
unedited skeleton saved as a "correction" (W1-T3), a file changed on disk being overwritten
silently (W1-T4), case-variant stems and unsupported formats splitting one image into two
(W1-T5), and the missing pix2pix licence notice (W1-T7).

**Architecture:** W1-T1 binds the chained skeleton pass to the mask pass's folder and refuses
folder changes during a job (and jobs during a folder load). W1-T2 to W1-T4 add one Qt-free
module, `app/gui/correct/save_gate.py`, that the `CorrectSession` asks before every layer save
(the "not shown" and "changed on disk" questions) and on Discard; layers opt in through one
optional protocol in `app/gui/correct/layer.py` (`DiskGuardedLayer`), which the future GT
classes layer (WG-T4) implements unchanged. W1-T3 moves the thinning helpers into the Qt-free
`app/editing/thinning.py` so Measure can later import `is_generated_as_opened`. W1-T5 adds
`app.naming.stem_key` and keys every scan by it.

**Tech Stack:** Python 3.10, PyQt6, numpy, OpenCV, scikit-image (lazy), pytest, ruff.

**Spec:** `docs/superpowers/plans/2026-10-25-owner-decisions/00-master.md` (sections 1-7) and
`docs/superpowers/plans/2026-10-12-known-issues-resolution.md` (section 2 = owner answers,
section 3 "Wave 1"). Evidence: `.claude/handoff/issue-audit-2026-10-12/` (`reproducibility.md`
R3, R4, R7; `mask-calibration.md` M1; `length.md` L2; `correct-ux.md` E2; `repo-docs.md` F1;
scripts in `scripts/<package>/`).

## Global Constraints

- See `00-master.md` section 2. In addition, for this wave:
- Line cap 450 per `app/gui/correct/*.py` (`tests/test_correct_layering.py`). At `19f3761`:
  `skeleton_layer.py` 448, `editor.py` 444, `session.py` 426, `mask_layer.py` 418,
  `skeleton_document.py` 362, `layer.py` 321, `mask_document.py` 317. Each task below says
  where its lines go; a task that would cross the cap moves code into `save_gate.py`.
- Cross-lane file order (not in the master's section 5, see the closing note): W1-T4's edit of
  `app/gui/main_window.py` starts after W1-T1 is committed; W1-T5's edit of
  `app/gui/window/project_loader.py` starts after W1-T1 is committed; W1-T7's edit of
  `app/inference/skeleton_inference.py` starts after W1-T5 is committed.
- `tests/data/correct_goldens.json`: only W1-T3 edits it, one block
  (`cases.synth_blank_png.skeleton_clear.saved`), because O2 removes the no-op file it records.
  No pixel hash changes.
- Run tests as `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest <files> -v`
  (written below as `PYTEST <files>`). Ruff: `./venv/Scripts/python.exe -m ruff check --fix <files>`
  then `./venv/Scripts/python.exe -m ruff format <files>` (written `RUFF <files>`).

## Review Focus

Master section 3 items that fall in Wave 1, each pinned by a test in its owning task:

1. **A photo that is not 640 x 480** (master item 2): an editor-saved mask of another size, or
   an undecodable one, is shown as "Mask not shown"; an untouched `Ctrl+S` keeps the file byte
   for byte; painting asks before replacing; `PgDn` stays. Pinned by W1-T2
   `tests/test_correct_unloaded_mask.py::test_a_2048_photo_never_loses_its_unshown_mask`.
2. **A second folder opened while a job runs** (item 3): refused with
   "Generating masks is running. Cancel it or wait before opening another folder."; the chained
   skeleton pass writes into the mask pass's folder. Pinned by W1-T1
   `tests/test_generation_job_folder_binding.py::test_the_skeleton_pass_writes_into_the_mask_pass_folder`
   and `tests/test_library_main_window.py::test_open_path_is_refused_while_a_job_runs`.
3. **Case-variant stems, or a `.tif` next to a `.png`** (item 4): one Library entry, one set of
   outputs from the ranked winner, every run; unsupported files reported on load. Pinned by
   W1-T5 `tests/test_stem_case_and_formats.py` (`test_case_variant_stems_give_one_image_and_one_output_every_run`,
   `test_mixed_formats_give_the_same_inputs_to_library_masks_and_skeletons`,
   `test_the_load_toast_names_hidden_and_unsupported_files`).
4. **A file rewritten on disk while open with unsaved edits** (item 5): Save asks
   (Overwrite / Reload / Cancel), equal pixels ask nothing, Discard reloads, an unedited layer
   follows a finished Generate. Pinned by W1-T4 `tests/test_correct_changed_on_disk.py` and
   `tests/test_correct_main_window.py::test_the_open_image_follows_a_finished_generate_run`.
5. **An unedited skeleton saved with `Ctrl+S`** (O2, the L2 half of the old item 5): nothing
   is written; a correction edited back to the generated skeleton is removed and the Library
   drops its mark. Pinned by W1-T3 `tests/test_skeleton_save_only_when_changed.py` and
   `tests/test_shell_library_corrections.py::test_a_correction_edited_back_to_the_generated_one_leaves_the_library`.

---

### Task W1-T1: A job belongs to the folder it started in (R7, O15)

Implementer **opus / high** · Reviewer **opus / medium** · Lane **L-job** · Sci: no.

**Decisions (from the code):**
- The chain is bound in `MainWindow.generate_all`: it reads `mask_generation_handler.project_folder()`
  once (the same synchronous value `generate_masks` reads) and passes it to the skeleton pass.
- `main_window.jobs.is_busy()` / `running_name()` is the check (`app/handlers/job_registry.py:76-82`).
  The refusal sits at the top of `open_path` (before the unsaved-edits prompt and the loading
  card) and of `load_images` (before the folder dialog), so the progress row keeps the job's
  name and Cancel.
- "No job while a folder loads" reads `MainWindow._loading_project` (set in `open_path`, cleared in
  `on_loading_finished` / `end_failed_load`) through one `HandlerDependencies` method, checked in
  both `_start`s (covers Generate, Measure and Measure retry) and at the top of `generate_masks`
  (so the overwrite prompt never shows for a refused run).
- No new `MainWindow` name: `tests/test_shell_window_split_surface.py` stays unchanged
  (`generate_all` keeps `()`).

**Files:**
- Modify: `app/gui/main_window.py:598-607` (`generate_all`), `:208-213` (warm-up comment:
  scikit-image "~0.2 s" → "about 0.2 s from a warm disk cache, 1.8 to 2.5 s in the logged
  launches")
- Modify: `app/gui/window/project_loader.py:40-45` (`load_images`), `:48-98` (`open_path`)
- Modify: `app/handlers/deps.py:40-72` (`HandlerDependencies`)
- Modify: `app/handlers/generation_base.py:85-107` (`_start`)
- Modify: `app/handlers/skeleton_handler.py:69-70` (`generate_skeleton`), `:145-161` (`_start`)
- Modify: `app/handlers/generate_skeleton_handler.py:111-142` (`generate_skeleton`)
- Modify: `app/handlers/mask_generation_handler.py:215-235` (`generate_masks`, refusal before the scan)
- Modify: `CHANGELOG.md` `[Unreleased]` → `### Fixed` (one bullet)
- Test: Create `tests/test_generation_job_folder_binding.py`; Modify `tests/test_library_main_window.py` (append two tests)

**Interfaces:**
- Produces: `GenerateSkeletonHandler.generate_skeleton(self, input_dir: str | None = None) -> bool`
  (None = today: the open project, else the folder dialog);
  `SkeletonHandler.generate_skeleton(self, input_dir: str | None = None) -> bool` (passes through);
  `HandlerDependencies.refuse_while_loading(self) -> bool` (True + warn toast
  `"A folder is loading. Try again when it has loaded."` while `main_window._loading_project`);
  `project_loader.JOB_RUNNING_OPEN = "{name} is running. Cancel it or wait before opening another folder."`.

- [ ] **Step 1: Write the failing tests**

`tests/test_generation_job_folder_binding.py` reuses `project`, `qapp`, `_wait` from
`tests/test_generation_handlers.py` (import them; `# noqa: F401` on the fixtures).

```python
def test_the_skeleton_pass_writes_into_the_mask_pass_folder(project, qapp, tmp_path):
    # window gets jobs=JobRegistry(), mask_generation_handler=MaskGenerationHandler(window),
    # skeleton_handler=SkeletonHandler(window, generation=GenerateSkeletonHandler(window));
    # folder B (2 images) is opened (window.image_manager swapped) from the stub model's
    # first forward, i.e. while the mask pass runs.
    assert MainWindow.generate_all(window) is True
    # wait for the mask thread, then for the chained skeleton thread
    assert sorted(pngs(A / "skeletons")) == ["a_fake.png", "b_fake.png", "c_fake.png"]
    assert not (B / "skeletons").exists()

def test_generate_skeleton_takes_the_folder_it_is_given(project, qapp, tmp_path):
    handler = gsh.GenerateSkeletonHandler(project.window)  # window shows folder A
    assert handler.generate_skeleton(input_dir=str(B)) is True
    # after _wait: B/skeletons has B's two *_fake.png, A has no skeletons/ folder

@pytest.mark.parametrize("start", ["masks", "skeletons", "measure_length"])
def test_no_job_starts_while_a_folder_loads(project, qapp, start):
    project.window._loading_project = True
    # masks: MaskGenerationHandler(window).generate_masks() is False and no prompt was asked
    # skeletons: GenerateSkeletonHandler(window).generate_skeleton() is False
    # measure_length: SkeletonHandler(window)._start(MeasureRun(LENGTH, {...}, folder)) is False
    notes = [c.args for c in project.window.feedback.calls if c.name == "notify"]
    assert notes[-1] == ("A folder is loading. Try again when it has loaded.", "warn")
```

Append to `tests/test_library_main_window.py`:

```python
def test_open_path_is_refused_while_a_job_runs(mw, tmp_path, monkeypatch):
    _open(mw, _project(tmp_path / "a"))
    monkeypatch.setattr(mw.jobs, "is_busy", lambda: True)
    monkeypatch.setattr(mw.jobs, "running_name", lambda: "Generating masks")
    mw.task_progress.start("Generating masks", on_cancel=lambda: None)
    mw.open_path(str(_project(tmp_path / "b", STEMS[:2])))
    QTest.qWait(50)
    assert ("warn", "Generating masks is running. Cancel it or wait before opening another folder.") in mw.toasts_seen
    assert os.path.normcase(mw.image_manager.original_folder) == os.path.normcase(str(tmp_path / "a"))
    assert mw.task_progress._operation == "Generating masks"
    assert not mw.task_progress.cancel_button.isHidden()

def test_the_folder_dialog_is_not_opened_while_a_job_runs(mw, monkeypatch):
    # same two monkeypatches; QFileDialog.getExistingDirectory -> pytest.fail
    mw.load_images()
    assert mw.toasts_seen[-1][0] == "warn"
```

- [ ] **Step 2: Run, expect failures**

Run: `PYTEST tests/test_generation_job_folder_binding.py tests/test_library_main_window.py`
Expected: FAIL — skeletons land in B (first test); `TypeError: generate_skeleton() got an
unexpected keyword argument 'input_dir'`; jobs start while loading; `open_path` loads B.

- [ ] **Step 3: Implement**

- `generate_all`: `folder = self.mask_generation_handler.project_folder()`, then
  `generate_masks(on_success=functools.partial(self.skeleton_handler.generate_skeleton, input_dir=folder))`.
- `GenerateSkeletonHandler.generate_skeleton(input_dir=None)`: `input_dir = input_dir or self.project_folder()`,
  rest unchanged. `SkeletonHandler.generate_skeleton(input_dir=None) -> bool` returns the result.
- `HandlerDependencies.refuse_while_loading()`: `getattr(self.main_window, "_loading_project", False) is True`
  → notify the text above as `"warn"`, return True. Called first in `GenerationHandlerBase._start`
  (then `thread.deleteLater()`, return False), in `SkeletonHandler._start` (same), and in
  `generate_masks` right after the model check.
- `project_loader`: `_refuse_while_job_runs(window) -> bool` (busy → `window.notify(JOB_RUNNING_OPEN.format(name=window.jobs.running_name() or "A task"), "warn")`, True);
  first statement of `load_images` and first after the empty-name check of `open_path`.
- Comment at `main_window.py:208-209` as given in Files.

- [ ] **Step 4: Run, expect pass**

Run: `PYTEST tests/test_generation_job_folder_binding.py tests/test_library_main_window.py`
Expected: PASS.

- [ ] **Step 5: Existing tests stay green**

Run: `PYTEST tests/test_generation_handlers.py tests/test_generation_all_failed.py tests/test_integration_main_window_wiring.py tests/test_shell_window_split_surface.py tests/test_jobs_cancel_cpu_fallback.py tests/test_warmup_main_window.py`
Expected: PASS.

- [ ] **Step 6: Ruff** — `RUFF` on every file listed above.

- [ ] **Step 7: Commit (lead)**

`fix(generate): bind the skeleton pass to the mask pass's folder; no folder change during a job`

CHANGELOG bullet: "Opening another folder while a job runs is refused (the job's outputs stayed
with the project it started in only for masks; the chained skeleton pass went to the newly
opened folder and replaced its skeletons). A job is refused while a folder loads."

---

### Task W1-T2: A mask the editor did not load is never removed or replaced silently (M1-A)

Implementer **opus / high** · Reviewer **opus / medium** · Lane **L-correct** · Sci: no.

**Decisions:**
- The document keeps *why* a mask file was not loaded: `MaskRead.not_loaded` replaces
  `MaskRead.mismatch` (the only other constructor is `read_back.py:76-78`, which passes `""`).
- The session asks before a layer save through a new Qt-free `SaveGate` (`save_gate.py`), so the
  W1-T4 question uses the same hook. `Prompts`, the answer constants and `SaveReport` move into
  `save_gate.py`; `session.py` re-imports them (tests import `CANCEL, DISCARD, KEEP, SAVE,
  SaveReport` from `app.gui.correct.session`), which also frees the lines W1-T4 needs.
- A user's "no" is not a failure: `SaveOutcome.declined` keeps `release()` from asking the
  failed-save question, and still makes `report.ok` False so `PgDn` and a leave stay.
- `PgDn` never prompts (`save_dirty` → `ask=False`): it stays and warns.
- Tests use an editor-saved (RGBA) mask of another size where possible, so W3-T4 (which stretches
  *model* masks) does not rewrite them; the two document tests that pin the model-mask case
  are renamed here and rewritten again by W3-T4.

**Files:**
- Create: `app/gui/correct/save_gate.py`
- Modify: `app/gui/correct/layer.py:61-70` (`SaveOutcome.declined`), after `:288` (new protocol `DiskGuardedLayer`)
- Modify: `app/gui/correct/mask_document.py:38` (texts), `:41-54` (`MaskSaveResult.kept`), `:57-76` (`MaskRead.not_loaded`), `:124-134` (`status`), `:148-200` (`read`, `open`), `:202-209` (`close`), `:223-252` (`save`)
- Modify: `app/gui/correct/mask_layer.py:406-418` (`_outcome`), add `unloaded_label`
- Modify: `app/gui/correct/session.py:24-52` (move out `Prompts`, constants, `SaveReport`), `:111-141` (construct the gate), `:283-309` (`save_dirty`, `_save`), `:327-356` (`release`)
- Modify: `app/gui/correct/read_back.py:76-78`
- Modify: `app/gui/editor_prompts.py:17-27` (`__all__`, `REPLACE`), append `ask_replace_unloaded`
- Modify: `tests/correct_fakes.py:63-180` (`FakeLayer.unloaded`, `unloaded_label`), `:372-391` (`FakePrompts`)
- Modify: `README.md:218-236` (one bullet under **Saving**), `CHANGELOG.md` `### Fixed`
- Test: Create `tests/test_correct_save_gate.py`, `tests/test_correct_unloaded_mask.py`; Modify
  `tests/test_correct_mask_document.py:134-155`, `tests/test_correct_mask_layer.py:1031-1038`,
  `tests/test_correct_layering.py:25-31` (add `"app.gui.correct.save_gate"` to `QT_FREE`)

**Interfaces:**
- Produces (consumed by W1-T3, W1-T4, W3-T4, WG-T2, WG-T4):
  - `layer.SaveOutcome(..., declined: bool = False)` — the user declined the write; nothing written.
  - `layer.DiskGuardedLayer(Protocol)` with `unloaded_label(self) -> str`: the project-relative
    file (`"mask/a.png"`, `/` separators) that a save would replace without having shown it,
    `""` when none (also `""` while the plane is empty: that save keeps the file).
  - `mask_document.MaskRead.not_loaded: str` — `""`, `"size"` or `"unreadable"` (replaces `mismatch`).
  - `MaskDocument.unloaded_file(self) -> bool`; `MaskSaveResult(..., kept: bool = False)`.
  - `save_gate`: `SAVE, DISCARD, CANCEL, KEEP, REPLACE = "save", "discard", "cancel", "keep", "replace"`;
    `class Prompts(Protocol)` (`ask_unsaved`, `ask_after_failed_save`, and
    `ask_replace_unloaded(self, parent: object, noun: str, label: str) -> str` → REPLACE / CANCEL);
    `class SaveReport` (as today, plus `declined -> tuple[str, ...]`; `failed` excludes declined);
    `def unloaded_label_of(layer: object) -> str` (the method, else `""`);
    `class SaveGate` with `__init__(self, prompts: Prompts, parent: Callable[[], object])` and
    `before_save(self, layer_id: str, layer: EditLayer, *, ask: bool) -> SaveOutcome | None`
    (None = go ahead and save).
  - `editor_prompts.ask_replace_unloaded(parent: QWidget | None, noun: str, label: str) -> str`.
- Exact texts:
  - open notice / status tooltip, `"size"`: `"Saved mask size does not match the image: it is not shown and is kept unless you save over it"`;
    `"unreadable"`: `"Saved mask could not be read: it is not shown and is kept unless you save over it"` (both `"warn"`).
  - status: `LayerStatus("error", "Mask not shown", <that text>)` while clean and not loaded.
  - kept save: `SaveOutcome("mask", ok=True, message="Nothing to save: the saved mask was kept", kind="info")`.
  - prompt: title `"Replace the saved mask?"` (noun title-cased), text `"mask/a.png exists but could not be shown. Replace it?"`,
    info `"Your edits replace the whole file. Keep it leaves the file as it is."`; buttons `Save` renamed `"Replace"`,
    `Cancel` renamed `"Keep it"`; default and Esc = Cancel.
  - declined after Keep it: `("Mask not saved: mask/a.png was kept", "info")`; on `PgDn`:
    `("mask/a.png could not be shown: save with Ctrl+S to replace it", "warn")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_correct_save_gate.py` (session with `FakeLayer` / `FakePrompts`, like `tests/test_correct_session.py`):

```python
def test_a_save_over_an_unloaded_file_asks_and_keep_it_declines():
    # mask FakeLayer: dirty=True, unloaded="mask/a.png"; FakePrompts(replace=["cancel"])
    report = session.save(MASK)
    assert "save" not in mask.names()
    assert report.outcomes == (SaveOutcome(MASK, ok=False, declined=True, message="Mask not saved: mask/a.png was kept", kind="info"),)
    assert prompts.asked == [("replace", parent, "mask", "mask/a.png")]
    assert report.declined == (MASK,) and report.failed == ()

def test_replace_saves_the_layer():                    # replace=["replace"] -> "save" in names, report.ok
def test_pgdn_never_asks_and_stays():                  # save_dirty(): asked == [], declined outcome
    # message == "mask/a.png could not be shown: save with Ctrl+S to replace it", kind "warn"
def test_a_declined_save_in_the_leave_prompt_stays_without_the_failed_save_question():
    # FakePrompts(unsaved=["save"], replace=["cancel"]); release() is False;
    # [a[0] for a in prompts.asked] == ["unsaved", "replace"]
def test_a_layer_without_the_protocol_is_saved_as_today():  # no unloaded attr -> no question
```

`tests/test_correct_mask_document.py` (rename + rewrite the two tests; `WRONG_SIZE` becomes the new text):

```python
def test_a_mask_of_another_size_is_not_shown_and_is_kept(tmp_path):   # was ..._opens_empty_with_a_warning
    assert doc.open(_image(tmp_path), (100, 50)) == [Notice(NOT_SHOWN_SIZE, "warn")]
    assert not doc.history.plane.any() and doc.unloaded_file() and not doc.is_dirty()
    assert doc.status() == LayerStatus("error", "Mask not shown", NOT_SHOWN_SIZE)
    assert doc.save() == MaskSaveResult(ok=True, kept=True)
    assert file_bytes == before                                         # nothing written or removed

def test_an_undecodable_mask_is_not_shown_says_so_and_is_kept(tmp_path):  # was test_undecodable_mask_opens_empty
    assert doc.open(_image(tmp_path), SIZE) == [Notice(NOT_SHOWN_UNREADABLE, "warn")]
    # same three asserts as above, with NOT_SHOWN_UNREADABLE

def test_painting_over_an_unshown_mask_then_saving_replaces_it(tmp_path):
    # _paint, save() -> ok, path == mask path; unloaded_file() is False; status "clean"
```

`tests/test_correct_mask_layer.py::test_open_hands_back_the_documents_notices` — expected notice
becomes `Notice(NOT_SHOWN_SIZE_TEXT, "warn")`; add
`test_unloaded_label_names_the_file_only_while_something_is_painted` (`""` after open, `"mask/<stem>.png"` after one brush stroke).

`tests/test_correct_unloaded_mask.py` (reuses `make_editor`, `answers`, `paint`, `record`,
`write_image` and the guards from `tests/test_correct_editor.py`; photo 2048 x 1536, mask written
with `app.masks.write_mask_file` at 640 x 480):

```python
def test_a_2048_photo_never_loses_its_unshown_mask(make_editor, tmp_path, answers):
    editor = make_editor(photo, folder)
    assert editor.layers[MASK].status() == LayerStatus("error", "Mask not shown", NOT_SHOWN_SIZE)
    assert editor.save() is True and read_bytes(mask) == before                # untouched Ctrl+S
    paint(editor, (100, 100), (300, 300)); answers.will(CANCEL)
    assert editor.save() is False and read_bytes(mask) == before
    assert answers.seen[-1] == ("Replace the saved mask?", "mask/a.png exists but could not be shown. Replace it?")
    moved = record(editor.navigate_requested); editor.navigate(1)
    assert moved == [] and read_bytes(mask) == before                          # PgDn stays
    answers.will(SAVE)                                                         # "Replace"
    assert editor.save() is True and cv2.imread(mask, cv2.IMREAD_UNCHANGED).shape[:2] == (1536, 2048)

def test_an_undecodable_mask_gives_a_notice_and_survives_ctrl_s(make_editor, tmp_path):
```

- [ ] **Step 2: Run, expect failures**

Run: `PYTEST tests/test_correct_save_gate.py tests/test_correct_unloaded_mask.py tests/test_correct_mask_document.py tests/test_correct_mask_layer.py tests/test_correct_layering.py`
Expected: FAIL — `ModuleNotFoundError: app.gui.correct.save_gate`, `TypeError` on `declined` / `kept`,
the mask file removed by the untouched `Ctrl+S`.

- [ ] **Step 3: Implement**

- `mask_document`: `read` sets `not_loaded="unreadable"` on `MaskFileError` when a candidate file
  exists, `"size"` on a shape mismatch; `open` stores it (`self._not_loaded`), returns the
  matching notice; `close` clears it; `status` returns the error state when clean and not loaded
  (a save error still wins, dirty still wins); `save` with an empty plane while not loaded returns
  `MaskSaveResult(ok=True, kept=True)` and touches no file; a successful write clears `_not_loaded`.
- `MaskLayer.unloaded_label()`: `""` unless `document.unloaded_file()` and the plane has a set
  pixel; else the first existing path of `document.file_stamps()` relative to the image's folder,
  `os.sep` → `/`. `_outcome`: `kept` → the info outcome above.
- `SaveGate.before_save`: `label = unloaded_label_of(layer)`; none → None; `ask=False` → the
  `PgDn` declined outcome; else `prompts.ask_replace_unloaded(parent(), layer.noun, label)`:
  REPLACE → None, anything else → the "kept" declined outcome.
- `CorrectSession._save(layer_ids, *, ask: bool)`: per layer, after `finish_gesture()`, `gate.before_save(...)`;
  an outcome is reported (`_on_saved`, `_announce`) instead of calling `layer.save()`.
  `save()` and `release()` pass `ask=True`, `save_dirty()` `ask=False`. `release()` after a Save
  answer: if `report.failed`, ask the failed-save question about those layers only (as today);
  then return False whenever `report.declined` is non-empty, else what the failed-save answer
  gives (as today).
- `editor_prompts.ask_replace_unloaded` via `_ask(..., rename={Save: "Replace", Cancel: "Keep it"})`.
- `FakeLayer`: `self.unloaded = ""` and `unloaded_label()` returning it; `FakePrompts(unsaved=(), after_failed=(), replace=())`,
  `ask_replace_unloaded` records `("replace", parent, noun, label)`, default `"cancel"`.
- README **Saving** bullet: "A saved mask that *Correct* cannot show (another size than the photo,
  or a file it cannot read) shows *Mask not shown* and is kept: `Ctrl+S` with nothing painted
  leaves it, painting over it asks before replacing it, and `PgDn` stays on the image."

- [ ] **Step 4: Run, expect pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Existing tests stay green**

Run: `PYTEST tests/test_correct_session.py tests/test_correct_session_paging.py tests/test_correct_editor.py tests/test_correct_editor_prompt_flow.py tests/test_correct_read_ahead_handback.py tests/test_correct_goldens.py tests/test_correct_mask_goldens.py tests/test_correct_controls.py tests/test_correct_main_window.py`
Expected: PASS (`test_an_unusable_mask_file_is_read_again_and_warned_about_again` still sees one warn per open).

- [ ] **Step 6: Ruff** — `RUFF` on the files above. `wc -l app/gui/correct/*.py`: none over 450.

- [ ] **Step 7: Commit (lead)**

`fix(correct): a mask that could not be shown is never removed or replaced silently`

---

### Task W1-T3: Skeleton Save writes only when it differs from the generated one (L2, O2)

Implementer **opus / high** · Reviewer **opus / medium** · Lane **L-correct** (after W1-T2) · Sci: no
(no CSV value; the no-op correction file is no longer created).

**Decisions:**
- **Placement of `is_generated_as_opened`.** `tests/test_layering_contract.py::test_lower_layers_do_not_import_gui_or_handlers`
  forbids `app/inference` importing `app.gui.*`, and `thin_raster` lives in
  `app/gui/skeleton_graph_model.py:263-289`. So `thin_raster` and what it needs
  (`_skimage_skeletonize`, `_skeletonize_fn`, `prewarm_skeletonize`, `_ensure_uint8_binary`,
  `_skeletonize_uint8`, `skeleton_graph_model.py:56-76` and `:101-114`) move to the new Qt-free
  `app/editing/thinning.py`, with the helper beside them. `skeleton_graph_model` imports them back
  under the same names, so `sgm.prewarm_skeletonize`, `sgm.thin_raster`, `sgm._skeletonize_uint8`
  (patched by `tests/test_skeleton_roi.py:98`) and `tests/test_skeleton_model_editing.py`'s lazy-import
  checks keep working. WG-T3 / WG-T8 import `app.editing.thinning`.
- No cache of the thinned generated skeleton: `thin_raster` costs 8.8 ms at 640 x 480 and 114 ms at
  2048 x 1536 (measured on this machine), once per Save of a *dirty* skeleton.
- A clean skeleton is never written (`unchanged`); a dirty one equal to the generated skeleton
  writes nothing and removes an existing correction (`removed`).
- The Library's corrected mark already follows the disk on every dirty → clean change
  (`app/gui/window/editors.py:74-101`, `sync_open_correction`), so `editor.py::_report` needs no
  change (old plan's item dropped). The Save tooltip (`controls.py:221`, "Save the unsaved layers")
  is already right (dropped too).
- `skeleton_layer.py` is at 448 lines: the save-outcome texts move into
  `SkeletonDocument.outcome(result) -> SaveOutcome`, which shortens `SkeletonLayer.save`.
- **Golden change (deliberate):** `synth_blank_png / skeleton_clear` clears an empty skeleton
  (no generated file) and saves; O2 makes that a no-op, so its recorded `saved` block becomes
  `{"exists": false}`. Every pixel hash stays.

**Files:**
- Create: `app/editing/thinning.py`
- Modify: `app/gui/skeleton_graph_model.py:17-20` (docstring pointer), `:56-76`, `:101-114`, `:263-289` (moved out, re-imported)
- Modify: `app/gui/correct/skeleton_document.py:28-31` (imports), `:48-58` (`SkeletonSaveResult`), `:280-306` (`save`), add `outcome`
- Modify: `app/gui/correct/skeleton_layer.py:215-226` (`save`), `:23` (imports)
- Modify: `app/gui/correct/layer.py:68` (comment: `cleared` = an empty mask removed the file, or a skeleton correction was removed)
- Modify: `tests/data/correct_goldens.json` (`cases.synth_blank_png.skeleton_clear.saved` → `{"exists": false}`)
- Modify: `README.md:220-222`, `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md:670` (D7: append "Amended 2026-10-25 (O2): the skeleton is written only when it differs from the generated one."), `CHANGELOG.md` `### Changed`
- Test: Create `tests/test_skeleton_save_only_when_changed.py`, `tests/test_editing_thinning.py`; Modify
  `tests/test_correct_editor.py:1187-1197`, `tests/test_shell_library_corrections.py` (append),
  `tests/test_correct_layering.py:25-31` (add `"app.editing.thinning"` to `QT_FREE`)

**Interfaces:**
- Consumes: W1-T2's `SaveOutcome` (fields unchanged here).
- Produces:
  - `app.editing.thinning.thin_raster(raster_gray: np.ndarray, target_size: tuple[int, int], threshold: str = "otsu") -> np.ndarray` (moved, unchanged)
  - `app.editing.thinning.prewarm_skeletonize() -> None` (moved)
  - `app.editing.thinning.is_generated_as_opened(mask: np.ndarray, generated_path: str | None, photo_size: tuple[int, int]) -> bool`:
    True when `mask > 0` equals `thin_raster(gray, photo_size) > 0` for the generated file read with
    `app.io_utils.imread_unicode(path, cv2.IMREAD_GRAYSCALE)`; a missing path, a missing file or an
    unreadable one counts as "empty" (`not mask.any()`), as Correct opens it. `photo_size` is `(w, h)`.
  - `SkeletonSaveResult(ok: bool, path: str | None = None, error: str = "", unchanged: bool = False, removed: bool = False)`
  - `SkeletonDocument.outcome(self, result: SkeletonSaveResult) -> SaveOutcome`
- Exact texts: unchanged → `SaveOutcome("skeleton", ok=True, message="Skeleton unchanged: nothing to save", kind="info")`;
  removed → `SaveOutcome("skeleton", ok=True, cleared=True, message="Skeleton is the generated one again: correction removed", kind="info")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_editing_thinning.py
def test_the_thinning_helpers_live_in_app_editing_and_load_no_qt():
    # subprocess probe as in tests/test_correct_layering.py: import app.editing.thinning,
    # no PyQt6 and no skimage in sys.modules
def test_skeleton_graph_model_reexports_the_same_functions():
    assert sgm.thin_raster is thinning.thin_raster and sgm.prewarm_skeletonize is thinning.prewarm_skeletonize
def test_is_generated_as_opened(tmp_path):
    # generated 341 x 256 file with one line; photo (640, 480)
    assert is_generated_as_opened(thin_raster(gray, (640, 480)), path, (640, 480))
    assert not is_generated_as_opened(other_mask, path, (640, 480))
    assert is_generated_as_opened(np.zeros((480, 640), np.uint8), None, (640, 480))
    assert not is_generated_as_opened(np.zeros((480, 640), np.uint8), path, (640, 480))  # emptied: a real correction
    assert is_generated_as_opened(np.zeros((480, 640), np.uint8), str(tmp_path / "missing.png"), (640, 480))
```

`tests/test_skeleton_save_only_when_changed.py` (fixtures from `tests/test_correct_editor.py`;
image `a` has a generated skeleton on row 110):

```python
def test_a_no_op_save_writes_nothing_and_says_so(make_editor, project):
    editor.set_active_layer(SKELETON); notices = record(editor.notice)
    assert editor.save() is True
    assert not os.path.exists(project.correction())
    assert notices == [("Skeleton unchanged: nothing to save", "info")]
def test_edit_undo_save_writes_nothing(make_editor, project):              # draw_line, undo, Ctrl+S
def test_a_correction_edited_back_to_the_generated_one_is_removed(make_editor, project):
    # write_skeleton(project.correction(), row=40); open; skeleton.load_file(project.generated()); Ctrl+S
    assert not os.path.exists(project.correction())
    assert ("Skeleton is the generated one again: correction removed", "info") in notices
    assert editor.layers[SKELETON].document.source == "generated" and not editor.has_unsaved_changes()
def test_an_emptied_skeleton_over_a_generated_one_is_written(make_editor, project):  # Clear, Ctrl+S -> file, on_count 0
def test_a_real_edit_is_written(make_editor, project):                      # draw_line, Ctrl+S -> file exists
```

`tests/test_correct_editor.py:1187-1197`: parametrize only `[MASK]`; the skeleton half is the
first test above.

Append to `tests/test_shell_library_corrections.py`:

```python
def test_a_correction_edited_back_to_the_generated_one_leaves_the_library(window, tmp_path):
    open_project(window, _corrected_project(tmp_path)); select(window, 0); enter(window, "Correct")
    editor = window.correct_editor
    editor.layers[SKELETON].load_file(project.generated(0)); editor.session.forget(SKELETON)
    assert editor.save(); settle()
    assert not os.path.isfile(project.correction(0))
    assert not window.image_manager.has_correction(STEMS[0])
    assert window.library_index.corrected_of(STEMS[0]) is False
```

- [ ] **Step 2: Run, expect failures**

Run: `PYTEST tests/test_editing_thinning.py tests/test_skeleton_save_only_when_changed.py tests/test_shell_library_corrections.py tests/test_correct_editor.py -k "thinning or only_when_changed or generated_one or nothing_unsaved"`
Expected: FAIL — `ModuleNotFoundError: app.editing.thinning`; the correction file is written on a no-op save.

- [ ] **Step 3: Implement**

- Move the five functions + the global into `app/editing/thinning.py` (numpy, cv2, `app.io_utils`
  only; scikit-image still imported on first use); add `is_generated_as_opened`.
- `SkeletonDocument.save()`: not dirty → `SkeletonSaveResult(True, unchanged=True)`; dirty and
  `is_generated_as_opened(self.model.mask, self.files().generated_path(), self._size)` →
  remove the correction if it exists (`OSError` → the failed path as for a write), `mark_saved()`,
  `source = GENERATED`, `save_size = None`, return `removed=existed, unchanged=not existed`;
  else today's write.
- `SkeletonDocument.outcome(result)`: the two texts above, today's success and failure texts.
  `SkeletonLayer.save()` returns `self.document.outcome(result)`.
- Golden JSON block, README sentence ("With nothing unsaved it saves the active mask (the way to
  mark a model mask as checked). A skeleton is saved only when it differs from the generated one:
  saving is not acceptance, and a skeleton edited back to the generated one removes its
  correction."), spec D7 line, CHANGELOG `### Changed` bullet.

- [ ] **Step 4: Run, expect pass** — Step 2's command without `-k`, plus `tests/test_correct_goldens.py tests/test_correct_skeleton_goldens.py`. Expected: PASS.

- [ ] **Step 5: Existing tests stay green**

Run: `PYTEST tests/test_skeleton_loaded_file_unsaved.py tests/test_correct_session.py tests/test_correct_main_window.py tests/test_correct_editor_prompt_flow.py tests/test_correct_read_ahead_handback.py tests/test_skeleton_model_editing.py tests/test_skeleton_roi.py tests/test_skeleton_item_halo.py tests/test_startup_import_budget.py tests/test_layering_contract.py tests/test_correct_layering.py tests/test_correct_perf.py`
Expected: PASS. A test that saves an *untouched* skeleton and expects a correction file breaks by
design (O2): rewrite it to the new rule and name it in the report.

- [ ] **Step 6: Ruff** — `RUFF` on the files above; `skeleton_layer.py` ≤ 450 lines.

- [ ] **Step 7: Commit (lead)**

`fix(correct): Save writes a skeleton correction only when it differs from the generated one`
Body: "Golden synth_blank_png/skeleton_clear: the no-op save no longer writes a file (O2); pixel hashes unchanged."

---

### Task W1-T4: Files changed on disk: Discard reloads, Save asks, no false alarm, the open image follows Generate (E2, O19)

Implementer **opus / xhigh** · Reviewer **opus / high** · Lane **L-correct** (after W1-T3;
its `main_window.py` line after W1-T1) · Sci: no. Correct layers only (the GT layer inherits the
contract in WG-T4).

**Decisions (the stamp contract):**
- `DiskGuardedLayer` (W1-T2) gains exactly two methods; the GT classes layer implements all three:
  - `write_stamps(self) -> tuple` — `(path, mtime_ns, size)` / `(path, None)` of every file
    `save()` writes or removes, read afresh; `()` when closed. Mask: falls back to `file_stamps()`
    (the save writes `.png` and removes the variants), so `MaskLayer` does not define it. Skeleton:
    the correction file only, so a Generate run (which changes only the generated file) never
    makes Save ask.
  - `matches_disk(self) -> bool` — True when reading the layer's files now would open exactly its
    saved state (mask: `MaskDocument.read` plane, absent = empty, equals the baseline; skeleton:
    the start file thinned at the image size, none = empty, equals the model's saved mask). This is
    the "no false alarm" check; it reads files, so it runs only after a stamp mismatch.
- Stamps are taken at open, reload, after each successful save and after each Discard (both
  branches): the session's existing `_stamps` (file stamps; `tests/test_correct_editor_prompt_flow.py:137`
  reads `session._stamps[MASK]`) and the gate's write stamps (`SaveGate.take`).
- Save (`SaveGate.before_save`, after the W1-T2 check): write stamps unchanged → go ahead.
  Changed and `matches_disk` → retake stamps, go ahead. Changed, layer clean → reload the layer,
  report ok with an info message, write nothing. Changed, dirty, `ask=False` (`PgDn`) → declined
  warn (stays). Changed, dirty, `ask=True` → `ask_changed_on_disk`: Overwrite → go ahead;
  Reload → reload the layer, declined; Cancel → declined.
- Discard (`CorrectSession._revert`): a layer whose `file_stamps()` differ from those at open or
  last save and whose files do not `matches_disk` is reopened from disk (`layer.open`) with a
  notice, its timeline entries forgotten; otherwise `layer.revert()` as today.
- No `paging.forget` after a reload (old plan item dropped): every read-ahead payload is checked
  against the files' stamps when adopted (`MaskDocument.open:184-191`, `SkeletonDocument._current`).
- The open image follows a finished Generate: `MainWindow.on_outputs_changed` calls a new part
  function in `app/gui/window/editors.py` (not bound on the window, so the surface pin is
  unchanged); while Correct is the visible page and the open image has no unsaved edits it calls
  `correct_editor.load_image(path, images_base_folder=image_manager.original_folder)`, whose
  same-image path already reloads changed files (`editor.py:210-216`). Entering Correct later does
  the same today.
- Line budget: `session.py` gets ~20 lines (W1-T2 freed ~20); `skeleton_layer.py` gets one line
  (attribute delegation like `:79`).

**Files:**
- Modify: `app/gui/correct/layer.py` (`DiskGuardedLayer`: `write_stamps`, `matches_disk`)
- Modify: `app/gui/correct/save_gate.py` (W1-T2's file)
- Modify: `app/gui/correct/session.py:135` (`_stamps` stays), `:166-219` (open / reload take both stamp sets), `:291-309` (`_save`), `:368-374` (`_revert`), add `_reopen`
- Modify: `app/gui/correct/mask_document.py` (add `matches_disk`), `app/gui/correct/mask_layer.py:152-158` (add `matches_disk`)
- Modify: `app/gui/correct/skeleton_document.py:332-358` (add `write_stamps`, `matches_disk`), `app/gui/correct/skeleton_layer.py:79` (delegate both)
- Modify: `app/gui/skeleton_graph_model.py:570-574` (read-only `saved_mask` property next to `mark_saved`)
- Modify: `app/gui/editor_prompts.py` (`OVERWRITE`, `RELOAD`, `ask_changed_on_disk`)
- Modify: `app/gui/window/editors.py` (add `follow_generated_outputs`), `app/gui/main_window.py:630-638` (`on_outputs_changed` calls it)
- Modify: `tests/correct_fakes.py` (`FakeLayer.disk_same`, `matches_disk`, `write_stamps`; `FakePrompts(changed=())`)
- Modify: `README.md:232` and **Saving** bullet, `CHANGELOG.md` `### Fixed`
- Test: Create `tests/test_correct_changed_on_disk.py`; Modify `tests/test_correct_save_gate.py`, `tests/test_correct_main_window.py` (append)

**Interfaces:**
- Consumes: W1-T2 `SaveGate`, `SaveOutcome.declined`, `Prompts`; W1-T3 `thin_raster` (skeleton `matches_disk`).
- Produces (WG-T4 implements / consumes):
  - `DiskGuardedLayer.write_stamps(self) -> tuple`, `DiskGuardedLayer.matches_disk(self) -> bool`
  - `save_gate.write_stamps_of(layer: object) -> tuple` (the method, else `layer.file_stamps()`);
    `save_gate.matches_disk_of(layer: object) -> bool` (the method, else False);
    `OVERWRITE, RELOAD = "overwrite", "reload"`; `Prompts.ask_changed_on_disk(self, parent: object, noun: str, label: str) -> str`.
  - `SaveGate.__init__(self, prompts, parent, *, reopen: Callable[[str], None], root: Callable[[], str])`;
    `SaveGate.take(self, layer_id: str, layer: EditLayer) -> None`; `SaveGate.forget(self) -> None`.
    `root()` is the project folder (`images_base_folder` or the image's folder); a label is the
    first changed write-stamp path relative to it, `/` separators.
  - `MaskDocument.matches_disk(self) -> bool`; `SkeletonDocument.write_stamps(self) -> tuple`;
    `SkeletonDocument.matches_disk(self) -> bool`; `SkeletonCorrectionModel.saved_mask -> np.ndarray | None`.
  - `editors.follow_generated_outputs(window: "MainWindow") -> None`.
- Exact texts (noun title-cased: "Mask", "Skeleton"):
  - prompt: title `"Mask changed on disk"`, text `"mask/a.png changed on disk after you opened it."`,
    info `"Overwrite it with your edits, reload it from disk (your edits are lost), or cancel."`;
    buttons `Save` → `"Overwrite"`, `Discard` → `"Reload (lose my edits)"`, `Cancel`; default and Esc = Cancel.
  - reload (clean Save, Reload answer, Discard): `("Mask reloaded: the file changed on disk", "info")`.
  - Cancel: `("Mask not saved: mask/a.png changed on disk", "info")`.
  - `PgDn`: `("mask/a.png changed on disk after you opened it: press Ctrl+S to choose", "warn")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_correct_changed_on_disk.py` (fixtures and helpers from `tests/test_correct_editor.py`;
"rewrite" = `write_mask(project.mask(), other)` + `bump(project.mask())`, `other` a box at `[5:25, 5:45]`):

```python
@pytest.mark.parametrize("layer_id", [MASK, SKELETON])
def test_discard_after_a_rewrite_shows_the_file_and_saved(make_editor, project, answers, layer_id):
    # mask: paint, rewrite; skeleton: correction on row 40 exists, draw_line, write_skeleton(correction, row=60) + bump
    answers.will(DISCARD); assert editor.maybe_save_changes() is True
    # mask: plane == other; skeleton: rows of raster == {60}
    assert editor.layers[layer_id].status().state == "clean"
    assert (f"{noun} reloaded: the file changed on disk", "info") in notices

def test_save_after_a_rewrite_asks_and_overwrite_writes_the_edits(...):    # answers.will(SAVE)
    assert answers.seen == [("Mask changed on disk", "mask/a.png changed on disk after you opened it.")]
    assert np.array_equal(saved_plane(project.mask()), plane(editor))
def test_save_after_a_rewrite_reload_shows_the_file_and_writes_nothing(...):   # DISCARD button
    assert editor.save() is False and np.array_equal(plane(editor), other) and file == other
    assert not editor.has_unsaved_changes()
def test_save_after_a_rewrite_cancel_keeps_the_edits_and_the_file(...):    # CANCEL
    assert editor.save() is False and editor.has_unsaved_changes() and file == other
def test_pgdn_after_a_rewrite_stays_and_says_so(...):
    assert moved == [] and file == other
    assert ("mask/a.png changed on disk after you opened it: press Ctrl+S to choose", "warn") in notices
def test_equal_pixels_with_a_new_mtime_ask_nothing(...):    # write_mask(mask, box_plane()) + bump; no answers queued
    assert editor.save() is True and np.array_equal(saved_plane(project.mask()), plane(editor))
def test_a_clean_ctrl_s_after_a_rewrite_reloads_and_writes_nothing(...):
    assert editor.save() is True and np.array_equal(plane(editor), other) and file_bytes == rewritten_bytes
def test_a_rewritten_correction_asks_but_a_rewritten_generated_skeleton_does_not(...):
    # correction rewritten -> "skeleton_corrections/a_skeleton.png changed on disk after you opened it."
    # only skeletons/a_fake.png rewritten (a Generate run) -> no prompt, correction written
```

Append to `tests/test_correct_save_gate.py` (fakes): `test_reload_reopens_the_layer_and_declines`
(`"open"` in `layer.names()`, outcome declined, `session.journal` has no entry of the layer);
`test_matching_files_only_refresh_the_stamps` (`disk_same=True` → no question, saved);
`test_discard_reopens_a_changed_layer_and_reverts_an_unchanged_one`.

Append to `tests/test_correct_main_window.py`:

```python
def test_the_open_image_follows_a_finished_generate_run(make_window, tmp_path):
    window, project = in_correct(make_window, tmp_path, 0)
    write_skeleton(project.generated(0), row=60); bump(project.generated(0))
    window.on_outputs_changed(); settle()
    assert skeleton_rows(window.correct_editor) == {60}
def test_an_image_with_unsaved_edits_does_not_follow_generate(make_window, tmp_path):
    # draw_line first; after on_outputs_changed the raster is unchanged and still dirty
```

- [ ] **Step 2: Run, expect failures**

Run: `PYTEST tests/test_correct_changed_on_disk.py tests/test_correct_save_gate.py tests/test_correct_main_window.py -k "rewrite or disk or reload or follow or matching or generate_run"`
Expected: FAIL — Discard leaves the stale plane; Save overwrites without a prompt; `AttributeError: ... ask_changed_on_disk`.

- [ ] **Step 3: Implement**

- Layer / document methods as in Interfaces; `SkeletonLayer.__init__` adds
  `self.write_stamps, self.matches_disk = doc.write_stamps, doc.matches_disk`.
- `SaveGate`: `take` stores `write_stamps_of(layer)`; `before_save` runs the decision list in
  Decisions; the reload path calls `reopen(layer_id)`.
- `CorrectSession`: `_reopen(layer_id)` = `layer.open(image_path, size, base)` (notices
  forwarded), `journal.forget(layer_id)`, retake both stamps. Take both stamp sets in
  `_open_layers`, after an ok save and in `_revert`; `close` calls `gate.forget()`.
- `editor_prompts.ask_changed_on_disk` with the renames above.
- `follow_generated_outputs(window)`: as in Decisions; last statement of `on_outputs_changed`.
- `FakeLayer`: `disk_same = False`, `matches_disk()`, `write_stamps()` returning `stamps`;
  `FakePrompts(changed=())`, records `("changed", parent, noun, label)`, default `"cancel"`.
- README line 232: "**Discard** (the unsaved layers go back to how they were opened or last
  saved; a layer whose file changed on disk meanwhile is read again from disk)". New **Saving**
  bullet: "If the file a layer saves to changed on disk after you opened it (another program, or a
  *Generate* run), **Save** asks first: **Overwrite**, **Reload (lose my edits)** or **Cancel**. A
  file rewritten with the same pixels asks nothing, and a layer without edits just reloads.
  `PgDn` / `PgUp` never ask: they stay on the image and say why. An image open in *Correct*
  without unsaved edits shows the new results when a *Generate* run finishes."

- [ ] **Step 4: Run, expect pass** — Step 2's command without `-k`. Expected: PASS.

- [ ] **Step 5: Existing tests stay green**

Run: `PYTEST tests/test_correct_session.py tests/test_correct_session_paging.py tests/test_correct_editor.py tests/test_correct_editor_prompt_flow.py tests/test_correct_read_ahead_handback.py tests/test_correct_mask_document.py tests/test_correct_mask_layer.py tests/test_correct_goldens.py tests/test_correct_skeleton_goldens.py tests/test_skeleton_loaded_file_unsaved.py tests/test_shell_window_split_surface.py tests/test_library_main_window.py tests/test_correct_layering.py`
Expected: PASS — in particular `test_release_discard_reverts_the_dirty_layers_and_forgets_their_history`,
`test_discard_on_leaving_then_coming_back_shows_the_files_without_reading_them`,
`test_a_file_changed_by_someone_else_after_our_save_is_read_again`,
`test_a_failed_save_trusts_nothing_new`, `test_revert_restores_the_saved_plane_and_clears_the_history`.

- [ ] **Step 6: Ruff** — `RUFF` on the files above; every `app/gui/correct/*.py` ≤ 450 lines.

- [ ] **Step 7: Commit (lead)**

`fix(correct): reload a file changed on disk on Discard; ask before Save overwrites it`

---

### Task W1-T5: `stem_key` everywhere; PNG / JPEG only; the load toast names what is not listed (R4, R3, O6, O7)

Implementer **opus / high** · Reviewer **opus / medium** · Lane **L-stems** (its
`project_loader.py` edit after W1-T1) · **Sci: yes** — only folders with case-variant stems or
with `.bmp` / `.tif` / `.tiff` / `.ppm` files next to supported ones.

**Decisions:**
- `stem_key(stem) = stem.lower()` on every platform (O7), not `casefold()` (`straße` ≠ `strasse`).
- Suffix matching is already case-insensitive in every reader (`_scan_ranked` lowercases at
  `project_layout.py:114`, `config.is_image_file` at `config.py:189`, `_MASK_LOOKUP` has both
  cases); only stems needed the change.
- One scan rule: `_scan_ranked` keys by `stem_key`, the winner is the lowest
  `(extension rank, file name)` (`.png` < `.jpg` < `.jpeg`; equal rank: the name that sorts
  first), and the result keeps the winner's real stem. Masks use the same scan, so
  `MASK_EXTENSIONS = IMAGE_EXTENSIONS`.
- Where a stem meets a file: the image scan (`scan_images` / new `scan_image_folder`), the
  existing-mask scan (`scan_existing_masks` re-keys found masks to the image's stem), the skip
  list (`list_inputs` compares keys), the Library lookups (`ImageManager.masks`,
  `skeleton_stems`, `corrected_stems` re-keyed to the Library's stem after each scan and in
  `refresh_outputs`), the measured-value lookup (`MeasurementIndex` row keys and `get`, not its
  freshness file names, `measurement_index.py:278-282`, which need the real stem on a
  case-sensitive disk). GT (`app/gt/store.py:83`, `export_dataset_dialog.py:72`) needs no change:
  the Library now hands it only the winner's stem; WG-T1 builds `gt/` with `stem_key`.
  Measure's own scans (`scan_skeletons`, `scan_masks(extensions=(".png",))`) keep today's keys, so
  the Measure CSVs stay byte-identical.
- O6: skeleton generation reads `config.IMAGE_EXTENSIONS` (delete `IMG_EXTENSIONS`,
  `is_image_file`, `_INPUT_EXTENSIONS`, `skeleton_inference.py:59-92`; `make_dataset` calls
  `scan_images()`). `naming.py` keeps stripping the seven suffixes so existing rows of `.tif`
  images keep their stems; the four extra ones become `naming.LEGACY_IMAGE_EXTENSIONS`, which the
  load scan also uses to name unsupported files.
- The scan's skips travel on the thread (`ImageLoaderThread.scan`) and the manager
  (`ImageManager.last_scan`), not through the `finished` signal or `on_loading_finished`'s
  pinned signature.
- The skips toast is a second, `"warn"` toast with its own key, so the success toast stays
  "Loaded N images" (`tests/test_library_main_window.py::test_load_success_is_one_formatted_message`).

**Files:**
- Modify: `app/naming.py:24` (`__all__`), `:42-45` (`LEGACY_IMAGE_EXTENSIONS`, comment), add `stem_key`
- Modify: `app/project_layout.py:44-61` (`__all__`, `MASK_EXTENSIONS`), `:98-123` (`_scan_ranked`), `:204-211` (`scan_images`), add `ImageScan`, `scan_image_folder`, `key_to_stems`
- Modify: `app/inference/mask_origin.py:133-150` (`scan_existing_masks`)
- Modify: `app/inference/mask_inference.py:43-58` (`list_inputs`)
- Modify: `app/inference/skeleton_inference.py:59-107` (delete the lists; `make_dataset`)
- Modify: `app/gui/image_manager.py:82-104` (`ImageLoaderThread.run`), `:225-249` (`_on_scan_finished`), `:266-292` (`on_loading_finished`), `:318-326` (`refresh_outputs`)
- Modify: `app/data_processing/measurement_index.py:63-64`, `:93`, `:186`
- Modify: `app/gui/window/project_loader.py:121-170` (`on_loading_finished`, `on_loading_empty`; the empty toast built from `config.IMAGE_EXTENSIONS`)
- Modify: `docs/DATA_DICTIONARY.md:27-34`, `CHANGELOG.md` `### Fixed` and `### Scientific output changes`
- Test: Create `tests/test_stem_case_and_formats.py`; Modify `tests/test_robust_generation_stem_clash.py:94-122`

**Interfaces:**
- Produces (WG-T1, WG-T6, WG-T8, W2-T1, W2-T11 consume):
  - `app.naming.stem_key(stem: str) -> str` — `stem.lower()`.
  - `app.naming.LEGACY_IMAGE_EXTENSIONS: tuple[str, ...] = (".bmp", ".ppm", ".tif", ".tiff")`.
  - `app.project_layout.ImageScan` (frozen dataclass): `images: dict[str, str]` (as `scan_images`),
    `hidden: tuple[tuple[str, str], ...]` (`(file not listed, file listed instead)`, sorted),
    `unsupported: tuple[str, ...]` (file names with a `LEGACY_IMAGE_EXTENSIONS` suffix, any case, sorted).
  - `ProjectLayout.scan_image_folder(self) -> ImageScan` (one scandir); `scan_images()` returns its `images`.
  - `app.project_layout.key_to_stems(found: Mapping[str, V], stems: Iterable[str]) -> dict[str, V]` —
    each key replaced by the stem of `stems` with the same `stem_key`; keys without one kept.
  - `ImageManager.last_scan: ImageScan | None`; `project_loader.skipped_text(scan: ImageScan) -> str`.
- Exact texts:
  - one hidden: `"1 image is not listed: a.jpg has the same name as A.png"`; n > 1:
    `"3 images are not listed: each has the same name as another image (a.jpg, b.JPG, c.jpeg)"`
    (the first three names, then `", …"`).
  - unsupported: `"1 file (.tif) is not supported and is not listed; convert it to PNG or JPEG"`;
    n > 1: `"3 files (.bmp, .tif) are not supported and are not listed; convert them to PNG or JPEG"`
    (suffixes lowercased, sorted, unique). Parts joined with `". "`.
  - empty folder: `"No .png/.jpg/.jpeg images in tifs — nothing loaded"`, + `". " + unsupported part` when any.

- [ ] **Step 1: Write the failing tests**

`tests/test_stem_case_and_formats.py` (`cpu_runtime` from `tests/test_generation_inference.py`;
`_Brightness`, `_Skeleton`, `_mean` from `tests/test_robust_generation_stem_clash.py`; `mw`,
`app`, `_wait` from `tests/test_library_main_window.py`; `_row`, `_write_lengths` from
`tests/test_shell_info_measurement_index.py`):

```python
def test_stem_key_lowers_and_does_not_casefold():
    assert stem_key("A_T001") == "a_t001" and stem_key("straße") != stem_key("strasse")

@pytest.mark.parametrize("run", range(5))
def test_case_variant_stems_give_one_image_and_one_output_every_run(tmp_path, cpu_runtime, run):
    # A.png (white), a.jpg (black), b.jpg
    scan = ProjectLayout(src).scan_image_folder()
    assert sorted(scan.images) == ["A", "b"] and scan.hidden == (("a.jpg", "A.png"),)
    report = mi.generate_masks(src, out, model=_Brightness(), batch_size=8)
    assert sorted(report.written) == ["A.png", "b.jpg"] and report.total == 2
    assert len(pngs(out)) == 2 and _mean(mask_of_A(out)) == 255
    assert sorted(si.run_skeleton_generation(src, skel, model=_Skeleton(), batch_size=8).written) == ["A.png", "b.jpg"]

@pytest.mark.parametrize("mask_name", ["A.png", "a.png"])
def test_a_manual_mask_survives_keep_existing_whatever_its_case(tmp_path, cpu_runtime, mask_name):
    # RGBA hand mask at mask/<mask_name>; images A.png, a.jpg, b.jpg
    existing = scan_existing_masks(layout, layout.scan_images())
    assert existing.manual == frozenset({"A"})
    report = mi.generate_masks(src, layout.mask_dir, model=_Brightness(), skip_stems=OverwritePolicy.KEEP_EXISTING.skip_stems(existing))
    assert report.written == ["b.jpg"] and read_bytes(hand_mask) == before

def test_equal_extensions_pick_the_name_that_sorts_first(tmp_path):
    # skip unless tmp_path is case-sensitive (probe: write "Probe", "probe" must not exist):
    # runs on the ubuntu CI. A.png and a.png -> images == {"A": ".../A.png"},
    # hidden == (("a.png", "A.png"),)

def test_mixed_formats_give_the_same_inputs_to_library_masks_and_skeletons(tmp_path):
    # a.png b.jpg c.JPEG d.tif e.bmp f.ppm g.TIFF
    scan = ProjectLayout(src).scan_image_folder()
    stems = sorted(scan.images)
    assert stems == ["a", "b", "c"]
    assert sorted(os.path.splitext(n)[0] for n in mi.list_inputs(src)[0]) == stems
    assert sorted(image_stem(p) for p in si.make_dataset(src)) == stems
    assert scan.unsupported == ("d.tif", "e.bmp", "f.ppm", "g.TIFF")

def test_lookup_finds_a_row_whose_stem_differs_in_case(tmp_path):
    _write_lengths(tmp_path, [_row("a", LENGTH_CSV_HEADERS[5], 18.77)])
    assert MeasurementIndex(str(tmp_path)).lookup("A") == Measurement(18.77, None, None)

def test_the_library_finds_a_mask_whose_name_differs_in_case(mw, tmp_path):
    # images A.png, b.png; mask/a.png -> mw.image_manager.has_mask("A")

def test_the_load_toast_names_hidden_and_unsupported_files(mw, tmp_path):
    # A.png, a.jpg, b.png, t.tif
    assert ("warn", "1 image is not listed: a.jpg has the same name as A.png. 1 file (.tif) is not supported and is not listed; convert it to PNG or JPEG") in mw.toasts_seen
    assert len(mw._image_leaves()) == 2

def test_a_folder_of_tiffs_says_why_nothing_loaded(mw, tmp_path):
    assert mw.toasts_seen[-1] == ("warn", "No .png/.jpg/.jpeg images in tifs — nothing loaded. 2 files (.bmp, .tif) are not supported and are not listed; convert them to PNG or JPEG")
```

`tests/test_robust_generation_stem_clash.py`: `test_skeleton_generation_still_reads_its_other_formats`
becomes `test_skeleton_generation_reads_png_and_jpeg_only` (`names == ["x.png"]`);
`test_inputs_without_a_shared_stem_are_unchanged` uses `config.is_image_file`.

- [ ] **Step 2: Run, expect failures**

Run: `PYTEST tests/test_stem_case_and_formats.py tests/test_robust_generation_stem_clash.py`
Expected: FAIL — `ImportError: cannot import name 'stem_key'`; two Library images for `A` / `a`;
skeleton inputs include `.tif`.

- [ ] **Step 3: Implement** the Interfaces as decided; `_scan_ranked` stays one scandir with an
optional collector for losers and unsupported names (so `scan_image_folder` adds no second
listing); `ImageLoaderThread.run` calls `scan_image_folder()`, keeps `self.scan`, re-keys the
mask / skeleton / correction scans with `key_to_stems(..., images)`; `_on_scan_finished` sets
`self.last_scan` before either outcome; `on_loading_finished` / `on_loading_empty` add the
`skipped_text` toast with key `"project-load-skipped"`. DATA_DICTIONARY lines 27-34 rewritten to
the new rule (one image per stem without case, the ranking, PNG and JPEG only, unsupported
files reported on load). CHANGELOG: a `### Fixed` bullet, and under `### Scientific output changes`:
"Folders with case-variant stems (`A.png`, `a.jpg`): one image, ranked, on every platform; the
hidden one gets no new mask, skeleton or CSV row (outputs already on disk are still measured).
Folders with `.bmp` / `.tif` / `.tiff` / `.ppm` next to supported files: those get no new
skeleton or `root_lengths.csv` row. Before / after on the audit folders: `r4_case` 3 → 2 written,
mask from `A.png` in 5 of 5 runs; `r3_formats` skeleton inputs 7 → 3. The 3000-pair real sample
(JPEG only, no case-variant stems): identical."

- [ ] **Step 4: Run, expect pass** — Step 2's command. Expected: PASS.

- [ ] **Step 5: Existing tests stay green**

Run: `PYTEST tests/test_robust_project_scan_speed.py tests/test_foundation_core_project_layout.py tests/test_foundation_core_config.py tests/test_gt_export_count_traced.py tests/test_generation_handlers.py tests/test_generation_inference.py tests/test_library_main_window.py tests/test_shell_info_measurement_index.py tests/test_shell_metrics_per_metric_staleness.py tests/test_shell_library_corrections.py tests/test_measure_core.py tests/test_shell_window_split_surface.py`
Expected: PASS — `test_the_ranked_scan_is_unchanged` / `test_the_layout_scans_are_unchanged` have
no case-variant stems; `test_measure_csv_is_byte_identical_to_legacy_pipeline` and
`test_real_sample_is_byte_identical_to_legacy_pipeline` unchanged.

- [ ] **Step 6: Ruff** — `RUFF` on the files above.

- [ ] **Step 7: Commit (lead)**

`fix(project): stems compare without case; skeleton generation reads the Library's formats`

---

### Task W1-T7: The pix2pix BSD notice (F1 step 1)

Implementer **sonnet / low** · Reviewer **sonnet / low** · Lane **L-docs** (its
`skeleton_inference.py` edit after W1-T5) · Sci: no.

**Decision:** the header names the derived code by function, not by line (W1-T5 deletes
`IMG_EXTENSIONS` / `is_image_file`, so `skeleton_inference.py:59-252` no longer holds).

**Files:**
- Create: `THIRD_PARTY_NOTICES.md` (repository root)
- Modify: `app/inference/skeleton_inference.py:1-15` (module docstring: one paragraph)
- Modify: `app/assets/THIRD_PARTY.md` (a pointer line after the table)
- Modify: `CHANGELOG.md` `### Added`
- Test: Modify `tests/test_fair_metadata.py` (append)

**Interfaces:** none.

- [ ] **Step 1: Write the failing test**

```python
def test_the_pix2pix_bsd_notice_is_reproduced():
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for needle in ("Copyright (c) 2017, Jun-Yan Zhu and Taesung Park",
                   "Copyright (c) 2016, Phillip Isola and Jun-Yan Zhu",
                   "Copyright (c) 2015, Facebook, Inc.",
                   "Redistributions of source code must retain the above copyright notice"):
        assert needle in text
    source = (ROOT / "app" / "inference" / "skeleton_inference.py").read_text(encoding="utf-8")
    assert "THIRD_PARTY_NOTICES.md" in source and "pytorch-CycleGAN-and-pix2pix" in source
```

- [ ] **Step 2: Run, expect failure** — `PYTEST tests/test_fair_metadata.py::test_the_pix2pix_bsd_notice_is_reproduced`. Expected: FAIL (`FileNotFoundError`).

- [ ] **Step 3: Implement**

- `THIRD_PARTY_NOTICES.md`: a heading, one sentence naming what is derived and where, then the
  upstream licence verbatim in a fenced block, taken from
  `gh api repos/junyanz/pytorch-CycleGAN-and-pix2pix/license --jq .content` (base64-decode; three
  blocks: CycleGAN 2017, pix2pix 2016, DCGAN 2015).
- Docstring paragraph: "`make_dataset`, `save_image`, `get_transform`, `Identity`, `get_norm_layer`,
  `ResnetBlock` and `EnhancedResnetGenerator` (after upstream `ResnetGenerator`) derive from
  pytorch-CycleGAN-and-pix2pix (https://github.com/junyanz/pytorch-CycleGAN-and-pix2pix), BSD
  licence; the notice is in THIRD_PARTY_NOTICES.md."
- `app/assets/THIRD_PARTY.md`: "Code outside this folder: see `THIRD_PARTY_NOTICES.md` at the repository root."

- [ ] **Step 4: Run, expect pass** — `PYTEST tests/test_fair_metadata.py tests/test_fair_packaging.py`. Expected: PASS
(`test_license_is_declared_everywhere_or_nowhere` unaffected: the notice file is not a `LICENSE`).

- [ ] **Step 5: Ruff** — `RUFF app/inference/skeleton_inference.py tests/test_fair_metadata.py`.

- [ ] **Step 6: Commit (lead)** — `docs(licence): reproduce the pix2pix BSD notice for the skeleton generator`

---

## Notes for the lead

- **Cross-lane file order** missing from master section 5/6: W1-T7 and W1-T5 both edit
  `app/inference/skeleton_inference.py` (master: W1-T7 "After —"); W1-T4 edits
  `app/gui/main_window.py` (`on_outputs_changed`) and `app/gui/window/editors.py`, which section 6
  gives to L-job / L-correct; W1-T5's `project_loader.py` waits for W1-T1 (already in the master).
- **Golden edit in W1-T3** (`cases.synth_blank_png.skeleton_clear.saved`): master section 2
  allows golden changes only in WG-T2 / WG-T3; O2 forces this one earlier. Pixel hashes unchanged.
- **New Qt-free modules** not in master section 2's list: `app/gui/correct/save_gate.py`,
  `app/editing/thinning.py` (both added to `tests/test_correct_layering.py::QT_FREE`).
- **Doc overlaps with W0-T2:** W1-T5 rewrites `docs/DATA_DICTIONARY.md:27-34` (supersedes W0-T2's
  line-30 row); W1-T4 writes README:232's final Discard wording (W0-T2's interim wording, if
  landed first, is replaced).
- W3-T4 rewrites the two renamed model-mask tests of W1-T2
  (`test_a_mask_of_another_size_is_not_shown_and_is_kept` and the layer notice test).

## Self-review

1. **Spec coverage.** Old W1-T1 behaviours 1-3 → W1-T1 steps (chain, refusal before dialog,
   no job while loading) + the scikit-image comment row. Old W1-T2 behaviours 1-4 → status /
   notice, kept save, Replace prompt, `PgDn` stays; the e2e 2048 x 1536 test. Old W1-T3 1-3 →
   unchanged, removed, emptied written; item 4 (mask half of D7) untouched; `is_generated_as_opened`
   placed Qt-free. Old W1-T4 1-4 → Discard reload, Save asks (three answers), no false alarm,
   follow Generate; Trace GT parts dropped per master 1.1, stamp contract named. Old W1-T5 1-5 →
   `stem_key`, ranking, hidden toast, R3 formats + unsupported toast, O7 on every platform; the
   moved project_loader toast row. W1-T6 dropped (master 1.1). W1-T7 → notice, header, pointer, test.
   Master 1 rows O2 (W1-T3), O6 / O7 (W1-T5), O15 (W1-T1), O19 (W1-T4) covered.
2. **Step scan.** Every test step names its file, test and the asserted values; code steps name
   signature, file and approach; no "handle edge cases" lines. Bodies are given only for texts.
3. **Type consistency.** `SaveOutcome.declined`, `DiskGuardedLayer.{unloaded_label, write_stamps,
   matches_disk}`, `SaveGate.{before_save, take, forget}`, `save_gate.{unloaded_label_of,
   write_stamps_of, matches_disk_of}`, `MaskRead.not_loaded`, `MaskDocument.unloaded_file`,
   `MaskSaveResult.kept`, `SkeletonSaveResult.{unchanged, removed}`, `is_generated_as_opened(mask,
   generated_path, photo_size)`, `stem_key`, `ImageScan`, `key_to_stems` are used with the same
   names and types in every task that mentions them.
4. **Review Focus.** Master items 2-5 each have a named test in their owning task (above); the
   L2 half of the old item 5 is added as item 5.
5. **Proportion.** Six tasks; code appears only as test assertions and fixed texts.
