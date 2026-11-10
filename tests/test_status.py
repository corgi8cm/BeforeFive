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


# 1.0.0 的預設 AI 指示（還是三種狀態、沒有 ⇄ 那一行的更舊版本，也要能升級）
V1_INSTRUCTIONS = """- 先依紀錄中的 [分類：X] 分成大段，用分類名稱當標題（例如 *【開發】*），沒有分類的放在最後的「*【未分類】*」
- 每個分類內再依 tag 分組，用 tag 當小標題（例如 *#專案A*），相同 tag 的項目放在同一組；一筆有多個 tag 時以第一個為準，沒有 tag 的放在該分類最後
- 每組內用條列，依「✅ 完成」「👀 待確認」「🔄 進行中」「⏳ 等待中」「📝 待辦」「🚫 取消」的順序排列，每項開頭標上對應的狀態符號
- 標有（延續自 MM-DD）的項目是從那天起持續進行的工作，可以在描述中帶出
- 標有 ⇄ 的是和前一次紀錄相比的變化（前：／後：是改動的內容），用來說明這次的進展；「沒有變更」的項目簡短帶過即可
- 只用紀錄和圖片中的內容，不要補充或編造
- 參考圖片內容讓描述更具體，但保留（附圖N）標記
- 用 Slack mrkdwn 格式，語氣簡潔、專業
"""
OLDER_INSTRUCTIONS = V1_INSTRUCTIONS.replace(
    "依「✅ 完成」「👀 待確認」「🔄 進行中」「⏳ 等待中」「📝 待辦」「🚫 取消」的順序", "依「✅ 完成」「🔄 進行中」「⛔ 卡住」的順序"
).replace(V1_INSTRUCTIONS.splitlines()[4] + "\n", "")


def _v2_instructions():
    from reporttool.config import DEFAULT_INSTRUCTIONS, RANGE_LINE, TIME_LINE
    return DEFAULT_INSTRUCTIONS.replace(TIME_LINE + "\n", "").replace(RANGE_LINE + "\n", "")


@pytest.mark.parametrize("old", [V1_INSTRUCTIONS, OLDER_INSTRUCTIONS, _v2_instructions()])
def test_old_instructions_are_upgraded(tmp_path, old):
    from reporttool.config import DEFAULT_INSTRUCTIONS, load_instructions

    (tmp_path / "instructions.md").write_text("- 我自己加的規則\n" + old, encoding="utf-8")
    text = load_instructions(load_settings(tmp_path))
    assert text == "- 我自己加的規則\n" + DEFAULT_INSTRUCTIONS
    assert load_instructions(load_settings(tmp_path)) == text  # 再開一次不會重複加
