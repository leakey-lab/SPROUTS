# SPROUTS (Root-Mask-and-Skeletons)

Desktop application (PyQt6 + PyTorch) for **plant-root phenotyping** from
minirhizotron images: model-predicted root masks and skeletons, editors to correct
them, calibrated root area (mm²) and root length (mm) exported as CSV, interactive
Dash/Plotly dashboards, and multi-class ground-truth tracing for training datasets.

## Pipeline

Two independent branches start from the **original images**:

```
image → mask model (ResNet)       → mask/<stem>.png           → Correct Mask → root area (mm²)   → mask/root_areas.csv
image → skeleton model (Pix2Pix)  → skeletons/<stem>_fake.png                → root length (mm)  → skeletons/root_lengths.csv
root_areas.csv + root_lengths.csv → Dash/Plotly dashboards
```

The skeleton model reads the original image, not the mask, so correcting a mask changes
**root area only**. Root length is measured from `skeletons/*_fake.png`; skeletons edited
in *Correct Skeleton* are saved to `skeleton_corrections/` and are not measured.
The models are described in [`docs/MODELS.md`](docs/MODELS.md), every output file and
column in [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).

## Requirements

- Windows 10/11 (the app is developed and shipped for Windows; the test suite also runs
  on Linux), Python 3.10.
- An NVIDIA GPU is recommended for inference; CPU works but is slow.

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

Results published so far were produced with a PyTorch nightly
(`torch 2.8.0.dev20250325+cu128`) that is no longer downloadable; torch 2.8.0 above is
the matching stable release. GPU results can differ from another build or from CPU in
the last decimal of the CSVs (see `docs/MODELS.md`).

### Model weights

Place the two checkpoints under `checkpoints/`:

```
checkpoints/mask_weights/best_mask_model_V5.pth   (mask model)
checkpoints/skeletonizer/latest_net_G.pth         (skeleton model)
```

The repository tracks `checkpoints.zip`, which contains the **skeleton** model only:
extract it here to get `checkpoints/skeletonizer/latest_net_G.pth`. The **mask** model
is not published yet; ask the maintainers. Check that you have the right files with the
SHA-256 digests in [`docs/MODELS.md`](docs/MODELS.md); the app warns in its log when a
checkpoint does not match.

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

Logs go to `%LOCALAPPDATA%\SPROUTS\Logs\app.log` (set `SPROUTS_LOG_DIR` to use another
folder). A native crash writes the stacks of every thread to `fault.log` next to it; an
unexpected Python error is logged and shown in a dialog instead of closing the app.

## Outputs

| File | Content |
|---|---|
| `mask/<stem>.png` | root mask: model-generated (grayscale 0/255, 640 x 480) or hand-traced (white on transparent) |
| `mask/root_areas.csv` | `Image, Tube, Position, Date, Time, Area (mm²), Error` |
| `skeletons/<stem>_fake.png` | predicted skeleton (RGB, 341 x 256) |
| `skeletons/root_lengths.csv` | `Image, Tube, Position, Date, Time, Length (mm), Error` |
| `gt/<stem>.png`, `gt/classes.json` | multi-class ground truth (see below) |
| `<csv>.provenance.json` | how a CSV was made: app version, git commit, calibration, runtime, input digest, error and zero counts |
| `mask/_sprouts_run.json`, `skeletons/_sprouts_run.json` | the latest generation run into that folder: model SHA-256, device and precision, parameters, failed inputs |

Generating masks into a folder that already has masks asks first: keep them and
generate only the missing ones (the default), or replace every mask. On a GPU, outputs
are reproducible run to run (cuDNN autotuning is off, deterministic algorithms on). They
are numerically equivalent to earlier versions, not bit-identical: root length is
unchanged and area moves by under 0.1 % (see `CHANGELOG.md`). On
Ampere and newer GPUs convolutions use TF32, recorded as `precision` in the run file.
Set `SPROUTS_CUDNN_BENCHMARK=1` to allow cuDNN autotuning (faster on some GPUs, not
bit-reproducible).

