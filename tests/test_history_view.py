"""延續工作的歷程：整條鏈、主視窗下方的預覽、右鍵「查看歷程…」的視窗。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.ui.history_view import HistoryDialog
from reporttool.ui.main_window import CONTENT_COL, MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def follow(db, prev_id, date, text, status="doing"):
    return db.save_entry(Entry(None, date, "09:00", text, status, carried_from=prev_id, title="修登入頁"))


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "data")
    a = db.save_entry(Entry(None, "2026-11-03", "09:00", "找到 timeout 的原因", "doing", title="修登入頁"))
    b = follow(db, a, "2026-11-04", "改好 timeout，送 PR")
    c = follow(db, b, "2026-11-05", "PR 合併，已上線", "done")
    db.save_entry(Entry(None, "2026-11-05", "10:00", "週會", "done"))
    db.ids = {"a": a, "b": b, "c": c}
    return db


def test_chain_includes_earlier_and_later(db):
    ids = db.ids
    for start in ids.values():  # 從哪一則開始找都是同一條
        assert [e.id for e in db.chain(start)] == [ids["a"], ids["b"], ids["c"]]
    db.delete_entry(ids["b"])  # 中間被刪除：仍然串得起來，只是不列出
    assert [e.id for e in db.chain(ids["c"])] == [ids["a"], ids["c"]]
    assert db.chain(None) == [] and db.chain(99999) == []


def test_preview_shows_whole_chain(qapp, db, tmp_path):
    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate(2026, 11, 5))
    items = {i.text(CONTENT_COL): i for i in win.entry_items()}
    win.tree.setCurrentItem(items["↪ 修登入頁"])
    html = win.preview.view.toPlainText()
    assert "這件工作的歷程（3 則" in win.preview.header.text()
    # 新的在上面，每一則都是完整內容
    assert html.index("PR 合併，已上線") < html.index("改好 timeout，送 PR") < html.index("找到 timeout 的原因")
    assert win.preview.open_btn.isEnabled()

    win.tree.setCurrentItem(items["週會"])  # 沒有延續的紀錄：只顯示自己
    assert win.preview.header.text() == "預覽" and "週會" in win.preview.view.toPlainText()
    assert not win.preview.open_btn.isEnabled()

    win.preview_box.setChecked(False)  # 關掉會記住
    assert load_settings(tmp_path / "config").show_preview is False
    assert win.preview.isHidden()


def test_history_dialog_compare(qapp, db):
    dlg = HistoryDialog(db, db.ids["b"])
    assert dlg.list.count() == 3
    assert dlg.list.currentRow() == 1  # 停在右鍵的那一則（新的在上面）
    assert "改好 timeout，送 PR" in dlg.single.toPlainText()

    dlg.compare_box.setChecked(True)
    assert "（前一則）" in dlg.old_title.text() and "11-03" in dlg.old_title.text()
    assert "找到 timeout 的原因" in dlg.old_view.toPlainText()
    assert "改好 timeout，送 PR" in dlg.new_view.toPlainText()

    dlg.list.setCurrentRow(0)
    assert "狀態" in dlg.fields.text()  # 進行中 → 完成
    dlg.list.setCurrentRow(2)
    assert "第一則" in dlg.fields.text()
