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


def test_history_dialog_shows_previous(qapp, db):
    dlg = HistoryDialog(db, db.ids["b"])
    assert dlg.list.count() == 3
    assert dlg.list.currentRow() == 1  # 停在右鍵的那一則（新的在上面）
    assert "改好 timeout，送 PR" in dlg.this_view.toPlainText()
    assert "找到 timeout 的原因" in dlg.prev_view.toPlainText()  # 預設同時顯示前一則的完整內容

    dlg.list.setCurrentRow(0)
    assert "PR 合併，已上線" in dlg.this_view.toPlainText() and "改好 timeout" in dlg.prev_view.toPlainText()
    dlg.list.setCurrentRow(2)
    assert "沒有前一則" in dlg.prev_view.toPlainText()

    dlg.prev_box.setChecked(False)
    assert dlg.prev_pane.isHidden()


def test_history_dialog_step_older_and_newer(qapp, db):
    dlg = HistoryDialog(db, db.ids["b"])
    assert dlg.position.text() == "第 2 / 3 則"
    dlg.older_btn.click()
    assert "找到 timeout 的原因" in dlg.this_view.toPlainText() and dlg.position.text() == "第 1 / 3 則"
    assert not dlg.older_btn.isEnabled()
    dlg.step(+1)  # 到最舊就停住
    assert dlg.list.currentRow() == 2
    dlg.newer_btn.click()
    dlg.newer_btn.click()
    assert "PR 合併，已上線" in dlg.this_view.toPlainText() and dlg.position.text() == "第 3 / 3 則"
    assert not dlg.newer_btn.isEnabled() and dlg.older_btn.isEnabled()


def test_preview_collapses_unchanged_carry_overs(qapp, tmp_path):
    """每天帶入但沒改的紀錄不重複列出，只在前一次有修改的那則下面註明。"""
    db = Database(tmp_path / "data")
    a = db.save_entry(Entry(None, "2026-11-02", "10:00", "查 log", "doing", title="登入 timeout",
                            start="09:00", end="10:00"))
    for src, dst in (("2026-11-02", "2026-11-03"), ("2026-11-03", "2026-11-04")):
        db.carry_over(src, dst)
    (c,) = db.entries_for_date("2026-11-04")
    c.text, c.status = "找到原因：連線池太小", "review"  # 11-04 有修改
    db.save_entry(c)
    db.carry_over("2026-11-04", "2026-11-05")
    db.carry_over("2026-11-05", "2026-11-06")
    (latest,) = db.entries_for_date("2026-11-06")
    (span_changed,) = db.carry_over("2026-11-06", "2026-11-07")
    e = db.get_entry(span_changed)
    e.start, e.end, e.start_date, e.end_date = "13:00", "14:00", "2026-11-07", "2026-11-07"  # 只改時段也算修改
    db.save_entry(e)

    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.preview.show_entry(latest.id)
    text = win.preview.view.toPlainText()
    assert "這件工作的歷程（6 則，新的在上面，3 則帶入後沒有修改不重複列出）" in win.preview.header.text()
    assert text.count("查 log") == 1 and text.count("找到原因：連線池太小") == 2  # 11-07 只改時段，內容照列
    assert "之後帶入 2 次都沒有修改（11-05、11-06）；目前選取的是 11-06 那則" in text
    assert "之後帶入 1 次都沒有修改（11-03）" in text
    assert text.index("11-07") < text.index("11-04") < text.index("11-02")
    assert "目前選取" in text.split("之後帶入 2 次")[0]  # 底色加在 11-04 那則

    win.preview.show_entry(a)
    assert "目前選取的是" not in win.preview.view.toPlainText()
