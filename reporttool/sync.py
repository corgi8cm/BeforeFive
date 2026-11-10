"""匯出／匯入資料包（bundle），以及合併兩台裝置資料的規則。格式說明見 docs/data-format.md。

bundle 是一個 zip（或同樣結構的資料夾）：
    bundle.json          分類與紀錄
    images/<日期>/<檔名>  紀錄用到的圖片

合併規則：紀錄與分類都以 uuid 對應，updated_at 較新的那邊勝出；完全同時則刪除優先，再比內容。
之後手機與電腦的區網同步也用同一套 snapshot()／merge()。

版本：
    1  分類只有一層，以名稱辨識；紀錄的 category 是分類名稱
    2  分類是樹狀（uuid、parent、progress、priority）；紀錄的 category 是分類 uuid
    3  紀錄有起訖時間 start_time、end_time
匯入第 1 版時會做和資料庫升級相同的轉換（第一個 tag 變成子分類）；
匯入第 1、2 版時，起訖時間和資料庫升級一樣由 time 回推。
"""
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from .db import (
    DEFAULT_PRIORITY, LEGACY_STATUSES, LIVE, PRIORITIES, STATUSES, Database, category_uuid, check_times,
    convert_first_tags, utc_now,
)

FORMAT = "reporttool-bundle"
FORMAT_VERSION = 3
SUPPORTED_VERSIONS = (1, 2, 3)

_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK = re.compile(r"^\d{2}:\d{2}$")
_IMAGE_PATH = re.compile(r"^images/\d{4}-\d{2}-\d{2}/[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,5}$")


class BundleError(ValueError):
    pass


# ---- 匯出 ----

