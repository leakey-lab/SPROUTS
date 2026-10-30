# -*- mode: python -*-
"""PyInstaller spec for the SPROUTS Windows app (one-folder bundle: dist/SPROUTS/SPROUTS.exe).

Build with the project's interpreter, from any folder:

    venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean scripts\\build_executable.spec

scripts\\build.ps1 and scripts\\build.bat do exactly that and fail loudly on errors.
Every path is anchored at the repository root by scripts/build_support.py, which also
lists the bundled data files, writes build_info.json (what was built, from which commit)
and builds the Windows version resource from app.__version__. The torch/CUDA DLLs are
collected by PyInstaller's torch hook (no manual copying).
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # noqa: F821 -- SPECPATH is set by PyInstaller
sys.path[:0] = [SPECPATH, ROOT]  # noqa: F821 -- build_support, and the app package it reads

import build_support  # noqa: E402

datas, warnings = build_support.resolve_datas()
for warning in warnings:
    print(f"WARNING: {warning}")
build_info = build_support.write_build_info(
    os.path.join(workpath, build_support.BUILD_INFO_FILENAME),  # noqa: F821 -- set by PyInstaller
    build_support.collect_build_info(),
)
datas.append((str(build_info), "."))

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    datas=datas,
    # app.* modules may be imported lazily (PEP 562 / importlib); bundle all of them.
    hiddenimports=build_support.app_modules() + ["resources.resource_utils"],
    # Present in dev environments but never imported by the app: keep them out of the bundle.
    excludes=["tkinter", "matplotlib", "pyqtgraph", "torchaudio", "IPython", "jedi"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=build_support.app.APP_NAME,
    console=False,  # GUI app; set True to see a traceback when debugging a frozen build
    upx=False,  # CUDA libraries do not compress well and UPX can corrupt them
    icon=os.path.join(ROOT, "resources", "app_icon.ico"),
    version=build_support.windows_version_info(),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=build_support.app.APP_NAME,
)
