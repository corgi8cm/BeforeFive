"""v3.0.0：紀錄的起始／結束時間（schema 8、資料包第 3 版）。"""
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from reporttool.db import SCHEMA_VERSION, Database, Entry, check_times
from reporttool.sync import BundleError, merge, import_bundle, snapshot

DATE = "2026-11-02"
TESTS = Path(__file__).parent
GOLDEN = TESTS / "golden"
LEGACY = TESTS / "legacy"
# 第 1、2 版的標準測試資料匯入後回推出來的起訖：結束＝time，起始＝同一天前一則（不含 tombstone）的結束
DERIVED = {"1111": ("16:00", "16:00"), "2222": ("09:30", "09:30"), "3333": ("09:30", "10:15"),
           "4444": ("11:00", "11:00"), "5555": ("10:15", "14:00"), "6666": ("14:00", "16:45")}


def times(db):
    return {e["uuid"][:4]: (e["start_time"], e["end_time"]) for e in snapshot(db)["entries"]}


# ---- 規則 ----

@pytest.mark.parametrize("start, end", [("09:00", ""), ("", "10:00"), ("10:00", "09:59"), ("9:00", "10:00"),
                                        ("09:00", "24:00")])
def test_check_times_rejects(start, end):
    with pytest.raises(ValueError):
        check_times(start, end)


def test_check_times_accepts_zero_duration():
    check_times("09:00", "09:00")


def test_minutes():
    assert Entry(None, DATE, "10:30", "a", "done", start="09:00", end="10:30").minutes == 90
    assert Entry(None, DATE, "10:30", "a", "done").minutes == 0


# ---- 自動補 ----

def test_first_entry_of_day_has_zero_duration(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-01", "17:00", "前一天的", "done"))
    entry_id = db.save_entry(Entry(None, DATE, "09:15", "今天第一筆", "done"))
    e = db.get_entry(entry_id)
    assert (e.start, e.end) == ("09:15", "09:15")  # 不跨日找前一則


def test_start_is_latest_previous_end(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "10:00", "a", "done", start="09:00", end="10:00"))
    db.save_entry(Entry(None, DATE, "10:05", "補記早上的事", "done", start="08:00", end="08:30"))
    gone = db.save_entry(Entry(None, DATE, "11:00", "刪掉的", "done", start="10:30", end="11:00"))
    db.delete_entry(gone)
    db.save_entry(Entry(None, DATE, "15:00", "下午之後才結束的", "doing", start="14:00", end="16:00"))
    e = db.get_entry(db.save_entry(Entry(None, DATE, "11:30", "新的", "done")))
    # 補記的舊事不會把起點拉回去；刪掉的、比現在晚結束的都不算
    assert (e.start, e.end) == ("10:00", "11:30")


def test_filled_times_are_kept_and_validated(tmp_path):
    db = Database(tmp_path)
    e = db.get_entry(db.save_entry(Entry(None, DATE, "11:00", "a", "done", start="09:00", end="10:00")))
    assert (e.start, e.end) == ("09:00", "10:00")
    with pytest.raises(ValueError):
        db.save_entry(Entry(None, DATE, "11:00", "b", "done", start="10:00", end="09:00"))
    with pytest.raises(ValueError):
        db.save_entry(Entry(None, DATE, "11:00", "c", "done", start="10:00"))
    assert [x.text for x in db.entries_for_date(DATE)] == ["a"]  # 不合規則的什麼都不寫入


