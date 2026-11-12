"""樹狀分類（分類／子分類）：資料庫升級、分類操作、同步。"""
import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from reporttool.db import CATEGORY_SEP, SCHEMA_VERSION, Database, Entry, category_uuid
from reporttool.report import generate_report
from reporttool.sync import BundleError, import_bundle, merge, snapshot

DATE = "2026-11-08"
GOLDEN = Path(__file__).parent / "golden"
LEGACY = Path(__file__).parent / "legacy"


def ids(db):
    return {label: cid for cid, label in db.categories()}


def sync_both(a, b):
    merge(b, snapshot(a), lambda rel: None)
    merge(a, snapshot(b), lambda rel: None)


# ---- 資料庫升級 ----

def make_v5_database(folder: Path):
    """用目前的程式建立資料庫，再把分類表改回第 5 版的樣子。"""
    db = Database(folder)
    db.close()
    conn = sqlite3.connect(folder / "reporttool.db")
    conn.executescript("""
        DROP TABLE categories;
        CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            position INTEGER NOT NULL DEFAULT 0, updated_at TEXT, deleted_at TEXT);
        INSERT INTO categories (id, name, position, updated_at) VALUES
            (1, '開發', 0, '2026-10-01T00:00:00.000Z'), (2, '會議', 1, '2026-10-01T00:00:00.000Z');
        INSERT INTO categories (id, name, position, updated_at, deleted_at) VALUES
            (3, '舊分類', 2, '2026-10-02T00:00:00.000Z', '2026-10-02T00:00:00.000Z');
        INSERT INTO entries (id, uuid, date, time, title, text, status, priority, category_id, created_at, updated_at)
        VALUES (1, 'e1', '2026-11-08', '09:00', '', '寫程式', 'doing', 3, 1, '2026-11-08T01:00:00.000Z',
                '2026-11-08T01:00:00.000Z'),
               (2, 'e2', '2026-11-08', '10:00', '', '只有 tag', 'done', 3, NULL, '2026-11-08T02:00:00.000Z',
                '2026-11-08T02:00:00.000Z'),
               (3, 'e3', '2026-11-08', '11:00', '', '開會', 'done', 3, 2, '2026-11-08T03:00:00.000Z',
                '2026-11-08T03:00:00.000Z');
        INSERT INTO entry_tags (entry_id, tag) VALUES (1, '專案A'), (1, 'bug'), (2, '雜事');
        PRAGMA user_version = 5;
    """)
    conn.close()


def test_upgrade_to_tree(tmp_path):
    make_v5_database(tmp_path)
    db = Database(tmp_path)
    assert (tmp_path / "reporttool-before-v6.db").exists()
    entries = {e.text: e for e in db.entries_for_date(DATE)}
    assert entries["寫程式"].category == f"開發{CATEGORY_SEP}專案A"
    assert entries["寫程式"].tags == ["專案A", "bug"]  # tag 原樣保留
    assert entries["只有 tag"].category == "雜事"
    assert entries["開會"].category == "會議"
    assert [label for _, label in db.categories()] == ["開發", f"開發{CATEGORY_SEP}專案A", "會議", "雜事"]
    # 轉換不算修改，各裝置算出的 uuid 相同
    record = {e["uuid"]: e for e in snapshot(db)["entries"]}["e1"]
    assert record["updated_at"] == "2026-11-08T01:00:00.000Z"
    assert record["category"] == category_uuid(["開發", "專案A"])
    deleted = [c for c in snapshot(db)["categories"] if c["deleted_at"]]
    assert [c["name"] for c in deleted] == ["舊分類"]


def test_backup_before_upgrade_even_from_version_0(tmp_path):
    make_v5_database(tmp_path)
    conn = sqlite3.connect(tmp_path / "reporttool.db")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    db = Database(tmp_path)
    assert (tmp_path / "reporttool-before-v6.db").exists()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_fresh_database_has_default_categories(tmp_path):
    db = Database(tmp_path)
    assert [label for _, label in db.categories()] == ["開發", "會議", "支援", "行政", "其他"]
    assert not (tmp_path / "reporttool-before-v6.db").exists()


# ---- 分類操作 ----

