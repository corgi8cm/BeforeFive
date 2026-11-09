"""主視窗：左邊分類樹（筆數、進度），右邊清單依分類樹分組，同一層依優先順序、再依標題。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QDialog

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.ui import main_window as mw
from reporttool.ui.main_window import CONTENT_COL, MainWindow, outline, sort_entries
from reporttool.ui.manage_dialog import ProgressDialog
from reporttool.ui.progress_delegate import PROGRESS_ROLE

DATE = "2026-11-02"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def e(text, priority=3, title="", time="00:00", category_id=None):
    return Entry(None, DATE, time, text, "done", ["不影響"], category_id=category_id, title=title, priority=priority)


def test_sort_by_priority_then_title():
    entries = [e("b 內容", time="08:00"), e("z", priority=1), e("a 內容", time="09:00"), e("內容", title="a 標題")]
    assert [x.summary for x in sort_entries(entries)] == ["z", "a 內容", "a 標題", "b 內容"]


def tree_outline(win):
    """把清單轉成 [(標題, [子項目…])]，紀錄用內容第一行表示。"""
    def node(item):
        if item.childCount():
            return (item.text(0), [node(item.child(i)) for i in range(item.childCount())])
        return item.text(CONTENT_COL)
    return [node(win.tree.topLevelItem(i)) for i in range(win.tree.topLevelItemCount())]


@pytest.fixture
def win(qapp, tmp_path):
    db = Database(tmp_path / "data")
    cats = {n: i for i, n in db.categories()}
    proj = db.add_category("專案A", cats["開發"])
    login = db.add_category("登入頁", proj)
    db.add_category("專案B", cats["開發"])  # 沒有紀錄，不列出
    db.set_category_progress(proj, 40)
    for time, text, cat, priority in [
        ("09:00", "修登入頁", login, 3),
        ("10:00", "寫測試", proj, 2),
        ("11:00", "整理架構", proj, 3),
        ("12:00", "整理筆記", cats["開發"], 3),
        ("13:00", "週會", cats["會議"], 3),
        ("14:00", "雜事", None, 3),
    ]:
        db.save_entry(Entry(None, DATE, time, text, "done", ["tag 不影響"], category_id=cat, priority=priority))
    w = MainWindow(db, load_settings(tmp_path / "config"))
    w.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    w.ids = {label: cid for cid, label in db.categories()}
    return w


def test_all_categories_follow_tree(win):
    assert tree_outline(win) == [
        ("開發（4）", ["整理筆記",
                     ("專案A（3）　進度 40%", ["寫測試", "整理架構",
                                          ("登入頁（1）", ["修登入頁"])])]),
        ("會議（1）", ["週會"]),
        ("未分類（1）", ["雜事"]),
    ]
    assert len(win.entry_items()) == 6
    assert win.tree.isColumnHidden(mw.CATEGORY_COL)


def test_select_category_shows_subtree(win):
    win.select_category(win.ids["開發 › 專案A"])
    assert tree_outline(win) == ["寫測試", "整理架構", ("登入頁（1）", ["修登入頁"])]
    assert "［開發 › 專案A］：共 3 筆" in win.statusBar().currentMessage()

    win.select_category(mw.UNCATEGORIZED)
    assert tree_outline(win) == ["雜事"]


def test_turn_off_grouping_shows_flat_list(win):
    win.group_box.setChecked(False)
    assert tree_outline(win) == ["寫測試", "修登入頁", "整理架構", "整理筆記", "週會", "雜事"]
    assert not win.tree.isColumnHidden(mw.CATEGORY_COL)


def sidebar_rows(win):
    rows = []

    def walk(item, depth):
        rows.append(("  " * depth + item.text(mw.SIDE_NAME), item.text(mw.SIDE_COUNT), item.data(mw.SIDE_PROGRESS, PROGRESS_ROLE)))
        for i in range(item.childCount()):
            walk(item.child(i), depth + 1)
    for i in range(win.sidebar.topLevelItemCount()):
        walk(win.sidebar.topLevelItem(i), 0)
    return rows


def test_sidebar_counts_and_progress(win):
    rows = sidebar_rows(win)
    assert rows[:5] == [("全部", "6", None), ("開發", "4", None), ("  專案A", "3", 40), ("    登入頁", "1", None),
                        ("  專案B", "", None)]
    assert rows[-1] == ("未分類", "1", None)

    win.date_edit.setDate(QDate.fromString("2026-11-03", "yyyy-MM-dd"))  # 換一天，筆數跟著變
    assert sidebar_rows(win)[0] == ("全部", "", None)


def test_set_progress_from_sidebar(win, monkeypatch):
    def fake_exec(dlg):
        dlg.enabled.setChecked(True)
        dlg.spin.setValue(90)
        return QDialog.Accepted

    monkeypatch.setattr(ProgressDialog, "exec", fake_exec)
    win.select_category("all")
    assert not win.progress_btn.isEnabled()  # 「全部」不是分類，不能設定進度
    win.select_category(win.ids["開發"])
    assert win.progress_btn.isEnabled() and win.child_btn.isEnabled()
    win.progress_btn.click()  # 分類樹下方的按鈕
    assert ("開發", "4", 90) in sidebar_rows(win)
    assert win.selected_category() == win.ids["開發"]  # 重建後仍選在同一個分類
    assert tree_outline(win)[0] == "整理筆記"


def test_group_headers_are_not_entries(win):
    header = win.tree.topLevelItem(0)
    win.tree.setCurrentItem(header)
    assert win._selected_id() is None  # 群組標題不能被選取，編輯／刪除不會作用在上面


def test_collapsed_groups_stay_collapsed_after_refresh(win):
    dev = win.tree.topLevelItem(0)
    dev.child(1).setExpanded(False)  # 收合 開發 › 專案A
    win.refresh()
    dev = win.tree.topLevelItem(0)
    assert dev.isExpanded() and not dev.child(1).isExpanded()


def test_outline_skips_empty_categories(tmp_path):
    db = Database(tmp_path)
    nodes = db.category_tree()
    direct, groups = outline([], nodes)
    assert direct == [] and groups == []


def test_show_progress_toggle_is_remembered(win, tmp_path):
    win.progress_box.setChecked(False)  # 一次關掉所有進度顯示
    assert win.sidebar.isColumnHidden(mw.SIDE_PROGRESS)
    assert win.progress_btn.isHidden()
    assert tree_outline(win)[0][1][1][0] == "專案A（3）"
    assert win.db.category_tree()[1].progress == 40  # 只是不顯示，進度還在
    assert load_settings(tmp_path / "config").show_progress is False  # 下次開啟仍是關閉

    win.progress_box.setChecked(True)
    assert not win.sidebar.isColumnHidden(mw.SIDE_PROGRESS)
    assert tree_outline(win)[0][1][1][0] == "專案A（3）　進度 40%"
    assert load_settings(tmp_path / "config").show_progress is True


def test_delete_category_from_sidebar(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, text, *a: asked.append(text) or QMessageBox.Yes)
    win.select_category(win.ids["開發 › 專案A"])
    win.delete_category()  # 右鍵選單的「刪除分類…」
    assert "1 個子分類" in asked[0] and "3 筆紀錄" in asked[0]
    labels = [label for _, label in win.db.categories()]
    assert "開發 › 專案A" not in labels and "開發 › 專案A › 登入頁" not in labels
    assert win.selected_category() == win.ids["開發"]  # 刪除後選到上一層
    assert sidebar_rows(win)[1] == ("開發", "4", None)  # 紀錄移到上一層


def test_set_category_priority_from_sidebar(win):
    win.select_category(win.ids["會議"])
    win.set_category_priority(1)  # 右鍵選單「優先度」
    rows = sidebar_rows(win)
    assert rows[1][0] == "會議" and rows[2][0] == "開發"
    assert win.selected_category() == win.ids["會議"]
    item = win.sidebar.topLevelItem(1)
    assert item.text(mw.SIDE_PRIORITY) == "1"
    win.select_category("all")
    assert tree_outline(win)[0][0] == "會議（1）"  # 右邊分組也照優先度


def sidebar_item(win, key):
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    it = QTreeWidgetItemIterator(win.sidebar)
    while it.value():
        if it.value().data(mw.SIDE_NAME, mw.SIDEBAR_KEY) == key:
            return it.value()
        it += 1


def drop_on(win, qapp, mime, key):
    """模擬把 mime 拖到左邊分類樹的 key 上放開，回傳事件有沒有被接受。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    win.show()
    pos = QPointF(win.sidebar.visualItemRect(sidebar_item(win, key)).center())
    event = QDropEvent(pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier, QEvent.Drop)
    win.sidebar.dropEvent(event)
    qapp.processEvents()
    return event.isAccepted()


