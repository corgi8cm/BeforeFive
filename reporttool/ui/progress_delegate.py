"""在清單或樹狀檢視的欄位裡畫進度條（分類管理與主視窗共用）。"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

PROGRESS_ROLE = Qt.UserRole + 10  # 0~100 的整數；None 表示沒有設定進度
TRACK = QColor("#e3e6ea")
FILL = QColor("#2f6fd6")
DONE = QColor("#2e9d5b")
TEXT = QColor("#555555")


def progress_color(value: int) -> QColor:
    return DONE if value >= 100 else FILL


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index):
        value = index.data(PROGRESS_ROLE)
        if value is None:
            super().paint(painter, option, index)
            return
        # 先畫選取時的底色
        self.initStyleOption(option, index)
        option.text = ""
        style = option.widget.style() if option.widget else None
        if style:
            style.drawControl(QStyle.CE_ItemViewItem, option, painter, option.widget)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(4, 0, -4, 0)
        label_width = painter.fontMetrics().horizontalAdvance("100%") + 8
        bar = QRectF(rect.left(), rect.center().y() - 4, max(rect.width() - label_width, 10), 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(TRACK)
        painter.drawRoundedRect(bar, 4, 4)
        if value > 0:
            painter.setBrush(progress_color(value))
            painter.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * min(value, 100) / 100, bar.height()), 4, 4)
        painter.setPen(TEXT)
        painter.drawText(QRectF(bar.right() + 4, rect.top(), label_width - 4, rect.height()),
                         Qt.AlignVCenter | Qt.AlignRight, f"{value}%")
        painter.restore()