def test_tree_operations(tmp_path):
    db = Database(tmp_path)
    dev = ids(db)["開發"]
    proj = db.add_category("專案A", dev)
    login = db.add_category("登入頁", proj)
    report = db.add_category("報表", proj)
    assert db.category_label(login) == f"開發{CATEGORY_SEP}專案A{CATEGORY_SEP}登入頁"
    labels = [label for _, label in db.categories()]
    assert labels[:4] == ["開發", "開發 › 專案A", "開發 › 專案A › 登入頁", "開發 › 專案A › 報表"]

    with pytest.raises(ValueError):
        db.add_category("登入頁", proj)  # 同一層不能重複
    db.add_category("登入頁", dev)  # 不同層可以同名
    with pytest.raises(ValueError):
        db.add_category("a › b")

    entry = db.save_entry(Entry(None, DATE, "09:00", "修 bug", "doing", category_id=login))
    db.rename_category(proj, "專案甲")
    assert db.get_entry(entry).category == "開發 › 專案甲 › 登入頁"

    db.reorder_categories([report, login])
    assert [label for _, label in db.categories()][2:4] == ["開發 › 專案甲 › 報表", "開發 › 專案甲 › 登入頁"]

    with pytest.raises(ValueError):
        db.move_category(proj, login)  # 不能移到自己的子分類底下
    db.move_category(login, None)
    assert db.get_entry(entry).category == "登入頁"
    db.move_category(login, proj)

    db.set_category_progress(proj, 40)
    assert {c.id: c.progress for c in db.category_tree()}[proj] == 40
    for bad in (101, -1, True, 1.5):
        with pytest.raises(ValueError):
            db.set_category_progress(proj, bad)
    db.set_category_progress(proj, None)
    assert {c.id: c.progress for c in db.category_tree()}[proj] is None

    # 刪除分類：子分類一起刪，紀錄移到上一層
    db.delete_category(proj)
    assert db.get_entry(entry).category == "開發"
    assert "開發 › 專案甲" not in ids(db)
    assert db.add_category("專案甲", dev)  # 刪掉的可以再加回來


# ---- 同步 ----

