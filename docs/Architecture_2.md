# 🌱 Root-Mask-and-Skeletons — System Architecture

## 📋 Executive Summary

The Root-Mask-and-Skeletons application is a comprehensive PyQt6-based system for automated root analysis in plant science. It combines computer vision, deep learning, and interactive visualization to process root images, generate segmentation masks, extract skeletal structures, and compute morphological metrics.

### Key Capabilities
- **🎨 Interactive Correction**: one *Correct* stage edits the mask and the skeleton of an image on one canvas; *Trace GT* paints multi-class ground truth
- **🤖 ML-Powered Processing**: Dual-model architecture using ResNet for masks and Pix2Pix for skeletons
- **📊 Advanced Analytics**: Automatic root length calculation and morphometric analysis
- **📈 Rich Visualizations**: Interactive dashboards with Plotly Dash integration
- **⚡ Hardware Optimization**: Intelligent GPU/CPU resource management

---

## 🏗️ System Architecture Overview

### Unified Functional Architecture

```mermaid
flowchart TB
    %% Styling
    classDef actor fill:#fef3c7,stroke:#f59e0b,stroke-width:3px,color:#000
    classDef storage fill:#dbeafe,stroke:#3b82f6,stroke-width:2px,color:#000
    classDef ui fill:#e9d5ff,stroke:#a855f7,stroke-width:2px,color:#000
    classDef process fill:#d1fae5,stroke:#10b981,stroke-width:2px,color:#000
    classDef ml fill:#fecaca,stroke:#ef4444,stroke-width:2px,color:#000
    classDef gpu fill:#fee2e2,stroke:#dc2626,stroke-width:3px,stroke-dasharray: 5 5,color:#000
    
    %% Actors & Storage
    U([👤 User]):::actor
    UI["🎛️ User Interface<br/>Orchestrator<br/>(PyQt6 GUI)<br/>[CPU]"]:::ui
    STORE[("📁 Local/Shared Storage<br/>Images & Folders")]:::storage
    ART[("💾 Processed Artifacts<br/>Masks, Skeletons<br/>CSV, HTML")]:::storage

    U ==> UI
    UI <==> STORE
    UI <==> ART

    %% Action Selection Hub
    subgraph SELECT["🎯 Action Selection Hub"]
        direction LR
        SEL{Select Operation}
    end
    
    UI --> SEL

    %% Core Functional Units
    subgraph CORE["🔧 Core Processing Units"]
        direction TB
        VIEW["👁️ View/Overlay/Compare<br/>(Display Controller)<br/>[CPU]"]:::process
        MT["✏️ Correct<br/>(mask + skeleton,<br/>one canvas)<br/>[CPU]"]:::process
        MG["🎭 Generate Masks<br/>(ML Inference)<br/>[GPU→CPU]"]:::ml
        SG["🦴 Generate Skeletons<br/>(ML via Subprocess)<br/>[GPU→CPU]"]:::ml
        RL["📏 Compute Root Length<br/>(Skeletonization)<br/>[CPU]"]:::process
        VIZ["📊 Visualize Metrics<br/>(Dash Server)<br/>[CPU]"]:::process
    end

    SEL ==> VIEW
    SEL ==> MT
    SEL ==> MG
    SEL ==> SG
    SEL ==> RL
    SEL ==> VIZ

    %% Data Flow Paths
    VIEW -.->|overlay masks| ART
    MT -->|save/edit| ART
    MG -->|binary masks| ART
    SG -->|skeleton images| ART
    RL -->|metrics CSV| ART
    VIZ -->|read/export| ART

    %% Device & Model Lifecycle
    subgraph ML["🧠 Machine Learning Models"]
        direction TB
        RESL["🔴 ResNet Skeleton Model<br/>• Loaded at startup<br/>• GPU/CPU adaptive<br/>• Persistent in memory<br/>• Released on exit"]:::gpu
        P2PL["🔵 Pix2Pix Model<br/>• Spawned in subprocess<br/>• GPU prioritized<br/>• Auto memory cleanup<br/>• Process isolation"]:::gpu
    end

    MG -.->|uses| RESL
    SG -.->|spawns| P2PL

    %% Storage Connections
    STORE ==> MG
    STORE ==> SG
    STORE ==> VIEW
    STORE ==> MT
```

