# Data dictionary

Every file SPROUTS writes, what each column or pixel means, its unit and type, how
failures are encoded, and how to open it. Headers and formats below are contracts:
`LENGTH_CSV_HEADERS` / `AREA_CSV_HEADERS` in `app/config.py` are pinned by the test suite.
For the models that produce masks and skeletons see [`docs/MODELS.md`](MODELS.md).

## Project folder

SPROUTS works on one folder of images (the *project*) and writes next to it:

```
<project>/
  <stem>.jpg | .jpeg | .png               original images (never modified)
  mask/<stem>.png                         predicted root masks (Generate; Correct never writes them)
  mask/root_areas.csv                     root area per mask
  skeletons/<stem>_fake.png               model-predicted skeletons
  skeletons/root_lengths.csv              root length per skeleton
  skeleton_corrections/<stem>_skeleton.png   hand-corrected skeletons
  gt/<stem>.png, gt/classes.json          multi-class ground truth (Trace GT)
  gt/mask/<stem>.png                      GT root mask: a mask saved after a human edit
  gt/skeletons/<stem>.png                 GT skeleton: a skeleton saved after a human edit
```

`<stem>` is the image file name without its extension. Images are read in their stored
orientation (EXIF orientation ignored). Older projects may have skeletons
in `output/skeletonizer/test_latest/images/` or masks in `output/mask/`; Measure still
finds them there.

Stems compare without case, on every platform: `A.png`, `a.jpg` and `a.png` are one
image. When several files share a stem, SPROUTS uses one of them everywhere: the one the
Library shows. It is ranked by extension (`.png`, then `.jpg`, then `.jpeg`), and with
equal extensions the file name that sorts first wins (`A.png` before `a.png`). The
others get no mask, skeleton or CSV row. SPROUTS reads PNG and JPEG images only
(`.png`, `.jpg`, `.jpeg`, in any letter case). `.bmp`, `.tif`, `.tiff` and `.ppm` files
are not read; opening a folder names the files it does not list, both the hidden
duplicates and the unsupported formats (since 2026-10-25; before, stems differing only
in case were two images sharing one output file on Windows, and skeleton generation
also read `.bmp` / `.tif` / `.tiff` / `.ppm`).

## File-name convention

Minirhizotron captures are named

```
<field>[camN]_T<tube>_L<position>_<YYYY.MM.DD>_<HHMMSS>_<anything>
e.g. 24EFCAM1_T001_L045_2024.07.18_111618_001_SMM
```

| Part | Meaning | Parsed as |
|---|---|---|
| `<field>` | project/field prefix (a trailing `camN` camera tag is dropped) | text |
| `T<tube>` | minirhizotron tube number | integer (`T001` -> 1) |
| `L<position>` | imaging position (location) along the tube | integer (`L045` -> 45) |
| `YYYY.MM.DD` | capture date (camera clock) | text, dots kept |
| `HHMMSS` | capture time (camera clock, local, no time zone) | text `HH:MM:SS` |

The parser is `app/naming.py`. Tube and position are read only from stand-alone tokens
(delimited by the start or end of the name or by a character that is not a letter or
digit), so a prefix such as `PLOT12_` is not taken for tube 12. A part that is missing
is left empty. Rows whose name has no tube, position or date cannot be placed on the
dashboards.

## Metric CSVs

| File | One row per | Written by |
|---|---|---|
| `skeletons/root_lengths.csv` | `skeletons/*_fake.png` file | Measure > Root length |
| `mask/root_areas.csv` | `mask/*.png` file (prediction) | Measure > Root area |

Each run rewrites the whole file. Rows are sorted by `Tube`, then `Date`, `Time` and
`Position`; rows without a tube come last.

A row exists only for an input whose image is in the project (an image the Library lists:
PNG or JPEG, matched without letter case). Masks, skeletons and ground-truth files whose
image is gone are skipped, never deleted; the sidecar `<csv>.provenance.json` has
`inputs.n_without_image`, `inputs.without_image` (file names, first 100),
`inputs.n_duplicate` and `inputs.duplicates` (files that share one image's name apart
from letter case; only one of them is measured) (since 2026-10-25).

