"""v3.0.0：甘特圖。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QDialog

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.gantt import build_gantt
from reporttool.ui.gantt_dialog import HEADER_H, ROW_H, GanttDialog, GanttView
from reporttool.ui.main_window import MainWindow
from reporttool.ui.range_dialog import RangeDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "data")
    dev, meeting = db.categories()[0][0], db.categories()[1][0]
    proj = db.add_category("專案A", dev)

    def add(*args, **kw):
        return db.save_entry(Entry(*args, **kw))

    a = add(None, "2026-11-02", "11:00", "查 log", "doing", title="登入 timeout", category_id=meeting,
            start="09:00", end="11:00")
    add(None, "2026-11-02", "10:00", "週會", "done", title="週會", category_id=meeting, start="09:30", end="10:00")
    add(None, "2026-11-02", "16:00", "整理文件", "done", start="16:00", end="16:00")
    b = add(None, "2026-11-03", "15:00", "改設定檔", "review", title="登入 timeout", category_id=proj, carried_from=a,
            start="13:00", end="15:00")
    add(None, "2026-11-03", "16:00", "補 PR 說明", "done", title="登入 timeout", category_id=proj, carried_from=b,
        start="15:00", end="16:00")
    add(None, "2026-11-05", "19:30", "加班", "done", category_id=dev, start="18:00", end="19:30")
    return db


def labels(gantt):
    return [("# " if r.group else "") + r.label for r in gantt.rows]


def test_single_day_rows_and_axis(db):
    g = build_gantt(db, "2026-11-02", "2026-11-02")
    assert g.single_day
    assert labels(g) == ["# 會議", "登入 timeout", "週會", "# 未分類", "整理文件"]  # 依分類樹，組內依起始時間
    assert (g.axis_start, g.axis_end) == (9 * 60, 18 * 60)  # 至少 09:00~18:00
    bar = g.rows[1].bars[0]
    assert (bar.start, bar.end, bar.status, bar.point) == (540, 660, "doing", False)
    assert "09:00–11:00（2 小時）" in bar.tooltip and "【登入 timeout】" in bar.tooltip
    assert g.rows[4].bars[0].point  # 時長 0 畫成點
    late = build_gantt(db, "2026-11-05", "2026-11-05")
    assert (late.axis_start, late.axis_end) == (9 * 60, 20 * 60)  # 超過 18:00 就延長到整點


def test_multi_day_rows_merge_chain(db):
    g = build_gantt(db, "2026-11-01", "2026-11-07")
    # 每天的時間窗一樣：09:00 起，11-05 加班到 19:30，所以到 20:00；7 天接起來
    assert not g.single_day and g.window == (540, 1200) and g.days[0] == "2026-11-01" and len(g.days) == 7
    assert (g.axis_start, g.axis_end) == (0, 7 * 660)
    # 整條鏈一列，分類用範圍內最新那筆的（開發 › 專案A）
    assert labels(g) == ["# 開發", "加班", "# 開發 › 專案A", "登入 timeout", "# 會議", "週會", "# 未分類", "整理文件"]
    chain = g.rows[3]
    # 依實際時間畫：11-02（第 1 天）09:00–11:00；11-03（第 2 天）13:00–15:00、15:00–16:00
    assert [(b.start, b.end, b.status) for b in chain.bars] == [
        (660, 780, "doing"), (1320 + 240, 1320 + 360, "review"), (1320 + 360, 1320 + 420, "done")]
    assert "改設定檔" in chain.bars[1].tooltip and "11-03（二） 13:00–15:00（2 小時）" in chain.bars[1].tooltip
    assert "範圍內 3 筆、2 天，合計 5 小時" in chain.tooltip


def test_multi_day_window_and_scale(db, qapp):
    from reporttool.ui.gantt_dialog import LABEL_W, PAD
    g = build_gantt(db, "2026-11-02", "2026-11-03")
    assert g.window == (540, 1080)  # 沒有早晚的紀錄：09:00~18:00
    view = GanttView(g)
    assert view.scale * 15 >= 6  # 兩天：15 分鐘一格夠寬，畫 15 分鐘格線
    assert view.minor_ticks()[:3] == [15, 30, 45] and 540 not in view.minor_ticks()
    assert view.width() == int(LABEL_W + 2 * 540 * view.scale + PAD)
    i = next(i for i, r in enumerate(g.rows) if r.label == "登入 timeout")
    widths = [view.bar_rect(i, b).width() for b in g.rows[i].bars]
    assert widths == pytest.approx([120 * view.scale, 120 * view.scale, 60 * view.scale])  # 長條長度依時長等比例
    long = GanttView(build_gantt(db, "2026-10-01", "2026-11-30"))  # 兩個月：每小時 16px，不畫 15 分鐘格線
    assert long.scale * 60 == 16 and long.minor_ticks() == []


def test_empty_range(db):
    assert build_gantt(db, "2026-12-01", "2026-12-01").empty
    assert build_gantt(db, "2026-12-01", "2026-12-07").empty


def test_double_click_jumps_and_png(qapp, db, tmp_path):
    win = MainWindow(db, load_settings(tmp_path / "config"))
    dlg = win.open_gantt("2026-11-01", "2026-11-07")
    view = dlg.view
    rect = view.bar_rect(3, view.gantt.rows[3].bars[2])  # 鏈在 11-03 15:00–16:00 那段
    pos = rect.center()
    event = QMouseEvent(QMouseEvent.MouseButtonDblClick, QPointF(pos), QPointF(view.mapToGlobal(pos.toPoint())),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    view.mouseDoubleClickEvent(event)
    assert win.current_date == "2026-11-03"
    assert [db.get_entry(i).text for i in win._selected_ids()] == ["補 PR 說明"]

    row, bar = view.hit(QPoint(10, int(HEADER_H + ROW_H * 3.5)))  # 標題欄
    assert row.label == "登入 timeout" and bar is None

    path = dlg.save_png(tmp_path / "out" / "g.png")
    image = QImage(str(path))
    assert image.width() == view.width() and image.height() == view.height()
    assert dlg.default_png_path().name == "gantt_2026-11-01_to_2026-11-07.png"


def test_dialog_empty_and_change_range(qapp, db, tmp_path, monkeypatch):
    dlg = GanttDialog(db, "2026-12-01", "2026-12-01", tmp_path)
    assert dlg.empty.isVisibleTo(dlg) and not dlg.scroll.isVisibleTo(dlg)

    def fake_exec(self):
        self.set_range(QDate(2026, 11, 2), QDate(2026, 11, 2))
        return QDialog.Accepted

    monkeypatch.setattr(RangeDialog, "exec", fake_exec)
    dlg.change_range()
    assert dlg.view.gantt.single_day and not dlg.empty.isVisibleTo(dlg)
    assert dlg.default_png_path().name == "gantt_2026-11-02.png"


def test_range_dialog_defaults_to_viewed_day(qapp, db, tmp_path, monkeypatch):
    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate(2026, 11, 3))
    seen = []

    def fake_exec(self):
        seen.append(self.range())
        return QDialog.Accepted

    monkeypatch.setattr(RangeDialog, "exec", fake_exec)
    dlg = win.open_gantt()
    assert seen == [("2026-11-03", "2026-11-03")]
    assert dlg.view.gantt.single_day and dlg.start == "2026-11-03"


def test_toolbar_button_opens_range_then_gantt(qapp, db, tmp_path, monkeypatch):
    """真的按主視窗的按鈕：clicked 會帶 checked=False，不能被當成起始日期。"""
    from PySide6.QtWidgets import QPushButton

    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate(2026, 11, 2))
    seen = []

    def fake_exec(self):
        seen.append(self.range())
        return QDialog.Accepted

    monkeypatch.setattr(RangeDialog, "exec", fake_exec)
    button = next(b for b in win.findChildren(QPushButton) if "甘特圖" in b.text())
    button.click()
    assert seen == [("2026-11-02", "2026-11-02")]
    dialogs = [d for d in win.findChildren(GanttDialog) if d.isVisible()]
    assert len(dialogs) == 1 and dialogs[0].start == "2026-11-02"
