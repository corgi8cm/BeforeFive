"""分類管理視窗（樹狀）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from reporttool.db import Database, Entry
from reporttool.ui import manage_dialog
from reporttool.ui.manage_dialog import NAME_COL, PROGRESS_COL, CategoryTab, ProgressDialog
from reporttool.ui.progress_delegate import PROGRESS_ROLE

DATE = "2026-11-08"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    t = CategoryTab(Database(tmp_path))
    t.warnings = warnings
    return t


def answer_text(monkeypatch, text):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (text, True))


def select(tab, label):
    for cid, path in tab.db.categories():
        if path == label:
            tab.reload(cid)
            return cid
    raise KeyError(label)


def shape(tab):
    """樹狀結構轉成文字，方便比對。"""
    def walk(item, depth):
        rows = ["  " * depth + item.text(NAME_COL)]
        for i in range(item.childCount()):
            rows += walk(item.child(i), depth + 1)
        return rows
    return [row for i in range(tab.tree.topLevelItemCount()) for row in walk(tab.tree.topLevelItem(i), 0)]


def test_add_child_rename_and_order(tab, monkeypatch):
    select(tab, "開發")
    answer_text(monkeypatch, "專案A")
    tab.add_child()
    select(tab, "開發")
    answer_text(monkeypatch, "專案B")
    tab.add_child()
    assert shape(tab)[:3] == ["開發", "  專案A", "  專案B"]

    tab.move(-1)  # 專案B 上移
    assert shape(tab)[:3] == ["開發", "  專案B", "  專案A"]
    tab.move(-1)  # 已經是第一個，不動
    assert shape(tab)[:3] == ["開發", "  專案B", "  專案A"]

    answer_text(monkeypatch, "專案乙")
    tab.rename()
    assert shape(tab)[1] == "  專案乙"

    select(tab, "開發 › 專案A")
    answer_text(monkeypatch, "專案乙")  # 同一層重複
    tab.rename()
    assert tab.warnings and "專案乙" in tab.warnings[-1]

    answer_text(monkeypatch, "新分類")
    tab.add()
    assert shape(tab)[-1] == "新分類"


def test_progress(tab, monkeypatch):
    cid = select(tab, "開發")

    def fake_exec(dlg):
        dlg.enabled.setChecked(True)
        dlg.spin.setValue(70)
        return QDialog.Accepted

    monkeypatch.setattr(ProgressDialog, "exec", fake_exec)
    tab.edit_progress()
    assert tab.tree.currentItem().data(PROGRESS_COL, PROGRESS_ROLE) == 70
    assert {c.id: c.progress for c in tab.db.category_tree()}[cid] == 70

    dlg = ProgressDialog("開發", 70)
    dlg.enabled.setChecked(False)
    assert dlg.value() is None
    assert ProgressDialog("開發", None).value() is None


def test_move_to_and_drop(tab, monkeypatch):
    dev = select(tab, "開發")
    proj = tab.db.add_category("專案A", dev)
    tab.reload(proj)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("會議", True))
    tab.move_to()
    assert "會議 › 專案A" in dict((label, i) for i, label in tab.db.categories())

    # 選項不包含自己和自己的子分類
    seen = {}
    monkeypatch.setattr(QInputDialog, "getItem", lambda _p, _t, _l, items, *a: (seen.setdefault("items", items),
                                                                                 ("", False))[1])
    select(tab, "會議")
    tab.move_to()
    assert "會議" not in seen["items"] and "會議 › 專案A" not in seen["items"] and "（第一層）" in seen["items"]

    # 模擬拖曳：把「專案A」拖到第一層最前面
    item = tab.tree.currentItem().child(0)
    item.parent().removeChild(item)
    tab.tree.insertTopLevelItem(0, item)
    tab._dropped(item)
    assert shape(tab)[0] == "專案A"
    assert tab.db.category_label(proj) == "專案A"


def test_delete_subtree(tab):
    dev = select(tab, "開發")
    proj = tab.db.add_category("專案A", dev)
    entry = tab.db.save_entry(Entry(None, DATE, "09:00", "a", "doing", category_id=proj))
    tab.reload(dev)
    tab.delete()
    assert "開發" not in shape(tab)
    assert tab.db.get_entry(entry).category == ""


def test_manage_dialog_opens(qapp, tmp_path):
    dlg = manage_dialog.ManageDialog(Database(tmp_path))
    assert dlg.findChild(CategoryTab) is not None
