"""管理分類（樹狀：新增、子分類、改名、進度、移動、排序、刪除）與標籤（改名／合併、刪除）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSlider, QSpinBox, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..db import Database
from .progress_delegate import PROGRESS_ROLE, ProgressDelegate


def _side_buttons(*buttons) -> QVBoxLayout:
    col = QVBoxLayout()
    for text, slot in buttons:
        b = QPushButton(text)
        b.setAutoDefault(False)
        b.clicked.connect(slot)
        col.addWidget(b)
    col.addStretch()
    return col


NAME_COL, COUNT_COL, PROGRESS_COL = 0, 1, 2
TOP_LEVEL = "（第一層）"


class ProgressDialog(QDialog):
    """設定分類進度：0~100，或不顯示進度。"""

    def __init__(self, name: str, value: int | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定進度")
        self.enabled = QCheckBox("顯示進度")
        self.enabled.setChecked(value is not None)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(10)
        self.spin = QSpinBox()
        self.spin.setRange(0, 100)
        self.spin.setSuffix(" %")
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.spin.setValue(value or 0)
        self.enabled.toggled.connect(self.slider.setEnabled)
        self.enabled.toggled.connect(self.spin.setEnabled)
        self.slider.setEnabled(value is not None)
        self.spin.setEnabled(value is not None)

        row = QHBoxLayout()
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"「{name}」的進度（手動填寫，不會自動計算）："))
        layout.addWidget(self.enabled)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.resize(360, 0)

    def value(self) -> int | None:
        return self.spin.value() if self.enabled.isChecked() else None


class CategoryTree(QTreeWidget):
    """可以拖曳調整順序與層級；放下後通知外層存回資料庫。"""

    def __init__(self, on_drop):
        super().__init__()
        self._on_drop = on_drop

    def dropEvent(self, event):
        moved = self.currentItem()
        super().dropEvent(event)
        if moved is not None:
            self._on_drop(moved)


class CategoryTab(QWidget):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.tree = CategoryTree(self._dropped)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["分類", "紀錄", "進度"])
        self.tree.setItemDelegateForColumn(PROGRESS_COL, ProgressDelegate(self.tree))
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(NAME_COL, QHeaderView.Stretch)
        self.tree.setColumnWidth(COUNT_COL, 60)
        self.tree.setColumnWidth(PROGRESS_COL, 170)
        self.tree.itemDoubleClicked.connect(self._double_clicked)

        row = QHBoxLayout()
        row.addWidget(self.tree, 1)
        row.addLayout(_side_buttons(
            ("新增分類…", self.add), ("新增子分類…", self.add_child), ("改名…", self.rename),
            ("設定進度…", self.edit_progress), ("移動到…", self.move_to), ("上移", lambda: self.move(-1)),
            ("下移", lambda: self.move(1)), ("刪除", self.delete)))
        layout = QVBoxLayout(self)
        hint = QLabel("拖曳可以調整順序或放進別的分類底下；雙擊名稱改名、雙擊進度設定進度。"
                      "回報與 PPT 會依這個順序排列。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(row)
        self.reload()

    # ---- 顯示 ----

    def reload(self, select_id=None):
        self.tree.clear()
        items = {}
        for node in self.db.category_tree():
            parent = items.get(node.parent_id)
            item = QTreeWidgetItem([node.name, str(self.db.category_usage(node.id)), ""])
            item.setData(NAME_COL, Qt.UserRole, node.id)
            item.setData(PROGRESS_COL, PROGRESS_ROLE, node.progress)
            item.setTextAlignment(COUNT_COL, Qt.AlignCenter)
            item.setToolTip(PROGRESS_COL, "雙擊設定進度" if node.progress is None else f"進度 {node.progress}%")
            if parent:
                parent.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            items[node.id] = item
        self.tree.expandAll()
        if select_id in items:
            self.tree.setCurrentItem(items[select_id])

    def _current(self):
        item = self.tree.currentItem()
        return (item.data(NAME_COL, Qt.UserRole), item.text(NAME_COL)) if item else (None, None)

    @staticmethod
    def _item_id(item):
        return item.data(NAME_COL, Qt.UserRole) if item else None

    def _siblings(self, item) -> list:
        parent = item.parent()
        if parent:
            return [parent.child(i) for i in range(parent.childCount())]
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _save_sibling_order(self, item):
        self.db.reorder_categories([self._item_id(i) for i in self._siblings(item)])

    def _warn(self, title, error):
        QMessageBox.warning(self, title, str(error))

    # ---- 操作 ----

    def _double_clicked(self, _item, column):
        if column == PROGRESS_COL:
            self.edit_progress()
        else:
            self.rename()

    def _ask_name(self, title, label, text=""):
        name, ok = QInputDialog.getText(self, title, label, text=text)
        return name if ok else None

    def add(self):
        name = self._ask_name("新增分類", "分類名稱：")
        if name is not None:
            try:
                self.reload(self.db.add_category(name))
            except ValueError as e:
                self._warn("新增分類", e)

    def add_child(self):
        cid, parent_name = self._current()
        if cid is None:
            QMessageBox.information(self, "新增子分類", "請先選擇要放在哪個分類底下。")
            return
        name = self._ask_name("新增子分類", f"在「{parent_name}」底下新增：")
        if name is not None:
            try:
                self.reload(self.db.add_category(name, cid))
            except ValueError as e:
                self._warn("新增子分類", e)

    def rename(self):
        cid, old = self._current()
        if cid is None:
            return
        name = self._ask_name("分類改名", "新的名稱：", old)
        if name is not None:
            try:
                self.db.rename_category(cid, name)
                self.reload(cid)
            except ValueError as e:
                self._warn("分類改名", e)

    def edit_progress(self):
        item = self.tree.currentItem()
        if item is None:
            return
        dlg = ProgressDialog(item.text(NAME_COL), item.data(PROGRESS_COL, PROGRESS_ROLE), self)
        if dlg.exec() == QDialog.Accepted:
            cid = self._item_id(item)
            self.db.set_category_progress(cid, dlg.value())
            self.reload(cid)

    def move_to(self):
        cid, name = self._current()
        if cid is None:
            return
        excluded = set(self.db.category_subtree(cid))
        targets = [(TOP_LEVEL, None)] + [(label, i) for i, label in self.db.categories() if i not in excluded]
        label, ok = QInputDialog.getItem(self, "移動分類", f"把「{name}」移到：", [t for t, _ in targets], 0, False)
        if ok:
            try:
                self.db.move_category(cid, dict(targets)[label])
            except ValueError as e:
                self._warn("移動分類", e)
            self.reload(cid)

    def move(self, step: int):
        item = self.tree.currentItem()
        if item is None:
            return
        siblings = self._siblings(item)
        row = siblings.index(item)
        if not 0 <= row + step < len(siblings):
            return
        ids = [self._item_id(i) for i in siblings]
        ids.insert(row + step, ids.pop(row))
        self.db.reorder_categories(ids)
        self.reload(self._item_id(item))

    def _dropped(self, item):
        """拖曳放下後：層級變了就移動，再存同一層的順序。"""
        cid = self._item_id(item)
        new_parent = self._item_id(item.parent())
        try:
            if new_parent != {c.id: c.parent_id for c in self.db.category_tree()}.get(cid):
                self.db.move_category(cid, new_parent)
            self._save_sibling_order(item)
        except ValueError as e:
            self._warn("移動分類", e)
        self.reload(cid)

    def delete(self):
        cid, name = self._current()
        if cid is None:
            return
        subtree = self.db.category_subtree(cid)
        used = sum(self.db.category_usage(i) for i in subtree)
        notes = []
        if len(subtree) > 1:
            notes.append(f"底下的 {len(subtree) - 1} 個子分類會一起刪除。")
        if used:
            notes.append(f"有 {used} 筆紀錄使用這些分類，刪除後會移到上一層。")
        note = "\n\n" + "\n".join(notes) if notes else ""
        if QMessageBox.question(self, "刪除分類", f"確定刪除分類「{name}」？{note}") == QMessageBox.Yes:
            self.db.delete_category(cid)
            self.reload()


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
        self.resize(640, 480)
        tabs = QTabWidget()
        tabs.addTab(CategoryTab(db), "分類")
        tabs.addTab(TagTab(db), "標籤")
        buttons = QDialogButtonBox()
        buttons.addButton("關閉", QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
