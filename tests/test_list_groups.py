"""主視窗清單依 tag（與分類）分組。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.ui.main_window import MainWindow, group_by_category, group_by_tag

DATE = "2026-11-02"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def e(text, tags=(), category="", time="00:00"):
    return Entry(None, DATE, time, text, "done", list(tags), category=category)


def test_group_by_tag_uses_first_tag_and_puts_untagged_last():
    entries = [e("a", ["B"]), e("b"), e("c", ["A", "B"]), e("d", ["B", "A"])]
    groups = group_by_tag(entries)
    assert [(tag, [x.text for x in members]) for tag, members in groups] == [
        ("B", ["a", "d"]), ("A", ["c"]), (None, ["b"])]


def test_group_by_category_follows_category_order():
    entries = [e("a", category=""), e("b", category="會議"), e("c", category="開發"), e("d", category="新分類")]
    assert [c for c, _ in group_by_category(entries, ["開發", "會議"])] == ["開發", "會議", "新分類", ""]


def tree_outline(win):
    """把清單轉成 [(標題, [子項目…])]，紀錄用內容第一行表示。"""
    def node(item):
        if item.childCount():
            return (item.text(0), [node(item.child(i)) for i in range(item.childCount())])
        return item.text(4)
    return [node(win.tree.topLevelItem(i)) for i in range(win.tree.topLevelItemCount())]


@pytest.fixture
def win(qapp, tmp_path):
    db = Database(tmp_path / "data")
    cats = {n: i for i, n in db.categories()}
    for time, text, tags, cat in [
        ("09:00", "修登入頁", ["專案A"], "開發"),
        ("10:00", "寫測試", ["專案B"], "開發"),
        ("11:00", "timeout 修好", ["專案A", "bug"], "開發"),
        ("12:00", "整理筆記", [], "開發"),
        ("13:00", "週會", ["專案A"], "會議"),
        ("14:00", "雜事", [], None),
    ]:
        db.save_entry(Entry(None, DATE, time, text, "done", tags, category_id=cats.get(cat)))
    w = MainWindow(db, load_settings(tmp_path / "config"))
    w.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    return w


def test_single_category_groups_by_tag(win):
    win.category_filter.setCurrentIndex(win.category_filter.findText("開發"))
    assert tree_outline(win) == [
        ("#專案A（2）", ["修登入頁", "timeout 修好"]),
        ("#專案B（1）", ["寫測試"]),
        ("沒有 tag（1）", ["整理筆記"]),
    ]


def test_all_categories_groups_by_category_then_tag(win):
    assert tree_outline(win) == [
        ("【開發】（4）", [("#專案A（2）", ["修登入頁", "timeout 修好"]),
                         ("#專案B（1）", ["寫測試"]),
                         ("沒有 tag（1）", ["整理筆記"])]),
        ("【會議】（1）", [("#專案A（1）", ["週會"])]),
        ("【未分類】（1）", [("沒有 tag（1）", ["雜事"])]),
    ]
    assert len(win.entry_items()) == 6


def test_turn_off_grouping_shows_flat_list(win):
    win.group_box.setChecked(False)
    assert tree_outline(win) == ["修登入頁", "寫測試", "timeout 修好", "整理筆記", "週會", "雜事"]


def test_group_headers_are_not_entries(win):
    header = win.tree.topLevelItem(0)
    win.tree.setCurrentItem(header)
    assert win._selected_id() is None  # 群組標題不能被選取，編輯／刪除不會作用在上面


def test_collapsed_groups_stay_collapsed_after_refresh(win):
    dev = win.tree.topLevelItem(0)
    dev.child(0).setExpanded(False)  # 收合 開發 / #專案A
    win.refresh()
    dev = win.tree.topLevelItem(0)
    assert dev.isExpanded() and not dev.child(0).isExpanded() and dev.child(1).isExpanded()