---

## 🌐 System Context & External Interfaces

```mermaid
flowchart LR
    %% Styling
    classDef external fill:#f0f9ff,stroke:#0284c7,stroke-width:2px
    classDef internal fill:#f0fdf4,stroke:#16a34a,stroke-width:2px
    classDef hardware fill:#fef3c7,stroke:#f59e0b,stroke-width:2px
    classDef model fill:#fce7f3,stroke:#ec4899,stroke-width:2px

    %% External Actors
    subgraph EXT["External Interfaces"]
        user([👤 User]):::external
        fs[("💾 File System<br/>Local Storage")]:::external
        gpu["⚡ GPU/CPU<br/>Hardware")]:::hardware
    end

    %% Core Application
    subgraph APP["Root Analysis Application"]
        gui["🖥️ PyQt6 GUI<br/>`main.py`<br/>`main_window.py`"]:::internal
        
        subgraph MODELS["ML Models"]
            pix2pix["🎨 Pix2Pix GAN<br/>`inference/skeleton_inference.py`<br/>Enhanced ResNet"]:::model
            resnet["🔍 Mask Model<br/>`mask_model/model.py`<br/>ResNetSkeleton"]:::model
        end
        
        dash["📊 Dash Server<br/>`root_length_visulization.py`<br/>`dash_app.py`"]:::internal
    end

    %% Connections
    user ==> gui
    gui <==> fs
    gui <==> dash
    gui --> pix2pix
    gui --> resnet
    pix2pix <==> fs
    resnet <==> fs
    dash <==> fs
    pix2pix -.-> gpu
    resnet -.-> gpu
```

---

## 🔌 Component Architecture & Module Dependencies

```mermaid
flowchart TB
    %% Styling
    classDef gui fill:#e0e7ff,stroke:#6366f1,stroke-width:2px
    classDef handler fill:#dcfce7,stroke:#22c55e,stroke-width:2px
    classDef inference fill:#fee2e2,stroke:#ef4444,stroke-width:2px
    classDef viz fill:#fef3c7,stroke:#f59e0b,stroke-width:2px

    %% GUI Layer
    subgraph GUI["🎨 GUI Layer (PyQt6)"]
        MW["📱 MainWindow<br/>`main_window.py`<br/>Application Controller"]:::gui
        DC["🖼️ DisplayController<br/>`display_controller.py`<br/>Image Rendering"]:::gui
        CE["✏️ CorrectEditor<br/>`correct/editor.py`<br/>Mask + skeleton layers"]:::gui
        GT["🏷️ GtTracingInterface<br/>`gt_tracing_interface.py`<br/>Ground truth"]:::gui
        IM["📂 ImageManager<br/>`image_manager.py`<br/>File Management"]:::gui
    end

    %% Handler Layer
    subgraph HANDLERS["⚙️ Handler Layer"]
        SKH["🦴 SkeletonHandler<br/>`skeleton_handler.py`<br/>Skeleton Ops"]:::handler
        MGH["🎭 MaskGenerationHandler<br/>`mask_generation_handler.py`<br/>Mask Ops"]:::handler
        GSH["🔄 GenerateSkeletonHandler<br/>`generate_skeleton_handler.py`<br/>Subprocess Manager"]:::handler
    end

    %% Inference Layer
    subgraph INFERENCE["🤖 Inference Layer"]
        P2P["🎨 Pix2Pix Engine<br/>`inference/skeleton_inference.py`"]:::inference
        
        subgraph P2P_DEPS["Pix2Pix Parts"]
            MODELS["📦 EnhancedResnetGenerator<br/>Network Definition"]:::inference
            DATA["📊 Image Loading<br/>& Transforms"]:::inference
            UTIL["🛠️ Batch Engine<br/>`inference/batch_engine.py`"]:::inference
        end
        
        RLC["📏 RootLengthCalculator<br/>`root_length_inference_handler.py`<br/>Morphometry"]:::inference
        RESNET["🧠 ResNetSkeleton<br/>`mask_model/model.py`<br/>Segmentation"]:::inference
    end

    %% Visualization Layer
    subgraph VIZ["📊 Visualization Layer"]
        RLV["📈 RootLengthVisualization<br/>`root_length_visulization.py`<br/>Qt Integration"]:::viz
        DASH["🎯 DashApp<br/>`dash_app.py`<br/>Web Dashboard"]:::viz
        DP["🔄 DataProcessor<br/>`data_processor.py`<br/>Data Pipeline"]:::viz
    end

    %% Connections
    MW ==> IM
    MW ==> DC
    MW ==> CE
    MW ==> GT
    MW ==> SKH
    MW ==> MGH
    SKH ==> GSH
    SKH ==> RLC
    MGH ==> RESNET
    GSH ==> P2P
    P2P --> MODELS
    P2P --> DATA
    P2P --> UTIL
    RLV ==> DASH
    RLV ==> DP
    MW <==> RLV
```

