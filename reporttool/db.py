"""SQLite 資料層：紀錄、標籤、圖片。

圖片原檔存放在 data_dir/images/YYYY-MM-DD/，資料庫只記相對路徑。
"""
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

STATUSES = ("done", "doing", "blocked")
STATUS_LABELS = {"done": "完成", "doing": "進行中", "blocked": "卡住"}
UNFINISHED = ("doing", "blocked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    carried_from INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (entry_id, tag)
);
CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    include INTEGER NOT NULL DEFAULT 1
);
"""


@dataclass
class Image:
    id: int | None
    path: str  # 相對於 data_dir
    original_name: str = ""
    include: bool = True


@dataclass
class Entry:
    id: int | None
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    text: str
    status: str
    tags: list[str] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    carried_from: int | None = None


def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").strip()


def parse_tags(text: str) -> list[str]:
    """把 "#專案A #bug, 會議" 拆成 ["專案A", "bug", "會議"]，去重並保留順序。"""
    tags = []
    for part in re.split(r"[\s,，、]+", text):
        tag = normalize_tag(part)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.data_dir / "reporttool.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---- 圖片檔案 ----

    def abs_path(self, rel_path: str) -> Path:
        return self.data_dir / rel_path

    def new_image_path(self, date: str, suffix: str) -> str:
        """配置一個新的圖片存放位置（相對路徑），並建立所需資料夾。"""
        rel = Path("images") / date / f"{uuid.uuid4().hex}{suffix.lower()}"
        (self.data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        return rel.as_posix()

    def import_image_file(self, src, date: str) -> Image:
        src = Path(src)
        rel = self.new_image_path(date, src.suffix or ".png")
        shutil.copy2(src, self.abs_path(rel))
        return Image(None, rel, src.stem)

    def _remove_files(self, rel_paths):
        for rel in rel_paths:
            try:
                self.abs_path(rel).unlink()
            except FileNotFoundError:
                pass

    # ---- 紀錄 ----

    def save_entry(self, entry: Entry) -> int:
        """新增或更新一筆紀錄（含 tag 與圖片），回傳 id。"""
        now = _now()
        removed_files = []
        with self.conn:
            if entry.id is None:
                cur = self.conn.execute(
                    "INSERT INTO entries (date, time, text, status, carried_from, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (entry.date, entry.time, entry.text, entry.status, entry.carried_from, now, now),
                )
                entry.id = cur.lastrowid
                old_tags = set()
            else:
                self.conn.execute(
                    "UPDATE entries SET date=?, time=?, text=?, status=?, updated_at=? WHERE id=?",
                    (entry.date, entry.time, entry.text, entry.status, now, entry.id),
                )
                old_tags = {r["tag"] for r in self.conn.execute(
                    "SELECT tag FROM entry_tags WHERE entry_id=?", (entry.id,))}
                keep = {img.id for img in entry.images if img.id is not None}
                for row in self.conn.execute(
                        "SELECT id, path FROM images WHERE entry_id=?", (entry.id,)).fetchall():
                    if row["id"] not in keep:
                        self.conn.execute("DELETE FROM images WHERE id=?", (row["id"],))
                        removed_files.append(row["path"])

            self.conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (entry.id,))
            for tag in entry.tags:
                self.conn.execute("INSERT INTO entry_tags (entry_id, tag) VALUES (?, ?)", (entry.id, tag))
                if tag not in old_tags:
                    self.conn.execute(
                        "INSERT INTO tags (name, use_count, last_used) VALUES (?, 1, ?)"
                        " ON CONFLICT(name) DO UPDATE SET use_count = use_count + 1, last_used = excluded.last_used",
                        (tag, now),
                    )

            for pos, img in enumerate(entry.images):
                if img.id is None:
                    cur = self.conn.execute(
                        "INSERT INTO images (entry_id, position, path, original_name, include) VALUES (?, ?, ?, ?, ?)",
                        (entry.id, pos, img.path, img.original_name, int(img.include)),
                    )
                    img.id = cur.lastrowid
                else:
                    self.conn.execute(
                        "UPDATE images SET position=?, include=? WHERE id=?", (pos, int(img.include), img.id))
        self._remove_files(removed_files)
        return entry.id

    def delete_entry(self, entry_id: int):
        paths = [r["path"] for r in self.conn.execute("SELECT path FROM images WHERE entry_id=?", (entry_id,))]
        with self.conn:
            self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self._remove_files(paths)

    def _load(self, row) -> Entry:
        tags = [r["tag"] for r in self.conn.execute(
            "SELECT tag FROM entry_tags WHERE entry_id=? ORDER BY rowid", (row["id"],))]
        images = [Image(r["id"], r["path"], r["original_name"], bool(r["include"])) for r in self.conn.execute(
            "SELECT * FROM images WHERE entry_id=? ORDER BY position", (row["id"],))]
        return Entry(row["id"], row["date"], row["time"], row["text"], row["status"], tags, images,
                     row["carried_from"])

    def get_entry(self, entry_id: int) -> Entry | None:
        row = self.conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        return self._load(row) if row else None

    def entries_for_date(self, date: str) -> list[Entry]:
        return self.entries_between(date, date)

    def entries_between(self, start: str, end: str) -> list[Entry]:
        """取得日期區間（含頭尾）內的紀錄，供週報等功能使用。"""
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE date BETWEEN ? AND ? ORDER BY date, time, id", (start, end)).fetchall()
        return [self._load(r) for r in rows]

    # ---- 標籤 ----

    def all_tags(self) -> list[str]:
        return [r["name"] for r in self.conn.execute("SELECT name FROM tags ORDER BY use_count DESC, name")]

    def recent_tags(self, limit: int = 8) -> list[str]:
        return [r["name"] for r in self.conn.execute(
            "SELECT name FROM tags ORDER BY last_used DESC, use_count DESC LIMIT ?", (limit,))]

    # ---- 帶入未完成項目 ----

    def previous_date_with_entries(self, date: str) -> str | None:
        row = self.conn.execute("SELECT MAX(date) AS d FROM entries WHERE date < ?", (date,)).fetchone()
        return row["d"]

    def carry_over_candidates(self, from_date: str, to_date: str) -> list[Entry]:
        """from_date 中「進行中／卡住」且尚未帶入 to_date 的紀錄。"""
        rows = self.conn.execute(
            "SELECT * FROM entries e WHERE e.date=? AND e.status IN (?, ?)"
            " AND NOT EXISTS (SELECT 1 FROM entries c WHERE c.date=? AND c.carried_from=e.id)"
            " ORDER BY e.time, e.id",
            (from_date, *UNFINISHED, to_date),
        ).fetchall()
        return [self._load(r) for r in rows]

    def carry_over(self, from_date: str, to_date: str, time: str | None = None) -> list[int]:
        """把未完成項目複製成 to_date 的新紀錄（不帶圖片），回傳新 id。"""
        time = time or datetime.now().strftime("%H:%M")
        new_ids = []
        for old in self.carry_over_candidates(from_date, to_date):
            new = Entry(None, to_date, time, old.text, old.status, list(old.tags), [], old.id)
            new_ids.append(self.save_entry(new))
        return new_ids