Beside each CSV, Measure writes `<csv>.inputs.json` (e.g. `mask/root_areas.csv.inputs.json`):
`{"csv_sha256": "<SHA-256 of the CSV bytes>", "inputs": {"mask/<stem>.png": [size, mtime_ns, "<SHA-256>"], ...}}`,
one entry per file measured, keyed by its path relative to the project folder. "Changed since
measured" (the Measure status line, the metrics bar) compares each input with its entry: size
and modification time first, the content only when they differ, so a copied or restamped
folder stays current and a repainted file with an older time is changed; inputs added or
removed since are reported. A record whose `csv_sha256` does not match the CSV on disk (the
CSV was edited or replaced) is ignored, and so is a missing one: the comparison then falls
back to file times (an input written after the CSV is changed). If the record cannot be
written, the previous one is removed so it can never describe the new CSV. The record never
changes a CSV value.

### Columns

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `Image` | text | | Image stem: the skeleton file name without `_fake.png`, or the mask file name without `.png` |
| `Tube` | integer | | Tube number from the file name; empty if absent or if the row failed |
| `Position` | integer | | Position along the tube; empty if absent or if the row failed |
| `Date` | text `YYYY.MM.DD` | | Capture date from the file name (dots, not ISO 8601 dashes); empty if absent or failed |
| `Time` | text `HH:MM:SS` | | Capture time from the file name, local camera time without time zone; empty if absent or failed |
| `Length (mm)` | decimal, 2 places | mm | Total root length in the image (`root_lengths.csv` only) |
| `Area (mm²)` | decimal, 2 places | mm² | Total root area in the image (`root_areas.csv` only) |
| `Error` | text | | Empty on success; otherwise the reason the image could not be measured, naming the file (never its folder; since 2026-10-25) |

### Zero versus failure

A failed image is written with value `0` **and** a non-empty `Error`; its `Tube`,
`Position`, `Date` and `Time` are empty. A successful measurement of an image without
roots is `0.0` with an empty `Error`. Zero is common (most images of young or sparse
root systems contain no root), so always filter on `Error` before analysing values:

```python
import pandas as pd
df = pd.read_csv("skeletons/root_lengths.csv", encoding="utf-8")
measured = df[df["Error"].isna()]      # pandas reads an empty Error as NaN
```

### How values are computed (`app/inference/metrics.py`)

Calibration: the camera field of view is 18 mm x 13 mm (`app/config.py::Calibration`),
mapped onto the actual resolution of the file being measured: `mm_per_px_x = 18 / width`,
`mm_per_px_y = 13 / height`.

- **Length.** The skeleton PNG is read as grayscale, binarised with Otsu's threshold
  (inverted if the foreground covers more than half the image) and thinned with
  `skimage.morphology.skeletonize`. Length is the arc length of the 1-pixel skeleton over
  its 8-connected neighbour pairs: horizontal steps cost `mm_per_px_x`, vertical steps
  `mm_per_px_y`, diagonal steps the diagonal `sqrt(mm_per_px_x² + mm_per_px_y²)`.
  Model skeletons are 341 x 256, i.e. 0.0528 x 0.0508 mm per pixel.
- **Area.** The mask PNG is read as grayscale; pixels above 127 are root. Area is the
  root-pixel count times `mm_per_px_x * mm_per_px_y`; a full 640 x 480 mask is 234 mm².
- Values are rounded to 2 decimal places.

Skeletons are predicted from the original images, not from the masks: correcting a mask
changes `Area (mm²)` only. Corrected skeletons (`skeleton_corrections/`) are not read by
Measure.

### Encoding

UTF-8 without byte-order mark, comma-separated, header row, `\r\n` line endings, minimal
quoting (a field is quoted only if it contains a comma, quote or line break). The `²` in
`Area (mm²)` is two bytes in UTF-8; opening the file by double-click in Excel on Windows
shows `Area (mmÂ²)`. Import it instead: *Data > From Text/CSV*, *File origin: 65001:
Unicode (UTF-8)*. In R: `read.csv(path, fileEncoding = "UTF-8", check.names = FALSE)`.

## Masks (`mask/<stem>.png`, `gt/mask/<stem>.png`)

The prediction and the GT mask of an image share the stem and live in two folders.

