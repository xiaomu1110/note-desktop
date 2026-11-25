# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 note-desktop 打成单个 exe。

用法：
    pyinstaller note-desktop.spec --noconfirm

产物：dist/note-desktop.exe
"""

from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH)
ASSETS = ROOT / "src" / "desktop_memo" / "assets"
ICON = ASSETS / "memo.ico"

a = Analysis(
    [str(ROOT / "src" / "desktop_memo" / "entry_exe.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(ICON), "desktop_memo/assets")],
    hiddenimports=[
        "desktop_memo.mcp_server",
        "desktop_memo.api",
        "desktop_memo.tray",
        "desktop_memo.win_layer",
        "desktop_memo.widget",
        # 标准库但 PyInstaller 有时会漏
        "sqlite3",
        "tkinter",
        "tkinter.font",
        "_tkinter",
        "ctypes.wintypes",
        "winreg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 砍掉用不到的大块依赖，压体积。
    # 注意：email / http.client / xml 是标准库 http.server 的依赖链，
    # 不能排除——排掉会 ModuleNotFoundError（踩过）。
    excludes=[
        "numpy", "pandas", "matplotlib", "scipy", "PIL",
        "pytest", "pydoc_data", "test", "unittest",
        "pydoc", "doctest", "sqlite3.test",
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="note-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI 程序，双击不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)