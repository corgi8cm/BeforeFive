"""選擇日期範圍，整理這段期間的所有紀錄。"""
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDateEdit, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


def week_range(day: QDate, weeks_ago: int = 0) -> tuple[QDate, QDate]:
    """day 所在那週（週一到週日）往前 weeks_ago 週。"""
    monday = day.addDays(1 - day.dayOfWeek()).addDays(-7 * weeks_ago)
    return monday, monday.addDays(6)


def month_range(day: QDate, months_ago: int = 0) -> tuple[QDate, QDate]:
    first = QDate(day.year(), day.month(), 1).addMonths(-months_ago)
    return first, first.addDays(first.daysInMonth() - 1)


REPORT_HINT = "產生 report_開始_to_結束 資料夾（report.md 與附圖），內容會複製到剪貼簿。"


class RangeDialog(QDialog):
    def __init__(self, today: QDate, parent=None, title: str = "整理日期範圍", ok_text: str = "整理",
                 hint: str = REPORT_HINT, day: QDate | None = None):
        """day：多一個「這一天」的選項，並預設選它（甘特圖用，預設是主視窗正在看的那天）。"""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.start_edit = self._date_edit()
        self.end_edit = self._date_edit()
        if day is None:
            self.set_range(week_range(today)[0], today)  # 本週預設到今天就好
        else:
            self.set_range(day, day)

        presets = QHBoxLayout()
        choices = [("這一天", (day, day))] if day is not None else []
        for label, (start, end) in choices + [
            ("本週", (week_range(today)[0], today)),
            ("上週", week_range(today, 1)),
            ("本月", (month_range(today)[0], today)),
            ("上個月", month_range(today, 1)),
        ]:
            b = QPushButton(label)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, s=start, e=end: self.set_range(s, e))
            presets.addWidget(b)

        dates = QHBoxLayout()
        dates.addWidget(QLabel("從"))
        dates.addWidget(self.start_edit)
        dates.addWidget(QLabel("到"))
        dates.addWidget(self.end_edit)

        ok = QPushButton(ok_text)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(ok)

        hint = QLabel(hint)
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666;")

        layout = QVBoxLayout(self)
        layout.addLayout(presets)
        layout.addLayout(dates)
        layout.addWidget(hint)
        layout.addLayout(buttons)

    @staticmethod
    def _date_edit() -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd (ddd)")
        return edit

    def set_range(self, start: QDate, end: QDate):
        self.start_edit.setDate(start)
        self.end_edit.setDate(end)

    def range(self) -> tuple[str, str]:
        """（開始, 結束），選反了會自動對調。"""
        a, b = sorted([self.start_edit.date(), self.end_edit.date()])
        return a.toString("yyyy-MM-dd"), b.toString("yyyy-MM-dd")
