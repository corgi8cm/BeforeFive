"""匯出／匯入資料包（bundle），以及合併兩台裝置資料的規則。格式說明見 docs/data-format.md。

bundle 是一個 zip（或同樣結構的資料夾）：
    bundle.json          分類與紀錄
    images/<日期>/<檔名>  紀錄用到的圖片

合併規則：以 uuid（紀錄）或名稱（分類）對應，updated_at 較新的那邊勝出；完全同時則刪除優先，再比內容。
之後手機與電腦的區網同步也用同一套 snapshot()／merge()。
"""
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from .db import LIVE, STATUSES, Database, utc_now

FORMAT = "reporttool-bundle"
FORMAT_VERSION = 1

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
    category = db.conn.execute("SELECT name FROM categories WHERE id=?", (row["category_id"],)).fetchone()
    carried = db.conn.execute("SELECT uuid FROM entries WHERE id=?", (row["carried_from"],)).fetchone()
    return {
        "uuid": row["uuid"],
        "date": row["date"],
        "time": row["time"],
        "text": row["text"],
        "status": row["status"],
        "category": category["name"] if category else None,
        "tags": tags,
        "images": images,
        "carried_from": carried["uuid"] if carried else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
    }


def snapshot(db: Database) -> dict:
    """整個資料庫（含 tombstone）轉成 bundle.json 的內容。"""
    categories = [
        {"name": r["name"], "position": r["position"], "updated_at": r["updated_at"], "deleted_at": r["deleted_at"]}
        for r in db.conn.execute("SELECT * FROM categories ORDER BY position, id")
    ]
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
    _require(isinstance(data, dict) and data.get("format") == FORMAT, "不是 ReportTool 的資料包")
    _require(data.get("version") == FORMAT_VERSION, f"不支援的資料包版本：{data.get('version')}")
    for c in data.get("categories", []):
        _require(isinstance(c.get("name"), str) and c["name"].strip(), "分類名稱是空的")
        _require(isinstance(c.get("position"), int), f"分類「{c['name']}」的 position 格式錯誤")
        _require(_TIME.match(c.get("updated_at") or ""), f"分類「{c['name']}」的 updated_at 格式錯誤")
        _require(c.get("deleted_at") is None or _TIME.match(c["deleted_at"]), "分類 deleted_at 格式錯誤")
    for e in data.get("entries", []):
        name = e.get("uuid")
        _require(isinstance(name, str) and name, "紀錄缺少 uuid")
        _require(_DATE.match(e.get("date") or ""), f"紀錄 {name} 的日期格式錯誤")
        _require(_CLOCK.match(e.get("time") or ""), f"紀錄 {name} 的時間格式錯誤")
        _require(e.get("status") in STATUSES, f"紀錄 {name} 的狀態不正確")
        _require(isinstance(e.get("text"), str), f"紀錄 {name} 缺少內容")
        _require(_TIME.match(e.get("created_at") or "") and _TIME.match(e.get("updated_at") or ""),
                 f"紀錄 {name} 的時間戳記格式錯誤")
        _require(e.get("deleted_at") is None or _TIME.match(e["deleted_at"]), f"紀錄 {name} deleted_at 格式錯誤")
        _require(all(isinstance(t, str) and t for t in e.get("tags", [])), f"紀錄 {name} 的 tag 格式錯誤")
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


def _category_id(db: Database, name: str | None, now: str) -> int | None:
    """紀錄用到的分類；本機沒有就建立，本機已刪除則當作未分類。"""
    if not name:
        return None
    row = db.conn.execute("SELECT id, deleted_at FROM categories WHERE name=?", (name,)).fetchone()
    if row:
        return None if row["deleted_at"] else row["id"]
    pos = db._next_category_position()
    return db.conn.execute("INSERT INTO categories (name, position, updated_at) VALUES (?, ?, ?)",
                           (name, pos, now)).lastrowid


def _merge_category(db: Database, c: dict, stats: dict):
    row = db.conn.execute("SELECT * FROM categories WHERE name=?", (c["name"],)).fetchone()
    if row and not _incoming_wins(
            {k: row[k] for k in ("name", "position", "updated_at", "deleted_at")},
            {k: c.get(k) for k in ("name", "position", "updated_at", "deleted_at")}):
        return
    if row is None:
        db.conn.execute("INSERT INTO categories (name, position, updated_at, deleted_at) VALUES (?, ?, ?, ?)",
                        (c["name"], c["position"], c["updated_at"], c["deleted_at"]))
    else:
        db.conn.execute("UPDATE categories SET position=?, updated_at=?, deleted_at=? WHERE id=?",
                        (c["position"], c["updated_at"], c["deleted_at"], row["id"]))
        if c["deleted_at"]:
            # 對方刪了這個分類：本機紀錄改成未分類，但不更新 updated_at，以免蓋掉對方較新的紀錄
            db.conn.execute("UPDATE entries SET category_id=NULL WHERE category_id=?", (row["id"],))
    stats["categories"] += 1


def _merge_entry(db: Database, e: dict, read_image, stats: dict, removed_files: list):
    row = db.conn.execute("SELECT * FROM entries WHERE uuid=?", (e["uuid"],)).fetchone()
    if row and not _incoming_wins(_entry_record(db, row), e):
        return

    if e["deleted_at"]:
        if row is None:
            db.conn.execute(
                "INSERT INTO entries (uuid, date, time, text, status, created_at, updated_at, deleted_at)"
                " VALUES (?, ?, ?, '', ?, ?, ?, ?)",
                (e["uuid"], e["date"], e["time"], e["status"], e["created_at"], e["updated_at"], e["deleted_at"]))
        else:
            removed_files += db._tombstone(row["id"], e["updated_at"])
            stats["deleted"] += 1
        return

    category_id = _category_id(db, e.get("category"), e["updated_at"])
    if row is None:
        entry_id = db.conn.execute(
            "INSERT INTO entries (uuid, date, time, text, status, category_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (e["uuid"], e["date"], e["time"], e["text"], e["status"], category_id, e["created_at"],
             e["updated_at"])).lastrowid
        old_tags, old_images = set(), {}
        stats["added"] += 1
    else:
        entry_id = row["id"]
        db.conn.execute(
            "UPDATE entries SET date=?, time=?, text=?, status=?, category_id=?, updated_at=?, deleted_at=NULL"
            " WHERE id=?",
            (e["date"], e["time"], e["text"], e["status"], category_id, e["updated_at"], entry_id))
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


def merge(db: Database, data: dict, read_image) -> dict:
    """把另一台裝置的 snapshot 合併進本機。read_image(相對路徑) 回傳圖片內容 bytes，沒有則 None。"""
    validate(data)
    stats = {"added": 0, "updated": 0, "deleted": 0, "categories": 0}
    removed_files = []
    with db.conn:
        for c in data.get("categories", []):
            _merge_category(db, c, stats)
        for e in data.get("entries", []):
            _merge_entry(db, e, read_image, stats, removed_files)
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