| File | Written by | PNG mode | Size | Root | Background |
|---|---|---|---|---|---|
| `mask/<stem>.png` (prediction) | *Generate* (mask model) | 8-bit grayscale (`L`) | always 640 x 480 | 255 | 0 |
| `gt/mask/<stem>.png` (GT) | *Correct* | RGBA | the source image's size | opaque white (255, 255, 255, 255) | fully transparent (0, 0, 0, 0) |

*Correct* opens the GT mask when there is one, else the prediction, and saves only to
`gt/mask/`; it never writes or removes a file in `mask/`. Saving an edited prediction makes
it GT; saving an empty (cleared) mask writes an empty GT mask; a mask edited back to equal
its prediction removes the GT file. *Correct* also opens `mask/<stem>.jpg`/`.jpeg`
predictions. Measure > Root area reads the `.png` files in `mask/`.

When predicted masks already exist, *Generate* asks first: **Keep predictions (predict
missing only)** (the default), **Replace all predictions**, or **Cancel**. Hand-corrected
masks live in `gt/mask/` and are never read or touched by Generate; `mask/` holds the
model's output only, so a file there is a prediction whatever its PNG type. An image that
cannot be decoded gets no mask file; it is listed in `mask/_sprouts_run.json`
(`"failed"`) and in the warning shown at the end of the run.

## Skeletons

| File | Format | Meaning |
|---|---|---|
| `skeletons/<stem>_fake.png` | RGB PNG, 341 x 256 | Skeleton predicted by the skeleton model from `<stem>`'s original image; light lines on a dark background, binarised at measure time (see above) |
| `skeleton_corrections/<stem>_skeleton.png` | 8-bit grayscale PNG, 1-pixel lines, 255 on 0 | Skeleton corrected in *Correct*, saved at the source image's resolution exactly as edited (builds before commit `d1645d2` of 2026-09-29, the published `main` among them, save 341 x 256); not read by Measure. The Library's Overlay and Side by side views draw it when it exists |

An image that cannot be decoded gets no skeleton file; it is listed in
`skeletons/_sprouts_run.json` (`"failed"`) and in the warning shown at the end of the
run.

## Ground truth (`gt/`)

Everything a person saves lives under `gt/`; the model's predictions in `mask/` and
`skeletons/` are never written by the editor. `gt/mask/<stem>.png` and
`gt/skeletons/<stem>.png` hold a root mask and a skeleton saved after a human edit (the
GT that Measure prefers over the prediction, once the editor and Measure use them); the
class labels below are hand-traced, multi-class ground truth for training segmentation
models.

### `gt/<stem>.png`

8-bit palette PNG at the source image's resolution. Pixel value = class id: `0`
background (transparent in the palette), `1`-`254` classes from `gt/classes.json`, `255`
Ignore (excluded from training loss). The palette shows each class in its colour. A text
chunk `sprouts_gt` holds JSON: `{"v": 1, "classes": {"<id>": "<key>", ...}, "reviewed":
["<key>", ...]}`, the id-to-key map the file was written with and the classes the
annotator checked on this image ("looked for it"). A saved file with nothing painted is a
valid negative example.

### `gt/classes.json`

```json
{"version": 1, "classes": [
  {"id": 1, "key": "root", "name": "Root", "color": "#5fd6a0", "retired": false},
  {"id": 255, "key": "ignore", "name": "Ignore", "color": "#8a8f98", "retired": false}]}
```

| Field | Type | Rule |
|---|---|---|
| `version` | integer | `1` |
| `id` | integer | 1-254, unique, never reused (retired classes keep theirs); Ignore is 255 |
| `key` | text | `^[a-z][a-z0-9_]{0,31}$`, unique, not `background` |
| `name` | text | 1-40 characters |
| `color` | text `#rrggbb` | unique ignoring case, retired classes included |
| `retired` | `true`/`false` | optional, default `false`; a retired class is exported as Ignore |

Exactly one class has key `ignore` and id 255; it is listed last and cannot be retired.
Values are type-checked strictly: `"id": 1.9`, `"id": true` or `"retired": "false"` make
the file invalid (the app then reports which class and field is wrong and turns GT saving
off until it is fixed).

## GT dataset export

*Export dataset...* writes to an empty folder:

