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
DERIVED = {"1111": ("16:00", "16:00"), "2222": ("09:30", "09:30"), "3333": ("09:30", "10:20"),
           "4444": ("11:00", "11:00"), "5555": ("10:20", "14:05"), "6666": ("14:05", "16:40")}


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
    assert times(db) == {"1111": ("14:30", "16:00"), "2222": ("09:00", "09:30"), "3333": ("09:30", "10:20"),
                         "4444": ("11:00", "11:00"), "5555": ("13:30", "14:05"), "6666": ("16:40", "16:40")}


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
    assert times(db)["3333"] == ("09:30", "10:20") and times(db)["5555"] == ("13:30", "14:05")


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
