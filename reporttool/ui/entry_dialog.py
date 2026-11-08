"""新增／編輯紀錄的視窗（全域快捷鍵叫出的也是這個）。"""
import os
import re
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QTime, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTimeEdit, QToolButton, QVBoxLayout, QWidget,
)

from ..db import DEFAULT_PRIORITY, PRIORITIES, STATUS_LABELS, STATUSES, Database, Entry, Image, parse_tags
from .category_picker import CategoryPicker

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
STATUS_COLORS = {"todo": "#6b7580", "doing": "#2f6fd6", "waiting": "#e08a00", "review": "#8a4fd1",
                 "done": "#2e9d5b", "cancelled": "#a0a0a0"}


def extract_images(mime) -> list:
    """從剪貼簿／拖曳資料取出圖片：檔案回傳 Path，截圖回傳 QImage。"""
    if mime.hasUrls():
        files = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
        files = [f for f in files if f.suffix.lower() in IMAGE_EXTS and f.is_file()]
        if files:
            return files
    if mime.hasImage():
        img = mime.imageData()
        if isinstance(img, QPixmap):
            img = img.toImage()
        if isinstance(img, QImage) and not img.isNull():
            return [img]
    return []


class PasteTextEdit(QPlainTextEdit):
    images_received = Signal(list)
    submit = Signal()

    def canInsertFromMimeData(self, source):
        return bool(extract_images(source)) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        images = extract_images(source)
        if images:
            self.images_received.emit(images)
        else:
            super().insertFromMimeData(source)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.submit.emit()
            return
        super().keyPressEvent(event)


