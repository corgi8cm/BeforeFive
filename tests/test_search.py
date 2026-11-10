"""v3.0.0：搜尋紀錄（不限日期，找標題、內容、tag、分類）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.ui.main_window import ALL, MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "data")
    dev = db.add_category("專案A", db.categories()[0][0])  # 開發 › 專案A
    db.save_entry(Entry(None, "2026-10-01", "09:00", "修登入頁 Timeout", "done", ["bug"], category_id=dev))
    db.save_entry(Entry(None, "2026-11-02", "10:00", "週會", "done", ["會議"], title="週會報告"))
    db.save_entry(Entry(None, "2026-11-02", "14:00", "登入頁又 timeout 了", "doing", ["客戶X"]))
    db.save_entry(Entry(None, "2026-11-03", "09:00", "看 100% 的 CPU 與 a_b", "waiting"))
    gone = db.save_entry(Entry(None, "2026-11-03", "10:00", "登入頁 刪掉的", "done"))
    db.delete_entry(gone)
    return db


def texts(entries):
    return [e.text for e in entries]


def test_search_fields_and_order(db):
    # 新的在前；不分大小寫；刪掉的不算
    assert texts(db.search("timeout")) == ["登入頁又 timeout 了", "修登入頁 Timeout"]
    assert texts(db.search("週會報告")) == ["週會"]  # 標題
    assert texts(db.search("客戶")) == ["登入頁又 timeout 了"]  # tag
    assert texts(db.search("專案A")) == ["修登入頁 Timeout"]  # 分類路徑
    assert texts(db.search("開發 › 專案")) == ["修登入頁 Timeout"]


def test_search_terms_and_tags(db):
    assert texts(db.search("登入 bug")) == ["修登入頁 Timeout"]  # 每個詞都要出現（可以在不同欄位）
    assert texts(db.search("#bug")) == ["修登入頁 Timeout"]
    assert db.search("#週會") == []  # #詞 只找 tag
    assert db.search("") == [] and db.search("   ") == []


def test_search_escapes_like_wildcards(db):
    assert texts(db.search("100%")) == ["看 100% 的 CPU 與 a_b"]
    assert db.search("%") == db.search("100%")
    assert texts(db.search("a_b")) == ["看 100% 的 CPU 與 a_b"]
    assert db.search("x_y") == []


def test_search_limit(db):
    assert len(db.search("登入", limit=1)) == 1


def test_search_dialog_jumps_to_entry(qapp, db, tmp_path):
    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate.fromString("2026-11-05", "yyyy-MM-dd"))
    other = [cid for cid, label in db.categories() if label == "會議"][0]
    win.select_category(other)  # 篩選中的分類看不到目標紀錄

    dlg = win.open_search("timeout")
    assert dlg.results.topLevelItemCount() == 2
    assert dlg.results.topLevelItem(1).text(0) == "2026-10-01（四）"
    assert "登入頁又 timeout 了" in dlg.preview.toPlainText()  # 第一筆自動選取並預覽
    target = dlg.results.topLevelItem(1)
    dlg._activate(target)

    assert win.current_date == "2026-10-01"
    assert win.selected_category() == ALL
    assert win._selected_ids() == [target.data(0, Qt.UserRole)]

    assert win.open_search("週會") is dlg  # 已經開著就重用，換關鍵字
    assert dlg.results.topLevelItemCount() == 1


def test_search_dialog_enter_opens_first(qapp, db, tmp_path):
    win = MainWindow(db, load_settings(tmp_path / "config"))
    dlg = win.open_search()
    dlg.box.setText("CPU")
    dlg._activate_first()  # 打完字直接按 Enter，不等延遲搜尋
    assert win.current_date == "2026-11-03"
    assert [e.text for e in (db.get_entry(i) for i in win._selected_ids())] == ["看 100% 的 CPU 與 a_b"]
