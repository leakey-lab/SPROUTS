# SPROUTS (Root-Mask-and-Skeletons)

Desktop application (PyQt6 + PyTorch) for **plant-root phenotyping** from
minirhizotron images: model-predicted root masks and skeletons, editors to correct
them, calibrated root area (mm²) and root length (mm) exported as CSV, interactive
Dash/Plotly dashboards, and multi-class ground-truth tracing for training datasets.

## Pipeline

Two independent branches start from the **original images**:

```
image → mask model (ResNet)       → mask/<stem>.png           → Correct → gt/mask/<stem>.png      → root area (mm²)   → mask/root_areas.csv
image → skeleton model (Pix2Pix)  → skeletons/<stem>_fake.png → Correct → gt/skeletons/<stem>.png → root length (mm)  → skeletons/root_lengths.csv
root_areas.csv + root_lengths.csv → Dash/Plotly dashboards
```

The skeleton model reads the original image, not the mask, so correcting a mask changes
**root area only** and correcting a skeleton **root length only**. Edits saved in *Correct*
are GT (`gt/mask/<stem>.png`, `gt/skeletons/<stem>.png`), and Measure reads GT first: an
image's GT file where it is valid, else its prediction (`mask/<stem>.png`,
`skeletons/<stem>_fake.png`). The last CSV column, `Source`, says which (`gt` /
`prediction`). The Library's Overlay and Side by side views draw the GT skeleton, and the
status reads *Skeleton: GT*.
The models are described in [`docs/MODELS.md`](docs/MODELS.md), every output file and
column in [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).

## Requirements

- Windows 10/11, Linux x86_64 (glibc 2.34 or newer: Ubuntu 22.04+, Debian 12+) or macOS 13+
  on Apple silicon only; Intel Macs are not supported (PyTorch publishes no Intel macOS
  wheels). Python 3.10.
- An NVIDIA GPU is used on Windows and Linux (CUDA 12.8 build); CPU works everywhere and is
  the only mode on macOS, but is slow.

## Installation

PyTorch is installed **first and separately**, from the PyTorch index that matches your
GPU driver, so pip can never silently replace it with a CPU-only wheel:

```powershell
py -3.10 -m venv venv
.\venv\Scripts\activate

# 1. PyTorch: CUDA 12.8 build (needed for RTX 50-series GPUs; driver 570 or newer)
pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
#    Other drivers: the same versions from .../whl/cu126 or .../whl/cu129
#    No NVIDIA GPU:  the same versions from .../whl/cpu

# 2. Application dependencies (the exact versions the app is tested with)
pip install -r requirements.txt

# 3. Optional: development and test tooling
pip install -r requirements-dev.txt
```

The runtime environment needs neither `pipreqs` nor `ipython`; if an older environment has
them, remove them (`pip uninstall -y pipreqs ipython`): IPython adds about 250 ms to the
first dashboard import. An older environment may also hold `opencv-python`: uninstall it
before installing the requirements (`pip uninstall -y opencv-python`), because it and
`opencv-python-headless` write the same `cv2` folder.

Results published so far were produced with a PyTorch nightly
(`torch 2.8.0.dev20250325+cu128`) that is no longer downloadable; torch 2.8.0 above is
the matching stable release. GPU results can differ from another build or from CPU in
the last decimal of the CSVs (see `docs/MODELS.md`).

### Installation (Linux / macOS)

Same order: PyTorch first, then the application dependencies.

```bash
# 0. Python 3.10 and a virtual environment, by system:
#    Ubuntu 22.04:         sudo apt install python3.10 python3.10-venv
#                          python3.10 -m venv venv
#    Ubuntu 24.04 / Debian 12 (no python3.10 package): with uv (https://docs.astral.sh/uv/)
#                          uv python install 3.10 && uv venv -p 3.10 venv
#    macOS (Homebrew):     brew install python@3.10
#                          python3.10 -m venv venv
source venv/bin/activate

# 1. PyTorch, one of:
#    Linux + NVIDIA GPU (CUDA 12.8 build; driver 570 or newer):
pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
#    Linux, CPU only (without --index-url pip pulls about 3 GB of CUDA wheels):
pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu
#    macOS (Apple silicon, CPU):
pip install torch==2.8.0 torchvision==0.23.0

# 2. Application dependencies, then optionally the development tooling
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3. Linux only: system libraries (Debian / Ubuntu package names)
sudo apt install libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
     libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
     libdbus-1-3 libnss3 libfontconfig1
#    Minimal / server images also need these for the embedded browser (QtWebEngine):
#    sudo apt install libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libasound2

python main.py
```