### ✏️ The Correct editor (composition)

One stage edits both documents of an image on one canvas. `CorrectEditor`
(`app/gui/correct/editor.py`) owns no document, tool or history logic: it builds the parts
below, forwards the `MainWindow` API (`load_image`, `maybe_save_changes`, `reset`,
`navigate`, …) and turns the session's save reports into signals (`mask_saved`,
`mask_cleared`, `skeleton_saved`; the last one is not connected, owner decision FR-03).

```mermaid
flowchart TB
    classDef gui fill:#e0e7ff,stroke:#6366f1,stroke-width:2px
    classDef core fill:#dcfce7,stroke:#22c55e,stroke-width:2px
    classDef item fill:#fef3c7,stroke:#f59e0b,stroke-width:2px

    CE["CorrectEditor<br/>`correct/editor.py`"]:::gui
    VIEW["EditorCanvasView<br/>`editor_canvas.py`"]:::gui
    ROUTER["ToolRouter<br/>`correct/tool_router.py`"]:::gui
    CHROME["EditorChrome, CorrectControls, LayerPanel<br/>`editor_chrome.py`, `correct/controls.py`,<br/>`correct/layer_panel.py`"]:::gui
    SESSION["CorrectSession (Qt-free)<br/>`correct/session.py`"]:::core
    JOURNAL["EditJournal (stdlib only)<br/>`app/editing/journal.py`"]:::core
    ML["MaskLayer + tools<br/>`correct/mask_layer.py`, `mask_tools.py`"]:::gui
    SL["SkeletonLayer<br/>`correct/skeleton_layer.py`"]:::gui
    MD["MaskDocument (Qt-free)<br/>PatchHistory, MaskStore I/O"]:::core
    SD["SkeletonDocument (Qt-free)<br/>SkeletonCorrectionModel"]:::core
    PHOTO["PhotoLayerItem<br/>area pyramid, bilinear"]:::item
    RASTER["RasterLayerItem<br/>exact pixels, 256 px tiles"]:::item
    SKEL["SkeletonLayerItem<br/>a line at every zoom"]:::item

    CE --> VIEW
    CE --> CHROME
    CE --> SESSION
    CE --> ROUTER
    CE --> PHOTO
    VIEW -->|input| ROUTER
    ROUTER -->|active layer| ML
    ROUTER -->|active layer| SL
    SESSION --> ML
    SESSION --> SL
    SESSION --> JOURNAL
    ML --> MD
    ML --> RASTER
    SL --> SD
    SL --> SKEL
```

- **Layers** implement one contract, `EditLayer` (`app/gui/correct/layer.py`): open, save
  and revert, their own undo stack, gestures, tools, visibility and opacity. The session
  and the router see only this contract, so another layer or raster tool is one more class.
- **The session** (`CorrectSession`) opens both layers, aggregates the unsaved state,
  saves (the unsaved layers, mask first; with nothing unsaved, the active layer), asks the
  one Save / Discard / Cancel question and keeps one undo timeline: `EditJournal` records
  which layer acted, each layer keeps its own stack.
