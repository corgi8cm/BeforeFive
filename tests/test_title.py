"""紀錄標題：資料庫、回報、同步、輸入視窗。"""
import json
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image as PILImage
from PySide6.QtWidgets import QApplication

from reporttool.config import DEFAULT_INSTRUCTIONS
from reporttool.db import Database, Entry
from reporttool.report import generate_report
from reporttool.sync import import_bundle, snapshot
from reporttool.ui.entry_dialog import EntryDialog

DATE = "2026-11-02"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_title_is_saved_and_shown_as_summary(tmp_path):
    db = Database(tmp_path)
    entry_id = db.save_entry(Entry(None, DATE, "09:00", "細節第一行\n第二行", "done", title="登入頁"))
    entry = db.get_entry(entry_id)
    assert entry.title == "登入頁" and entry.summary == "登入頁"
    entry.title = ""
    db.save_entry(entry)
    assert db.get_entry(entry_id).summary == "細節第一行"


def test_old_database_gets_empty_title(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "09:00", "舊紀錄", "done"))
    db.close()
    conn = sqlite3.connect(tmp_path / "reporttool.db")
    conn.execute("ALTER TABLE entries DROP COLUMN title")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    db = Database(tmp_path)
    assert [(e.title, e.text) for e in db.entries_for_date(DATE)] == [("", "舊紀錄")]


def test_report_line_and_image_name_use_title(tmp_path):
    db = Database(tmp_path / "data")
    src = tmp_path / "a.png"
    PILImage.new("RGB", (10, 10)).save(src)
    db.save_entry(Entry(None, DATE, "09:00", "查到原因\n改設定檔", "doing",
                        images=[db.import_image_file(src, DATE)], title="登入 timeout"))
    db.save_entry(Entry(None, DATE, "10:00", "", "done", title="只有標題"))
    folder = generate_report(db, DATE, tmp_path / "out", DEFAULT_INSTRUCTIONS)
    md = (folder / "report.md").read_text(encoding="utf-8")
    assert "- [09:00][進行中] 【登入 timeout】查到原因\n  改設定檔（附圖1：01_登入timeout.png）" in md
    assert "- [10:00][完成] 【只有標題】\n" in md
    assert (folder / "01_登入timeout.png").exists()


def test_bundle_without_title_imports(tmp_path):
    src = Database(tmp_path / "a")
    src.save_entry(Entry(None, DATE, "09:00", "內容", "done"))
    data = snapshot(src)
    for e in data["entries"]:
        del e["title"]  # 舊版匯出的資料包沒有這個欄位
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "bundle.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    dst = Database(tmp_path / "b")
    import_bundle(dst, bundle)
    assert [(e.title, e.text) for e in dst.entries_for_date(DATE)] == [("", "內容")]


def test_dialog_saves_title_only_entry(qapp, tmp_path):
    db = Database(tmp_path)
    dlg = EntryDialog(db, DATE)
    dlg.title_edit.setText("  開會  ")
    dlg.save()
    [entry] = db.entries_for_date(DATE)
    assert (entry.title, entry.text) == ("開會", "")


def test_follow_up_keeps_title(qapp, tmp_path):
    db = Database(tmp_path)
    old_id = db.save_entry(Entry(None, "2026-11-01", "09:00", "a", "doing", title="登入頁"))
    dlg = EntryDialog(db, DATE, follow_up_of=db.get_entry(old_id))
    assert dlg.title_edit.text() == "登入頁"
    assert EntryDialog(db, DATE).title_edit.text() == ""  # 一般新增不沿用標題
    dlg.text.setPlainText("b")
    dlg.save()
    new = db.entries_for_date(DATE)[0]
    assert new.title == "登入頁" and new.carried_from == old_id


def test_carry_over_keeps_title(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-01", "09:00", "a", "doing", title="登入頁"))
    db.carry_over("2026-11-01", DATE, "08:00")
    assert db.entries_for_date(DATE)[0].title == "登入頁"


def test_slide_uses_title_as_heading(tmp_path):
    from pptx import Presentation
    from reporttool.slides import export_slides

    db = Database(tmp_path / "data")
    db.save_entry(Entry(None, DATE, "09:00", "查到原因", "doing", title="登入 timeout"))
    slide = Presentation(str(export_slides(db, DATE, tmp_path / "out"))).slides[1]
    texts = [s.text_frame.text for s in slide.shapes if s.has_text_frame]
    assert "登入 timeout" in texts and "• 查到原因" in texts
