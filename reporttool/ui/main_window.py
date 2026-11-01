"""主視窗：檢視／編輯某天的紀錄、帶入未完成項目、工作回顧。"""
import os
import subprocess
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDateEdit, QHBoxLayout, QHeaderView, QMainWindow, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..config import Settings, load_instructions
from ..db import STATUS_LABELS, Database
from ..report import generate_report
from .entry_dialog import STATUS_COLORS, EntryDialog

STATUS_ICONS = {"done": "✅", "doing": "🔄", "blocked": "⛔"}


def open_path(path):
    """用系統預設程式開啟；.md 沒有關聯程式時改用記事本。"""
    try:
        os.startfile(path)
    except OSError:
        subprocess.Popen(["notepad.exe", str(path)])


class MainWindow(QMainWindow):
    def __init__(self, db: Database, settings: Settings):
        super().__init__()
        self.db = db
        self.settings = settings
        self.quitting = False
        self._quick_dialog = None
        self._tray_hint_shown = False
        self.setWindowTitle("工作回報工具")
        self.resize(820, 520)

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd (ddd)")
        self.date_edit.dateChanged.connect(self.refresh)

        def button(text, slot, tip=""):
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setToolTip(tip)
            return b

        top = QHBoxLayout()
        top.addWidget(button("◀", lambda: self.date_edit.setDate(self.date_edit.date().addDays(-1))))
        top.addWidget(self.date_edit)
        top.addWidget(button("▶", lambda: self.date_edit.setDate(self.date_edit.date().addDays(1))))
        top.addWidget(button("今天", self.go_today))
        top.addStretch()
        top.addWidget(button("＋ 新增紀錄", self.new_entry, f"全域快捷鍵：{settings.hotkey}"))
        top.addWidget(button("帶入未完成項目", self.carry_over, "把上一個有紀錄的日子中「進行中／卡住」的項目帶進這一天"))
        self.review_btn = button("📋 工作回顧", self.review, "產生 report_日期 資料夾並開啟")
        self.review_btn.setStyleSheet("font-weight:bold; padding:4px 12px;")
        top.addWidget(self.review_btn)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["時間", "狀態", "Tags", "內容", "圖片"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        header = self.tree.header()
        header.setStretchLastSection(False)
        for col in (0, 1, 2, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        bottom = QHBoxLayout()
        bottom.addWidget(button("編輯", self.edit_selected))
        bottom.addWidget(button("刪除", self.delete_selected))
        bottom.addStretch()
        bottom.addWidget(button("編輯 AI 指示", lambda: open_path(settings.instructions_path)))
        bottom.addWidget(button("開啟設定資料夾", lambda: open_path(settings.config_dir)))
        bottom.addWidget(button("開啟回報資料夾", self.open_output_dir))

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        layout.addLayout(bottom)
        self.setCentralWidget(central)

        self.refresh()

    @property
    def current_date(self) -> str:
        return self.date_edit.date().toString("yyyy-MM-dd")

    def go_today(self):
        self.date_edit.setDate(QDate.currentDate())
        self.refresh()

    def refresh(self):
        self.tree.clear()
        entries = self.db.entries_for_date(self.current_date)
        for e in entries:
            first_line = e.text.strip().splitlines()[0] if e.text.strip() else ""
            if e.carried_from:
                first_line = "↪ " + first_line
            n_img = len(e.images)
            n_ai = sum(i.include for i in e.images)
            img_text = f"{n_img}" + (f"（{n_ai} 給 AI）" if n_ai != n_img else "") if n_img else ""
            item = QTreeWidgetItem([e.time, f"{STATUS_ICONS[e.status]} {STATUS_LABELS[e.status]}",
                                    " ".join(f"#{t}" for t in e.tags), first_line, img_text])
            item.setForeground(1, Qt.GlobalColor.white)
            item.setBackground(1, QColor(STATUS_COLORS[e.status]))
            item.setToolTip(3, e.text)
            item.setData(0, Qt.UserRole, e.id)
            self.tree.addTopLevelItem(item)
        counts = {s: sum(e.status == s for e in entries) for s in STATUS_LABELS}
        self.statusBar().showMessage(
            f"{self.current_date}：共 {len(entries)} 筆　" +
            "　".join(f"{STATUS_LABELS[s]} {counts[s]}" for s in STATUS_LABELS))

    def _selected_id(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    # ---- 新增／編輯／刪除 ----

    def open_quick_entry(self):
        """全域快捷鍵：一律記到今天。視窗已開著就拉到最前面。"""
        if self._quick_dialog is not None and self._quick_dialog.isVisible():
            dlg = self._quick_dialog
        else:
            dlg = EntryDialog(self.db, date.today().isoformat())
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
            dlg.setAttribute(Qt.WA_DeleteOnClose)
            dlg.saved.connect(lambda _id: self.refresh())
            dlg.finished.connect(lambda _r: setattr(self, "_quick_dialog", None))
            self._quick_dialog = dlg
            dlg.show()
        bring_to_front(dlg)

    def new_entry(self):
        dlg = EntryDialog(self.db, self.current_date, parent=self)
        if dlg.exec():
            self.refresh()

    def edit_selected(self):
        entry_id = self._selected_id()
        if entry_id is None:
            return
        dlg = EntryDialog(self.db, self.current_date, self.db.get_entry(entry_id), parent=self)
        if dlg.exec():
            self.refresh()

    def delete_selected(self):
        entry_id = self._selected_id()
        if entry_id is None:
            return
        entry = self.db.get_entry(entry_id)
        if QMessageBox.question(self, "刪除紀錄", f"確定刪除這筆紀錄？\n\n[{entry.time}] {entry.text[:60]}") \
                == QMessageBox.Yes:
            self.db.delete_entry(entry_id)
            self.refresh()

    # ---- 帶入 / 回顧 ----

    def carry_over(self):
        target = self.current_date
        source = self.db.previous_date_with_entries(target)
        if source is None:
            QMessageBox.information(self, "帶入未完成項目", f"{target} 之前沒有任何紀錄。")
            return
        candidates = self.db.carry_over_candidates(source, target)
        if not candidates:
            QMessageBox.information(self, "帶入未完成項目", f"{source} 沒有尚未帶入的「進行中／卡住」項目。")
            return
        lines = "\n".join(f"・[{STATUS_LABELS[e.status]}] {e.text.strip().splitlines()[0][:40]}" for e in candidates)
        if QMessageBox.question(self, "帶入未完成項目",
                                f"將 {source} 的 {len(candidates)} 筆項目帶入 {target}：\n\n{lines}") \
                == QMessageBox.Yes:
            self.db.carry_over(source, target)
            self.refresh()

    def review(self):
        d = self.current_date
        if not self.db.entries_for_date(d) and QMessageBox.question(
                self, "工作回顧", f"{d} 沒有任何紀錄，仍要產生嗎？") != QMessageBox.Yes:
            return
        try:
            folder = generate_report(self.db, d, self.settings.output_dir, load_instructions(self.settings),
                                     self.settings.max_image_side)
        except Exception as e:  # 顯示給使用者，避免在系統匣模式下靜默失敗
            QMessageBox.critical(self, "產生失敗", str(e))
            return
        self.statusBar().showMessage(f"已產生 {folder}", 8000)
        os.startfile(folder)

    def open_output_dir(self):
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.settings.output_dir)

    # ---- 視窗行為 ----

    def show_and_raise(self):
        if not self.isVisible():
            self.go_today()
        self.showNormal()
        bring_to_front(self)

    def closeEvent(self, event):
        if self.quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown and hasattr(self, "tray"):
            self.tray.showMessage("工作回報工具", f"程式仍在系統匣執行，按 {self.settings.hotkey} 可快速記錄。")
            self._tray_hint_shown = True


def bring_to_front(widget):
    widget.raise_()
    widget.activateWindow()
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(int(widget.winId()))