| Path | Content |
|---|---|
| `images/<stem>.<ext>` | byte-for-byte copies of the original images |
| `masks/<stem>.png` | 8-bit grayscale class-index mask at image resolution: `0` background, `1`..`N` the active classes in display order, `255` Ignore (also retired classes and ids unknown to the registry) |
| `classes.json` | `{"version": 1, "ignore_index": 255, "classes": [{"id": 0, "key": "background", "name": "Background"}, {"id": 1, "key": ..., "name": ..., "color": ...}, ...]}`: the exported ids, which differ from the project's ids when classes were retired or reordered |
| `splits/train.txt`, `splits/val.txt` | one stem per line, sorted |
| `manifest.csv` | one row per exported image (below) |
| `manifest.csv.provenance.json` | how the dataset was made (see [Provenance](#provenance-records)) |
| `errors.txt` | only if some images failed: `<stem><TAB><message>` per line (below) |

`manifest.csv` columns (UTF-8, comma-separated):

| Column | Meaning |
|---|---|
| `stem` | image stem |
| `source_image` | absolute path of the original image when it was exported |
| `split` | `train` or `val` |
| `tube`, `position`, `date` | parsed from the file name as above (`date` is `YYYY.MM.DD`); empty if absent |
| `classes_present` | `;`-separated keys of the active classes with at least one pixel |

By default only images on which every active class was checked are exported. The split
is reproducible: stems are grouped by tube (so one tube never spans train and val), the
groups are shuffled with the recorded `seed`, and whole groups go to val until it holds
at least `val_fraction` of the images; the last group always stays in train.

An image fails, and nothing is written for it, when its GT file cannot be read, its source
image is missing or unreadable, or the tracing is not the size of the image (the image was
resized or replaced after tracing; `errors.txt` then says, for example, `GT is 20x20 but
the image is 40x30`). Before 2026-09-30 such a pair was exported misaligned. The rest of the
dataset still exports, and the failures are counted in `outputs.errors` of the provenance
record. The text files (`splits/*.txt`, `classes.json`, `manifest.csv`, `errors.txt`) are
written atomically: each is complete or absent.

## Provenance records

`<output>.provenance.json` next to an output describes what produced it
(`app/provenance.py`, schema `sprouts-provenance/1`). Measure writes
`root_lengths.csv.provenance.json` / `root_areas.csv.provenance.json`, the GT export
`manifest.csv.provenance.json`. Mask and skeleton generation write one
`_sprouts_run.json` per output folder (`app/inference/run_record.py`) with the same
keys, describing the latest run into that folder that wrote or failed an input (completed,
cancelled, or stopped with an error after writing outputs: `outputs.status` is then
`failed`). A run that did neither (a *Keep predictions* run with nothing missing, a
cancel before the first batch) leaves the record alone (since 2026-10-25, O16; before, it
replaced it), and so does a run that stops with an error before writing anything.
`<output folder>/_sprouts_runs.jsonl` holds one compact JSON line per run that wrote or
failed an input, appended: `created_utc`, `kind`, `app_version`, `git_commit`, `models`
(`{name: sha256}`; `{}` and `git_commit` `null` when only a minimal record could be
built), `status` (`completed` / `cancelled` / `failed`), `n_written`, `n_failed`,
`n_skipped`, and `written` (sorted input names) when at most 1,000 were written, else
`written_count` and `written_sha256` (a digest of the names, as `inputs.names_sha256`). A full disk stops a generation run: the first save that finds no space
left ends the run at the next batch boundary with that error (`status` `failed`), and the
outputs already written are kept (since 2026-09-30; before, every remaining image failed on
its own while the run went on). Other save errors stay per-image failures.

When the full record cannot be built or written (since 2026-09-30), a **minimal record**
replaces it, so the folder never keeps the record of an older run: the same top-level keys,
with `git_commit` `null`, `runtime`, `models`, `inputs` and `parameters` empty (`{}`),
`outputs` complete, and an extra key `provenance_error` that says why
(`"<ExceptionType>: <message>"`). If even that cannot be written, the outdated
`_sprouts_run.json` is removed.

| Key | Content |
|---|---|
| `schema` | `sprouts-provenance/1` |
| `kind` | what was done, e.g. `gt_export` |
| `created_utc` | ISO 8601 UTC time, e.g. `2026-09-28T22:42:02Z` |
| `app` | `name`, `full_name`, `version` |
| `git_commit` | commit of the code that ran (from `.git`, or `build_info.json` in the Windows app); `null` if unknown |
| `calibration` | `fov_width_mm`, `fov_height_mm` |
| `runtime` | Python version and implementation, OS, versions of the libraries that were loaded, and (when PyTorch was used) its CUDA/cuDNN versions and precision flags |
| `models` | per model: `path` (relative to the app folder), `sha256`, `size` in bytes; empty for the GT export |
| `inputs`, `parameters`, `outputs` | set by the writer. GT export: `inputs` = project folder name, number of images, the class registry with its original ids; `parameters` = `val_fraction`, `group_by_tube`, `only_complete`, `seed`; `outputs` = `exported`, `train`, `val`, `skipped_incomplete`, `errors`. Generation (`_sprouts_run.json`): `inputs` = `folder`, `n_images`, `names_sha256`; `parameters` = the batch size, `batch_policy` (`"fixed"`: every forward at `batch_size`, the last batch padded with zero images whose outputs are dropped, out of GPU memory stops the run; `"auto"`: the former VRAM-sized, halving behaviour; from `SPROUTS_BATCH_POLICY`) and the model's input settings; `outputs` = `status` (`completed`: the run reached the end, even if every input failed; `cancelled`; `failed`: it stopped with an error), `error` (`null` or `"<ExceptionType>: <message>"`), `n_inputs`, `n_written`, `n_failed`, `n_skipped`, `cancelled`, `batch_size`, `effective_batch_size` (on a GPU the batch size can change outputs slightly, see `docs/KNOWN_ISSUES.md` #7), `elapsed_s`, `failed` (`input` + `reason` per input), `skipped`, `batch_shapes` (forward size as a string key, largest first, -> number of input slots that ran at that size; zero-filled slots included; only forwards that returned a result count), `oom_events` (`{"at": <input file name of the slice's first slot>, "from": <slice length>, "to": <retry length>}` in the order they happened; a single image that runs out of memory alone is a `failed` entry, not an event), `free_vram_gib` (the driver's free GPU memory in GiB, read after the model loaded and before the first batch of a fixed-policy GPU run, 2 dp; `null` on the CPU or under `auto`). Under `fixed`, `batch_shapes` has one key (`batch_size`; padding rows are in the size, never in the count) and `oom_events` is always empty: running out of memory ends the run (`status` `failed`). |
| `provenance_error` | only in a minimal record (generation): why the full record could not be made, `"<ExceptionType>: <message>"` |

Paths in records are relative to the app or project folder so a shared record does not
reveal user or drive names. Loaders ignore these files.

## Windows app build information

The Windows app (`dist/SPROUTS`) contains `_internal/build_info.json`: `name`, `version`,
`git_commit`, `git_dirty`, `built_at` (ISO 8601 with UTC offset), `python`, `platform`
and `packages` (torch, torchvision, PyQt6, PyInstaller versions; the torch version names
its CUDA build, e.g. `2.8.0+cu128`).

## Logs

`app.log` (UTF-8, rotating) in the per-user log folder (`app/user_paths.py`; on Windows
`%LOCALAPPDATA%\SPROUTS\Logs`, on Linux `~/.local/state/sprouts/log`, on macOS
`~/Library/Logs/SPROUTS`). The first line of each session gives the SPROUTS version,
Python and OS.

## Open questions for the owner

Listed, not implemented (each would change a published format):

- an ISO 8601 `DateTime` column in the metric CSVs (today `Date` uses dots and `Time` has
  no time zone);
- writing a UTF-8 byte-order mark so Excel opens the CSVs correctly by double-click
  (other tools would then see a stray character before `Image`);
- exporting skeletons as RSML, the root-architecture exchange format (Lobet et al.
  2015, *Plant Physiology* 167:617);
- MIAPPE-aligned study metadata (Papoutsoglou et al. 2020, *New Phytologist* 227:260);
- storing `source_image` in the GT manifest relative to the project folder instead of as
  an absolute path.
