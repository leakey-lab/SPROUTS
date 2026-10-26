# Model card: SPROUTS checkpoints

SPROUTS runs two neural networks. Both take the **original image** as input; they are
independent of each other (correcting a mask never changes a skeleton, see
[Pipeline](../README.md#pipeline)). This card records what each checkpoint is, how to
check you have the right file, and how its output is produced.

## Identity and verification

The registry of known weights is `app/models.py` (`MODELS`, `verify()`); the app compares
each file with its SHA-256, logs a warning on a mismatch and keeps running (labs may use
retrained weights). Provenance records store the digest of the weights that were used.

| Key | File (under the app folder) | Size (bytes) | SHA-256 |
|---|---|---|---|
| `mask` | `checkpoints/mask_weights/best_mask_model_V5.pth` | 338,502,139 | `ca1f92d21609f9905bd407bd0562f8456cfff3987d1493c701a5585ee3a3a43f` |
| `skeleton` | `checkpoints/skeletonizer/latest_net_G.pth` | 51,749,020 | `a312586ce086aa913b6823f1c99c064f9189d71f77bd575a6529ee9f6a0d2daa` |

Check a file yourself:

```powershell
Get-FileHash checkpoints\mask_weights\best_mask_model_V5.pth -Algorithm SHA256   # PowerShell
certutil -hashfile checkpoints\skeletonizer\latest_net_G.pth SHA256              # cmd
```

```bash
sha256sum checkpoints/mask_weights/best_mask_model_V5.pth checkpoints/skeletonizer/latest_net_G.pth   # Linux
shasum -a 256 checkpoints/mask_weights/best_mask_model_V5.pth checkpoints/skeletonizer/latest_net_G.pth   # macOS
```

### Where to get them

- **Both checkpoints** are released as one Zenodo record under CC-BY-4.0. The record does
  not exist yet (`url` and `doi` are `None` in `app/models.py`); the checklist below is the
  owner's to complete. Until then ask the maintainers.
- **Download and check:** `./venv/Scripts/python.exe scripts/fetch_models.py` fetches what
  is missing and verifies each file against the digests above; it discards a download
  whose digest differs, and leaves a file you already have (for example retrained
  weights) alone unless you pass `--replace`.
- **Skeleton model from git history:** `checkpoints.zip` (47,935,331 bytes, SHA-256
  `6db9555891102e2eff148d22216b141589a28558ed8a6eb85248e4040510e037`) was tracked until
  this release; `git restore --source=2075568 -- checkpoints.zip` restores it. It holds
  `checkpoints/skeletonizer/latest_net_G.pth` (the digest above) and a stale
  `checkpoints/experiment_name/test_opt.txt`.
- **Mask model:** not distributed in git. Without it, *Generate* cannot produce masks and
  root area cannot be measured from model masks (GT masks saved in *Correct* still work).

### Zenodo deposit checklist (owner)

1. Create a new upload on zenodo.org; resource type Software (or Dataset); title "SPROUTS
   model weights (root mask and skeleton models)"; creators Kinjalk Parth, Sebastian
   Varela, Andrew D. B. Leakey; licence **CC-BY-4.0**.
2. Upload exactly `best_mask_model_V5.pth` (338,502,139 bytes) and `latest_net_G.pth`
   (51,749,020 bytes). Check them against the table above before uploading.
3. Description: what each model does (this card), the training data of each (the two
   `TODO(owner)` rows below are still open), the software version that runs them, and a
   related identifier "is supplement to" `https://github.com/leakey-lab/Root-Mask-and-Skeletons`.
4. Publish. Copy the DOI and the two file URLs (`https://zenodo.org/records/<id>/files/<name>`).
5. In `app/models.py` set `url` and `doi` of both `ModelSpec`s; in this file fill the
   table of "Identity and verification" with the DOI; add the DOI as `references` or
   `identifiers` in `CITATION.cff` if wanted.
6. In a clean folder run `scripts/fetch_models.py --root <folder>`; both models must report ok.

## Licence of the weights

Both checkpoints are released under **CC-BY-4.0** (https://creativecommons.org/licenses/by/4.0/):
you may share and adapt them if you give credit (cite SPROUTS, see the README). The licence
of the weights is separate from the GPL-3.0-or-later licence of the code. `ModelSpec.license`
in `app/models.py` records it for each checkpoint.

## Mask model (root segmentation)

| | |
|---|---|
| Code | `app/mask_model/model.py`, class `ResNetSkeleton` (despite its name, the *mask* network); run by `app/inference/mask_inference.py` (started by `app/handlers/mask_generation_handler.py`) |
| Architecture | ResNet-18 encoder (torchvision layout, no fully connected head). The stage-2, -3 and -4 features each go through three parallel dilated 3x3 convolutions (dilation 2, 4, 8) and a DualAttention block (position attention plus channel attention, fused by a 1x1 convolution). A decoder of 2x2 transposed convolutions concatenates the stage-3 and stage-2 attention features, applies dilated refinement at 1/4 and 1/2 resolution, and ends in two 1x1 convolutions and a sigmoid. |
| Parameters | 84,558,217. The checkpoint stores the shared encoder twice (as `resnet18.*` and `layer0-4.*`), hence 338 MB. ImageNet weights are not used at inference (`pretrained=False`); every weight comes from the checkpoint. |
| Input | The original image as RGB, resized to 480 x 640 (height x width, bilinear, antialiased), scaled to [0, 1], normalised per channel with mean (0.5514, 0.4094, 0.3140) and std (0.1299, 0.1085, 0.0914) (R, G, B; `MASK_MEAN` and `MASK_STD` in `app/inference/mask_inference.py`). |
| Output | One 480 x 640 probability map. Pixels with probability `> MASK_THRESHOLD` (0.5, `app/config.py`) are root. Saved as `mask/<stem>.png`, 8-bit grayscale, values 0/255, always 640 x 480 whatever the input size (see `docs/DATA_DICTIONARY.md`). |
| Precision | fp32, no autocast. On CUDA the runtime (`app/inference/runtime.py`) uses TF32 and the channels-last memory format; cuDNN autotuning is off and deterministic algorithms are on by default (since `abc34fa`; `SPROUTS_CUDNN_BENCHMARK=1` opts out). Outputs can still differ by a few pixels from CPU and between batch sizes (see Known limits). |
| Training data | TODO(owner): not documented in the repository. |

## Skeleton model (skeletonizer)

| | |
|---|---|
| Code | `app/inference/skeleton_inference.py`, class `EnhancedResnetGenerator`, loaded by `SkeletonModel` |
| Architecture | Pix2Pix-style ResNet generator: 7x7 convolution (64 channels), two stride-2 dilated convolution blocks (dilation 1, 2, 4, with residual shortcuts) to 256 channels, nine residual blocks (reflection padding, BatchNorm, dropout layers inactive at inference) with one squeeze-and-excitation layer after the fifth, two 3x3 transposed convolutions back to 64 channels, and a 7x7 convolution to 3 channels with tanh. |
| Parameters | 12,909,443 (`norm="batch"`, `use_dropout=True`, `n_blocks=9`: the checkpoint loads with every key matched). |
| Input | The original image (not the mask) as RGB, resized to 256 x 256 (bicubic), normalised to [-1, 1] (mean 0.5, std 0.5 per channel). |
| Output | A 3 x 256 x 256 image in [-1, 1], mapped to 0-255 and resized (bicubic) to 341 x 256 (width x height, aspect ratio 3/4 as in the validated pipeline). Saved as `skeletons/<stem>_fake.png` (RGB). |
| Measurement | Not part of the model: `app/inference/metrics.py` reads the PNG as grayscale, applies an Otsu threshold (inverted if the foreground is the majority) and `skimage.morphology.skeletonize`, then measures the 1-pixel skeleton's length with the corner-count estimator of Vossepoel &
  Smeulders (1982) (0.980 per orthogonal step, 1.406 per diagonal step, −0.091 per change of
  direction, m-adjacent steps, per-axis pixel pitch). |
| Precision | fp32, no autocast (mixed precision visibly changes the generator's output). Same CUDA runtime caveats as the mask model. |
| Training data | TODO(owner): not documented in the repository. |

`checkpoints/experiment_name/test_opt.txt` is left over from the original pix2pix test
run (dataroot `D:/2024 EF image data`). It says `netG: resnet_9blocks`,
`norm: instance` and `aspect_ratio: 1.0`; the code, which is authoritative, uses the
enhanced generator with batch normalisation and aspect ratio 3/4.

## Known limits

- **Fixed geometry.** Masks are always 640 x 480 and skeletons 341 x 256. Length and
  area are calibrated from the camera field of view (18 x 13.5 mm by default,
  `app/config.py`; a project can set its own in `sprouts_project.json`)
  mapped onto the actual output resolution, so they are valid for images of that field
  of view; a camera with a different field of view needs a project file (or the default
  changed).
- **Domain.** Behaviour on other cameras, soils, crops or lighting is untested.
- **GPU reproducibility.** cuDNN autotuning is off by default, so repeated GPU runs at the
  same batch size reproduce; a different batch size can flip a few pixels (the batch
  shape decides which kernel runs), and GPU and CPU differ slightly.
  `SPROUTS_CUDNN_BENCHMARK=1` brings back the run-to-run noise measured in a 2026-09-28
  audit of 12 images: 18 of 24 output files differed between two identical runs and one
  image's CSV values moved (length 60.75 vs 60.77 mm, area 126.62 vs 126.63 mm²).
- **Two independent predictions.** Skeletons are not derived from masks, so the two can
  disagree for the same image; a correction saved in *Correct* (GT) applies to one output only.
