# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path(SPECPATH)

datas = [
    (str(project_dir / "assets"), "assets"),
]

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
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

app = BUNDLE(
    exe,
    name="WoWLauncher.app",
    icon=str(project_dir / "assets" / "wow.icns"),
    bundle_identifier="com.frankrosello.wowlauncher",
    version="1.0.0",
    info_plist={
        "CFBundleName": "WoW Launcher",
        "CFBundleDisplayName": "WoW Launcher",
        "CFBundleIdentifier": "com.frankrosello.wowlauncher",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSPrincipalClass": "NSApplication",
        "NSAppleScriptEnabled": False,
    },
)
