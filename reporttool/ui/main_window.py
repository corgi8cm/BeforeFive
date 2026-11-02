"""主視窗：檢視／編輯某天的紀錄、帶入未完成項目、工作回顧。"""
import os
import subprocess
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QHeaderView, QMainWindow,
    QMenu, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..config import Settings, load_instructions
from ..db import STATUS_LABELS, Database
from ..report import generate_report
from ..slides import export_slides
from ..sync import BundleError, export_bundle, import_bundle, live_counts
from .entry_dialog import STATUS_COLORS, EntryDialog
from .manage_dialog import ManageDialog

STATUS_ICONS = {"done": "✅", "doing": "🔄", "blocked": "⛔"}
ALL, UNCATEGORIZED = "all", "none"


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
        self.category_filter = QComboBox()
        self.category_filter.setToolTip("依分類篩選")
        self.category_filter.currentIndexChanged.connect(lambda _i: self.refresh())
        top.addWidget(self.category_filter)
        top.addStretch()
        top.addWidget(button("＋ 新增紀錄", self.new_entry, f"全域快捷鍵：{settings.hotkey}"))
        top.addWidget(button("帶入未完成項目", self.carry_over, "把上一個有紀錄的日子中「進行中／卡住」的項目帶進這一天"))
        self.review_btn = button("📋 工作回顧", self.review, "產生 report_日期 資料夾並開啟")
        self.review_btn.setStyleSheet("font-weight:bold; padding:4px 12px;")
        top.addWidget(self.review_btn)
        top.addWidget(button("📊 匯出 PPT", self.export_ppt, "一則紀錄一頁，存成 report_日期/report.pptx"))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["時間", "狀態", "分類", "Tags", "內容", "圖片"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        for col in (0, 1, 2, 3, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)

        bottom = QHBoxLayout()
        bottom.addWidget(button("編輯", self.edit_selected))
        bottom.addWidget(button("刪除", self.delete_selected))
        bottom.addWidget(button("追加今日進度", self.follow_up_selected, "替選取的工作新增今天的進度，會串成同一件工作的歷程"))
        bottom.addStretch()
        bottom.addWidget(button("管理分類與標籤", self.manage))
        bottom.addWidget(button("編輯 AI 指示", lambda: open_path(settings.instructions_path)))
        bottom.addWidget(button("開啟設定資料夾", lambda: open_path(settings.config_dir)))
        bottom.addWidget(button("開啟回報資料夾", self.open_output_dir))
        backup = QPushButton("備份 ▾")
        backup_menu = QMenu(backup)
        backup_menu.addAction("匯出備份…", self.export_backup)
        backup_menu.addAction("從備份匯入…", self.import_backup)
        backup.setMenu(backup_menu)
        backup.setToolTip("把所有紀錄與圖片匯出成一個 zip，或從 zip 合併回來")
        bottom.addWidget(backup)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        layout.addLayout(bottom)
        self.setCentralWidget(central)

        self.reload_categories()

    @property
    def current_date(self) -> str:
        return self.date_edit.date().toString("yyyy-MM-dd")

    def go_today(self):
        self.date_edit.setDate(QDate.currentDate())
        self.refresh()

    def reload_categories(self):
        """分類清單有變動時重建篩選選單，並盡量保留目前的選擇。"""
        current = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("全部分類", ALL)
        for cid, name in self.db.categories():
            self.category_filter.addItem(name, cid)
        self.category_filter.addItem("未分類", UNCATEGORIZED)
        self.category_filter.setCurrentIndex(max(self.category_filter.findData(current), 0))
        self.category_filter.blockSignals(False)
        self.refresh()

    def _visible(self, entry) -> bool:
        selected = self.category_filter.currentData()
        if selected in (None, ALL):
            return True
        if selected == UNCATEGORIZED:
            return entry.category_id is None
        return entry.category_id == selected

    def refresh(self):
        self.tree.clear()
        entries = [e for e in self.db.entries_for_date(self.current_date) if self._visible(e)]
        for e in entries:
            first_line = e.text.strip().splitlines()[0] if e.text.strip() else ""
            if e.carried_from:
                first_line = "↪ " + first_line
            n_img = len(e.images)
            n_ai = sum(i.include for i in e.images)
            img_text = f"{n_img}" + (f"（{n_ai} 給 AI）" if n_ai != n_img else "") if n_img else ""
            item = QTreeWidgetItem([e.time, f"{STATUS_ICONS[e.status]} {STATUS_LABELS[e.status]}", e.category,
                                    " ".join(f"#{t}" for t in e.tags), first_line, img_text])
            item.setForeground(1, Qt.GlobalColor.white)
            item.setBackground(1, QColor(STATUS_COLORS[e.status]))
            item.setToolTip(4, self._tooltip(e))
            item.setData(0, Qt.UserRole, e.id)
            self.tree.addTopLevelItem(item)
        counts = {s: sum(e.status == s for e in entries) for s in STATUS_LABELS}
        scope = "" if self.category_filter.currentData() in (None, ALL) else f"［{self.category_filter.currentText()}］"
        self.statusBar().showMessage(
            f"{self.current_date}{scope}：共 {len(entries)} 筆　" +
            "　".join(f"{STATUS_LABELS[s]} {counts[s]}" for s in STATUS_LABELS))

    def _tooltip(self, entry) -> str:
        history = self.db.history(entry.id)
        if not history:
            return entry.text
        lines = [f"{e.date[5:]} [{STATUS_LABELS[e.status]}] {e.text.strip().splitlines()[0]}" for e in history[-5:]]
        return entry.text + "\n\n先前進度：\n" + "\n".join(lines)

    def _selected_id(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        entry_id = item.data(0, Qt.UserRole)
        entry = self.db.get_entry(entry_id)
        menu = QMenu(self)
        for status, label in STATUS_LABELS.items():
            action = menu.addAction(f"{STATUS_ICONS[status]} 標記為{label}")
            action.setEnabled(status != entry.status)
            action.triggered.connect(lambda _=False, s=status: self.set_status(entry_id, s))
        menu.addSeparator()
        menu.addAction("追加今日進度…", self.follow_up_selected)
        menu.addAction("編輯…", self.edit_selected)
        menu.addAction("刪除", self.delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def set_status(self, entry_id: int, status: str):
        self.db.set_status(entry_id, status)
        self.refresh()

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

    def follow_up_selected(self):
        """替選取的工作新增今天的進度。"""
        entry_id = self._selected_id()
        if entry_id is None:
            return
        dlg = EntryDialog(self.db, date.today().isoformat(), parent=self,
                          follow_up_of=self.db.get_entry(entry_id))
        if dlg.exec():
            self.go_today()

    def manage(self):
        ManageDialog(self.db, self).exec()
        self.reload_categories()

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
        # 順便把 report.md 內容放進剪貼簿，到 AI 網頁直接貼上
        QApplication.clipboard().setText((folder / "report.md").read_text(encoding="utf-8"))
        self.statusBar().showMessage(f"已產生 {folder}，report.md 內容已複製到剪貼簿", 8000)
        os.startfile(folder)

    def export_ppt(self):
        d = self.current_date
        if not self.db.entries_for_date(d):
            QMessageBox.information(self, "匯出 PPT", f"{d} 沒有任何紀錄。")
            return
        try:
            path = export_slides(self.db, d, self.settings.output_dir, self.settings.ppt_template_path,
                                 self.settings.max_image_side)
        except PermissionError:
            QMessageBox.warning(self, "匯出 PPT", "report.pptx 可能正在 PowerPoint 中開啟，請先關閉再匯出。")
            return
        except Exception as e:
            QMessageBox.critical(self, "匯出失敗", str(e))
            return
        self.statusBar().showMessage(f"已匯出 {path}", 8000)
        os.startfile(path.parent)

    # ---- 備份 ----

    def export_backup(self):
        default = self.settings.output_dir / f"ReportTool備份_{date.today().isoformat()}.zip"
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "匯出備份", str(default), "ReportTool 備份 (*.zip)")
        if not path:
            return
        try:
            export_bundle(self.db, path)
        except Exception as e:
            QMessageBox.critical(self, "匯出失敗", str(e))
            return
        entries, images = live_counts(self.db)
        self.statusBar().showMessage(f"已匯出 {entries} 筆紀錄、{images} 張圖片到 {path}", 8000)

    def import_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "從備份匯入", str(self.settings.output_dir),
                                              "ReportTool 備份 (*.zip)")
        if not path:
            return
        if QMessageBox.question(
                self, "從備份匯入",
                "會把備份合併進目前的資料：同一筆紀錄以較晚修改的版本為準，目前資料中較新的修改不會被覆蓋。\n\n要繼續嗎？") \
                != QMessageBox.Yes:
            return
        try:
            stats = import_bundle(self.db, path)
        except BundleError as e:
            QMessageBox.warning(self, "無法匯入", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "匯入失敗", str(e))
            return
        self.reload_categories()
        QMessageBox.information(
            self, "匯入完成",
            f"新增 {stats['added']} 筆、更新 {stats['updated']} 筆、刪除 {stats['deleted']} 筆紀錄，"
            f"分類變動 {stats['categories']} 個。")

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