def _entry_record(db: Database, row) -> dict:
    tags = [r["tag"] for r in db.conn.execute(
        "SELECT tag FROM entry_tags WHERE entry_id=? ORDER BY rowid", (row["id"],))]
    images = [{"path": r["path"], "original_name": r["original_name"], "include": bool(r["include"])}
              for r in db.conn.execute("SELECT * FROM images WHERE entry_id=? ORDER BY position", (row["id"],))]
    category = db.conn.execute("SELECT uuid FROM categories WHERE id=?", (row["category_id"],)).fetchone()
    carried = db.conn.execute("SELECT uuid FROM entries WHERE id=?", (row["carried_from"],)).fetchone()
    return {
        "uuid": row["uuid"],
        "date": row["date"],
        "time": row["time"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "title": row["title"],
        "text": row["text"],
        "status": row["status"],
        "priority": row["priority"],
        "category": category["uuid"] if category else None,
        "tags": tags,
        "images": images,
        "carried_from": carried["uuid"] if carried else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
    }


def _category_record(db: Database, row) -> dict:
    parent = db.conn.execute("SELECT uuid FROM categories WHERE id=?", (row["parent_id"],)).fetchone()
    return {
        "uuid": row["uuid"],
        "parent": parent["uuid"] if parent else None,
        "name": row["name"],
        "position": row["position"],
        "progress": row["progress"],
        "priority": row["priority"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
    }


def snapshot(db: Database) -> dict:
    """整個資料庫（含 tombstone）轉成 bundle.json 的內容。"""
    categories = [_category_record(db, r) for r in db.conn.execute("SELECT * FROM categories ORDER BY id")]
    entries = [_entry_record(db, row)
               for row in db.conn.execute("SELECT * FROM entries ORDER BY date, time, id").fetchall()]
    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "device_id": db.device_id,
        "exported_at": utc_now(),
        "categories": categories,
        "entries": entries,
    }


def export_bundle(db: Database, path) -> Path:
    path = Path(path)
    data = snapshot(db)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", json.dumps(data, ensure_ascii=False, indent=1))
        for entry in data["entries"]:
            for img in entry["images"]:
                src = db.abs_path(img["path"])
                if src.exists():
                    zf.write(src, img["path"])
    return path


# ---- 檢查 ----

def _require(cond, message):
    if not cond:
        raise BundleError(message)


def validate(data: dict):
    """匯入前先整份檢查，有問題就整份不匯入。"""
    _require(isinstance(data, dict) and data.get("format") == FORMAT, "不是下班前（ReportTool）的資料包")
    _require(data.get("version") in SUPPORTED_VERSIONS, f"不支援的資料包版本：{data.get('version')}")
    for c in data.get("categories", []):
        _require(isinstance(c.get("name"), str) and c["name"].strip(), "分類名稱是空的")
        if data["version"] >= 2:
            _require(isinstance(c.get("uuid"), str) and c["uuid"], f"分類「{c['name']}」缺少 uuid")
            _require(c.get("parent") is None or isinstance(c["parent"], str), f"分類「{c['name']}」的 parent 格式錯誤")
            progress = c.get("progress")
            _require(progress is None or (isinstance(progress, int) and not isinstance(progress, bool)
                                          and 0 <= progress <= 100), f"分類「{c['name']}」的進度不正確")
            priority = c.get("priority", DEFAULT_PRIORITY)
            _require(priority in PRIORITIES and not isinstance(priority, bool), f"分類「{c['name']}」的優先度不正確")
        _require(isinstance(c.get("position"), int), f"分類「{c['name']}」的 position 格式錯誤")
        _require(_TIME.match(c.get("updated_at") or ""), f"分類「{c['name']}」的 updated_at 格式錯誤")
        _require(c.get("deleted_at") is None or _TIME.match(c["deleted_at"]), "分類 deleted_at 格式錯誤")
    for e in data.get("entries", []):
        name = e.get("uuid")
        _require(isinstance(name, str) and name, "紀錄缺少 uuid")
        _require(_DATE.match(e.get("date") or ""), f"紀錄 {name} 的日期格式錯誤")
        _require(_CLOCK.match(e.get("time") or ""), f"紀錄 {name} 的時間格式錯誤")
        if data["version"] >= 3:
            try:
                check_times(e.get("start_time"), e.get("end_time"))
                _require(e["start_time"], "")
            except (ValueError, TypeError, KeyError):
                raise BundleError(f"紀錄 {name} 的起訖時間不正確") from None
        _require(e.get("status") in STATUSES or e.get("status") in LEGACY_STATUSES, f"紀錄 {name} 的狀態不正確")
        _require(isinstance(e.get("text"), str), f"紀錄 {name} 缺少內容")
        _require(isinstance(e.get("title", ""), str), f"紀錄 {name} 的標題格式錯誤")
        _require(e.get("priority", DEFAULT_PRIORITY) in PRIORITIES and not isinstance(e.get("priority"), bool),
                 f"紀錄 {name} 的優先順序不正確")
        _require(_TIME.match(e.get("created_at") or "") and _TIME.match(e.get("updated_at") or ""),
                 f"紀錄 {name} 的時間戳記格式錯誤")
        _require(e.get("deleted_at") is None or _TIME.match(e["deleted_at"]), f"紀錄 {name} deleted_at 格式錯誤")
        _require(all(isinstance(t, str) and t for t in e.get("tags", [])), f"紀錄 {name} 的 tag 格式錯誤")
        _require(e.get("category") is None or isinstance(e["category"], str), f"紀錄 {name} 的分類格式錯誤")
        for img in e.get("images", []):
            _require(_IMAGE_PATH.match(img.get("path") or ""), f"紀錄 {name} 的圖片路徑不合法：{img.get('path')}")


# ---- 合併 ----

def _incoming_wins(local: dict, incoming: dict) -> bool:
    """updated_at 較新的勝出。完全同時的話：刪除優先，再比內容，讓兩台裝置得到相同結果。"""
    if incoming["updated_at"] != local["updated_at"]:
        return incoming["updated_at"] > local["updated_at"]
    if bool(incoming.get("deleted_at")) != bool(local.get("deleted_at")):
        return bool(incoming.get("deleted_at"))
    return json.dumps(incoming, sort_keys=True, ensure_ascii=False) > json.dumps(local, sort_keys=True,
                                                                                  ensure_ascii=False)


def _nearest_live(db: Database, category_id: int | None) -> int | None:
    """這個分類；已刪除的話往上找第一個還在的分類，都刪了就是未分類。"""
    seen = set()
    while category_id is not None and category_id not in seen:
        seen.add(category_id)
        row = db.conn.execute("SELECT parent_id, deleted_at FROM categories WHERE id=?", (category_id,)).fetchone()
        if row is None:
            return None
        if row["deleted_at"] is None:
            return category_id
        category_id = row["parent_id"]
    return None


def _apply_remote_delete(db: Database, category_id: int, updated_at: str, deleted_at: str):
    """對方刪了這個分類：子分類一起刪除，紀錄移到還在的上層。不更新紀錄的 updated_at，以免蓋掉對方較新的紀錄。"""
    subtree = db.category_subtree(category_id)
    marks = ", ".join("?" * len(subtree))
    db.conn.execute(f"UPDATE categories SET updated_at=?, deleted_at=? WHERE id IN ({marks}) AND {LIVE}",
                    (updated_at, deleted_at, *subtree))
    db.conn.execute(f"UPDATE entries SET category_id=? WHERE category_id IN ({marks})",
                    (_nearest_live(db, category_id), *subtree))


def _incoming_order(categories: list[dict]) -> list[dict]:
    """先處理上層再處理子分類，同層維持原本順序。"""
    by_uuid = {c["uuid"]: c for c in categories}

    def depth(c, seen=()):
        parent = by_uuid.get(c.get("parent"))
        return 0 if parent is None or c["uuid"] in seen else 1 + depth(parent, seen + (c["uuid"],))

    return sorted(categories, key=depth)


def _merge_categories(db: Database, categories: list[dict], stats: dict) -> dict[str, int]:
    """合併第 2 版的分類，回傳 {對方的 uuid: 本機 id}。"""
    mapping: dict[str, int] = {}
    keys = ("parent", "name", "position", "progress", "priority", "updated_at", "deleted_at")
    for c in _incoming_order(categories):
        c = {**c, "priority": c.get("priority", DEFAULT_PRIORITY)}  # 較早的 2.0 備份沒有分類優先度
        parent_id = None
        if c.get("parent"):
            parent_id = mapping.get(c["parent"])
            if parent_id is None:
                found = db.conn.execute("SELECT id FROM categories WHERE uuid=?", (c["parent"],)).fetchone()
                parent_id = found["id"] if found else None
        row = db.conn.execute("SELECT * FROM categories WHERE uuid=?", (c["uuid"],)).fetchone()
        if row is None and not c.get("deleted_at"):
            # 兩邊各自建立了同一層同名的分類（uuid 不同）：當成同一個
            row = db.conn.execute(f"SELECT * FROM categories WHERE name=? AND parent_id IS ? AND {LIVE}",
                                  (c["name"], parent_id)).fetchone()
        if row is not None:
            mapping[c["uuid"]] = row["id"]
            local = _category_record(db, row)
            if not _incoming_wins({k: local[k] for k in keys}, {k: c.get(k) for k in keys}):
                continue
            if parent_id is not None and parent_id in db.category_subtree(row["id"]):
                parent_id = row["parent_id"]  # 會形成循環時保留本機的上層
            db.conn.execute(
                "UPDATE categories SET parent_id=?, name=?, position=?, progress=?, priority=?, updated_at=?"
                " WHERE id=?",
                (parent_id, c["name"], c["position"], c.get("progress"), c["priority"], c["updated_at"], row["id"]))
            if c.get("deleted_at") and row["deleted_at"] is None:
                _apply_remote_delete(db, row["id"], c["updated_at"], c["deleted_at"])
            elif not c.get("deleted_at"):
                db.conn.execute("UPDATE categories SET deleted_at=NULL WHERE id=?", (row["id"],))
        else:
            mapping[c["uuid"]] = db.conn.execute(
                "INSERT INTO categories (uuid, parent_id, name, position, progress, priority, updated_at, deleted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (c["uuid"], parent_id, c["name"], c["position"], c.get("progress"), c["priority"], c["updated_at"],
                 c.get("deleted_at"))).lastrowid
        stats["categories"] += 1
    return mapping


