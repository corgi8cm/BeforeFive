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
PX_PER_HOUR = 96  # 15 分鐘一格 24px
GRID = QColor("#d5d9de")
MINOR_GRID = QColor("#eef0f3")  # 15 分鐘格線
GROUP_BG = QColor("#f1f3f5")
WEEKEND_BG = QColor("#f7f7f9")
TEXT = QColor("#333333")
MUTED = QColor("#888888")


def day_px(days: int) -> float:
    """好幾天時每一天的寬度：天數少時寬一點，一個月左右約 28px。"""
    return max(28.0, min(120.0, 860 / days))


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
            self.scale = PX_PER_HOUR / 60  # 每分鐘幾 px
        else:
            self.scale = day_px(int(gantt.axis_end))
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
        inset = 0 if self.gantt.single_day else 1.5  # 好幾天時相鄰的天之間留一點縫
        return QRectF(x0 + inset, top + 5, max(x1 - x0 - 2 * inset, 3), ROW_H - 10)

    def hit(self, pos) -> tuple[Row | None, Bar | None]:
        i = int((pos.y() - HEADER_H) // ROW_H)
        if pos.y() < HEADER_H or not 0 <= i < len(self.gantt.rows):
            return None, None
        row = self.gantt.rows[i]
        for bar in row.bars:
            if self.bar_rect(i, bar).adjusted(-2, -3, 2, 3).contains(pos):
                return row, bar
        return row, None

    # ---- 畫圖 ----

    def ticks(self) -> list[tuple[float, str, bool]]:
        """（位置, 文字, 是否是週末）。"""
        g = self.gantt
        if g.single_day:
            return [(m, f"{m // 60:02d}:00", False) for m in range(int(g.axis_start), int(g.axis_end) + 1, 60)]
        result = []
        for i, day in enumerate(g.days):
            w = weekday(day)
            text = f"{day[5:]}\n{w}" if self.scale >= 40 else (f"{int(day[8:])}\n{w}")
            result.append((i, text, w in "六日"))
        return result

    def minor_ticks(self) -> list[int]:
        """單日檢視：整點之間每 15 分鐘的位置（分鐘）。"""
        g = self.gantt
        return [m for m in range(int(g.axis_start), int(g.axis_end), SLOT_MINUTES) if m % 60]

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = self.gantt
        chart_bottom = HEADER_H + len(g.rows) * ROW_H
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1, 7))

        # 週末底色與格線
        for value, text, weekend in self.ticks():
            x = self.x_of(value)
            if weekend:
                p.fillRect(QRectF(x, HEADER_H, self.scale, chart_bottom - HEADER_H), WEEKEND_BG)
        for i, row in enumerate(g.rows):
            if row.group:
                p.fillRect(self.row_rect(i), GROUP_BG)
        if g.single_day:  # 每 15 分鐘一條淡格線，整點的用下面的深色
            p.setPen(QPen(MINOR_GRID, 1))
            for m in self.minor_ticks():
                x = self.x_of(m)
                p.drawLine(QPointF(x, HEADER_H), QPointF(x, chart_bottom))
        p.setPen(QPen(GRID, 1))
        for value, _text, _weekend in self.ticks():
            x = self.x_of(value)
            p.drawLine(QPointF(x, HEADER_H - 4), QPointF(x, chart_bottom))
        if not g.single_day:  # 最後一天的右邊界
            x = self.x_of(g.axis_end)
            p.drawLine(QPointF(x, HEADER_H - 4), QPointF(x, chart_bottom))
        p.drawLine(QPointF(LABEL_W, HEADER_H), QPointF(self.width() - PAD, HEADER_H))
        p.drawLine(QPointF(LABEL_W - 0.5, 0), QPointF(LABEL_W - 0.5, chart_bottom))

        # 刻度文字
        p.setFont(small)
        p.setPen(MUTED)
        for value, text, _weekend in self.ticks():
            x = self.x_of(value)
            if g.single_day:
                p.drawText(QRectF(x - 30, 0, 60, HEADER_H - 6), Qt.AlignHCenter | Qt.AlignBottom, text)
            else:
                p.drawText(QRectF(x, 0, self.scale, HEADER_H - 4), Qt.AlignCenter, text)

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
                          "選好幾天：橫軸是日期，每件工作一列。", QDate.fromString(self.start, "yyyy-MM-dd"))
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
