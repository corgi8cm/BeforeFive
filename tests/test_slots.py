"""3.0.1：起訖時間以 15 分鐘為一格（四捨五入），甘特圖單日檢視每 15 分鐘一條格線。"""
import json
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from reporttool.db import SCHEMA_VERSION, Database, Entry, resolve_times, snap_clock
from reporttool.sync import import_bundle, snapshot

DATE = "2026-11-02"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("value, snapped", [
    ("09:00", "09:00"), ("09:07", "09:00"), ("09:08", "09:15"), ("09:22", "09:15"), ("09:23", "09:30"),
    ("17:08", "17:15"), ("10:52", "10:45"), ("10:53", "11:00"), ("00:05", "00:00"),
    ("23:53", "23:45"),  # 不會進位到 24:00
    ("", ""),
])
def test_snap_clock(value, snapped):
    assert snap_clock(value) == snapped


def test_resolve_times_snaps_and_clamps_to_snapped_now():
    def rt(start, end, now):
        return resolve_times(start, end, date=DATE, today=DATE, now=now, editing=False, follow_up=False)

    assert rt("9:07", "1052", "15:00") == ("09:00", "10:45")
    assert rt("16:00", "17:15", "17:08") == ("16:00", "17:15")  # 17:08 取整是 17:15，不算超過現在
    assert rt("16:00", "18:00", "17:05") == ("16:00", "17:00")
    assert resolve_times("", "", date=DATE, today=DATE, now="17:08", editing=False, follow_up=True) == \
        ("17:15", "17:15")


def test_save_entry_snaps_filled_and_auto_times(tmp_path):
    db = Database(tmp_path)
    a = db.get_entry(db.save_entry(Entry(None, DATE, "10:00", "手填", "done", start="09:07", end="10:08")))
    assert (a.time, a.start, a.end, a.minutes) == ("10:00", "09:00", "10:15", 75)
    # 自動補：結束＝建立時間取整；前一則的結束（10:15）比建立時間 10:10 晚也照樣接上
    b = db.get_entry(db.save_entry(Entry(None, DATE, "10:10", "自動", "done")))
    assert (b.time, b.start, b.end) == ("10:10", "10:15", "10:15")
    c = db.get_entry(db.save_entry(Entry(None, DATE, "11:38", "再一筆", "done")))
    assert (c.start, c.end) == ("10:15", "11:45")
    with pytest.raises(ValueError):  # 格式不對的還是會擋下來，不會在取整時當掉
        db.save_entry(Entry(None, DATE, "11:00", "x", "done", start="9:0", end="10:00"))


def test_carry_over_snaps_now(tmp_path):
    db = Database(tmp_path)
    db.save_entry(Entry(None, "2026-11-01", "17:00", "做一半", "doing", start="13:00", end="17:00"))
    (new_id,) = db.carry_over("2026-11-01", DATE, "09:08")
    e = db.get_entry(new_id)
    assert (e.start, e.end) == ("09:15", "09:15")


def test_migrates_v8_database_snaps_all_times(tmp_path):
    db = Database(tmp_path)
    keep = db.save_entry(Entry(None, DATE, "10:00", "整格", "done", start="09:00", end="10:00"))
    odd = db.save_entry(Entry(None, DATE, "11:00", "不整齊", "done", start="10:00", end="11:00"))
    gone = db.save_entry(Entry(None, DATE, "12:00", "刪掉的", "done", start="11:00", end="12:00"))
    db.delete_entry(gone)
    db.conn.execute("UPDATE entries SET start_time='10:07', end_time='10:52' WHERE id=?", (odd,))
    db.conn.execute("UPDATE entries SET start_time='11:53', end_time='11:53' WHERE id=?", (gone,))
    db.conn.execute("PRAGMA user_version = 8")
    db.conn.commit()
    before = db.conn.execute("SELECT id, updated_at FROM entries ORDER BY id").fetchall()
    db.close()

    db = Database(tmp_path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 9
    assert (tmp_path / "reporttool-before-v9.db").exists()
    with sqlite3.connect(tmp_path / "reporttool-before-v9.db") as old:  # 備份是升級前的樣子
        assert old.execute("SELECT start_time FROM entries WHERE id=?", (odd,)).fetchone()[0] == "10:07"
    assert (db.get_entry(keep).start, db.get_entry(keep).end) == ("09:00", "10:00")
    assert (db.get_entry(odd).start, db.get_entry(odd).end) == ("10:00", "10:45")
    row = db.conn.execute("SELECT start_time, end_time FROM entries WHERE id=?", (gone,)).fetchone()
    assert tuple(row) == ("12:00", "12:00")
    # 不算修改：每台裝置轉換結果相同，不會在同步時蓋掉別台
    assert db.conn.execute("SELECT id, updated_at FROM entries ORDER BY id").fetchall() == before


def test_import_snaps_unaligned_times(tmp_path):
    src = Database(tmp_path / "a")
    src.save_entry(Entry(None, DATE, "11:00", "別台還沒升級", "done", start="10:00", end="11:00"))
    data = snapshot(src)
    data["entries"][0].update(start_time="10:07", end_time="10:52")
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "bundle.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    dst = Database(tmp_path / "b")
    import_bundle(dst, folder)
    (e,) = dst.entries_for_date(DATE)
    assert (e.start, e.end) == ("10:00", "10:45")


def test_dialog_field_shows_snapped_value(qapp, tmp_path, monkeypatch):
    from reporttool.ui import entry_dialog
    from reporttool.ui.entry_dialog import EntryDialog

    monkeypatch.setattr(entry_dialog, "now_parts", lambda: (DATE, "17:08"))
    db = Database(tmp_path)
    dlg = EntryDialog(db, DATE)
    dlg.start_edit.setText("1607")
    dlg.start_edit.editingFinished.emit()
    assert dlg.start_edit.text() == "16:00"
    dlg.end_edit.setText("abc")
    dlg.end_edit.editingFinished.emit()
    assert dlg.end_edit.text() == "abc"  # 看不懂的留著，存檔時才提醒
    dlg.end_edit.setText("17:10")
    dlg.text.setPlainText("a")
    dlg.save()
    (e,) = db.entries_for_date(DATE)
    assert (e.start, e.end) == ("16:00", "17:15")


def test_gantt_minor_grid_every_15_minutes(qapp, tmp_path):
    from reporttool.gantt import build_gantt
    from reporttool.ui.gantt_dialog import PX_PER_HOUR, GanttView

    db = Database(tmp_path)
    db.save_entry(Entry(None, DATE, "10:00", "a", "done", start="09:00", end="10:00"))
    view = GanttView(build_gantt(db, DATE, DATE))
    minor = view.minor_ticks()
    assert minor[:3] == [9 * 60 + 15, 9 * 60 + 30, 9 * 60 + 45] and 10 * 60 not in minor
    assert len(minor) == 9 * 3  # 09:00~18:00 每小時 3 條
    assert view.x_of(9 * 60 + 15) - view.x_of(9 * 60) == PX_PER_HOUR / 4