class TagEdit(QLineEdit):
    """可輸入多個 tag，輸入時針對目前這個 tag 自動補完。"""

    def __init__(self, tags: list[str], parent=None):
        super().__init__(parent)
        self.setPlaceholderText("#專案A #bug（空白分隔）")
        self._completer = QCompleter(tags, self)
        self._completer.setWidget(self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.activated.connect(self._insert_completion)
        self.textEdited.connect(self._update_completer)

    def _token_start(self) -> int:
        before = self.text()[: self.cursorPosition()]
        m = re.search(r"[^\s,，、]*$", before)
        return m.start()

    def _update_completer(self, _text):
        token = self.text()[self._token_start(): self.cursorPosition()].lstrip("#")
        popup = self._completer.popup()
        if not token:
            popup.hide()
            return
        self._completer.setCompletionPrefix(token)
        if self._completer.completionCount() == 0:
            popup.hide()
            return
        rect = self.cursorRect()
        rect.setWidth(max(200, popup.sizeHintForColumn(0) + 30))
        self._completer.complete(rect)

    def _insert_completion(self, value):
        tag = value.data() if isinstance(value, QModelIndex) else value
        start, cursor = self._token_start(), self.cursorPosition()
        before = self.text()[:start] + f"#{tag} "
        self.setText(before + self.text()[cursor:].lstrip())
        self.setCursorPosition(len(before))

    def keyPressEvent(self, event):
        if self._completer.popup().isVisible() and event.key() in (
                Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
            event.ignore()
            return
        super().keyPressEvent(event)

    def tags(self) -> list[str]:
        return parse_tags(self.text())

    def set_tags(self, tags: list[str]):
        self.setText("".join(f"#{t} " for t in tags))

    def toggle_tag(self, tag: str):
        tags = self.tags()
        if tag in tags:
            tags.remove(tag)
        else:
            tags.append(tag)
        self.set_tags(tags)


class ImageItem(QFrame):
    removed = Signal(object)

    def __init__(self, pixmap: QPixmap, *, image: Image | None = None, src_path: Path | None = None,
                 qimage: QImage | None = None, include: bool = True, open_path: Path | None = None):
        super().__init__()
        self.image, self.src_path, self.qimage, self.open_path = image, src_path, qimage, open_path
        self.setFrameShape(QFrame.StyledPanel)

        thumb = QLabel()
        thumb.setFixedSize(120, 80)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setPixmap(pixmap.scaled(120, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if open_path:
            thumb.setToolTip("雙擊開啟原圖")
            thumb.mouseDoubleClickEvent = lambda _e: os.startfile(open_path)

        self.include_box = QCheckBox("給 AI")
        self.include_box.setChecked(include)
        self.include_box.setToolTip("取消勾選：不放進回報資料夾，只在 md 留附圖標記")
        remove = QToolButton()
        remove.setText("✕")
        remove.setToolTip("移除這張圖")
        remove.clicked.connect(lambda: self.removed.emit(self))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.include_box)
        row.addStretch()
        row.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(thumb)
        layout.addLayout(row)

    @property
    def include(self) -> bool:
        return self.include_box.isChecked()


class ImageStrip(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFixedHeight(140)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self._layout = QHBoxLayout(inner)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.addStretch()
        self.setWidget(inner)
        self.items: list[ImageItem] = []
        self.hide()

    def add(self, item: ImageItem):
        item.removed.connect(self._remove)
        self._layout.insertWidget(len(self.items), item)
        self.items.append(item)
        self.show()

    def _remove(self, item: ImageItem):
        self.items.remove(item)
        item.deleteLater()
        self.setVisible(bool(self.items))


class EntryDialog(QDialog):
    saved = Signal(int)

    def __init__(self, db: Database, entry_date: str, entry: Entry | None = None, parent=None,
                 follow_up_of: Entry | None = None):
        """entry：編輯既有紀錄；follow_up_of：替這筆工作追加一筆新進度（會串成同一件工作的歷程）。"""
        super().__init__(parent)
        self.db = db
        self.entry = entry
        self.follow_up_of = follow_up_of
        self.entry_date = entry.date if entry else entry_date
        if entry:
            title = "編輯紀錄"
        elif follow_up_of:
            title = "追加進度"
        else:
            title = "快速記錄"
        self.setWindowTitle(f"{title}（{self.entry_date}）")
        self.setAcceptDrops(True)
        self.resize(600, 500)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("標題（可省略）")
        self.text = PasteTextEdit()
        self.title_edit.returnPressed.connect(self.text.setFocus)  # Enter 跳到內容
        self.text.setPlaceholderText("做了什麼？\nCtrl+V 可貼上截圖，也可以把圖片檔拖進來")
        self.text.images_received.connect(self.add_images)
        self.text.submit.connect(self.save)

        # 狀態 + 時間
        self.status_group = QButtonGroup(self)
        status_row = QHBoxLayout()
        for i, key in enumerate(STATUSES, start=1):
            btn = QPushButton(STATUS_LABELS[key])
            btn.setToolTip(f"Alt+{i}")
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setStyleSheet(
                f"QPushButton:checked {{ background:{STATUS_COLORS[key]}; color:white; font-weight:bold; }}")
            btn.setProperty("status", key)
            self.status_group.addButton(btn)
            status_row.addWidget(btn)
            QShortcut(QKeySequence(f"Alt+{i}"), self, activated=btn.click)
        status_row.addStretch()
        status_row.addWidget(QLabel("優先"))
        self.priority_box = QComboBox()
        for p in PRIORITIES:
            self.priority_box.addItem({1: "1 最優先", 5: "5 最不急"}.get(p, str(p)), p)
        self.priority_box.setFixedWidth(100)
        self.priority_box.setToolTip("執行優先順序，主視窗同一組內優先的排前面")
        status_row.addWidget(self.priority_box)
        status_row.addWidget(QLabel("時間"))
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setButtonSymbols(QTimeEdit.NoButtons)
        self.time_edit.setFixedWidth(80)
        status_row.addWidget(self.time_edit)

        # 分類 + tags
        self.category_picker = CategoryPicker(db)
        self.tag_edit = TagEdit(db.all_tags())
        self.tag_edit.setToolTip("tag 只是標籤，不影響分組與排序")
        tag_row = QHBoxLayout()
        tag_row.addWidget(self.category_picker, 3)
        tag_row.addWidget(self.tag_edit, 2)
        chips = QHBoxLayout()
        chips.setSpacing(4)
        for tag in db.recent_tags(8):
            chip = QToolButton()
            chip.setText(f"#{tag}")
            chip.setAutoRaise(True)
            chip.setToolTip("點一下加入／移除")
            chip.clicked.connect(lambda _=False, t=tag: self.tag_edit.toggle_tag(t))
            chips.addWidget(chip)
        chips.addStretch()

        self.strip = ImageStrip()

        attach = QPushButton("附加圖片…")
        attach.setAutoDefault(False)
        attach.clicked.connect(self._pick_files)
        save = QPushButton("儲存 (Ctrl+Enter)")
        save.setAutoDefault(False)
        save.clicked.connect(self.save)
        cancel = QPushButton("取消 (Esc)")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(attach)
        bottom.addStretch()
        bottom.addWidget(cancel)
        bottom.addWidget(save)

        layout = QVBoxLayout(self)
        history = self._history_label()
        if history:
            layout.addWidget(history)
        layout.addWidget(self.title_edit)
        self.text.setMinimumHeight(120)
        layout.addWidget(self.text, 1)
        layout.addLayout(status_row)
        layout.addLayout(tag_row)
        layout.addLayout(chips)
        layout.addWidget(self.strip)
        layout.addLayout(bottom)

        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, activated=self.save)

        self._load(entry)
        self.text.setFocus()

    def _history_label(self) -> QLabel | None:
        """延續的工作在上方列出先前進度（最近 5 筆）。"""
        if self.entry:
            chain = self.db.history(self.entry.id)
        elif self.follow_up_of:
            chain = self.db.history(self.follow_up_of.id) + [self.follow_up_of]
        else:
            return None
        if not chain:
            return None
        lines = [f"{e.date[5:]} {e.time}　[{STATUS_LABELS[e.status]}]　{e.summary}"
                 for e in chain[-5:]]
        label = QLabel("先前進度：\n" + "\n".join(lines))
        label.setWordWrap(True)
        label.setStyleSheet("background:#f3f4f6; color:#444; padding:6px; border-radius:4px;")
        return label

    def _set_priority(self, priority: int):
        self.priority_box.setCurrentIndex(max(self.priority_box.findData(priority), 0))

    def _set_category(self, category_id):
        self.category_picker.set_category(category_id)

    def _load(self, entry: Entry | None):
        if entry is None:
            # 新紀錄：追加進度時沿用該工作的設定，否則沿用上一筆的分類與 tag
            source = self.follow_up_of or self.db.last_entry()
            status = self.follow_up_of.status if self.follow_up_of else "doing"
            for btn in self.status_group.buttons():
                btn.setChecked(btn.property("status") == status)
            self.time_edit.setTime(QTime.currentTime())
            self._set_priority(self.follow_up_of.priority if self.follow_up_of else DEFAULT_PRIORITY)
            if source:
                self._set_category(source.category_id)
                self.tag_edit.set_tags(source.tags)
            if self.follow_up_of:  # 同一件工作沿用標題
                self.title_edit.setText(self.follow_up_of.title)
            return
        for btn in self.status_group.buttons():
            btn.setChecked(btn.property("status") == entry.status)
        self._set_category(entry.category_id)
        self.time_edit.setTime(QTime.fromString(entry.time, "HH:mm"))
        self.title_edit.setText(entry.title)
        self._set_priority(entry.priority)
        self.text.setPlainText(entry.text)
        self.text.moveCursor(self.text.textCursor().MoveOperation.End)
        self.tag_edit.set_tags(entry.tags)
        for img in entry.images:
            path = self.db.abs_path(img.path)
            self.strip.add(ImageItem(QPixmap(str(path)), image=img, include=img.include, open_path=path))

    # ---- 圖片 ----

    def add_images(self, images: list):
        for img in images:
            if isinstance(img, QImage):
                self.strip.add(ImageItem(QPixmap.fromImage(img), qimage=img))
            else:
                self.strip.add(ImageItem(QPixmap(str(img)), src_path=Path(img), open_path=Path(img)))

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "選擇圖片", "", "圖片 (" + " ".join(f"*{e}" for e in sorted(IMAGE_EXTS)) + ")")
        self.add_images([Path(f) for f in files])

    def dragEnterEvent(self, event):
        if extract_images(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_images(extract_images(event.mimeData()))
        event.acceptProposedAction()

    # ---- 儲存 ----

    def save(self):
        title = self.title_edit.text().strip()
        text = self.text.toPlainText().strip()
        if not text and not title:
            QMessageBox.warning(self, "還沒寫內容", "請輸入這筆紀錄的標題或內容。")
            self.text.setFocus()
            return
        images = []
        for item in self.strip.items:
            if item.image is not None:
                img = item.image
            elif item.src_path is not None:
                img = self.db.import_image_file(item.src_path, self.entry_date)
            else:
                rel = self.db.new_image_path(self.entry_date, ".png")
                item.qimage.save(str(self.db.abs_path(rel)), "PNG")
                img = Image(None, rel, "截圖")
            img.include = item.include
            images.append(img)

        entry = self.entry or Entry(None, self.entry_date, "", "", "",
                                    carried_from=self.follow_up_of.id if self.follow_up_of else None)
        entry.category_id = self.category_picker.category_id()
        entry.time = self.time_edit.time().toString("HH:mm")
        entry.title = title
        entry.text = text
        entry.status = self.status_group.checkedButton().property("status")
        entry.priority = self.priority_box.currentData()
        entry.tags = self.tag_edit.tags()
        entry.images = images
        entry_id = self.db.save_entry(entry)
        self.saved.emit(entry_id)
        self.accept()