Troubleshooting: as root or in a container the app sets `QTWEBENGINE_DISABLE_SANDBOX=1`
itself; on Ubuntu 24.04 with restricted user namespaces
(`kernel.apparmor_restrict_unprivileged_userns=1`) set it yourself before starting the app.

### Model weights

Place the two checkpoints under `checkpoints/`:

```
checkpoints/mask_weights/best_mask_model_V5.pth   (mask model)
checkpoints/skeletonizer/latest_net_G.pth         (skeleton model)
```

The weights are not stored in this repository. They are published as a Zenodo record
(licence CC-BY-4.0); the link and DOI are in [`docs/MODELS.md`](docs/MODELS.md) once the
record exists, and until then you can get them from the maintainers. With the record's
address registered, `./venv/Scripts/python.exe scripts/fetch_models.py` downloads both files
into `checkpoints/` and checks their SHA-256 (the app also warns in its log when a
checkpoint does not match). The skeleton model of earlier versions is still in the git
history: `git restore --source=2075568 -- checkpoints.zip`.

To keep the weights somewhere else (required for a `pip install .` copy, whose
application folder is `site-packages`), point `SPROUTS_CHECKPOINTS_DIR` at the folder
that holds `mask_weights/` and `skeletonizer/`:

```powershell
$env:SPROUTS_CHECKPOINTS_DIR = 'D:\models\sprouts-checkpoints'
```

## Running

```powershell
python main.py
```

It works from any folder (`python D:\path\to\main.py`). To get a `sprouts` command,
install the checkout in editable mode, which keeps using this folder's `checkpoints/`:

```powershell
pip install -e .
sprouts
```

Logs go to `app.log` in `%LOCALAPPDATA%\SPROUTS\Logs` (Windows),
`~/.local/state/sprouts/log` (Linux) or `~/Library/Logs/SPROUTS` (macOS); set
`SPROUTS_LOG_DIR` to use another folder. A native crash writes the stacks of every thread
to `fault.log` next to it (the file also records handled Windows exceptions such as `0x8001010d`, so an entry alone is not a sign of a crash); an unexpected Python error is logged and shown in a dialog
instead of closing the app.

Set `ROOT_VIEWER_ENABLE_OPENGL_VIEWPORT=0` to draw the canvases without OpenGL; About (row
`Canvas`) and the device chip's tooltip say which path is active, and a warning appears at
start-up when OpenGL is unavailable and you did not turn it off.

The status line names the GPU (or CPU) it will use without waiting for a first run; the
check asks the NVIDIA driver directly, so it does not load PyTorch. The heavy libraries
load when you head for the stage that needs them (entering Generate loads PyTorch;
entering Measure or pointing at Visualize loads the dashboards). Set
`SPROUTS_DEVICE_CHECK=0` (or the setting `device_check/at_startup` to `false`) to turn it
off; the status line then reads "Device not detected yet" until the first run.

## Stages

The top bar walks through the work in six stages, in this order (`F1` lists every key):

| Stage | Key | What it does |
|---|---|---|
| **Library** | `Alt+1` | Browse and select minirhizotron images from the tree. |
| **Trace GT** | `Alt+2` | Paint ground-truth classes for a segmentation dataset (see below). |
| **Generate** | `Alt+3` | Generate masks and skeletons for all loaded images in one pass. |
| **Correct** | `Alt+4` | Fix the mask (area) and the skeleton (length) of an image on one canvas. |
| **Measure** | `Alt+5` | Compute root length (mm) and area (mm²), from each image's GT file where it is valid, else its prediction. |
| **Visualize** | `Alt+6` | Explore length and area trends across the trial. |

A stage's keys work wherever the keyboard is while the stage is on screen, also right
after a click in the library tree. In the Library, `Ctrl+0`, `Ctrl+1` and `Ctrl+=` /
`Ctrl+-` fit the photo, show it at 100 % and zoom. *Visualize* opens the dashboard you
chose last (root length at first); with nothing to chart yet it says what to run first.

Each Library row shows a badge of three marks: mask (green), skeleton (orange) and class
labels (purple). A ring means none, a filled circle a model prediction, a filled diamond
your GT (`gt/mask/`, `gt/skeletons/`, `gt/<stem>.png`); the class-label mark is a ring or
a diamond. Hover a row for the words, e.g. `Mask: GT · Skeleton: prediction · No classes`.

## Outputs

