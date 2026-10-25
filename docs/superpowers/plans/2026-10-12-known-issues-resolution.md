# Known-Issues Resolution Plan (audit of 2026-10-12)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every open item of `docs/KNOWN_ISSUES.md` and every finding of the
2026-10-12 audit, in an order that removes data loss first and changes a scientific output
only after the owner has said so.

**Architecture:** Seven waves. Wave 0 makes the documents true. Wave 1 stops the paths
that lose or misplace data. Wave 2 adds records and wording that change no value. Wave 3
holds every change to a CSV value or a saved pixel, each behind one owner answer. Waves 4
to 6 are editor UX, repository / FAIR work and optional performance work.

**Tech Stack:** Python 3.10, PyQt6, PyTorch, OpenCV, Pillow, scikit-image, Dash / Plotly,
pytest, ruff. Windows 11, PowerShell.

**Spec:** the eight audit reports in `.claude/handoff/issue-audit-2026-10-12/`
(`length.md`, `mask-calibration.md`, `reproducibility.md`, `correct-ux.md`, `trace-gt.md`,
`perf-editors.md`, `generate-dash.md`, `repo-docs.md`) and the reproducing scripts in its
`scripts/<package>/` folder. That folder is git-ignored: it exists on the owner's machine
only. Section 8 of `docs/KNOWN_ISSUES.md` is the tracked summary. This plan repeats what a
task needs, so it can be read without the reports; the reports hold the measurements.

**Scope note.** This is a master plan over about forty issues in eight subsystems. It
fixes, per task, the behaviour, the files, the tests and the acceptance check. It does not
contain step-level code: half of the tasks depend on an owner answer that changes the
code, and the reproducing scripts already are the failing tests in script form. At the
start of each wave, write that wave's step-level plan with superpowers:writing-plans from
the tasks below (one plan per wave; each wave leaves working, tested software).

## Global Constraints

- Every run uses `./venv/Scripts/python.exe`. Never bare `python`.
- Tests: `$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest`. Pass
  `--junitxml` when you need the summary (`-q` hides it).
- Lint and format with ruff only: `./venv/Scripts/python.exe -m ruff check --fix .` and
  `./venv/Scripts/python.exe -m ruff format .` (line length 100, py310).
- A change that can alter a value in `root_lengths.csv` / `root_areas.csv`, a saved mask,
  skeleton or GT pixel, or a generated output gets an entry in `CHANGELOG.md` under
  "Scientific output changes". Only Wave 3 tasks, W1-T5 and W2-T5 may make such a change.
- `tests/data/correct_goldens.json` stays identical unless a task says otherwise. No task
  in this plan changes it.
- The Measure CSVs stay byte-identical outside Wave 3
  (`tests/test_measure_core.py::test_measure_csv_is_byte_identical_to_legacy_pipeline`,
  `::test_real_sample_is_byte_identical_to_legacy_pipeline`).
- Every `app/gui/correct/*.py` stays at 450 lines or fewer (ratchet test).
- Qt-free core modules import neither PyQt6 nor torch (`tests/test_layering_contract.py`,
  the import smoke test).
- `MainWindow`'s API surface is pinned by `tests/test_shell_window_split_surface.py`: new
  window logic goes into a part under `app/gui/window/`.
- Trace GT never reads or writes `mask/` or `skeletons/`.
- The metrics bar shows measured values only. Never fabricate a value.
- `CLAUDE.md` has an uncommitted change by the owner (it removes the mem0 memory layer).
  Do not edit `CLAUDE.md` until the owner has committed or dropped that change.
- One commit per task, made by the lead after review. Implementing agents make no git
  state change. No attribution lines in commit messages.
- After code edits, refresh the graph: `python -m graphify update .` from the uv tool venv
  (the `graphify` shim is broken in Git Bash).

## Review Focus

The five inputs most likely to bite a user, each with the task whose tests pin it:

1. A photo that is not 640 x 480. Expected: its model mask is shown, and no Save removes a
   mask file the editor did not load. Tests in W1-T2 and W3-T4.
2. A second folder opened while a job runs. Expected: the job's outputs go to the folder
   the job started in. Tests in W1-T1.
3. Two images whose stems differ only by case, or a `.tif` next to a `.png`. Expected: one
   Library entry, one set of outputs, the same on every run. Tests in W1-T5.
4. A mask, skeleton or tracing rewritten by another program or by a Generate run while it
   is open with unsaved edits. Expected: Save asks before it overwrites. Tests in W1-T4.
5. A skeleton correction that is unedited, or saved in the old 341 x 256 format, once
   corrections are measured. Expected: Measure uses the generated skeleton for it and says
   so. Tests in W1-T3, W2-T7 and W3-T1.

---

## 1. Status of every issue

`KI` is the section of `docs/KNOWN_ISSUES.md`. "Sci" says whether the fix can change a CSV
value or a saved pixel. Effort: S under half a day, M up to two days, L more.

| ID | Issue                                                      | KI   | Status after the audit       | Task                       | Owner | Sci           | Effort |
| -- | ---------------------------------------------------------- | ---- | ---------------------------- | -------------------------- | ----- | ------------- | ------ |
| L1 | Corrected skeletons are not measured (FR-03)               | 5    | Open                         | W3-T1                      | O1    | yes           | L      |
| L2 | Save with nothing unsaved writes a correction (V1)         | 7    | Open                         | W1-T3                      | O2    | no            | M      |
| L3 | Mask and skeleton are independent; Correct does not say so | 8    | Partly                       | W2-T8                      | O14   | no            | S      |
| L4 | The name `<stem>_fake.png`                               | 8    | Not an issue                 | W2-T8 (docs, one constant) | none  | no            | S      |
| L5 | Two thinning paths; old 341 x 256 corrections              | 6 Q1 | Open                         | W2-T7                      | O13   | no            | S      |
| M1 | Model mask on a photo that is not 640 x 480                | 8    | Worse than thought           | W1-T2, W3-T4               | O8    | B only        | S + M  |
| M2 | One 18 x 13 mm field of view for every image               | 8    | Open                         | W2-T4, W3-T2               | O3    | step 2        | M + L  |
| M3 | EXIF orientation (Q4)                                      | 6    | Not an issue                 | W2-T10 (policy, tests)     | none  | no            | S      |
| M4 | Mask origin by PNG colour type; cleared mask regenerated   | 8    | Partly                       | W2-T5                      | O9    | no            | M      |
| M5 | Area of editor masks; two thresholds                       | 8    | Not an issue; small residual | W2-T6                      | O10   | no            | S      |
| R1 | Batch size changes GPU outputs                             | 7    | Open                         | W2-T3, W3-T3               | O4    | mode on       | M      |
| R2 | Deterministic cuDNN                                        | 5    | Resolved in code             | W0-T2 (docs)               | O5    | no            | S      |
| R3 | Supported formats differ per reader                        | 7    | Open                         | W1-T5                      | O6    | mixed folders | S      |
| R4 | Case-variant stems                                         | 7    | Worse than recorded          | W1-T5                      | O7    | such folders  | M      |
| R5 | "Changed since measured" compares file times               | 7    | Open, text partly wrong      | W2-T2                      | none  | no            | M      |
| R6 | A no-op run replaces `_sprouts_run.json`                 | 5    | Open                         | W2-T1                      | O16   | no            | S      |
| R7 | Job ends after another folder was opened                   | 7    | Bug                          | W1-T1                      | O15   | no            | S      |
| R8 | Measure buttons are nouns; re-measures everything (W12)    | 7    | Open                         | W2-T8                      | O17   | no            | S      |
| E1 | Save placement and states (W16)                            | 7    | Open                         | W4-T1                      | O18   | no            | S      |
| E2 | Discard / Save after an external change                    | 7    | Worse than recorded          | W1-T4                      | O19   | no            | M      |
| E3 | Smooth on a picked branch (J1)                             | 7    | Open by design               | W4-T2                      | O20   | no            | S      |
| E4 | A click with brush size 1 paints nothing                   | 6    | Open                         | W3-T5                      | O11   | 1 px          | S      |
| E5 | Branch pick speed and semantics                            | 6    | Partly; numbers stale        | W4-T7 (optional)           | none  | no            | M      |
| E6 | Pinch zoom; bicubic refine                                 | 6    | Open                         | close (section 4)          | O21   | no            | -      |
| E7 | JPEG deblock (Q2)                                          | 6    | Open question                | W4-T5                      | O22   | no            | M      |
| G1 | Two tracing interfaces; merge Trace GT into Correct        | 6    | Follow-up, not a defect      | W4-T3                      | O23   | no            | S x4   |
| G2 | Trace GT v1 limits; un-retire worse than recorded          | 4    | Open                         | W4-T4                      | O24   | no            | S-M    |
| G3 | Compact topbar sign-off                                    | 4    | Built; numbers stale         | close on O25               | O25   | no            | S      |
| G4 | Naming: trace, correct, GT, mask                           | 8    | Open                         | W2-T8, W4-T8               | O26   | no            | S      |
| P1 | Editor pages after the first paint                         | 7    | Open                         | close (section 4)          | O30   | no            | -      |
| P2 | First image read on the GUI thread                         | 7    | Open                         | W6-T1 (optional)           | none  | no            | S      |
| P3 | Deflate in the skeleton history                            | 7    | Open                         | W6-T2 (optional)           | none  | no            | S      |
| P4 | Halo layer at 4K                                           | 7    | Open                         | close (section 4)          | none  | no            | -      |
| P5 | Skeleton first frame at 100 % (Q5)                         | 6    | Open                         | close (section 4)          | none  | no            | -      |
| P6 | Hardware OpenGL check                                      | 7    | Checked; indicator open      | W2-T9                      | O29   | no            | S      |
| P7 | Warm-up leftovers                                          | 7    | Open                         | close (section 4)          | none  | no            | -      |
| D1 | `refresh_outputs` on the GUI thread                      | 7    | Open                         | close (section 4)          | none  | no            | -      |
| D2 | Pre-Generate scan as a warm-up                             | 7    | Open                         | W6-T3 (optional)           | O28   | no            | M      |
| D3 | Raster viewports while a dashboard lives                   | 7    | By design                    | close (section 4)          | none  | no            | -      |
| D4 | First dashboard open freeze                                | 7    | Open, located                | close (section 4)          | none  | no            | -      |
| D5 | IPython in the venv                                        | 7    | Environment only             | W6-T4                      | O31   | no            | S      |
| D6 | Bar labels above 30 bars                                   | 7    | By design                    | close (section 4)          | none  | no            | -      |
| D7 | Dashboard view names and date colours (W19)                | 7    | Colours worse                | W4-T6                      | O27   | no            | S-M    |
| F1 | No licence; Pix2Pix notice missing                         | 5    | Worse than recorded          | W1-T7, W5-T1               | O32   | no            | S + S  |
| F2 | `checkpoints.zip` tracked; no DOI; no mask model         | 5    | Partly                       | W5-T2                      | O33   | no            | M      |
| F3 | Metadata placeholders; citation not shipped                | 8    | Open                         | W5-T3                      | O34   | no            | S      |
| F4 | Real images as test data (Q3); stale gate                  | 6    | Worse than recorded          | W5-T4                      | O35   | no            | S-M    |
| F5 | Compat shims (CQ-10)                                       | 5    | Partly                       | W5-T5                      | none  | no            | S-M    |
| F6 | Script teardown note                                       | 6    | Not a product issue          | W5-T7                      | none  | no            | S      |
| F7 | Docs consistency; KNOWN_ISSUES structure                   | 8    | Open                         | W0-T1, W0-T2, W5-T7        | O36   | no            | M      |
| X1 | The length estimator reads about 4.4 % long                | 8    | New, science                 | W3-T8                      | O12   | every length  | -      |
| X2 | Measure-area measures masks whose image is gone            | 8    | New                          | W2-T11                     | O37   | no            | S      |
| X3 | `Error` cells hold absolute paths                        | 8    | New                          | W3-T7                      | O38   | Error text    | S      |
| X4 | Generate can overwrite a mask saved during the run         | 8    | New, not reproduced          | W1-T6                      | none  | no            | S      |
| X5 | pytest writes into the user's log                          | 8    | New                          | W5-T6                      | none  | no            | S      |
| X6 | `fault.log` wording; start-up warnings                   | 8    | New                          | W5-T6                      | none  | no            | S      |
| X7 | CI has not run since 2026-07-25                            | 8    | New                          | W5-T6                      | none  | no            | S      |
| X8 | Stamp helpers defined six times                            | 8    | New                          | W5-T5                      | none  | no            | S      |
| X9 | Correct with no image shows a tool hint                    | 8    | New                          | W2-T8                      | none  | no            | S      |

