"""六種狀態：待辦／進行中／等待中／待確認／完成／取消，以及舊版「卡住」的轉換。"""
import json
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from reporttool.config import load_settings
from reporttool.db import STATUSES, Database, Entry
from reporttool.slides import order_by_tag
from reporttool.sync import import_bundle, snapshot
from reporttool.ui.entry_dialog import EntryDialog
from reporttool.ui.main_window import CONTENT_COL, MainWindow

DATE = "2026-11-02"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_old_blocked_becomes_todo(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "09:00", "舊的卡住", "doing"))
    updated_at = snapshot(db)["entries"][0]["updated_at"]
    db.close()
    conn = sqlite3.connect(tmp_path / "reporttool.db")
    conn.execute("UPDATE entries SET status='blocked'")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()

    db = Database(tmp_path)
    assert db.entries_for_date(DATE)[0].status == "todo"
    # 升級不算修改，不會在同步時蓋掉別台裝置
    assert snapshot(db)["entries"][0]["updated_at"] == updated_at


def test_old_bundle_blocked_imports_as_todo(tmp_path):
    src = Database(tmp_path / "a")
    src.save_entry(Entry(None, DATE, "09:00", "內容", "doing"))
    data = snapshot(src)
    data["entries"][0]["status"] = "blocked"  # 舊版資料包
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "bundle.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    dst = Database(tmp_path / "b")
    import_bundle(dst, folder)
    assert dst.entries_for_date(DATE)[0].status == "todo"


def test_carry_over_skips_done_and_cancelled(tmp_path):
    db = Database(tmp_path)
    for i, status in enumerate(STATUSES):
        db.save_entry(Entry(None, "2026-11-01", f"0{i}:00", status, status))
    db.carry_over("2026-11-01", DATE, "08:00")
    assert [e.status for e in db.entries_for_date(DATE)] == ["todo", "doing", "waiting", "review"]


def test_slides_status_order():
    items = [(Entry(None, DATE, "00:00", s, s, ["A"]), []) for s in STATUSES]
    assert [it[0].status for it in order_by_tag(items)] == ["done", "review", "doing", "waiting", "todo",
                                                            "cancelled"]


def test_dialog_status_shortcut_order(qapp, tmp_path):
    db = Database(tmp_path / "data")
    dlg = EntryDialog(db, DATE)
    keys = [b.property("status") for b in dlg.status_group.buttons()]
    assert keys == list(STATUSES)
    assert [b.text() for b in dlg.status_group.buttons()][0] == "待辦"
    assert dlg.status_group.buttons()[0].toolTip() == "Alt+1"


def test_cancelled_is_struck_out_and_status_bar_hides_zero(qapp, tmp_path):
    db = Database(tmp_path / "data")
    db.save_entry(Entry(None, DATE, "09:00", "不做了", "cancelled"))
    db.save_entry(Entry(None, DATE, "10:00", "做完了", "done"))
    settings = load_settings(tmp_path / "config")
    win = MainWindow(db, settings)
    win.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    items = {it.text(CONTENT_COL): it for it in win.entry_items()}
    assert items["不做了"].font(CONTENT_COL).strikeOut()
    assert not items["做完了"].font(CONTENT_COL).strikeOut()
    message = win.statusBar().currentMessage()
    assert "取消 1" in message and "完成 1" in message and "待辦" not in message


def test_old_instructions_are_upgraded(tmp_path):
    from reporttool.config import DEFAULT_INSTRUCTIONS, OLD_STATUS_LINES, load_instructions

    old = DEFAULT_INSTRUCTIONS.replace(DEFAULT_INSTRUCTIONS.splitlines()[2], OLD_STATUS_LINES[0])
    old = old.replace(DEFAULT_INSTRUCTIONS.splitlines()[4] + "\n", "")
    custom = "- 我自己加的規則\n" + old
    (tmp_path / "instructions.md").write_text(custom, encoding="utf-8")
    text = load_instructions(load_settings(tmp_path))
    assert text == "- 我自己加的規則\n" + DEFAULT_INSTRUCTIONS
    assert load_instructions(load_settings(tmp_path)) == text  # 再開一次不會重複加
