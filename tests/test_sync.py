import json
import re
import time
import sqlite3
import zipfile

import pytest
from PIL import Image as PILImage

from reporttool.db import SCHEMA_VERSION, Database, Entry
from reporttool.sync import BundleError, export_bundle, import_bundle, merge, snapshot

DATE = "2026-11-02"
UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def entries_view(db):
    """比較兩台裝置用：去掉本機 id 等只在單機有意義的欄位。"""
    return snapshot(db)["entries"]


def sync_both(a, b):
    merge(b, snapshot(a), lambda rel: a.abs_path(rel).read_bytes() if a.abs_path(rel).exists() else None)
    merge(a, snapshot(b), lambda rel: b.abs_path(rel).read_bytes() if b.abs_path(rel).exists() else None)


def test_migrates_v1_database(tmp_path):
    conn = sqlite3.connect(tmp_path / "reporttool.db")
    conn.executescript("""
        CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, time TEXT NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL, carried_from INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, category_id INTEGER);
        CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            position INTEGER NOT NULL DEFAULT 0);
        INSERT INTO categories (name, position) VALUES ('開發', 0);
        INSERT INTO entries (date, time, text, status, created_at, updated_at, category_id)
            VALUES ('2026-11-01', '09:00', '舊紀錄', 'done', '2026-11-01T09:00:00', '2026-11-01T09:05:00', 1);
    """)
    conn.close()

    db = Database(tmp_path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    old = db.entries_for_date("2026-11-01")[0]
    assert old.uuid and old.category == "開發"
    row = db.conn.execute("SELECT created_at, updated_at FROM entries").fetchone()
    assert UTC.match(row["created_at"]) and UTC.match(row["updated_at"])
    assert [n for _, n in db.categories()] == ["開發"]  # v1 已做過，不會再塞預設分類
    device = db.device_id
    db.close()
    assert Database(tmp_path).device_id == device


def test_delete_leaves_tombstone(tmp_path):
    db = Database(tmp_path)
    PILImage.new("RGB", (10, 10)).save(tmp_path / "a.png")
    img = db.import_image_file(tmp_path / "a.png", DATE)
    entry_id = db.save_entry(Entry(None, DATE, "09:00", "要刪的", "done", ["x"], [img]))
    db.delete_entry(entry_id)

    assert db.entries_for_date(DATE) == [] and db.get_entry(entry_id) is None
    assert not db.abs_path(img.path).exists()
    (tomb,) = snapshot(db)["entries"]
    assert tomb["deleted_at"] and tomb["text"] == "" and tomb["tags"] == [] and tomb["images"] == []


def test_export_import_zip(tmp_path):
    a = Database(tmp_path / "a")
    PILImage.new("RGB", (10, 10), "red").save(tmp_path / "a.png")
    cid = a.add_category("專案B")
    first = a.save_entry(Entry(None, "2026-11-01", "09:00", "第一天", "doing", ["t1"], category_id=cid))
    a.save_entry(Entry(None, DATE, "10:00", "第二天", "done", ["t1", "t2"],
                       [a.import_image_file(tmp_path / "a.png", DATE)], carried_from=first, category_id=cid))
    path = export_bundle(a, tmp_path / "backup.zip")

    b = Database(tmp_path / "b")
    stats = import_bundle(b, path)
    assert stats["added"] == 2
    assert entries_view(b) == entries_view(a)
    second = b.entries_for_date(DATE)[0]
    assert b.abs_path(second.images[0].path).read_bytes() == a.abs_path(second.images[0].path).read_bytes()
    assert [e.text for e in b.history(second.id)] == ["第一天"]
    assert "專案B" in [n for _, n in b.categories()] and "t2" in b.all_tags()

    assert import_bundle(b, path) == {"added": 0, "updated": 0, "deleted": 0, "categories": 0}  # 重複匯入沒影響


def test_two_devices_converge(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    shared = a.save_entry(Entry(None, DATE, "09:00", "共同的", "doing", ["x"]))
    sync_both(a, b)
    b_id = b.entries_for_date(DATE)[0].id

    # A 改狀態，B 稍後改內容：B 比較新，B 的版本勝出
    a.set_status(shared, "waiting")
    time.sleep(0.005)  # updated_at 只到毫秒，確保 B 的修改確實比較晚
    entry = b.get_entry(b_id)
    entry.text = "B 改過的"
    b.save_entry(entry)
    a.save_entry(Entry(None, DATE, "11:00", "只在 A", "done"))
    b.save_entry(Entry(None, DATE, "12:00", "只在 B", "done"))
    sync_both(a, b)

    assert entries_view(a) == entries_view(b)
    texts = {e.text: e.status for e in a.entries_for_date(DATE)}
    assert texts == {"B 改過的": "doing", "只在 A": "done", "只在 B": "done"}


def test_delete_propagates_and_older_edit_loses(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    a_id = a.save_entry(Entry(None, DATE, "09:00", "會被刪", "doing"))
    sync_both(a, b)
    b_id = b.entries_for_date(DATE)[0].id

    entry = b.get_entry(b_id)
    entry.text = "B 先改"
    b.save_entry(entry)
    a.delete_entry(a_id)  # 刪除比較晚
    sync_both(a, b)
    assert a.entries_for_date(DATE) == [] and b.entries_for_date(DATE) == []


def test_category_rename_and_delete_propagate(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    dev = dict((n, i) for i, n in a.categories())["開發"]
    meeting = dict((n, i) for i, n in a.categories())["會議"]
    a.save_entry(Entry(None, DATE, "09:00", "寫程式", "done", category_id=dev))
    a.save_entry(Entry(None, DATE, "10:00", "開會", "done", category_id=meeting))
    sync_both(a, b)

    a.rename_category(dev, "研發")
    a.delete_category(meeting)
    sync_both(a, b)

    names = [n for _, n in b.categories()]
    assert "研發" in names and "開發" not in names and "會議" not in names
    assert {e.text: e.category for e in b.entries_for_date(DATE)} == {"寫程式": "研發", "開會": ""}
    assert entries_view(a) == entries_view(b)

    b.add_category("會議")  # 刪掉的分類可以再加回來
    assert "會議" in [n for _, n in b.categories()]


def test_rejects_bad_bundle(tmp_path):
    db = Database(tmp_path / "db")
    data = snapshot(db)
    data["entries"] = [{
        "uuid": "x", "date": DATE, "time": "09:00", "text": "a", "status": "done", "category": None, "tags": [],
        "images": [{"path": "../../evil.png", "original_name": "", "include": True}], "carried_from": None,
        "created_at": "2026-11-02T00:00:00.000Z", "updated_at": "2026-11-02T00:00:00.000Z", "deleted_at": None}]
    with pytest.raises(BundleError):
        merge(db, data, lambda rel: b"")
    assert db.entries_for_date(DATE) == []

    with zipfile.ZipFile(tmp_path / "bad.zip", "w") as zf:
        zf.writestr("bundle.json", json.dumps({"format": "other"}))
    with pytest.raises(BundleError):
        import_bundle(db, tmp_path / "bad.zip")
    (tmp_path / "not.zip").write_text("hello")
    with pytest.raises(BundleError):
        import_bundle(db, tmp_path / "not.zip")


def test_exact_tie_resolves_the_same_on_both_devices(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    a.save_entry(Entry(None, DATE, "09:00", "原本", "doing"))
    sync_both(a, b)
    stamp = "2030-01-01T00:00:00.000Z"
    a.conn.execute("UPDATE entries SET text='A 版本', updated_at=?", (stamp,))
    b.conn.execute("UPDATE entries SET text='B 版本', updated_at=?", (stamp,))
    sync_both(a, b)
    assert entries_view(a) == entries_view(b)
    assert a.entries_for_date(DATE)[0].text == "B 版本"  # 內容比較大的勝出（"B" > "A"）