def _category_by_uuid(db: Database, node_uuid: str | None, mapping: dict[str, int]) -> int | None:
    if not node_uuid:
        return None
    category_id = mapping.get(node_uuid)
    if category_id is None:
        row = db.conn.execute("SELECT id FROM categories WHERE uuid=?", (node_uuid,)).fetchone()
        category_id = row["id"] if row else None
    return _nearest_live(db, category_id)


def _category_by_name(db: Database, name: str | None, now: str) -> int | None:
    """第 1 版：紀錄的分類是第一層的名稱；本機沒有就建立，本機已刪除則當作未分類。"""
    if not name:
        return None
    row = db.conn.execute("SELECT id, deleted_at FROM categories WHERE name=? AND parent_id IS NULL"
                          " ORDER BY deleted_at IS NOT NULL", (name,)).fetchone()
    if row:
        return None if row["deleted_at"] else row["id"]
    return db._ensure_category(None, name, now)


def _merge_category_v1(db: Database, c: dict, stats: dict):
    """第 1 版的分類：只有第一層，以名稱對應。"""
    row = db.conn.execute("SELECT * FROM categories WHERE name=? AND parent_id IS NULL"
                          " ORDER BY deleted_at IS NOT NULL", (c["name"],)).fetchone()
    keys = ("name", "position", "updated_at", "deleted_at")
    if row and not _incoming_wins({k: row[k] for k in keys}, {k: c.get(k) for k in keys}):
        return
    if row is None:
        db.conn.execute(
            "INSERT INTO categories (uuid, parent_id, name, position, updated_at, deleted_at)"
            " VALUES (?, NULL, ?, ?, ?, ?)",
            (category_uuid([c["name"]]), c["name"], c["position"], c["updated_at"], c["deleted_at"]))
    else:
        db.conn.execute("UPDATE categories SET position=?, updated_at=? WHERE id=?",
                        (c["position"], c["updated_at"], row["id"]))
        if c["deleted_at"] and row["deleted_at"] is None:
            _apply_remote_delete(db, row["id"], c["updated_at"], c["deleted_at"])
        elif not c["deleted_at"]:
            db.conn.execute("UPDATE categories SET deleted_at=NULL WHERE id=?", (row["id"],))
    stats["categories"] += 1


