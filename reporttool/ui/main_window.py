"""主視窗：左邊分類樹（含進度），右邊某天的紀錄；帶入未完成項目、工作回顧。"""
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDateEdit, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
    QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from .. import __version__
from ..changes import changes_for
from ..config import Settings, load_instructions
from ..db import STATUS_ICONS, STATUS_LABELS, Category, Database, Entry, same_level_key
from ..report import generate_range_report, generate_report
from ..slides import export_slides
from ..sync import BundleError, export_bundle, import_bundle, live_counts
from .changes_dialog import ChangesDialog
from .entry_dialog import STATUS_COLORS, EntryDialog
from .manage_dialog import (
    PRIORITY_CHOICES, ManageDialog, ProgressDialog, ask_category_priority, delete_category_with_confirm, show_priority,
)
from .progress_delegate import PROGRESS_ROLE, SETTABLE_ROLE, ProgressDelegate
from .range_dialog import RangeDialog

ALL, UNCATEGORIZED = "all", "none"
ALL_LABEL = "全部"
UNCATEGORIZED_LABEL = "未分類"
GROUP_KEY = Qt.UserRole + 1  # 群組標題記住自己的 key，用來保留展開／收合狀態
SIDEBAR_KEY = Qt.UserRole + 2
PRIORITY_COLORS = {1: "#d64545", 2: "#e08a00"}
CATEGORY_COL, CONTENT_COL = 3, 5
SIDE_NAME, SIDE_PRIORITY, SIDE_COUNT, SIDE_PROGRESS = 0, 1, 2, 3


def sort_entries(entries: list[Entry]) -> list[Entry]:
    """同一層的紀錄：優先順序高（數字小）的在前，相同時依標題（沒有標題用內容第一行），再依時間。"""
    return sorted(entries, key=same_level_key)


@dataclass
class Group:
    """清單中的一個分類群組：直接屬於它的紀錄，以及有紀錄的子分類。"""
    category: Category
    entries: list[Entry]
    children: list["Group"] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries) + sum(g.count for g in self.children)


