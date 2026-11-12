"""時段可以設定日期（跨日、和紀錄不同天），帶入未完成項目沿用原本的時段；工時同一件工作相同時段只算一次。"""
import json
import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from reporttool.db import (
    SCHEMA_VERSION, Database, Entry, check_times, parse_day, resolve_span, span_text,
)
from reporttool.gantt import build_gantt
from reporttool.report import generate_range_report, group_works, report_span
from reporttool.sync import BundleError, import_bundle, merge, snapshot

DATE = "2026-11-02"  # conftest：今天 2026-11-02 17:00
TESTS = Path(__file__).parent


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def span(e):
    return e.span_key


# ---- 規則 ----

@pytest.mark.parametrize("text, value", [
    ("", ""), ("11-10", "2026-11-10"), ("11/9", "2026-11-09"), ("1110", "2026-11-10"),
    ("2025-12-31", "2025-12-31"), ("2025/1/2", "2025-01-02"), ("20251231", "2025-12-31"), (" 11－10 ", "2026-11-10"),
])
def test_parse_day(text, value):
    assert parse_day(text, DATE) == value


@pytest.mark.parametrize("text", ["13-01", "02-30", "abc", "1-2-3", "123"])
def test_parse_day_rejects(text):
    with pytest.raises(ValueError):
        parse_day(text, DATE)


def test_check_times_with_dates():
    check_times("22:00", "02:00", "2026-11-01", "2026-11-02")  # 跨午夜
    check_times("09:00", "09:00", "2026-11-01", "2026-11-01")
    for args in [("10:00", "09:00", "2026-11-01", "2026-11-01"), ("09:00", "10:00", "2026-11-02", "2026-11-01"),
                 ("09:00", "10:00", "2026-11-01", ""), ("09:00", "10:00", "2026-11-31", "2026-12-01")]:
        with pytest.raises(ValueError):
            check_times(*args)


def rs(start, end, start_date="", end_date="", date=DATE, now="17:00", editing=False):
    return resolve_span(start, end, date=date, today=DATE, now=now, editing=editing, follow_up=False,
                        start_date_text=start_date, end_date_text=end_date)


def test_resolve_span():
    assert rs("09:00", "10:00") == (DATE, "09:00", DATE, "10:00")  # 日期空白＝紀錄那天
    assert rs("22:00", "02:00", "10-31", "11-01") == ("2026-10-31", "22:00", "2026-11-01", "02:00")
    assert rs("09:00", "18:00", "11-01") == ("2026-11-01", "09:00", DATE, "17:00")  # 只填起始日期；今天結束最晚到現在
    assert rs("09:00", "10:00", "11-01", "11-05") == ("2026-11-01", "09:00", DATE, "17:00")  # 結束在未來也改成現在
    assert rs("09:00", "18:00", "", "11-05", date="2026-10-30") == ("2026-10-30", "09:00", "2026-11-05", "18:00")
    with pytest.raises(ValueError, match="填了日期也要填"):
        rs("", "", "11-01")
    with pytest.raises(ValueError, match="結束不能早於起始"):
        rs("09:00", "10:00", "11-02", "11-01")
    with pytest.raises(ValueError, match="看不懂的日期"):
        rs("09:00", "10:00", "明天")


# ---- 存檔、顯示 ----

def test_save_cross_day_span_and_display(tmp_path):
    db = Database(tmp_path)
    e = db.get_entry(db.save_entry(Entry(None, DATE, "17:00", "跨好幾天", "doing", start="09:00", end="18:00",
                                         start_date="2026-10-30", end_date=DATE)))
    assert span(e) == ("2026-10-30", "09:00", DATE, "18:00")
    assert e.minutes == 3 * 24 * 60 + 9 * 60  # 實際經過的時間
    assert span_text(e) == "10-30 09:00–11-02 18:00（81 小時）"
    assert report_span(e) == "10-30 09:00–11-02 18:00 81h"

    same_day = Entry(None, DATE, "17:00", "a", "done", start="09:00", end="10:30", start_date="2026-10-30",
                     end_date="2026-10-30")
    assert span_text(same_day) == "10-30 09:00–10:30（1 小時 30 分）"
    zero = Entry(None, DATE, "17:00", "a", "done", start="09:00", end="09:00", start_date="2026-10-30",
                 end_date="2026-10-30")
    assert span_text(zero) == report_span(zero) == "10-30 09:00"
    other_year = Entry(None, "2026-01-02", "17:00", "a", "done", start="22:00", end="02:00",
                       start_date="2025-12-31", end_date="2026-01-01")
    assert span_text(other_year, False) == "2025-12-31 22:00–01-01 02:00"
    normal = db.get_entry(db.save_entry(Entry(None, DATE, "11:00", "b", "done", start="10:00", end="11:00")))
    assert (normal.start_date, normal.end_date) == (DATE, DATE) and span_text(normal, False) == "10:00–11:00"


