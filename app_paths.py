"""Writable user state is independent of the executable and extraction paths."""
import os
import shutil
import sys
from pathlib import Path

VERSION = '1.0.0'
SOURCE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else SOURCE_DIR


def data_dir():
    folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'GlassDash'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def state_path(name):
    target = data_dir() / name
    if not target.exists():
        legacy = APP_DIR / name
        if legacy.is_file() and legacy != target:
            try:
                shutil.copy2(legacy, target)
            except OSError:
                pass
    return target


def startup_command():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --tray'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    return f'"{pythonw}" "{SOURCE_DIR / "glassdash.py"}" --tray'