## 2. Owner decisions

One line each. "Rec." is the audit's recommendation unless marked (lead). A task marked
with an O number does not start before its answer; tasks without one can start now.

**Science and outputs**

| No. | Question                                                                                                                                                                                                                                                    | Rec.                                                                                                                                                                                                                                                                            | Gates                    |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| O1  | Shall `root_lengths.csv` report the hand-corrected skeleton's length where a correction exists, in the same column, with a new last column `Source`? A corrected row is measured at photo resolution and reads a median 0.8 % longer on the same lines. | Yes, after L2                                                                                                                                                                                                                                                                   | W3-T1                    |
| O2  | Is saving an unedited skeleton meant to mark it as accepted?                                                                                                                                                                                                | No: write a correction only when it differs from the generated skeleton                                                                                                                                                                                                         | W1-T3                    |
| O3  | Is the camera frame 18 x 13 mm or 18 x 13.5 mm, and are CAM1, CAM2 and CAM3 the same model? Then: one constant, or a per-project calibration file?                                                                                                          | Check the manual or image a ruler; per-project file.<br />They are the same batz camera by vineàscietific called 100x. I think the correct values is 18x13.5 mm. Yes it should be one constant per project file.                                                              | W3-T2                    |
| O4  | Build a fixed-shape batch mode (masks 16, skeletons 64, last batch padded)? On out of memory in that mode, stop the run or halve and flag? Default on?                                                                                                      | Yes; stop; on, after one GPU session                                                                                                                                                                                                                                            | W3-T3                    |
| O5  | Keep deterministic cuDNN as the default, with `SPROUTS_CUDNN_BENCHMARK=1` as the opt-out?                                                                                                                                                                 | Yes                                                                                                                                                                                                                                                                             | W0-T2                    |
| O6  | Input formats: PNG and JPEG only (skeleton generation stops reading `.bmp`, `.tif`, `.tiff`, `.ppm`), or all seven everywhere?                                                                                                                      | PNG and JPEG only, this app only focus on rgb roots for now. next set of updates will be released with the next version. so skip them. keep png, jpg, PNG, JPEG and normal general rgb image formatso nly                                                                       | W1-T5 (R3 part)          |
| O7  | Compare stems without case on case-sensitive file systems too, so that one of two valid images there is hidden?                                                                                                                                             | Yes                                                                                                                                                                                                                                                                             | W1-T5 (non-Windows part) |
| O8  | Should Generate write masks at the photo's size? Nearest or bilinear when a model mask is shown on a larger photo?                                                                                                                                          | No; nearest                                                                                                                                                                                                                                                                     | W3-T4                    |
| O9  | Should a mask cleared by hand stay on disk as an empty mask, so Generate keeps it and Measure writes 0? Add "Replace model masks only" to the Generate prompt?                                                                                              | Yes; yes                                                                                                                                                                                                                                                                        | W2-T5                    |
| O10 | Should Correct read masks at Measure's threshold (127) instead of 200?                                                                                                                                                                                      | Yes                                                                                                                                                                                                                                                                             | W2-T6                    |
| O11 | Make a click with the 1 px brush paint (or erase) its pixel? One pixel per click; no golden changes.                                                                                                                                                        | Yes                                                                                                                                                                                                                                                                             | W3-T5                    |
| O12 | The 8-connected arc-length estimator reads about 4.4 % long on synthetic lines (0 to +9 % by direction). Change its weights?                                                                                                                                | We must used 8 connected arc-length since it is more relaistic. you must rmeebr that pixels are square, you can find documentations for the camera online and make the best use of your judgemnt to come up with a solution. you can use a opus 5.5 max subagent for this task. | W3-T8                    |
| O13 | Old-format corrections (341 x 256, broken lines) are never measured and are flagged for retracing: agreed?                                                                                                                                                  | Yes                                                                                                                                                                                                                                                                             | W2-T7, W3-T1             |
| O14 | State in Correct and in Measure that the mask gives area, the skeleton gives length, and neither follows the other? (For information: mask says root, skeleton says none in 32 % of sampled images.)                                                        | Yes                                                                                                                                                                                                                                                                             | W2-T8                    |
| O37 | Measure-area writes a row for a mask whose image is gone. Keep such rows and report their count, or drop them?                                                                                                                                              | if the image is gone the mask should also be gone, you can docuemnt it soemwhere but dont hsow the mask if there is no image corresponding to it available.                                                                                                                     | W2-T11                   |
| O38 | Write only the file name, not the absolute path, in the `Error` cell of the Measure CSVs?                                                                                                                                                                 | Yes (lead)                                                                                                                                                                                                                                                                      | W3-T7                    |

**Workflow and UX**

| No. | Question                                                                                                                                                                               | Rec.                                                                                                                                                                                                                                                                                                                                                                                                                                          | Gates                  |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| O15 | Refuse to open another folder while a job runs, or ask whether to stop the job?                                                                                                        | Refuse                                                                                                                                                                                                                                                                                                                                                                                                                                        | W1-T1 (step 2 wording) |
| O16 | Should a run that writes and fails nothing leave `_sprouts_run.json` alone, and should runs be kept as a history file?                                                               | Yes; yes                                                                                                                                                                                                                                                                                                                                                                                                                                      | W2-T1                  |
| O17 | Rename the buttons to "Measure Length" / "Measure Area"? Build incremental measurement?                                                                                                | Yes; no                                                                                                                                                                                                                                                                                                                                                                                                                                       | W2-T8                  |
| O18 | Remove the top-bar *Clear* in Correct, keep *Save* in the dock in both editors, and fill *Save* with the accent only while something is unsaved?                                 | Yes                                                                                                                                                                                                                                                                                                                                                                                                                                           | W4-T1                  |
| O19 | On Discard, reload a file that another program or a Generate run changed, and ask before a Save overwrites such a file?                                                                | Yes; yes                                                                                                                                                                                                                                                                                                                                                                                                                                      | W1-T4                  |
| O20 | Keep "S on an unmoved picked branch is not an unsaved change", with a toast when such a preview is dropped?                                                                            | Yes                                                                                                                                                                                                                                                                                                                                                                                                                                           | W4-T2                  |
| O21 | Is the app used on macOS or Linux, or with a touch screen?                                                                                                                             | Yes the app should support work on linux and macOS. We can ignore gpu operation on macOS, but must keep it workable on linux, therfeore we must not use any windows specific packages like pywin32                                                                                                                                                                                                                                            | E6                     |
| O22 | Add a display-only "Smooth JPEG blocks" switch to Enhance, default off?                                                                                                                | Yes, else drop Q2, thei mages ar enot of very high quality so as we zoom in thei mages become bad loose detail, are there other very very light weight models that we can run to enahnce the image for zooming so the images looks better? only do it if there is a very small image upsaclaer method/algorithm or ML model that can do it in real time without eating off a lot of computeo n gpu or cpu pu and run very very lighting fast. | W4-T5                  |
| O23 | Merge the Trace GT editor's code into the Correct workspace now (about a week, no visible change beyond "Image 12 / 40" and kept opacity), or keep two editors and fix the wording?    | Keep two, we shouldo nly have one unified editor 2 editors is a waste of space.                                                                                                                                                                                                                                                                                                                                                               | W4-T3                  |
| O24 | Trace GT: add Restore for retired classes and "Remove tracing"? Show traced images in the library tree? Write per-class pixel counts into the export manifest? Instance / COCO export? | Yes; yes; yes; not now                                                                                                                                                                                                                                                                                                                                                                                                                        | W4-T4                  |
| O25 | Below about 1040 px only the selected stage shows its name; the other five are icons with tooltips. Accept, or force all six names (smallest window 1040 px instead of 700 px)?        | Accept                                                                                                                                                                                                                                                                                                                                                                                                                                        | G3                     |
| O26 | In Trace GT, call the saved thing "tracing" (Save tracing, No tracing) or keep "GT"? Move Trace GT to the right end of the top bar? Rename the stage?                                  | A ML generated oputput is a prediction, but as soon aşhuman fixes i₺edits it and saves it again by pressing the save button it becomes GT and should be saved as GT. IN the left hand side of the app, we use two indicators to sho₩skeleton, area mask and  now it should have one more similar indicator to show GT                                                                                                                    | W4-T8                  |
| O27 | Rename the dashboard views to one scheme, and colour dates with one blue ramp?                                                                                                         | Yes; yes                                                                                                                                                                                                                                                                                                                                                                                                                                      | W4-T6                  |
| O28 | Is 0.44 s saved once per Generate click worth about 150 lines plus tests?                                                                                                              | Only with other Generate work                                                                                                                                                                                                                                                                                                                                                                                                                 | W6-T3                  |
| O29 | Canvas indicator: a warn toast plus an About row, or About only?                                                                                                                       | Both                                                                                                                                                                                                                                                                                                                                                                                                                                          | W2-T9                  |
| O30 | Build the editor pages after the first paint?                                                                                                                                          | No                                                                                                                                                                                                                                                                                                                                                                                                                                            | P1                     |
| O31 | Uninstall `pipreqs` and `ipython` from the runtime venv?                                                                                                                           | Yes (owner runs it)                                                                                                                                                                                                                                                                                                                                                                                                                           | W6-T4                  |

**Repository**

| No. | Question                                                                                                                                                                                  | Rec.                                                                                                                                 | Gates        |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------ |
| O32 | Which licence, and who holds the copyright?                                                                                                                                               | GPL-3.0-or-later for the code (PyQt6 is GPL-3.0-only), CC-BY-4.0 for weights, no smaple data will be released                        | W5-T1        |
| O33 | May both checkpoints (mask 338 MB, skeleton 52 MB) be published as a Zenodo record with a DOI and their own licence, and `checkpoints.zip` then be dropped from the tree, history kept? | only weight swill be releasd no data will be pushed as zenodo checkpoints.                                                           | W5-T2        |
| O34 | Author list in order with ORCID and affiliation, contact e-mail, funding statement, name to cite. Tag v3.0.0 at the merge of this branch?                                                 | Cite as "SPROUTS"; tag at the merge                                                                                                  | W5-T3        |
| O35 | May three 640 x 480 images of 2024-07-18 (T001 L044, T001 L050, T012 L047; 52 KB) with their mask and skeleton be committed as public test data?                                          | NO imageşof the dataset will be commited, except for a few example images along with thier mask and skeleton and some result graphs | W5-T4 step 2 |
| O36 | Delete `docs/Architecture_2 useful.md` and its 2.6 MB PDF? Delete `docs/BUILD_EXECUTABLE.md` in favour of the README section?                                                         | Yes; yes                                                                                                                             | W5-T7        |

---

## 3. Waves and tasks

Lanes: tasks in different lanes of one wave touch disjoint files and can go to parallel
subagents. Tasks in one lane run in the order given.

### Wave 0: make the documents true (no code)

#### W0-T1: Restructure `docs/KNOWN_ISSUES.md` (F7)

**Files:** Modify `docs/KNOWN_ISSUES.md`.

- [ ] Retitle to "SPROUTS — known issues and open decisions"; replace the intro line
  ("Tracked during the `feat/sprouts-redesign` work") with a status line: date, HEAD, how
  to read the file.