| File | Content |
|---|---|
| `mask/<stem>.png` | predicted root mask (grayscale 0/255, 640 x 480); *Correct* never changes it |
| `mask/root_areas.csv` | `Image, Tube, Position, Date, Time, Area (mm²), Error, Source` |
| `skeletons/<stem>_fake.png` | predicted skeleton (RGB, 341 x 256) |
| `skeletons/root_lengths.csv` | `Image, Tube, Position, Date, Time, Length (mm), Error, Source` |
| `gt/skeletons/<stem>.png` | skeleton saved in *Correct* after an edit: the GT skeleton (8-bit grey, 1-pixel lines at the photo's resolution, so a saved skeleton reopens unchanged); Measure reads it first where it is valid |
| `gt/mask/<stem>.png` | root mask saved in *Correct* after an edit: the GT mask (white on transparent, at the photo's size); Measure reads it first |
| `gt/<stem>.png`, `gt/classes.json` | multi-class ground truth (see below) |
| `<csv>.provenance.json` | how a CSV was made: app version, git commit, calibration, runtime, input digest, error and zero counts |
| `<csv>.inputs.json` | the files a CSV was measured from: each one's size, time and SHA-256, and the CSV's SHA-256 (what "changed since measured" compares) |
| `mask/_sprouts_run.json`, `skeletons/_sprouts_run.json` | the latest generation run into that folder: model SHA-256, device and precision, parameters, failed inputs |
| `mask/_sprouts_runs.jsonl`, `skeletons/_sprouts_runs.jsonl` | one line per generation run that wrote or failed an input (the run file holds the latest) |

Generating masks into a folder that already has predicted masks asks first: keep the
predictions and predict only the missing ones (the default), or replace every prediction.
Hand-corrected masks live in `gt/` and are never read or touched by Generate. On a GPU, outputs
are reproducible run to run (cuDNN autotuning is off, deterministic algorithms on, batches
at one fixed size, see below). They are numerically
equivalent to earlier versions, not bit-identical: root length is unchanged and area moves
by under 0.1 % (see `CHANGELOG.md`). On
Ampere and newer GPUs convolutions use TF32, recorded as `precision` in the run file.
Set `SPROUTS_CUDNN_BENCHMARK=1` to allow cuDNN autotuning (faster on some GPUs, not
bit-reproducible).

Batches run at one fixed size (masks 4, skeletons 64 on a GPU; 4 and 8 on the CPU), the
last one padded with blank images whose outputs are dropped, so a mask or skeleton does not
depend on how many images were in the folder or how much GPU memory was free. Before the
first batch a GPU run checks free GPU memory (3.6 GiB for masks, 3.8 GiB for skeletons,
after the model has loaded) and stops with a message if there is less, because under memory
pressure the GPU can silently switch to other kernels and change a few pixels. If the GPU
runs out of memory at the fixed size, the run stops with a message (no smaller retry).
`SPROUTS_BATCH_POLICY=auto` restores the earlier behaviour (masks in batches of 16, skeleton
batch sized to free memory, batches halved on out of memory; a few pixels can then differ).
Outputs are reproducible on one machine and software stack, not across GPUs, drivers or
PyTorch / cuDNN versions.

Tube, position, date and time come from the image file name,
`<field>_T<tube>_L<position>_<YYYY.MM.DD>_<HHMMSS>_...`. A row that could not be measured
has value `0` **and** a non-empty `Error`; a genuine zero has an empty `Error`, so filter
on `Error` before analysis. The CSVs are UTF-8: in Excel use *Data > From Text/CSV* with
UTF-8, otherwise `mm²` shows as `mmÂ²`. Details: [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).
Measure skips a mask or skeleton whose image is no longer in the project (an image the
Library lists: PNG or JPEG) and says how many in the end notice and in the CSV's provenance
file; the files stay on disk. Measure reads an image's GT file first. A GT skeleton of another
size than the photo, one equal to the prediction as *Correct* opens it, or one that cannot be
read (nor its photo's size) is not used: the prediction is measured, the end notice says how
many, the CSV's provenance file names them, and the metrics bar's tooltip says why. A GT mask
is used whenever it can be read.

## Calibration & measurement notes

Metrics are derived from the camera's physical field of view (default
**18 mm × 13 mm**, see `app/config.py::Calibration`) mapped onto the **actual**
image/mask resolution, so they are resolution-independent:

- **Area** = root pixels × (FOV_w/width) × (FOV_h/height). A fully-root mask
  measures the whole FOV (234 mm²) at any resolution.
- **Length** uses a true arc-length estimator: orthogonal pixel steps cost one
  pixel pitch, diagonal steps cost the Euclidean diagonal (per-axis pitch).

Every image is taken to cover the whole field of view, whatever its size, aspect or crop:
a quarter-covered mask of a 640 × 480 frame, of a 320 × 240 crop and of a 1920 × 1080
frame all measure the same area. The pixel is not square (0.028125 × 0.027083 mm for
640 × 480).

**Orientation.** Every stage uses the image's stored pixel grid; EXIF orientation is never
applied. A photo with an orientation tag is shown as stored (not as a photo viewer shows
it), and its mask, skeleton and GT line up with it. The field of view belongs to the
sensor's stored axes, so this is also the calibrated orientation.

If your imaging system has a different FOV, change it in one place:
`app/config.py`.

## Correcting masks and skeletons (Correct)

The **Correct** stage fixes both model outputs of an image on one canvas. The mask (root
area) and the skeleton (root length) are drawn together over the photo and you edit one
of them at a time, the *active layer*; the other stays visible at half its opacity as a
reference. The **Layers** panel on the right shows each layer's state (GT, Prediction,
Unsaved, Not saved …): click a row to edit that layer, click its eye to hide it (or reach
them with Tab: Space or Enter picks a layer, Space toggles an eye). Opacity and visibility
are kept across restarts.

- **Mask** (green): Brush, Eraser and Fill (fills a closed outline). It opens the saved
  GT mask `gt/mask/<stem>.png` if there is one, else the model's prediction
  `mask/<stem>.png`, and saves only to `gt/mask/<stem>.png` (white on transparent); the
  prediction is never changed. Saving an edited prediction makes it GT; saving an empty
  (cleared) mask writes an empty GT mask; a mask edited back to equal its prediction
  removes the GT file. **Load prediction** (dock, chip icon) puts the prediction back as
  one undoable step.
- **Skeleton** (orange): Select (pick a branch; Delete removes it), Eraser, Polyline
  (click points, drag them to adjust, Ctrl+click adds a bend, Shift+drag moves the line,
  Enter or a double-click finishes it, S smooths it) and Connect (drag from an endpoint
  to another point). It opens the GT skeleton `gt/skeletons/<stem>.png` if there is one,
  else the model's prediction `skeletons/<stem>_fake.png`, and saves only to
  `gt/skeletons/<stem>.png`; the prediction is never changed. A GT skeleton of another size
  opens resampled to the photo and is saved at the photo's size. **Load prediction** (dock,
  chip icon) puts the prediction back as one undoable step. **Load skeleton file…** in the
  dock opens another file; if it differs from the skeleton shown, it counts as unsaved.
  Measure reads the GT skeleton first where it is valid.
