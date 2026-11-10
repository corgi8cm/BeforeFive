"""延續工作的歷程：主視窗下方的預覽面板，以及右鍵「查看歷程…」開的視窗。"""
from datetime import date as Date
from html import escape

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..db import STATUS_ICONS, STATUS_LABELS, Database, Entry
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
    """右鍵「查看歷程…」：左邊列出這件工作的每一則，右邊看完整內容與附圖，可以同時顯示前一則。"""

    def __init__(self, db: Database, entry_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.chain = list(reversed(db.chain(entry_id)))  # 新的在上面
        latest = self.chain[0] if self.chain else None
        self.setWindowTitle(f"工作歷程：{latest.summary[:40]}" if latest else "工作歷程")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1280, 720)

        self.header = QLabel(f"共 {len(self.chain)} 則（新的在上面）。")
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

        # 右邊：前一則（可以關掉）｜這一則，各自是完整內容與附圖
        self.prev_title, self.this_title = QLabel(), QLabel()
        self.prev_view, self.this_view = QTextBrowser(), QTextBrowser()
        self.prev_pane = QWidget()
        this_pane = QWidget()
        for pane, title, view in ((self.prev_pane, self.prev_title, self.prev_view),
                                  (this_pane, self.this_title, self.this_view)):
            title.setStyleSheet("font-weight:bold;")
            view.setOpenExternalLinks(True)
            col = QVBoxLayout(pane)
            col.setContentsMargins(0, 0, 0, 0)
            col.addWidget(title)
            col.addWidget(view)
        panes = QSplitter(Qt.Horizontal)
        panes.addWidget(self.prev_pane)
        panes.addWidget(this_pane)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.list)
        splitter.addWidget(panes)
        splitter.setSizes([380, 900])

        self.prev_box = QCheckBox("同時顯示前一則")
        self.prev_box.setChecked(True)
        self.prev_box.toggled.connect(lambda _c: self.show_row(self.list.currentRow()))
        # 往前（較舊）／往後（較新）一則；清單是新的在上面
        self.older_btn = QPushButton("◀ 往前一則")
        self.older_btn.setToolTip("看較舊的一則（Alt+←）")
        self.older_btn.clicked.connect(lambda: self.step(+1))
        self.newer_btn = QPushButton("往後一則 ▶")
        self.newer_btn.setToolTip("看較新的一則（Alt+→）")
        self.newer_btn.clicked.connect(lambda: self.step(-1))
        QShortcut(QKeySequence("Alt+Left"), self, activated=lambda: self.step(+1))
        QShortcut(QKeySequence("Alt+Right"), self, activated=lambda: self.step(-1))
        self.position = QLabel()
        close = QPushButton("關閉")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.prev_box)
        bottom.addStretch()
        bottom.addWidget(self.older_btn)
        bottom.addWidget(self.position)
        bottom.addWidget(self.newer_btn)
        bottom.addStretch()
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

        rows = [i for i, e in enumerate(self.chain) if e.id == entry_id]
        if self.chain:
            self.list.setCurrentRow(rows[0] if rows else 0)

    def step(self, delta: int):
        """+1 往前（較舊），-1 往後（較新）；到頭就停住。"""
        row = self.list.currentRow() + delta
        if 0 <= row < len(self.chain):
            self.list.setCurrentRow(row)

    def show_row(self, row: int):
        if row < 0 or row >= len(self.chain):
            return
        self.older_btn.setEnabled(row + 1 < len(self.chain))
        self.newer_btn.setEnabled(row > 0)
        # 由舊到新數第幾則
        self.position.setText(f"第 {len(self.chain) - row} / {len(self.chain)} 則")
        entry = self.chain[row]
        previous = self.chain[row + 1] if row + 1 < len(self.chain) else None
        self.this_title.setText("這一則")
        self.this_view.setHtml(entry_html(self.db, entry))
        self.prev_pane.setVisible(self.prev_box.isChecked())
        self.prev_title.setText("前一則")
        if previous is None:
            self.prev_view.setHtml('<span style="color:#888;">這是第一則，沒有前一則。</span>')
        else:
            self.prev_view.setHtml(entry_html(self.db, previous))
