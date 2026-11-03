"""日期範圍整理。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image as PILImage
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QDialog

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.report import generate_range_report
from reporttool.ui import main_window
from reporttool.ui.main_window import MainWindow
from reporttool.ui.range_dialog import RangeDialog, month_range, week_range


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def add(db, date, time, text, images=()):
    return db.save_entry(Entry(None, date, time, text, "done", images=list(images)))


def test_range_report_groups_by_day_and_numbers_images_across_days(tmp_path):
    db = Database(tmp_path / "data")
    PILImage.new("RGB", (10, 10)).save(tmp_path / "a.png")
    add(db, "2026-10-30", "09:00", "範圍外")
    add(db, "2026-11-02", "14:00", "週一下午", [db.import_image_file(tmp_path / "a.png", "2026-11-02")])
    add(db, "2026-11-02", "09:00", "週一早上")
    add(db, "2026-11-04", "10:00", "週三", [db.import_image_file(tmp_path / "a.png", "2026-11-04")])
    add(db, "2026-11-09", "10:00", "範圍外")

    folder = generate_range_report(db, "2026-11-02", "2026-11-08", tmp_path / "out", "指示")
    assert folder.name == "report_2026-11-02_to_2026-11-08"
    md = (folder / "report.md").read_text(encoding="utf-8")
    assert md == (
        "# 工作回報整理請求（2026-11-02 ~ 2026-11-08）\n\n"
        "## 指示\n指示\n\n"
        "## 期間工作紀錄（共 3 筆，2 天）\n"
        "### 2026-11-02（週一）\n"
        "- [09:00][完成][優先3] 週一早上\n"
        "- [14:00][完成][優先3] 週一下午（![附圖1]）\n\n"
        "### 2026-11-04（週三）\n"
        "- [10:00][完成][優先3] 週三（![附圖2]）\n"
        "\n[附圖1]: <01_週一下午.png>\n[附圖2]: <02_週三.png>\n"
    )
    assert sorted(p.name for p in folder.iterdir()) == ["01_週一下午.png", "02_週三.png", "report.md"]


def test_empty_range(tmp_path):
    folder = generate_range_report(Database(tmp_path / "data"), "2026-11-02", "2026-11-03", tmp_path / "out", "x")
    assert "（這段期間沒有紀錄）" in (folder / "report.md").read_text(encoding="utf-8")


def test_week_and_month_ranges():
    wed = QDate(2026, 11, 4)
    assert week_range(wed) == (QDate(2026, 11, 2), QDate(2026, 11, 8))
    assert week_range(wed, 1) == (QDate(2026, 10, 26), QDate(2026, 11, 1))
    assert month_range(wed, 1) == (QDate(2026, 10, 1), QDate(2026, 10, 31))
    assert month_range(QDate(2026, 1, 15), 1) == (QDate(2025, 12, 1), QDate(2025, 12, 31))


def test_dialog_defaults_and_swaps_reversed_dates(qapp):
    dlg = RangeDialog(QDate(2026, 11, 4))
    assert dlg.range() == ("2026-11-02", "2026-11-04")  # 本週一到今天
    dlg.set_range(QDate(2026, 11, 10), QDate(2026, 11, 1))
    assert dlg.range() == ("2026-11-01", "2026-11-10")


def test_main_window_range_review(qapp, tmp_path, monkeypatch):
    db = Database(tmp_path / "data")
    add(db, "2026-11-02", "09:00", "a")
    add(db, "2026-11-03", "09:00", "b")
    settings = load_settings(tmp_path / "config")
    settings.output_dir = tmp_path / "out"
    win = MainWindow(db, settings)

    def fake_exec(self):
        self.set_range(QDate(2026, 11, 1), QDate(2026, 11, 7))
        return QDialog.Accepted

    monkeypatch.setattr(RangeDialog, "exec", fake_exec)
    opened = []
    monkeypatch.setattr(main_window.os, "startfile", opened.append)
    win.review_range()
    assert opened == [tmp_path / "out" / "report_2026-11-01_to_2026-11-07"]
    assert QApplication.clipboard().text().startswith("# 工作回報整理請求（2026-11-01 ~ 2026-11-07）")
