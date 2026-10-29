# Changelog

All notable changes to SPROUTS (Root-Mask-and-Skeletons) are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/); the version lives in `app/__init__.py`.

Every release has a **Scientific output changes** section: anything that can change a
value in `root_lengths.csv` / `root_areas.csv`, a mask or skeleton pixel, or a
ground-truth export. Record it there even when the change is a bug fix, so results
produced before and after the change can be told apart.

## [Unreleased]

Seven passes, not yet released:

- **Python 3.14 and the newest dependencies** (branch `feat/unified-correct-editor`,
  2026-10-27; the owner's decision: "Use python 3.14 i accept gpu cudnn changes. Upgrade to
  most latest versions"): built and checked in an isolated worktree and venv. The GPU
  outputs of the new PyTorch build were measured and accepted (*Scientific output
  changes*); the CPU outputs, Measure, the Correct saves and every dashboard figure and
  Export CSV were compared with the old stack and are identical.
- **Redundancy and speed pass** (branch `feat/unified-correct-editor`, audit 2026-10-26,
  built 2026-10-27 in `89965f5`..`5f0ca18`, and `011ad3f` on 2026-10-28): a read-only
  audit for repeated code, dead code, bugs and speed, each finding checked by adversarial
  verifiers, then built in lanes with disjoint files, each lane reviewed and committed on
  its own. Waves 5 and 6 built what two owner requests of 2026-10-27 led to: dashboard
  speed-ups and a fix of the dashboard release (from an investigation of the dashboard
  flicker and of the GPU), and an in-house layer over the app's Qt workarounds (settings,
  threads, dialogs, hold keys, guard tests). Entries carry the finding ID (for example
  S01-06); `docs/KNOWN_ISSUES.md` resolves the IDs the code cites. Nothing visible changed
  without the owner: the fixes restore documented behaviour, and the owner questions are
  not built. At the end of each wave a science harness compared every generated mask and
  skeleton (GPU and CPU), every CSV row, provenance and inputs record and every GT-first
  decision with `f392a85` (Measure alone after wave 3, which changed no Generate code):
  identical each time. Timings are min / median of interleaved runs on a machine shared
  with other agents, offscreen unless stated; 46k is the owner's 46,178-image project or a
  synthetic project of its layout.
- **Owner-decisions build** (branch `feat/unified-correct-editor`, 2026-10-25; plan
  `docs/superpowers/plans/2026-10-25-owner-decisions/00-master.md`): the owner's answers
  to the 2026-10-12 decision sheet (O1-O38) and the follow-up answers, built task by task,
  each reviewed and committed on its own. Entries carry the task ID (for example W4-T6).
- **Fix pass** (branch `feat/unified-correct-editor`, 2026-10-02): the engineering items
  of `docs/KNOWN_ISSUES.md` #6 and #7 that need no owner decision, by parallel
  packages, each reviewed and committed on its own. The owner questions stay open.
- **UX and speed pass** (branch `feat/unified-correct-editor`, 2026-09-30): the open items
  of `docs/KNOWN_ISSUES.md` #5 and #6 worked through with the emphasis on UI/UX and speed,
  plus a bug hunt: per-area packages with a UI walkthrough and a measured runtime review,
  then a review of every commit, a fourth wave that split `MainWindow`, reviewed the
  warm-up and memory work and walked the app again, and a fifth wave that fixed what
  that walkthrough found. Timings are medians of interleaved runs
  on a machine shared by up to eight agents, offscreen unless stated. Still open:
  `docs/KNOWN_ISSUES.md` #7.
- **Unified Correct editor** (branch `feat/unified-correct-editor`, 2026-09-29; spec
  `docs/superpowers/specs/2026-09-29-unified-correct-editor-design.md`): one **Correct**
  stage replaces *Correct Mask* and *Correct Skeleton*, and the photo stays smooth at every
  zoom on every canvas. Entries that name *Correct Mask* or *Correct Skeleton* describe
  those stages before the merge; their fixes carry over to the mask and skeleton layers of
  *Correct*.
- **Optimization, UI-error and FAIR pass** (branch `feat/ui-perf-refinement`, 2026-09-28):
  a 161-finding audit implemented by per-area packages, then integrated and reviewed.