- [ ] New order: "Open: owner decisions" (table: ID, question, recommended answer, since,
  detail), "Open: engineering" (table: ID, item, measured cost, since, detail), "By design"
  (today's section 4), "Details" (one subsection per open ID), "Resolved history" (by pass,
  newest first).
- [ ] Use the IDs of this plan (section 1) and keep the existing ones (FR-01, FR-03,
  FR-07, CQ-10, Q2 to Q5, W12, W16, W19, V1, J1) as aliases.
- [ ] Split every mixed bullet: the open remainder becomes a row, the resolved sentence
  moves to the history unchanged. The eleven mixed bullets are at the old lines 44-47,
  65-78, 137-142, 152-157, 171-173, 205-211, 217-219, 297-301, 305-316, 336-344, 418-421.
- [ ] Fold section 8 (this audit) into the tables and the details.

**Done when:** every sentence of the file at `git show HEAD:docs/KNOWN_ISSUES.md` is still
present (check with a script that compares normalised sentences), and every row of
section 1 above has a row in an open table or an entry in the history.

**Commit:** `docs(known-issues): open items first, with IDs; resolved history below`

#### W0-T2: Correct the stale statements (F7, R2, and the audit's doc corrections)

**Files:** Modify the files below. Comments in code count as docs here.

| Where                                                                                             | Stale text                                                                                                                                                        | Correction                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `README.md:232`                                                                                 | "**Discard** (the unsaved layers go back to what is on disk)"                                                                                               | until W1-T4 lands: "(the unsaved layers go back to how they were opened or last saved here)"                                                                                                                                |
| `README.md:156-158`                                                                             | calibration paragraph                                                                                                                                             | add: the whole frame is assumed to cover the field of view, and the pixel is not square (0.028125 x 0.027083 mm)                                                                                                            |
| `CHANGELOG.md:562-563`                                                                          | "within 0.6-2.3 % of the generated file on the real data"                                                                                                         | "a median 0.8 % longer; 90 % of files between -1.0 % and +3.9 %; extremes -3.4 % and +11.3 % (285 files over 5 mm)"                                                                                                         |
| `CHANGELOG.md` batch-size correction                                                            | "On 300 images", "CPU runs were not measured."                                                                                                                    | "On 300 synthetic images"; "On the CPU, 8 real images gave identical masks at batch 4, 2 and 1; skeletons at 8 and 1 differed in one value."                                                                                |
| `CHANGELOG.md:424-425`, `docs/KNOWN_ISSUES.md` CQ-07                                          | "served layout, callbacks and figure JSON" pinned by the split golden                                                                                             | "layout hash, callback wiring and clientside JS hash"                                                                                                                                                                       |
| `docs/DATA_DICTIONARY.md:148`                                                                   | "(corrections saved before 2026-09-29 are 341 x 256)"                                                                                                             | "(versions before commit `d1645d2`, which includes the current `main`, save 341 x 256)"                                                                                                                                 |
| `docs/DATA_DICTIONARY.md:132`                                                                   | "Saving an empty mask in *Correct* deletes the file"                                                                                                            | add: Generate then makes it again; a mask of another size than the photo is not loaded (M1)                                                                                                                                 |
| `docs/DATA_DICTIONARY.md:30`                                                                    | case-variant entry ending "Rare."                                                                                                                                 | add the replaced hand-corrected mask                                                                                                                                                                                        |
| `docs/MODELS.md:54`, `:82`                                                                    | cuDNN autotuning described as possible by default                                                                                                                 | deterministic cuDNN is the default since `abc34fa`; `SPROUTS_CUDNN_BENCHMARK=1` opts out                                                                                                                                |
| `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md:714-715`                    | the EXIF sentence                                                                                                                                                 | the text of KNOWN_ISSUES Q4                                                                                                                                                                                                 |
| same spec, section 20 and D23                                                                     | "1.5 s → 0.13 ms ... 1-3 %"                                                                                                                                      | the numbers of KNOWN_ISSUES E5                                                                                                                                                                                              |
| `docs/Architecture_2.md:54,78,773`                                                              | "ML via Subprocess", "Spawned in subprocess", "Sandboxing"                                                                                                        | inference runs on a `GuardedThread` in the app process                                                                                                                                                                    |
| `app/config.py:61-62`, `app/inference/metrics.py:17-18`                                       | "the imaging system is near-isotropic"                                                                                                                            | "the calibration constant is near-isotropic (18 / 640 against 13 / 480)"                                                                                                                                                    |
| `app/gui/shell_chrome.py:21-22`                                                                 | "well under 900 px instead of the ~1.2-1.9k px"                                                                                                                   | "about 700 px instead of the about 1040 px the full row needs"                                                                                                                                                              |
| `app/gui/main_window.py:209`                                                                    | scikit-image "~0.2 s"                                                                                                                                             | "1.8 to 2.5 s in the logged launches"                                                                                                                                                                                       |
| `app/handlers/mask_generation_handler.py:122`, `tests/test_robust_generation_handler_ux.py:4` | "about 0.7 s" / "about 1 s for 46k masks"                                                                                                                         | "about 0.6 s on six threads"                                                                                                                                                                                                |
| `app/gui/window/project_loader.py:170`                                                          | toast "No .jpg/.png images"                                                                                                                                       | build the text from `config.IMAGE_EXTENSIONS` (adds `.jpeg`)                                                                                                                                                            |
| `app/models.py:10`, `scripts/build_support.py:64`                                             | checkpoints "not committed"                                                                                                                                       | `checkpoints.zip` (skeleton model only) is tracked                                                                                                                                                                        |
| `CLAUDE.md` (after the owner's change is committed)                                             | line 55 checkpoints not committed; line 51 "on push"; line 70 "autocast"; line 71 "hand-traced masks"; lines 83-84 tokens source; the golden test's "figure JSON" | tracked zip; CI runs on `main`, `v3`, `refactor/**` and pull requests; no autocast; "hand-corrected masks" plus the cleared-mask exception; source is `app/design/tokens.py`; layout hash, callback wiring, JS hash |

- [ ] Before editing `CLAUDE.md:75` ("`EnhancedResnetGenerator`"), read `app/mask_model/`
  and write the class name that is there. The audit reports `ResNetSkeleton`; confirm it.

**Done when:** `git grep -n "hand-traced"` returns no line outside the changelog history,
and each row above is applied or carries a note why not.

**Commit:** one per file, `docs: <file> matches the code at <hash>`

### Wave 1: stop data loss (start now)

Lanes: **A** W1-T1. **B** W1-T2, then W1-T3, then W1-T4 (all three touch
`app/gui/correct/session.py` and `app/gui/editor_prompts.py`). **C** W1-T5 (its edit of
`app/gui/window/project_loader.py` waits for W1-T1's commit). **D** W1-T6. **E** W1-T7.

#### W1-T1: A job belongs to the folder it started in (R7)

**Why:** with folder B opened during the mask pass of A, the chained skeleton pass wrote 4
skeletons into B and replaced B's existing one; A got 6 masks and 0 skeletons. Script:
`scripts/reproducibility/r7_job_folder.py`.

**Files:**

- Modify: `app/gui/main_window.py::generate_all`,
  `app/handlers/skeleton_handler.py::generate_skeleton`,
  `app/handlers/generate_skeleton_handler.py::generate_skeleton`,
  `app/gui/window/project_loader.py` (`load_images`, `open_path`),
  `app/handlers/generation_base.py::_start`, `app/handlers/skeleton_handler.py::_start`.
- Test: Create `tests/test_generation_job_folder_binding.py`.

**Interfaces:** Produces `generate_skeleton(input_dir: str | None = None) -> bool` on both
handlers; `None` keeps today's behaviour (the open project, else the folder dialog).

**Behaviour:**

1. `generate_all` passes the folder the mask pass ran on to the chained
   `generate_skeleton(input_dir=...)`.
2. `open_path` and `load_images` refuse while `main_window.jobs` has a running job, before
   the folder dialog, with the wording a second job already gets: "Generating masks is
   running. Cancel it or wait before opening another folder."
3. A job refuses to start while a folder is loading.

**Tests:** existing `tests/test_generation_handlers.py`, `tests/test_generation_all_failed.py`,
`tests/test_library_main_window.py`, `tests/test_integration_main_window_wiring.py` stay
green. Add, with stub models as in the script:

- [ ] the chain writes into the mask pass's folder even when the open project changed in
  between (assert the file lists of both folders);
- [ ] `open_path` is refused while a job runs, and the progress row keeps the job's name
  and its Cancel button;
- [ ] a job is refused while a folder loads.

**Scientific output:** none. **Commit:**
`fix(generate): bind the skeleton pass to the mask pass's folder; no folder change during a job`

#### W1-T2: Never remove or replace a mask the editor did not load (M1, part A)

**Why:** photo 2048 x 1536 with a 640 x 480 model mask: Correct opens "No mask"; `Ctrl+S`
deletes `mask/<stem>.png` with no prompt. Script: `scripts/mask-calibration/m1_mask_size.py`.

**Files:**

- Modify: `app/gui/correct/mask_document.py` (`MaskRead`: `mismatch` becomes a reason
  field; `read`, `open`, `status`, `save`, the `_SIZE_MISMATCH` text),
  `app/gui/correct/mask_layer.py` (`_outcome`), `app/gui/correct/session.py`,
  `app/gui/editor_prompts.py` (the replace question), `app/gui/correct/read_back.py`.
- Test: Modify `tests/test_correct_mask_document.py`, `tests/test_correct_mask_layer.py`;
  add cases to `tests/test_correct_editor.py`.

**Interfaces:** Produces `MaskRead.not_loaded: str` (`""`, `"size"` or `"unreadable"`) and
`MaskDocument.unloaded_file() -> bool`. W3-T4 consumes both.

**Behaviour:**

1. The document remembers that a mask file exists but was not loaded (size it cannot map,
   or undecodable). Status is an error state "Mask not shown" with the reason in the
   tooltip, not "No mask". An undecodable mask gets a notice at open (today: none).
2. In that state `save()` with an empty plane writes and removes nothing and reports
   "Nothing to save: the saved mask was kept".
3. A save with painted pixels asks first through `editor_prompts`: "mask/<stem></stem>.png exists
   but could not be shown. Replace it?" (default: do not replace).
4. `PgDn`, which never prompts, stays on the image and warns, as it does for a polyline
   draft.

**Tests:** rewrite
`tests/test_correct_mask_document.py::test_wrong_size_mask_opens_empty_with_a_warning`,
`::test_undecodable_mask_opens_empty`,
`tests/test_correct_mask_layer.py::test_open_hands_back_the_documents_notices`. Add:

- [ ] an untouched `Ctrl+S` on a not-loaded mask never removes the file (bytes equal);
- [ ] an undecodable mask gives a notice and survives `Ctrl+S`;
- [ ] painting over a not-shown mask asks before replacing; "No" keeps the file;
- [ ] `PgDn` does not replace it;
- [ ] end to end through `CorrectEditor` with a 2048 x 1536 photo.

The goldens are unchanged (both cases are 640 x 480 with an equal-size mask or none).

**Scientific output:** none. **Commit:**
`fix(correct): a mask that could not be shown is never removed or replaced silently`

#### W1-T3: A skeleton Save writes a correction only when it differs (L2, V1) — gate O2

**Why:** `Ctrl+S` on an untouched skeleton turned T100_L049's generated 341 x 256,
46.79 mm into a 640 x 480 "correction" of 45.99 mm. Script:
`scripts/length/exp_l1_l2_window.py`.

**Files:**

- Modify: `app/gui/correct/skeleton_document.py::save` (a cached generated baseline),
  `app/gui/correct/skeleton_layer.py::save`, `app/gui/correct/layer.py:68` (comment:
  `cleared` is no longer mask only), `app/gui/correct/editor.py::_report` (a removal
  refreshes the Library's corrected state), `app/gui/correct/controls.py:221` (tooltip),
  `README.md:220-222`, the spec's D7 note, `CHANGELOG.md` (*Changed*).
- Test: Modify `tests/test_correct_editor.py`; create
  `tests/test_skeleton_save_only_when_changed.py`.

**Interfaces:** Produces `SkeletonSaveResult(ok, path=None, error="", unchanged=False, removed=False)` and a helper `is_generated_as_opened(mask, generated_path, photo_size) -> bool` (the skeleton equals `thin_raster(generated, photo size)`; no generated file means
"equals empty"). W3-T1 consumes the helper. Put it next to `thin_raster`; if
`tests/test_layering_contract.py` forbids `app/inference` importing from
`app/gui/skeleton_graph_model.py`, move `thin_raster` and the helper into `app/editing/`
and re-export.

**Behaviour:**

1. Save on a skeleton with nothing unsaved writes nothing and says "Skeleton unchanged:
   nothing to save" (info toast). The session rule "dirty layers, else the active one"
   stays; the layer decides.
2. Save of an unsaved skeleton that equals the generated skeleton as Correct opens it
   writes no correction and removes an existing one: "Skeleton is the generated one again:
   correction removed".
3. An empty skeleton over a non-empty generated one is a real correction and is written.
4. The mask half of D7 is not changed here (W4-T1 decides the mask's clean-state button).

**Tests:** breaks by design
`tests/test_correct_editor.py::test_saving_with_nothing_unsaved_saves_the_edited_layer_only[skeleton]`.
Re-read `tests/test_skeleton_loaded_file_unsaved.py` (two tests; green when the loaded
file differs from the generated one). `tests/test_correct_session.py::test_save_with_nothing_dirty_saves_the_active_layer`
and the pixel goldens stay green. Add:

- [ ] a no-op Save writes no file and reports `unchanged`;
- [ ] edit, undo, Save writes nothing;
- [ ] a correction edited back to the generated skeleton is removed and the Library drops
  its mark;
- [ ] an emptied skeleton over a non-empty generated one is written;
- [ ] Load generated over a correction, Save: the correction is removed.

**Scientific output:** no CSV value; the no-op file is no longer created. **Commit:**
`fix(correct): Save writes a skeleton correction only when it differs from the generated one`

#### W1-T4: Files changed on disk: Discard reloads, Save asks (E2) — gate O19

**Why:** after an outside rewrite, Discard leaves the stale plane under "Saved"
(10,000 px differ), and Save, the prompt's Save and `PgDn` overwrite the changed file
without asking (1,900 px of the other writer lost). A Generate run is such a writer.

**Files:**

- Modify: `app/gui/correct/session.py` (`_revert`, `_save`, the `Prompts` protocol),
  `app/gui/correct/layer.py` (optional `write_stamps()`, default `file_stamps()`),
  `app/gui/correct/skeleton_layer.py`, `app/gui/correct/skeleton_document.py`
  (correction-only stamp), `app/gui/correct/editor.py` (`paging.forget` after a reload),
  `app/gui/editor_prompts.py` (third question), `app/gui/raster_editor_base.py`
  (`revert_to_saved`), `app/gui/gt_tracing_interface.py` (`save_mask`, `_navigate`),
  `app/gui/main_window.py::on_outputs_changed`, `tests/correct_fakes.py` (`FakePrompts`),
  `README.md:232`.
- Test: Create `tests/test_correct_changed_on_disk.py`, `tests/test_gt_changed_on_disk.py`.

**Behaviour:**

1. Discard: when `layer.file_stamps()` differs from the stamps taken at open, reopen that
   layer from disk instead of reverting in memory, say once "Mask reloaded: the file
   changed on disk", and take the stamps again in both branches. Trace GT:
   `revert_to_saved` calls `_open_plane` when `_document_changed()`.
2. Save: when the file the layer writes changed since open or last save, ask
   "mask/a.png changed on disk after you opened it" with *Overwrite*, *Reload (lose my
   edits)*, *Cancel* (default Cancel). Any answer but Overwrite is a not-ok outcome, so
   `PgDn` stays and a leave is refused. A clean document reloads instead of asking. The
   skeleton compares only the correction file's stamp.
3. No false alarm: on a stamp mismatch, read the file and compare its pixels with the
   saved baseline; equal pixels only refresh the stamps.
4. After a Generate run, `on_outputs_changed` re-selects the image open in Correct when it
   has no unsaved edits.

**Tests:** existing pins stay valid with a stamp-conditional reload
(`tests/test_correct_editor_prompt_flow.py:99,114,141`,
`tests/test_correct_read_ahead_handback.py:180,200`,
`tests/test_correct_mask_document.py:369,391`, `tests/test_gt_editor_discard.py`,
`tests/test_gt_editor_open.py:124`, `tests/test_correct_session.py:463-529`). Add:

- [ ] Discard after a rewrite shows the file and "Saved" (mask, skeleton, GT);
- [ ] Save after a rewrite asks; one test per answer;
- [ ] `PgDn` stays;/
- [ ] equal pixels with a new mtime ask nothing;
- [ ] a clean `Ctrl+S` after a rewrite reloads;
- [ ] the open image follows a finished Generate run.

**Scientific output:** none in a normal session. **Commit:**
`fix(editors): reload a file changed on disk on Discard; ask before Save overwrites it`

#### W1-T5: One rule for "the same image": stems without case, one format list (R4, R3)

**Why:** `A.png` + `a.jpg`: 3 "written" for 2 files, the winner random (10 against 10 in
20 runs), and *Keep existing masks* replaced a hand-corrected mask. Mixed formats: Library
and masks 3 files, skeletons and length rows 7. Scripts:
`scripts/reproducibility/r4_case.py`, `r4b_manual_mask.py`, `r3_formats.py`.

**Files:**

- Modify: `app/naming.py` (`stem_key`), `app/project_layout.py` (`_scan_ranked`, `_scan`
  keyed by `stem_key`, returning the winner's real stem; `MASK_EXTENSIONS = IMAGE_EXTENSIONS`), `app/inference/mask_origin.py::scan_existing_masks`,
  `app/inference/mask_inference.py::list_inputs` (skip by key),
  `app/gui/image_manager.py` (`masks`, `skeleton_stems`, `corrected_stems` keyed by the
  Library's stem; count skipped files), `app/data_processing/measurement_index.py`
  (`_key`), `app/gui/window/project_loader.py` (the load toast),
  `app/inference/skeleton_inference.py` (R3: delete `IMG_EXTENSIONS`, `is_image_file`,
  `_INPUT_EXTENSIONS`; `make_dataset` calls `scan_images()` with the default),
  `docs/DATA_DICTIONARY.md`, `CHANGELOG.md` "Scientific output changes".
- Test: Create `tests/test_stem_case_and_formats.py`.

**Interfaces:** Produces `app.naming.stem_key(stem: str) -> str`, which is `stem.lower()`
(not `casefold()`: it would merge `straße` and `strasse`). W3-T1 consumes it.

**Behaviour:**

1. Stems are compared through `stem_key` wherever a stem meets a file: the image scan, the
   existing-mask scan, the skip list, the Library's has-mask / has-skeleton lookups, the
   measured-value lookup.
2. The existing clash rule picks the winner (`.png` before `.jpg` before `.jpeg`; equal
   extensions: the name that sorts first). Outputs carry the winner's stem.
3. On load, say what is hidden: "1 image is not listed: a.jpg has the same name as A.png".
4. R3 (gate O6): skeleton generation reads `config.IMAGE_EXTENSIONS` only; on load, "3
   files (.tif, .bmp) are not supported and are not listed; convert them to PNG or JPEG".
   `naming.py` keeps the seven suffixes as "suffixes stripped from a name", so existing
   rows of `.tif` images keep their stems.
5. Gate O7: the rule applies on every platform. If the owner says no, guard step 1 with
   "the file system is case-insensitive" and record it.

**Tests:** `tests/test_robust_generation_stem_clash.py` passes unchanged;
`tests/test_robust_project_scan_speed.py` compares against a verbatim old scan: read it
first. Invert `test_skeleton_generation_still_reads_its_other_formats`; switch
`test_inputs_without_a_shared_stem_are_unchanged` to `config.is_image_file`. Add:

- [ ] the `r4_case.py` folder gives one Library image and one output from the winner in 20
  runs, and written == files;
- [ ] a manual mask survives *Keep existing* whatever its file-name case;
- [ ] `lookup("A")` finds a row named `a`;
- [ ] for a mixed-format folder, Library stems == mask inputs == skeleton inputs.

**Scientific output:** only folders with case-variant stems or with `.bmp` / `.tif` /
`.tiff` / `.ppm` next to supported files. **Commit:**
`fix(project): stems compare without case; skeleton generation reads the Library's formats`

#### W1-T6: A mask saved by hand during a Generate run survives it (X4)

**Why:** read from code, not reproduced: the overwrite question is asked at the start of a
run, so a mask saved in Correct while the run is under way can be replaced when the run
reaches that image.

**Files:** Modify `app/inference/mask_inference.py` (`make_mask_spec.save`, `list_inputs`),
`app/inference/mask_origin.py`. Test: Create `tests/test_generation_keeps_masks_saved_during_run.py`.

- [ ] Reproduce first with a stub model whose `step` writes a hand mask for a later stem.
  If the mask survives today, close X4 in `docs/KNOWN_ISSUES.md` and stop.
- [ ] Fix: the scan records each existing mask's `(size, st_mtime_ns)`; `save` stats the
  target once and skips the write when the file appeared or changed since the scan. The
  run report counts it ("1 mask saved during the run was kept").
- [ ] Tests: the hand mask's bytes are unchanged after the run; the report names it; a
  folder nobody touches gets the same files as before.

**Scientific output:** none. **Commit:** `fix(generate): keep a mask that was saved while the run was under way`

#### W1-T7: Add the Pix2Pix licence notice (F1, step 1)

**Files:** Create `THIRD_PARTY_NOTICES.md` (the full pytorch-CycleGAN-and-pix2pix BSD
text, three blocks). Modify `app/inference/skeleton_inference.py` (a header comment naming
the origin of lines 59-252), `app/assets/THIRD_PARTY.md` (a pointer).

- [ ] Copy the notice from the upstream repository's `LICENSE` file, verbatim.
- [ ] Test: add to `tests/test_fair_metadata.py` that `THIRD_PARTY_NOTICES.md` contains
  "Jun-Yan Zhu".

**Commit:** `docs(licence): reproduce the pix2pix BSD notice for the skeleton generator`

### Wave 2: records and wording (no CSV value changes)

Lanes: **A** W2-T1, W2-T3 (run records). **B** W2-T2, then W2-T4 and W2-T11 (Measure
records; all touch `measure_run.py`). **C** W2-T5, then W2-T6 (masks). **D** W2-T7.
**E** W2-T8 (texts; after lanes A to D to avoid text conflicts). **F** W2-T9. **G** W2-T10.

#### W2-T1: Run records survive a no-op run; runs have a history (R6) — gate O16

**Files:** Modify `app/inference/run_record.py` (`write_run_manifest`, the history append),
`app/provenance.py` (`RUN_HISTORY_FILENAME = "_sprouts_runs.jsonl"`), the two call sites,
`docs/DATA_DICTIONARY.md`, `README.md` "Outputs". Test: Create `tests/test_run_record_history.py`;
modify `tests/test_generation_handlers.py::test_no_existing_masks_means_no_prompt` (it
pins the listing of `mask/`).

**Behaviour:** a run that wrote nothing and failed nothing leaves `_sprouts_run.json`
alone. A run in which every input failed still writes. Every run that changed the folder
appends one compact line to `_sprouts_runs.jsonl`; a partial run's line lists the written
inputs (up to 1,000 names, else a count and a names digest).

**Tests to add:** a *Keep existing* run with nothing missing leaves the record
byte-identical; a cancel before the first batch does too; a partial run appends a line and
replaces the latest; an all-failed run still writes.

**Commit:** `feat(provenance): keep the run record on a no-op run; append runs to a history`

#### W2-T2: Measure records its inputs; "changed since measured" compares with the record (R5)

**Files:** Modify `app/inference/metrics.py` (read the bytes once, return the fingerprint
beside the row; rows unchanged), `app/inference/measure_run.py` (`_write_inputs_record`),
`app/provenance.py`, `app/gui/stage_summary.py` (`Freshness.removed`, `.new`),
`app/gui/stage_status.py`, `app/data_processing/measurement_index.py::outdated`,
`app/gui/metrics_bar.py` (tooltips), `docs/DATA_DICTIONARY.md`, `README.md`. Test: Create
`tests/test_measure_inputs_record.py`.

**Interfaces:** Produces `<csv>.inputs.json`: `{"csv_sha256": ..., "inputs": {name: [size, mtime_ns, sha256]}}`. W2-T8's "Measure again?" prompt and W3-T1 consume it.

**Behaviour:** at the check, an input whose size and time equal the record is current; one
whose stamp differs is hashed and is current when the hash matches; an input without an
entry is new; an entry without a file is removed. Hash results are kept in memory for the
session. A record whose `csv_sha256` does not match the CSV on disk is ignored. A CSV
without a record keeps today's rule, and the tooltip says the comparison is by file time.

**Tests:** the existing freshness tests write CSVs by hand without a record, so they keep
testing the fallback (`tests/test_shell_stage_summary.py`,
`tests/test_shell_metrics_per_metric_staleness.py`,
`tests/test_shell_info_measurement_index.py`). Add: stamped anew with equal content is
current; regenerated identically is current; a repainted mask with an older time is
changed; a deleted input is reported; a new input is reported; a record for another CSV is
ignored. The CSVs stay byte-identical.

**Commit:** `feat(measure): record the measured inputs; freshness compares content, not only times`

#### W2-T3: Run records say which batch shapes ran (R1, step 1)

**Files:** Modify `app/inference/batch_engine.py` (`BatchReport.batch_shapes`,
`.oom_events`), `app/inference/run_record.py`. Test: Modify
`tests/test_generation_batch_engine.py`.

**Behaviour:** `outputs.batch_shapes` (`{"16": 2880, "12": 12}`: images per forward size)
and `outputs.oom_events` (`[{"at": "<input name>", "from": 16, "to": 8}]`) in
`_sprouts_run.json`. No output pixel changes.

**Commit:** `feat(provenance): record the batch shapes and out-of-memory events of a run`

#### W2-T4: Record the measured pixel sizes; warn on mixed sizes (M2, step 1)

**Files:** Modify `app/inference/measure_run.py::_write_provenance`,
`app/inference/metrics.py`, `app/handlers/skeleton_handler.py::_on_finished`,
`app/visualization/dash_export.py::provenance_lines` (also print the field of view with
two decimals), `app/gui/metrics_bar.py::fov_text`, `app/gui/about_dialog.py`, `README.md`,
`docs/DATA_DICTIONARY.md`. Test: Modify `tests/test_measure_core.py`,
`tests/test_viz_dash_export.py`.

**Behaviour:** provenance gains `inputs.sizes_px` (a small histogram). One warning toast
when the measured files have more than one size or aspect. The export header, the metrics
bar and About take the calibration from the CSV's sidecar and say when it differs from the
running code's.

**Commit:** `feat(measure): record pixel sizes, warn on mixed sizes, show the CSV's own calibration`

#### W2-T5: Mask origin in the file; "Replace model masks only" (M4) — gate O9

**Files:** Modify `app/inference/mask_origin.py` (`classify_mask`, `_HEADER_BYTES`,
`ExistingMasks`, `OverwritePolicy` with a third value, `skip_stems`),
`app/inference/mask_inference.py::make_mask_spec.save`, `app/masks/store.py`
(`encode_mask_png`, `write_mask_file(origin=...)`),
`app/gui/correct/mask_document.py::save` (passes `edited`),
`app/handlers/mask_generation_handler.py::ask_overwrite_policy`,
`docs/DATA_DICTIONARY.md`, `README.md:137`. Test: Modify
`tests/test_generation_mask_origin.py`, `tests/test_mask_origin_scan_fast.py`,
`tests/test_robust_mask_origin_scan.py`, `tests/test_generation_handlers.py:350-365`,
`tests/test_prompt_dialog_lifecycle.py`.

**Behaviour:**

1. Both writers add a PNG `tEXt` chunk `sprouts_mask` right after IHDR:
   `{"v": 1, "origin": "model" | "editor", "edited": true | false, "app": "<version>"}`.
2. `classify_mask`: chunk present, its origin; chunk absent, today's colour-type rule,
   reported as a separate group in the prompt ("from the file format only").
3. A third answer in the prompt: "Replace model masks only (keep N saved in Correct)".
4. If O9 is yes: clearing a mask in Correct writes an empty mask file instead of deleting
   it, so Generate keeps it and Measure writes 0.0. This adds rows of 0.0 to
   `root_areas.csv` for such images: record it under "Scientific output changes".

**Tests to add:** the chunk round-trips through both writers; a chunk beats the colour
type in both directions; a re-saved file without a chunk lands in the "format only" group;
the third policy skips exactly the editor-saved stems; the 46k scan stays within today's
time. The goldens compare shape, colour type, pixel hash and area: all identical with the
chunk (checked by `scripts/mask-calibration/m4b_text_chunk.py`).

**Commit:** `feat(masks): record a mask's origin in the file; offer "Replace model masks only"`

#### W2-T6: One mask threshold; count non-binary masks (M5) — gate O10

**Files:** Modify `app/masks/store.py` (`MASK_THRESHOLD = 127`, shared with
`app/inference/metrics.py::load_mask`), `app/inference/measure_run.py::_write_provenance`
(`outputs.n_non_binary`), `app/handlers/skeleton_handler.py::_on_finished` (one warning),
`docs/DATA_DICTIONARY.md` (the alpha rule). Test: Modify `tests/test_mask_editor_store.py`
(check for literal 200 / 201); add "Measure and the mask store agree on a mask with grey
levels" and "the non-binary count is recorded".

**Commit:** `fix(masks): Correct reads masks at Measure's threshold; Measure counts non-binary masks`

#### W2-T7: Detect and flag old-format corrections (L5) — gate O13

**Files:** Modify `app/gui/correct/skeleton_document.py` (`is_legacy_correction`: the
correction's size differs from the photo's; a notice on open with "Start again from the
generated skeleton"), `app/gui/library_index.py::status_text`,
`app/gui/window/metrics.py`, `app/gui/image_manager.py` (a lazy check for the selected
image). Test: Create `tests/test_legacy_correction_flag.py`.

**Tests to add:** a 341 x 256 correction over a 640 x 480 photo is reported old-format; a
photo-sized one is not; the Library and Correct texts.

**Commit:** `feat(correct): flag skeleton corrections saved in the old 341 x 256 format`

#### W2-T8: Wording pass (L3, L4, R8, G4 tier 1, X9) — gates O14, O17

**Files:** Modify the files in the table; `app/gui/correct/layer_panel.py:142`,
`app/gui/ui_panels.py:217-219,235-248`, `app/gui/window/stages.py:126,130`,
`app/gui/visualization_manager.py:120,133,142`, `app/gui/correct/editor.py` (X9: with no
image open show "Select an image in the Library" instead of the tool hint),
`app/inference/skeleton_inference.py:57`, `app/gui/image_manager.py:389`,
`app/gui/window/editors.py:104`, `app/inference/measure_run.py:56` (L4: one constant from
`project_layout.py`). Test: Modify `tests/test_correct_main_window.py:284`,
`tests/test_library_index.py:175`; create `tests/test_wording_guards.py`.

Exact string changes (G4 tier 1):

| File:line                                          | Old                                                                                                            | New                                                                                                                                                                                                                                                                              |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/gui/shell_chrome.py:112`, `README.md:113` | `Paint ground-truth classes for a segmentation dataset.`                                                     | `Paint training labels by hand (ground truth for a new model). Separate from masks and skeletons; never measured.`                                                                                                                                                             |
| `app/gui/shell_chrome.py:123`, `README.md:115` | `Fix the mask (area) and the skeleton (length) of an image on one canvas.`                                   | `Fix the model's mask (area) and skeleton (length) of an image on one canvas.`                                                                                                                                                                                                 |
| `app/gui/shortcuts.py:154`                       | `Show or hide the overlay`                                                                                   | `Show or hide the tracing`                                                                                                                                                                                                                                                     |
| `app/gui/shortcuts.py:220`                       | `Pick the n-th labelled class; 0 = Ignore`                                                                   | `Pick the n-th class; 0 = Ignore`                                                                                                                                                                                                                                              |
| `app/gui/shell_chrome.py:358`                    | `Export images + class-id masks as a dataset`                                                                | `Export the traced images and their class-id label images as a training dataset`                                                                                                                                                                                               |
| `app/gui/export_dataset_dialog.py:169`           | `Copies GT-traced images and remapped class masks into a training-ready layout.`                             | `Copies the traced images and their class-id label images (masks/) into a training-ready layout.`                                                                                                                                                                              |
| `README.md:128`                                  | `hand-traced (white on transparent)`                                                                         | `saved in Correct (hand-corrected; white on transparent)`                                                                                                                                                                                                                      |
| `docs/DATA_DICTIONARY.md:15,64,130`              | `hand-traced`                                                                                                | `hand-corrected`                                                                                                                                                                                                                                                               |
| `docs/MODELS.md:42`                              | `(hand-traced masks still work)`                                                                             | `(masks painted in Correct still work)`                                                                                                                                                                                                                                        |
| `README.md:267`                                  | `**Editor:** the same brush / eraser / fill canvas as the mask in Correct, plus a class panel on the right.` | `**Editor:** a brush / eraser / fill canvas with a class panel on the right. The tools work as on the mask in Correct, but the two stages write different files: Correct fixes the model's mask in mask/ (measured), Trace GT paints training labels in gt/ (never measured).` |
| `README.md` after line 122                       | (none)                                                                                                         | a three-row table "Which editor?": fix what the model produced for an image, Correct; make labels to train a model, Trace GT; they never read each other's files                                                                                                                 |
| `app/gui/library_index.py:225`                   | `Mask saved`                                                                                                 | `Mask ready`                                                                                                                                                                                                                                                                   |
| `app/inference/mask_origin.py:48` (comment)      | `saved by the Correct Mask editor`                                                                           | `saved by the Correct editor`                                                                                                                                                                                                                                                  |

Other texts:

- L3: Layers rows get a tooltip: "Mask: gives root area. The skeleton and root length do
  not follow it." and "Skeleton: gives root length. The mask and root area do not follow
  it." Put the lost sentence back on the Generate page (`shell_chrome.py:366-369`) and
  delete the dead text in `ui_panels.py:217-219`.
- R8: buttons "Measure Length" and "Measure Area", and the three texts that quote them;
  their tooltips name their inputs ("generated skeletons in skeletons/", "masks in
  mask/"). After W2-T2: when nothing changed since the CSV, the button asks "Nothing
  changed since the last measurement. Measure again?".
- L4: `docs/DATA_DICTIONARY.md` and `README.md` get one sentence: "`_fake` is the Pix2Pix
  name for a generated image; `<stem>_fake.png` is the generated skeleton."

**Tests to add:** no string literal under `app/` matches `hand-traced`; none under
`app/gui/correct/` matches `\btrac`; no `"_fake.png"` literal in `app/` outside
`project_layout.py`; the tooltips of the two Layers rows and the two Measure buttons.

**Commit:** `docs(ui): one vocabulary for Correct and Trace GT; say what each layer measures`

#### W2-T9: Say which canvas path is active (P6) — gate O29

**Files:** Modify `app/gui/canvas_gl.py` (`canvas_line() -> str`),
`app/gui/about_dialog.py` (a "Canvas" row and a line in `details_text`), the status-line
device chip (a tooltip line), one warn toast when the slow path was not chosen by the
user. Test: Modify `tests/test_shell_info_about.py`.

**Commit:** `feat(shell): About and the device chip say whether the canvases use OpenGL`

#### W2-T10: Write down the EXIF policy and pin it (M3)

**Files:** Modify `README.md` (Calibration notes: "the stored pixel grid everywhere; EXIF
orientation is never applied"), `app/gt/export.py` (`_MANIFEST_FIELDS` gains
`exif_orientation`; one summary line when any exported image carries a tag other than 1).
Test: Create `tests/test_exif_policy.py` on the helper
`tests/test_enhance_core.py::exif_rotated_jpeg`: mask generation and skeleton generation
keep the stored geometry of a tagged JPEG (stub models, as in
`scripts/mask-calibration/m3_exif.py`); GT export reports the tag. Check
`tests/test_robust_gt_export_atomic.py:69` and the GT export tests that compare the
manifest header.

**Scientific output:** none for the app's CSVs; the GT export manifest gains a column.
**Commit:** `test(exif): pin the stored-grid policy; GT export flags tagged photos`

#### W2-T11: Report masks whose image is gone (X2) — gate O37

**Files:** Modify `app/handlers/skeleton_handler.py` (the area run, around line 112),
`app/inference/measure_run.py::_write_provenance` (`inputs.n_without_image`, names up to
100). Test: Modify `tests/test_measure_core.py`.

**Behaviour (recommended answer):** rows stay as they are; the end-of-run notice says "3
masks have no image in the project" and provenance lists them.

**Commit:** `feat(measure): report masks and skeletons whose image is no longer in the project`

### Wave 3: scientific output changes (each behind its owner answer)

Rule for every task here: before the change, run Measure on the synthetic test set and on
the 3000-pair real sample (`scripts/length/sample_real.py` copies every 15th pair of
`D:\2024 EF image data`, read-only) and keep the CSVs; run it again after; put the diff
summary (rows changed, median and extreme change) into the task's CHANGELOG entry.

#### W3-T1: Measure the corrected skeleton (L1, FR-03) — gate O1; after W1-T3, W2-T7

**Files:**

- Create: `app/inference/length_inputs.py` (Qt-free): `resolve_length_inputs(layout, skeletons_dir, use_corrections) -> dict[str, tuple[str, str, str]]` (stem to path,
  source, skipped reason), `correction_state(...)`, a header-only `image_size(path)`.
- Modify: `app/config.py` (`LENGTH_CSV_HEADERS` + `"Source"` as the last column;
  `MEASURE_CORRECTED_SKELETONS`), `app/inference/measure_run.py` (`MeasureRun(..., sources=...)`, the provenance block), `app/handlers/skeleton_handler.py::calculate_root_length`,
  `app/data_processing/measurement_index.py` (`_read_metric_csv`, `_Row`, `Measurement`
  gains `length_source` with a default, `outdated`, `_table_outdated`),
  `app/gui/window/metrics.py`, `app/gui/metrics_bar.py`,
  `app/gui/library_index.py::status_text`, `app/gui/stage_status.py`,
  `app/gui/stage_summary.py`, `app/gui/main_window.py:177`,
  `app/gui/window/editors.py::sync_open_correction`, `app/gui/ui_panels.py:235-238`,
  `README.md`, `docs/DATA_DICTIONARY.md`, `CHANGELOG.md`.
- Test: Create `tests/test_length_inputs.py`, `tests/test_measure_corrected_skeletons.py`.

**Behaviour:**

1. A valid correction always wins (not "the newer file": a second Generate rewrites every
   `*_fake.png`). Valid: readable, the photo's size (else old format, reason
   `legacy_size`), and not equal to the generated skeleton as Correct opens it (else
   `unedited`; helper from W1-T3). An invalid correction falls back to the generated file
   and is counted and named.
2. The CSV gains a last column `Source`: `generated` or `corrected`.
3. Provenance: `parameters.skeleton_source` (`"corrected-first"` or `"generated"`),
   `inputs.corrections` (`folder`, `n_used`, `files`: stem to SHA-256, `skipped`: stem and
   reason), `outputs.n_corrected`.
4. `MeasurementIndex.outdated()["length"]`: the measured file is newer than the CSV, or
   the row says `generated` and a correction newer than the CSV exists, or the row says
   `corrected` and the correction is gone.
5. Metrics bar and Library: the LENGTH tooltip names the source; the two "not measured"
   texts show only when a correction exists that the row did not use; connect
   `correct_editor.skeleton_saved` so LENGTH turns outdated at once.
6. Status line: "20 skeletons (2 corrected): ...". Dashboards need no change (they read by
   column name).

**Build order:** (a) resolver, validity rules and tests with the switch off: the first
seven columns stay byte-identical; (b) the `Source` column and the switch on, with O1.

**Tests:** break by design:
`tests/test_foundation_core_config.py::test_existing_contract_values_unchanged`,
`tests/test_fair_docs.py::test_data_dictionary_documents_every_metric_csv_column`, the two
byte-identity tests of `tests/test_measure_core.py` (give the legacy reference
`"Source": "generated"` or compare the first seven columns). Change with the switch on:
`tests/test_shell_library_corrections.py::test_the_metrics_bar_says_skeleton_corrected_and_what_is_measured`,
two tests in `tests/test_shell_metrics_per_metric_staleness.py`,
`tests/test_shell_window_split_surface.py:284-285`, the three
`receivers(editor.skeleton_saved) == 0` assertions,
`tests/test_correct_main_window.py::test_a_skeleton_save_changes_neither_has_skeleton_nor_the_metrics_bar`.
Already prepared: `tests/correct_goldens_support.py:193-194` holds
`length_mm_if_measured_informational` (28.86 and 60.83 mm), which become asserted values.
Add: correction wins; legacy skipped; unedited skipped; a correction without a generated
file gets a row; switch off equals today's inputs; `Source` values; the provenance block;
the three `outdated` rules; a saved mask still never outdates length; one window test:
draw, save, measure, the row is the correction's value.

**Scientific output:** corrected images only. The owner's project has 2 such rows today
(+11 % and +10 %). **Commit:** two: `feat(measure): resolve length inputs (corrections first, switch off)` and `feat(measure): measure hand-corrected skeletons; Source column`.

#### W3-T2: Calibration: the constant, then a per-project file (M2, step 2) — gate O3

**Files:** Create `app/calibration.py` (Qt-free reader of `<project>/sprouts_project.json`:
`{"schema": 1, "calibration": {"fov_width_mm": 18.0, "fov_height_mm": 13.5, "reference_size_px": [640, 480], "source": "..."}}`; default `config.CALIBRATION`).
Modify `app/project_layout.py`, `app/config.py` (default only),
`app/inference/metrics.py` (`root_length_mm(skeleton, calibration)`,
`root_area_mm2(mask, calibration)`), `app/inference/measure_run.py`
(`MeasureRun(..., calibration=)`), `app/provenance.py` (`calibration_info`,
`build_record(..., calibration=)` with `source` "project" or "default"),
`app/handlers/skeleton_handler.py::_measure`,
`app/data_processing/measurement_index.py`, the GUI and export sites of W2-T4. Test:
Create `tests/test_project_calibration.py`.

**Behaviour:** a project file changes the values and is recorded in the sidecar; a missing
or invalid file falls back to the default and says so; `reference_size_px` lets Measure
warn when a photo is not the size the field of view was stated for.

**Tests:** none of the 18 x 13 pins breaks with the default (`tests/test_regression.py`,
`tests/test_measure_core.py`, `tests/test_foundation_core_config.py`,
`tests/test_foundation_core_provenance.py`, `tests/test_metrics_bar_smoke.py`,
`tests/test_shell_info_about.py`, `tests/test_viz_dash_export.py`). If the owner changes
the default to 13.5, all of them change, and the goldens' `area_mm2_as_measured` become
15.08 and 39.55: that is one separate commit with its own CHANGELOG entry (every area
x 1.03846; lengths +1.1 to +3.7 %).

**Commit:** `feat(calibration): per-project field of view, recorded with every measurement`

#### W3-T3: Fixed-shape batch mode (R1, step 2) — gate O4; after W2-T3

**Files:** Modify `app/config.py` (skeleton fixed value 64),
`app/inference/runtime.py` (`BATCH_POLICY_ENV = "SPROUTS_BATCH_POLICY"`,
`RuntimeConfig.batch_policy`), `app/inference/batch_engine.py` (`run_batches(pad_to=, backoff=)`, an error class for the stop), `app/inference/mask_inference.py::generate_masks`,
`app/inference/skeleton_inference.py` (`default_skeleton_batch_size`,
`run_skeleton_generation`), README, `docs/DATA_DICTIONARY.md`, CHANGELOG.

- [ ] First, one GPU session on 300 real images (about an hour, nothing else on the GPU):
  padded tail against full batch (expected identical; verified on the CPU only), skeleton
  64 against 128 (pixels and seconds), peak VRAM of masks at 16 and skeletons at 64. Stop
  and report if the padded tail is not identical.
- [ ] Then the mode: every forward runs at exactly one N; the last batch is padded with
  zero tensors and the padded outputs dropped; on out of memory free the cache, retry
  once, then stop the run with "Out of GPU memory at the fixed batch size (16). Close
  other GPU programs, or set `SPROUTS_BATCH_POLICY=auto` ...".

**Tests:** all of `tests/test_generation_batch_engine.py`,
`tests/test_generation_oom_backoff.py`, `tests/test_foundation_core_config.py`,
`tests/test_jobs_cancel_cpu_fallback.py` stay green when `auto` keeps today's code path.
Add: a padded tail runs `[3, 3, 3]` and writes the same 7 files; fixed policy plus out of
memory stops after one retry, record `failed`, no halving; fixed policy never calls
`mem_get_info`.

**Commit:** `feat(inference): fixed batch shapes for reproducible outputs (SPROUTS_BATCH_POLICY)`

#### W3-T4: Show a model mask on a photo of another size (M1, part B) — gate O8; after W1-T2

**Files:** Modify `app/gui/correct/mask_document.py::read` (`MaskRead.resized_from`),
`app/gui/correct/read_back.py`. Test: extend `tests/test_correct_mask_document.py`.

**Behaviour:** a model mask (8-bit grey today; the origin chunk of W2-T5 once it exists)
whose size differs is stretched to the photo with `cv2.INTER_NEAREST` and opens clean,
`has_saved_file = True`, with the notice "Mask is 640 x 480; shown at the photo's
2048 x 1536. Saving stores it at the photo's size." An editor-saved mask of another size
stays under W1-T2's rule.

**Tests to add:** x2 is exact; x3.2 and 16:9 open resized, clean, with the notice; the
scenario table of `scripts/mask-calibration/m1b_resize_area.py` as assertions.

**Scientific output:** only when such a mask is saved from Correct: x2 exact; x3.2 total
+0.013 %, worst image 1.45 %. Goldens unchanged. **Commit:**
`feat(correct): open a model mask of another size stretched to the photo`

#### W3-T5: A 1 px click paints its pixel (E4) — gate O11

**Files:** Modify `app/gui/paint_canvas.py::PaintCanvas._draw` (first-dab branch: for
`self._size <= 1`, `painter.fillRect(QRect(x, y, 1, 1), colour)` in the same composition
mode), `CHANGELOG.md`, the comment at `tests/test_canvas_zoom_quality.py:328`. Test:
extend `tests/test_paint_canvas.py`.

**Tests to add:** a size-1 click paints exactly its pixel; the eraser clears exactly it;
in `PaintCanvas`, `MaskLayer` and Trace GT; sizes 2 to 9 give today's dab (hash of a fixed
set); a size-1 click is a real undo step. The golden tests stay green (checked by replay).

**Scientific output:** +1 px per size-1 click (0.00076 mm² at 640 x 480), and +1 px at the
start of an exact anti-diagonal size-1 drag. **Commit:** `fix(paint): a click with the 1 px brush paints its pixel`

#### W3-T6: Formats (R3) — gate O6

Built in W1-T5 (behaviour 4), because it shares one function with the stem rule. It is
listed here because it is a scientific output change for folders that mix `.bmp`, `.tif`,
`.tiff` or `.ppm` with supported files: those images get no new skeleton and no new
length row. If O6 is "all seven everywhere" instead, do not build behaviour 4; write a
separate plan that checks every reader (the display decodes with OpenCV, generation with
Pillow: a 16-bit TIFF scales differently in the two).

#### W3-T7: File names, not absolute paths, in `Error` cells (X3) — gate O38

**Files:** Modify `app/inference/metrics.py:64,76`. Test: Modify `tests/test_error_column.py`.

**Scientific output:** the text of the `Error` column of rows that failed. **Commit:**
`fix(measure): the Error cell names the file, not its absolute path`

#### W3-T8: The length estimator's bias (X1) — gate O12

- [ ] Recommended (lead): no code change. Add to `README.md` (Calibration) and
  `docs/DATA_DICTIONARY.md`: the 8-connected arc length reads 0 % long at 0° and 45° and up
  to +8.2 % at 22.5°, about +4.4 % on mixed synthetic shapes; lengths are comparable with
  each other, not with a ruler, to that margin.
- [ ] Only on the owner's word: other weights (the audit's trial brought the total to
  -1 %). That moves every length in every CSV: its own plan, with a calibration target.

### Wave 4: editors

Lanes: **A** W4-T1, W4-T2 (Correct). **B** W4-T3, W4-T4, W4-T8 (Trace GT). **C** W4-T5.
**D** W4-T6. **E** W4-T7.

#### W4-T1: Save states and placement (E1, W16) — gate O18; after W1-T3

**Files:** Modify `app/gui/shell_chrome.py::ShellTopbar.populate` (the "Correct" page),
`app/gui/correct/controls.py` (drop `clear_button`, `_CLEAR_TEXTS`; add `set_save_state`),
`app/gui/correct/editor.py` (`_refresh`), `app/gui/raster_editor_base.py`
(`_sync_open_controls`, `_set_dirty`; delete the unplaced `clear_button`),
`app/gui/widgets/buttons.py` (quiet helper), `resources/themes/dark_theme.qss`
(`#primaryButton[quiet="true"]`). Test: Modify `tests/test_correct_main_window.py:327-328`,
`tests/test_shell_chrome_topbar.py:184-207`, `tests/test_correct_editor.py:462,1957-1963`,
`tests/test_correct_controls.py:367-378,586,634,966`, `tests/test_gt_editor_ux.py:189`,
`tests/test_raster_editor_behaviour.py:211`.

**Behaviour:** the top bar holds stage actions, the dock holds image actions: remove the
top-bar Clear (the dock's stays). Save: disabled with no image; accent-filled only while
something is unsaved (a polyline draft counts); with nothing unsaved, after W1-T3: the
skeleton layer disabled, the mask layer quiet with the tooltip "Nothing unsaved · Save
marks the model mask as checked" (or disabled, as the owner decides for the mask). Trace
GT's Save: accent only while unsaved; quiet with no GT file; disabled with a saved one.

**Tests to add:** Save disabled with no image; accent only while `has_unsaved_changes()`;
the state after stroke, undo, save, draft; Trace GT the same; the dock width equal in
every state.

**Commit:** `feat(editors): Save is filled only while something is unsaved; one Clear, in the dock`

#### W4-T2: A toast when a smoothed pick is dropped (E3, J1) — gate O20

**Files:** Modify `app/gui/skeleton_tools.py` (`PolylineDraft.smooth_touched`),
`app/gui/correct/skeleton_layer.py` (`set_smooth`, `_drop_transient`),
`app/gui/correct/editor.py::navigate`. Test: extend
`tests/test_correct_pick_paging_and_draft_discard.py`.

**Tests to add:** pick + `S` + tree click or `PgDn` toasts once ("Smoothed branch not
applied · pick it and press Enter to apply") and asks nothing; pick with Smooth already
on: no toast; pick + `S` + `S`: no toast; Esc: no toast.

**Commit:** `feat(correct): say when a smoothed, uncommitted pick is dropped`

#### W4-T3: Trace GT: goldens, position chip, kept opacity (G1, recommended path) — gate O23

**Files:** Create `tests/data/gt_goldens.json`, `tests/test_gt_goldens.py`. Modify
`app/gui/raster_editor_base.py` (`_update_status_chip`, `hideEvent`),
`app/gui/gt_tracing_interface.py` (`set_position_provider`), `app/gui/main_window.py`
(`_wire_library_order`, `on_library_filtered`), `app/gui/correct/layer_prefs.py` (a
settings-group argument).

- [ ] Goldens first, captured from the code before any other change in this task: a
  scripted session (two crossing classes, eraser, fill limited to the active class, clear
  + undo, a hand tick, save) with the SHA-256 of the ids, the `reviewed` list and the
    decoded file.
- [ ] Then the chip shows "Image i / n" in Trace GT and follows the library search.
- [ ] Then opacity survives a new editor with the same QSettings (`trace_gt/opacity`).

If O23 is "merge now": write a separate plan from `trace-gt.md` G1 ("The merge as a
plan": `GtDocument`, a `RasterLayer` base lifted from `MaskLayer`, `GtLayer`, a `GtEditor`
page; 5 to 7 days; 133 tests in 12 files to port). Trace GT stays its own stage in every
case: as a Correct layer, one wrong layer would write green root paint into `mask/`.

**Commit:** three: `test(gt): editor goldens`, `feat(gt): position chip`, `feat(gt): keep the tracing opacity`

#### W4-T4: Trace GT: traced badge, Restore, Remove tracing, class-balance counts (G2) — gate O24

**Files:** badge: `app/project_layout.py` (`scan_gt_stems`), `app/gui/image_manager.py`
(`gt_stems`), `app/gui/library_index.py` (`_badge_pixmap`, `status_text`),
`app/gui/window/editors.py::on_gt_saved`. Remove: `app/gt/store.py` (`remove_tracing`),
`app/gui/gt_tracing_interface.py`, `app/gui/shell_chrome.py`. Restore:
`app/gt/registry.py` (`Registry.restore(key)`), `app/gui/class_editor_dialog.py` (a
"Retired" group with a Restore button). Counts: `app/gt/export.py` (per-class pixel-count
columns in `manifest.csv`). Docs: README recipe "copy `gt/classes.json` into a new folder
before tracing".

**Tests:** existing pins `tests/test_library_index.py:175`, `tests/test_gt_registry.py`,
`tests/test_gt_widgets.py`, `tests/test_gt_export.py`. Add: the badge follows `gt_saved`
and a rescan, and shows only in folders with `gt/`; Remove deletes only `gt/<stem>.png`
after a confirm; Restore keeps ids and the next export gives the class its own id again.

**Not built** (reasons in `trace-gt.md` G2): measurement of GT classes (the tracings are a
hand-picked sample, not the trial), ML seeding or overlay (labels would inherit the
model's misses), multi-folder projects. Later, on request: per-class visibility (must be a
display-only colour map in `RasterLayerItem`: a transparent class would be read back as
background and erased), instance / COCO export.

**Scientific output:** no GT byte changes; Restore shifts the export ids of later classes
(documented in `classes.json`). **Commit:** one per feature.

#### W4-T5: Display-only "Smooth JPEG blocks" (E7, Q2) — gate O22

**Files:** Modify `app/enhancement.py` (`deblock(img, threshold=14)`,
`EnhanceSettings.deblock`, JSON key `jpeg_deblock`, default false; runs before CLAHE /
stretch), `app/gui/image_normalization_interface.py` (a check box, offered for `.jpg` /
`.jpeg` only), `config/normalization_defaults.json`, `README.md`. Test: extend
`tests/test_enhance_core.py`, `tests/test_enhance_controls.py`.

**Tests to add:** a step above the threshold is left alone and one below is flattened;
idempotent on a flat image; off by default and absent from an old defaults file; saved
mask, skeleton and GT bytes are identical with the toggle on (goldens replayed with it
on); no module under `app/inference` or `app/handlers` imports `app.enhancement`.

If O22 is no: remove Q2 from `docs/KNOWN_ISSUES.md`. **Commit:** `feat(enhance): display-only JPEG block smoothing, default off`

#### W4-T6: Dashboard view names and date colours (D7, W19) — gate O27

**Files:** Modify `app/visualization/theme.py` (`date_colors(n)`: one blue ramp, oldest
`#127abe` to newest `#dbeefe`), the length and area layout and view modules (titles,
legend titles, hover date formats; the area profile indexes all dates, not the plotted
tube's), `docs/SPROUTS_DASH_THEME.md`. Test: re-record `GOLDEN_LAYOUT_SHA256` in
`tests/test_viz_dash_split_golden.py`; update
`test_area_profile_bars_use_the_theme_palette` and the reference title in
`tests/test_viz_data_time_view.py`.

**Names (both dashboards; keys do not change, so export file names and the `# view:` line
stay):**

| Key                    | Label              | Chart title                                               |
| ---------------------- | ------------------ | --------------------------------------------------------- |
| `stacked`            | Totals by Tube     | Root Length / Area by Tube and Date                       |
| `time`               | Growth Over Time   | Root Length / Area Growth Over Time                       |
| `lines`, `profile` | Depth Profile      | Root Length / Area Depth Profile - Tube N                 |
| `faceted`            | Depth Profile Grid | Depth Profile Grid - Field Average (Left) vs Tube (Right) |

One legend title ("Measurement date"), one hover date form (ISO) and "Position (L)" on
both depth axes. The ramp is indexed by the date's place among all dates of the trial, so
a date keeps its colour in every view and for every tube; 13 dates:
`#127abe #2c84c4 #3e8dcb #4f97d1 #5ea1d8 #6cabde #7bb4e5 #89beeb #97c8f2 #a6d2f8 #b4dcff #c8e5ff #dbeefe`.

**Note:** above about seven dates, neighbouring ramp colours cannot be told apart by
colour alone; the hover and the legend order carry the date.

**Scientific output:** none; no dashboard CSV export changes. **Commit:**
`feat(viz): one set of view names; dates on a blue ramp that keeps clear of the layer accents`

#### W4-T7: Exact windowed branch pick (E5) — optional, only if large or dense images matter

**Files:** Modify `app/gui/skeleton_graph_model.py` (split `vectorize_skeleton` into its
node set and `trace(node, neighbour)`; `SkeletonCorrectionModel.pick_branch(xy, reach)`;
a node-order cache dropped in `_touch`), `app/gui/skeleton_tools.py::SelectTool.press`.
Test: `pick_branch` equals `topology()` + `nearest_polyline_index` on random clicks over
dense and real skeletons, at junctions, at the image edge and on node-free loops; a time
budget under `SPROUTS_PERF_TESTS`. The skeleton golden stays green. Today's data costs
11 ms per pick, so skip unless needed.

#### W4-T8: One noun for the Trace GT object (G4, tier 2) — gate O26

If "tracing": delete `EDITOR_NOUN = "GT"` (`gt_tracing_interface.py:144`) and reword the
strings at `:246, :247, :248, :537, :556, :563, :568, :655`,
`app/gui/window/editors.py:61`, `app/gui/export_dataset_dialog.py:48,82`; update about 30
assertions in 11 test files (list in `trace-gt.md` G4, tier 2). If "GT": remove the three
retitles in `_retitle_controls` (`:195, :197, :198`); no test changes.

### Wave 5: repository and FAIR

Lanes: **A** W5-T1, W5-T2, W5-T3 (metadata files overlap; in this order). **B** W5-T4.
**C** W5-T5. **D** W5-T6. **E** W5-T7.

#### W5-T1: Licence (F1, step 2) — gate O32

**Files:** Create `LICENSE` (verbatim text; the copyright line as the owner says). Modify
`pyproject.toml` (`license = "<id>"`, `license-files`, `setuptools>=77`), `CITATION.cff`,
`codemeta.json`, `README.md:335-339`, `app/gui/about_dialog.py` (a "License" row; the id
from one constant in `app/__init__.py`), `scripts/build_support.py::REQUIRED_DATAS`
(`LICENSE`, `THIRD_PARTY_NOTICES.md`), `docs/MODELS.md:32-35`.

**Tests:** `tests/test_fair_metadata.py::test_license_is_declared_everywhere_or_nowhere`
flips by itself. Add: the SPDX id is valid; About shows it; `REQUIRED_DATAS` contains
`LICENSE`.

#### W5-T2: Weights: deposit, DOI, fetch script (F2) — gate O33; after W5-T1

**Files:** `git rm --cached checkpoints.zip` (`.gitignore:4` then applies; no history
rewrite: it would change 228 commit hashes and break the `git_commit` of every provenance
record). Modify `app/models.py` (`ModelSpec.url`, `.doi`), `docs/MODELS.md:30-43`,
`README.md:58-71`, `scripts/build_support.py:64`. Create `scripts/fetch_models.py`
(download to `config.CHECKPOINTS_DIR`, verify with `app.models.verify`, exit non-zero on a
mismatch).

**Tests to add:** `ModelSpec.url` / `.doi` are well formed; `fetch_models` against a local
file server: a good file verifies, a corrupted one is refused and removed.

If the mask model may not be published yet: do the doc corrections only (W0-T2) and keep
this task open.

#### W5-T3: Citation metadata; ship the citation; tag (F3) — gate O34

**Files:** Modify `CITATION.cff`, `pyproject.toml`, `codemeta.json`, `README.md`,
`scripts/build_support.py::REQUIRED_DATAS` (add `CITATION.cff`), package data for the
wheel, `CHANGELOG.md` (fold `[Unreleased]` into `[3.0.0]`, set `date-released`).

**Tests to add:** README's cited version equals `app.__version__`; `CITATION.cff` is among
the bundle datas (`tests/test_fair_build.py`); the About citation is available without the
checkout; ORCIDs match `0000-000X-XXXX-XXXX`.

- [ ] After the merge: tag `v3.0.0`, then move `__version__` to `3.1.0.dev0`. Tagging and
  pushing are the owner's to do or to approve.

#### W5-T4: The real-image golden gate, inside the repository (F4, Q3)

**Files:** Create `tests/test_correct_goldens_real.py` (driven by the replay code of
`tests/test_correct_goldens.py`; inputs from the folder in `SPROUTS_REAL_GOLDENS`; skipped
when unset), `tests/data/correct_goldens_real.json` (hashes only). Modify
`tests/correct_goldens_support.py` (a loader for real cases), `tests/test_enhance_core.py`
and `tests/test_foundation_core_naming.py` (the hard-coded `D:/2024 EF image data` becomes
an environment variable).

- [ ] Step 1 (no decision): port the gate from
  `.claude/handoff/unified-editor-2026-09-29/scratch/integrator/check_goldens_unified.py`;
  re-record the skeleton entries of the real cases at HEAD (the mask entries stand).
- [ ] Step 2 (gate O35): commit the three cases under `tests/data/real/` with a README
  (origin, owner, licence), or only their model outputs (12 KB) with a synthetic photo.

#### W5-T5: Compat shims and duplicated helpers (F5, X8)

- [ ] Delete `shell_chrome.build_ribbon` and its comment block (no caller, no test).
- [ ] Reword the docstring of `app/gui/widgets/tokens.py`: "the Qt-side name of the
  tokens" (61 importers; keep it).
- [ ] Point the 13 app import sites of `app/handlers/worker_base.py` and
  `app/gui/feedback.py` at `app.workers` and `app.feedback`; then delete the two
  re-exports and update `tests/test_layering_contract.py`, `tests/test_import_smoke.py`.
- [ ] Move the one test of `app/handlers/window_feedback.py` to `QtUiFeedback` /
  `RecordingFeedback`; delete the module.
- [ ] X8: one `file_stamp` and one `same_file` in `app/io_utils.py`; replace the six and
  four private copies (`raster_editor_base.py`, `correct/session.py`,
  `correct/mask_document.py`, `correct/skeleton_document.py`, `correct/read_ahead.py`,
  `display_read_ahead.py`, `correct/editor.py`, `correct/paging.py`).
- [ ] Add to the layering test: nothing under `app/handlers/` or `app/inference/` imports
  `app.gui.feedback`.

#### W5-T6: CI and test hygiene (X5, X6, X7)

- [ ] X7: `.github/workflows/ci.yml` also runs on `feat/**` and `fix/**`, or open a draft
  pull request for this branch (pushing and opening it are the owner's to approve). 106
  commits have not run on Linux.
- [ ] X5: `tests/conftest.py` sets `SPROUTS_LOG_DIR` to a temp folder for the session.
- [ ] X6: `README.md` says that `fault.log` also records handled Windows exceptions
  (`0x8001010d`) and is not by itself a sign of a crash. Find and fix the 8 `setTabOrder`
  and 6 `QFont::setPointSize: Point size <= 0` warnings logged at each start.

#### W5-T7: Remaining docs (F6, F7) — gate O36

- [ ] F6: move the script-teardown note from `docs/KNOWN_ISSUES.md` to `CLAUDE.md`
  "Commands" (after the owner's `CLAUDE.md` change is committed).
- [ ] `docs/Architecture_2.md`: trim and correct (about half is wrong; line list in
  `repo-docs.md` F7).
- [ ] `docs/BUILD_EXECUTABLE.md`: delete in favour of the README section, or rewrite from
  the build scripts (O36). It has not changed since 2025-11-26.
- [ ] `docs/Architecture_2 useful.md` and its PDF: delete on O36.
- [ ] Add `tests/test_doc_paths.py`: every back-ticked repository path in `README.md`,
  `CLAUDE.md` and `docs/*.md` exists (start from `scripts/repo-docs/doc_paths.py`).

### Wave 6: optional performance work

- **W6-T1 (P2):** prefetch the first image on intent: a `Neighbours.prefetch` call when
  the pointer or the focus is on the Correct stage button; the existing `take`, file
  stamps and `keep_only` give adoption, staleness and cancellation. Saves 3.4 ms at
  640 x 480 and 42 ms at 2048 x 1536. Do it when images larger than 640 x 480 arrive.
- **W6-T2 (P3):** bit-pack exactly-0/255 patches in `app/gui/patch_history.py` (`_pack`,
  `Patch._unpack`, `_stored_size`), with an exactness check. Measured prototype: a Clear at
  6000 x 4000 70 ms to 36 ms, undo 30 ms to 14 ms; undo and redo states bit-identical.
  Do it when large images arrive.
- **W6-T3 (D2, gate O28):** `MaskOriginCache` in `app/inference/mask_origin.py`, keyed by
  file name with size and `st_mtime_ns` from `scandir`, warmed as the first `GENERATE`
  warm-up. Click on 46k masks 711 ms to 269 ms warm; 8.3 MiB as packed ints. Breaks
  `tests/test_warmup_stage_dwell.py`, nine lines of `tests/test_warmup_main_window.py` and
  two spies in `tests/test_robust_generation_handler_ux.py`.
- **W6-T4 (D5, gate O31):** the owner runs
  `./venv/Scripts/python.exe -m pip uninstall -y pipreqs ipython` (saves 228 ms on the
  first dashboard import); add one README line that the runtime venv needs neither.

---

## 4. Closed as accepted

Each line is the audit's measured reason. Mark them in `docs/KNOWN_ISSUES.md` during
W0-T1; none needs code.

| ID        | Decision                                      | Reason                                                                                                                                                      |
| --------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P1        | Do not defer the editor pages (O30)           | 124 ms of `MainWindow()`; a deferred build gives the same first paint and then GUI blocks of 83 ms and 39 ms; on the real platform it is 130 ms of 0.87 s |
| P4        | Leave the halo layer                          | raster only, 6.7 ms at 4K and 800 %; the OpenGL path, which every logged launch uses, is not affected                                                       |
| P5        | Leave the first skeleton frame                | 3.75 ms once on the stress skeleton, 0.98 ms on a real one                                                                                                  |
| P7        | Leave the warm-up leftovers                   | the window is gone 15 ms after close; the 1.25 s wait is invisible, and a daemon thread risks a crash at exit                                               |
| D1        | Leave `refresh_outputs` on the GUI thread   | 94 ms for 23k + 23k once per pass; an asynchronous scan races with saves                                                                                    |
| D3        | Keep raster viewports while a dashboard lives | one graphics backend per top-level window; a separate window is a UX regression                                                                             |
| D4        | Leave the first dashboard open                | the 234 ms is the QtWebEngine start, bound to the GUI thread                                                                                                |
| D6        | Keep the 30-bar label limit                   | a zoom-following callback costs a second full draw on the largest trials                                                                                    |
| E6        | Pinch and bicubic: not planned (O21)          | Windows touchpads already pinch through Ctrl+wheel; bicubic sharpens the JPEG blocks                                                                        |
| G3        | Sign off the compact top bar (O25)            | built and tested; the alternative is a 1040 px minimum window                                                                                               |
| L4        | Do not rename `<stem>_fake.png`             | cosmetic gain; a reader for two names for good                                                                                                              |
| M3        | No reader change for EXIF                     | every reader already uses the stored grid                                                                                                                   |
| R2        | Deterministic cuDNN stays the default (O5)    | already in the code and in every run record                                                                                                                 |
| R8 (part) | No incremental Measure (O17)                  | a safe reuse has to verify content, which costs as much as measuring (17 to 21 s for 46k)                                                                   |

## 5. Verification

After each task:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
./venv/Scripts/python.exe -m pytest <the task's test files> -v
./venv/Scripts/python.exe -m ruff check --fix .
./venv/Scripts/python.exe -m ruff format .
```

At the end of each wave:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
./venv/Scripts/python.exe -m pytest --junitxml="$env:TEMP\sprouts-wave.xml"
```

Expected: no failure and no error; read the counts from the XML, not from `-q`. Then run
the wave's reproducing scripts from `.claude/handoff/issue-audit-2026-10-12/scripts/` on a
scratch copy and confirm that the recorded failure no longer happens. For Wave 3, attach
the before / after CSV diff to the CHANGELOG entry.

## 6. Order and effort

| Wave | Content                                                  | Needs the owner                                                        | Effort                                    |
| ---- | -------------------------------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------- |
| 0    | Documents true                                           | O5 only                                                                | about 1.5 days                            |
| 1    | Data loss: R7, M1-A, L2, E2, R4 + R3, X4, pix2pix notice | O2, O19 (recommended answers are the safe ones); O6, O7, O15 for parts | about 5 days                              |
| 2    | Records and wording                                      | O9, O10, O13, O14, O16, O17, O29, O37                                  | about 7 days                              |
| 3    | Scientific outputs                                       | O1, O3, O4, O8, O11, O12, O38, one GPU session                         | about 8 days                              |
| 4    | Editors                                                  | O18, O20, O22 to O27                                                   | about 6 days (without the Trace GT merge) |
| 5    | Repository and FAIR                                      | O32 to O36                                                             | about 4 days plus the owner's inputs      |
| 6    | Optional performance                                     | O28, O31                                                               | about 2 days if all are done              |

Wave 1 can start without an answer except W1-T3 (O2) and W1-T4 (O19); W1-T1, W1-T2, the
Windows part of W1-T5, W1-T6 and W1-T7 need none. Waves 2, 4 and 5 are independent of
each other once Wave 1 is in. Wave 3 follows Wave 2 (W3-T1 needs W1-T3 and W2-T7; W3-T3
needs W2-T3; W3-T4 needs W1-T2).