def outline(entries: list[Entry], nodes: list[Category], root_id=None) -> tuple[list[Entry], list[Group]]:
    """依分類樹分組。回傳（直接屬於 root_id 的紀錄, 子分類群組）；沒有紀錄的分類不列出，分類順序照手動排序。"""
    by_category: dict = {}
    for e in entries:
        by_category.setdefault(e.category_id, []).append(e)
    children: dict = {}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node)

    def build(node: Category) -> Group:
        subgroups = [g for g in (build(c) for c in children.get(node.id, [])) if g.count]
        return Group(node, sort_entries(by_category.get(node.id, [])), subgroups)

    direct = sort_entries(by_category.get(root_id, [])) if root_id is not None else []
    groups = [g for g in (build(n) for n in children.get(root_id, [])) if g.count]
    return direct, groups


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
        self._quick_dialog = None
        self._nodes: list[Category] = []
        self.setWindowTitle(f"工作回報工具 v{__version__}")
        self.resize(1100, 600)

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
        self.group_box = QCheckBox("依分類分組")
        self.group_box.setChecked(True)
        self.group_box.setToolTip("照分類樹分組；同一層依優先順序、再依標題排列")
        self.group_box.toggled.connect(lambda _c: self.refresh())
        top.addWidget(self.group_box)
        self.progress_box = QCheckBox("顯示進度")
        self.progress_box.setChecked(settings.show_progress)
        self.progress_box.setToolTip("在分類樹與分組標題顯示各分類的進度；關閉只是不顯示，已填的進度會保留")
        self.progress_box.toggled.connect(self._toggle_progress)
        top.addWidget(self.progress_box)
        top.addStretch()
        top.addWidget(button("＋ 新增紀錄", lambda: self.new_entry(),f"全域快捷鍵：{settings.hotkey}"))
        top.addWidget(button("帶入未完成項目", self.carry_over, "把上一個有紀錄的日子中未完成（不是完成或取消）的項目帶進這一天"))
        self.review_btn = button("📋 工作回顧", self.review, "產生 report_日期 資料夾並開啟")
        self.review_btn.setStyleSheet("font-weight:bold; padding:4px 12px;")
        top.addWidget(self.review_btn)
        top.addWidget(button("📅 範圍整理", self.review_range, "選擇日期範圍，整理期間內的所有紀錄（例如週報）"))
        top.addWidget(button("📊 匯出 PPT", self.export_ppt, "一則紀錄一頁，存成 report_日期/report.pptx"))

        # 左邊：分類樹（點選後右邊只顯示這個分類與子分類的紀錄）
        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderLabels(["分類", "優", "筆", "進度"])
        self.sidebar.setIndentation(14)
        self.sidebar.setMinimumWidth(260)
        self.sidebar.setItemDelegateForColumn(SIDE_PROGRESS, ProgressDelegate(self.sidebar))
        self.sidebar.setSelectionMode(QAbstractItemView.SingleSelection)
        side_header = self.sidebar.header()
        side_header.setStretchLastSection(False)
        side_header.setSectionResizeMode(SIDE_NAME, QHeaderView.Stretch)
        self.sidebar.setColumnWidth(SIDE_PRIORITY, 30)
        self.sidebar.setColumnWidth(SIDE_COUNT, 34)
        self.sidebar.setColumnWidth(SIDE_PROGRESS, 112)
        self.sidebar.currentItemChanged.connect(lambda *_: self.refresh())
        self.sidebar.itemDoubleClicked.connect(self._sidebar_double_clicked)
        self.sidebar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["時間", "優先", "狀態", "分類", "Tags", "內容", "圖片"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        # 雙擊群組標題是展開／收合，只有雙擊紀錄才編輯
        self.tree.itemDoubleClicked.connect(lambda item, _c: self.edit_selected() if self._entry_id(item) else None)
        self._collapsed = set()
        self.tree.itemCollapsed.connect(lambda item: self._collapsed.add(item.data(0, GROUP_KEY)))
        self.tree.itemExpanded.connect(lambda item: self._collapsed.discard(item.data(0, GROUP_KEY)))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        for col in (0, 1, 2, 3, 4, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(CONTENT_COL, QHeaderView.Stretch)

        # 分類樹下方的按鈕：選到分類時才能用
        self.progress_btn = button("設定進度…", self.edit_category_progress, "設定選取分類的進度（0～100%）")
        self.child_btn = button("新增子分類…", self.add_child_category, "在選取的分類底下新增子分類")
        side_buttons = QHBoxLayout()
        side_buttons.setContentsMargins(0, 0, 0, 0)
        side_buttons.addWidget(self.progress_btn)
        side_buttons.addWidget(self.child_btn)
        side_buttons.addStretch()
        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.sidebar, 1)
        side_layout.addLayout(side_buttons)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(side_panel)
        splitter.addWidget(self.tree)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 820])

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
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        self.setCentralWidget(central)

        self._apply_progress_visibility()
        self.reload_categories()

    @property
    def current_date(self) -> str:
        return self.date_edit.date().toString("yyyy-MM-dd")

    def go_today(self):
        self.date_edit.setDate(QDate.currentDate())
        self.refresh()

    # ---- 左邊分類樹 ----

    def reload_categories(self):
        """分類有變動時重建左邊的分類樹，並盡量保留目前的選擇。"""
        current = self.selected_category()
        self._nodes = self.db.category_tree()
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        all_item = QTreeWidgetItem([ALL_LABEL])
        all_item.setData(SIDE_NAME, SIDEBAR_KEY, ALL)
        self.sidebar.addTopLevelItem(all_item)
        items = {ALL: all_item}
        for node in self._nodes:
            item = QTreeWidgetItem([node.name])
            item.setData(SIDE_NAME, SIDEBAR_KEY, node.id)
            show_priority(item, SIDE_PRIORITY, node.priority)
            item.setData(SIDE_PROGRESS, PROGRESS_ROLE, node.progress)
            item.setData(SIDE_PROGRESS, SETTABLE_ROLE, True)
            item.setToolTip(SIDE_NAME, node.label)
            item.setToolTip(SIDE_PROGRESS, "雙擊設定進度，或在分類上按右鍵")
            parent = items.get(node.parent_id)
            if parent is not None and node.parent_id is not None:
                parent.addChild(item)
            else:
                self.sidebar.addTopLevelItem(item)
            items[node.id] = item
        none_item = QTreeWidgetItem([UNCATEGORIZED_LABEL])
        none_item.setData(SIDE_NAME, SIDEBAR_KEY, UNCATEGORIZED)
        self.sidebar.addTopLevelItem(none_item)
        items[UNCATEGORIZED] = none_item
        for item in items.values():
            item.setTextAlignment(SIDE_COUNT, Qt.AlignCenter)
        self.sidebar.expandAll()
        self.sidebar.setCurrentItem(items.get(current, all_item))
        self.sidebar.blockSignals(False)
        self.refresh()

    def selected_category(self):
        """左邊選到的分類：ALL、UNCATEGORIZED 或分類 id。"""
        item = self.sidebar.currentItem()
        return item.data(SIDE_NAME, SIDEBAR_KEY) if item else ALL

    def select_category(self, key):
        it = QTreeWidgetItemIterator(self.sidebar)
        while it.value():
            if it.value().data(SIDE_NAME, SIDEBAR_KEY) == key:
                self.sidebar.setCurrentItem(it.value())
                return
            it += 1

    def _update_sidebar_counts(self, entries: list[Entry]):
        """每個分類（含子分類）在這一天的紀錄數。"""
        parents = {n.id: n.parent_id for n in self._nodes}
        counts: dict = {ALL: len(entries), UNCATEGORIZED: 0}
        for e in entries:
            if e.category_id is None:
                counts[UNCATEGORIZED] += 1
            cid, seen = e.category_id, set()
            while cid is not None and cid not in seen:
                seen.add(cid)
                counts[cid] = counts.get(cid, 0) + 1
                cid = parents.get(cid)
        it = QTreeWidgetItemIterator(self.sidebar)
        while it.value():
            n = counts.get(it.value().data(SIDE_NAME, SIDEBAR_KEY), 0)
            it.value().setText(SIDE_COUNT, str(n) if n else "")
            it += 1

    def _toggle_progress(self, checked: bool):
        self.settings.save("show_progress", checked)
        self._apply_progress_visibility()
        self.refresh()

    def _apply_progress_visibility(self):
        """「顯示進度」關閉時，分類樹的進度欄、設定進度按鈕與分組標題的進度都不顯示。"""
        on = self.progress_box.isChecked()
        self.sidebar.setColumnHidden(SIDE_PROGRESS, not on)
        self.progress_btn.setVisible(on)

    def _sidebar_double_clicked(self, item, column):
        if column == SIDE_PROGRESS:
            self.edit_category_progress()
        elif column == SIDE_PRIORITY:
            self.edit_category_priority()

    def _sidebar_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        key = item.data(SIDE_NAME, SIDEBAR_KEY) if item else None
        menu = QMenu(self)
        if isinstance(key, int):
            self.sidebar.setCurrentItem(item)
            menu.addAction("新增紀錄…", lambda: self.new_entry(key))
            menu.addSeparator()
            priority_menu = menu.addMenu("優先度")
            current = next((n.priority for n in self._nodes if n.id == key), None)
            for p, text in PRIORITY_CHOICES.items():
                action = priority_menu.addAction(text, lambda p=p: self.set_category_priority(p))
                action.setCheckable(True)
                action.setChecked(p == current)
            if self.progress_box.isChecked():
                menu.addAction("設定進度…", self.edit_category_progress)
            menu.addAction("新增子分類…", self.add_child_category)
            menu.addAction("刪除分類…", self.delete_category)
            menu.addSeparator()
        menu.addAction("管理分類與標籤…", self.manage)
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def _selected_node(self):
        key = self.selected_category()
        return next((n for n in self._nodes if n.id == key), None)

    def set_category_priority(self, priority: int):
        node = self._selected_node()
        if node is not None:
            self.db.set_category_priority(node.id, priority)
            self.reload_categories()

    def edit_category_priority(self):
        node = self._selected_node()
        if node is not None:
            value = ask_category_priority(self, node.name, node.priority)
            if value is not None:
                self.set_category_priority(value)

    def edit_category_progress(self):
        key = self.selected_category()
        node = next((n for n in self._nodes if n.id == key), None)
        if node is None:
            return
        dlg = ProgressDialog(node.name, node.progress, self)
        if dlg.exec() == QDialog.Accepted:
            self.db.set_category_progress(node.id, dlg.value())
            self.reload_categories()

    def delete_category(self):
        """刪除選取的分類（子分類一起刪，紀錄移到上一層），刪除後選到上一層。"""
        key = self.selected_category()
        node = next((n for n in self._nodes if n.id == key), None)
        if node is None:
            return
        if delete_category_with_confirm(self, self.db, node.id, node.label):
            self.reload_categories()
            self.select_category(node.parent_id if node.parent_id is not None else ALL)

    def add_child_category(self):
        key = self.selected_category()
        node = next((n for n in self._nodes if n.id == key), None)
        if node is None:
            return
        name, ok = QInputDialog.getText(self, "新增子分類", f"在「{node.name}」底下新增：")
        if not ok:
            return
        try:
            self.select_category_after_reload(self.db.add_category(name, node.id))
        except ValueError as e:
            QMessageBox.warning(self, "新增子分類", str(e))

    def select_category_after_reload(self, category_id):
        self.reload_categories()
        self.select_category(category_id)

    # ---- 右邊紀錄清單 ----

    def _visible(self, entry, key) -> bool:
        if key in (None, ALL):
            return True
        if key == UNCATEGORIZED:
            return entry.category_id is None
        return entry.category_id in self._subtree(key)

    def _subtree(self, category_id) -> set:
        children: dict = {}
        for n in self._nodes:
            children.setdefault(n.parent_id, []).append(n.id)
        result, stack = set(), [category_id]
        while stack:
            cid = stack.pop()
            if cid not in result:
                result.add(cid)
                stack += children.get(cid, [])
        return result

    def _entry_item(self, e: Entry) -> QTreeWidgetItem:
        first_line = e.summary
        if e.carried_from:
            first_line = "↪ " + first_line
        n_img = len(e.images)
        n_ai = sum(i.include for i in e.images)
        img_text = f"{n_img}" + (f"（{n_ai} 給 AI）" if n_ai != n_img else "") if n_img else ""
        item = QTreeWidgetItem([e.time, str(e.priority), f"{STATUS_ICONS[e.status]} {STATUS_LABELS[e.status]}",
                                e.category, " ".join(f"#{t}" for t in e.tags), first_line, img_text])
        item.setTextAlignment(1, Qt.AlignCenter)
        if e.priority in PRIORITY_COLORS:
            item.setForeground(1, QColor(PRIORITY_COLORS[e.priority]))
            font = QFont(item.font(1))
            font.setBold(True)
            item.setFont(1, font)
        item.setToolTip(1, "優先順序：1 最優先，5 最不急")
        item.setForeground(2, Qt.GlobalColor.white)
        item.setBackground(2, QColor(STATUS_COLORS[e.status]))
        if e.status == "cancelled":
            font = QFont(item.font(CONTENT_COL))
            font.setStrikeOut(True)
            item.setFont(CONTENT_COL, font)
            item.setForeground(CONTENT_COL, QColor(STATUS_COLORS["cancelled"]))
        item.setToolTip(CONTENT_COL, self._tooltip(e))
        item.setData(0, Qt.UserRole, e.id)
        return item

    def _group_item(self, parent, title: str, count: int, key, progress=None) -> QTreeWidgetItem:
        show = progress is not None and self.progress_box.isChecked()
        text = f"{title}（{count}）" + (f"　進度 {progress}%" if show else "")
        item = QTreeWidgetItem([text])
        item.setFlags(Qt.ItemIsEnabled)  # 不能選取，避免被當成紀錄編輯或刪除
        font = QFont(item.font(0))
        font.setBold(True)
        item.setFont(0, font)
        item.setData(0, GROUP_KEY, key)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        item.setFirstColumnSpanned(True)
        return item

    def _add_groups(self, parent, groups: list[Group]):
        for g in groups:
            item = self._group_item(parent, g.category.name, g.count, ("cat", g.category.id), g.category.progress)
            item.addChildren([self._entry_item(e) for e in g.entries])
            self._add_groups(item, g.children)

    def refresh(self):
        self.tree.clear()
        day_entries = self.db.entries_for_date(self.current_date)
        self._update_sidebar_counts(day_entries)
        key = self.selected_category()
        is_category = isinstance(key, int)
        self.progress_btn.setEnabled(is_category)
        self.child_btn.setEnabled(is_category)
        entries = [e for e in day_entries if self._visible(e, key)]
        grouped = self.group_box.isChecked() and key != UNCATEGORIZED
        self.tree.setRootIsDecorated(grouped)
        self.tree.setColumnHidden(CATEGORY_COL, grouped)  # 分組時分類就是群組標題，不用重複
        if not grouped:
            self.tree.addTopLevelItems([self._entry_item(e) for e in sort_entries(entries)])
        else:
            direct, groups = outline(entries, self._nodes, None if key == ALL else key)
            self.tree.addTopLevelItems([self._entry_item(e) for e in direct])
            self._add_groups(None, groups)
            uncategorized = [e for e in entries if e.category_id is None] if key == ALL else []
            if uncategorized:
                group = self._group_item(None, UNCATEGORIZED_LABEL, len(uncategorized), ("none",))
                group.addChildren([self._entry_item(e) for e in sort_entries(uncategorized)])

        # 還原使用者收合過的群組；過程中不要觸發 itemExpanded 改到 _collapsed
        self.tree.blockSignals(True)
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            if item.childCount():
                item.setExpanded(item.data(0, GROUP_KEY) not in self._collapsed)
            it += 1
        self.tree.blockSignals(False)

        counts = {s: sum(e.status == s for e in entries) for s in STATUS_LABELS}
        current = self.sidebar.currentItem()
        scope = "" if key == ALL or current is None else f"［{current.toolTip(SIDE_NAME) or current.text(SIDE_NAME)}］"
        self.statusBar().showMessage(
            f"{self.current_date}{scope}：共 {len(entries)} 筆　" +
            "　".join(f"{STATUS_LABELS[s]} {counts[s]}" for s in STATUS_LABELS if counts[s]))

    def _tooltip(self, entry) -> str:
        text = f"【{entry.title}】\n{entry.text}" if entry.title else entry.text
        history = self.db.history(entry.id)
        if not history:
            return text
        lines = [f"{e.date[5:]} [{STATUS_LABELS[e.status]}] {e.summary}" for e in history[-5:]]
        return text + "\n\n先前進度：\n" + "\n".join(lines)

    @staticmethod
    def _entry_id(item):
        return item.data(0, Qt.UserRole) if item else None

    def entry_items(self) -> list[QTreeWidgetItem]:
        """清單中所有紀錄（不含群組標題）。"""
        items = []
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            if self._entry_id(it.value()):
                items.append(it.value())
            it += 1
        return items

    def _selected_id(self):
        return self._entry_id(self.tree.currentItem())

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        entry_id = self._entry_id(item)
        if entry_id is None:
            return
        self.tree.setCurrentItem(item)
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

    def new_entry(self, category_id: int | None = None):
        """category_id：從分類樹右鍵新增時，直接放在那個分類。"""
        dlg = EntryDialog(self.db, self.current_date, parent=self, category_id=category_id)
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
        if QMessageBox.question(self, "刪除紀錄", f"確定刪除這筆紀錄？\n\n[{entry.time}] {entry.summary[:60]}") \
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
            QMessageBox.information(self, "帶入未完成項目", f"{source} 沒有尚未帶入的未完成項目。")
            return
        lines = "\n".join(f"・[{STATUS_LABELS[e.status]}] {e.summary[:40]}" for e in candidates)
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
        except Exception as e:  # 顯示給使用者，避免靜默失敗
            QMessageBox.critical(self, "產生失敗", str(e))
            return
        self._report_done(folder)
        self.show_changes(d)

    def show_changes(self, date: str):
        """有延續的工作時，跳出和前一次紀錄的比較。"""
        changes = changes_for(self.db, self.db.entries_for_date(date))
        if not changes:
            return
        self.changes_dialog = ChangesDialog(changes, self)
        self.changes_dialog.show()
        self.changes_dialog.raise_()
        self.changes_dialog.activateWindow()

    def _report_done(self, folder):
        # 順便把 report.md 內容放進剪貼簿，到 AI 網頁直接貼上
        QApplication.clipboard().setText((folder / "report.md").read_text(encoding="utf-8"))
        self.statusBar().showMessage(f"已產生 {folder}，report.md 內容已複製到剪貼簿", 8000)
        os.startfile(folder)

    def review_range(self):
        dlg = RangeDialog(QDate.currentDate(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        start, end = dlg.range()
        if not self.db.entries_between(start, end):
            QMessageBox.information(self, "範圍整理", f"{start} ~ {end} 沒有任何紀錄。")
            return
        try:
            folder = generate_range_report(self.db, start, end, self.settings.output_dir,
                                           load_instructions(self.settings), self.settings.max_image_side)
        except Exception as e:  # 顯示給使用者，避免靜默失敗
            QMessageBox.critical(self, "產生失敗", str(e))
            return
        self._report_done(folder)

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


def bring_to_front(widget):
    widget.raise_()
    widget.activateWindow()
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(int(widget.winId()))
