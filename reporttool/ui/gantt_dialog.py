"""甘特圖視窗：用 QPainter 自己畫，不用其他套件。資料見 reporttool/gantt.py。"""
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QToolTip, QVBoxLayout,
    QWidget,
)

from ..db import SLOT_MINUTES, Database
from ..gantt import Bar, Gantt, Row, build_gantt, weekday
from .entry_dialog import STATUS_COLORS
from .range_dialog import RangeDialog

LABEL_W = 280  # 左邊標題欄
ROW_H = 24
HEADER_H = 34
PAD = 12  # 右邊與下方留白
PX_PER_HOUR = 96  # 單日：15 分鐘一格 24px
DAY_LINE = QColor("#b8bec6")  # 好幾天時每天的分界
GRID = QColor("#d5d9de")
MINOR_GRID = QColor("#eef0f3")  # 15 分鐘格線
MIN_SLOT_PX = 6  # 15 分鐘一格比這窄時不畫 15 分鐘格線
GROUP_BG = QColor("#f1f3f5")
WEEKEND_BG = QColor("#f7f7f9")
TEXT = QColor("#333333")
MUTED = QColor("#888888")


def multi_day_px_per_hour(hours: float) -> float:
    """好幾天時每小時的寬度：總時數少時寬一點（一週約 48px），很長時至少 16px（再窄就看不出 15 分鐘）。"""
    return max(16.0, min(48.0, 3200 / hours))