### Fixed
- **Redundancy and speed pass (2026-10-26).** None of these changes a CSV value or an
  output pixel, except one corner case under *Scientific output changes* (S01-06).
  - Correct: undo across layers keeps each layer's newest 100 actions on the one timeline,
    so a busy layer no longer pushes another layer's steps out of reach (300 skeleton
    actions after 50 mask strokes had left none of the strokes undoable, and the Undo button
    turned off while the mask still held steps) (C4-01).
  - Canvas keys: a `Space` pan ends when `Space` is released with `Ctrl`, `Shift`, `Alt` or
    `Meta` down (`Space`, then `Ctrl` for a Ctrl+wheel zoom, then `Space` up first: left
    drags kept panning). `B` pressed with the keyboard on the library tree and released with
    `Ctrl` down ends its hold as on the canvas. `B` or `Space` pressed in the tree and
    released on another stage has ended when Correct is back (the wheel kept sizing the
    brush, or left drags kept panning) (S05-02).
  - Status chip: with the polyline prompt up, a zoom or a fit no longer narrows a chip placed
    below the prompt to the room beside it (name elided to `…T012…`, hint wrapped over the
    tool rail) (S05-01).
  - A mask prediction whose name differs from the image's only in case is pinned like any
    other. On a case-sensitive disk, a prediction named `mask/IMG.png` or `mask/img.Png` for
    `img.png` is stamped when *Correct* reads it: a rewrite of it by another program is seen
    by Discard (*Mask reloaded: the file changed on disk*), by the same image opened again
    and by the read-ahead and hand-back, and a save of a plane equal to the prediction as
    opened writes the GT mask instead of removing it (S01-06).
  - Class labels that Pillow refuses (a broken PNG chunk, or a header over its size limit)
    open empty in *Correct* with the usual unreadable-labels notice; *Correct* raised in the
    middle of the open and left the previous image's photo and labels on screen. The
    dataset export lists such a file in `errors.txt` and carries on; a broken chunk stopped
    it with *Export failed* (S17-04).
  - The Measure status line counts the files Measure reads: masks in `mask/` and GT files in
    `gt/mask/` and `gt/skeletons/` by Measure's own name rule (`.png` in any case, hidden
    names such as `._a.png` included), and the predicted skeletons in `skeletons/` by
    Measure Length's (`*_fake.png` with exactly that suffix, hidden names included: a
    `B_fake.PNG` is no longer counted, a `._a_fake.png` is). The count can change in
    projects with such names; the wording is the same (C1-04).
  - Measure clicked while a folder loads gives the loading refusal at once; it named the
    previous project's missing outputs, or listed its folders first (S11-01).
  - Durable saves survive a power cut on Linux and macOS: after the swap, a durable save (GT
    files, CSVs, records, sidecars) also syncs its folder (about 6 ms per file); a folder
    that cannot be synced (CIFS, FUSE) never fails the save. Windows is unchanged (S16-09).
  - Metrics bar: a `root_lengths.csv` found in a legacy layout stays on the bar when a job
    ends (S06-01, C1-03); a legacy project opened while the previous project's CSV parse
    still ran shows its own length values (S06-04); a prediction whose name differs from the
    image's in case (O7) is no longer shown outdated right after Measure, and its tooltip
    names the measured file (C1-02).
  - The length CSV of a legacy layout is found where Measure writes it. In a project whose
    skeletons sit in a `test_latest/images` folder found by the folder search, Measure
    Length wrote `root_lengths.csv` into that `images/` folder, but the dashboard, the
    metrics bar and the status line looked only in `test_latest/` and said there was no
    length data; the `images/` CSV is found now, ahead of a CSV an older version wrote into
    `test_latest/` (S16-02).
  - Visualize, root area: the dashboard draws its Totals by Tube chart when it opens; its
    first figure request was taken for a *Toggle Legend* press and left the chart empty
    (P4-01). Totals by Tube drew a tube's later dates from 0 after a date the tube was not
    measured on, so its bars overlapped and its total was drawn too short (162 of 170 tubes
    on the 2024 EF trial); both dashboards now draw the chart with one builder that sends a
    missed date as null, which plotly.js leaves out of the stack. Values, hover and exports
    are unchanged (S14-02).
  - Visualize: a wide tube span in *Add Range* or the typed tube list (for example
    `1-100000000`) no longer freezes the app for seconds or exhausts memory: the span is
    matched against the trial's tubes, and the selection is the same (S14-01, S15-02).
  - A dashboard build or a dataset export ended by `SystemExit`, `KeyboardInterrupt` or
    another `BaseException` reports a failure (the page stayed at *Loading data…*, and the
    export's modal dialog could not be closed), and an unexpected export error is logged
    with its traceback (S10-02, S17-06).
  - A crash dialog closed together with the dialog it sat on no longer mutes every later
    error dialog of the session: the next error shows a new one (S10-01).
  - The app no longer aborts with "QThread: Destroyed while thread ... is still running"
    (exit code 0xC0000409, no Python traceback; it ended a session on 2026-09-29): a
    dashboard deleted while its server thread ran, or a thread object dropped right after
    its start, destroyed a running thread. Every `QThread` is now held from `start()` until
    Qt's own `finished` and never has a parent, and the quit asks the running ones to stop
    and waits at most 2 s in all; a quit during a long folder scan can therefore keep the
    process up to 2 s after the window has closed. The three dashboard cases and the
    dropped thread abort on the old code and exit normally now (K1a-B).
  - A `SystemExit`, `KeyboardInterrupt` or other `BaseException` in a dashboard's server
    thread shows on the page ("The dashboard server stopped: …"): a `SystemExit` there ended
    the app, and the others left the thread without reaching the page (G06).
  - With OpenGL canvases, the 5-minute release of the dashboards recreates the main window
    once instead of twice (one blink instead of two) and no longer logs "... 'D3D11' is not
    compatible with QOpenGLWidget": a released dashboard leaves the window before the
    canvases get OpenGL back (K1b-D). The first open and each reopen still recreate the
    window once (`docs/KNOWN_ISSUES.md` D8).
  - Dashboard blink (D8, route (b), 2026-10-29, after the owner's console test at the RTX
    5070 Ti's own screen): on Windows each dashboard is a native child window with its own
    Direct3D 11 surface (`viz_window.native_child_surface`), so the main window keeps the
    canvases' surface and is never recreated: no blink at the first open, at the 5-minute
    release or at a reopen (it was gone for 44-86 ms each time), and the OpenGL canvases
    stay on while a dashboard lives (they used to switch to raster viewports, D3). The first
    dashboard load took 1.1 s instead of 3.8 s in that test, and the canvases redrew at
    61-62 fps with a dashboard as without one. Cost: Qt makes 16-18 widgets native (the
    dashboard panel's ancestors and their siblings) and the app holds about 100 MB more.
    Toasts, the loading overlay and the Correct page were pixel-identical on screen; a
    resize no longer shows black bands for a frame. Linux and macOS keep plain child
    widgets and the raster swap (not checked there). No CSV value or pixel is involved.
  - A held `Space` ends when the keyboard leaves a canvas (another widget, a popup or another
    window takes it): the Library display kept panning after the focus had moved away
    (K1a-C1).
  - A window started without a screen (tests, scripts) and without explicit settings keeps
    its recent projects in memory; it wrote every folder it opened into the user's list
    (C5-04). Every store made without a settings object (the recent projects, the window
    state, the Correct layers' opacity and visibility, the device-check switch) now gets it
    from `app.settings.app_settings()`, a memory store on such a platform: a Welcome screen
    or a recent-projects store built in a test, and the device check, still opened the
    user's settings (K1a-A).
  - Raster canvases (the macOS default; Windows or Linux without OpenGL, or while a
    dashboard lives): the translucent skeleton's joints no longer flicker brighter or darker
    under the brush or eraser ring, because a partial repaint draws the full frame's lines
    (X2-06; the skeleton's share of such a repaint at 200 % and above 0.15 -> 0.55 ms). At
    125-175 % display scaling the skeleton's pixel tiles sit on the mask layer's device
    pixels (no 1 px drift at .5 ties; S04-03). At zooms where the endpoint markers are now
    drawn directly, they no longer shift by a device pixel after some pans, nor drift at
    125-175 % (X2-03).
  - Tests: the oracle of `tests/test_gt_export_count_traced.py` uses the O7 case rule on
    every OS; it failed on the Linux and macOS CI legs (S17-01). The metrics-badge test of
    `tests/test_correct_main_window.py` waits for the read-ahead (S02-02), and the Enhance
    defaults test for its preview instead of a fixed 120 ms: both could fail on a busy
    machine. The strict Correct budgets (`SPROUTS_PERF_TESTS=1`) time a Pix2Pix prediction
    of `docs/examples`: the synthetic 45-degree line cost 17-25 ms of thinning, and two
    budgets failed (X6-02). The wide-tube-span test keeps a trace that `PYTHONTRACEMALLOC=1`
    started (it failed and stopped the trace), and the read-ahead cache's deadlock test fails
    instead of hanging.
- **GT files of an image found, saved and removed as its own (2026-10-26, final review D1,
  C1, C2, D5).**
  - An image whose name ends in two extensions (`X.png.jpg`, stem `X.png`): *Correct* opens
    and saves `gt/skeletons/X.png.png`, **Remove GT…** deletes `gt/mask/X.png.png`,
    `gt/skeletons/X.png.png` and `gt/X.png.png`, and the Library marks and the metrics bar
    look under `X.png`. Before, the stem was stripped a second time to `X`: the skeleton
    layer opened and overwrote image `X.png`'s GT skeleton, and Remove GT… deleted `X.png`'s
    three GT files. Files already saved under the wrong name are not moved.
  - A GT skeleton that cannot be read is no longer replaced or removed by a save without a
    question: the skeleton layer shows *Skeleton not shown*, `Ctrl+S` asks Replace / Keep
    it, and `PgDn` stays on the image, as the mask layer does.
  - GT files whose name differs from the image's only in case (O7) are found by the skeleton
    layer, the open image's Library mark, the dataset export and its count, on
    case-sensitive disks too; a skeleton save writes onto that file and never creates a
    second one.
  - The dataset export refuses an output folder inside `mask/`, `skeletons/` or `gt/`
    spelled in any case, on every platform (Linux on a case-insensitive volume included).
- **One image per stem, PNG and JPEG only (2026-10-25, W1-T5, R4, R3, O6, O7).** Stems
  compare without case on every platform (`app.naming.stem_key`). `A.png` and `a.jpg` were
  two Library images racing for one `mask/A.png`: a run reported 3 written for 2 files,
  and *Keep existing masks* replaced a hand-corrected mask of `A.png` with `a.jpg`'s. Now
  they are one image (`.png` before `.jpg` before `.jpeg`, then the name that sorts first),
  and a mask, skeleton or correction whose name differs only in case is found for it.
  Mask and skeleton generation, the Library and the measured-value lookup read the same
  PNG / JPEG files; skeleton generation no longer reads `.bmp` / `.tif` / `.tiff` / `.ppm`.
  Opening a folder names the files it does not list in a second toast. A failure while
  the Library adopts a scan ends the load as failed (nothing loaded), so Generate and
  Measure are never refused for good. See "Scientific output changes".
- **Owner-decisions build (2026-10-25).** None of these changes a CSV value or an output
  pixel.
  - Start-up: the D3D11 / ANGLE flags are set on Windows only; Linux and macOS take Qt's
    default RHI (on Linux Qt failed to create its RHI and the dashboards were black). Fusion
    style off Windows; desktop file name `sprouts` for Wayland icons (WX-T1, O21).
  - Correct: a saved mask it cannot show (a GT mask of another size than the photo, or an
    undecodable file; a prediction of another size opens stretched since W3-T4) is no
    longer removed by `Ctrl+S`. The mask row reads *Mask not shown*, the open warns, a save
    with nothing painted keeps the file, a save after painting asks **Replace** / **Keep
    it**, and `PgDn` stays on the image with a warning (W1-T2, M1 part A).
  - Generate: opening another folder while a job runs is refused (the chained skeleton pass
    of *Generate all* went to the newly opened folder and replaced its skeletons); the
    skeleton pass is bound to the folder the mask pass ran on, and a job is refused while a
    folder loads (W1-T1, R7, O15).
  - Correct: a mask or skeleton correction that changed on disk while open (another
    program, a *Generate* run) is no longer lost or overwritten unasked. **Discard** reads
    such a layer again from disk; **Save** asks first (**Overwrite** / **Reload (lose my
    edits)** / **Cancel**, Cancel by default); a file rewritten with the same pixels asks
    nothing; `Ctrl+S` without edits reloads the file and writes nothing; `PgDn` stays on the
    image and says why. The image open in *Correct* without unsaved edits (and no drag in
    progress) shows the new outputs when a *Generate* run finishes (W1-T4, E2, O19).
  - Paths: `app.io_utils.path_key` / `same_file` compare paths the way the platform's file
    system does (case-insensitive and Unicode-normalising on macOS APFS); the recent
    projects and the Library read-ahead use it. Temp names of atomic writes stay within 255
    bytes on Linux and macOS (also for non-UTF-8 names). The no-driver device note on macOS
    no longer mentions NVIDIA (WX-T4, O21).
  - File names: a Windows path (`C:\x\a.jpg`) in a CSV cell or a path string splits on `\`
    on every OS, so Linux and macOS read the same stem; a literal backslash in a POSIX file
    name is treated as a separator too (WX-T10, O21).
  - Start-up no longer logs eight `QWidget::setTabOrder` warnings (a combo box's popup list
    was in the editor's Tab chain); tests log into a temporary folder instead of the user's
    log; CI also runs on `feat/**` and `fix/**` pushes; README says `fault.log` also records
    handled Windows exceptions (W5-T6, X5, X6, X7).
  - Dependencies: `pywin32` is no longer a dependency (nothing imported it) and OpenCV is
    `opencv-python-headless` (no OpenCV window is ever opened; avoids a second Qt on
    Linux); Linux and macOS are listed as supported (WX-T2, O21).
- **Fix pass (2026-10-02).** None of these changes a CSV value or an output pixel.
  - CI's `ruff check .` failed with 7 F811 errors: `ruff format` had put the fixtures'
    `noqa` comments on the wrong parameter.
  - A background job that ends with something other than an `Exception` (`SystemExit`,
    `KeyboardInterrupt`, any other `BaseException`) now reports a failure: the Measure
    CSV parse (the metrics stayed loading for good, blocking the warm-ups), the device
    check (the chip stayed at "Checking GPU…"), the folder scan (it caught only
    `OSError`, so any other error left the loading card up) and every batch job
    (`GuardedThread`: the error reached the crash dialog while the progress row kept
    spinning).
  - A Measure CSV value that is not a finite number (`nan`, `inf`) shows the dash in the
    failure colour, with a tooltip naming the CSV, instead of "nan mm".
  - The progress row says "Generating masks — cancelling…" (was "Cancelling Generating
    masks…"); the Generate prompt's button reads "Overwrite all" when no mask is
    hand-corrected (was "Overwrite all (0 hand-corrected)").
  - A save toast keeps one line: a long file name is elided in the middle (start and
    extension kept), the full text in the tooltip. Toasts keep clear of the Welcome
    screen's About button (a danger toast covered it at 1024 x 640).
  - GT export: a drive-relative output folder (`D:out` with the project on `C:`) stayed
    relative in the dialog, and the export used the raw string for every write, so a
    change of working folder during an export could split its output. The dialog spells
    the folder out and the export resolves it once, at the start.
- **UX and speed pass (2026-09-30).** None of these changes a CSV value or an output pixel
  except the two entries under *Scientific output changes* (stem clash, GT export size
  check).
  - Second walkthrough (fifth wave): entering Visualize no longer leaves the focus on the
    Library stage button; opening another folder clears the previous project's saved-CSV
    status and toasts; library date rows are no longer cut mid-word at the default width
    (a regression of this pass); a typed letter in Correct or Trace GT no longer moves
    the library selection and so the edited image; save toasts update in place with one
    wording for both editors; Trace GT no longer asks to save after every stroke was
    undone; the device chip keeps its warnings whole; Trace GT's Tab order follows the
    screen; the status line elides a long message in the middle.
  - Generation and Measure: two image files with one stem (`a.jpg` and `a.png`) no longer
    race for one output file. A full disk ends a run at the next batch boundary as failed
    and keeps the outputs already written (every remaining image used to fail one by one
    while the model kept running). A cancelled or failed skeleton run refreshes the Library.
    The cleanup after a sticky CUDA error no longer replaces the run's own error. A progress
    callback that raises no longer counts an input as both written and failed, and counts
    from several writer threads arrive in order (the bar could end at "1 of 7"). When the
    full provenance record cannot be built, a minimal `_sprouts_run.json` replaces the
    previous run's record. Measure-area warns when it measures an older `output/mask` folder
    while `mask/` exists (the CSV is unchanged).
  - Files: the GT export's text files (splits, `classes.json`, `manifest.csv`, `errors.txt`)
    are written atomically, with the same bytes. The atomic writer shortens its temp name
    when the temp path would pass Windows' 260 characters while the target fits, and on a
    full disk its cleanup removes the temp file and keeps the original error. The per-user
    settings folder is swept for stale temp files, and loading the Enhance defaults no
    longer fails when no thread can start.
  - Correct: coming back to an image after saving it no longer re-reads it and drops its
    undo history. `B` or `Space` held while the canvas loses the keyboard (Alt+Tab, a
    dialog) no longer stays on (Trace GT too). `PgDn` with a polyline draft hidden behind
    the mask layer no longer traps the keyboard: it brings the skeleton forward so Enter and
    Esc reach the draft. A skeleton opened with *Load skeleton file* that differs from the
    open one counts as unsaved (it was dropped without a prompt). A read-ahead image is kept
    when an open is cancelled, and the read-ahead plans again after a library search or a
    Generate run.
  - Skeleton layer: endpoint markers, polyline handles and previews sit on pixel centres (at
    1600 % a marker sat 8 screen px off its line tip and a committed line jumped 8 px). The
    marker cache is kept on large and HiDPI viewports (at about 2600 x 1500 device px all
    1,738 markers were re-rendered every frame: 25.4 ms instead of 0.62 ms). The translucent
    halo no longer darkens where segments overlap.
  - Canvas: a very large image fits (the fit was clamped to 10 %, which cut off a
    6000 x 4000 photo in a canvas narrower than about 650 px). The point under a still
    cursor stays put through a run of Ctrl+wheel notches (it drifted up to 5 screen px).
    Zooming in after zooming out to the limit no longer jumps back to an earlier point. The
    pixel grid sits on the pixel edges on raster viewports. The photo's idle texture uploads
    also happen when the view does not show the image's top-left corner.
  - Trace GT: *Discard* puts back the tracing as last saved (or as opened), class ticks and
    counts included; it used to keep the strokes on screen, and a later save wrote them. A
    failed first save no longer has already replaced the tracing file (`gt/classes.json` is
    written first). Opening another image decodes it before asking about unsaved edits.
    Clicking the open image's library row gives the keys back to the canvas. In the class
    editor, Enter or OK adds a typed class instead of dropping it. In the export dialog,
    Enter exports instead of opening Browse, a relative folder goes next to the images and a
    pasted quoted path works. Long class names no longer push the check boxes behind a
    horizontal scroll bar. The dataset export goes on past an image or tracing that Pillow
    refuses as too large (over about 179 Mpx): it becomes that image's `errors.txt` entry
    and its partial outputs are removed. The export used to stop there and leave a
    half-written folder; the other images are exported byte-identical to an export without
    the large one.
  - Library and shell: the app no longer fails to start on a recent-projects entry with a
    UTC-offset time or a NaN count. After paging in an editor, the Library shows the
    selected image, not the one it showed before. The Library stage button no longer keeps
    the keyboard after a folder opens. Opening a folder again updates the "Loaded N images"
    toast instead of stacking another. The selected library row keeps its green and orange
    status dots and no longer shifts 3 px. The status line no longer says "Ready" during a
    job or keeps a red dot after a failure has cleared, its idle text follows the loaded
    folder ("Ready · 1 image"), and "Cancelling…" stays until the job stops. The metrics bar
    has no stray separator. The loading card sits below the top bar and names the folder,
    and the empty state says "Loading images…" during the first load; long folder names no
    longer cut the card or widen the empty state. The window title is "<project> — SPROUTS"
    (was "Root Viewer"). Welcome rows no longer clip descenders and underscores, count
    images like the Library, and keep the keyboard focus after a recent project is removed.
  - Memory and warm-ups: a folder scan's thread and its results are freed as soon as the
    scan ends. A reference cycle kept them until a full garbage collection (46,178 images:
    17.2 MB and 46,838 objects, freed by a 41-51 ms pass), and a heap freeze made while the
    project was open kept them for the session (13.5 MB per freeze). A warm-up ended by
    `SystemExit` or `KeyboardInterrupt` no longer leaves the warm-up queue stuck and
    automatic full garbage collections off for the rest of the session (no production path
    raises one there).
  - Dashboards: the area profile's x axis reads shallow to deep (it was in first-appearance
    order: L10, L20, L5). Removing the last Multi-Select tube clears the chart. Typed or
    ranged tubes without data no longer count as selected. Stacked bars leave out the dates
    a tube was not measured on (they showed 0.00). A crashed page renderer is reported and
    the next visit rebuilds the page (it stayed blank). An unchartable CSV says why (header
    only, every row failed, a missing column). Dashboards are released when another project
    opens (they kept the previous project's data). Hint and error panels no longer draw an
    empty axis grid.
- Correct: a skeleton file that *Load* cannot read changes nothing; it used to forget the
  skeleton's undo steps and drop its polyline draft.
- The arrow keys in the Library tree keep stepping images while Correct or Trace GT is
  open (the canvas took the keyboard focus after the first step).
- **Discard reverts the mask**: answering *Discard* puts the mask back to its saved file.
  The mask editor used to mark the edits as saved but leave them on screen.
- Undoing back to the saved mask reads as saved again (no unsaved-changes prompt for a
  mask with no net change).
- Enhance on a JPEG with an EXIF orientation tag no longer shows the photo rotated against
  the mask: the photo and Enhance share one decode that ignores the tag, as Qt does.
- **Generate no longer overwrites hand-corrected masks**: when `mask/` already has
  masks, a dialog offers "Keep existing masks (generate missing only)" (default),
  "Overwrite all" (counts the hand-corrected ones) or Cancel.
- Dashboards no longer drop data: `Position` was downcast to int8 and wrapped for depths
  L121-L127, hiding 7.9% of the real 2024 rows in Growth Lines and Faceted views.
- Correct Skeleton no longer freezes on a skeleton with a loop off a junction
  (`vectorize_skeleton` looped forever).
- A successful skeleton run no longer ends in a false "Images directory not found" error,
  and new outputs appear without reloading the folder.
- JPG projects no longer show every traced mask as "No mask" (the status check looked
  for `mask/<stem>.jpg`).
- Ctrl+Y redo works in Correct Mask and Trace GT (two identical shortcuts cancelled each
  other); Ctrl+Shift+Z also redoes.
- A metric CSV open in Excel no longer crashes the app: writes are atomic and a locked
  file gives a Retry prompt. Unhandled exceptions are logged and shown once instead of
  aborting the process.
- Correct Skeleton asks before discarding unsaved corrections and opens the generated or
  corrected skeleton for the selected image automatically (no file dialog per image).
- Keyboard browsing: arrow keys load the highlighted image; PgDn/PgUp step images in
  every stage.
- Toasts no longer nest graphics effects (seven QPainter warnings per repaint); the
  metrics bar elides long image names instead of forcing a 1058 px minimum window width.
- Robustness fixes R4-R8 and RB-16 (branch `fix/batch-robustness`, 2026-09-29). None of them
  changes a CSV value or an output pixel.
  - A generation run that stops with an error after writing outputs replaces
    `_sprouts_run.json` with a record of that run (`status` "failed", the error, the outputs
    it wrote). The folder used to keep the previous run's record, which then described
    outputs it had not produced. A run that fails before writing anything leaves the record
    as it is. Every record now states `status` (completed, cancelled or failed) and `error`.
  - A Generate run in which every image failed (for example out of GPU memory even on single
    images) ends as a failure ("Mask generation failed: none of N images could be
    processed") and does not start the skeleton pass; it used to report "Generated 0 masks".
  - A JSON file nested deeper than Python's recursion limit (`gt/classes.json`, a GT file's
    metadata, the Enhance defaults, `build_info.json`, the recent-projects list) is reported
    like any other unreadable file instead of reaching the crash dialog.
  - Temp files left by a killed write (`.<name>.<8 hex>.tmp`, older than one hour) are
    removed from the project's output folders a moment after the project has loaded, on a
    background thread. GT tracings and `gt/classes.json` are written through the shared
    atomic writer (fsync, lock retry; a GT file held open by another program gets the usual
    message).
  - Out of GPU memory, a sub-batch at least as long as one that already ran out of memory is
    split without being tried (saving one wasted forward per split). Under a steady memory
    limit the sub-batches that run are the same as before; after a transient out-of-memory
    they can differ, which can change the outputs of those images slightly (see *Scientific
    output changes*, the batch-size correction of 2026-09-30). An image
    that runs out of memory alone is reported once as "OutOfMemoryError: ...", not
    "ItemOutOfMemory: OutOfMemoryError: ...".
  - Closing the F1 shortcut sheet or the About dialog no longer raises "wrapped C/C++ object
    ... has been deleted", and the Measure Retry prompt is freed once answered.

### Performance
- **Redundancy and speed pass (2026-10-26).** Outputs are unchanged by all of these.
  - Whole app (the pass's perf harness, `f392a85` against `ea205a9`, three interleaved runs
    each, medians): opening the 46k project 1222 -> 903 ms (GUI thread 359 -> 290 ms);
    Measure on the harness project 26 % (area) and 27 % (length) faster; entering Correct
    21.1 -> 16.9 ms, `PgDn` 13.7 -> 11.3 ms, a 200-point brush stroke 103 -> 79 ms;
    `import main` (the app's modules) 354 -> 249 ms; the length dashboard build 92 -> 82 ms.
  - Generate on the GPU: the decode, prefetch and save threads run their tensor ops on one
    torch thread while a CUDA run lasts (each had started a 16-thread OpenMP team): 409 masks
    median 15.0 -> 7.2 s, 1227 skeletons 19.3 -> 7.0 s, process CPU about 25x lower
    (S12-01). Re-measured on 2026-10-28 with less load on the machine (400 photos, three
    interleaved runs, the old stack, `KMP_BLOCKTIME` as each version's `main.py` sets it):
    masks 7.82 -> 7.67 s, skeletons 3.48 -> 3.00 s, process CPU 120 -> 10 s and
    40 -> 5 s. The wall-time gain depends on how busy the CPU is; the CPU saving holds.
    `main.py` sets `KMP_BLOCKTIME=0` before torch loads, unless the user set it, so idle
    OpenMP workers sleep at once (Linux's GNU OpenMP ignores it): on the CPU
    fallback 300 skeletons 64 -> 51 s with about 40 % less CPU time (P3-01). The skeleton
    model loads its checkpoint without a random init (0.12 -> 0.05 s on the GPU,
    0.35 -> 0.05 s on the CPU; S12-03).
  - Generate: a finished mask or skeleton run (its thread and report, about 5 MB at 46k
    images) is freed as soon as the next run of its kind starts; a heap freeze while it was
    alive kept it for the rest of the session (C3-05). A run in which many inputs fail logs
    the first 20 of each kind, one count and one bounded end-of-run line (5000 unreadable
    inputs: 2.7 MB -> 8 KB of `app.log`; C6-02).
  - Measure at 46k (synthetic, no GT; min): length 46.1 -> 25.5 s, area 32.6 -> 23.7 s. An
    empty prediction is not thinned, a run decodes each repeated empty file once, the pool
    runs images in tasks of several, and an image that fails stops the run at once (S13-02,
    S13-03, S13-04, P3-03); Cancel is heard as quickly as before (about 30 ms). Input keys
    are made once per folder: on a synthetic 46k project seven folders deep the status
    count takes 0.46 s instead of 3.9 s and the "nothing changed" check 0.29 s instead of
    3.1 s (S06-06). One parse of an inputs record serves the metrics bar, the status line and
    the Measure click (C3-02); a run keeps no session SHA-256 entries (about 25 MiB per
    metric at 46k; S13-05); the click no longer pairs its inputs on the GUI thread
    (0.2-0.5 -> 0.15-0.3 s at 46k; S11-05). The status line runs one count at a time, and
    once after a Measure run on the Measure stage instead of twice (S06-05). A run whose
    inputs cannot be read logs its first 20 rows with an Error and one count (5000
    unreadable skeletons: 1.6 -> 0.65 s, 1 MiB -> 4 KiB of `app.log`; C6-02); the rows and
    their `Error` column are unchanged.
  - Metrics bar and Visualize: the end of a Generate pass no longer blanks LENGTH / AREA and
    re-parses both 46k-row CSVs (its slot 5.3 -> 1.5 ms, no 174-253 ms pool parse; S06-02).
    Before a project's first Measure Length, a Visualize entry, a Length click and the
    job-end retry no longer walk the project folders on the GUI thread for a legacy
    `test_latest` folder (about 100 ms at 46k, 280 ms on the retry -> under 1 ms): they use
    the search the project open ran in the background (S08-01). A Library click stats about
    10 files instead of 24 and refreshes the bar in 0.95 instead of 1.56 ms (4.6 -> 3.9 ms
    median per click under a profiler).
  - Library on case-sensitive disks (Linux): a repeated miss of an exact name costs one
    `os.stat` instead of listing a 46k-file folder, about 150-220 ms less per Correct open
    of an image without a prediction (P2-01; Windows and default macOS: no change). Project
    open: the tree index takes the names it has just set (about 45-55 ms less GUI-thread
    time; S06-07); the scan builds its winners map only when a stem collides, and the remaps
    build no stem-key table when every name already is a Library stem (46k scan
    261 -> 98 ms on Windows, its five remaps 75 -> 9 ms; S06-08, S16-01).
  - Correct: a page refresh no longer scans the whole skeleton (2048 x 1536 1.6 -> 0.25 ms,
    6000 x 4000 12.4 -> 1.8 ms; S02-03); a skeleton save thins once (2048 x 1536
    35-37 -> 23 ms; S03-05); a skeleton edit below 100 % patches the cached zoom levels
    (6000 px: 17.3 -> 0.08 ms; S04-07); the skeleton files are looked up once per open, save
    and read-ahead plan, and the plan lists nothing on the GUI thread (on a case-sensitive
    disk 227 -> 0.2 ms; S01-05, C1-06); a blank class-labels layer that is not edited is not
    painted, tiled or warmed (about 1 ms less per frame at 640 x 480, 25 MiB less at
    2048 x 1536; S05-04); GT masks take the exact fast decode (640 x 480 2.2 -> 1.3 ms;
    S01-07); a case-variant prediction is looked up once per read (37 -> 19 ms on a
    case-sensitive folder of 20k masks), and the skeleton model keeps its saved copy in one
    buffer (2048 x 1536: an open 0.41 -> 0.07 ms, an edit and save 0.6 -> 0.26 ms). Zooms
    and fits take the overlay insets from the applied layout plan (73 -> 7 µs; S05-01).
  - Raster canvases (the macOS default; Windows or Linux without OpenGL, or while a
    dashboard lives): blank mask and label tiles are not drawn and partly empty ones are
    clipped to their content (at DPR 2 a frame with edited blank class labels 1.5-10 ms
    faster; X2-02); the endpoint markers are drawn directly where Qt would rebuild a
    viewport-size piece of their cache on every pan step (pan and zoom-step frames at DPR 2
    about 9-11 ms faster, 19-25 -> 8-14 ms at 400 %; X2-03); the Correct viewport is opaque,
    so the window is no longer painted under it (about 2-3 ms per frame at DPR 2; X2-04).
  - *Smooth JPEG blocks* (deblock) is 2.4-2.9x faster on 640 x 480 JPEGs (12-15 -> 5-6 ms
    per Correct open, `PgDn` and Enhance re-render while the box is ticked), with
    bit-identical pixels (S09-01). The F1 sheet's first paint takes 18 instead of 48 ms: its
    widgets are created under their final parents (S09-02).
  - Dataset export: images without labels cost no file check, the ids are remapped with one
    table and the counts take one histogram: 46k images with 300 labelled 3.8 -> 1.0 s,
    byte-identical output (S17-02, S17-03).
  - Dashboards: each figure is serialised once per revision (a repeated 170-tube time view
    14.6 -> 1.8 ms per callback; S14-04); built figures skip plotly's validation (that view's
    first build 29.5 -> 7.9 ms; P4-03); the Depth Profile Grid is laid out without
    `make_subplots` (the first six picks 249 -> 88 ms; P4-02); Export CSV of Growth Over Time
    builds one frame (26 -> 3.4 ms; S15-05); each dashboard after the first skips Dash's scan
    of every installed package's entry points (5.5 -> 1.9 ms), and the data load copies no
    text column. A released dashboard is collected (Python heap held 10.2 -> 0.03 MiB;
    S15-01), and a dashboard keeps only the columns it charts (7.7 -> 1.5 MiB at 46k;
    S15-04). The length dashboard parses its hover-image names once its first chart has
    been sent (the first chart about 110 ms sooner per open); the autosize relayout that
    plotly.js made after every view switch at an unchanged size is skipped
    (`app/assets/sprouts-autosize.js`: about 230 ms of JavaScript less per ten switches,
    charts settle about 1.3 s sooner per ten switches); the per-tube dropdown selection runs
    in the browser (one server request less per view switch, the figure request leaves
    7-17 ms sooner). These timings come from a browser bench in real windows. All 50
    sampled figure responses (25 view and selection cases, length and area, 46k-row CSVs)
    are byte-identical (F1a).
  - Result slots no longer wait on the GUI thread for their thread to end: the end of a
    Measure run 0.62 -> 0.003 ms, the export dialog's outcome 0.55-0.62 -> 0.03-0.04 ms
    (medians; the thread keeper holds the thread instead; K1a-B, G09).
- **Fix pass (2026-10-02).** Outputs are unchanged by all of these.
  - The Library read-ahead counts a decoded photo against its 192 MiB as soon as the
    decode finishes and enforces the bounds then (before, an unshown decode counted
    0 bytes, so up to 3 of them could exceed the bound).
  - The pre-Generate mask-origin scan reads the 26-byte headers on six threads for a
    folder of more than 1024 masks: 46k masks 1.4 -> 0.6 s under the wait cursor (the
    opens, not the reads, were 0.85 s of it). Same classification, and the scan still
    ends before the prompt.
  - Masks are read from disk once and decoded from those bytes (a model mask was read
    twice), and model masks (8-bit grey) take the exact Pillow fast path editor masks
    already took: a model mask reads 16 % faster at 640 x 480 and 19 % at 2048 x 1536, an
    editor mask 12 % / 4 % faster (the bands are copied straight out of the RGBA pixels).
    The fast path now accepts only files libpng reads the same way, so seven kinds of
    crafted, CRC-valid broken PNG raise `MaskFileError` again instead of reading as a
    mask; every real mask reads as before (3,001 checked).
- **UX and speed pass (2026-09-30).** Outputs are unchanged by all of these.
  - Fifth wave: entering Generate on a 46k project 6.8 -> 1.4 ms to paint (the status text
    recounted every name on each refresh; the counts are now cached); stage warm-ups wait
    for a 1 s stay, so passing through Generate starts no torch import (worst GUI gap while
    working on 45.4 -> 12.7 ms); the freeze after a warm-up no longer walks the frozen heap
    (30.4 -> 0.5 ms on the GUI thread).
  - Correct reads the next and previous images ahead on a worker thread (photo, mask plane,
    thinned skeleton; at most 3 images and 256 MiB, used only while the files are
    unchanged): `PgDn` costs 19.2 ms of GUI time instead of 78.9 ms at 2048 px and 8.9
    instead of 17.9 ms at 640 px (first paint included); the first `PgDn` after a library
    search or a Generate run 66-76 -> 20 ms at 2048 px. The Library display reads ahead too:
    `PgDn` at 2048 px about 10.4 -> 4.4 ms, arrow steps 9.2 -> 3.5 ms.
  - Skeleton layer (dense 2048 px stress skeleton): undo history after 25 commits
    75 -> 3.0 MiB; undo + paint 36.0 -> 3.9 ms; polyline commit + paint 29.6 -> 4.7 ms
    (endpoints recomputed only around the change, markers repainted only where they
    changed); first frame after a zoom to 200 % 9.9 -> 5.2 ms on the raster canvas, and on
    OpenGL the skeleton item's share 5.2 -> 2.0 ms (vector tiles made ahead when idle).
  - Mask canvas at 2048 x 1536: undo / redo of a whole-image stroke
    31.7 / 30.7 -> 2.0 / 2.0 ms, of a Clear 39.3 / 37.5 -> 2.0 / 1.3 ms; committing that
    stroke 24.7 -> 14.3 ms; opening a mask document 12.1 -> 10.5 ms.
  - OpenGL first frames at a new zoom: a 6000 x 4000 photo at 33 % 10.35 -> 0.29 ms and at
    67 % 10.06 -> 0.39 ms (the next pyramid level is prepared when idle); on the dense
    2048 px page fit -> 200 % 3.87 -> 2.32 ms and `Ctrl+=` from 50 % 8.34 -> 0.76 ms (mask
    tiles made ahead).
  - Raster canvas: a full frame with the five floating panels 3.93 -> 2.80 ms at 400 % and
    2.57 -> 1.20 ms at 100 % (cached shadow). The pixel grid costs 0.2-0.4 ms per frame.
  - Trace GT at 2048 x 1536: opening an image with a tracing 58.0 -> 23.7 ms, saving
    44.5 -> 7.3 ms, `PgDn` (save, then open the next image) 105.4 -> 35.5 ms, re-selecting
    the open image 43.4 -> 0.04 ms (at 640 px: open 11.1 -> 8.1 ms, `PgDn` 19.5 -> 13.8 ms).
    *Export dataset* opens without a freeze at 46k images: counting the traced images takes
    20 ms instead of 740 ms (74 ms with 100 traced).
  - Dashboards (46,970-row trial, 160 tubes): the first chart of a warm open 1886 -> 979 ms,
    of a cold open 3298 -> 2203 ms (649 ms when Measure was entered first, which warms
    the dashboard imports; 909 ms after resting on the Visualize button); the page is
    laid out at its real
    size, so the first chart is drawn once. View switches: length stacked 1028 -> 646 ms to
    paint, area profile 501 -> 256 ms, the 160-tube time view 639 -> 279 ms (figures built
    from dicts, legend automatic); 5-6 -> 3 server requests per switch, each chart drawn
    once per switch, and the faceted grid no longer drawn twice (last redraw
    1805 -> 1343 ms). The area Growth Over Time view (legend hidden above 30 tubes) paints
    in 501 instead of 600 ms when entered and in 399 instead of 524 ms the next time; the
    next profile or stacked view pays part of it back inside plotly.js (251 -> 272 ms,
    304 -> 394 ms), so five switches take 1,699 instead of 1,808 ms. *Toggle Legend* shows
    the legend in 369 ms and hides it in 183 ms (a 275-byte patch).
  - Measure-length preprocessing 313 -> 178 ms per 300 images (CSV bytes identical); the
    pre-Generate mask-origin scan of 46k files 1.57 -> 0.99 s; the project output scan
    84.5 -> 58.8 ms at 46k files; a library selection in an unmeasured 46k project
    59 -> 0.1 ms (the legacy-folder search left the GUI thread), without the ~50 ms wait
    while a CSV is parsed; the Welcome screen with a 500-entry recent list 496 -> 7 ms.
  - End to end (second walkthrough: 40 images at 640 x 480, `1a023bf` against `fcef800`,
    three interleaved runs each, median ms to paint): Correct `PgDn` 14.9 -> 10.3 and Trace
    GT `PgDn` 11.3 -> 8.7; Library -> Trace GT 14.5 -> 8.2 and Correct -> Trace GT
    12.4 -> 8.3; skeleton undo 8.0 -> 1.8 and Trace GT undo 5.9 -> 1.2; in Correct
    `Ctrl`+wheel 5.5 -> 4.5, `Ctrl+1` 4.0 -> 1.9 and a pan at 400 % 5.8 -> 4.1. Slightly
    slower: the Library's overlay and side-by-side image switch (6.2 -> 7.1, 5.0 -> 6.1;
    the single view 7.6 -> 5.6), its `Ctrl`+wheel (1.1 -> 1.5) and switching to a display
    page (2.8-3.0 -> 5.2-5.9, all under 8 ms).
- Correct decodes an image once for the photo, Enhance and the zoom pyramid, and switching
  between the mask and the skeleton reads no file and keeps the view (switching between the
  two old stages cost 13-20 ms plus a re-fit).
- Mask strokes on OpenGL cost the same at any image size: a stroke re-uploads only the
  256 px tiles it touched (stroke frame at 6000 x 4000: 19.5-22.2 ms -> 0.7-1.8 ms).
- Opening a mask builds its canvas 14-21x faster (640 x 480: 2.8-4.2 ms -> 0.2 ms;
  2048 x 1536: 29-33 ms -> 1.5 ms).
- Skeleton edits re-thin only the edited window, bit-identical to re-thinning the whole
  image (dense 2048 x 1536 stress skeleton: 26.6-27.3 ms -> 0.7 ms per edit).
- Skeleton endpoint markers are cached and drawn only while the skeleton layer is active
  (191 markers: 7.8 ms -> 0.13 ms per repaint); a dense skeleton at high zoom extracts its
  line segments per 256 px tile, at most 4 ms per frame (extracting a whole dense 2048
  frame took 334 ms).
- scikit-image is no longer imported at startup (`import app.gui.main_window` about
  200 ms faster, from 402 ms); a background prewarm loads it once the window shows, so the
  first Correct open does not wait for it either.
- Library zoom on OpenGL: 2.8-7.0 ms -> 0.6-0.95 ms per frame (the view no longer
  re-renders a cached background at every zoom step).
- Cold start to first window 4.1 s -> 0.6 s: no torch, CUDA, Dash or model load on the
  startup path.
- 46k-image library: view-mode switch 2.0 s -> 27 ms, entering/leaving Correct Mask
  1.7 s -> 3 ms, search 80 ms -> 6-19 ms per keystroke (debounced), PgDn 79 ms -> 1 ms.
- Mask/GT editor strokes repaint only their dirty rectangle (3-7 ms -> <0.1 ms per
  segment); undo stores patches (2048 px canvas: +300 MiB -> <1 MiB after 50 strokes);
  saved masks are compressed (1.2 MB -> ~5-8 KB, identical pixels).
- Dashboards: first chart about 30-40% sooner, closing 0.5 s -> 3 ms, faceted profile
  3-7x faster; first GPU run no longer spends 40-70 s in cuDNN autotuning.

### Added
- **Owner-decisions build (2026-10-25).**
  - Key texts show Cmd / Option on macOS (F1 sheet, tooltips, hints); on macOS each stage
    also answers to Cmd+Option+N (Option+N types a character in a text field); the F1 sheet
    lists the trackpad Pinch gesture (zoom about the fingers) in the Library and Correct
    scopes (WX-T7, O21).
  - `docs/examples/`: three real photos (the audit's T001 L044, T001 L050, T012 L047 of
    2024-07-18) with their mask, skeleton, Measure CSVs and a graph;
    `tests/test_correct_goldens_real.py` replays the Correct goldens on them in CI.
    `scripts/make_examples.py` regenerates the CSVs and the graph; a later change of the
    calibration or the estimator must regenerate them, or `tests/test_examples.py` fails
    (W5-T4, O35).
  - Correct: leaving an image (PgDn / PgUp, opening another, or Discard) with a branch
    picked with Select and smoothed in the meantime now says so in a toast, "Smoothed
    branch not applied · pick it and press Enter to apply" (also when Load file / Load
    prediction replaces it). A plain pick, Esc, or Smooth already on before the pick stay
    silent, and the pick is still not an unsaved change (W4-T2, O20).
  - The class editor lists retired classes under "Retired" with a **Restore** button; a
    restored class returns with its id, colour and place, and its pixels export under its
    own id again (WG-T9, O24).
  - Per-project field of view in `sprouts_project.json`, recorded with every measurement;
    CSVs measured with another field of view or length method are marked outdated (O3
    mechanism, memo Q3, W3-T2). The default stays 18 x 13 mm in this step.
  - Enhance popover: **Smooth JPEG blocks** (off by default, shown only for `.jpg` /
    `.jpeg` photos) softens the 8 x 8 compression blocks when zoomed in. Display only:
    masks, skeletons, ground truth and measurements are untouched (pinned by the Correct
    goldens replayed with the box on and an import scan). Saved in the Enhance defaults as
    `jpeg_deblock` (W4-T5, O22).
  - About, the device chip's tooltip and, when OpenGL was not turned off by the user, one
    warning at start-up say whether the canvases draw with OpenGL (W2-T9, P6, O29).
  - Trackpad pinch zoom on macOS and Linux (Wayland pointer gestures, X11 XInput 2.4): a
    pinch zooms every canvas about the fingers, continuously, between the zoom-out limit
    and 2000 %; a two-finger double tap (smart zoom) toggles fit and 100 %. Ignored while a
    stroke is drawn. Windows precision touchpads keep zooming through Ctrl+wheel (WX-T3,
    O21).
  - `THIRD_PARTY_NOTICES.md` reproduces the pytorch-CycleGAN-and-pix2pix BSD licence for
    the skeleton generator code derived from it (W1-T7, F1).
  - `SPROUTS_BATCH_POLICY` (`fixed` default, `auto`); run records gain
    `parameters.batch_policy` and `outputs.free_vram_gib` (W3-T3, O4).
  - Licence: GPL-3.0-or-later for the code (`LICENSE`), CC-BY-4.0 for the model weights,
    copyright "the SPROUTS authors"; the bundle ships both licence files; About shows the
    licence (W5-T1, O32).
  - Weights: the model weights are published separately (a Zenodo record, CC-BY-4.0; the
    DOI is added when the owner deposits them) and `checkpoints.zip` is no longer tracked
    (it stays in the history); `scripts/fetch_models.py` downloads and verifies them and
    never replaces a different file without `--replace` (W5-T2, O33).
- FAIR metadata: `CITATION.cff` (GitHub "Cite this repository"), `codemeta.json`, this
  changelog, a model card (`docs/MODELS.md`, with the SHA-256 of both checkpoints) and a
  data dictionary (`docs/DATA_DICTIONARY.md`: every output file, column, unit and
  encoding).
- `pip install .` builds an installable package with a `sprouts` command; the version is
  read from `app/__init__.py` only.
- GT dataset export writes `manifest.csv.provenance.json` (`app.provenance` schema): the
  export options (including the split seed), the counts, the class registry and the app
  version/commit.
- Reproducible Windows build: the PyInstaller spec is tracked, anchored at the repository
  root, bundles the SPROUTS icons and dashboard assets, and adds a version resource and
  `build_info.json` (version, commit, build time) to the app.
- CI runs the tests on Windows as well as Linux, builds and inspects the wheel, and
  validates `CITATION.cff`.
- Interface: responsive top bar (compact below the full width), one status line with the
  real device and task state, task progress with counts, ETA and Cancel, Alt+1..6 stage
  keys, a generated F1 shortcut sheet, an About dialog (version, commit, model checksums,
  citation), measured length/area in the metrics bar (read from the Measure CSVs),
  visible focus rings, AA-contrast text tokens, crisp HiDPI icons, collision-free editor
  overlays, offline dashboard assets and a CSV export from each dashboard.
- Provenance sidecars (`*.provenance.json`) next to metric CSVs and generation runs:
  app version, commit, model SHA-256, calibration and runtime precision.

### Changed
- **Python 3.14 and the newest dependencies (2026-10-27, owner decision).** Python 3.14
  (from 3.10: `requires-python >= 3.14`, CI on 3.14, ruff `py314`); PyTorch 2.14.1 and
  torchvision 0.29.1 from the CUDA 13.2 index (`cu132`, NVIDIA driver of the 580 series or
  newer; CPU wheels from `/whl/cpu`, macOS 14+ from PyPI) instead of 2.8.0+cu128; numpy
  2.5.4, scipy 1.18.1, scikit-image 0.26.0, opencv-python-headless 5.0.0.93, pillow 13.0.0,
  pandas 3.1.0, dash 4.4.1, plotly 7.1.0 with the vendored plotly.js basic bundle 4.1.1;
  PyQt6 6.11.0 runs on the Qt 6.11.2 runtime with PyQt6-sip 13.13.0; development tools
  pytest 9.2 (run with `-p no:dash`: Dash's pytest plugin, which the suite does not use,
  marks a hook the way pytest 10 removes), ruff 0.17.3, pre-commit 4.7 (ruff-pre-commit
  v0.17.3 with the `ruff-check` hook, pre-commit-hooks v6.0.0), packaging 26.3, setuptools
  84.2 (`tomli` dropped: the FAIR tests read `pyproject.toml` with the standard library's
  `tomllib`); CI on `actions/checkout` and `actions/setup-python` v7 (Node 24). Supported
  NVIDIA GPUs are now Turing (RTX 20 series, GTX 16xx) and newer (owner decision,
  2026-10-28): the CUDA 13 builds have no kernels for Maxwell, Pascal or Volta GPUs (GTX
  9xx / 10xx, Titan V, V100), which torch 2.7.1+cu128 still had; such a machine uses the CPU
  build (README "Requirements"). On the GPU, torch 2.14.1 (cuDNN 9.24) generates masks
  about 9 % and skeletons about 4 % slower than the old build on the same code (400
  photos, three interleaved runs: 7.67 -> 8.37 s and 3.00 -> 3.12 s); on the same code,
  `import main` is 17 %, opening the 46k project 11 % and `PgDn` in Correct 11 % faster on
  Python 3.14 and the new packages. The GPU outputs change
  (*Scientific output changes*); everything the dashboards show and export is as before,
  through these adaptations:
  - pandas 3: the dashboards' dates stay nanosecond timestamps (pandas 3 parses to
    microseconds), and a date outside 1677-2262 (a mistyped year in a file name) leaves out
    its own row, as pandas 2 did, instead of emptying the dashboard. A UTF-8 CSV damaged by
    a NUL byte keeps its header and image names (pandas 3.1 reads it as latin-1).
  - pandas 3.1 sums the rows of a key-sorted frame without Kahan compensation, which moved
    117 of 478 length and 196 of 478 area tube x date totals of the owner's trial in the
    last digit (Export CSV `297.54` -> `297.53999999999996`, the field average
    `218.3259375` -> `218.32593749999995`). `dash_data_cache.group_sum` / `group_mean` run the
    compensated sums of pandas 2 / 3.0 (pinned against pandas' own kernels), so every total,
    field mean, figure value and export value is unchanged.
  - plotly.js 4 defaults set back to plotly.js 3's: no "Upload to Cloud" modebar button (it
    would post a chart to cloud.plotly.com) and a 300 ms double-click (4.1 waits 500 ms)
    (`dash_shell.graph_config`); bar value labels keep their colour where plotly.js 4's WCAG
    rule picks white instead of dark grey (the fourth date-ramp blue `#4f97d1` and the
    blends next to it: trials with 5, 8-10, 12 or more dates; `theme.inside_label_colour`).
  - Dash 4.4.1 dropped a zoom when Select All or Clear All redrew Growth Over Time with the
    same `uirevision` (plotly/dash#4093): `app/assets/sprouts-uirevision.js` keeps it. In a
    browser check (QtWebEngine) of 23 states over drag and wheel zoom, pan, double-click
    reset, Toggle Legend, a view option, Select All, Clear All and view switches on both
    dashboards, every axis range equals Dash 4.1.0's.
  - Python 3.12's `sum()` adds floats with compensation (Neumaier), which moved the Depth
    Profile Grid's panel domains and title positions by up to 4 units in the last place
    (`0.9466666666666668` -> `0.9466666666666667`) in 837 of the 900 grids of 1-60 dates x
    1-15 tubes (nothing visible or exported). `faceted_profile.grid_layout` adds the panel
    sizes up left to right, as `sum()` did on Python 3.10, so all 900 grids' JSON is
    unchanged (`tests/test_viz_data_faceted.py` pins seven of them to Python 3.10's JSON).
  - Python 3.14 code style (ruff `py314`): `datetime.UTC`, PEP 695 generics
    (`SnapshotHistory[T]`, `PrefetchIterator[T]`, `run_modal`, `show_transient`, `_scan`,
    `key_to_stems`), `except A, B:` without parentheses (PEP 758), `map(..., strict=False)`
    in one test. Quoted annotations stay: `inspect.signature` evaluates annotations, and the
    window parts annotate with names imported only for type checking (UP037 ignored).
    Tools that parse the sources must run on Python 3.14, as Python 3.13 and older reject
    `except A, B:` (graphify's uv tool, for one: `docs/KNOWN_ISSUES.md` U5).
  - Provenance records name the new runtime (`runtime.python` 3.14.10, and on Windows 11
    `runtime.platform` `Windows-11-...` where Python 3.10 wrote `Windows-10-...`), and the
    About dialog's system line reads `Windows 11` where it read `Windows 10` (Python 3.12's
    `platform.release()` returns `11` on Windows 11); metadata and display text only.
  - pre-commit's end-of-file-fixer leaves `app/assets/plotly-basic.min.js` alone (upstream
    has no final newline), and `.gitattributes` checks it out with LF line endings whatever
    `core.autocrlf` says, so the committed file and every checkout of it stay
    byte-identical to upstream (its SHA-256 in `app/assets/THIRD_PARTY.md`, which
    `tests/test_viz_dash_plotly_bundle.py` checks).
- **Redundancy and speed pass (2026-10-26).** Code structure, interfaces and log records; no
  CSV value, output pixel or visible behaviour changes.
  - New Qt-free modules: `app/editing/planes.py` (`PlaneDocument`, `SavedPlane`,
    `planes_equal`: the saved state and the one plane compare that `MaskDocument`,
    `ClassesDocument` and the skeleton model share, while `RasterLayer` holds the layer
    members both raster layers repeated; S01-01, S01-02, C4-02, S03-08),
    `app/imaging/skeleton_vectors.py` (the skeleton's vector links, moved verbatim out of the
    skeleton layer item; S04-06) and `app/read_ahead_cache.py` (`ReadAheadCache`, the
    bounded cache the Library and Correct read-aheads share, with their limits, thread names
    and public methods unchanged; S07-04).
  - `app.handlers` imports nothing from `app.gui`: `project_layout_of` lives in
    `app/project_layout.py` (still importable from `app.gui.image_manager`), pinned by
    `tests/test_layering_contract.py` (X7-01).
  - One copy of each repeated rule: the generation threads and their outcome slots
    (`GenerationThread`, `thread_slot` in `app/handlers/generation_base.py`; S11-03), the
    grey decoder and the file hash (`io_utils.imdecode_bytes`, `sha256_file`; S13-07,
    S16-08), the GT mask read (`app.masks.read_mask_file`; S01-07), the GT-first resolvers
    (S13-08), the bounded failure log (`LogLimiter`; C6-02), the class histogram
    (`app.gt.store.histogram`; S17-03), the elided label and the first form that fits
    (`text_fit.ElidedLabel`, `fit_forms`; S09-05, S10-03), the device-pixel tile snapper and
    the pyramid level rule (S04-03, S04-05), the Totals by Tube builder and the dashboards'
    tube, legend and error helpers (S14-02, S14-03), Measure Length's name rule
    (`project_layout.skeleton_stem`; C1-04) and the prompts' `join_nouns` (S02-06).
  - Interfaces: `metrics.length_row` and `area_row` take keyword-only `memo=None` and
    `log=None` (compatible); `MaskStore.read` takes a keyword-only `path=`; `GuardedThread`
    has a `describe(exc)` hook, and `DashInitWorker`, `ExportThread` (E18), the Library's
    `ImageLoaderThread` and the dashboards' server threads (G06, G07) are `GuardedThread`s;
    `CanvasViewMixin` emits `viewport_changed` and has `end_holds()`, and an `InputSink` has
    `canvas_focus_lost()` (G13); `DashboardShell.register_callbacks` no longer takes `tubes`
    (F1a); `MemorySettings` is `app.settings.MemorySettings` (it left
    `app.gui.recent_projects`, no alias).
  - Settings: `app/settings.py` `app_settings()` is the only `QSettings` constructor: the
    user's store, or a fresh `MemorySettings` on the offscreen platform;
    `tests/test_settings_factory.py` fails on any other (K1a-A).
  - Threads: `app/workers.py` keeps every `QThread` from `start()` until Qt's own
    `finished`: `keep`, which `GuardedThread.start` calls; `release_qthread`, moved from
    `app.visualization._viz_server`, deletes a thread once it has finished without blocking
    the caller; `stop_all` runs on `aboutToQuit`; `base_finished` is the one accessor of the
    `finished` signal that subclasses shadow. Every `QThread` subclass of the app is a
    `GuardedThread` without its own `run()`, and no `QThread` gets a parent
    (`tests/test_workers_thread_keeper.py`; K1a-B, G05 to G09).
  - Pool jobs: `app.workers.start_pool_job` starts every `QThreadPool` job and marks it;
    resolving the runtime, the warm-up and building a model raise `RuntimeError` in such a
    job, where torch deadlocked (dc11edf), and `app.inference.runtime` still imports no Qt
    (`tests/test_workers_pool_jobs.py`; K1a-C2, G10, G11).
  - Dialogs: `app/dialogs.py` has the app's one `exec()` of a dialog (`run_modal`: `exec()`,
    the answer read, then the dialog freed) and shows every box that frees itself once
    answered (`show_transient`). Every dialog the app builds runs through one of the two,
    with the same modality, parents and texts; `tests/test_dialogs.py` finds no other
    `exec()` of a dialog. The static `QMessageBox`, `QFileDialog` and `QColorDialog`
    functions do not go through it (K1b-E, G17, G18).
  - Hold keys: `CanvasViewMixin.end_holds()` ends a held `Space` (on the editor canvas `B`
    first); a view's focus-out and the library tree's key relay call it, so the Correct
    page's event filter for it and the relay's made-up focus event are gone (K1a-C1, G12,
    G13).
  - Log records: a failed dashboard build is logged as "DashInitWorker failed" by
    `app.handlers.worker_base` (it was "Dashboard initialisation failed" by
    `app.visualization.viz_window`); a failed Correct read-ahead job by
    `app.read_ahead_cache`; an unexpected dataset-export error as "ExportThread failed" by
    `app.handlers.worker_base`, with its traceback (E18; earlier in this pass it was "Dataset
    export failed" by `app.gui.export_dataset_dialog`, S10-02); a failed folder scan as
    "ImageLoaderThread failed" there, and the scan's "Failed to scan image folder <folder>"
    record stays, now without the traceback, which the new record carries (G07); a
    dashboard server's failure as "DashServerThread failed" or "DashServerThreadArea
    failed" there (it was "DashServerThreadLength failed" or
    "DashServerThreadArea failed" by `app.visualization._viz_server`; G06); a quit that
    leaves a kept `QThread` running logs "Quitting with N thread(s) still running: …".
  - Tests: `tests/conftest.py` owns one session `QApplication`, a fresh config folder per
    test (no test reads or writes the user's Enhance defaults), `KMP_BLOCKTIME=0` and opt-in
    guard fixtures, with shared helpers in `tests/guards.py`, `tests/qt_wait.py`,
    `tests/window_support.py`, `tests/correct_support.py` and `tests/generation_support.py`
    (T1-01, T1-04 to T1-09). The test modules use them since wave 3b (one copy of the
    Correct helpers instead of two), and fixed sleeps became waits on a condition: the
    generation handler tests 12.3 -> 1.8 s, the canvas memory-cycle tests 5.9 -> 0.4 s, a
    released-thread test 1.58 -> 0.39 s; the fetch-model tests give up on an unreachable
    server after 0.3 s instead of 2 s and poll their test server every 0.05 s
    (5.2 -> 0.9 s) (T1-01, T1-02, T1-10). After each test, conftest warns about a thread
    the keeper still holds running (G14); `tests/leak_census.py` (`assert_freed`, `no_gc`)
    is the one leak-test helper
    (G15). Guard tests (K1b-F) audit click focus on the Correct page, the focus ring of
    every widget stylesheet, the 900 px window width with a measured image selected, the side
    card's width and text fitting at 125 % scaling; today's offenders are strict `xfail`s
    with their IDs (`docs/KNOWN_ISSUES.md`, table 2). On Windows,
    `tests/test_visualization_manager_release_native.py` runs the dashboard release on the
    native platform (about 25 s).
- **Owner-decisions build (2026-10-25).** Display and wording; no CSV value or output pixel
  changes.
  - Dashboard views have one set of names (Totals by Tube, Growth Over Time, Depth Profile,
    Depth Profile Grid; legend "Measurement date", ISO hover dates); dates are coloured on
    one blue ramp that keeps clear of the mask, skeleton and selection colours, the same
    colour for a date in every view and every tube (W4-T6, O27).
  - A generation run that writes and fails nothing leaves `_sprouts_run.json` alone; every
    other run is also appended to `_sprouts_runs.jsonl` in the same folder (W2-T1, R6, O16).
  - macOS: the canvases draw without OpenGL by default (OpenGL is opt-in with
    `ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=1`); About and the device chip say so, and no
    slow-path warning appears for this default (WX-T8, O21).
  - Correct: Save writes a skeleton correction only when the skeleton differs from the
    generated one as Correct opens it. Saving is not acceptance: `Ctrl+S` on an untouched
    skeleton writes nothing ("Skeleton unchanged: nothing to save"). A correction edited back
    to the generated skeleton is removed ("Skeleton is the generated one again: correction
    removed"), and so is an old no-op correction once **Load generated** is saved over it.
    An emptied skeleton over a non-empty generated one is still saved. The thinning helpers
    moved to the Qt-free `app/editing/thinning.py`. No CSV value or existing output pixel
    changes (W1-T3, L2, O2).
  - Measure records each input's size, time and SHA-256 in `<csv>.inputs.json`; "changed
    since measured" compares content when the times differ and reports new and removed
    inputs; without a record it keeps the file-time rule (W2-T2, R5).
  - Fonts: the sans and mono font chains fall back to the macOS and Linux system fonts
    (SF Pro Text, Helvetica Neue, Noto Sans, DejaVu Sans; SF Mono, Menlo, DejaVu Sans Mono,
    Liberation Mono) after IBM Plex and the Windows fonts (WX-T5, O21).
  - CI also runs on macOS (Apple silicon; the test step may fail until its first green run);
    the suite passes on Linux (the font-dependent checks now use the real font's widths);
    README documents installing on Linux x86_64 and Apple-silicon macOS. The status chip
    picks its name form by the width its label really gets (it could end 1 px over its
    budget) (WX-T6, O21).
  - Citation record: authors Parth, Varela and Leakey; cited as "SPROUTS"; `CITATION.cff`
    ships in the Windows bundle so About can show how to cite (W5-T3, O34).
  - Correct, mask layer (WG-T2, O2, O9, O26): the mask opens `gt/mask/<stem>.png` when it
    exists, else the model's prediction `mask/<stem>.png`, and Save writes only
    `gt/mask/<stem>.png`; `mask/` is never written or removed. Save writes only the layers
    with unsaved edits; with none it writes nothing ("Nothing unsaved"). Clear + Save writes
    an empty GT mask ("Empty mask saved as GT for <image>: root area 0") instead of deleting
    the file; a mask edited back to equal its prediction removes the GT file ("Mask equals
    the prediction: removed gt/mask/<stem>.png"). A **Load prediction** dock button puts the
    prediction back as one undoable step. Mask states read "GT" / "Prediction". No CSV
    value changes yet: Measure reads `gt/` from WG-T8 on. Golden `mask_clear.saved` of
    `synth_model_mask_and_skeleton`: file removed → empty GT file (O9); every other golden
    pixel unchanged.
  - Generate's overwrite prompt speaks of predictions: "Predictions already exist", with
    **Keep predictions (predict missing only)** (default), **Replace all predictions** and
    **Cancel**. It no longer counts hand-corrected masks: they live in `gt/`, which Generate
    never reads or writes. Run records and the `keep_existing` / `overwrite_all` policy
    values are unchanged (WG-T7, O9).
  - Correct, skeleton layer (WG-T3, O2, O26): the skeleton opens `gt/skeletons/<stem>.png`
    when it exists, else the prediction `skeletons/<stem>_fake.png`, and Save writes only
    `gt/skeletons/<stem>.png`; `skeletons/` is never written or removed, and
    `skeleton_corrections/` is retired (never read or written; no migration). **Load
    prediction** works on the skeleton too (one undoable step; a Save of it removes the GT
    file). Skeleton states read "GT" / "Prediction". No CSV value or output pixel changes;
    the goldens' `start.skeleton_source` reads `prediction` instead of `generated`.
  - Library badge with three marks (WG-T6, O26): mask (green) and skeleton (orange) — a ring
    for none, a circle for a prediction, a diamond for GT — and a purple class-labels mark;
    hovering a row gives the words (`Mask: GT · Skeleton: prediction · No classes`).
  - Metrics bar (WG-T8, O1): the status uses the Library badge's words (`Mask: … ·
    Skeleton: … · Classes / No classes`, replacing *Skeleton corrected* / *Mask ready*), and
    each value's tooltip names the file it was measured from (GT or prediction) and why a GT
    file was not used.
  - Correct, class labels (WG-T5, O23, O14, O24): the class labels are the third layer of
    Correct (row "Classes", purple), painted with Brush, Eraser and Fill in the active
    class's colour; the class panel (the classes, their "checked" boxes and the gear of the
    class editor) and the layer's three rail tools show while they are edited. Keys:
    **M** / **L** / **G** edit the mask / the skeleton / the class labels (they were `1` /
    `2`), **X** goes back to the previous layer, `1`-`9` and `0` pick a class (class labels
    only); `B` and `F` stay on the class labels (from the skeleton they take the mask's), and
    `H` / `Shift+H` show or hide every layer / the other layers. A class-label save writes
    only `gt/<stem>.png` (`gt/classes.json` first when missing) and marks the Library row;
    an untouched image is not saved (O2: tick the boxes to record "looked, nothing found";
    Trace GT wrote an empty labels file). `PgDn` saves them without asking unless unknown
    classes were mapped to Ignore (then it asks). While `gt/classes.json` is invalid only
    the class labels are read-only, with one danger notice per image (on the first refused
    edit). The top bar's Correct page holds **Classes…**, **Export dataset…** (it saves
    Correct's edits first) and the new **Remove GT…**, which, after asking, deletes the
    image's `gt/mask/<stem>.png`, `gt/skeletons/<stem>.png` and `gt/<stem>.png` (never
    `mask/`, `skeletons/` or `gt/classes.json`) and reopens the image from its predictions;
    a file it cannot delete is named. The Trace GT page has no actions until WG-T11 removes
    that stage. No CSV value or output pixel changes; the class-label goldens replay through
    the page.
  - Stages (WG-T11, O23): the **Trace GT** stage, its page and its shortcut scope are gone;
    the class labels are a layer of **Correct** (WG-T5). Five stages remain, `Alt+1` …
    `Alt+5`: Library, Generate, Correct (`Alt+3`, was `Alt+4`), Measure (`Alt+4`),
    Visualize (`Alt+5`); the F1 sheet has three sections (Global, Library, Correct). A GT
    skeleton saved in Correct marks the Library row and refreshes the metrics bar at once,
    and a class-label save shows one toast ("Saved class labels · …"). With one stage
    fewer the window's minimum width is about 877 px with the app's fonts (was about
    917 px), under the 900 px target.
  - Correct's **Save** is enabled (purple) only while something is unsaved and disabled
    otherwise; its tooltip says "Nothing unsaved" or "Open an image first". The page has one
    **Clear**, in the dock, named for the active layer, whose tooltip says what Save then
    writes; the top bar's Clear button is gone, which narrows the window's minimum width from
    877 to 759 px (W4-T1, O18, E1).
  - The Measure buttons read *Measure Length* / *Measure Area* and say what they read and
    write; measuring again with nothing changed since the CSV (inputs, field of view, length
    method) asks first, also when values come from GT files (R8, O17, W2-T8a). The Generate
    stage says masks give area and skeletons length, independently (O14). Correct with no
    image open says "Select an image in the Library" (X9).
  - `metrics.skeleton_arc_length_px` returns `SkeletonSteps(horizontal, vertical, diagonal,
    corners)` with m-adjacent diagonals (it returned a 3-tuple of all-pairs counts); code
    that unpacks three values must change (W3-T8). `root_length_mm` / `area_row` keep their
    signatures.
  - Wording (WG-T10, O14, O26): model output is a "prediction", a human-saved result is "GT",
    and Correct is one editor with three layers (Mask M, Skeleton L, Classes G). The
    Generate tooltip and prompt, the Export dataset tooltip and dialog say "class labels"
    and "class-id label images". README gains the "Which layer?" table; README, the data
    dictionary, the model card and CLAUDE.md describe the `gt/mask/`, `gt/skeletons/` and
    `gt/<stem>.png` folders. No measured value or output pixel changes.
  - Calibration on record (W2-T4, M2 step 1): each Measure CSV's provenance records the
    pixel sizes of the measured files (`inputs.sizes_px`); Measure warns once when the GT
    files of a run have more than one size (the photos differ) or the predictions do; the
    dashboards' export, the metrics bar's field-of-view cell and About show the field of
    view each CSV was measured with, and say when this app now uses another. No CSV value
    changes.
  - One `file_stamp` and one `same_file` in `app/io_utils.py` (W5-T5, X8), used by the
    read-aheads, the Correct documents, the save guards, the recent projects, the GT
    export's folder check and the window. `file_stamp` treats anything but a regular file as
    unreadable everywhere; `same_file` is False for an empty name and compares by
    `path_key` (case folding on Windows and macOS).
  - The pre-Generate scan only lists `mask/` and opens no file (W5-T5): the PNG-header
    mask-origin classifier had no reader left after WG-T7.
- **UX and speed pass (2026-09-30).** Display and interaction; saved pixels stay pinned by
  the goldens.
  - Keys: page shortcuts work wherever the keyboard is while their page is on screen, so
    `Ctrl+S`, undo, tool, layer and zoom keys work right after a click in the library tree
    (text fields keep their typing, dialogs block the keys); held `B` and `Space` and
    Correct's `S`, Enter, Esc and Delete reach the canvas from the tree. In the Library
    `Ctrl+0` / `Ctrl+1` / `Ctrl+=` / `Ctrl+-` fit, show 100 % and zoom, F1 lists them, and
    the metrics bar shows the zoom. `Ctrl+'` shows a pixel grid at 800 % and above in
    Correct and Trace GT (display only). Tab reaches the Correct Layers rows and their eyes
    (Space or Enter picks a layer, Space toggles an eye) and the Trace GT class panel's
    boxes (Space ticks); a click never takes the keyboard from the canvas. The Tab order of
    the top bar, of the Welcome screen and of Correct follows the screen. In Correct it goes
    canvas, polyline prompt (while shown), rail (at the chosen tool), Enhance popover (while
    open), dock, zoom cluster, Layers; it used to visit the zoom cluster first and the size
    slider after the Layers rows.
  - Correct: a polyline draft of two or more points (or a picked branch that was moved)
    counts as unsaved, so the one prompt also covers a tree click, a stage switch, *Load*
    and quitting; Save and `Ctrl+S` finish the draft first. Skeleton undo keeps 100 steps
    (was 25; 64 MiB cap). The skeleton tools' grab and snap distances are screen px
    (unchanged at 100 %). The status chip shows "Image 12 / 40" and shortens a camera file
    name by its suffix, time and field first, keeping tube, L-number and date. Layer opacity
    and visibility are kept across restarts. The floating panels' shadow is cached (same
    look).
  - Zoom: step zooms (keys, buttons, whole wheel notches) glide over 120 ms, anchored and
    interruptible (instant when Windows' UI effects are off); fits and zoom presets stay
    instant. Zooming out stops at min(100 %, half the fit zoom) instead of a fixed 10 %.
    Between about 76 % and 50 % the photo blends in the half-size pyramid level (less
    aliasing).
  - Trace GT: selecting the open image again keeps its tracing, history and view unless its
    files changed on disk, as Correct does. An invalid `gt/classes.json` shows "Read-only"
    on the chip, and "Editing is off" is announced once per image. The export dialog says
    how many images have a GT tracing and keeps Export disabled when none has. Long class
    names are elided with the full name as a tooltip.
  - Library: Overlay and Side by side draw the skeleton correction when one exists (display
    only; Measure still reads the generated skeleton, FR-03), with STATUS "Skeleton
    corrected" and a diamond mark in the tree. The metrics bar flags LENGTH and AREA as
    outdated separately (AREA against its mask, LENGTH against the generated skeleton), and
    each tooltip names its own CSV and the input that changed. Narrow rows keep their start
    ("2024.07.18 · 128", "07.18", "L44 · 11:15", "L44"). The status line says what Generate
    or Measure would work on (images without a mask or skeleton; CSVs current or changed
    since measured).
  - Visualize: entering the stage opens the last chosen dashboard (else the highlighted
    one); with nothing to chart, the page says why and offers *Go to Measure* or *Go to
    Generate*. Dashboards are released 5 minutes after the stage is left, which gives the
    canvases their OpenGL viewports back (a visit within those 5 minutes stays instant).
    Bars carry value labels only when at most 30 share the axis (the hover keeps the values;
    SVG exports of denser charts have no labels), the time views of both dashboards show
    their legend automatically for up to 30 tubes (the area view gains a *Toggle Legend*
    button over the chart's top-left margin; the choice is kept across views), and the
    control strip opens without animation and hides for views without settings. plotly.js
    basic 3.5.0 (1.1 MB, MIT) is vendored in `app/assets/` and pinned to plotly.py's
    version by `tests/test_viz_dash_plotly_bundle.py`.
  - Notices: toasts keep clear of the status line, the metrics bar and the editors' docks,
    zoom clusters and side panels. The Measure and Generate toasts name the file and its
    project folder ("Saved root_lengths.csv in the skeletons folder"), with the full path in
    the status line. Checked boxes show a check mark (8:1 contrast), not only a fill. The
    Welcome stage list is plain text. The device chip names the device without a first
    inference ("Checking GPU…" meanwhile); `SPROUTS_DEVICE_CHECK=0` or the setting
    `device_check/at_startup` turns the check off. The chip is named through the NVIDIA
    driver (ctypes, no torch import): 271 ms after the window shows, +20 MB. The About
    dialog's Runtime line does the same before PyTorch loads ("NVIDIA GeForce RTX 5070 Ti
    (from the NVIDIA driver; PyTorch not loaded yet)", asked on a worker thread, 59 ms after
    the dialog opened) and shows the resolved runtime once it has; *Copy details* reads the
    line again, so a runtime resolved meanwhile is copied.
  - Warm-ups follow intent instead of timers (`app/gui/warmup.py`): entering Generate or
    resting on its run buttons loads torch and the models' code; entering Measure, resting
    on its buttons or on the Visualize stage button loads dash / plotly / pandas. One at a
    time, never during a folder load, a Measure CSV parse or a batch job. Nothing heavy
    loads at start-up or project load any more: start-up memory 511 -> 145 MB (124 MB at
    `1a023bf`), no GUI pause over 16 ms at start-up, quit 466 -> 91 ms. A Generate
    clicked 8 s after entering the stage reaches its first batch in 977 ms (3124 ms at
    `1a023bf`).
  - `app/gc_policy.py`: full garbage collections are held while a warm-up imports, and the
    long-lived heap is frozen (`gc.freeze()`) after start-up and after each warm-up, so a
    full collection no longer rescans the imported libraries (a full pass 10 s after
    start-up: 55.8 -> 0.0 ms; 10 s after a 46k load: 122 -> 13 ms).
  - Library browsing at 2048 x 1536 (40 images forward and back): an image you leave is
    freed at once instead of by a later garbage-collection pass (the photo and
    mask-canvas items no longer hold themselves in a reference cycle): memory peak
    1374 -> 657 MB, 1153 -> 650 MB at the end (+506 -> +2 MB), and no collection pause
    during the browse instead of two of 40-51 ms.
  - Correct keeps the image it leaves as a finished read and adopts the Library's photo
    decodes (one decode per photo): a 15-action session at 2048 px does 27 reads instead
    of 48, and going back to the Library takes 2.2 ms instead of 65.5 ms.
  - Tests: one shared Qt teardown (`tests/qt_teardown.py`) frees deleted widgets and
    their signal proxies after every test; no crash in 24 loops of the editor test set,
    and the whole suite runs 22 % faster (195.5 -> 152.7 s).
  - Code (review CQ-07): `MainWindow` is built from parts in `app/gui/window/`
    (`main_window.py` 1,556 -> 699 lines), and the length dashboard from
    `dash_length_layout.py`, `dash_length_views.py` and `dash_length_callbacks.py`
    (`dash_app.py` 845 -> 178 lines). Both are pure moves:
    `tests/test_shell_window_split_surface.py` pins the window's API surface and
    `tests/test_viz_dash_split_golden.py` the hash of the dashboard's served layout, its
    callback wiring and the hash of its clientside JS. A scripted golden run of the window
    gave identical session states, signal
    receivers and QObject trees, and start-up stayed within noise (`MainWindow()`
    221.0 -> 218.9 ms, process start to first paint 438.4 -> 441.7 ms, n = 20).
  - Generation ignores a folder named like an image (`x.jpg/`), as the Library does; it was
    counted as a failed input.
- **One Correct stage** replaces *Correct Mask* and *Correct Skeleton* (seven stages become
  six: Library, Trace GT, Generate, Correct, Measure, Visualize; `Alt+1` … `Alt+6`, Correct
  is `Alt+4`). The mask and the skeleton of an image are drawn together on one canvas and
  edited one at a time: `1` / `2` / `X` pick the layer (a tool key of the other layer
  switches too), the other layer stays visible at half its opacity, a Layers panel shows
  each layer's state and visibility, `H` hides both layers and `Shift+H` the other one.
  One prompt names every unsaved layer, and one undo timeline covers both (`Ctrl+Z` undoes
  the last action of either layer without switching layers). Coming back to the same
  image keeps the zoom and position. File formats and locations do not change.
- `Ctrl+S` in Correct saves every layer with unsaved changes, the mask first; with nothing
  unsaved it saves the active layer, as each editor did. A layer without changes that is
  not active is never rewritten (a model mask keeps its 8-bit greyscale file).
- `PgDn` / `PgUp` in Correct save the unsaved layers without asking, then open the next /
  previous image, as in Trace GT; every other way of leaving an image asks once. A polyline
  draft that could be finished (two or more points) is never saved: `PgDn` / `PgUp` then
  stay and say "Finish the line (Enter) or cancel it (Esc) first". A draft dropped by
  opening another image, *Discard*, *Load* or a new folder is announced ("Polyline draft
  discarded").
- The polyline prompt names the Smooth key as it names the others: *Finish line (Enter)*,
  *Cancel (Esc)*, *Smooth (S)*. `S` toggles Smooth (a curve through the points, 3+ points)
  while the Polyline tool is active, also on a branch picked with Select; the Select and
  Polyline help texts and the branch-editing hint name `S` too.
- Holding `B` and scrolling from the skeleton (or from Fill) switches to the mask brush on
  the first notch, so the dock's slider and the ring show the size being set.
- The rail dims the tools of the layer that is not edited more clearly, and the two erasers'
  tooltips name their layer ("Mask eraser", "Skeleton eraser"). The dock keeps its width
  and place across tools and layers: a tool without a size shows a disabled placeholder,
  *Load skeleton file* is disabled (not hidden) while the mask is edited, and the size
  readout has a fixed width.
- Skeleton endpoint markers, the highlighted endpoint and polyline handles stop growing at
  100 % zoom (at 1600 % a marker was a 128 px disc hiding the end of its line); below
  100 % they shrink with the image as before. Grab distances are unchanged (image px).
- `[` / `]` change the size of the current tool by 12 % (at least 1 px) for the brush and
  both erasers; at the skeleton eraser's default 20 px this is still 2 px.
- Brush and eraser rings use the layer's colour (green mask, orange skeleton); eraser rings
  are dashed.
- A key or click that changes the tool, the layer or the visibility in the middle of a drag
  first ends the drag as a release there would (a mask stroke is committed, a skeleton
  eraser drag is thinned and recorded, a Connect drag joins there); the rest of that drag
  is ignored, and undo / redo wait until it ends. `Esc` in the middle of a mask stroke
  keeps the stroke. (`E` pressed in the middle of a mask stroke used to erase the rest of
  it.)
- **Smooth zoom** on every canvas (Correct, Trace GT, Library): the photo is drawn from an
  area-averaged pyramid with bilinear filtering, so it shows no pixel squares when zoomed
  in and does not shimmer when zoomed out. Masks and GT tracings show their exact saved
  pixels when magnified and thin strokes no longer vanish when zoomed out; the skeleton is
  a line at every zoom. Zoom keys and the − / + buttons step through round percentages
  (… 80, 100, 125, 150, 200, 300, 400 …, up to 2000 %), a right-click on the zoom readout
  offers presets, and the wheel stays continuous. OpenGL canvases use 4x multisampling
  (`ROOT_VIEWER_GL_SAMPLES=0` turns it off, a number sets the sample count).
- The Library overlay draws the generated skeleton with the same layer item: soft at
  100 % and above as before, and never broken into dashes when zoomed out.
- Trace GT runs on the same canvas parts as Correct; its behaviour is unchanged except the
  zoom, and a class key, `E` / `B` or `[` / `]` pressed in the middle of a GT stroke now
  applies from the next stroke (the stroke keeps its class, tool and size).
- Provenance records (`*.provenance.json`) list the packages the run had loaded
  (`app/provenance.py` is unchanged). scikit-image is no longer imported at startup: a
  background prewarm loads it once the window shows (and measuring loads it when needed),
  so records normally still list it; one written before the prewarm finished may not.
- `gt/classes.json` is parsed strictly: a wrong field type (for example
  `"retired": "false"` or `"id": 1.9`) is reported with the class and field it concerns
  instead of being silently coerced, and `"id": 1e999` no longer aborts the app.
- `scripts/build.ps1` and `scripts/build.bat` work from any folder, stop when model
  weights are missing (unless told otherwise) and exit with an error when PyInstaller
  fails (`build.bat` used to report success after a failed build).
- README: the documented PyTorch nightly no longer exists; install the stable
  torch 2.8.0 / torchvision 0.23.0 build for your CUDA version (or the CPU build).
- `SPROUTS_CHECKPOINTS_DIR` relocates the model weights, so a `pip install .` copy
  (whose application folder is `site-packages`) can find them.
- Cancel (or "Stop it and quit") during the last mask batch no longer starts the
  chained skeleton pass; once the window closes no new job can start. On the CPU
  fallback batches are small (masks 4, skeletons 8), so Cancel takes a second or two
  instead of about 10 s; a job that still runs after the first 5 s on close gets the
  window hidden and a longer wait instead of being killed mid-batch.
- `CUDA_VISIBLE_DEVICES=""` (CUDA reported available with no device) now takes the CPU
  fallback; the mask model used to fail to load.

### Removed
- **Redundancy and speed pass (2026-10-26).** Code with no caller in the app, the scripts or
  the documents (the tests that only covered it went with it):
  - `app.inference.root_length_inference_handler` and `root_area_inference_handler`
    (`RootLengthCalculatorThread`, `RootAreaCalculatorThread`, `process_single_image`,
    `process_single_mask`): use `MetricCalculatorThread(MeasureRun(LENGTH or AREA, images,
    folder))` and `app.inference.metrics` (S11-06).
  - `MaskStore.write` and `MaskStore.remove`: GT masks are written by
    `app.masks.write_mask_file`, and `MaskStore` keeps `find` and `read` (S16-05).
  - `app.gui.ImageNormalization` (`apply_clahe`, `apply_contrast_stretching`, `apply`), the
    facade over `app.enhancement` that lost its callers with the two editors the Correct
    stage replaced: use `app.enhancement.clahe`, `contrast_stretch` and `enhance` (S09-06).
  - Library and widgets: `ImageManager.parse_image_name`, `get_image_names`, its folder
    dialog and `has_fake_real_pairs` (S07-06); `ui_panels.create_icon_button` and
    `create_section_label` (S08-02); the seven XML icons of `app/utils` and their lookup
    branch, and five icons of `resources/icons` (`calc-area.svg`, `calc-length.svg`,
    `viz-area.svg`, `viz-length.svg`, `paint-brush.xml`; S08-03); `IconButton.set_icon_name`,
    `CanvasViewMixin.set_pan_mode`, `update_cursor` and `_scene_pixel`, and the `TypeError`
    fallback of `shell_chrome._stage_icon` (C5-05, S05-05); `Calibration.mean_mm_per_px`
    (S16-10); the `Toast` / `ToastManager` re-export of `app.gui.widgets.overlays` (S10-04).
  - Generate: `SkeletonModel.run` and its DataParallel and InstanceNorm-patch paths
    (S12-03); `PercentThrottle` and the `on_done` / `should_stop` options of `AsyncWriter`
    (S12-04); the generation threads' unconnected `progress` and `error` signals (C3-07).
  - Visualize: `prewarm_dashboards` and `viz_window.prewarm_web_engine` (S11-04, S15-08);
    the busy fallbacks `DashboardSpec.calculator_attr` and `SkeletonHandler._generation_busy`
    (S11-08); the light template `sprouts_light` (the documented light print build no longer
    worked: the figures use the dark tokens), `theme.style()`, `FONT_MONO`,
    `DashApp.run_server` and `get_encoded_image`, and `DashAppArea.run_server` (S14-07,
    S15-07); `DataProcessor.get_unique_treatments` and `get_unique_genotypes` (S13-09).
  - Correct: the skeleton model's legacy history paths for whole-array steps (S03-03),
    `read_back.mask_read_back` and the lazy re-export of `app.gui.canvas_layers` (S04-04).
- **Owner-decisions build (2026-10-25).** The Trace GT editor (WG-T11):
  `GtTracingInterface` (`app/gui/gt_tracing_interface.py`) and its base
  `RasterEditorBase` (`app/gui/raster_editor_base.py`), `MainWindow.gt_tracing_interface` /
  `toggle_gt_tracing`, `Panel.GT_TRACING` (the key `"gt_tracing"` and index 3 are
  rejected, no alias), the shortcut scope `"Trace GT"` (`shortcuts.TRACE_GT`), and their
  tests (ported to `tests/test_correct_classes_*.py` / `tests/test_correct_tab_order.py`;
  the teardown guards to `tests/test_correct_editor_teardown.py`). `ClassPanel`, the class
  editor, the dataset export and `app/gt/` stay. Also retired: `skeleton_corrections/`
  (WG-T3; corrections are GT in `gt/skeletons/`).
- **Documents (2026-10-25, W5-T7, O36).** `docs/Architecture_2 useful.md` and its PDF, and
  `docs/BUILD_EXECUTABLE.md` (the README section "Building the Windows app" is the one
  text). `docs/Architecture_2.md` is trimmed to what is true of the code (the Correct
  editor's composition, the file tree, a short data flow); `tests/test_doc_paths.py`
  checks that every repository path the README, `CLAUDE.md` and `docs/*.md` name exists.
- **Compatibility re-exports and dead code (2026-10-25, W5-T5, F5).** The modules
  `app/handlers/worker_base.py`, `app/gui/feedback.py` and `app/handlers/window_feedback.py`
  and `shell_chrome.build_ribbon`: import `GuardedThread`, `JobCancelled` and
  `describe_error` from `app.workers` and the `UiFeedback` port from `app.feedback` (the
  worker logger is still named `app.handlers.worker_base`). Left by the GT unification:
  `config.SKELETON_CORRECTIONS_DIRNAME` (and its entry in the dataset export's protected
  folders; `mask/`, `skeletons/` and `gt/` stay protected), `ImageManager.mask_path_for`,
  and the PNG-header mask-origin classifier of `app/inference/mask_origin.py`
  (`MaskOrigin`, `classify_mask`, `png_colour_type`, `ExistingMasks.manual` / `model` /
  `unknown`).
- The two editors the Correct stage replaced: `MaskTracingInterface`,
  `SkeletonCorrectionInterface`, their graphics views, `MaskDrawingMixin`,
  `CanvasPixmapItem`, `SkeletonOverlayRenderer` (replaced by `SkeletonLayerItem`) and
  `skeleton_document.ask_unsaved` (replaced by `editor_prompts`); the unused
  `mask_cursor_utils.py` and `app/handlers/mask_handler.py`; the hidden legacy toggle
  buttons and `MainWindow.toggle_mask_tracing` / `toggle_skeleton_correction` (now
  `toggle_correct`). The right-panel pages are renumbered without aliases (`Panel.DISPLAY`
  0, `CORRECT` 1, `VISUALIZATION` 2, `GT_TRACING` 3): the keys `"mask_tracing"` and
  `"skeleton_correction"` are rejected. The shortcut scopes "Correct Mask" and "Correct
  Skeleton" become one scope, "Correct".
- `MainWindow.mask_handler` and the legacy `MainWindow.loading_progress_bar` progress
  shim (handlers report through `MainWindow.feedback`).
- `VisualizationInitWorker` / `VisualizationInitWorkerArea` and their `finished` /
  `error` signals; both dashboards use `app.visualization.viz_window.DashInitWorker`
  (`built` / `failed`).
- black and isort configuration: ruff is the single lint, import-sort and format tool,
  as pre-commit already ran it.

### Scientific output changes
- **GPU predictions: PyTorch 2.14.1 with cuDNN 9.24 (2026-10-27, accepted by the owner).**
  Generate on the GPU (RTX 5070 Ti, TF32, deterministic cuDNN, default batch policy) moves a
  few pixels. Before: torch `2.8.0.dev20250325+cu128` (cuDNN 9.7.1.26, CUDA 12.8); after:
  torch `2.14.1+cu132` (cuDNN 9.24.0.43, CUDA 13.2; the `cu130` build gives the same
  outputs bit for bit). Measured pixel by pixel on the science harness's 40 Generate photos
  (every third of 120 real photos of the owner's trial), against the outputs of torch
  2.7.1+cu128, whose cuDNN 9.7.1 reproduces the published baseline bit for bit:
  - **Masks** (`mask/<stem>.png`, 640 x 480, 0 / 255): 11 of 40 differ, in 16 pixels in all.
    3 pixels in `24EFCAM1_T151_L043_2024.08.27_085149_003_SMM` (2 root pixels lost, 1
    gained: 2573 -> 2572 root pixels) and 4 in `24EFCAM2_T098_L052_2024.08.27_114928_003_SMM`
    (3 lost, 1 gained: 17833 -> 17831); 1 pixel in each of the other 9:
    `24EFCAM1_T010_L028_2024.07.18_103003_001_SMM` (861 -> 860),
    `24EFCAM1_T036_L076_2024.07.18_145933_001_SMM` (1095 -> 1094),
    `24EFCAM1_T055_L066_2024.08.26_094641_003_SMM` (798 -> 799),
    `24EFCAM1_T068_L040_2024.08.07_113400_002_SMM` (3857 -> 3856),
    `24EFCAM1_T100_L042_2024.07.19_162448_001_SCW` (2189 -> 2188),
    `24EFCAM1_T161_L069_2024.08.08_095605_002_SMM` (9482 -> 9483),
    `24EFCAM2_T006_L077_2024.08.07_104704_002_SMM` (8060 -> 8059),
    `24EFCAM2_T017_L032_2024.08.26_095103_003_SMM` (3121 -> 3120),
    `24EFCAM2_T088_L055_2024.08.27_084033_003_SMM` (2866 -> 2865).
  - **Raw skeleton predictions** (`skeletons/<stem>_fake.png`, 341 x 256, 3 channels): 6 of
    40 differ, in 72 pixels in all, each by 1 of 255 in one colour channel (in two or three
    channels for 3 of T006's pixels): `24EFCAM1_T068_L040_2024.08.07_113400_002_SMM` 5 pixels,
    `24EFCAM1_T100_L042_2024.07.19_162448_001_SCW` 8,
    `24EFCAM1_T161_L069_2024.08.08_095605_002_SMM` 15,
    `24EFCAM1_T175_L089_2024.08.08_111719_002_SMM` 1,
    `24EFCAM2_T006_L077_2024.08.07_104704_002_SMM` 18,
    `24EFCAM2_T098_L052_2024.08.27_114928_003_SMM` 25. Their thinned skeletons (what
    Measure reads), step counts and lengths are identical: `root_lengths.csv` is
    byte-identical.
  - **`root_areas.csv`**: 3 of 40 rows change by 0.01 mm² (default field of view; the
    cells as written): `24EFCAM1_T151_L043_2024.08.27_085149_003_SMM` `2.04` -> `2.03`,
    `24EFCAM2_T006_L077_2024.08.07_104704_002_SMM` `6.38` -> `6.37`,
    `24EFCAM2_T098_L052_2024.08.27_114928_003_SMM` `14.11` -> `14.1`. The other 8 changed
    masks keep their rounded areas.
  - 94 harness entries in all: those 17 output files, the 3 CSV rows, 44 estimator entries
    (the binarised hash, root pixels and two unrounded areas of each of the 11 masks) and 30
    size / SHA-256 entries of the CSVs' inputs records (19 of `root_areas.csv`, 11 of
    `root_lengths.csv`). Two runs give the same outputs (deterministic), and the harness's
    GPU baseline is re-recorded with this build (`science_manifest_py314.json`).

  Unchanged: Generate on the CPU (torch 2.14.1 reproduces the nightly's CPU outputs bit
  for bit), Measure on existing masks and skeletons (the harness's two fixed projects,
  GT-first decisions included), the saved Correct pixels (goldens) and every dashboard
  figure and Export CSV (the owner's 46,178-row CSVs and the harness CSVs). Python 3.14,
  numpy 2.5, scipy 1.18, scikit-image 0.26, OpenCV 5.0 and Pillow 13 change no output; the
  difference is the cuDNN of the PyTorch build (bisected on 2026-10-27: torch 2.7.0 and
  2.7.1 with cuDNN 9.7.1 are identical; 2.8.0 and 2.9.0 with cuDNN 9.10.2 already differ in
  6 masks and 5 skeleton predictions, no CSV value, counted from the harness's hashes, the
  pixels not diffed; `docs/MODELS.md`). GPU results made before and after this change can
  differ in these ways; generate both with the same build to compare them.
- **Redundancy and speed pass (2026-10-26): one corner case, otherwise none.** The pass's
  science harness found every generated mask and skeleton (GPU and CPU), every CSV row,
  provenance and inputs record and every GT-first decision identical to `f392a85` (80
  generated outputs, 6 CSVs with 572 rows, 357 estimator entries); the saved-pixel goldens,
  the GT export (an identity run over 2,136 images, case-sensitive folders included) and the
  dashboards' values and CSV exports are unchanged. The corner case (S01-06): on a
  case-sensitive disk, a mask prediction whose name differs from the image's only in case
  (`mask/IMG.png` for `img.png`) and that another program rewrote while *Correct* held it.
  A save of a plane equal to the prediction as opened now writes `gt/mask/<stem>.png`, as
  for an exactly named prediction, and Measure measures the plane saved (`Source` = `gt`);
  before, the save removed the GT file and Measure measured the rewritten prediction. One
  interaction change alters what the same keys can do: undo across layers reaches each
  layer's newest 100 actions (C4-01, under *Fixed*).
- **GT files of an image found as its own (2026-10-26, final review D1, C2).** Root length
  / area of an image whose stem ends in an image extension (`X.png.jpg`): a skeleton
  corrected in *Correct* is saved as that image's GT (`gt/skeletons/X.png.png`) and measured
  for it (`Source` = `gt`). Before, the correction was saved under `X.png`'s name: in a
  project that also holds `X.png` it replaced `X.png`'s GT skeleton (so `X.png`'s length
  came from the other image's correction), and alone it was skipped as "image gone" and
  the prediction was measured. On a case-sensitive disk, a skeleton correction of an image
  whose GT skeleton differs in case (`IMG.png`, `gt/skeletons/img.png`) is saved onto that
  file; before, *Correct* showed the prediction and saved a second file `IMG.png`, which
  Measure then measured (Measure's rule is unchanged). The dataset export includes images
  whose class labels differ in case from the image (`gt/img.png` for `IMG.png`) on
  case-sensitive disks; before they were skipped silently. CSV values change only for such
  images, and only after a new save: the byte-identity tests of `tests/test_measure_core.py`
  and the saved-pixel goldens are unchanged.
- **A GT mask of another size than its photo is not used (2026-10-26, final review S1,
  O1).** Measure area checks `gt/mask/<stem>.png` against its photo as Measure length
  checks `gt/skeletons/<stem>.png`: a mask whose size differs from the photo's is not used,
  the prediction `mask/<stem>.png` is measured (`Source` = `prediction`), the reason is
  `size` (`inputs.gt.skipped`, the end notice, the metrics bar), and an image with no
  prediction gets no row. A GT mask that cannot be read, or whose photo's size cannot be
  read, is `unreadable` the same way. *Correct* already refused such a mask ("Mask not
  shown"); the CSV no longer reports a file the editor does not show. Only GT masks of
  another size change, and the app never writes them (*Correct* saves at the photo's size),
  so no CSV value changes in a project saved by the app: the synthetic set (byte-identity
  tests of `tests/test_measure_core.py`) and `docs/examples` (regenerated with
  `scripts/make_examples.py`) are byte-identical. The provenance parameter `input` of an
  area CSV reads `gt/mask/<stem>.png where readable and the photo's size, else
  mask/<stem>.png`. An area CSV measured before this change keeps its values until Measure
  runs again: freshness compares the files, not the rule.
- **Length estimator (2026-10-25, W3-T8, O12).** Root length now uses the corner-count
  estimator of Vossepoel & Smeulders (1982), 0.980 n_e + 1.406 n_o − 0.091 n_c, on the
  m-adjacent skeleton (a junction's diagonal is no longer counted on top of its two
  orthogonal steps), with the per-axis pixel pitch. The former Freeman weights (1, √2) read
  +5.3 % long on synthetic roots (0 to +8.3 % by direction); the new estimator −0.1 % on
  average (−2.0 to +1.0 % by direction); a perfectly horizontal or vertical line reads
  2.0 % short. Every non-zero length decreases: on 3,000 real skeletons −5.24 % in total;
  for the 574 longer than 1 mm, median −5.1 %, 90 % of files between −7.3 % and −2.9 %,
  extremes −11.5 % and −1.9 % (unrounded). In the 2-dp CSVs, 654 of 3,000 length rows
  change and no value changes between zero and non-zero; areas are byte-identical.
  Synthetic set (6 images): lengths −6.94 % in total (−8.3 … −5.8 % per image). The
  goldens' GT lengths follow (29.44 → 27.97 and 61.84 → 58.87 mm; no pixel changes).
  Together with the field-of-view change (W3-T2): −3.33 % in total on the same sample. A
  length CSV with a provenance sidecar (Measure since 2026-09-28) is marked outdated
  (earlier length method; one note when the field of view differs too); an older CSV has
  no sidecar and is not marked.
- **One mask threshold (2026-10-25, W2-T6, M5, O10).** Correct reads a mask at Measure's
  threshold (grey > 127, was > 200), so the editor and Measure see the same root pixels.
  Saved GT pixels can change: a mask with grey levels 128 to 200 (legacy JPEG masks, files
  from other programs; Generate writes only 0 and 255) opens with those pixels as root, and
  a save after any edit writes them into `gt/mask/<stem>.png`. Measure already used 127 and
  now records `outputs.n_non_binary`, with a warning when masks have grey levels. No CSV
  value changes.
- **GT export manifest columns (2026-10-25, WG-T9, O24).** The GT dataset export's
  `manifest.csv` gains `exif_orientation` (the source image's EXIF tag 0x0112 as an integer
  1-8, blank when absent or unreadable; recorded only, the orientation is never applied)
  after `classes_present`, then `px_background`, `px_<key>` for each exported class in
  registry order, and `px_ignore` (pixel counts of the exported mask ids; a retired class
  counts under `px_ignore`). Exported masks, images and `classes.json` are unchanged; no
  measured value changes.
- **Field of view 18 × 13 mm → 18 × 13.5 mm (2026-10-25, W3-T2, O3).** The default is the
  Bartz BTC-100X / I-CAP frame at 640 × 480 px (Rajurkar et al. 2022, Plant Methods
  18:39); pixels are now square (0.028125 mm at 640 × 480). Exact effect per image: every
  root area × 1.038462 (+3.85 %); root lengths +0 to +3.85 % by step direction (vertical
  +3.85 %, diagonal +1.87 %, horizontal 0). 3,000 real skeletons, unrounded: +2.02 % in
  total; for the 571 longer than 1 mm, median +2.01 %, 5th…95th percentile +0.43…+3.59 %,
  max +3.80 %. In the 2-dp CSVs, 616 of 3,000 length rows and 1,294 of 3,000 area rows
  change, and no value changes between zero and non-zero; a small value can move by one
  0.01 rounding step more or less than its exact ratio. Synthetic set (6 + 6 images):
  lengths +1.39 % in total (+0.90…+1.95 % per image), areas +3.85 %. The goldens'
  measured values follow (areas 14.52 → 15.08 and 38.09 → 39.55 mm²; GT lengths 28.86 →
  29.44 and 60.83 → 61.84 mm; no pixel changes). A project with another camera states its
  field of view in `sprouts_project.json`. CSVs measured before are marked outdated until
  measured again.
- **Measure reads GT first; `Source` column (2026-10-25, WG-T8, O1).** Root length measures
  an image's GT skeleton (`gt/skeletons/<stem>.png`, saved in *Correct*) where it is valid,
  else the prediction `skeletons/<stem>_fake.png`; root area measures its GT mask
  (`gt/mask/<stem>.png`) where it can be read, else `mask/<stem>.png`. An image with only a
  GT file gets a row. A GT skeleton is not used when it is not the photo's size, equals the
  prediction as *Correct* opens it, or cannot be read (nor its photo's size); the end notice
  counts them and the provenance names them (`inputs.gt.skipped`). Both CSVs gain a last
  column `Source` (`gt` / `prediction`); `[5]` stays the value and `Error` stays at `[6]`.
  The provenance adds `parameters.source_rule` `gt-first`, `inputs.gt` and
  `outputs.n_gt` / `n_prediction`; the record of the inputs lists the file each value was
  measured from, and freshness follows it (a regenerated prediction under a GT row stays
  current; a GT file saved after the CSV makes the value outdated). Before / after (before:
  44bace1; both metrics):
  - Synthetic set (`test_measure_core`'s 8 images with photos, no `gt/`): 8 + 8 rows,
    first seven columns byte-identical, `Source` = `prediction` on every row.
  - The goldens' synthetic projects with their two skeleton corrections saved as GT through
    the Correct skeleton layer: `synth_model_mask_and_skeleton` length 42.34 mm
    (prediction) -> 60.83 mm (`gt`); `synth_blank_png` had no length row (no prediction)
    and now has one, 28.86 mm (`gt`), in a `skeletons/` folder created for it. Their area
    rows: unchanged, `prediction`. Nothing else changes.
  - The audit's 3000-pair real sample (no `gt/`): 3000 + 3000 rows, first seven columns
    byte-identical, `Source` = `prediction` on every row, no value changes.
- **A 1 px click paints its pixel (2026-10-25, W3-T5, O11).** A click with the 1 px brush
  paints (or erases) exactly the clicked pixel in *Correct* and in the GT classes; it
  painted nothing and still added an empty undo step. A 1 px stroke along an exact
  anti-diagonal also gains its pressed pixel. Sizes 2 and above, and every golden, are
  unchanged.
- **A prediction of another size opens stretched (2026-10-25, W3-T4, O8).** A prediction
  mask of another size than its photo (the model writes 640 x 480) opens in *Correct*
  stretched to the photo with nearest neighbour and says so, instead of not opening at all;
  **Load prediction** loads the stretched mask, and a save equal to it removes the GT, as
  for a same-size prediction. Nothing changes until such a mask is edited and saved; the
  saved GT mask is then at the photo's size. Its area against the prediction's: identical
  for integer factors (x2); on the audit's 17 masks, x3.2 total +0.013 % (worst image
  1.45 %), 16:9 +0.026 % (0.70 %), x1.25 +0.069 % (3.11 %). A GT mask of another size is
  still not shown and is kept. All 640 x 480 photos: no change.
- **Fixed-shape batches (2026-10-25, W3-T3, O4).** Generation runs every batch at one fixed
  size (masks 4, skeletons 64 on a GPU; 4 and 8 on the CPU), the last batch padded with zeros
  whose outputs are dropped; a GPU run checks free memory first (3.6 GiB for masks, 3.8 GiB
  for skeletons, after the model loads) and stops on out of memory instead of halving.
  `SPROUTS_BATCH_POLICY=auto` keeps the former behaviour. Measured on 300 real 640 x 480
  images (RTX 5070 Ti, `.claude/handoff/owner-decisions-2026-10-25/research/o4-gpu-batch.md`):
  at one batch shape every output is bit-identical whatever the composition, order, slot, zero
  padding or process. Masks at 4 against today's 16: 96 of 300 mask files differ (199 pixels,
  a few per image), 9 of 300 CSV areas change at 2 dp, by at most 0.003 mm² (median of the
  changed ones 0.026 %). Skeletons at 64: identical to 128 and to 32 (0 of 300 files); the
  auto size in a Generate run on this card was already 64. Today's unpadded mask tail (12
  images) against the padded one differed in 4 files / 5 pixels; that dependence on folder
  size is gone. CPU tails are now padded too (not measured). Masks run at N = 4 instead of
  the proposed 16 (a build decision from the GPU session, to be confirmed by the owner):
  masks at 16 need about 12 GiB free.
- **`Error` cells name the file only (2026-10-25, W3-T7, O38).** The `Error` cell of a row
  that could not be measured names the file, not its absolute path, so a shared CSV no
  longer carries user or folder names. Values and every other cell are unchanged.
- **Row order of tied rows (2026-10-25, WX-T9, O21).** Rows of `root_lengths.csv` /
  `root_areas.csv` that tie on tube, date, time and position are now ordered by image name;
  before, they kept the folder-scan order, which differs per OS. Library entries that tie are
  ordered by (depth, name). No value changes; on the owner's project (46,178 skeleton and
  46,175 mask names) and the synthetic set 0 rows move.
- **Inputs without an image (2026-10-25, W2-T11, O37).** Measure writes no row for a mask
  or skeleton whose image is no longer in the project (an image the Library lists: PNG or
  JPEG, any letter case; a mask whose image is `x.tif` counts as without an image); the
  provenance lists them (`inputs.n_without_image`, up to 100 names in
  `inputs.without_image`), the end notice counts them, and the files stay on disk. When
  several files share one image's name apart from letter case (a case-sensitive disk), only
  the one named exactly like the image (else the first by file name) is measured; the others
  are recorded in `inputs.duplicates` / `inputs.n_duplicate`. Owner's project (46,178
  photos): masks 0 of 46,175 and skeletons 0 of 46,178 rows dropped. Synthetic test set: 5 ->
  4 rows.
- **Case-variant stems and unsupported formats (2026-10-25, W1-T5).** Folders with
  case-variant stems (`A.png`, `a.jpg`): one image, ranked, on every platform; the hidden
  one gets no new mask, skeleton or CSV row (outputs already on disk are still measured).
  Folders with `.bmp` / `.tif` / `.tiff` / `.ppm` next to supported files: those get no new
  skeleton or `root_lengths.csv` row. Before / after on the audit folders: `r4_case` 3 -> 2
  written, the mask from `A.png` in 20 of 20 runs; `r3_formats` skeleton inputs 7 -> 3. The
  3000-pair real sample (JPEG only, no case-variant stems): identical.
- **Stem clash (2026-09-30).** When two image files in a project share a stem (`a.jpg` and
  `a.png`), Generate now uses only the image the Library shows (`.png` before `.jpg` before
  `.jpeg`) for `mask/a.png` and `skeletons/a_fake.png`. Before, both images were processed
  and their saves raced for the one file, so either image could win (over 30 runs one image
  won 29 times for masks and 27 times for skeletons), and the run counted 2 inputs written
  for 1 file. Folders without such a clash get the same inputs and outputs as before.
- **GT export size check (2026-09-30).** A tracing whose size differs from its image (the
  image was resized or replaced after tracing) is now an `errors.txt` entry and nothing is
  exported for it; it used to be exported as a misaligned image / mask pair.
- **Otherwise none from the UX and speed pass (2026-09-30).** Measure CSVs are
  byte-identical before and after (300 synthetic and 50 real images, and 626 edge-case
  files), saved mask and skeleton pixels are pinned by the goldens
  (`tests/data/correct_goldens.json`), GT files are byte-identical for the same edits, and
  the dashboards' CSV exports carry the same values. Interaction changes that alter what is
  saved for the same actions are listed under *Changed* and *Fixed*: Save finishes a pending
  polyline draft first; a skeleton loaded from a file that differs from the open one is
  unsaved, so Save or `PgDn` writes it as the correction; Trace GT *Discard* reverts.
- **Correction (2026-09-30): the batch size can change GPU outputs slightly.** BatchNorm
  runs in eval mode, but the kernel cuDNN picks depends on the batch shape, also with
  deterministic cuDNN. On 300 synthetic images, masks made with batch 16 and batch 8
  differed in 148 files (327 pixels flipped between 0 and 255); skeletons made with batch
  128 and batch 8 differed in 1 file (43 values by one grey level), with 128 and 1 in 208
  files (by at most 5 levels). Runs with the same batch sizes are reproducible; the
  halving after an out-of-memory error and the skeleton batch sized from free VRAM can
  change the outputs of the images they touch (`_sprouts_run.json` records `batch_size`
  and `effective_batch_size`). This section used to say that batch sizes do not change
  outputs.
  On the CPU, 8 real images gave identical masks at batch 4, 2 and 1; skeletons at batch 8
  and 1 differed in one value. A fixed batch per model is an owner decision
  (`docs/KNOWN_ISSUES.md` #7).
- **Skeleton corrections are saved at image resolution (Q1 fixed).**
  `skeleton_corrections/<stem>_skeleton.png` is now written at the photo's size, exactly
  as edited, instead of being shrunk back to the generated file's 341 x 256 with nearest
  neighbour. The shrink broke 1 px lines apart (an unedited save turned 7 components into
  48 and 12 into 109, and kept only 8-22 % of the skeleton's length); a saved correction
  now reopens pixel for pixel. Opening a generated `skeletons/<stem>_fake.png` (or an older
  small correction) upscales the grey file bilinearly before thresholding and thinning, not
  nearest neighbour: lines keep their connectivity (0 splits in 300 random files) and
  their length (a median 0.8 % longer than the generated file's on the real data, 90 % of
  285 files over 5 mm between -1.0 % and +3.9 %, extremes -3.4 % and +11.3 %, audit
  2026-10-12; was 2.5-4.2 %).
  Generated skeletons and Measure are unchanged, and corrections are still not measured
  (FR-03), so no CSV value changes. Corrections saved before this change stay 341 x 256
  and keep the fragments they lost. The skeleton pixel goldens were re-recorded for this
  change (`tests/data/correct_goldens.json`; the mask goldens are untouched).
- **Otherwise none from the Correct stage and the zoom work.** For the same edits, saved
  masks (`mask/<stem>.png`, RGBA; a model mask is never rewritten unless you save it) are
  pixel-identical to the editor they replace, and no CSV value changes. This is pinned by pixel goldens captured from the pre-merge editors
  (`tests/data/correct_goldens.json`, replayed per layer and through the Correct editor);
  three more cases from the real dataset are in the suite (docs/examples,
  tests/test_correct_goldens_real.py). Interaction changes that
  alter what a gesture paints (a tool, layer or visibility key pressed in the middle of a
  drag; a GT class key in the middle of a stroke) are listed under *Changed*.
- **Tube / Position** (`app/naming.py`, FR-15) come only from stand-alone `T<n>` /
  `L<n>` tokens of the file name. A leading field such as `PLOT12_` or `EXPT2_` is no
  longer read as the tube, and a name without separators (`T1L2_...`) now gives an
  empty Tube and Position. This changes the `Tube` / `Position` columns of both metric
  CSVs and the tube grouping (`group_by_tube`) of a GT dataset export split.
- **Non-ASCII paths** (for example `résumé_T1_L1.png`) are now read
  (`io_utils.imread_unicode`) and measured; they used to become an error row with
  value 0.
- **Deterministic GPU inference** (`app/inference/runtime.py`): cuDNN runs with
  `benchmark` off and `deterministic` on (set `SPROUTS_CUDNN_BENCHMARK=1` for the old
  autotuning). Generated masks and skeletons are now reproducible run to run on the same
  GPU at the same batch sizes (see the correction above), but they are numerically
  equivalent to 2.x/3.0 output, not bit-identical: on 128
  real images, 40 of 39.3 M mask pixels differed (area changed on 24/128 images by at
  most 0.00076 mm², 0.085 %) and 502 of 33.5 M skeleton channel values differed by one
  grey level with no pixel crossing the binarisation threshold (root length identical on
  128/128). 2.x/3.0 masks were not reproducible run to run on the GPU either (two
  baseline runs gave different masks).
- Batch sizes: see the correction of 2026-09-30 at the top of this section (the batch size
  can change GPU outputs slightly; BatchNorm runs in eval mode).

## [3.0.0] - not yet tagged

Version number set on 2026-05-29 (`9a17485`); no release or tag has been published yet.
Main changes since the untagged 2.x code: shared metric core and central configuration,
runtime GPU auto-tuning with CPU fallback, the SPROUTS guided interface, the reworked mask
and skeleton editors, multi-class ground-truth tracing with dataset export, and CI.

### Scientific output changes
- `7b326d7` (2026-05-29) **Root length**: the skeleton is measured as an 8-connected arc
  length (diagonal steps count as the diagonal pixel pitch, per-axis calibration) instead
  of a pixel count. On 300 real skeletons, new/old length has median 1.104 (5th-95th
  percentile 0.975-1.288). **Root area** is calibrated from the mask's actual resolution
  (a mask that is not 640x480 was previously off by the square of the resolution ratio).
- `aa59106` (2026-05-31) Metric CSVs gain the `Error` column (failed rows keep value 0;
  the reason is in `Error`). GPU mask and skeleton inference switched to mixed-precision
  autocast (bf16 where the GPU supports it).
- `bf1f898` (2026-06-01) GPU mask and skeleton inference returned to fp32 without
  autocast. In this release cuDNN TF32 convolutions and `cudnn.benchmark` stayed
  enabled, so its GPU results were not bit-reproducible run to run (fixed under
  [Unreleased]: deterministic cuDNN). Masks and skeletons generated on a GPU between
  2026-05-31 and 2026-06-01 used mixed precision.