- **Enhance** (the contrast button on the tool rail) changes how the photo is shown, for
  every image while it is on; it never changes a mask, a skeleton or a measurement. For
  `.jpg` / `.jpeg` photos, **Smooth JPEG blocks** (off by default) softens the 8 x 8
  compression blocks you see when zoomed in; like the rest of Enhance it is display only.

| Key | Action |
|---|---|
| `1` / `2` / `X` | Edit the mask / the skeleton / switch between them |
| `B` (tap) · hold `B` and scroll | Mask brush · brush size (from either layer; it switches to the brush) |
| `F` | Mask fill |
| `V` · `P` · `C` | Skeleton select · polyline · connect |
| `E` | Eraser of the active layer |
| `[` / `]` | Smaller / larger brush or eraser |
| `H` / `Shift+H` | Show or hide both layers (see the photo) / the other layer |
| `S` · `Enter` · `Esc` | Smooth · finish · cancel the polyline |
| `Delete` / `Backspace` | Delete the picked branch |
| `Space` (hold), middle-drag | Pan |
| `Ctrl+Z` · `Ctrl+Y` / `Ctrl+Shift+Z` | Undo · redo |
| `Ctrl+S` | Save |
| `Ctrl+0` · `Ctrl+1` · `Ctrl+=` / `Ctrl+-` · `Ctrl`+wheel | Fit · 100 % · zoom in / out · zoom about the cursor |
| `Ctrl+'` | Show or hide the pixel grid (drawn from 800 %; also in Trace GT) |
| `PgDn` / `PgUp` | Save, then open the next / previous image |

A tool key of the other layer switches layers too (`B`, `F` → mask; `V`, `P`, `C` →
skeleton).

