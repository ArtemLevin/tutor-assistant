# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
datas = [
    (str(ROOT / "config" / "app.example.yaml"), "config"),
    (str(ROOT / "build" / "build-info.json"), "."),
]
binaries = []
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "sounddevice",
    "soundcard",
    "soundfile",
    "yaml",
    "sqlite3",
]

for package in ("faster_whisper", "ctranslate2", "tokenizers", "onnxruntime"):
    try:
        hiddenimports += collect_submodules(package)
        datas += collect_data_files(package)
        binaries += collect_dynamic_libs(package)
    except Exception:
        pass

analysis = Analysis(
    [str(ROOT / "scripts" / "windows_entrypoint.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "ruff", "IPython", "tkinter", "torch", "transformers"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TutorAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
distribution = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="TutorAssistant",
)