- **Trace GT** stays a separate stage on `RasterEditorBase` (`app/gui/raster_editor_base.py`),
  built from the same canvas parts: `EditorCanvasView`, `EditorChrome`, `PaintCanvas`,
  `PhotoLayerItem`, `RasterLayerItem` and `editor_prompts`.
- Every `app/gui/correct/*.py` stays at or under 450 lines (a ratchet test), so no module
  of the stage becomes a god class.

---

## 🔄 Operational Workflows

### 🦴 Skeleton Generation Workflow (Pix2Pix GAN)

```mermaid
sequenceDiagram
    %% Styling
    autonumber
    
    participant U as 👤 User
    participant MW as 🖥️ MainWindow
    participant GSH as ⚙️ GenerateSkeletonHandler<br/>(QThread)
    participant P2P as 🎨 pix2pix_inference_script<br/>(Subprocess)
    participant M as 🧠 Models & Data<br/>(PyTorch)
    participant FS as 💾 File System
    participant GPU as ⚡ GPU/CUDA

    rect rgb(230, 245, 255)
        Note over U,GPU: Initialization Phase
        U->>+MW: Click "Generate Skeleton"
        MW->>+GSH: generate_skeleton(input_dir)
        GSH->>GSH: Validate input directory
    end

    rect rgb(245, 230, 255)
        Note over GSH,GPU: Processing Phase
        GSH->>+P2P: spawn_subprocess(args)<br/>[dataroot, results_dir, gpu_ids]
        P2P->>+GPU: Check CUDA availability
        GPU-->>-P2P: Device status
        P2P->>+M: create_dataset()<br/>create_model()<br/>model.setup(opt)
        M->>GPU: Load model weights<br/>Initialize tensors
        
        loop For each image
            P2P->>M: model.set_input(data)
            M->>GPU: Forward pass
            GPU-->>M: Generated skeleton
            M-->>P2P: Post-processed image
            P2P->>FS: Save *_fake.png
            P2P-->>GSH: Progress update (stdout)
        end
    end

    rect rgb(230, 255, 230)
        Note over P2P,MW: Completion Phase
        P2P->>FS: Generate index.html
        P2P-->>-GSH: Process complete (exit 0)
        GSH-->>-MW: finished(results_dir)
        MW->>MW: Update UI status
        MW->>IM: load_results(results_dir)
        deactivate MW
    end
```

### 🎭 Mask Generation Workflow (ResNet CNN)

```mermaid
sequenceDiagram
    %% Styling
    autonumber
    
    participant U as 👤 User
    participant MW as 🖥️ MainWindow
    participant MGH as ⚙️ MaskGenerationHandler
    participant TH as 🔄 MaskGenerationThread<br/>(QThread)
    participant RES as 🧠 ResNetSkeleton<br/>(PyTorch)
    participant FS as 💾 File System
    participant GPU as ⚡ GPU/CUDA

    rect rgb(230, 245, 255)
        Note over U,GPU: Initialization
        U->>+MW: Click "Generate ML Masks"
        MW->>+MGH: generate_masks(input_dir)
        MGH->>MGH: Create output mask/ directory
        MGH->>+TH: start(input_dir, mask_dir)
    end

    rect rgb(245, 230, 255)
        Note over TH,GPU: Model Loading
        TH->>+RES: Load model weights
        RES->>GPU: model.to(device)
        GPU-->>RES: Model ready
        RES-->>-TH: Model loaded
    end

    rect rgb(255, 245, 230)
        Note over TH,FS: Batch Processing
        TH->>FS: Enumerate images
        FS-->>TH: Image list
        
        loop For each image
            TH->>FS: Load image
            TH->>TH: Preprocess<br/>(resize, normalize)
            TH->>RES: model(image_tensor)
            RES->>GPU: Forward pass
            GPU-->>RES: Predicted mask
            RES-->>TH: Binary mask
            TH->>TH: Post-process<br/>(threshold, clean)
            TH->>FS: Save mask/*.png
            TH-->>MGH: progress_update(%)
        end
    end

    rect rgb(230, 255, 230)
        Note over TH,MW: Completion
        TH-->>-MGH: finished(mask_dir)
        MGH-->>-MW: masks_generated signal
        MW->>MW: Enable overlay options
        deactivate MW
    end
```

