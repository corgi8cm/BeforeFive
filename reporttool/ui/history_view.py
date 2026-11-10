"""延續工作的歷程：主視窗下方的預覽面板，以及右鍵「查看歷程…」開的視窗。"""
from datetime import date as Date
from html import escape

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter, QStackedWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from ..changes import char_segments, compare
from ..db import STATUS_ICONS, STATUS_LABELS, Database, Entry
from .changes_dialog import ADDED_BG, REMOVED_BG, _full_text, segments_html
from .entry_dialog import STATUS_COLORS

WEEKDAYS = "一二三四五六日"
CURRENT_BG = "#fff6d6"
THUMB_WIDTH = 160


def day_label(entry: Entry) -> str:
    try:
        weekday = WEEKDAYS[Date.fromisoformat(entry.date).weekday()]
    except ValueError:
        weekday = ""
    return f"{entry.date[5:]}（{weekday}）{entry.time}" if weekday else f"{entry.date[5:]} {entry.time}"


def entry_html(db: Database, entry: Entry, current: bool = False, images: bool = True) -> str:
    """一則紀錄的完整內容：日期、狀態、優先、分類、tag、標題、內容與附圖。"""
    status = (f'<span style="background:{STATUS_COLORS[entry.status]}; color:white;">'
              f'&nbsp;{STATUS_ICONS[entry.status]} {STATUS_LABELS[entry.status]}&nbsp;</span>')
    meta = [f"<b>{escape(day_label(entry))}</b>", status, f"優先{entry.priority}"]
    if entry.category:
        meta.append(escape(entry.category))
    if entry.tags:
        meta.append(escape(" ".join(f"#{t}" for t in entry.tags)))
    if current:
        meta.append('<span style="color:#b07800;">◀ 目前選取</span>')
    parts = ["　".join(meta)]
    if entry.title:
        parts.append(f"<b>【{escape(entry.title)}】</b>")
    body = escape(entry.text.strip().replace("\r\n", "\n")).replace("\n", "<br>")
    parts.append(body or '<span style="color:#888;">（沒有內容）</span>')
    if images and entry.images:
        thumbs = []
        for i, img in enumerate(entry.images, 1):
            url = QUrl.fromLocalFile(str(db.abs_path(img.path))).toString()
            note = "" if img.include else "（不給 AI）"
            thumbs.append(f'<a href="{url}"><img src="{url}" width="{THUMB_WIDTH}"></a>{note}')
        parts.append("&nbsp;".join(thumbs))
    style = f"background:{CURRENT_BG};" if current else ""
    return f'<table width="100%" cellpadding="6" style="{style}"><tr><td>{"<br>".join(parts)}</td></tr></table>'


def chain_html(db: Database, chain: list[Entry], current_id: int | None) -> str:
    """整條歷程，新的在上面；目前選取的那則加底色。"""
    blocks = [entry_html(db, e, e.id == current_id) for e in reversed(chain)]
    return '<hr style="border:0; border-top:1px solid #ddd;">'.join(blocks)


class HistoryPreview(QWidget):
    """主視窗清單下方的預覽：選取一筆紀錄，顯示它的完整內容；延續的工作顯示整條歷程。"""
    open_history = Signal(int)

    EMPTY = "選取一筆紀錄，這裡會顯示完整內容；延續的工作會列出每一天的進度（新的在上面）。"

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.entry_id = None
        self.header = QLabel()
        self.header.setStyleSheet("font-weight:bold;")
        self.open_btn = QPushButton("在新視窗查看歷程…")
        self.open_btn.clicked.connect(lambda: self.entry_id and self.open_history.emit(self.entry_id))
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.header, 1)
        top.addWidget(self.open_btn)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        self.show_entry(None)

    def show_entry(self, entry_id: int | None):
        self.entry_id = entry_id
        chain = self.db.chain(entry_id)
        self.open_btn.setEnabled(len(chain) > 1)
        if not chain:
            self.header.setText("預覽")
            self.view.setHtml(f'<span style="color:#888;">{self.EMPTY}</span>')
            return
        self.header.setText(f"這件工作的歷程（{len(chain)} 則，新的在上面）" if len(chain) > 1 else "預覽")
        self.view.setHtml(chain_html(self.db, chain, entry_id if len(chain) > 1 else None))