def _merge_entry(db: Database, e: dict, read_image, stats: dict, removed_files: list, resolve_category,
                 derive: list) -> int | None:
    """合併一筆紀錄，回傳寫入的本機 id（沒有寫入時回傳 None）。需要回推起始時間的 uuid 加進 derive。"""
    # 舊資料包沒有這些欄位：當作沒有標題、預設優先順序
    e = {**e, "title": e.get("title", ""), "priority": e.get("priority", DEFAULT_PRIORITY),
         "status": LEGACY_STATUSES.get(e["status"], e["status"])}
    row = db.conn.execute("SELECT * FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()
    if "start_time" not in e:  # 第 1、2 版：本機有同一筆而且日期時間沒變就沿用本機的起訖，否則之後回推
        if row and (row["date"], row["time"]) == (e["date"], e["time"]):
            e = {**e, "start_time": row["start_time"], "end_time": row["end_time"]}
        else:
            e = {**e, "start_time": e["time"], "end_time": e["time"]}
            if not e["deleted_at"]:
                derive.append(e["uuid"])
    if row and not _incoming_wins(_entry_record(db, row), e):
        return None

    if e["deleted_at"]:
        if row is None:
            db.conn.execute(
                "INSERT INTO entries (uuid, date, time, start_time, end_time, text, status, created_at, updated_at,"
                " deleted_at) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?)",
                (e["uuid"], e["date"], e["time"], e["start_time"], e["end_time"], e["status"], e["created_at"],
                 e["updated_at"], e["deleted_at"]))
        else:
            removed_files += db._tombstone(row["id"], e["updated_at"])
            stats["deleted"] += 1
        return None

    category_id = resolve_category(e.get("category"), e["updated_at"])
    if row is None:
        entry_id = db.conn.execute(
            "INSERT INTO entries (uuid, date, time, start_time, end_time, title, text, status, priority, category_id,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (e["uuid"], e["date"], e["time"], e["start_time"], e["end_time"], e["title"], e["text"], e["status"],
             e["priority"], category_id, e["created_at"], e["updated_at"])).lastrowid
        old_tags, old_images = set(), {}
        stats["added"] += 1
    else:
        entry_id = row["id"]
        db.conn.execute(
            "UPDATE entries SET date=?, time=?, start_time=?, end_time=?, title=?, text=?, status=?, priority=?,"
            " category_id=?, updated_at=?, deleted_at=NULL WHERE id=?",
            (e["date"], e["time"], e["start_time"], e["end_time"], e["title"], e["text"], e["status"],
             e["priority"], category_id, e["updated_at"], entry_id))
        old_tags = {r["tag"] for r in db.conn.execute("SELECT tag FROM entry_tags WHERE entry_id=?", (entry_id,))}
        old_images = {r["path"]: r["id"] for r in db.conn.execute(
            "SELECT id, path FROM images WHERE entry_id=?", (entry_id,))}
        stats["updated"] += 1

    db.conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (entry_id,))
    for tag in dict.fromkeys(e.get("tags", [])):
        db.conn.execute("INSERT INTO entry_tags (entry_id, tag) VALUES (?, ?)", (entry_id, tag))
        if tag not in old_tags:
            db._add_tag_use(tag, e["updated_at"])

    incoming = {img["path"] for img in e.get("images", [])}
    for path, image_id in old_images.items():
        if path not in incoming:
            db.conn.execute("DELETE FROM images WHERE id=?", (image_id,))
            removed_files.append(path)
    for pos, img in enumerate(e.get("images", [])):
        dst = db.abs_path(img["path"])
        if not dst.exists():
            content = read_image(img["path"])
            if content is not None:  # 對方也沒有這張圖時，照樣記錄，回報會標示「檔案遺失」
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(content)
        if img["path"] in old_images:
            db.conn.execute("UPDATE images SET position=?, include=? WHERE id=?",
                            (pos, int(img.get("include", True)), old_images[img["path"]]))
        else:
            db.conn.execute(
                "INSERT INTO images (entry_id, position, path, original_name, include) VALUES (?, ?, ?, ?, ?)",
                (entry_id, pos, img["path"], img.get("original_name", ""), int(img.get("include", True))))
    return entry_id