def test_drag_entries_onto_category(win, qapp):
    db = win.db
    items = {i.text(CONTENT_COL): i for i in win.entry_items()}
    # 群組標題不會被當成紀錄拖出去
    group = win.tree.topLevelItem(0)
    ids = {text: item.data(0, mw.Qt.UserRole) for text, item in items.items()}
    mime = win.tree.mimeData([items["週會"], items["雜事"], group])
    assert mw.entry_ids_from_mime(mime) == [ids["週會"], ids["雜事"]]

    win.select_category(win.ids["會議"])
    assert drop_on(win, qapp, mime, win.ids["開發 › 專案A"])
    moved = {e.summary: e.category for e in db.entries_for_date(DATE)}
    assert moved["週會"] == moved["雜事"] == "開發 › 專案A"
    assert win.selected_category() == win.ids["會議"]  # 畫面停在原本選的分類
    assert "已把 2 筆紀錄移到「開發 › 專案A」" in win.statusBar().currentMessage()

    # 拖到「未分類」就是清除分類；拖到「全部」不接受
    assert drop_on(win, qapp, mw.entry_ids_mime([ids["週會"]]), mw.UNCATEGORIZED)
    assert {e.summary: e.category_id for e in db.entries_for_date(DATE)}["週會"] is None
    assert not drop_on(win, qapp, mime, mw.ALL)
    assert {e.summary: e.category for e in db.entries_for_date(DATE)}["雜事"] == "開發 › 專案A"
    win.close()


def test_set_entries_category(tmp_path):
    db = Database(tmp_path / "data")
    cats = {n: i for i, n in db.categories()}
    a = db.save_entry(e("a", category_id=cats["會議"]))
    b = db.save_entry(e("b"))
    before = db.conn.execute("SELECT updated_at FROM entries WHERE id=?", (a,)).fetchone()[0]
    assert db.set_entries_category([a, b], cats["會議"]) == 1  # a 本來就在會議，不算改動，也不更新修改時間
    assert db.conn.execute("SELECT updated_at FROM entries WHERE id=?", (a,)).fetchone()[0] == before
    assert db.get_entry(b).category == "會議"
    assert db.set_entries_category([a, b], None) == 2
    with pytest.raises(ValueError):
        db.set_entries_category([a], 99999)


def test_delete_multiple_selected(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[2]) or QMessageBox.Yes)
    for item in win.entry_items():
        item.setSelected(item.text(CONTENT_COL) in ("週會", "雜事"))
    win.delete_selected()
    assert "確定刪除這 2 筆紀錄？" in asked[0]
    assert sorted(e.summary for e in win.db.entries_for_date(DATE)) == ["修登入頁", "寫測試", "整理架構", "整理筆記"]
