# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

base_dir = os.path.abspath(SPECPATH)

datas = []
assets_dir = os.path.join(base_dir, "assets")
if os.path.isdir(assets_dir):
    datas.append((assets_dir, "assets"))

binaries = []
dll_candidates = [
    os.path.join(base_dir, "assets", "bin", "phonoryx_engine.dll"),
    os.path.join(base_dir, "build", "engine", "bin", "Release", "phonoryx_engine.dll"),
]
for dll_path in dll_candidates:
    if os.path.isfile(dll_path):
        binaries.append((dll_path, "."))
        binaries.append((dll_path, os.path.join("assets", "bin")))
        break

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "shiboken6",
    "phonoryx",
    "phonoryx.__main__",
    "phonoryx.engine_bridge",
    "phonoryx.models",
    "phonoryx.models.graph_model",
    "phonoryx.ui",
    "phonoryx.ui.canvas",
    "phonoryx.ui.icons",
    "phonoryx.ui.main_window",
    "phonoryx.ui.panels",
    "phonoryx.ui.theme",
    "phonoryx.ui.widget_helpers",
    "phonoryx.ui.workflow_tabs",
]

icon_path = os.path.join(base_dir, "assets", "icons", "app_icon.ico")

a = Analysis(
    ["main.py"],
    pathex=[base_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Phonoryx",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if os.path.isfile(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Phonoryx",
)
