"""選分類：可以直接打字搜尋完整路徑，或按 ▾ 從樹狀清單點選。"""
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QCompleter, QHBoxLayout, QLineEdit, QToolButton, QTreeWidget, QTreeWidgetItem, QWidget,
)

from ..db import Database

UNCATEGORIZED_LABEL = "（未分類）"


class CategoryPopup(QTreeWidget):
    """點 ▾ 跳出的樹狀清單，點一下就選定並關閉。"""
    chosen = Signal(object)  # 分類 id，None 表示未分類

    def __init__(self, db: Database, current_id, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup)
        self.setHeaderHidden(True)
        self.setAttribute(Qt.WA_DeleteOnClose)
        none = QTreeWidgetItem([UNCATEGORIZED_LABEL])
        none.setData(0, Qt.UserRole, None)
        self.addTopLevelItem(none)
        items = {}
        for node in db.category_tree():
            item = QTreeWidgetItem([node.name])
            item.setData(0, Qt.UserRole, node.id)
            item.setToolTip(0, node.label)
            parent_item = items.get(node.parent_id)
            if parent_item:
                parent_item.addChild(item)
            else:
                self.addTopLevelItem(item)
            items[node.id] = item
        self.expandAll()
        self.setCurrentItem(items.get(current_id, none))
        self.itemClicked.connect(self._choose)
        self.itemActivated.connect(self._choose)

    def _choose(self, item, _column=0):
        self.chosen.emit(item.data(0, Qt.UserRole))
        self.close()


class CategoryPicker(QWidget):
    changed = Signal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._labels = {cid: label for cid, label in db.categories()}
        self._ids = {label: cid for cid, label in self._labels.items()}
        self._id = None

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(f"{UNCATEGORIZED_LABEL}　輸入可搜尋")
        self.edit.setClearButtonEnabled(True)
        self.edit.setToolTip("分類：輸入任何一段文字搜尋（例如「專案A」），或按右邊 ▾ 從清單選")
        completer = QCompleter(list(self._ids), self.edit)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setMaxVisibleItems(12)
        completer.activated.connect(self._choose_label)
        self.edit.setCompleter(completer)
        self.edit.editingFinished.connect(self.commit_text)

        self.button = QToolButton()
        self.button.setText("▾")
        self.button.setToolTip("從分類清單選")
        self.button.clicked.connect(self.show_popup)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)

    def category_id(self):
        self.commit_text()
        return self._id

    def set_category(self, category_id):
        self._id = category_id if category_id in self._labels else None
        self.edit.setText(self._labels.get(self._id, ""))
        self.edit.setCursorPosition(0)
        self.changed.emit()

    def _choose_label(self, label: str):
        self.set_category(self._ids.get(label))

    def commit_text(self):
        """把打的字對應到分類：完全相同、或只找到一個符合的就選它；空白是未分類；其他情況還原。"""
        text = self.edit.text().strip()
        if not text:
            target = None
        elif text in self._ids:
            target = self._ids[text]
        else:
            matches = [label for label in self._ids if text.lower() in label.lower()]
            target = self._ids[matches[0]] if len(matches) == 1 else self._id
        if target != self._id or self.edit.text() != self._labels.get(target, ""):
            self.set_category(target)

    def show_popup(self):
        popup = CategoryPopup(self.db, self._id, self)
        popup.chosen.connect(self.set_category)
        popup.resize(max(self.width(), 280), 320)
        popup.move(self.mapToGlobal(QPoint(0, self.height())))
        popup.show()
        self.popup = popup
