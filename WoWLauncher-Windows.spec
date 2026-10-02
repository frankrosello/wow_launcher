# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

project_dir = Path(SPECPATH)

# Assets used by the launcher
datas = [
    (str(project_dir / "assets"), "assets"),
]

# Add hidden imports if needed
hiddenimports = [
    "psutil",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "PIL.ImageDraw",
    "PIL.ImageFilter",
    "PIL.ImageOps",
]

a = Analysis(
    [str(project_dir / "main.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(
    a.pure,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WoWLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,

    # Change this to your actual icon
    icon=str(project_dir / "assets" / "wow.ico"),
)
