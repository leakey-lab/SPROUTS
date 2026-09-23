# CLAUDE.md — Root-Mask-and-Skeletons

Desktop app (**PyQt6 + PyTorch**, Python 3.10) for **plant-root phenotyping**
from minirhizotron images. Pipeline:

```
image → ResNet mask → manual mask trace → Pix2Pix skeleton →
skeleton-correction GUI → root length (mm) & area (mm²) → CSV → Dash/Plotly viz
```

See `README.md` for the user-facing overview and calibration math. This file is
the agent contract: how to run things, where things live, and the conventions to
follow.

---

## Environment

- **Use `./venv/Scripts/python.exe` for every run.** System Python is CPU-only
  torch and *falsely* reports CUDA available. Never invoke bare `python` /
  `python3`. (memory: `venv-is-real-torch`)
- Platform: Windows 11, PowerShell. Use PowerShell syntax (`$null`, `$env:VAR`,
  backtick line-continuation).
- PyTorch is installed **separately** from `requirements.txt` so the build
  matches the local CUDA toolkit (pip won't silently swap in a CPU wheel). See
  README "Installation".

---

## Commands

```powershell
# Run the app
./venv/Scripts/python.exe main.py

# Tests (Qt needs an offscreen platform in headless/CI)
$env:QT_QPA_PLATFORM = 'offscreen'; ./venv/Scripts/python.exe -m pytest

# Lint / format (configured in pyproject.toml — line-length 100, py310)
./venv/Scripts/python.exe -m ruff check .
./venv/Scripts/python.exe -m black .
./venv/Scripts/python.exe -m isort .
```

- Test suite pins the scientific calibration (length/area), skeleton
  vectorization, the CSV schema, and an import-smoke of every app module.
- CI: `.github/workflows/ci.yml` runs that suite on push. Two CI gotchas already
  fixed (memory `ci-deps-install-gotchas`): `requirements.txt` must stay UTF-8
  (was UTF-16), and `pywin32` is gated to `platform_system == "Windows"` (no
  Linux wheel). `.pre-commit-config.yaml` mirrors the lint/format hooks.
- Model checkpoints are **not** committed — place under `checkpoints/`
  (`mask_weights/best_mask_model_V5.pth`, `skeletonizer/latest_net_G.pth`).

---

## Architecture map

Entry: `main.py` — sets Qt6 D3D11 RHI + ANGLE flags (WebEngine stability, see
`gles3-error-from-disable-gpu`), loads `resources/themes/dark_theme.qss`, applies
the SPROUTS dark palette, opens `MainWindow`.

| Area | Path | Notes |
|---|---|---|
| Config | `app/config.py` | **Single source of truth**: `Calibration` (FOV 18×13 mm), ports, paths, batch sizes. Change FOV here only. |
| Inference | `app/inference/` | `metrics.py` = shared length+area primitives (de-duplicated); `skeleton_inference.py` = Pix2Pix; ResNet mask inference; `runtime.py` = shared GPU auto-tuner (device/autocast/channels_last) + singleton skeleton-model cache. |
| Handlers | `app/handlers/` | Batch orchestration on `QThread` (skeleton, mask-gen, root-length/area). Keep heavy work off the GUI thread. |
| GUI | `app/gui/` | PyQt6 widgets. `MainWindow` orchestrates and **delegates** to `ui_panels`, `file_tree_manager`, `visualization_manager`. SPROUTS guided shell: titlebar / 7-stage ribbon / action bar / `MetricsBar`. |
| Editors | `app/gui/` | `MaskTracingInterface` (interface + graphics view + drawing mixin + cursor) and `SkeletonCorrectionInterface` (interface + view + `SkeletonCorrectionModel`) are **isolated** editor streams. |
| Visualization | `app/visualization/` | Dash/Plotly length + area dashboards, `DataProcessor` / `DataCache`, embedded via `QWebEngineView`. |
| Mask model | `app/mask_model/` | ResNet segmentation (`EnhancedResnetGenerator`, SE/`DualAttention` blocks). |
| GT tracing | `app/gt/` + `app/gui/gt_tracing_interface.py` | Multi-class **ground truth** for segmentation datasets ("Trace GT" stage, right_panel index 4). Qt-free core: `registry.py` (`gt/classes.json`), `store.py` (`gt/<stem>.png` 8-bit palette, id 255 = Ignore), `export.py` (dataset export). **Independent of every ML model**: never reads/writes `mask/` or `skeletons/`, never measures. Spec: `docs/superpowers/specs/2026-09-22-multiclass-labels-design.md`. |

**God nodes** (most-connected core abstractions, from the graph — touch with
care): `SkeletonCorrectionInterface` (85 edges), `MainWindow` (72),
`MaskTracingInterface` (47), `NormalizationControls` (34),
`SkeletonCorrectionModel` (34).

**Other single-source files:** `app/inference/runtime.py` (how inference runs),
`resources/themes/dark_theme.qss` + `app/gui/widgets/tokens.py` (SPROUTS design
tokens). SPROUTS accents: mask green `#5fd6a0`, skeleton orange `#f0a868`,
purple primary `#c39af6`, selection `#b794f6`.

---

## Docs

- `docs/KNOWN_ISSUES.md` — **read first** for current open items.
- `docs/Architecture_2.md` — deeper architecture / data-flow write-up.
- `docs/BUILD_EXECUTABLE.md` — PyInstaller packaging (CUDA build notes).
- `docs/SPROUTS_DASH_THEME.md` — Dash theme + design-token reference.
- `docs/superpowers/{plans,specs}/` — implementation plans + specs (viz redesign,
  ribbon/metrics, SPROUTS shell).

**Current open item** (KNOWN_ISSUES #3): the display `MetricsBar` shows Root
length / Root area as `—`. Measured values are produced only by batch threads and
written to CSV; they're never loaded back into per-image state. **Do not
fabricate values** — populating them read-only means parsing those CSVs.

---

## Memory — two layers (use both)

### 1. Local auto-memory (file-based, per-project)

Path: `~/.claude/projects/D--Root-Mask-and-Skeletons/memory/`

- `MEMORY.md` is the index, loaded each session — **read it for the current fact
  list** (don't trust a hard-coded list here; it drifts).
- Each fact is its own `.md` file with frontmatter (`type:` user | feedback |
  project | reference).
- **Before recommending a file/flag named in a recalled memory, verify it still
  exists.** Memories reflect what was true when written.
- Write here for durable decisions, debugging insights, user preferences, env
  gotchas. Update the matching file instead of duplicating; delete memories
  proven wrong.

### 2. mem0 (semantic memory, MCP server)

Self-hosted mem0 MCP (`~/mem0-mcp/`, local Ollama + Qdrant, no cloud). Requires
the Qdrant container + Ollama running.

- **At start of coding work:** `mcp__mem0__search_coding_memory` to recall prior
  context, conventions, decisions.
- **When you learn** architecture, conventions, key decisions, debugging
  insights, or user preferences: `mcp__mem0__add_coding_memory`.

Rule of thumb: **file-memory = pinned, human-curated facts**; **mem0 = semantic
recall across everything learned**. Significant decisions go in both.

---

## Knowledge graph — graphify

Graph at `graphify-out/` (built AST-only, no API cost).

- Before architecture/codebase questions, read `graphify-out/GRAPH_REPORT.md`
  (god nodes, communities, surprising connections).
- Navigate `graphify-out/wiki/index.md` instead of raw-file sweeps when it covers
  the area.
- After editing code this session, run `graphify update .` to refresh the graph
  (check staleness: graph header lists its build commit vs `git rev-parse HEAD`).
- `/graphify` for a fresh build of any input.

---

## MCP servers

- **mem0** — persistent coding memory (above).
---

## Skills & tooling conventions

- **Search/explore:** prefer Glob/Grep and the graphify wiki over raw file
  sweeps. For broad multi-file fan-out, delegate to `Explore` or cavecrew agents.
- **Code review:** `/code-review` for correctness bugs; `/simplify` for
  quality-only cleanup; `caveman:caveman-review` for compressed PR feedback.
- **Caveman mode** is active in this workspace — chat responses compressed,
  technical substance preserved. Code / commits / PRs still written normally.
- **superpowers** skills (brainstorming, TDD, systematic-debugging,
  verification-before-completion) govern *how* to approach work — invoke the
  relevant process skill before implementation.
- **Academic / writing work** (reports, papers, lit reviews):
  `academic-research-skills` (`/ars-*`) and `claude-scientific-writer` skills are
  installed.

---

## Workflow defaults

1. Recall memory (file `MEMORY.md` + mem0 search) at start.
2. Consult graphify before architecture questions.
3. Use `./venv/Scripts/python.exe` for any run.
4. Persist new decisions/insights to **both** memory layers.
5. Run `graphify update .` after code edits.
</content>
</invoke>