class GanttView(QWidget):
    entry_activated = Signal(int)

    def __init__(self, gantt: Gantt, parent=None):
        super().__init__(parent)
        self.gantt = gantt
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), Qt.white)
        self.setPalette(pal)
        if gantt.single_day:
            px_per_hour = PX_PER_HOUR
        else:
            px_per_hour = multi_day_px_per_hour(len(gantt.days) * gantt.day_length / 60)
        self.scale = px_per_hour / 60  # 每分鐘幾 px，單日和好幾天都依實際時間等比例
        width = LABEL_W + (gantt.axis_end - gantt.axis_start) * self.scale + PAD
        self.setFixedSize(int(width), HEADER_H + len(gantt.rows) * ROW_H + PAD)

    # ---- 座標 ----

    def x_of(self, value: float) -> float:
        return LABEL_W + (value - self.gantt.axis_start) * self.scale

    def row_rect(self, i: int) -> QRectF:
        return QRectF(0, HEADER_H + i * ROW_H, self.width(), ROW_H)

    def bar_rect(self, i: int, bar: Bar) -> QRectF:
        top = HEADER_H + i * ROW_H
        if bar.point:
            x = self.x_of(bar.start)
            return QRectF(x - 6, top + 6, 12, ROW_H - 12)
        x0, x1 = self.x_of(bar.start), self.x_of(bar.end)
        return QRectF(x0, top + 5, max(x1 - x0, 3), ROW_H - 10)

    def hit(self, pos) -> tuple[Row | None, Bar | None]:
        i = int((pos.y() - HEADER_H) // ROW_H)
        if pos.y() < HEADER_H or not 0 <= i < len(self.gantt.rows):
            return None, None
        row = self.gantt.rows[i]
        for bar in reversed(row.bars):  # 重疊時後畫的在上面
            if self.bar_rect(i, bar).adjusted(-2, -3, 2, 3).contains(pos):
                return row, bar
        return row, None

    # ---- 刻度 ----

    def hour_ticks(self) -> list[tuple[float, int]]:
        """（位置, 幾點）：每天時間窗內的整點，含時間窗頭尾。"""
        g = self.gantt
        w0, w1 = g.window
        return [(g.position(i, h * 60), h) for i in range(len(g.days)) for h in range(w0 // 60, w1 // 60 + 1)]

    def minor_ticks(self) -> list[float]:
        """整點之間每 15 分鐘的位置；一格太窄時不畫。"""
        g = self.gantt
        if SLOT_MINUTES * self.scale < MIN_SLOT_PX:
            return []
        w0, w1 = g.window
        return [g.position(i, m) for i in range(len(g.days)) for m in range(w0, w1, SLOT_MINUTES) if m % 60]

    def day_ticks(self) -> list[tuple[float, str, bool]]:
        """好幾天時：（這天開始的位置, 「11-02（一）」, 是否週末）。"""
        g = self.gantt
        if g.single_day:
            return []
        return [(g.position(i, g.window[0]), f"{day[5:]}（{weekday(day)}）", weekday(day) in "六日")
                for i, day in enumerate(g.days)]

    # ---- 畫圖 ----

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = self.gantt
        chart_bottom = HEADER_H + len(g.rows) * ROW_H
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1, 7))
        day_w = g.day_length * self.scale

        # 週末底色、分組底色與格線
        for value, _text, weekend in self.day_ticks():
            if weekend:
                p.fillRect(QRectF(self.x_of(value), HEADER_H, day_w, chart_bottom - HEADER_H), WEEKEND_BG)
        for i, row in enumerate(g.rows):
            if row.group:
                p.fillRect(self.row_rect(i), GROUP_BG)
        p.setPen(QPen(MINOR_GRID, 1))
        for value in self.minor_ticks():
            x = self.x_of(value)
            p.drawLine(QPointF(x, HEADER_H), QPointF(x, chart_bottom))
        p.setPen(QPen(GRID, 1))
        hour_top = HEADER_H - 4 if g.single_day else HEADER_H
        for value, _hour in self.hour_ticks():
            x = self.x_of(value)
            p.drawLine(QPointF(x, hour_top), QPointF(x, chart_bottom))
        p.setPen(QPen(DAY_LINE, 1))
        for value, _text, _weekend in self.day_ticks():
            x = self.x_of(value)
            p.drawLine(QPointF(x, 0), QPointF(x, chart_bottom))
        if not g.single_day:  # 最後一天的右邊界
            x = self.x_of(g.axis_end)
            p.drawLine(QPointF(x, 0), QPointF(x, chart_bottom))
        p.setPen(QPen(GRID, 1))
        p.drawLine(QPointF(LABEL_W, HEADER_H), QPointF(self.width() - PAD, HEADER_H))
        p.drawLine(QPointF(LABEL_W - 0.5, 0), QPointF(LABEL_W - 0.5, chart_bottom))

        # 刻度文字
        p.setFont(small)
        p.setPen(MUTED)
        if g.single_day:
            for value, hour in self.hour_ticks():
                x = self.x_of(value)
                p.drawText(QRectF(x - 30, 0, 60, HEADER_H - 6), Qt.AlignHCenter | Qt.AlignBottom, f"{hour:02d}:00")
        else:
            half = HEADER_H / 2
            for value, text, _weekend in self.day_ticks():
                p.drawText(QRectF(self.x_of(value), 0, day_w, half), Qt.AlignCenter, text)
            step = 1 if self.scale * 60 >= 30 else 3  # 每小時太窄時每 3 小時寫一次
            edges = {g.window[0] // 60, g.window[1] // 60}  # 每天頭尾在分界線上，不寫
            for value, hour in self.hour_ticks():
                if hour % step == 0 and hour not in edges:
                    x = self.x_of(value)
                    p.drawText(QRectF(x - 15, half, 30, half - 2), Qt.AlignHCenter | Qt.AlignBottom, f"{hour:02d}")

        # 每一列：標題與長條
        bold = QFont(self.font())
        bold.setBold(True)
        for i, row in enumerate(g.rows):
            rect = self.row_rect(i)
            p.setFont(bold if row.group else self.font())
            p.setPen(TEXT)
            indent = 6 if row.group else 18
            label_rect = QRectF(indent, rect.top(), LABEL_W - indent - 6, ROW_H)
            text = QFontMetrics(p.font()).elidedText(row.label, Qt.ElideRight, int(label_rect.width()))
            p.drawText(label_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            if row.note and not row.bars:  # 時段不在這天：寫出時段
                p.setFont(small)
                p.setPen(MUTED)
                p.drawText(QRectF(LABEL_W + 8, rect.top(), self.width() - LABEL_W - 8 - PAD, ROW_H),
                           Qt.AlignVCenter | Qt.AlignLeft, row.note)
            for bar in row.bars:
                color = QColor(STATUS_COLORS.get(bar.status, "#888888"))
                r = self.bar_rect(i, bar)
                p.setPen(QPen(color.darker(130), 1))
                p.setBrush(color)
                if bar.point:
                    c = r.center()
                    p.drawPolygon(QPolygonF([QPointF(c.x(), r.top()), QPointF(r.right(), c.y()),
                                             QPointF(c.x(), r.bottom()), QPointF(r.left(), c.y())]))
                else:
                    p.drawRoundedRect(r, 3, 3)
        p.end()

    # ---- 互動 ----

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            row, bar = self.hit(event.pos())
            text = bar.tooltip if bar else (row.tooltip if row and event.pos().x() < LABEL_W else "")
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mouseDoubleClickEvent(self, event):
        row, bar = self.hit(event.position().toPoint())
        target = bar.entry_id if bar else (row.entry_id if row and event.position().x() < LABEL_W else None)
        if target:
            self.entry_activated.emit(target)


class GanttDialog(QDialog):
    """主視窗「📈 甘特圖」：不會擋住主視窗，可以換範圍、存成 PNG。"""
    entry_activated = Signal(int)

    def __init__(self, db: Database, start: str, end: str, output_dir: Path, parent=None):
        super().__init__(parent)
        self.db = db
        self.output_dir = Path(output_dir)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1200, 640)

        self.title = QLabel()
        title_font = QFont(self.title.font())
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() + 1)
        self.title.setFont(title_font)
        hint = QLabel("滑鼠移到長條上看標題與時段，雙擊跳到那筆紀錄")
        hint.setStyleSheet("color:#666;")
        change = QPushButton("換範圍…")
        change.setAutoDefault(False)
        change.clicked.connect(self.change_range)
        save = QPushButton("存成 PNG…")
        save.setAutoDefault(False)
        save.clicked.connect(self.ask_save)
        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addSpacing(12)
        top.addWidget(hint)
        top.addStretch()
        top.addWidget(change)
        top.addWidget(save)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setStyleSheet("QScrollArea { background: white; }")
        self.empty = QLabel("這段期間沒有紀錄。")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color:#888; font-size:14px;")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.empty, 1)
        self.view: GanttView | None = None
        self.show_range(start, end)

    def show_range(self, start: str, end: str):
        self.start, self.end = start, end
        gantt = build_gantt(self.db, start, end)
        if start == end:
            label = f"{start}（{weekday(start)}）"
        else:
            label = f"{start}（{weekday(start)}）～ {end}（{weekday(end)}）"
        self.title.setText(label)
        self.setWindowTitle(f"甘特圖：{label}")
        self.view = GanttView(gantt)
        self.view.entry_activated.connect(self.entry_activated)
        self.scroll.setWidget(self.view)  # 舊的 view 由 QScrollArea 刪除
        self.scroll.setVisible(not gantt.empty)
        self.empty.setVisible(gantt.empty)

    def change_range(self):
        dlg = RangeDialog(QDate.currentDate(), self, "甘特圖範圍", "畫出來", "只選一天：橫軸是時間，每筆紀錄一條。"
                          "選好幾天：每件工作一列，幾天的時間接起來依實際時間畫。", QDate.fromString(self.start, "yyyy-MM-dd"))
        if dlg.exec() == QDialog.Accepted:
            self.show_range(*dlg.range())

    def default_png_path(self) -> Path:
        name = f"gantt_{self.start}.png" if self.start == self.end else f"gantt_{self.start}_to_{self.end}.png"
        return self.output_dir / name

    def save_png(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.view.grab().save(str(path), "PNG"):
            raise OSError(f"無法寫入 {path}")
        return path

    def ask_save(self):
        if self.view is None or self.view.gantt.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "存成 PNG", str(self.default_png_path()), "PNG 圖片 (*.png)")
        if not path:
            return
        try:
            self.save_png(path)
        except OSError as e:
            QMessageBox.warning(self, "存檔失敗", str(e))
