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
DEFAULT_CATEGORIES = ("開發", "會議", "支援", "行政", "其他")

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
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL DEFAULT 0
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
    carried_from: int | None = None  # 從哪一筆延續（帶入或追加進度）
    category_id: int | None = None
    category: str = ""  # 分類名稱，讀取時填入


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
        self._migrate()

    def _migrate(self):
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(entries)")}
        with self.conn:
            if "category_id" not in columns:
                self.conn.execute("ALTER TABLE entries ADD COLUMN category_id INTEGER REFERENCES categories(id)")
                # 第一次建立分類欄位時給預設分類
                for pos, name in enumerate(DEFAULT_CATEGORIES):
                    self.conn.execute("INSERT OR IGNORE INTO categories (name, position) VALUES (?, ?)", (name, pos))

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
                    "INSERT INTO entries (date, time, text, status, carried_from, category_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry.date, entry.time, entry.text, entry.status, entry.carried_from, entry.category_id,
                     now, now),
                )
                entry.id = cur.lastrowid
                old_tags = set()
            else:
                self.conn.execute(
                    "UPDATE entries SET date=?, time=?, text=?, status=?, category_id=?, updated_at=? WHERE id=?",
                    (entry.date, entry.time, entry.text, entry.status, entry.category_id, now, entry.id),
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
        category = self.conn.execute(
            "SELECT name FROM categories WHERE id=?", (row["category_id"],)).fetchone()
        return Entry(row["id"], row["date"], row["time"], row["text"], row["status"], tags, images,
                     row["carried_from"], row["category_id"], category["name"] if category else "")

    def get_entry(self, entry_id: int) -> Entry | None:
        row = self.conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        return self._load(row) if row else None

    def last_entry(self) -> Entry | None:
        """最近一次新增的紀錄，用來預設新紀錄的分類與 tag。"""
        row = self.conn.execute("SELECT * FROM entries ORDER BY id DESC LIMIT 1").fetchone()
        return self._load(row) if row else None

    def set_status(self, entry_id: int, status: str):
        with self.conn:
            self.conn.execute("UPDATE entries SET status=?, updated_at=? WHERE id=?", (status, _now(), entry_id))

    def history(self, entry_id: int | None) -> list[Entry]:
        """沿著 carried_from 往前找這件工作先前的紀錄，由舊到新，不含 entry_id 本身。"""
        chain = []
        seen = set()
        current = self.get_entry(entry_id) if entry_id else None
        while current and current.carried_from and current.carried_from not in seen:
            seen.add(current.carried_from)
            current = self.get_entry(current.carried_from)
            if current:
                chain.append(current)
        return chain[::-1]

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

    def tag_usage(self) -> list[tuple[str, int]]:
        """所有 tag 與使用中的紀錄數。"""
        return [(r["name"], r["n"]) for r in self.conn.execute(
            "SELECT t.name, COUNT(et.entry_id) AS n FROM tags t LEFT JOIN entry_tags et ON et.tag = t.name"
            " GROUP BY t.name ORDER BY n DESC, t.name")]

    def rename_tag(self, old: str, new: str):
        """改名；新名稱已存在時等於合併。"""
        new = normalize_tag(new)
        if not new or new == old:
            return
        with self.conn:
            # 同時有新舊兩個 tag 的紀錄，只留新的
            self.conn.execute(
                "DELETE FROM entry_tags WHERE tag=? AND entry_id IN (SELECT entry_id FROM entry_tags WHERE tag=?)",
                (old, new))
            self.conn.execute("UPDATE entry_tags SET tag=? WHERE tag=?", (new, old))
            row = self.conn.execute("SELECT use_count, last_used FROM tags WHERE name=?", (old,)).fetchone()
            if row:
                self.conn.execute(
                    "INSERT INTO tags (name, use_count, last_used) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET"
                    " use_count = use_count + excluded.use_count, last_used = MAX(last_used, excluded.last_used)",
                    (new, row["use_count"], row["last_used"]))
                self.conn.execute("DELETE FROM tags WHERE name=?", (old,))

    def delete_tag(self, name: str):
        """從所有紀錄與建議清單中移除。"""
        with self.conn:
            self.conn.execute("DELETE FROM entry_tags WHERE tag=?", (name,))
            self.conn.execute("DELETE FROM tags WHERE name=?", (name,))

    # ---- 分類 ----

    def categories(self) -> list[tuple[int, str]]:
        return [(r["id"], r["name"]) for r in self.conn.execute(
            "SELECT id, name FROM categories ORDER BY position, id")]

    def category_usage(self, category_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM entries WHERE category_id=?", (category_id,)).fetchone()[0]

    def _check_category_name(self, name: str, exclude_id: int = -1) -> str:
        name = name.strip()
        if not name:
            raise ValueError("分類名稱不能是空的")
        if self.conn.execute("SELECT 1 FROM categories WHERE name=? AND id<>?", (name, exclude_id)).fetchone():
            raise ValueError(f"已經有「{name}」這個分類")
        return name

    def add_category(self, name: str) -> int:
        name = self._check_category_name(name)
        with self.conn:
            pos = self.conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM categories").fetchone()[0]
            return self.conn.execute("INSERT INTO categories (name, position) VALUES (?, ?)", (name, pos)).lastrowid

    def rename_category(self, category_id: int, name: str):
        name = self._check_category_name(name, category_id)
        with self.conn:
            self.conn.execute("UPDATE categories SET name=? WHERE id=?", (name, category_id))

    def delete_category(self, category_id: int):
        """刪除分類，原本屬於它的紀錄變成未分類。"""
        with self.conn:
            self.conn.execute("UPDATE entries SET category_id=NULL WHERE category_id=?", (category_id,))
            self.conn.execute("DELETE FROM categories WHERE id=?", (category_id,))

    def reorder_categories(self, ids: list[int]):
        with self.conn:
            for pos, cid in enumerate(ids):
                self.conn.execute("UPDATE categories SET position=? WHERE id=?", (pos, cid))

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
            new = Entry(None, to_date, time, old.text, old.status, list(old.tags), [], old.id, old.category_id)
            new_ids.append(self.save_entry(new))
        return new_ids
