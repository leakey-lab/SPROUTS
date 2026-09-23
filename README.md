# Root-Mask-and-Skeletons

Desktop application (PyQt6 + PyTorch) for **plant-root phenotyping** from
minirhizotron images. Pipeline:

```
image → ResNet mask → manual mask trace → Pix2Pix skeleton →
skeleton-correction GUI → root length (mm) & area (mm²) → CSV → Dash/Plotly viz
```

The goal is to make root insights broadly available: quantitative metrics
exported as CSV plus interactive web visualizations.

## Requirements

- Python 3.10
- A CUDA GPU is recommended for inference (CPU works but is slow).

## Installation

PyTorch is **installed separately** so the build matches your CUDA toolkit and
pip cannot silently downgrade you to a CPU wheel:

```bash
# 1. GPU build (example: CUDA 12.8). Use the index that matches your CUDA.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
#    CPU-only alternative:
#    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 2. Application dependencies
pip install -r requirements.txt

# 3. (optional) development / test tooling
pip install -r requirements-dev.txt
```

Model checkpoints are not committed; place them under `checkpoints/`
(`mask_weights/best_mask_model_V5.pth`, `skeletonizer/latest_net_G.pth`).

## Running

```bash
python main.py
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen pytest          # Linux/macOS
$env:QT_QPA_PLATFORM='offscreen'; pytest  # Windows PowerShell
```

The suite pins the scientific calibration (length/area), skeleton vectorization,
CSV schema, and imports of every app module.

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
  retired (its id is never reused).

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
images/<stem>.<ext>    copied originals
masks/<stem>.png       class-id PNG: background 0, classes 1..N, Ignore 255
classes.json           id → name/colour, ignore_index 255
splits/train.txt, splits/val.txt
manifest.csv           stem, source image, split, tube, position, date, classes present
```

By default only images where every class is checked are exported, and all images
of one tube stay in the same split (so the same root on different dates cannot
leak between train and validation). Images that fail to export are listed in the
dialog and in `errors.txt`; the rest still export.

If a GT save fails (for example the file is locked by another program), your edits
are kept: the prompt offers **Keep editing** instead of silently moving on.

## Project layout

```
app/
  config.py            # single source of calibration, ports, paths, batch sizes
  gt/                  # ground-truth tracing core: class registry, GT PNG store, export
  inference/           # metrics (length/area), skeleton & mask inference
  handlers/            # batch orchestration (QThread)
  gui/                 # PyQt6 widgets, editors, graphics views
  visualization/       # Dash/Plotly apps + data processing
  mask_model/          # ResNet segmentation model
main.py                # entry point
tests/                 # regression + import-smoke suite
```
