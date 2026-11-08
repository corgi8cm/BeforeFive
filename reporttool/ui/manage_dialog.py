"""管理分類（新增、改名、刪除、排序）與標籤（改名／合併、刪除）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..db import Database


def _side_buttons(*buttons) -> QVBoxLayout:
    col = QVBoxLayout()
    for text, slot in buttons:
        b = QPushButton(text)
        b.setAutoDefault(False)
        b.clicked.connect(slot)
        col.addWidget(b)
    col.addStretch()
    return col


class CategoryTab(QWidget):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.model().rowsMoved.connect(self._save_order)
        self.list.itemDoubleClicked.connect(lambda *_: self.rename())

        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(_side_buttons(("新增…", self.add), ("改名…", self.rename), ("刪除", self.delete),
                                    ("上移", lambda: self.move(-1)), ("下移", lambda: self.move(1))))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("拖曳或用上移／下移調整順序，回報與 PPT 會依這個順序排列。"))
        layout.addLayout(row)
        self.reload()

    def reload(self, select_id=None):
        self.list.clear()
        for cid, name in self.db.categories():
            item = QListWidgetItem(f"{name}（{self.db.category_usage(cid)} 筆）")
            item.setData(Qt.UserRole, cid)
            item.setData(Qt.UserRole + 1, name)
            self.list.addItem(item)
            if cid == select_id:
                self.list.setCurrentItem(item)

    def _current(self):
        item = self.list.currentItem()
        return (item.data(Qt.UserRole), item.data(Qt.UserRole + 1)) if item else (None, None)

    def _save_order(self, *_):
        self.db.reorder_categories([self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())])

    def add(self):
        name, ok = QInputDialog.getText(self, "新增分類", "分類名稱：")
        if ok:
            try:
                self.reload(self.db.add_category(name))
            except ValueError as e:
                QMessageBox.warning(self, "新增分類", str(e))

    def rename(self):
        cid, old = self._current()
        if cid is None:
            return
        name, ok = QInputDialog.getText(self, "分類改名", "新的名稱：", text=old)
        if ok:
            try:
                self.db.rename_category(cid, name)
                self.reload(cid)
            except ValueError as e:
                QMessageBox.warning(self, "分類改名", str(e))

    def delete(self):
        cid, name = self._current()
        if cid is None:
            return
        used = self.db.category_usage(cid)
        note = f"\n\n有 {used} 筆紀錄使用這個分類，刪除後會移到上一層。" if used else ""
        if QMessageBox.question(self, "刪除分類", f"確定刪除分類「{name}」？{note}") == QMessageBox.Yes:
            self.db.delete_category(cid)
            self.reload()

    def move(self, step: int):
        row = self.list.currentRow()
        if row < 0 or not 0 <= row + step < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row + step, item)
        self.list.setCurrentItem(item)
        self._save_order()


class TagTab(QWidget):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self.rename())

        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(_side_buttons(("改名／合併…", self.rename), ("刪除", self.delete)))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("改成已存在的 tag 名稱就會合併，例如把 #專按A 改成 #專案A。"))
        layout.addLayout(row)
        self.reload()

    def reload(self, select=None):
        self.list.clear()
        for name, count in self.db.tag_usage():
            item = QListWidgetItem(f"#{name}（{count} 筆）")
            item.setData(Qt.UserRole, name)
            self.list.addItem(item)
            if name == select:
                self.list.setCurrentItem(item)

    def _current(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def rename(self):
        old = self._current()
        if old is None:
            return
        new, ok = QInputDialog.getText(self, "tag 改名／合併", f"把 #{old} 改成：", text=old)
        new = new.strip().lstrip("#").strip()
        if not ok or not new or new == old:
            return
        if new in dict(self.db.tag_usage()) and QMessageBox.question(
                self, "合併 tag", f"#{new} 已經存在，要把 #{old} 合併進去嗎？") != QMessageBox.Yes:
            return
        self.db.rename_tag(old, new)
        self.reload(new)

    def delete(self):
        name = self._current()
        if name is None:
            return
        used = dict(self.db.tag_usage()).get(name, 0)
        note = f"\n\n會從 {used} 筆紀錄中移除這個 tag（紀錄本身保留）。" if used else ""
        if QMessageBox.question(self, "刪除 tag", f"確定刪除 #{name}？{note}") == QMessageBox.Yes:
            self.db.delete_tag(name)
            self.reload()


class ManageDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理分類與標籤")
        self.resize(460, 400)
        tabs = QTabWidget()
        tabs.addTab(CategoryTab(db), "分類")
        tabs.addTab(TagTab(db), "標籤")
        buttons = QDialogButtonBox()
        buttons.addButton("關閉", QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