def test_previous_end_only_counts_spans_ending_that_day(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "10:00", "早上", "done", start="09:00", end="10:00"))
    db.save_entry(Entry(None, DATE, "12:00", "補記昨天的", "done", start="11:00", end="12:00",
                        start_date="2026-11-01", end_date="2026-11-01"))
    e = db.get_entry(db.save_entry(Entry(None, DATE, "13:00", "新的", "done")))
    assert span(e) == (DATE, "10:00", DATE, "13:00")


def test_cross_midnight_orders_by_start(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "08:00", "早上", "done", start="07:00", end="08:00"))
    db.save_entry(Entry(None, DATE, "02:00", "前一晚開始", "done", start="22:00", end="02:00",
                        start_date="2026-11-01", end_date=DATE))
    assert [e.text for e in db.entries_for_date(DATE)] == ["前一晚開始", "早上"]


# ---- 帶入未完成項目 ----

@pytest.fixture
def carried(tmp_path):
    """11-01 一筆跨日的工作，帶入 11-02、再帶入 11-03（時段都沿用）；11-03 另外追加了一段進度。"""
    db = Database(tmp_path / "data")
    dev = db.categories()[0][0]
    db.save_entry(Entry(None, "2026-11-01", "18:00", "部署", "doing", category_id=dev, title="部署",
                        start="22:00", end="02:00", start_date="2026-10-31", end_date="2026-11-01"))
    (b,) = db.carry_over("2026-11-01", DATE, "09:00")
    (c,) = db.carry_over(DATE, "2026-11-03", "09:00")
    db.save_entry(Entry(None, "2026-11-03", "15:00", "收尾", "done", category_id=dev, title="部署", carried_from=c,
                        start="13:00", end="15:00"))
    return db


def test_carry_over_keeps_span_across_chain(carried):
    db = carried
    spans = [span(e) for d in ("2026-11-01", DATE, "2026-11-03") for e in db.entries_for_date(d)]
    assert spans[:3] == [("2026-10-31", "22:00", "2026-11-01", "02:00")] * 3
    assert spans[3] == ("2026-11-03", "13:00", "2026-11-03", "15:00")


def test_hours_count_same_span_once(carried, tmp_path):
    db = carried
    entries = db.entries_between("2026-11-01", "2026-11-03")
    (work,) = group_works(db, entries)
    assert work.minutes == 4 * 60 + 2 * 60  # 帶入的兩筆不重複算
    md = (generate_range_report(db, "2026-11-01", "2026-11-03", tmp_path / "out", "x") / "report.md").read_text(
        encoding="utf-8")
    assert "（範圍內 4 筆，合計 6h）" in md
    assert "- 開發：6h（100%）\n- 合計：6h\n" in md
    assert "[11-02（一）][10-31 22:00–11-01 02:00 4h][進行中]" in md  # 每一筆還是寫出自己的時段
    # 範圍只有 11-02、11-03 時，最早那筆不在範圍內，帶入的那筆照樣算一次
    md = (generate_range_report(db, DATE, "2026-11-03", tmp_path / "out", "x") / "report.md").read_text(
        encoding="utf-8")
    assert "- 合計：6h\n" in md


def test_sidebar_hours_count_same_span_once(qapp, carried, tmp_path):
    from PySide6.QtCore import QDate

    from reporttool.config import load_settings
    from reporttool.ui.main_window import ALL, SIDE_HOURS, SIDEBAR_KEY, SIDE_NAME, MainWindow
    db = carried
    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate(2026, 11, 3))
    item = next(win.sidebar.topLevelItem(i) for i in range(win.sidebar.topLevelItemCount())
                if win.sidebar.topLevelItem(i).data(SIDE_NAME, SIDEBAR_KEY) == ALL)
    assert item.text(SIDE_HOURS) == "6h"  # 這天兩筆：帶入的 4h ＋ 收尾 2h


# ---- 甘特圖 ----

def test_gantt_single_day_clips_and_notes(carried):
    g = build_gantt(carried, "2026-11-01", "2026-11-01")
    (row,) = [r for r in g.rows if not r.group]
    assert [(b.start, b.end) for b in row.bars] == [(0, 120)]  # 只畫 11-01 00:00~02:00 那段
    assert g.axis_start == 0

    g = build_gantt(carried, DATE, DATE)  # 帶入的紀錄時段在前兩天：不畫長條，寫出時段
    (row,) = [r for r in g.rows if not r.group]
    assert row.bars == [] and row.note == "時段在 10-31 22:00–11-01 02:00" and not g.empty