Tube, position, date and time come from the image file name,
`<field>_T<tube>_L<position>_<YYYY.MM.DD>_<HHMMSS>_...`. A row that could not be measured
has value `0` **and** a non-empty `Error`; a genuine zero has an empty `Error`, so filter
on `Error` before analysis. The CSVs are UTF-8: in Excel use *Data > From Text/CSV* with
UTF-8, otherwise `mm²` shows as `mmÂ²`. Details: [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).

## Calibration & measurement notes

Metrics are derived from the camera's physical field of view (default
**18 mm × 13 mm**, see `app/config.py::Calibration`) mapped onto the **actual**
image/mask resolution, so they are resolution-independent:

- **Area** = root pixels × (FOV_w/width) × (FOV_h/height). A fully-root mask
  measures the whole FOV (234 mm²) at any resolution.
- **Length** uses a true arc-length estimator: orthogonal pixel steps cost one
  pixel pitch, diagonal steps cost the Euclidean diagonal (per-axis pitch).

If your imaging system has a different FOV, change it in one place:
`app/config.py`.

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

**Editor:** the same brush / eraser / fill canvas as Correct Mask, plus a class
panel on the right. The brush paints the active class and replaces whatever was
under it (one class per pixel). Each class has a ✓ box: it ticks itself when the
class has pixels, and you tick it by hand for "looked for it, none present".

| Key | Action |
|---|---|
| `1`–`9` (top row or keypad) | select the n-th class |
| `0` | select Ignore |
| `PgDn` / `PgUp` | save and open the next / previous image (asks first if the file's classes had to be remapped on load) |
| `B` `E` `F`, `[` `]`, Space, `H`, `Ctrl+S`, `Ctrl+Z`/`Ctrl+Y` | as in Correct Mask |

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
in `manifest.csv.provenance.json`. Images that fail to export are listed in the
dialog and in `errors.txt`; the rest still export.

If a GT save fails (for example the file is locked by another program), your edits
are kept: the prompt offers **Keep editing** instead of silently moving on.

## Building the Windows app

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1     # or: scripts\build.bat
```

This runs PyInstaller on `scripts/build_executable.spec` and produces
`dist\SPROUTS\SPROUTS.exe` (zip the whole `dist\SPROUTS` folder to distribute; with CUDA
PyTorch it is about 6 GB). The scripts work from any folder, stop if the model weights
are missing (pass `-AllowMissingModels` / `/allow-missing-models` to build anyway) and
exit with an error when the build fails. The app records its version and git commit in
`_internal\build_info.json`.

## Tests

```bash
QT_QPA_PLATFORM=offscreen pytest          # Linux/macOS
$env:QT_QPA_PLATFORM='offscreen'; pytest  # Windows PowerShell
```

The suite pins the scientific calibration (length/area), skeleton vectorization,
CSV schema, and imports of every app module. CI runs it on Windows and Linux, builds
the Python wheel and validates `CITATION.cff`.

## How to cite

Use GitHub's **Cite this repository** button (generated from
[`CITATION.cff`](CITATION.cff)), or cite: Parth, K. *SPROUTS (Root-Mask-and-Skeletons)*,
version 3.0.0, https://github.com/leakey-lab/Root-Mask-and-Skeletons. Please also state
the model checkpoints you used (their SHA-256 is in [`docs/MODELS.md`](docs/MODELS.md)).
A DOI will be added when the first release is archived.

## License

**Pending.** No license has been chosen yet, so no rights to copy, modify or
redistribute this software or the model weights are granted at this time. The
maintainers will add a `LICENSE` file; until then please contact them before reuse.

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
  visualization/       # Dash/Plotly apps + data processing
  mask_model/          # ResNet segmentation model
docs/                  # model card, data dictionary, architecture, known issues
scripts/               # Windows build (PyInstaller spec, build.ps1 / build.bat)
main.py                # entry point
tests/                 # regression, import-smoke, GUI and FAIR-metadata tests
```