### 📏 Root Length Calculation & Visualization

```mermaid
sequenceDiagram
    %% Styling
    autonumber
    
    participant U as 👤 User
    participant MW as 🖥️ MainWindow
    participant SKH as 🦴 SkeletonHandler
    participant RLC as 📏 RootLengthCalculator<br/>(Thread)
    participant FS as 💾 File System
    participant RLV as 📊 RootLengthVisualization
    participant DP as 🔄 DataProcessor
    participant DASH as 🎯 DashApp<br/>(Web Server)
    participant WEB as 🌐 QWebEngineView

    rect rgb(230, 245, 255)
        Note over U,FS: Length Calculation
        U->>+MW: Click "Calculate Root Length"
        MW->>+SKH: calculate_root_length()
        SKH->>FS: Find skeleton images<br/>(*_fake.png)
        FS-->>SKH: Image paths
        SKH->>+RLC: start(image_map, params)
        
        loop For each skeleton
            RLC->>RLC: Skeletonize image
            RLC->>RLC: Extract centerline
            RLC->>RLC: Calculate physical length<br/>(pixels → mm)
            RLC->>RLC: Compute metrics<br/>(total, primary, lateral)
        end
        
        RLC->>FS: Write root_lengths.csv
        RLC-->>-SKH: finished(csv_path)
        SKH-->>-MW: calculation_complete
    end

    rect rgb(245, 230, 255)
        Note over U,WEB: Visualization
        U->>MW: Click "Visualize Root Length"
        MW->>+RLV: show_visualization(csv_path)
        RLV->>+DP: load_data(csv_path)
        DP->>FS: Read CSV
        DP->>DP: Clean & validate data
        DP-->>-RLV: Processed DataFrame
        
        RLV->>+DASH: start_server(data, port)
        DASH->>DASH: Create layout
        DASH->>DASH: Register callbacks
        DASH->>DASH: Generate figures<br/>(scatter, box, histogram)
        DASH-->>-RLV: Server running
        
        RLV->>+WEB: load(http://localhost:port)
        WEB-->>-RLV: Page loaded
        RLV-->>MW: Widget ready
        deactivate RLV
        deactivate MW
    end
```

---

## 🔀 Data Flow Architecture

```mermaid
flowchart LR
    %% Styling
    classDef input fill:#dbeafe,stroke:#3b82f6,stroke-width:2px
    classDef process fill:#d1fae5,stroke:#10b981,stroke-width:2px
    classDef output fill:#fef3c7,stroke:#f59e0b,stroke-width:2px
    classDef optional fill:#f3f4f6,stroke:#6b7280,stroke-width:1px,stroke-dasharray: 5 5

    %% Input Stage
    subgraph INPUT["📥 Input Stage"]
        raw["📷 Raw Images<br/>(PNG/JPG)"]:::input
        meta["📋 Metadata<br/>(optional)"]:::optional
    end

    %% Processing Pipeline
    subgraph PIPELINE["⚙️ Processing Pipeline"]
        direction TB
        
        subgraph MASK_PROC["Mask Processing"]
            man_mask["✏️ Manual Masks<br/>(User Drawn)"]:::process
            ml_mask["🤖 ML Masks<br/>(ResNet)"]:::process
            mask_merge["🔄 Mask Merge<br/>&amp; Cleanup"]:::process
        end
        
        subgraph SKEL_PROC["Skeleton Processing"]
            p2p["🎨 Pix2Pix<br/>GAN"]:::process
            skel_post["🦴 Skeleton<br/>Post-Process"]:::process
        end
        
        subgraph ANALYSIS["Analysis"]
            morph["📐 Morphometry<br/>Analysis"]:::process
            stats["📊 Statistical<br/>Processing"]:::process
        end
    end

    %% Output Stage
    subgraph OUTPUT["📤 Output Stage"]
        masks["🎭 Binary Masks<br/>mask/*.png"]:::output
        skels["🦴 Skeletons<br/>*_fake.png"]:::output
        csv["📊 Metrics<br/>root_lengths.csv"]:::output
        viz["📈 Visualizations<br/>Interactive Dash"]:::output
        reports["📄 Reports<br/>(HTML/PDF)"]:::output
    end

    %% Connections
    raw --> man_mask
    raw --> ml_mask
    meta -.-> ANALYSIS
    
    man_mask --> mask_merge
    ml_mask --> mask_merge
    
    raw --> p2p
    mask_merge -.->|optional overlay| p2p
    p2p --> skel_post
    
    skel_post --> morph
    morph --> stats
    
    mask_merge --> masks
    skel_post --> skels
    stats --> csv
    csv --> viz
    stats --> reports
```