def test_gantt_multi_day_covers_span_days(carried):
    g = build_gantt(carried, "2026-10-30", "2026-11-03")
    (row,) = [r for r in g.rows if not r.group]
    # 跨夜的時段讓時間窗變成整天；三筆起訖相同只畫一次，跨夜分成 10-31、11-01 兩段（接在一起）；11-03 是收尾那段
    assert g.window == (0, 1440)
    day = 1440
    assert [(b.start, b.end, b.status) for b in row.bars] == [
        (day + 22 * 60, 2 * day, "doing"), (2 * day, 2 * day + 120, "doing"), (4 * day + 780, 4 * day + 900, "done")]
    latest = carried.entries_for_date("2026-11-03")[0]
    assert row.bars[0].entry_id == latest.id  # 相同時段用最晚那筆
    assert "範圍內 4 筆、3 天，合計 6 小時" in row.tooltip


# ---- 資料庫升級、資料包 ----

def test_migrates_v9_database(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "10:00", "a", "done", start="09:00", end="10:00"))
    before = db.conn.execute("SELECT id, updated_at FROM entries").fetchall()
    db.conn.execute("ALTER TABLE entries DROP COLUMN start_date")
    db.conn.execute("ALTER TABLE entries DROP COLUMN end_date")
    db.conn.execute("PRAGMA user_version = 9")
    db.conn.commit()
    db.close()
    db = Database(tmp_path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 10
    (e,) = db.entries_for_date(DATE)
    assert span(e) == (DATE, "09:00", DATE, "10:00") and (e.start_date, e.end_date) == (DATE, DATE)
    assert db.conn.execute("SELECT id, updated_at FROM entries").fetchall() == before


def test_bundle_round_trip_with_dates(carried, tmp_path):
    data = snapshot(carried)
    assert data["version"] == 4
    dst = Database(tmp_path / "other")
    merge(dst, data, lambda rel: None)
    assert snapshot(dst)["entries"] == data["entries"]
    assert [span(e) for e in dst.entries_for_date(DATE)] == [("2026-10-31", "22:00", "2026-11-01", "02:00")]


def test_v3_bundle_spans_on_record_date(tmp_path):
    folder = tmp_path / "v3"
    folder.mkdir()
    shutil.copy(TESTS / "legacy" / "bundle_v3.json", folder / "bundle.json")
    db = Database(tmp_path / "data")
    import_bundle(db, folder)
    golden = json.loads((TESTS / "golden" / "basic" / "bundle.json").read_text(encoding="utf-8"))
    assert snapshot(db)["entries"] == golden["entries"]


@pytest.mark.parametrize("change", [
    {"start_date": None}, {"end_date": ""}, {"start_date": "2026-11-03"}, {"end_date": "2026-13-01"},
])
def test_v4_bundle_validates_dates(carried, tmp_path, change):
    data = snapshot(carried)
    data["entries"][0].update(change)
    with pytest.raises(BundleError):
        merge(Database(tmp_path / "other"), data, lambda rel: None)


# ---- 輸入視窗 ----

def test_dialog_date_fields(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from PySide6.QtCore import QDate

    from reporttool.ui.entry_dialog import EntryDialog, date_value
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a: shown.append(text))
    db = Database(tmp_path)
    dlg = EntryDialog(db, DATE)
    # 下拉日曆，預設是紀錄那天；沒改日期、起訖都空白時照樣自動補（見 test_times）
    assert dlg.start_date_edit.calendarPopup() and dlg.end_date_edit.calendarPopup()
    assert date_value(dlg.start_date_edit) == date_value(dlg.end_date_edit) == DATE
    dlg.text.setPlainText("跨夜部署")
    dlg.start_date_edit.setDate(QDate(2026, 11, 1))
    dlg.save()
    assert shown == ["填了日期也要填起始和結束時間"] and db.entries_for_date(DATE) == []
    dlg.start_edit.setText("22:00")
    dlg.end_edit.setText("02:00")
    dlg.save()
    (e,) = db.entries_for_date(DATE)
    assert span(e) == ("2026-11-01", "22:00", DATE, "02:00")

    edit = EntryDialog(db, DATE, db.get_entry(e.id))
    assert (date_value(edit.start_date_edit), edit.start_edit.text()) == ("2026-11-01", "22:00")
    assert (date_value(edit.end_date_edit), edit.end_edit.text()) == (DATE, "02:00")
