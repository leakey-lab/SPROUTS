# Changelog

All notable changes to SPROUTS (Root-Mask-and-Skeletons) are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/); the version lives in `app/__init__.py`.

Every release has a **Scientific output changes** section: anything that can change a
value in `root_lengths.csv` / `root_areas.csv`, a mask or skeleton pixel, or a
ground-truth export. Record it there even when the change is a bug fix, so results
produced before and after the change can be told apart.

## [Unreleased]

Five passes, not yet released:

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
  - One mask threshold (W2-T6, M5, O10): Correct reads a mask at Measure's threshold (grey
    > 127, was > 200), so the editor and Measure see the same root pixels. Only masks with
    grey levels (legacy JPEG masks, files from other programs) open differently; saving one
    in Correct then writes those pixels as root. Measure records `outputs.n_non_binary` and
    warns when masks have grey levels. No CSV value changes.
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
- **Owner-decisions build (2026-10-25).** The Trace GT editor (WG-T11):
  `GtTracingInterface` (`app/gui/gt_tracing_interface.py`) and its base
  `RasterEditorBase` (`app/gui/raster_editor_base.py`), `MainWindow.gt_tracing_interface` /
  `toggle_gt_tracing`, `Panel.GT_TRACING` (the key `"gt_tracing"` and index 3 are
  rejected, no alias), the shortcut scope `"Trace GT"` (`shortcuts.TRACE_GT`), and their
  tests (ported to `tests/test_correct_classes_*.py` / `tests/test_correct_tab_order.py`;
  the teardown guards to `tests/test_correct_editor_teardown.py`). `ClassPanel`, the class
  editor, the dataset export and `app/gt/` stay. Also retired: `skeleton_corrections/`
  (WG-T3; corrections are GT in `gt/skeletons/`).
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
