# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for strokespec.
#
# Build a standalone editor executable:
#   uv run pyinstaller strokespec.spec
# Outputs: dist/uffy-editor(.exe) and dist/uffy(.exe)
#
# The compiler (fontmake/gftools) still needs to be available at runtime as an external
# command (it is a separate CLI), so the packaged GUI shell-uses whatever is on PATH.

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = collect_submodules("strokespec")

a = Analysis(
    ["src/strokespec/editor/editor_main.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hidden + ["PySide6.QtSvg"],
    hookspath=[],
    excludes=["PySide6.QtWebEngine", "PySide6.QtMultimedia", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_editor = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="uffy-editor",
    console=False,          # GUI app
    disable_windowed_traceback=False,
    icon=None,
)
coll = COLLECT(exe_editor, a.binaries, a.datas, strip=False, upx=True, name="uffy-editor")

# A tiny console build used by `uffy build/preview/refs` could be added here too, but for
# the common case the editor is the packaged artifact. `uffy` is normally run from source.
