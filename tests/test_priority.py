"""執行優先順序（1~5，1 最優先）。"""
import json
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.slides import order_by_category
from reporttool.sync import BundleError, import_bundle, snapshot
from reporttool.ui.entry_dialog import EntryDialog
from reporttool.ui.main_window import CONTENT_COL, MainWindow

DATE = "2026-11-02"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_default_and_saved_priority(tmp_path):
    db = Database(tmp_path)
    a = db.save_entry(Entry(None, DATE, "09:00", "a", "done"))
    b = db.save_entry(Entry(None, DATE, "10:00", "b", "done", priority=1))
    assert db.get_entry(a).priority == 3 and db.get_entry(b).priority == 1


def test_old_database_gets_default_priority(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "09:00", "舊紀錄", "done"))
    db.close()
    conn = sqlite3.connect(tmp_path / "reporttool.db")
    conn.execute("ALTER TABLE entries DROP COLUMN priority")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    assert [e.priority for e in Database(tmp_path).entries_for_date(DATE)] == [3]


def write_bundle(folder, data):
    folder.mkdir()
    (folder / "bundle.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return folder


def test_bundle_priority(tmp_path):
    src = Database(tmp_path / "a")
    src.save_entry(Entry(None, DATE, "09:00", "內容", "done", priority=2))
    data = snapshot(src)
    dst = Database(tmp_path / "b")
    import_bundle(dst, write_bundle(tmp_path / "new", data))
    assert dst.entries_for_date(DATE)[0].priority == 2

    del data["entries"][0]["priority"]  # 舊版資料包沒有這個欄位
    old = Database(tmp_path / "c")
    import_bundle(old, write_bundle(tmp_path / "old", data))
    assert old.entries_for_date(DATE)[0].priority == 3

    data["entries"][0]["priority"] = 9
    with pytest.raises(BundleError):
        import_bundle(Database(tmp_path / "d"), write_bundle(tmp_path / "bad", data))


def test_list_sorts_by_priority_within_group(qapp, tmp_path):
    db = Database(tmp_path / "data")
    for time, text, tags, priority in [("09:00", "a", ["A"], 3), ("10:00", "b", ["B"], 5),
                                       ("11:00", "c", ["A"], 1), ("12:00", "d", ["A"], 3)]:
        db.save_entry(Entry(None, DATE, time, text, "done", tags, priority=priority))
    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    [group] = [win.tree.topLevelItem(i) for i in range(win.tree.topLevelItemCount())]
    assert group.text(0) == "未分類（4）"
    # tag 不影響分組：同一層依優先順序，再依標題
    assert [group.child(i).text(CONTENT_COL) for i in range(group.childCount())] == ["c", "a", "d", "b"]
    assert group.child(0).text(1) == "1"

    win.group_box.setChecked(False)
    flat = [win.tree.topLevelItem(i).text(CONTENT_COL) for i in range(win.tree.topLevelItemCount())]
    assert flat == ["c", "a", "d", "b"]


def test_dialog_priority(qapp, tmp_path):
    db = Database(tmp_path)
    dlg = EntryDialog(db, DATE)
    assert dlg.priority_box.currentData() == 3
    dlg.text.setPlainText("急件")
    dlg.priority_box.setCurrentIndex(dlg.priority_box.findData(1))
    dlg.save()
    [entry] = db.entries_for_date(DATE)
    assert entry.priority == 1
    assert EntryDialog(db, DATE, entry).priority_box.currentData() == 1
    assert EntryDialog(db, DATE, follow_up_of=entry).priority_box.currentData() == 1
    assert EntryDialog(db, DATE).priority_box.currentData() == 3  # 一般新增不沿用


def test_carry_over_keeps_priority(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-01", "09:00", "a", "doing", priority=2))
    db.carry_over("2026-11-01", DATE, "08:00")
    assert db.entries_for_date(DATE)[0].priority == 2


def test_slides_order_by_priority_first():
    def e(text, status, priority):
        return Entry(None, DATE, "00:00", text, status, ["A"], priority=priority), []

    items = [e("a", "done", 3), e("b", "waiting", 1), e("c", "done", 2)]
    assert [it[0].text for it in order_by_category(items, [])] == ["b", "c", "a"]
