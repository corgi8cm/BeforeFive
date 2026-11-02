import sqlite3

import pytest

from reporttool.db import DEFAULT_CATEGORIES, Database, Entry

DATE = "2026-11-02"


def test_default_categories(tmp_path):
    db = Database(tmp_path)
    assert [name for _id, name in db.categories()] == list(DEFAULT_CATEGORIES)


def test_migrates_existing_database(tmp_path):
    # 舊版資料庫：entries 沒有 category_id
    conn = sqlite3.connect(tmp_path / "reporttool.db")
    conn.executescript("""
        CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, time TEXT NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL, carried_from INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO entries (date, time, text, status, created_at, updated_at)
            VALUES ('2026-11-01', '09:00', '舊紀錄', 'done', 'x', 'x');
    """)
    conn.close()

    db = Database(tmp_path)
    old = db.entries_for_date("2026-11-01")[0]
    assert old.text == "舊紀錄" and old.category_id is None and old.category == ""
    assert len(db.categories()) == len(DEFAULT_CATEGORIES)
    db.close()
    assert len(Database(tmp_path).categories()) == len(DEFAULT_CATEGORIES)  # 再開一次不重複建立


def test_category_crud(tmp_path):
    db = Database(tmp_path)
    cid = db.add_category("專案B")
    entry_id = db.save_entry(Entry(None, DATE, "09:00", "a", "done", category_id=cid))
    assert db.get_entry(entry_id).category == "專案B"

    db.rename_category(cid, "客戶支援")
    assert db.get_entry(entry_id).category == "客戶支援"
    with pytest.raises(ValueError):
        db.add_category("開發")
    with pytest.raises(ValueError):
        db.rename_category(cid, "  ")

    ids = [i for i, _ in db.categories()]
    db.reorder_categories([cid] + [i for i in ids if i != cid])
    assert db.categories()[0] == (cid, "客戶支援")

    assert db.category_usage(cid) == 1
    db.delete_category(cid)
    e = db.get_entry(entry_id)
    assert e.category_id is None and e.category == ""


def test_rename_merge_delete_tag(tmp_path):
    db = Database(tmp_path)
    a = db.save_entry(Entry(None, DATE, "09:00", "a", "done", ["專按A"]))
    b = db.save_entry(Entry(None, DATE, "10:00", "b", "done", ["專按A", "專案A"]))
    db.save_entry(Entry(None, DATE, "11:00", "c", "done", ["專案A", "bug"]))

    db.rename_tag("專按A", "#專案A")  # 合併
    assert db.get_entry(a).tags == ["專案A"]
    assert db.get_entry(b).tags == ["專案A"]
    usage = dict(db.tag_usage())
    assert "專按A" not in usage and usage["專案A"] == 3

    db.rename_tag("bug", "缺陷")
    assert "缺陷" in dict(db.tag_usage())

    db.delete_tag("專案A")
    assert db.get_entry(a).tags == [] and "專案A" not in db.all_tags()


def test_last_entry_status_and_history(tmp_path):
    db = Database(tmp_path)
    assert db.last_entry() is None
    first = db.save_entry(Entry(None, "2026-10-30", "09:00", "修登入頁", "doing", ["專案A"]))
    second = db.save_entry(Entry(None, "2026-10-31", "09:00", "找到原因", "doing", carried_from=first))
    third = db.save_entry(Entry(None, DATE, "09:00", "修好了", "doing", carried_from=second))

    assert db.last_entry().id == third
    db.set_status(third, "done")
    assert db.get_entry(third).status == "done"
    assert [e.id for e in db.history(third)] == [first, second]
    assert db.history(first) == []
