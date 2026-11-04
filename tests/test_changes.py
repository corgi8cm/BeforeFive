"""延續的工作和前一次紀錄的比較。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image as PILImage
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from reporttool.changes import change_for, changes_for, char_segments, compare
from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.report import generate_report
from reporttool.ui.changes_dialog import ADDED_BG, REMOVED_BG, ChangesDialog, segments_html

DATE = "2026-11-04"
PREV = "2026-11-03"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def entry(text, status="todo", **kw):
    return Entry(None, PREV, "09:00", text, status, kw.pop("tags", ["專案A"]), **kw)


def test_field_changes_and_summary():
    prev = entry("查 log 中", "todo", title="登入頁", category="開發", priority=3)
    cur = Entry(None, DATE, "10:00", "查 log 中", "doing", ["專案A", "bug"], title="登入頁 timeout",
                category="支援", priority=1)
    c = compare(prev, cur)
    assert c.fields == ["狀態 待辦→進行中", "優先 3→1", "標題 登入頁→登入頁 timeout", "分類 開發→支援", "tag +#bug"]
    assert not c.text_changed and c.changed
    assert c.previous_label == "11-03"


def test_unchanged_and_text_diff():
    prev = entry("查 log 中\n明天問 DBA")
    same = Entry(None, DATE, "10:00", "查 log 中\r\n明天問 DBA\n", "todo", ["專案A"])
    assert compare(prev, same).summary() == "沒有變更"

    cur = Entry(None, DATE, "10:00", "已找到原因\n明天問 DBA\n上測試機", "todo", ["專案A"])
    c = compare(prev, cur)
    assert c.summary() == "內容有更新"
    assert c.text_diff() == [("前", "查 log 中"), ("後", "已找到原因"), ("後", "上測試機")]


def test_same_day_follow_up_label_has_time():
    prev = Entry(None, DATE, "09:00", "a", "doing")
    cur = Entry(None, DATE, "15:30", "b", "doing")
    assert compare(prev, cur).previous_label == "11-04 09:00"


def test_change_for_needs_live_previous(tmp_path):
    db = Database(tmp_path)
    first = db.save_entry(entry("查 log 中"))
    plain = db.save_entry(Entry(None, DATE, "08:00", "新的", "doing"))
    db.carry_over(PREV, DATE, "09:00")
    carried = [e for e in db.entries_for_date(DATE) if e.carried_from][0]
    assert change_for(db, db.get_entry(plain)) is None
    assert change_for(db, carried).summary() == "沒有變更"
    db.delete_entry(first)
    assert changes_for(db, db.entries_for_date(DATE)) == []


def test_report_has_change_lines(tmp_path):
    db = Database(tmp_path / "data")
    db.save_entry(entry("查 log 中", "todo", priority=3))
    db.save_entry(Entry(None, PREV, "11:00", "等 DBA", "waiting"))
    db.carry_over(PREV, DATE, "09:00")
    first, second = db.entries_for_date(DATE)
    first.text, first.status, first.priority = "已找到原因\n上測試機", "doing", 1
    db.save_entry(first)
    PILImage.new("RGB", (10, 10), "red").save(tmp_path / "a.png")
    second.images = [db.import_image_file(tmp_path / "a.png", DATE)]
    db.save_entry(second)

    md = (generate_report(db, DATE, tmp_path / "out", "指示") / "report.md").read_text(encoding="utf-8")
    assert ("（延續自 11-03）\n"
            "  ⇄ 與 11-03 相比：狀態 待辦→進行中；優先 3→1；內容有更新\n"
            "    前：查 log 中\n"
            "    後：已找到原因\n"
            "    後：上測試機\n") in md
    assert "  ⇄ 與 11-03 相比：新增附圖 1 張\n" in md


def test_char_segments():
    old, new = char_segments("查 log 中", "查到 log 了")
    assert "".join(t for t, _ in old) == "查 log 中" and "".join(t for t, _ in new) == "查到 log 了"
    assert ("到", True) in new and ("中", True) in old


def test_dialog_starts_on_first_changed(qapp):
    unchanged = compare(entry("a"), Entry(None, DATE, "08:00", "a", "todo", ["專案A"]))
    changed = compare(entry("查 log 中"), Entry(None, DATE, "09:00", "查到 log 了", "doing", ["專案A"]))
    dlg = ChangesDialog([unchanged, changed])
    assert dlg.list.currentRow() == 1
    assert "狀態 待辦→進行中" in dlg.fields.text()
    assert dlg.old_view.toPlainText() == "查 log 中"
    assert dlg.new_view.toPlainText() == "查到 log 了"


def test_segments_html_marks_changes():
    html = segments_html([("查", False), ("<到>", True), ("\nlog", False)], ADDED_BG)
    assert html == f'查<span style="background:{ADDED_BG};">&lt;到&gt;</span><br>log'
    assert "（空白）" in segments_html([], REMOVED_BG)


def test_review_opens_changes_only_for_carried(qapp, tmp_path, monkeypatch):
    import reporttool.ui.main_window as mw
    monkeypatch.setattr(mw.os, "startfile", lambda *_: None)
    db = Database(tmp_path / "data")
    settings = load_settings(tmp_path / "config")
    settings.output_dir = tmp_path / "out"
    db.save_entry(Entry(None, DATE, "08:00", "新的", "doing"))
    win = mw.MainWindow(db, settings)
    win.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    win.review()
    assert getattr(win, "changes_dialog", None) is None

    db.save_entry(entry("查 log 中"))
    db.carry_over(PREV, DATE, "09:00")
    win.review()
    assert win.changes_dialog.isVisible() and win.changes_dialog.list.count() == 1
    win.changes_dialog.close()
