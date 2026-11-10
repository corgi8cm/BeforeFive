"""搜尋紀錄：不限日期找標題、內容、tag、分類，雙擊跳到那一天並選取那筆。"""
from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHeaderView, QLabel, QLineEdit, QSplitter, QTextBrowser, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from ..db import STATUS_ICONS, STATUS_LABELS, Database, span_text
from .entry_dialog import STATUS_COLORS
from .history_view import WEEKDAYS, entry_html

HINT = "空白分隔多個詞（每個都要出現）；#詞 只找 tag。雙擊或按 Enter 跳到那一天。"


class SearchDialog(QDialog):
    entry_activated = Signal(int)

    def __init__(self, db: Database, query: str = "", parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("搜尋紀錄")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(900, 620)

        self.box = QLineEdit(query)
        self.box.setPlaceholderText("搜尋標題、內容、tag、分類…")
        self.box.setClearButtonEnabled(True)
        self._timer = QTimer(self, singleShot=True, interval=200)
        self._timer.timeout.connect(self.run)
        self.box.textChanged.connect(lambda _t: self._timer.start())
        self.box.returnPressed.connect(self._activate_first)

        self.count = QLabel(HINT)
        self.count.setStyleSheet("color:#666;")

        self.results = QTreeWidget()
        self.results.setHeaderLabels(["日期", "時段", "狀態", "分類", "內容"])
        self.results.setRootIsDecorated(False)
        self.results.setAlternatingRowColors(True)
        self.results.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.results.header()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        self.results.itemActivated.connect(self._activate)  # 雙擊或 Enter
        self.results.currentItemChanged.connect(self._preview)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.results)
        splitter.addWidget(self.preview)
        splitter.setSizes([400, 200])

        layout = QVBoxLayout(self)
        layout.addWidget(self.box)
        layout.addWidget(self.count)
        layout.addWidget(splitter, 1)

        self.run()

    def search(self, query: str):
        """從主視窗再次搜尋：換掉關鍵字並立刻顯示結果。"""
        self.box.setText(query)
        self.run()

    def run(self):
        self._timer.stop()
        query = self.box.text().strip()
        self.results.clear()
        self.preview.clear()
        if not query:
            self.count.setText(HINT)
            return
        limit = 500
        found = self.db.search(query, limit)
        for e in found:
            weekday = WEEKDAYS[date.fromisoformat(e.date).weekday()]
            item = QTreeWidgetItem([f"{e.date}（{weekday}）", span_text(e, False),
                                    f"{STATUS_ICONS[e.status]} {STATUS_LABELS[e.status]}", e.category, e.summary])
            item.setData(0, Qt.UserRole, e.id)
            item.setForeground(2, Qt.GlobalColor.white)
            item.setBackground(2, QColor(STATUS_COLORS[e.status]))
            item.setToolTip(4, f"【{e.title}】\n{e.text}" if e.title else e.text)
            self.results.addTopLevelItem(item)
        more = f"（只列出最新的 {limit} 筆）" if len(found) >= limit else ""
        self.count.setText(f"找到 {len(found)} 筆{more}。雙擊或按 Enter 跳到那一天。" if found else "沒有找到符合的紀錄。")
        if found:
            self.results.setCurrentItem(self.results.topLevelItem(0))

    def _preview(self, item, _previous=None):
        entry = self.db.get_entry(item.data(0, Qt.UserRole)) if item else None
        self.preview.setHtml(entry_html(self.db, entry) if entry else "")

    def _activate_first(self):
        if self._timer.isActive():
            self.run()
        item = self.results.currentItem() or self.results.topLevelItem(0)
        if item:
            self._activate(item)

    def _activate(self, item, _column=0):
        self.entry_activated.emit(item.data(0, Qt.UserRole))
