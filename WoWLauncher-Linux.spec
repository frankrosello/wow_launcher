# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path(SPECPATH)

datas = []

for folder in [
    "backgrounds",
    "fonts",
    "logos",
    "screenshots",
]:
    path = project_dir / folder
    if path.exists():
        datas.append((str(path), folder))

for filename in [
    "wow_logo.png",
    "wowicon.png",
    "image.png",
]:
    path = project_dir / filename
    if path.exists():
        datas.append((str(path), "."))

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

pyz = PYZ(a.pure)

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
    upx=False,
    console=False,
)