class HistoryDialog(QDialog):
    """右鍵「查看歷程…」：左邊列出這件工作的每一則，右邊看完整內容，或和前一則比較。"""

    def __init__(self, db: Database, entry_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.chain = list(reversed(db.chain(entry_id)))  # 新的在上面
        latest = self.chain[0] if self.chain else None
        self.setWindowTitle(f"工作歷程：{latest.summary[:40]}" if latest else "工作歷程")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1180, 700)

        self.header = QLabel(f"共 {len(self.chain)} 則（新的在上面）；勾選「與前一則比較」可以看出改了哪些字。")
        self.list = QListWidget()
        for e in self.chain:
            item = QListWidgetItem(f"{day_label(e)}　{STATUS_ICONS[e.status]} {STATUS_LABELS[e.status]}　{e.summary[:30]}")
            item.setToolTip(e.summary)
            if e.id == entry_id:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
                item.setBackground(QColor(CURRENT_BG))
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self.show_row)

        self.single = QTextBrowser()
        self.single.setOpenExternalLinks(True)
        self.fields = QLabel()
        self.fields.setWordWrap(True)
        self.old_title, self.new_title = QLabel(), QLabel()
        self.old_view, self.new_view = QTextBrowser(), QTextBrowser()
        panes = QHBoxLayout()
        for title, view in ((self.old_title, self.old_view), (self.new_title, self.new_view)):
            col = QVBoxLayout()
            title.setStyleSheet("font-weight:bold;")
            title.setWordWrap(True)
            col.addWidget(title)
            col.addWidget(view)
            panes.addLayout(col)
        diff = QWidget()
        diff_layout = QVBoxLayout(diff)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        diff_layout.addWidget(self.fields)
        diff_layout.addLayout(panes)
        diff_layout.addWidget(QLabel(f'<span style="background:{REMOVED_BG};">刪除的字</span>　'
                                     f'<span style="background:{ADDED_BG};">新增的字</span>'))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.single)
        self.stack.addWidget(diff)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.list)
        splitter.addWidget(self.stack)
        splitter.setSizes([420, 760])

        self.compare_box = QCheckBox("與前一則比較")
        self.compare_box.toggled.connect(lambda _c: self.show_row(self.list.currentRow()))
        close = QPushButton("關閉")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.compare_box)
        bottom.addStretch()
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

        rows = [i for i, e in enumerate(self.chain) if e.id == entry_id]
        if self.chain:
            self.list.setCurrentRow(rows[0] if rows else 0)

    def show_row(self, row: int):
        if row < 0 or row >= len(self.chain):
            return
        entry = self.chain[row]
        previous = self.chain[row + 1] if row + 1 < len(self.chain) else None
        if not self.compare_box.isChecked():
            self.stack.setCurrentIndex(0)
            self.single.setHtml(entry_html(self.db, entry))
            return
        self.stack.setCurrentIndex(1)
        if previous is None:
            self.fields.setText("這是第一則，沒有可以比較的前一則。")
            self.old_title.setText("")
            self.new_title.setText(day_label(entry))
            self.old_view.setHtml("")
            self.new_view.setPlainText(_full_text(entry))
            return
        self.fields.setText("變更：" + compare(previous, entry).summary())
        self.old_title.setText(f"{day_label(previous)}（前一則）")
        self.new_title.setText(f"{day_label(entry)}（這一則）")
        old, new = char_segments(_full_text(previous), _full_text(entry))
        self.old_view.setHtml(segments_html(old, REMOVED_BG))
        self.new_view.setHtml(segments_html(new, ADDED_BG))