**Undo** is one timeline for both layers: `Ctrl+Z` undoes the last thing you did, on
either layer, without switching layers (the other layer's row flashes and a message says
"Undo · mask" or "Undo · skeleton"). While you place a polyline, `Ctrl+Z` first removes
its last point.

**Saving**

- `Ctrl+S` (or **Save**) saves every layer with unsaved changes, the mask first. With
  nothing unsaved it writes nothing and says *Nothing unsaved*. A
  skeleton is saved only when it differs from the prediction: saving is not acceptance, and
  a skeleton edited back to the prediction (or **Load prediction** and Save) removes its GT
  file `gt/skeletons/<stem>.png`.
- `PgDn` / `PgUp` save the unsaved layers without asking, then open the next / previous
  image. If a save fails, you stay on the image and a message says why. An unfinished
  polyline is never saved by `PgDn` / `PgUp`: with two or more points they stay until you
  finish it (Enter) or cancel it (Esc), and bring the skeleton forward if it was hidden.
- A saved GT mask that *Correct* cannot show (another size than the photo, or a file it
  cannot read) shows *Mask not shown* and is kept: `Ctrl+S` with nothing painted writes
  nothing, painting over it asks before replacing it, and `PgDn` stays on the image. A
  prediction that cannot be shown is never replaced: a save writes `gt/mask/`. A
  prediction of another size than the photo (Generate keeps the model's 640 x 480) opens
  stretched to the photo with nearest neighbour and says so; saving an edit stores it at
  the photo's size.
- If the file a layer saves to changed on disk after you opened it (another program, or a
  *Generate* run), **Save** asks first: **Overwrite**, **Reload (lose my edits)** or
  **Cancel**. A file rewritten with the same pixels asks nothing, and a layer without edits
  reloads when the image is opened again. `PgDn` / `PgUp` never ask: they stay on the image and say why. An image
  open in *Correct* without unsaved edits shows the new results when a *Generate* run
  finishes.
- A polyline you are placing (two or more points) counts as unsaved: **Save** and
  `Ctrl+S` finish it first.
- Every other way of leaving an image with unsaved changes (another image, another stage,
  opening a folder, closing the window) asks once and names the unsaved layers:
  **Save**, **Discard** (the unsaved layers go back to how they were opened or last
  saved; a layer whose file changed on disk meanwhile is read again from disk) or
  **Cancel** (stay).
  If a save fails, a second question offers **Discard** or **Keep editing**.
- Coming back to the same image keeps your zoom and position; the layers reload only when
  their files changed on disk (for example after *Generate*).

**Zoom:** the photo stays smooth at every zoom (no pixel squares when zoomed in, no
shimmer when zoomed out), the mask shows its exact saved pixels from 100 % up, and the
skeleton is drawn as a line at every zoom. The zoom buttons and keys step through round
percentages with a short glide, the wheel zooms continuously, and a right-click on the zoom
readout offers presets (Fit, 50 % … 1600 %). Zooming out stops at half the fitted zoom or
at 100 %, whichever is smaller.

## Ground-truth tracing (Trace GT)

The **Trace GT** stage paints hand-traced, multi-class ground truth for training
segmentation models (for example, roots vs nodules). It is independent of every
model in the app: it never starts from the ResNet mask and never reads or writes
`mask/` or `skeletons/`. It traces classes; it does not measure them.

**Files** (next to the images, in the loaded folder):

- `gt/<stem>.png` — the tracing: an 8-bit palette PNG where each pixel is one
  class id (`0` background, `1`–`254` classes, `255` Ignore). Any image viewer
  shows it in the class colours. A text chunk records the class list and which
  classes were checked on that image.
- `gt/classes.json` — the project's classes (id, key, name, colour). Defaults:
  Root, Nodule, Root tip, Ignore. Edit them with **Classes…**; a deleted class is
  retired (its id is never reused). If the file is edited by hand and a field has
  the wrong type, the app names the class and field and turns GT saving off until
  it is fixed.

A saved file means the image was annotated, even when nothing is painted
("checked, nothing present" is a valid negative example).

**Editor:** the same brush / eraser / fill canvas as the mask in Correct, plus a class
panel on the right. The brush paints the active class and replaces whatever was
under it (one class per pixel). Each class has a ✓ box: it ticks itself when the
class has pixels, and you tick it by hand for "looked for it, none present" (with the
mouse, or reach the boxes with Tab and press Space).

| Key | Action |
|---|---|
| `1`–`9` (top row or keypad) | select the n-th class |
| `0` | select Ignore |
| `PgDn` / `PgUp` | save and open the next / previous image (asks first if the file's classes had to be remapped on load) |
| `B` `E` `F`, `[` `]`, Space, `Ctrl+S`, `Ctrl+Z`/`Ctrl+Y` | as for the mask in Correct |
| `H` | show or hide the tracing |

**Export dataset…** writes a training set to an empty folder:

```
images/<stem>.<ext>            copied originals
masks/<stem>.png               class-id PNG: background 0, classes 1..N, Ignore 255
classes.json                   id → name/colour, ignore_index 255
splits/train.txt, splits/val.txt
manifest.csv                   stem, source image, split, tube, position, date, classes present
manifest.csv.provenance.json   how it was made: options (split seed), counts, class registry, app version
```

By default only images where every class is checked are exported, and all images
of one tube stay in the same split (so the same root on different dates cannot
leak between train and validation). The split is reproducible from the seed recorded
in `manifest.csv.provenance.json`. Images that fail to export (for example a tracing
whose size no longer matches its image) are listed in the dialog and in `errors.txt`; the
rest still export. A relative output folder is created next to the images.

If a GT save fails (for example the file is locked by another program), your edits
are kept: the prompt offers **Keep editing** instead of silently moving on. **Discard**
puts the tracing back as it was last saved (or opened).

## Building the Windows app

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1     # or: scripts\build.bat
```

This runs PyInstaller on `scripts/build_executable.spec` and produces
`dist\SPROUTS\SPROUTS.exe` (zip the whole `dist\SPROUTS` folder to distribute; with CUDA
PyTorch it is about 6 GB). Run `scripts/fetch_models.py` first. The scripts work from any
folder, stop if the model weights are missing (pass `-AllowMissingModels` /
`/allow-missing-models` to build anyway) and exit with an error when the build fails.
The app records its version and git commit in `_internal\build_info.json`.

The bundle is distributed under the GPL: ship `LICENSE` and `THIRD_PARTY_NOTICES.md` (the
build copies both into the bundle root) and, on request, the source of the commit recorded
in `_internal\build_info.json`. A CPU-only PyTorch (README "Installation") makes a much
smaller bundle.

## Tests

```bash
QT_QPA_PLATFORM=offscreen pytest          # Linux/macOS
$env:QT_QPA_PLATFORM='offscreen'; pytest  # Windows PowerShell
```

The suite pins the scientific calibration (length/area), skeleton vectorization,
CSV schema, and imports of every app module. CI runs it on Windows, Linux and macOS, builds
the Python wheel and validates `CITATION.cff`.

## How to cite

Use GitHub's **Cite this repository** button (generated from
[`CITATION.cff`](CITATION.cff)), or cite: Parth, K., Varela, S., & Leakey, A. D. B.
*SPROUTS*, version 3.0.0, https://github.com/leakey-lab/Root-Mask-and-Skeletons. Please
also state the model checkpoints you used (their SHA-256 is in
[`docs/MODELS.md`](docs/MODELS.md)). A DOI will be added when the first release is archived.

## License

SPROUTS is free software under the **GNU General Public License v3.0 or later**
(`GPL-3.0-or-later`, see [`LICENSE`](LICENSE)); PyQt6, which it imports, is GPL-3.0 too.
Copyright (c) 2026 the SPROUTS authors. Third-party code and assets keep their licences:
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and
[`app/assets/THIRD_PARTY.md`](app/assets/THIRD_PARTY.md). The **model weights** are not
part of the source and are licensed separately under **CC-BY-4.0**
([`docs/MODELS.md`](docs/MODELS.md)).

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md). Changes that alter measured values are listed under
*Scientific output changes* for every release.

## Project layout

```
app/
  __init__.py          # version and product name (single source)
  config.py            # single source of calibration, ports, paths, batch sizes
  gt/                  # ground-truth tracing core: class registry, GT PNG store, export
  inference/           # metrics (length/area), skeleton & mask inference
  handlers/            # batch orchestration (QThread)
  gui/                 # PyQt6 widgets, editors, graphics views
    correct/           # the Correct stage: mask and skeleton layers, session, input routing
  visualization/       # Dash/Plotly apps + data processing
  mask_model/          # ResNet segmentation model
docs/                  # model card, data dictionary, architecture, known issues
scripts/               # Windows build (PyInstaller spec, build.ps1 / build.bat)
main.py                # entry point
tests/                 # regression, import-smoke, GUI and FAIR-metadata tests
```