def _derive_start_times(db: Database, uuids: list[str]):
    """第 1、2 版的紀錄：結束＝time，起始＝同一天前一則的結束（沒有就是 time）。不更新 updated_at。

    結束時間已經都是 time，所以處理順序不影響結果。"""
    for entry_uuid in uuids:
        row = db.conn.execute(f"SELECT id, date, time FROM entries WHERE uuid=? AND {LIVE}", (entry_uuid,)).fetchone()
        if row:
            start = db.previous_end(row["date"], row["time"], row["id"]) or row["time"]
            db.conn.execute("UPDATE entries SET start_time=? WHERE id=?", (start, row["id"]))


def merge(db: Database, data: dict, read_image) -> dict:
    """把另一台裝置的 snapshot 合併進本機。read_image(相對路徑) 回傳圖片內容 bytes，沒有則 None。"""
    validate(data)
    stats = {"added": 0, "updated": 0, "deleted": 0, "categories": 0}
    removed_files = []
    legacy = data["version"] == 1
    with db.conn:
        if legacy:
            for c in data.get("categories", []):
                _merge_category_v1(db, c, stats)
            resolve = lambda name, now: _category_by_name(db, name, now)  # noqa: E731
        else:
            mapping = _merge_categories(db, data.get("categories", []), stats)
            resolve = lambda node_uuid, _now: _category_by_uuid(db, node_uuid, mapping)  # noqa: E731
        written, derive = [], []
        for e in data.get("entries", []):
            entry_id = _merge_entry(db, e, read_image, stats, removed_files, resolve, derive)
            if entry_id is not None:
                written.append(entry_id)
        if legacy:  # 舊版資料包：和資料庫升級一樣，把第一個 tag 轉成子分類
            convert_first_tags(db, written)
        _derive_start_times(db, derive)
        # 所有紀錄都進來後再接上 carried_from，前後順序不影響
        for e in data.get("entries", []):
            if e.get("carried_from"):
                db.conn.execute(
                    "UPDATE entries SET carried_from=COALESCE((SELECT id FROM entries WHERE uuid=?), carried_from)"
                    " WHERE uuid=?",
                    (e["carried_from"], e["uuid"]))
    db._remove_files(removed_files)
    return stats


def import_bundle(db: Database, path) -> dict:
    """匯入 zip 或同樣結構的資料夾。"""
    path = Path(path)
    if path.is_dir():
        data = json.loads((path / "bundle.json").read_text(encoding="utf-8"))

        def read_image(rel):
            f = path / PurePosixPath(rel)
            return f.read_bytes() if f.is_file() else None

        return merge(db, data, read_image)

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise BundleError("不是有效的資料包檔案") from e
    with zf:
        try:
            data = json.loads(zf.read("bundle.json").decode("utf-8"))
        except (KeyError, json.JSONDecodeError) as e:
            raise BundleError("資料包裡沒有正確的 bundle.json") from e
        members = set(zf.namelist())
        return merge(db, data, lambda rel: zf.read(rel) if rel in members else None)


def live_counts(db: Database) -> tuple[int, int]:
    """（紀錄數, 圖片數），給匯出完成的訊息用。"""
    entries = db.conn.execute(f"SELECT COUNT(*) FROM entries WHERE {LIVE}").fetchone()[0]
    images = db.conn.execute(
        f"SELECT COUNT(*) FROM images i JOIN entries e ON e.id = i.entry_id WHERE e.{LIVE}").fetchone()[0]
    return entries, images
