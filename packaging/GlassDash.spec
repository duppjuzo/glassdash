# Build with the Python interpreter matching the target architecture.
import struct
import sys
from pathlib import Path

root = Path(SPECPATH).parent
with open(sys.executable, 'rb') as stream:
    stream.seek(0x3c)
    offset = struct.unpack('<I', stream.read(4))[0]
    stream.seek(offset + 4)
    machine = struct.unpack('<H', stream.read(2))[0]
arch = {0xAA64: 'arm64', 0x8664: 'x64'}[machine]
name = 'GlassDash-' + arch

a = Analysis(
    [str(root / 'glassdash.py')], pathex=[str(root)], binaries=[], datas=[],
    hiddenimports=['comtypes.client', 'comtypes.stream', 'comtypes.persist'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pandas', 'scipy', 'IPython', 'pytest',
              'PySide6.QtWebEngineCore', 'PySide6.Qt3DCore', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.QtPdf', 'PySide6.QtMultimedia'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name,
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)