---

## 💻 Hardware Resource Management

```mermaid
flowchart TB
    %% Styling
    classDef cpu fill:#e0e7ff,stroke:#6366f1,stroke-width:2px
    classDef gpu fill:#fee2e2,stroke:#ef4444,stroke-width:2px
    classDef memory fill:#dcfce7,stroke:#22c55e,stroke-width:2px
    classDef task fill:#fef3c7,stroke:#f59e0b,stroke-width:2px

    %% Resource Pool
    subgraph RESOURCES["🖥️ System Resources"]
        direction LR
        
        subgraph CPU_POOL["CPU Resources"]
            CPU1["Core 1-4<br/>UI & Control"]:::cpu
            CPU2["Core 5-8<br/>Processing"]:::cpu
        end
        
        subgraph GPU_POOL["GPU Resources"]
            GPU1["CUDA Cores<br/>ML Inference"]:::gpu
            VRAM["VRAM<br/>Model Storage"]:::gpu
        end
        
        subgraph MEM_POOL["Memory"]
            RAM["System RAM<br/>Data Buffers"]:::memory
            CACHE["Cache<br/>Image Cache"]:::memory
        end
    end

    %% Task Allocation
    subgraph TASKS["📋 Task Allocation"]
        UI_TASK["🎨 UI Rendering<br/>Event Handling"]:::task
        MASK_TASK["🎭 Mask Generation<br/>ResNet Inference"]:::task
        SKEL_TASK["🦴 Skeleton Generation<br/>Pix2Pix Inference"]:::task
        CALC_TASK["📏 Length Calculation<br/>Morphometry"]:::task
        VIZ_TASK["📊 Visualization<br/>Dash Server"]:::task
    end

    %% Allocations
    UI_TASK --> CPU1
    CALC_TASK --> CPU2
    VIZ_TASK --> CPU2
    
    MASK_TASK --> GPU1
    MASK_TASK --> VRAM
    SKEL_TASK --> GPU1
    SKEL_TASK --> VRAM
    
    UI_TASK --> RAM
    MASK_TASK --> RAM
    SKEL_TASK --> RAM
    
    UI_TASK --> CACHE
```

---

## 📁 File Structure & Artifacts

### Directory Structure

