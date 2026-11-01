"""GUI 煙霧測試：用 offscreen 模式跑，不會跳出視窗。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.ui.entry_dialog import EntryDialog
from reporttool.ui.main_window import MainWindow

DATE = "2026-11-02"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_paste_screenshot_and_drop_file(qapp, tmp_path):
    db = Database(tmp_path / "data")
    db.save_entry(Entry(None, DATE, "09:00", "舊紀錄", "done", ["專案A"]))
    dlg = EntryDialog(db, DATE)

    shot = QImage(300, 200, QImage.Format_RGB32)
    shot.fill(QColor("green"))
    mime = QMimeData()
    mime.setImageData(shot)
    dlg.text.insertFromMimeData(mime)  # 等同 Ctrl+V 貼上截圖

    file_png = tmp_path / "drop.png"
    shot.save(str(file_png))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file_png))])
    dlg.text.insertFromMimeData(mime)  # 等同拖曳檔案

    assert len(dlg.strip.items) == 2
    dlg.strip.items[1].include_box.setChecked(False)
    dlg.text.setPlainText("修登入頁")
    dlg.tag_edit.setText("#專案A #bug")
    dlg.status_group.buttons()[2].click()  # 卡住
    dlg.save()

    saved = [e for e in db.entries_for_date(DATE) if e.text == "修登入頁"][0]
    assert saved.status == "blocked" and saved.tags == ["專案A", "bug"]
    assert [i.include for i in saved.images] == [True, False]
    assert all(db.abs_path(i.path).exists() for i in saved.images)


def test_empty_text_is_not_saved(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    db = Database(tmp_path)
    EntryDialog(db, DATE).save()
    assert db.entries_for_date(DATE) == []


def test_main_window_lists_entries(qapp, tmp_path):
    db = Database(tmp_path / "data")
    db.save_entry(Entry(None, DATE, "09:00", "a", "done"))
    settings = load_settings(tmp_path / "config")
    win = MainWindow(db, settings)
    from PySide6.QtCore import QDate
    win.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    assert win.tree.topLevelItemCount() == 1
