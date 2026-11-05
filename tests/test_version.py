"""版本號。"""
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from reporttool import __version__
from reporttool.config import load_settings
from reporttool.db import Database
from reporttool.ui.main_window import MainWindow
from tools.make_version_file import version_info

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver_and_in_changelog():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert f"## {__version__}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_window_title_shows_version(tmp_path):
    QApplication.instance() or QApplication([])
    win = MainWindow(Database(tmp_path / "data"), load_settings(tmp_path / "config"))
    assert win.windowTitle() == f"工作回報工具 v{__version__}"


def test_exe_version_resource():
    # PyInstaller 只有跑過 build.bat 才會安裝
    names = vars(pytest.importorskip("PyInstaller.utils.win32.versioninfo"))
    info = eval(version_info("1.2.3"), dict(names))
    assert info.ffi.fileVersionMS == (1 << 16) | 2 and info.ffi.fileVersionLS == 3 << 16
    assert "'ProductVersion', '1.2.3'" in version_info("1.2.3")
