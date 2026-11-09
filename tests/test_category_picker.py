"""輸入視窗的分類選擇：打字搜尋、樹狀清單、沿用分類。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from reporttool.db import Database, Entry
from reporttool.ui.category_picker import CategoryPicker, CategoryPopup
from reporttool.ui.entry_dialog import EntryDialog

DATE = "2026-11-08"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path)
    cats = {label: cid for cid, label in db.categories()}
    proj = db.add_category("專案A", cats["開發"])
    db.add_category("登入頁", proj)
    db.add_category("報表", proj)
    db.add_category("專案A", cats["支援"])
    return db


def ids(db):
    return {label: cid for cid, label in db.categories()}


def type_text(picker, text):
    picker.edit.setText(text)
    picker.commit_text()


def test_type_to_search(qapp, db):
    picker = CategoryPicker(db)
    assert picker.category_id() is None

    type_text(picker, "開發 › 專案A › 登入頁")  # 完整路徑
    assert picker.category_id() == ids(db)["開發 › 專案A › 登入頁"]

    type_text(picker, "報表")  # 只有一個符合
    assert picker.category_id() == ids(db)["開發 › 專案A › 報表"]
    assert picker.edit.text() == "開發 › 專案A › 報表"

    type_text(picker, "專案A")  # 好幾個符合：維持原本的選擇
    assert picker.category_id() == ids(db)["開發 › 專案A › 報表"]
    assert picker.edit.text() == "開發 › 專案A › 報表"

    type_text(picker, "找不到")
    assert picker.category_id() == ids(db)["開發 › 專案A › 報表"]

    type_text(picker, "")  # 清空就是未分類
    assert picker.category_id() is None


def test_completer_matches_any_part(qapp, db):
    picker = CategoryPicker(db)
    completer = picker.edit.completer()
    completer.setCompletionPrefix("專案A")
    matches = [completer.completionModel().index(i, 0).data() for i in range(completer.completionCount())]
    assert matches == ["開發 › 專案A", "開發 › 專案A › 登入頁", "開發 › 專案A › 報表", "支援 › 專案A"]


def test_popup_tree(qapp, db):
    picker = CategoryPicker(db)
    picker.set_category(ids(db)["開發 › 專案A"])
    popup = CategoryPopup(db, picker.category_id())
    popup.chosen.connect(picker.set_category)
    assert popup.topLevelItem(0).text(0) == "（未分類）"
    dev = popup.topLevelItem(1)
    assert dev.text(0) == "開發" and dev.child(0).text(0) == "專案A" and dev.child(0).childCount() == 2
    assert popup.currentItem() is dev.child(0)  # 停在目前的分類

    popup._choose(dev.child(0).child(1))
    assert picker.category_id() == ids(db)["開發 › 專案A › 報表"]
    popup2 = CategoryPopup(db, picker.category_id())
    popup2.chosen.connect(picker.set_category)
    popup2._choose(popup2.topLevelItem(0))
    assert picker.category_id() is None


def test_entry_dialog_saves_and_reuses_category(qapp, db):
    login = ids(db)["開發 › 專案A › 登入頁"]
    dlg = EntryDialog(db, DATE)
    dlg.text.setPlainText("修 timeout")
    type_text(dlg.category_picker, "登入頁")
    dlg.save()
    saved = db.entries_for_date(DATE)[0]
    assert saved.category_id == login and saved.category == "開發 › 專案A › 登入頁"

    # 新紀錄沿用上一筆的分類；追加進度沿用那件工作的分類
    assert EntryDialog(db, DATE).category_picker.category_id() == login
    db.save_entry(Entry(None, DATE, "10:00", "另一件", "doing", category_id=ids(db)["支援 › 專案A"]))
    follow = EntryDialog(db, DATE, follow_up_of=saved)
    assert follow.category_picker.category_id() == login


def test_entry_dialog_with_preset_category(qapp, db):
    """從左邊分類樹右鍵「新增紀錄…」：直接放在那個分類，不沿用上一筆其他分類的 tag。"""
    db.save_entry(Entry(None, DATE, "09:00", "上一筆", "doing", ["別的"], category_id=ids(db)["支援 › 專案A"]))
    login = ids(db)["開發 › 專案A › 登入頁"]
    dlg = EntryDialog(db, DATE, category_id=login)
    assert dlg.category_picker.category_id() == login
    assert dlg.tag_edit.tags() == []
    dlg.text.setPlainText("新的")
    dlg.save()
    assert {e.text: e.category_id for e in db.entries_for_date(DATE)}["新的"] == login
