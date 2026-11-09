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
    dlg.status_group.buttons()[2].click()  # 等待中
    dlg.save()

    saved = [e for e in db.entries_for_date(DATE) if e.text == "修登入頁"][0]
    assert saved.status == "waiting" and saved.tags == ["專案A", "bug"]
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
    assert len(win.entry_items()) == 1


def test_new_entry_defaults_to_last_category_and_tags(qapp, tmp_path):
    db = Database(tmp_path)
    cid = db.categories()[2][0]
    db.save_entry(Entry(None, DATE, "09:00", "a", "done", ["專案A"], category_id=cid))
    dlg = EntryDialog(db, DATE)
    assert dlg.category_picker.category_id() == cid
    assert dlg.tag_edit.tags() == ["專案A"]


def test_follow_up_links_to_previous(qapp, tmp_path):
    db = Database(tmp_path)
    cid = db.categories()[0][0]
    old_id = db.save_entry(Entry(None, "2026-11-01", "09:00", "修登入頁", "waiting", ["專案A"], category_id=cid))
    dlg = EntryDialog(db, DATE, follow_up_of=db.get_entry(old_id))
    assert dlg.windowTitle().startswith("追加進度")
    assert dlg.status_group.checkedButton().property("status") == "waiting"
    assert dlg.text.toPlainText() == ""
    dlg.text.setPlainText("DBA 已開權限")
    dlg.status_group.buttons()[0].click()
    dlg.save()
    new = db.entries_for_date(DATE)[0]
    assert new.carried_from == old_id and new.category_id == cid and new.tags == ["專案A"]


def test_review_copies_markdown_and_manage_dialog(qapp, tmp_path, monkeypatch):
    import reporttool.ui.main_window as mw
    from reporttool.ui.manage_dialog import ManageDialog

    monkeypatch.setattr(mw.os, "startfile", lambda *_: None)
    db = Database(tmp_path / "data")
    db.save_entry(Entry(None, DATE, "09:00", "寫週報", "done"))
    settings = load_settings(tmp_path / "config")
    settings.output_dir = tmp_path / "out"
    win = MainWindow(db, settings)
    from PySide6.QtCore import QDate
    win.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    win.review()
    assert "寫週報" in QApplication.clipboard().text()

    win.select_category(mw.UNCATEGORIZED)
    assert len(win.entry_items()) == 1
    win.select_category(db.categories()[0][0])  # 第一個分類，沒有紀錄
    assert len(win.entry_items()) == 0

    ManageDialog(db, win)  # 能建立即可


def test_backup_export_and_import(qapp, tmp_path, monkeypatch):
    import reporttool.ui.main_window as mw
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    zip_path = str(tmp_path / "backup.zip")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (zip_path, ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (zip_path, ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    a = Database(tmp_path / "a")
    a.save_entry(Entry(None, DATE, "09:00", "要備份的", "done", ["專案A"]))
    settings = load_settings(tmp_path / "config")
    settings.output_dir = tmp_path / "out"
    mw.MainWindow(a, settings).export_backup()

    b = Database(tmp_path / "b")
    win = mw.MainWindow(b, settings)
    win.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    win.import_backup()
    assert len(win.entry_items()) == 1


def test_closing_main_window_really_closes(qapp, tmp_path):
    """不再縮到系統匣：關掉主視窗就關閉（程式在最後一個視窗關閉時結束）。"""
    win = MainWindow(Database(tmp_path / "data"), load_settings(tmp_path / "config"))
    win.show()
    assert win.close() and not win.isVisible()
    assert qapp.quitOnLastWindowClosed()
