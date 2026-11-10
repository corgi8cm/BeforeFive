"""工作回顧後顯示：延續的工作和前一次紀錄相比改了什麼。"""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
    QWidget,
)

from ..changes import EntryChange, char_segments
from ..db import span_text

REMOVED_BG = "#ffd7d5"
ADDED_BG = "#ccf2d1"
UNCHANGED_FG = "#888888"


def _full_text(entry) -> str:
    text = entry.text.strip().replace("\r\n", "\n")
    return f"【{entry.title}】\n{text}" if entry.title else text


def segments_html(parts: list[tuple[str, bool]], background: str) -> str:
    html = "".join(f'<span style="background:{background};">{escape(t)}</span>' if changed else escape(t)
                   for t, changed in parts)
    return html.replace("\n", "<br>") or '<span style="color:#888;">（空白）</span>'


class ChangesDialog(QDialog):
    def __init__(self, changes: list[EntryChange], parent=None):
        super().__init__(parent)
        self.changes = changes
        changed = sum(c.changed for c in changes)
        self.setWindowTitle("延續項目的變更")
        self.resize(900, 640)

        header = QLabel(f"這一天有 {len(changes)} 筆延續的工作，其中 {changed} 筆有更新。")
        self.list = QListWidget()
        for c in changes:
            item = QListWidgetItem(f"{span_text(c.entry, False)}　{c.entry.summary[:30]}　—　{c.summary()}")
            if not c.changed:
                item.setForeground(QColor(UNCHANGED_FG))
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self.show_change)

        self.fields = QLabel()
        self.fields.setWordWrap(True)
        self.old_title, self.new_title = QLabel(), QLabel()
        self.old_view, self.new_view = QTextBrowser(), QTextBrowser()
        panes = QHBoxLayout()
        for title, view in ((self.old_title, self.old_view), (self.new_title, self.new_view)):
            col = QVBoxLayout()
            title.setStyleSheet("font-weight:bold;")
            col.addWidget(title)
            col.addWidget(view)
            panes.addLayout(col)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.fields)
        detail_layout.addLayout(panes)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.list)
        splitter.addWidget(detail)
        splitter.setSizes([200, 420])

        close = QPushButton("關閉")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        hint = QLabel(f'<span style="background:{REMOVED_BG};">刪除的字</span>　'
                      f'<span style="background:{ADDED_BG};">新增的字</span>')
        buttons.addWidget(hint)
        buttons.addStretch()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(splitter)
        layout.addLayout(buttons)

        # 先停在第一筆有更新的
        first = next((i for i, c in enumerate(changes) if c.changed), 0)
        if changes:
            self.list.setCurrentRow(first)

    def show_change(self, row: int):
        if row < 0:
            return
        c = self.changes[row]
        self.fields.setText("變更：" + c.summary())
        self.old_title.setText(f"{c.previous.date}　{span_text(c.previous, False)}（前）")
        self.new_title.setText(f"{c.entry.date}　{span_text(c.entry, False)}（後）")
        old, new = char_segments(_full_text(c.previous), _full_text(c.entry))
        self.old_view.setHtml(segments_html(old, REMOVED_BG))
        self.new_view.setHtml(segments_html(new, ADDED_BG))