```
Root-Mask-and-Skeletons/
│
├── main.py                              # Application entry point
│
├── 📱 app/gui/                          # PyQt6 interface
│   ├── main_window.py                   # Main window controller
│   ├── display_controller.py            # Library canvas (single, overlay, side by side)
│   ├── image_manager.py                 # File management
│   ├── correct/                         # The Correct stage: mask + skeleton on one canvas
│   │   ├── editor.py                    # CorrectEditor: composes the parts below
│   │   ├── session.py                   # Open, save, the prompt, one undo timeline (Qt-free)
│   │   ├── tool_router.py               # Canvas input to the active layer
│   │   ├── layer.py                     # EditLayer contract (Qt-free)
│   │   ├── mask_layer.py                # Mask layer (+ mask_tools.py, mask_document.py)
│   │   ├── skeleton_layer.py            # Skeleton layer (+ skeleton_document.py)
│   │   └── controls.py, layer_panel.py  # Rail groups, dock, polyline prompt; Layers panel
│   ├── gt_tracing_interface.py          # Trace GT, on raster_editor_base.py
│   ├── editor_canvas.py                 # EditorCanvasView (shared by both editors)
│   ├── editor_chrome.py                 # Rail, dock, Enhance popover, chip, zoom cluster
│   ├── paint_canvas.py                  # Editable mask / GT canvas over PatchHistory
│   ├── editor_prompts.py                # Save / Discard / Cancel prompts
│   └── canvas_layers/                   # Photo, raster and skeleton render items
│
├── ⚙️ app/handlers/
│   ├── skeleton_handler.py              # Skeleton operations coordinator
│   ├── mask_generation_handler.py       # Mask generation coordinator
│   └── generate_skeleton_handler.py     # Skeleton generation run
│
├── 🤖 app/inference/, app/mask_model/
│   ├── inference/skeleton_inference.py  # Pix2Pix skeleton inference
│   ├── inference/mask_inference.py      # Mask inference
│   ├── inference/metrics.py             # Length and area primitives
│   ├── inference/root_length_inference_handler.py  # Morphometry calculator
│   └── mask_model/model.py              # Mask segmentation network
│
├── 🧮 app/imaging/pyramid.py            # Image pyramid of smooth zoom (Qt-free)
├── ↩️ app/editing/                      # Undo history and the cross-layer journal (stdlib)
│
├── 📊 app/visualization/, app/data_processing/
│   ├── visualization/root_length_visulization.py  # Qt-Dash integration
│   ├── visualization/dash_app.py        # Dash application
│   └── data_processing/data_processor.py  # Data processing pipeline
│
└── 📤 <project>/                        # Outputs, next to the images
    ├── mask/<stem>.png                  # Masks (generated, or corrected in Correct)
    ├── mask/root_areas.csv              # Root area per mask
    ├── skeletons/<stem>_fake.png        # Generated skeletons
    ├── skeletons/root_lengths.csv       # Root length per skeleton
    ├── skeleton_corrections/<stem>_skeleton.png  # Skeletons corrected in Correct
    └── gt/                              # Ground truth (Trace GT)
```

### Runtime Artifacts

| Artifact Type | Location | Format | Description |
|--------------|----------|--------|-------------|
| **Input Images** | User specified | PNG/JPG | Original root images |
| **Binary Masks** | `mask/*.png` | 8-bit PNG (generated), RGBA PNG (corrected) | Segmentation masks |
| **Skeleton Images** | `*_fake.png` | RGB PNG | Skeletonized roots |
| **Corrected Skeletons** | `skeleton_corrections/*_skeleton.png` | 8-bit PNG | Skeletons corrected in *Correct* (not measured) |
| **Metrics CSV** | `root_lengths.csv` | CSV | Morphometric data |
| **HTML Reports** | `index.html` | HTML | Visual results browser |
| **Dash Server** | `localhost:8050` | Web | Interactive dashboard |

---

## 🔧 Configuration & Dependencies

### System Requirements

#### Minimum Requirements
- **OS**: Windows 10/11, Ubuntu 20.04+, macOS 11+
- **CPU**: Intel i5 or AMD Ryzen 5 (4+ cores)
- **RAM**: 8 GB
- **GPU**: NVIDIA GTX 1060 (6GB VRAM) for ML acceleration
- **Storage**: 10 GB available space
- **Python**: 3.8+

#### Recommended Requirements
- **CPU**: Intel i7/i9 or AMD Ryzen 7/9 (8+ cores)
- **RAM**: 16-32 GB
- **GPU**: NVIDIA RTX 3060 or better (8+ GB VRAM)
- **Storage**: 50 GB SSD

### Key Dependencies

```python
# Core Framework
PyQt6>=6.4.0          # GUI framework
numpy>=1.21.0         # Numerical computing
opencv-python>=4.6.0  # Image processing
scikit-image>=0.19.0  # Advanced image processing

# Machine Learning
torch>=1.13.0         # Deep learning framework
torchvision>=0.14.0   # Computer vision models
cuda-toolkit>=11.7    # GPU acceleration (optional)

# Visualization
plotly>=5.11.0        # Interactive plots
dash>=2.7.0           # Web dashboards
pandas>=1.5.0         # Data manipulation

# Web Integration
PyQtWebEngine>=6.4.0  # Web content display
flask>=2.2.0          # Web server (for Dash)
```

