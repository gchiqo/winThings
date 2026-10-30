# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for "Read Selected" (reader/app.py)
#
# Run from the repo root:
#   pyinstaller reader/reader.spec
#
# Output: dist/reader/  — the entire folder becomes the MSIX package content.
# The PyInstaller one-folder layout keeps piper.exe and voices/ next to
# reader.exe so BUNDLE_DIR in bootstrap.py resolves them correctly.

import os

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))   # …/winThings/reader/
REPO_DIR = os.path.dirname(SPEC_DIR)                 # …/winThings/

piper_dir  = os.path.join(SPEC_DIR, 'piper')
voices_dir = os.path.join(SPEC_DIR, 'voices')
assets_dir = os.path.join(REPO_DIR, 'assets')

# reader/kokoro is deliberately left out: Kokoro isn't shipped in the Store
# build yet, and without its files settings.py hides the Engine picker.
datas = []
if os.path.isdir(piper_dir):
    datas.append((piper_dir, 'piper'))
if os.path.isdir(voices_dir):
    datas.append((voices_dir, 'voices'))

# customtkinter ships theme JSON/image files that PyInstaller doesn't auto-detect.
try:
    import customtkinter
    ctk_dir = os.path.dirname(customtkinter.__file__)
    datas.append((ctk_dir, 'customtkinter'))
except ImportError:
    pass

a = Analysis(
    [os.path.join(SPEC_DIR, 'app.py')],
    pathex=[REPO_DIR, SPEC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # pywin32
        'win32api', 'win32con', 'win32event', 'win32gui', 'win32process',
        'win32clipboard', 'winerror', 'pywintypes',
        # UI Automation via comtypes
        'comtypes', 'comtypes.client', 'comtypes.gen',
        # tray icon
        'pystray._win32',
        # Pillow / tkinter integration
        'PIL._tkinter_finder',
        # CustomTkinter
        'customtkinter', 'darkdetect',
        # audio
        'sounddevice', '_sounddevice',
        # system info
        'psutil', 'psutil._pswindows',
        # numeric
        'numpy', 'numpy.lib.format',
        # winrt: Start with Windows via StartupTask in the MSIX
        'winrt.windows.applicationmodel', 'winrt.windows.foundation',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test', 'unittest', 'distutils', 'setuptools',
              'email', 'html', 'http', 'xml', 'xmlrpc'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='reader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no black console window — same as pythonw.exe
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(assets_dir, 'reader.ico'),
    version=os.path.join(SPEC_DIR, 'version_info.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='reader',          # output goes to dist/reader/
)
