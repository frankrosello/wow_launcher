# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path(SPECPATH)
icon_path = project_dir / "wowicon.icns"

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
    [],
    exclude_binaries=True,
    name="WoWLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

app = BUNDLE(
    exe,
    a.binaries,
    a.datas,
    name="WoWLauncher.app",
    icon=str(icon_path),
    bundle_identifier="com.frankrosello.wowlauncher",
    info_plist={
        "CFBundleName": "WoW Launcher",
        "CFBundleDisplayName": "WoW Launcher",
        "CFBundleIdentifier": "com.frankrosello.wowlauncher",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
    },
)