---

## 🚀 Performance Optimization Strategies

### GPU Utilization
- **Model Persistence**: ResNet model loaded once and kept in memory
- **Batch Processing**: Process multiple images in single GPU transfer
- **Mixed Precision**: FP16 computation where supported
- **Memory Management**: Automatic VRAM cleanup after subprocess completion

### CPU Optimization
- **Multithreading**: Separate threads for UI, processing, and visualization
- **Async Operations**: Non-blocking file I/O and network requests
- **Cache Strategy**: LRU cache for frequently accessed images
- **Lazy Loading**: Load data only when needed

### Memory Management
- **Image Pyramids**: Multi-resolution representation for large images
- **Streaming Processing**: Process large datasets in chunks
- **Garbage Collection**: Explicit cleanup after major operations
- **Resource Pooling**: Reuse objects and buffers where possible

---

## 📚 API Reference

### Core Classes

#### MainWindow
```python
class MainWindow(QMainWindow):
    """Main application window coordinating all operations"""
    
    def load_folder(self, folder_path: str) -> None:
        """Load images from specified folder"""
    
    def generate_masks(self) -> None:
        """Trigger ML mask generation"""
    
    def generate_skeletons(self) -> None:
        """Trigger Pix2Pix skeleton generation"""
    
    def calculate_root_length(self) -> None:
        """Compute morphometric measurements"""
    
    def visualize_results(self) -> None:
        """Launch interactive dashboard"""
```

#### ImageManager
```python
class ImageManager:
    """Manages image loading and caching"""
    
    def load_images(self, directory: str) -> List[str]:
        """Load all images from directory"""
    
    def get_processed_path(self, image_name: str) -> Optional[str]:
        """Get path to processed version of image"""
    
    def cache_image(self, path: str, image: np.ndarray) -> None:
        """Cache image in memory"""
```

#### MaskGenerationHandler
```python
class MaskGenerationHandler(QObject):
    """Coordinates mask generation using ResNet model"""
    
    finished = pyqtSignal(str)  # Emits output directory
    progress = pyqtSignal(int)  # Emits progress percentage
    
    def generate_masks(self, input_dir: str, output_dir: str) -> None:
        """Generate masks for all images in input directory"""
```

---

## 🔒 Security & Error Handling

### Security Measures
- **Input Validation**: Sanitize file paths and user inputs
- **Sandboxing**: Run inference in isolated subprocesses
- **Resource Limits**: Prevent memory exhaustion with limits
- **Access Control**: Restrict file system access to designated directories

### Error Handling
- **Graceful Degradation**: Fall back to CPU if GPU unavailable
- **Recovery Mechanisms**: Auto-save and recovery for long operations
- **User Feedback**: Clear error messages and suggested actions

---

## 📈 Future Enhancements

### Planned Features
- 🌐 **Cloud Processing**: Distributed processing for large datasets
- 🤝 **Collaboration Tools**: Multi-user annotation and review
- 📱 **Mobile Support**: Companion mobile app for field data collection
- 🧪 **Extended Metrics**: Additional morphological measurements
- 🔄 **Real-time Processing**: Stream processing for video input
- 🎯 **Custom Models**: User-trainable models for specific root types

### Technical Roadmap
1. **Q1 2025**: Implement distributed processing architecture
2. **Q2 2025**: Add real-time collaboration features
3. **Q3 2025**: Release mobile companion app
4. **Q4 2025**: Deploy cloud-based processing service

---

## 📞 Support & Documentation

- **Documentation**: Comprehensive user and developer guides
- **API Reference**: Detailed API documentation with examples
- **Tutorials**: Step-by-step guides for common workflows
- **Community Forum**: Active user community for support
- **Issue Tracker**: GitHub issues for bug reports and features
- **Contact**: support@rootanalysis.org

---

*Last Updated: September 2026 (the Correct stage: editor node, composition, file tree)*
*Version: 3.0.0*
*Architecture Document Revision: 3.2*