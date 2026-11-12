"""日期範圍整理。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image as PILImage
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QDialog

from reporttool.config import load_settings
from reporttool.db import Database, Entry
from reporttool.report import generate_range_report
from reporttool.ui import main_window
from reporttool.ui.main_window import MainWindow
from reporttool.ui.range_dialog import RangeDialog, month_range, week_range


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def add(db, date, time, text, images=()):
    return db.save_entry(Entry(None, date, time, text, "done", images=list(images)))


def test_range_report_lists_by_time_and_numbers_images_across_days(tmp_path):
    db = Database(tmp_path / "data")
    PILImage.new("RGB", (10, 10)).save(tmp_path / "a.png")
    add(db, "2026-10-30", "09:00", "範圍外")
    add(db, "2026-11-02", "14:00", "週一下午", [db.import_image_file(tmp_path / "a.png", "2026-11-02")])
    add(db, "2026-11-02", "09:00", "週一早上")
    add(db, "2026-11-04", "10:00", "週三", [db.import_image_file(tmp_path / "a.png", "2026-11-04")])
    add(db, "2026-11-09", "10:00", "範圍外")

    folder = generate_range_report(db, "2026-11-02", "2026-11-08", tmp_path / "out", "指示")
    assert folder.name == "report_2026-11-02_to_2026-11-08"
    md = (folder / "report.md").read_text(encoding="utf-8")
    assert md == (
        "# 工作回報整理請求（2026-11-02 ~ 2026-11-08）\n\n"
        "## 指示\n指示\n\n"
        "## 期間工作紀錄（共 3 件工作、3 筆紀錄，2 天）\n"
        "- [11-02（一）][09:00][完成][優先3] 週一早上\n"
        "- [11-02（一）][14:00][完成][優先3] 週一下午（![附圖1]）\n"
        "- [11-04（三）][10:00][完成][優先3] 週三（![附圖2]）\n"
        "\n[附圖1]: <01_週一下午.png>\n[附圖2]: <02_週三.png>\n"
    )
    assert sorted(p.name for p in folder.iterdir()) == ["01_週一下午.png", "02_週三.png", "report.md"]


def chain_db(tmp_path):
    """10-30 開始的工作，11-02、11-03 延續，11-03 又分岔出一支；中間 11-02 那筆被刪掉也照樣串起來。"""
    db = Database(tmp_path / "data")
    dev = db.categories()[0][0]
    meeting = db.categories()[1][0]
    a = db.save_entry(Entry(None, "2026-10-30", "16:00", "開始查", "doing", ["bug"], title="登入 timeout",
                            category_id=meeting, start="15:00", end="16:00"))
    b = db.save_entry(Entry(None, "2026-11-02", "11:00", "查 log", "doing", ["bug"], title="登入 timeout",
                            carried_from=a, category_id=meeting, start="09:00", end="11:00"))
    gone = db.save_entry(Entry(None, "2026-11-02", "12:00", "刪掉的", "doing", carried_from=b, start="11:00",
                               end="12:00"))
    db.save_entry(Entry(None, "2026-11-02", "10:00", "週會", "done", title="週會", start="09:30", end="10:00"))
    c = db.save_entry(Entry(None, "2026-11-03", "15:00", "改設定檔", "done", ["bug", "設定"], title="登入 timeout",
                            carried_from=gone, priority=1, category_id=dev, start="13:00", end="15:00"))
    db.delete_entry(gone)
    db.save_entry(Entry(None, "2026-11-03", "16:00", "補測試", "doing", title="補測試", carried_from=b,
                        category_id=dev, start="15:00", end="16:00"))
    return db, c


def test_range_report_merges_chain(tmp_path):
    db, _ = chain_db(tmp_path)
    md = (generate_range_report(db, "2026-11-02", "2026-11-08", tmp_path / "out", "指示") / "report.md").read_text(
        encoding="utf-8")
    records = md[md.index("## 期間工作紀錄"):md.index("\n## 工時統計")]
    stats = md[md.index("## 工時統計"):md.index("\n## 分類資訊")]
    assert stats == (
        "## 工時統計\n"
        "（每個分類都包含子分類；時段重疊的紀錄各自計算，沒有扣除重疊）\n"
        "- 開發：3h（55%）\n"  # 中途改分類：各筆的時數算在自己的分類
        "- 會議：2h（36%）\n"
        "- 未分類：0.5h（9%）\n"
        "- 合計：5.5h\n"
    )
    assert records == (
        "## 期間工作紀錄（共 2 件工作、4 筆紀錄，2 天）\n"
        # 分類、優先、tag 用範圍內最新那筆；分岔各自的最新狀態都列；有分岔時名稱用範圍內最早那筆
        "- 【登入 timeout】[最新：完成、進行中][優先3][分類：開發]（延續自 10-30）（範圍內 3 筆，合計 5h）\n"
        "  - [11-02（一）][09:00–11:00 2h][進行中] 查 log\n"
        "    ⇄ 與 10-30 相比：內容有更新\n"
        "  - [11-03（二）][13:00–15:00 2h][完成] 改設定檔\n"
        "  - [11-03（二）][15:00–16:00 1h][進行中] 【補測試】補測試\n"
        "    ⇄ 與 11-02 相比：標題 登入 timeout→補測試；分類 會議→開發；tag -#bug；內容有更新\n"
        "- [11-02（一）][09:30–10:00 0.5h][完成][優先3] 【週會】週會\n"
    )


def test_range_report_single_chain_uses_latest(tmp_path):
    db, _ = chain_db(tmp_path)
    md = (generate_range_report(db, "2026-11-03", "2026-11-03", tmp_path / "out", "指示") / "report.md").read_text(
        encoding="utf-8")
    # 範圍內這件工作的兩筆不相連（共同的上一筆在範圍外），仍是同一件工作
    assert "- 【登入 timeout】[最新：完成、進行中]" in md and "（範圍內 2 筆，合計 3h）" in md
    md = (generate_range_report(db, "2026-11-01", "2026-11-02", tmp_path / "out", "指示") / "report.md").read_text(
        encoding="utf-8")
    # 範圍內只有一筆：和單日的寫法相同，前面多日期
    assert "- [11-02（一）][09:00–11:00 2h][進行中][優先3][分類：會議][#bug] 【登入 timeout】查 log（延續自 10-30）" in md


def test_empty_range(tmp_path):
    folder = generate_range_report(Database(tmp_path / "data"), "2026-11-02", "2026-11-03", tmp_path / "out", "x")
    assert "（這段期間沒有紀錄）" in (folder / "report.md").read_text(encoding="utf-8")


def test_week_and_month_ranges():
    wed = QDate(2026, 11, 4)
    assert week_range(wed) == (QDate(2026, 11, 2), QDate(2026, 11, 8))
    assert week_range(wed, 1) == (QDate(2026, 10, 26), QDate(2026, 11, 1))
    assert month_range(wed, 1) == (QDate(2026, 10, 1), QDate(2026, 10, 31))
    assert month_range(QDate(2026, 1, 15), 1) == (QDate(2025, 12, 1), QDate(2025, 12, 31))


def test_dialog_defaults_and_swaps_reversed_dates(qapp):
    dlg = RangeDialog(QDate(2026, 11, 4))
    assert dlg.range() == ("2026-11-02", "2026-11-04")  # 本週一到今天
    dlg.set_range(QDate(2026, 11, 10), QDate(2026, 11, 1))
    assert dlg.range() == ("2026-11-01", "2026-11-10")


def test_main_window_range_review(qapp, tmp_path, monkeypatch):
    db = Database(tmp_path / "data")
    add(db, "2026-11-02", "09:00", "a")
    add(db, "2026-11-03", "09:00", "b")
    settings = load_settings(tmp_path / "config")
    settings.output_dir = tmp_path / "out"
    win = MainWindow(db, settings)

    def fake_exec(self):
        self.set_range(QDate(2026, 11, 1), QDate(2026, 11, 7))
        return QDialog.Accepted

    monkeypatch.setattr(RangeDialog, "exec", fake_exec)
    opened = []
    monkeypatch.setattr(main_window.os, "startfile", opened.append)
    win.review_range()
    assert opened == [tmp_path / "out" / "report_2026-11-01_to_2026-11-07"]
    assert QApplication.clipboard().text().startswith("# 工作回報整理請求（2026-11-01 ~ 2026-11-07）")


def test_hours_section_rolls_up_subcategories(tmp_path):
    db = Database(tmp_path / "data")
    dev = db.categories()[0][0]
    proj = db.add_category("專案A", dev)
    sub = db.add_category("登入頁", proj)
    db.save_entry(Entry(None, "2026-11-02", "12:00", "a", "done", category_id=sub, start="09:00", end="12:00"))
    db.save_entry(Entry(None, "2026-11-02", "12:00", "重疊", "done", category_id=dev, start="11:00", end="12:00"))
    db.save_entry(Entry(None, "2026-11-03", "09:00", "很短", "done", start="09:00", end="09:15"))
    db.save_entry(Entry(None, "2026-11-03", "10:00", "時長 0", "done", category_id=db.categories()[1][0],
                        start="10:00", end="10:00"))
    md = (generate_range_report(db, "2026-11-02", "2026-11-08", tmp_path / "out", "x") / "report.md").read_text(
        encoding="utf-8")
    assert (
        "- 開發：4h（94%）\n"  # 重疊的時段各自計算
        "- 開發 › 專案A：3h（71%）\n"
        "- 開發 › 專案A › 登入頁：3h（71%）\n"
        "- 未分類：0.25h（6%）\n"  # 時長 0 的「會議」不列
        "- 合計：4.25h\n"
    ) in md


def test_hours_share_below_one_percent(tmp_path):
    """3.0.1 起存檔都會取整到 15 分鐘，<1% 用沒存進資料庫的紀錄直接測。"""
    from reporttool.report import hours_lines

    db = Database(tmp_path / "data")
    dev = db.categories()[0][0]
    entries = [Entry(1, "2026-11-02", "18:00", "a", "done", category_id=dev, start="09:00", end="18:00"),
               Entry(2, "2026-11-03", "09:01", "很短", "done", start="09:00", end="09:01")]
    assert hours_lines(db, entries)[-2:] == ["- 未分類：0.02h（<1%）", "- 合計：9.02h"]


def test_hours_section_omitted_without_durations(tmp_path):
    db = Database(tmp_path / "data")
    add(db, "2026-11-02", "09:00", "a")
    md = (generate_range_report(db, "2026-11-02", "2026-11-02", tmp_path / "out", "x") / "report.md").read_text(
        encoding="utf-8")
    assert "工時統計" not in md


def test_sidebar_hours_column(qapp, tmp_path):
    from reporttool.ui import main_window as mw
    db = Database(tmp_path / "data")
    dev = db.categories()[0][0]
    proj = db.add_category("專案A", dev)
    db.save_entry(Entry(None, "2026-11-02", "10:30", "a", "done", category_id=proj, start="09:00", end="10:30"))
    db.save_entry(Entry(None, "2026-11-02", "11:00", "b", "done", start="10:30", end="11:00"))
    db.save_entry(Entry(None, "2026-11-03", "11:00", "別天", "done", category_id=dev, start="08:00", end="11:00"))
    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate(2026, 11, 2))
    hours = {}
    it = mw.QTreeWidgetItemIterator(win.sidebar)
    while it.value():
        hours[it.value().text(mw.SIDE_NAME)] = it.value().text(mw.SIDE_HOURS)
        it += 1
    assert hours["全部"] == "2h" and hours["開發"] == "1.5h" and hours["專案A"] == "1.5h"
    assert hours["未分類"] == "0.5h" and hours["會議"] == ""

    win.hours_box.setChecked(False)
    assert win.sidebar.isColumnHidden(mw.SIDE_HOURS)
    assert load_settings(tmp_path / "config").show_hours is False
    again = MainWindow(db, load_settings(tmp_path / "config"))
    assert again.sidebar.isColumnHidden(mw.SIDE_HOURS) and not again.hours_box.isChecked()