def test_tree_sync_converges(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    dev = ids(a)["開發"]
    proj = a.add_category("專案A", dev)
    login = a.add_category("登入頁", proj)
    a.set_category_progress(proj, 30)
    a.save_entry(Entry(None, DATE, "09:00", "修 bug", "doing", category_id=login))
    sync_both(a, b)
    assert [e.category for e in b.entries_for_date(DATE)] == ["開發 › 專案A › 登入頁"]
    assert {c.label: c.progress for c in b.category_tree()}["開發 › 專案A"] == 30

    # 同一個分類兩邊都改：和紀錄一樣，整個分類以較新的修改為準
    a.rename_category(proj, "專案甲")
    time.sleep(0.005)  # updated_at 只到毫秒，確保 B 的修改確實比較晚
    b.set_category_progress(ids(b)["開發 › 專案A"], 80)
    sync_both(a, b)
    assert snapshot(a)["entries"] == snapshot(b)["entries"]
    for db in (a, b):
        assert {c.label: c.progress for c in db.category_tree()}["開發 › 專案A"] == 80

    # 刪除子樹同步到另一邊，紀錄移到還在的上層
    a.delete_category(proj)
    sync_both(a, b)
    assert [e.category for e in b.entries_for_date(DATE)] == ["開發"]
    assert not any(label.startswith("開發 › 專案") for label in ids(b))


def test_same_path_created_on_both_devices_merges(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    a.add_category("專案A", ids(a)["開發"])
    b.add_category("專案A", ids(b)["開發"])
    sync_both(a, b)
    assert [label for _, label in a.categories()].count("開發 › 專案A") == 1
    assert [label for _, label in b.categories()].count("開發 › 專案A") == 1


def test_bad_progress_rejected(tmp_path):
    db = Database(tmp_path)
    data = snapshot(db)
    data["categories"][0]["progress"] = 150
    with pytest.raises(BundleError):
        merge(db, data, lambda rel: None)


def test_legacy_v1_bundle_matches_golden(tmp_path):
    """第 1 版資料包匯入後，結果和第 2 版的標準測試資料相同。"""
    folder = tmp_path / "v1"
    folder.mkdir()
    shutil.copy(LEGACY / "bundle_v1.json", folder / "bundle.json")
    shutil.copytree(GOLDEN / "basic" / "images", folder / "images")
    db = Database(tmp_path / "data")
    import_bundle(db, folder)
    md = (generate_report(db, "2026-11-02", tmp_path / "out", (GOLDEN / "instructions.md").read_text(
        encoding="utf-8")) / "report.md").read_text(encoding="utf-8")
    expected = (GOLDEN / "basic" / "expected_report.md").read_text(encoding="utf-8")
    # 第 1 版沒有起訖時間：由 time 回推（見 test_times.py 的 DERIVED）
    for golden, derived in (("[09:00–09:30 0.5h]", "[09:30]"), ("[13:30–14:00 0.5h]", "[10:15–14:00 3.75h]"),
                            ("[16:45]", "[14:00–16:45 2.75h]")):
        expected = expected.replace(golden, derived)
    # 第 1 版沒有進度與分類優先度：「分類資訊」照原本的順序、沒有附註，其他內容相同
    start, end = expected.index("\n## 分類資訊\n"), expected.index("\n[附圖1]")
    v1_section = "\n## 分類資訊\n- 開發\n- 開發 › 專案A\n- 會議\n- 支援\n- 支援 › 客戶X\n"
    assert md == expected[:start] + v1_section + expected[end:]
    v2 = json.loads((LEGACY / "bundle_v2.json").read_text(encoding="utf-8"))
    span = ("start_date", "start_time", "end_date", "end_time")
    without_times = [{k: v for k, v in e.items() if k not in span}
                     for e in snapshot(db)["entries"]]
    assert without_times == v2["entries"]


# ---- 產出 ----

def test_report_category_section_lists_used_categories(tmp_path):
    from reporttool.report import generate_range_report

    db = Database(tmp_path / "data")
    dev = ids(db)["開發"]
    proj = db.add_category("專案A", dev)
    other = db.add_category("專案B", dev)
    db.set_category_progress(dev, 60)
    db.set_category_progress(other, 90)  # 沒有紀錄，不列出
    db.set_category_progress(ids(db)["會議"], 10)  # 沒有紀錄，不列出
    db.save_entry(Entry(None, DATE, "09:00", "a", "doing", category_id=proj))  # 專案A 沒有進度，但上層有
    md = (generate_report(db, DATE, tmp_path / "out", "指示") / "report.md").read_text(encoding="utf-8")
    assert "\n\n## 分類資訊\n- 開發：進度 60%\n- 開發 › 專案A\n" in md
    assert "專案B" not in md and "會議" not in md

    # 分類優先度：同一層依優先度排列，不是預設 3 的才寫出來
    support = ids(db)["支援"]
    db.save_entry(Entry(None, DATE, "10:00", "b", "doing", category_id=support))
    db.set_category_priority(support, 1)
    db.set_category_priority(proj, 2)
    md = (generate_range_report(db, DATE, DATE, tmp_path / "out", "指示") / "report.md").read_text(encoding="utf-8")
    assert md.endswith("\n## 分類資訊\n- 支援：優先1\n- 開發：進度 60%\n- 開發 › 專案A：優先2\n")

    db.save_entry(Entry(None, "2026-11-09", "09:00", "c", "doing"))  # 只有未分類的紀錄：整段省略
    md = (generate_report(db, "2026-11-09", tmp_path / "out", "指示") / "report.md").read_text(encoding="utf-8")
    assert "分類資訊" not in md


def test_category_priority_orders_siblings_and_syncs(tmp_path):
    a, b = Database(tmp_path / "a"), Database(tmp_path / "b")
    dev = ids(a)["開發"]
    first = a.add_category("甲", dev)
    second = a.add_category("乙", dev)
    a.set_category_priority(second, 1)
    assert [label for _, label in a.categories()][:3] == ["開發", "開發 › 乙", "開發 › 甲"]
    a.reorder_categories([first, second])  # 手動順序只在相同優先度之間有作用
    assert [label for _, label in a.categories()][1] == "開發 › 乙"
    for bad in (0, 6, True, "1"):
        with pytest.raises(ValueError):
            a.set_category_priority(first, bad)

    sync_both(a, b)
    assert {c.label: c.priority for c in b.category_tree()}["開發 › 乙"] == 1
    assert [label for _, label in b.categories()][:3] == ["開發", "開發 › 乙", "開發 › 甲"]

    data = snapshot(a)
    data["categories"][0]["priority"] = 9
    with pytest.raises(BundleError):
        merge(b, data, lambda rel: None)
    del data["categories"][0]["priority"]  # 較早的 2.0 備份沒有分類優先度：當成 3
    merge(b, data, lambda rel: None)


def test_fresh_database_keeps_imported_category_settings(tmp_path):
    """全新資料庫自動建立的預設分類，不會蓋掉匯入備份裡的排序與進度。"""
    a = Database(tmp_path / "a")
    a.set_category_progress(ids(a)["開發"], 60)
    a.reorder_categories([ids(a)["會議"], ids(a)["開發"]])
    b = Database(tmp_path / "b")
    merge(b, snapshot(a), lambda rel: None)
    assert [label for _, label in b.categories()][:2] == ["會議", "開發"]
    assert {c.label: c.progress for c in b.category_tree()}["開發"] == 60


def test_upgrade_from_v6_adds_category_priority(tmp_path):
    db = Database(tmp_path)
    db.close()
    conn = sqlite3.connect(tmp_path / "reporttool.db")
    conn.executescript("ALTER TABLE categories DROP COLUMN priority; PRAGMA user_version = 6;")
    conn.close()
    db = Database(tmp_path)
    assert {c.priority for c in db.category_tree()} == {3}
    assert not (tmp_path / "reporttool-before-v6.db").exists()