def test_carry_over_has_zero_duration(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-01", "17:00", "做一半", "doing", start="13:00", end="17:00"))
    db.save_entry(Entry(None, DATE, "08:50", "早上先記的", "done", start="08:00", end="08:50"))
    (new_id,) = db.carry_over("2026-11-01", DATE, "09:00")
    e = db.get_entry(new_id)
    assert (e.start, e.end) == ("09:00", "09:00")  # 不沿用前一筆，也不套前一則規則


# ---- 資料庫升級 ----

def test_migrates_v7_database(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-01", "17:00", "前一天", "done"))
    db.save_entry(Entry(None, DATE, "14:00", "下午", "done"))
    db.save_entry(Entry(None, DATE, "09:00", "早上", "done"))
    gone = db.save_entry(Entry(None, DATE, "11:00", "刪掉的", "done"))
    db.delete_entry(gone)
    db.save_entry(Entry(None, DATE, "16:30", "傍晚", "doing"))
    db.conn.execute("ALTER TABLE entries DROP COLUMN start_time")
    db.conn.execute("ALTER TABLE entries DROP COLUMN end_time")
    db.conn.execute("PRAGMA user_version = 7")
    db.conn.commit()
    before = db.conn.execute("SELECT id, updated_at FROM entries ORDER BY id").fetchall()
    db.close()

    db = Database(tmp_path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert (tmp_path / "reporttool-before-v8.db").exists()
    got = {e.text: (e.start, e.end) for d in ("2026-11-01", DATE) for e in db.entries_for_date(d)}
    assert got == {"前一天": ("17:00", "17:00"), "早上": ("09:00", "09:00"), "下午": ("09:00", "14:00"),
                   "傍晚": ("14:00", "16:30")}  # 刪掉的 11:00 不算前一則
    assert db.conn.execute("SELECT id, updated_at FROM entries ORDER BY id").fetchall() == before


def test_new_database_has_no_backup(tmp_path):
    db = Database(tmp_path)
    db.close()
    Database(tmp_path)
    assert not list(tmp_path.glob("reporttool-before-*.db"))


# ---- 資料包 ----

def test_golden_has_times(tmp_path):
    db = Database(tmp_path)
    import_bundle(db, GOLDEN / "basic")
    assert times(db) == {"1111": ("14:30", "16:00"), "2222": ("09:00", "09:30"), "3333": ("09:30", "10:15"),
                         "4444": ("11:00", "11:00"), "5555": ("13:30", "14:00"), "6666": ("16:45", "16:45")}


@pytest.mark.parametrize("name", ["bundle_v1.json", "bundle_v2.json"])
def test_legacy_bundle_derives_times(name, tmp_path):
    folder = tmp_path / "legacy"
    folder.mkdir()
    shutil.copy(LEGACY / name, folder / "bundle.json")
    db = Database(tmp_path / "data")
    import_bundle(db, folder)
    assert times(db) == DERIVED


def test_legacy_bundle_keeps_local_times(tmp_path):
    """本機已經有同一筆（日期時間沒變）時，匯入舊版資料包不會蓋掉本機填好的起訖。"""
    db = Database(tmp_path)
    import_bundle(db, GOLDEN / "basic")
    v2 = json.loads((LEGACY / "bundle_v2.json").read_text(encoding="utf-8"))
    for e in v2["entries"]:
        e["updated_at"] = "2030-01-01T00:00:00.000Z"
    merge(db, v2, lambda rel: None)
    assert times(db)["3333"] == ("09:30", "10:15") and times(db)["5555"] == ("13:30", "14:00")


@pytest.mark.parametrize("start, end", [(None, "10:00"), ("10:00", "09:00"), ("", ""), ("10:00", None)])
def test_v3_bundle_requires_valid_times(start, end, tmp_path):
    db = Database(tmp_path)
    data = json.loads((GOLDEN / "basic" / "bundle.json").read_text(encoding="utf-8"))
    entry = data["entries"][1]
    for key, value in (("start_time", start), ("end_time", end)):
        if value is None:
            del entry[key]
        else:
            entry[key] = value
    with pytest.raises(BundleError):
        merge(db, data, lambda rel: None)
    assert snapshot(db)["entries"] == []


def test_unknown_version_rejected(tmp_path):
    """2.x 的 SUPPORTED_VERSIONS 是 (1, 2)，讀到第 3 版會拒絕；同樣地這版也拒絕更新的版本。"""
    db = Database(tmp_path)
    data = snapshot(db)
    data["version"] = 4
    with pytest.raises(BundleError):
        merge(db, data, lambda rel: None)


def test_two_devices_converge_with_times(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    a.save_entry(Entry(None, DATE, "10:00", "a", "done", start="09:00", end="10:00"))
    merge(b, snapshot(a), lambda rel: None)
    (e,) = b.entries_for_date(DATE)
    e.start, e.end = "09:15", "10:00"
    b.save_entry(e)
    merge(a, snapshot(b), lambda rel: None)
    assert a.entries_for_date(DATE)[0].start == "09:15"
    assert snapshot(a)["entries"] == snapshot(b)["entries"]


# ---- 第 1 步：輸入規則與顯示 ----

from reporttool.db import duration_text, hours_text, parse_clock, resolve_times, span_text  # noqa: E402


@pytest.mark.parametrize("text, value", [("", ""), (" 9:05 ", "09:05"), ("09:05", "09:05"), ("0905", "09:05"),
                                         ("905", "09:05"), ("9：05", "09:05")])
def test_parse_clock(text, value):
    assert parse_clock(text) == value


@pytest.mark.parametrize("text", ["25:00", "9", "abc", "12:60", "12345"])
def test_parse_clock_rejects(text):
    with pytest.raises(ValueError):
        parse_clock(text)


def rt(start, end, date=DATE, editing=False, follow_up=False):
    return resolve_times(start, end, date=date, today=DATE, now="15:00", editing=editing, follow_up=follow_up)


def test_resolve_times():
    assert rt("", "") == ("", "")  # 今天的新紀錄：交給 save_entry 自動補
    assert rt("", "", follow_up=True) == ("15:00", "15:00")  # 追加進度：時長 0
    assert rt("9:00", "1030") == ("09:00", "10:30")
    assert rt("14:00", "18:00") == ("14:00", "15:00")  # 今天的結束時間最晚到現在
    assert rt("14:00", "18:00", date="2026-11-01") == ("14:00", "18:00")  # 別天不限制
    for kwargs in ({"date": "2026-11-01"}, {"editing": True}):
        with pytest.raises(ValueError):
            rt("", "", **kwargs)
    with pytest.raises(ValueError):
        rt("16:00", "18:00")  # 結束改成 15:00 之後比起始早
    with pytest.raises(ValueError):
        rt("09:00", "")


def test_display_formats():
    assert duration_text(45) == "45 分" and duration_text(120) == "2 小時" and duration_text(90) == "1 小時 30 分"
    assert hours_text(90) == "1.5h" and hours_text(50) == "0.83h" and hours_text(60) == "1h"
    e = Entry(None, DATE, "10:30", "a", "done", start="09:00", end="10:30")
    assert span_text(e) == "09:00–10:30（1 小時 30 分）" and span_text(e, False) == "09:00–10:30"
    assert span_text(Entry(None, DATE, "09:12", "a", "done", start="09:12", end="09:12")) == "09:12"


# ---- 輸入視窗（GUI） ----

@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def warnings(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a: shown.append(text))
    return shown


def test_dialog_new_entry_auto_fills(qapp, tmp_path):
    from reporttool.ui.entry_dialog import EntryDialog
    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "10:00", "a", "done", start="09:00", end="10:00"))
    dlg = EntryDialog(db, DATE)  # conftest：今天 2026-11-02 17:00
    assert dlg.start_edit.text() == "" and dlg.end_edit.text() == ""
    dlg.text.setPlainText("b")
    dlg.save()
    e = [x for x in db.entries_for_date(DATE) if x.text == "b"][0]
    assert (e.time, e.start, e.end) == ("17:00", "10:00", "17:00")


def test_dialog_other_day_requires_times(qapp, tmp_path, warnings):
    from reporttool.ui.entry_dialog import EntryDialog
    db = Database(tmp_path)
    dlg = EntryDialog(db, "2026-11-01")
    dlg.text.setPlainText("補記")
    dlg.save()
    assert warnings and db.entries_for_date("2026-11-01") == []
    dlg.start_edit.setText("13:00")
    dlg.end_edit.setText("14:30")
    dlg.save()
    (e,) = db.entries_for_date("2026-11-01")
    assert (e.start, e.end, e.minutes) == ("13:00", "14:30", 90)


def test_dialog_edit_keeps_times_and_rejects_clearing(qapp, tmp_path, warnings):
    from reporttool.ui.entry_dialog import EntryDialog
    db = Database(tmp_path)
    entry_id = db.save_entry(Entry(None, DATE, "10:00", "a", "done", start="09:00", end="10:00"))
    dlg = EntryDialog(db, DATE, db.get_entry(entry_id))
    assert (dlg.start_edit.text(), dlg.end_edit.text()) == ("09:00", "10:00")
    dlg.end_edit.setText("")
    dlg.save()
    assert warnings and db.get_entry(entry_id).end == "10:00"
    dlg.start_edit.setText("")
    dlg.save()
    assert len(warnings) == 2
    dlg.start_edit.setText("16:00")
    dlg.end_edit.setText("23:00")  # 超過現在（17:00）就改成現在
    dlg.save()
    e = db.get_entry(entry_id)
    assert (e.time, e.start, e.end) == ("10:00", "16:00", "17:00")  # 建立時間不變


def test_dialog_follow_up_has_zero_duration(qapp, tmp_path):
    from reporttool.ui.entry_dialog import EntryDialog
    db = Database(tmp_path)
    old = db.save_entry(Entry(None, DATE, "10:00", "a", "doing", start="09:00", end="10:00"))
    dlg = EntryDialog(db, DATE, follow_up_of=db.get_entry(old))
    dlg.text.setPlainText("追加")
    dlg.save()
    e = [x for x in db.entries_for_date(DATE) if x.text == "追加"][0]
    assert (e.start, e.end, e.carried_from) == ("17:00", "17:00", old)


def test_main_window_shows_span(qapp, tmp_path):
    from PySide6.QtCore import QDate
    from reporttool.config import load_settings
    from reporttool.ui.main_window import MainWindow
    db = Database(tmp_path / "data")
    db.save_entry(Entry(None, DATE, "10:30", "a", "done", start="09:00", end="10:30"))
    db.save_entry(Entry(None, DATE, "11:00", "b", "done", start="11:00", end="11:00"))
    win = MainWindow(db, load_settings(tmp_path / "config"))
    win.date_edit.setDate(QDate.fromString(DATE, "yyyy-MM-dd"))
    assert sorted(item.text(0) for item in win.entry_items()) == ["09:00–10:30（1 小時 30 分）", "11:00"]
