# Example project

Three 640 x 480 minirhizotron photos of 2024-07-18 with the model outputs SPROUTS made
for them, the Measure results and one graph. Copy this folder somewhere first, then open
the copy in SPROUTS (Library, then Open folder) to try Correct, Measure and Visualize on
real data. Correct and Measure write into the folder they open (`gt/`, the CSVs and their
sidecars), so opening the checkout itself would rewrite its tracked files.

| File | What it is |
|---|---|
| `24EFCAM1_T001_L044_2024.07.18_111530_001_SMM.jpg` | tube 1, position 44: roots in the mask and the skeleton |
| `24EFCAM1_T001_L050_2024.07.18_111522_001_SMM.jpg` | tube 1, position 50: the skeleton model found no root (empty skeleton) |
| `24EFCAM1_T012_L047_2024.07.18_101909_001_SMM.jpg` | tube 12, position 47 |
| `mask/<stem>.png` | root mask from the mask model (8-bit grey, 640 x 480) |
| `skeletons/<stem>_fake.png` | skeleton from the skeleton model (341 x 256) |
| `skeletons/root_lengths.csv`, `mask/root_areas.csv` | Measure results (columns: `docs/DATA_DICTIONARY.md`) |
| `results/length_and_area.svg` | root length and root area of the three images |

Origin: the Leakey lab 2024 field campaign (camera `24EFCAM1`); masks and skeletons are
the outputs of the checkpoints in `docs/MODELS.md`, copied unchanged from the full project.
Licence: CC-BY-4.0 for the photos, masks, skeletons, CSVs and graph; cite SPROUTS (see the
README). These three images are the only ones of the dataset that are published.
Regenerate the CSVs and the graph with `./venv/Scripts/python.exe scripts/make_examples.py`.

Measure also writes `<csv>.provenance.json` (clock time, host details) and `<csv>.inputs.json`
(file times) next to each CSV. They are deleted here, so that regenerating the folder gives
the same bytes. Measure writes them again when you run it on this folder in SPROUTS.
`tests/test_examples.py` checks that the committed CSVs are what Measure writes today, and
`tests/test_correct_goldens_real.py` replays the Correct goldens on these photos.
