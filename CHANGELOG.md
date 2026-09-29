# Changelog

All notable changes to SPROUTS (Root-Mask-and-Skeletons) are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/); the version lives in `app/__init__.py`.

Every release has a **Scientific output changes** section: anything that can change a
value in `root_lengths.csv` / `root_areas.csv`, a mask or skeleton pixel, or a
ground-truth export. Record it there even when the change is a bug fix, so results
produced before and after the change can be told apart.

## [Unreleased]

### Added
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

### Changed
- `gt/classes.json` is parsed strictly: a wrong field type (for example
  `"retired": "false"` or `"id": 1.9`) is reported with the class and field it concerns
  instead of being silently coerced, and `"id": 1e999` no longer aborts the app.
- `scripts/build.ps1` and `scripts/build.bat` work from any folder, stop when model
  weights are missing (unless told otherwise) and exit with an error when PyInstaller
  fails (`build.bat` used to report success after a failed build).
- README: the documented PyTorch nightly no longer exists; install the stable
  torch 2.8.0 / torchvision 0.23.0 build for your CUDA version (or the CPU build).

### Scientific output changes
- None from the changes above.

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
  autocast (cuDNN TF32 convolutions and `cudnn.benchmark` stay enabled, so GPU results
  are not bit-reproducible run to run). Masks and skeletons generated on a GPU between
  2026-05-31 and 2026-06-01 used mixed precision.
